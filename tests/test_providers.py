"""Provider registry: honest reporting, and no silent fallbacks."""

from __future__ import annotations

import os

import pytest

from deckpager.config import Settings, load_settings
from deckpager.errors import ConfigError
from deckpager.llm.base import Usage
from deckpager.llm.registry import KNOWN_PROVIDERS, describe, describe_all, get_provider


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Neither the developer's keys nor a local Ollama should decide these assertions."""
    for name in list(os.environ):
        if name.startswith("DECKPAGER_"):
            monkeypatch.delenv(name, raising=False)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


class TestDescribe:
    def test_every_known_provider_is_reported(self) -> None:
        names = [status.name for status in describe_all(Settings())]
        assert names == list(KNOWN_PROVIDERS)

    def test_fake_is_always_ready_and_offline(self) -> None:
        status = describe("fake", Settings())
        assert status.ready
        assert "never hits the network" in status.detail

    def test_anthropic_without_a_key_is_not_ready(self) -> None:
        status = describe("anthropic", Settings())
        assert not status.ready
        assert "ANTHROPIC_API_KEY" in status.detail

    def test_anthropic_with_a_key_is_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        status = describe("anthropic", Settings())
        assert status.ready
        assert status.vision

    def test_readiness_never_makes_a_billable_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A key is checked for presence only — verifying it would cost a request."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-obviously-invalid")
        assert describe("anthropic", Settings()).ready

    def test_selected_provider_is_marked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECKPAGER_PROVIDER", "fake")
        assert "selected" in describe("fake", Settings()).notes
        assert "selected" not in describe("anthropic", Settings()).notes

    def test_unwired_adapter_is_disclosed_separately_from_readiness(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ready` means configured; whether the adapter exists is a separate fact."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        status = describe("anthropic", Settings())
        assert status.ready
        assert any("adapter" in note for note in status.notes)

    def test_unknown_name_is_reported_not_raised(self) -> None:
        status = describe("gpt5", Settings())
        assert not status.ready
        assert status.detail == "unknown provider"


class TestGetProvider:
    def test_unwired_provider_refuses_with_the_milestone(self) -> None:
        """Better a clear refusal than an ImportError on a module that does not exist."""
        with pytest.raises(ConfigError) as excinfo:
            get_provider(load_settings(provider="fake"))
        assert "M2" in str(excinfo.value)

    @pytest.mark.parametrize("name", KNOWN_PROVIDERS)
    def test_no_provider_silently_falls_back(self, name: str) -> None:
        """Every selection either builds its own backend or raises — never substitutes."""
        settings = load_settings(provider=name)
        try:
            provider = get_provider(settings)
        except ConfigError:
            return
        assert provider.name == name


class TestUsage:
    def test_usage_accumulates_across_calls(self) -> None:
        total = Usage(input_tokens=10, output_tokens=5) + Usage(input_tokens=3, output_tokens=2)
        assert total.input_tokens == 13
        assert total.output_tokens == 7
        assert total.calls == 2

    def test_unknown_cost_stays_unknown(self) -> None:
        """A missing price must not be reported as free."""
        assert (Usage(input_tokens=10) + Usage(input_tokens=1)).cost_usd is None

    def test_known_costs_sum(self) -> None:
        total = Usage(cost_usd=0.5) + Usage(cost_usd=0.25)
        assert total.cost_usd == pytest.approx(0.75)

    def test_summary_omits_cost_when_unknown(self) -> None:
        assert "$" not in Usage(input_tokens=1, output_tokens=2).summary()
        assert "$0.5000" in Usage(cost_usd=0.5).summary()
