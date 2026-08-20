"""Locating the analyst-editable data files.

`config/` and `prompts/` are product data, not source. They live at the repository root so
an analyst can edit a weight or reword the persona without touching Python. This module is
the single place that knows where they are.

Resolution order, first hit wins:

1. ``DECKPAGER_CONFIG_DIR`` / ``DECKPAGER_PROMPTS_DIR`` — an explicit override.
2. The repository root, four parents up from this file. This is the path that exists under
   an editable install, which is how the tool is normally run.
3. The copy bundled into the wheel at ``deckpager/_config`` / ``deckpager/_prompts``.

If none resolve, the error names the environment variable to set rather than reporting a
bare missing file.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from deckpager.errors import ConfigError

#: `<!-- deckpager:section deck_payload -->` — the section delimiter in the prompt files.
_SECTION = re.compile(r"^[ \t]*<!--[ \t]*deckpager:section[ \t]+([\w.-]+)[ \t]*-->[ \t]*$", re.M)

#: src/deckpager/paths.py -> src/deckpager -> src -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where the wheel build drops its copy (see [tool.hatch.build] force-include).
_BUNDLED = Path(__file__).resolve().parent


def _resolve(kind: str, env_var: str) -> Path:
    """Find the directory named `kind`, or explain how to point at it."""
    override = os.environ.get(env_var)
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_dir():
            raise ConfigError(
                f"{env_var} is set to {candidate}, which is not a directory.\n"
                f"Point it at the deckpager {kind}/ directory, or unset it to use the "
                f"copy shipped with the package."
            )
        return candidate

    for candidate in (_REPO_ROOT / kind, _BUNDLED / f"_{kind}"):
        if candidate.is_dir():
            return candidate

    raise ConfigError(
        f"Could not find the {kind}/ directory.\n"
        f"Looked in:\n"
        f"    {_REPO_ROOT / kind}\n"
        f"    {_BUNDLED / f'_{kind}'}\n"
        f"Set {env_var} to the directory holding deckpager's {kind} files."
    )


@lru_cache(maxsize=1)
def config_dir() -> Path:
    """The directory holding `default.toml` and `weights.toml`."""
    return _resolve("config", "DECKPAGER_CONFIG_DIR")


@lru_cache(maxsize=1)
def prompts_dir() -> Path:
    """The directory holding the analyst prompt files."""
    return _resolve("prompts", "DECKPAGER_PROMPTS_DIR")


def read_prompt(name: str) -> str:
    """Read a prompt file by filename, e.g. ``read_prompt("analyst_system.md")``."""
    path = prompts_dir() / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(
            f"Could not read the prompt file {path}: {exc}\n"
            f"It ships with deckpager; restore it from version control if it was moved."
        ) from exc


def read_prompt_sections(name: str) -> dict[str, str]:
    """Read a prompt file split on its ``<!-- deckpager:section NAME -->`` markers.

    Text before the first marker is the file's own documentation and is discarded, which
    is what lets the prompt files carry an editing note for the analyst without it
    reaching the model.
    """
    text = read_prompt(name)
    parts = _SECTION.split(text)
    if len(parts) < 3:
        raise ConfigError(
            f"{prompts_dir() / name} contains no `<!-- deckpager:section NAME -->` markers.\n"
            f"Each section the code asks for must be introduced by one."
        )
    # re.split with one capture group yields [preamble, name, body, name, body, ...].
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}
    return sections


def require_sections(name: str, *wanted: str) -> dict[str, str]:
    """Read a prompt file and fail loudly if any expected section is absent."""
    sections = read_prompt_sections(name)
    missing = [section for section in wanted if section not in sections]
    if missing:
        raise ConfigError(
            f"{prompts_dir() / name} is missing the section(s) {missing}.\n"
            f"Found: {sorted(sections)}. Add a `<!-- deckpager:section NAME -->` marker "
            f"for each, or restore the file from version control."
        )
    return sections


def clear_caches() -> None:
    """Forget resolved directories. Tests use this after moving the environment."""
    config_dir.cache_clear()
    prompts_dir.cache_clear()
