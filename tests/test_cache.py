"""The extraction cache: what makes a key, and the promise that a miss is never a crash."""

from __future__ import annotations

from pathlib import Path

import pytest

from deckpager.cache import (
    CACHE_DIR_ENV,
    ExtractionCache,
    cache_key,
    deck_fingerprint,
    default_cache_root,
)

DECK = b"%PDF-1.4 pretend this is a deck"
SCHEMA: dict[str, object] = {"type": "object", "properties": {"company_name": {}}}
PROMPT = "You are an investment analyst at TEN Capital."


def _key(**overrides: object) -> str:
    base: dict[str, object] = {
        "deck_bytes": DECK,
        "model": "claude-opus-5",
        "prompt": PROMPT,
        "schema": SCHEMA,
        "options": {"effort": "high", "max_slides": 40},
    }
    base.update(overrides)
    return cache_key(**base)  # type: ignore[arg-type]


class TestKeying:
    def test_the_same_inputs_give_the_same_key(self) -> None:
        assert _key() == _key()

    def test_the_key_is_the_deck_content_not_its_name(self) -> None:
        """The same deck re-sent under a new filename is the same deck."""
        assert deck_fingerprint(DECK) == deck_fingerprint(bytes(DECK))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("deck_bytes", b"a different deck"),
            ("model", "claude-sonnet-5"),
            ("prompt", "A reworded system prompt."),
            ("schema", {"type": "object", "properties": {"tagline": {}}}),
            ("options", {"effort": "low", "max_slides": 40}),
        ],
    )
    def test_every_input_that_steers_the_model_changes_the_key(
        self, field: str, value: object
    ) -> None:
        """A cache that answers today's prompt with yesterday's result is worse than none."""
        assert _key(**{field: value}) != _key()

    def test_option_ordering_does_not_change_the_key(self) -> None:
        forward = _key(options={"effort": "high", "max_slides": 40})
        backward = _key(options={"max_slides": 40, "effort": "high"})
        assert forward == backward


class TestRoundTrip:
    def test_a_stored_payload_comes_back(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        assert cache.put("a" * 64, {"company_name": {"value": "Helion Bio"}})
        assert cache.get("a" * 64) == {"company_name": {"value": "Helion Bio"}}

    def test_an_unknown_key_is_a_miss(self, tmp_path: Path) -> None:
        assert ExtractionCache(tmp_path).get("b" * 64) is None

    def test_records_are_sharded_so_one_directory_stays_browsable(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        cache.put("abcdef" + "0" * 58, {"x": 1})
        assert (tmp_path / "ab" / ("abcdef" + "0" * 58 + ".json")).is_file()

    def test_disabled_means_no_read_and_no_write(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path, enabled=False)
        assert not cache.put("c" * 64, {"x": 1})
        assert cache.get("c" * 64) is None
        assert not any(tmp_path.rglob("*.json"))

    def test_clear_removes_every_record(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        for index in range(3):
            cache.put(f"{index}" * 64, {"x": index})
        assert cache.clear() == 3
        assert cache.get("0" * 64) is None


class TestFailureIsAlwaysAMiss:
    """A cache that can break a run is a liability. Every fault degrades to paying again."""

    def test_a_truncated_record_is_a_miss(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        key = "d" * 64
        cache.put(key, {"x": 1})
        cache.path_for(key).write_text('{"version": 1, "payload": {"x"', encoding="utf-8")
        assert cache.get(key) is None

    def test_a_record_from_an_older_format_is_a_miss(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        key = "e" * 64
        cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
        cache.path_for(key).write_text('{"version": 0, "payload": {"x": 1}}', encoding="utf-8")
        assert cache.get(key) is None

    def test_a_record_that_is_not_an_object_is_a_miss(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        key = "f" * 64
        cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
        cache.path_for(key).write_text("[1, 2, 3]", encoding="utf-8")
        assert cache.get(key) is None

    def test_an_unwritable_root_does_not_raise(self, tmp_path: Path) -> None:
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("I am a file", encoding="utf-8")
        cache = ExtractionCache(blocked)
        assert cache.put("0" * 64, {"x": 1}) is False
        assert cache.get("0" * 64) is None

    def test_clear_on_a_cache_that_was_never_written_is_zero(self, tmp_path: Path) -> None:
        assert ExtractionCache(tmp_path / "absent").clear() == 0


class TestLocation:
    def test_the_environment_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CACHE_DIR_ENV, "/tmp/deckpager-cache")
        assert default_cache_root() == Path("/tmp/deckpager-cache")

    def test_the_default_is_a_user_cache_directory_not_the_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Extractions are as confidential as the decks they came from — not in the repo."""
        monkeypatch.delenv(CACHE_DIR_ENV, raising=False)
        root = default_cache_root()
        assert "deckpager" in str(root).lower()
        assert Path.cwd() not in root.parents
