"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from deckpager.config import Settings, reset_settings_cache

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests off the developer's real .env, API key, and cache directory."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in list(Settings.model_fields):
        monkeypatch.delenv(f"DECKPAGER_{name.upper()}", raising=False)
    monkeypatch.setenv("DECKPAGER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    reset_settings_cache()
    yield
    reset_settings_cache()
