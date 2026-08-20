"""Configuration: the precedence chain, the weights table, and provider selection."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from pitchlens import paths
from pitchlens.analysis.schema import SCORECARD_ORDER
from pitchlens.config import Settings, _flatten_toml, load_settings, load_weights
from pitchlens.errors import ConfigError

SCORED = [name for name in SCORECARD_ORDER if name != "Overall Investability"]


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run each test against a known configuration, not the developer's own.

    Both `paths` and `load_weights` memoize, so the caches are cleared on the way in and
    again on the way out — otherwise a test that redirects PITCHLENS_CONFIG_DIR would
    poison every test that follows it.
    """
    for name in list(os.environ):
        if name.startswith("PITCHLENS_"):
            monkeypatch.delenv(name, raising=False)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    # `.env` resolves against the process CWD; chdir somewhere without one so a real
    # .env beside the repo cannot leak into these assertions.
    monkeypatch.chdir(tmp_path)

    paths.clear_caches()
    load_weights.cache_clear()
    yield
    paths.clear_caches()
    load_weights.cache_clear()


def _redirect_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, weights: dict) -> None:
    """Point the config directory at a scratch weights.toml."""
    body = "\n".join(f'"{name}" = {value}' for name, value in weights.items())
    (tmp_path / "weights.toml").write_text(f"[weights]\n{body}\n", encoding="utf-8")
    monkeypatch.setenv("PITCHLENS_CONFIG_DIR", str(tmp_path))
    paths.clear_caches()
    load_weights.cache_clear()


class TestPrecedence:
    def test_toml_supplies_the_default(self) -> None:
        """With no environment and no override, the shipped config file wins."""
        settings = Settings()
        assert settings.provider == "anthropic"
        assert settings.model == "claude-opus-5"
        assert settings.effort == "high"
        assert settings.max_slides == 60

    def test_environment_beats_the_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PITCHLENS_PROVIDER", "fake")
        monkeypatch.setenv("PITCHLENS_MODEL", "some-other-model")
        settings = Settings()
        assert settings.provider == "fake"
        assert settings.model == "some-other-model"

    def test_cli_override_beats_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PITCHLENS_PROVIDER", "ollama")
        assert load_settings(provider="fake").provider == "fake"

    def test_unset_overrides_do_not_clobber(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing one override must not reset unrelated settings to their defaults."""
        monkeypatch.setenv("PITCHLENS_MAX_SLIDES", "12")
        settings = load_settings(provider="fake")
        assert settings.provider == "fake"
        assert settings.max_slides == 12

    def test_unknown_provider_is_actionable(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_settings(provider="gpt5")
        message = str(excinfo.value)
        assert "gpt5" in message
        assert "anthropic" in message and "fake" in message
        assert "pitchlens providers" in message


class TestApiKeys:
    def test_missing_key_names_the_variable_and_the_offline_escape(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            Settings().require_api_key("anthropic")
        message = str(excinfo.value)
        assert "ANTHROPIC_API_KEY" in message
        assert "--provider fake" in message

    def test_key_is_read_from_the_unprefixed_vendor_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not PITCHLENS_ANTHROPIC_API_KEY — the same variable the SDKs already use."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert Settings().require_api_key("anthropic") == "sk-ant-test"

    def test_keyless_provider_says_so(self) -> None:
        with pytest.raises(ConfigError, match="does not use an API key"):
            Settings().require_api_key("ollama")


class TestTomlMapping:
    def test_unknown_section_is_rejected(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            _flatten_toml({"nonsense": {"a": 1}}, Path("default.toml"))
        assert "unknown section [nonsense]" in str(excinfo.value)

    def test_unknown_key_lists_the_valid_ones(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            _flatten_toml({"model": {"nmae": "typo"}}, Path("default.toml"))
        message = str(excinfo.value)
        assert "unknown key `nmae`" in message
        assert "max_tokens" in message

    def test_nested_tables_flatten_onto_settings_fields(self) -> None:
        flat = _flatten_toml(
            {"provider": {"name": "fake"}, "ingest": {"max_slides": 7}},
            Path("default.toml"),
        )
        assert flat == {"provider": "fake", "max_slides": 7}


class TestWeights:
    def test_shipped_weights_are_valid(self) -> None:
        weights = load_weights()
        assert len(weights) == 10
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_weights_cover_exactly_the_scored_categories(self) -> None:
        """Overall Investability is the computed output, so weighting it would be circular."""
        assert set(load_weights()) == set(SCORED)

    @pytest.mark.parametrize(
        ("weights", "expected"),
        [
            ({"Founder": 1.0}, "missing"),
            (dict.fromkeys([*SCORED, "Nonsense"], 1 / 11), "unexpected"),
        ],
    )
    def test_wrong_categories_are_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        weights: dict,
        expected: str,
    ) -> None:
        _redirect_config(tmp_path, monkeypatch, weights)
        with pytest.raises(ConfigError) as excinfo:
            load_weights()
        assert expected in str(excinfo.value)

    def test_weights_that_do_not_sum_to_one_are_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_config(tmp_path, monkeypatch, dict.fromkeys(SCORED, 0.5))
        message = str(pytest.raises(ConfigError, load_weights).value)
        assert "must sum to 1.0" in message
        assert "5.0" in message  # the actual total, so the fix is obvious

    def test_negative_weight_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        weights = dict.fromkeys(SCORED, 0.2)
        weights["Storytelling"] = -0.8
        _redirect_config(tmp_path, monkeypatch, weights)
        with pytest.raises(ConfigError, match="negative"):
            load_weights()


class TestConfigDirResolution:
    def test_env_override_pointing_at_nothing_names_the_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PITCHLENS_CONFIG_DIR", str(tmp_path / "does-not-exist"))
        paths.clear_caches()
        with pytest.raises(ConfigError) as excinfo:
            paths.config_dir()
        assert "PITCHLENS_CONFIG_DIR" in str(excinfo.value)

    def test_repo_root_is_found_without_any_override(self) -> None:
        assert (paths.config_dir() / "default.toml").is_file()
        assert (paths.prompts_dir() / "analyst_system.md").is_file()
