"""Settings loading."""

from __future__ import annotations

import pytest

from deckpager.config import DEFAULT_MODEL, MODEL_PRICING_USD_PER_MTOK, get_settings, reset_settings_cache


def test_defaults_when_env_is_empty() -> None:
    settings = get_settings()
    assert settings.model == DEFAULT_MODEL
    assert settings.min_confidence == 0.6
    assert settings.engine == "weasyprint"
    assert settings.has_api_key() is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DECKPAGER_MIN_CONFIDENCE", "0.8")
    reset_settings_cache()
    settings = get_settings()
    assert settings.has_api_key() is True
    assert settings.min_confidence == 0.8


def test_api_key_is_not_exposed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret")
    reset_settings_cache()
    assert "supersecret" not in repr(get_settings())


def test_default_model_has_pricing() -> None:
    assert DEFAULT_MODEL in MODEL_PRICING_USD_PER_MTOK
