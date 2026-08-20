"""Locating the analyst-editable data files.

`config/` is product data, not source. It lives at the repository root so an operator can
change a default without touching Python. This module is the single place that knows where
it is.

Resolution order, first hit wins:

1. ``DECKPAGER_CONFIG_DIR`` — an explicit override.
2. The repository root, four parents up from this file. This is the path that exists under
   an editable install, which is how the tool is normally run.
3. The copy bundled into the wheel at ``deckpager/_config``.

If none resolve, the error names the environment variable to set rather than reporting a
bare missing file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from deckpager.errors import ConfigError

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
    """The directory holding `default.toml`."""
    return _resolve("config", "DECKPAGER_CONFIG_DIR")


def clear_caches() -> None:
    """Forget resolved directories. Tests use this after moving the environment."""
    config_dir.cache_clear()
