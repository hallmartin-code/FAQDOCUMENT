"""Provider lookup and readiness reporting.

Two entry points, deliberately separate:

`get_provider` constructs a backend and is allowed to fail loudly — a missing API key
should stop the run before a deck is parsed.

`describe` reports what each backend *would* do without constructing it, so
`deckpager providers` can list a backend that is not configured instead of crashing on
the first one that is not. It also keeps the heavy vendor SDKs out of a `--help`.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from deckpager.config import Settings
from deckpager.errors import ConfigError
from deckpager.llm.base import LLMProvider, ProviderStatus

#: Registry order — this is the order `deckpager providers` prints.
KNOWN_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "ollama", "fake")

#: Provider -> the distribution that must be importable for it to run.
_REQUIRED_PACKAGE: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
}

#: Providers whose `LLMProvider` implementation exists. Grows as milestones land; a name
#: outside this set is reported honestly by `describe` and refused by `get_provider`
#: rather than failing later on an ImportError.
_WIRED: frozenset[str] = frozenset()

#: Why a known-but-unwired provider is not available yet.
_PENDING: dict[str, str] = {
    "fake": "arrives in milestone M2",
    "anthropic": "arrives in milestone M3",
    "openai": "arrives in milestone M3",
    "ollama": "arrives in milestone M3",
}


def _installed(package: str) -> bool:
    """Whether a vendor SDK is importable, without importing it."""
    return importlib.util.find_spec(package) is not None


def _ollama_reachable(host: str, timeout: float = 1.0) -> tuple[bool, str]:
    """Probe a local Ollama daemon. Cheap and free, unlike probing a hosted API."""
    url = f"{host.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed scheme
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return False, f"not reachable at {host} ({exc.reason})"
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"not reachable at {host} ({exc})"
    models = [m.get("name", "?") for m in payload.get("models", [])]
    if not models:
        return True, f"reachable at {host}, but no models are pulled"
    return True, f"reachable at {host}, {len(models)} model(s) available"


def describe(name: str, settings: Settings) -> ProviderStatus:
    """Report whether `name` could run right now, without constructing it."""
    if name not in KNOWN_PROVIDERS:
        return ProviderStatus(name=name, ready=False, detail="unknown provider")

    # `ready` answers "is this backend configured and reachable" — the reachability check.
    # Whether the adapter has been written yet is a separate fact, carried in `notes`.
    notes: list[str] = []
    if name == settings.provider:
        notes.append("selected")
    pending = _PENDING.get(name) if name not in _WIRED else None
    if pending:
        notes.append(f"adapter {pending}")

    if name == "fake":
        return ProviderStatus(
            name=name,
            ready=True,
            detail="always available; replays a recorded fixture, never hits the network",
            model="(fixture)",
            vision=False,
            notes=notes,
        )

    package = _REQUIRED_PACKAGE.get(name)
    if package and not _installed(package):
        return ProviderStatus(
            name=name,
            ready=False,
            detail=f"the `{package}` package is not installed (pip install {package})",
            notes=notes,
        )

    if name == "ollama":
        reachable, detail = _ollama_reachable(settings.ollama_host)
        return ProviderStatus(
            name=name,
            ready=reachable,
            detail=detail,
            model=settings.model,
            vision=False,
            notes=notes,
        )

    # anthropic / openai: a key is the only thing we can check without spending money.
    try:
        settings.require_api_key(name)
    except ConfigError:
        variable = "ANTHROPIC_API_KEY" if name == "anthropic" else "OPENAI_API_KEY"
        return ProviderStatus(
            name=name,
            ready=False,
            detail=f"{variable} is not set",
            model=settings.model,
            notes=notes,
        )

    return ProviderStatus(
        name=name,
        ready=True,
        detail="key present (not verified against the API — that would cost a request)",
        model=settings.model,
        vision=True,
        notes=notes,
    )


def describe_all(settings: Settings) -> list[ProviderStatus]:
    """Status for every known provider, in registry order."""
    return [describe(name, settings) for name in KNOWN_PROVIDERS]


def get_provider(settings: Settings, fixture: Path | BaseModel | dict[str, Any] | None = None) -> LLMProvider:
    """Construct the configured provider, or explain why it cannot be built."""
    name = settings.provider
    if name not in KNOWN_PROVIDERS:
        raise ConfigError(
            f"Unknown provider {name!r}. Choose one of: {', '.join(KNOWN_PROVIDERS)}."
        )

    if name not in _WIRED:
        available = ", ".join(sorted(_WIRED)) or "none yet"
        raise ConfigError(
            f"The {name} provider adapter {_PENDING.get(name, 'is not implemented')}.\n"
            f"Wired today: {available}. Run `deckpager providers` to see the state of each."
        )

    if name == "fake":
        from deckpager.llm.fake import FakeProvider

        return FakeProvider(fixture)

    from deckpager.llm.anthropic import AnthropicProvider

    # Annotated rather than returned directly: the adapter module is untyped to mypy,
    # and an implicit Any escaping a declared return type is what --strict forbids.
    provider: LLMProvider = AnthropicProvider(settings)
    return provider
