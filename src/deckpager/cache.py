"""Disk cache for extraction results, keyed by everything that could change them.

Spec §3 asks for determinism: the same deck and the same model response must produce the
same one-pager, and a re-render must cost nothing. That is what this is for. Layout work
is iterative — a partner asks for the risks to be shorter, the analyst re-runs — and paying
for a fresh extraction each time would make the tool too expensive to iterate with.

The key covers the deck bytes and every input that steers the model: the model ID, the
effort setting, the prompt, the tool schema, and the ingest budgets that decide which
slides it sees. Change any of them and you get a miss, which is the point — a cache that
returns yesterday's answer to today's prompt is worse than no cache.

What lands on disk is the extraction, which is derived from the deck and is as confidential
as the deck. It is written under the user's own cache directory, and `deckpager check`
prints where.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Bumped when the on-disk record shape changes. Old entries then miss rather than
#: deserializing into something that no longer means what it meant.
CACHE_VERSION = 1

#: Environment override for the cache location.
CACHE_DIR_ENV = "DECKPAGER_CACHE_DIR"


def default_cache_root() -> Path:
    """Where the cache lives when nothing overrides it.

    Follows the platform convention rather than dropping a directory in the project or the
    user's deck folder: on Windows LOCALAPPDATA, on macOS Library/Caches, elsewhere the
    XDG cache directory.
    """
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base or Path.home() / "AppData" / "Local") / "deckpager" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "deckpager"
    base = os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "deckpager"


def _canonical(value: Any) -> str:
    """A stable string for any JSON-able value: sorted keys, no incidental whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def deck_fingerprint(deck_bytes: bytes) -> str:
    """The content hash of the deck file itself.

    Hashing the file, not its path or mtime: the same deck re-sent by a founder under a new
    filename is the same deck, and a file touched by a sync client is not a new one.
    """
    return hashlib.sha256(deck_bytes).hexdigest()


def cache_key(
    *,
    deck_bytes: bytes,
    model: str,
    prompt: str,
    schema: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
) -> str:
    """Hash every input that could change the extraction into one key."""
    material = _canonical(
        {
            "version": CACHE_VERSION,
            "deck": deck_fingerprint(deck_bytes),
            "model": model,
            "prompt": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "schema": hashlib.sha256(_canonical(dict(schema)).encode("utf-8")).hexdigest(),
            "options": dict(options or {}),
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ExtractionCache:
    """A content-addressed store of extraction payloads.

    Every failure mode here is a miss, never an exception. A cache that can break a run is
    a liability: if the directory is unwritable, the disk is full, or a record was
    truncated by a crash, the correct behaviour is to pay for the extraction again.
    """

    def __init__(self, root: Path | None = None, *, enabled: bool = True) -> None:
        self.root = root if root is not None else default_cache_root()
        self.enabled = enabled

    def path_for(self, key: str) -> Path:
        """Where a key is stored. Sharded by prefix so one directory stays browsable."""
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """The cached payload, or None on any kind of miss."""
        if not self.enabled:
            return None
        path = self.path_for(key)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict) or record.get("version") != CACHE_VERSION:
            return None
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else None

    def put(self, key: str, payload: Mapping[str, Any]) -> bool:
        """Store a payload. Returns whether it was actually written.

        Written to a temporary file and moved into place, so a crash mid-write leaves the
        previous record or nothing at all — never a half-written record that would read as
        a corrupt hit on the next run.
        """
        if not self.enabled:
            return False
        path = self.path_for(key)
        record = {
            "version": CACHE_VERSION,
            "key": key,
            "created_at": datetime.now(UTC).isoformat(),
            "payload": dict(payload),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{key[:8]}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(record, handle, indent=2, default=str)
                temporary = Path(handle.name)
            temporary.replace(path)
        except OSError:
            return False
        return True

    def clear(self) -> int:
        """Delete every record. Returns how many were removed."""
        removed = 0
        if not self.root.is_dir():
            return 0
        for path in self.root.glob("*/*.json"):
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed
