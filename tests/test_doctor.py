"""Environment checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from deckpager.config import Settings
from deckpager.doctor import find_soffice, has_blocking_failure, run_checks


def _settings(tmp_path: Path) -> Settings:
    return Settings(cache_dir=tmp_path / "cache")


def test_missing_api_key_blocks(tmp_path: Path) -> None:
    results = run_checks(_settings(tmp_path))
    assert has_blocking_failure(results)
    api = next(r for r in results if r.name == "anthropic api key")
    assert api.ok is False
    assert api.fix is not None


def test_soffice_is_optional_and_never_raises(tmp_path: Path) -> None:
    results = run_checks(_settings(tmp_path))
    soffice = next(r for r in results if r.name.startswith("libreoffice"))
    assert soffice.required is False


def test_cache_dir_is_created(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_checks(settings)
    assert settings.cache_dir.is_dir()


def test_find_soffice_honours_explicit_path(tmp_path: Path) -> None:
    fake = tmp_path / "soffice.exe"
    fake.write_text("", encoding="utf-8")
    assert find_soffice(Settings(soffice_path=fake)) == fake


def test_find_soffice_returns_none_for_bad_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("deckpager.doctor._SOFFICE_FALLBACKS", ())
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert find_soffice(Settings(soffice_path=tmp_path / "nope.exe")) is None
