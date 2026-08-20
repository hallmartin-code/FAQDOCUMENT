"""Extraction against a stubbed API: the tool contract, the one correction turn, the cache.

Nothing here touches the network. The Anthropic client is replaced with a stub that returns
scripted responses, so the tests assert on what deckpager sends and how it reacts rather
than on what a model happens to say today.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from deckpager.cache import ExtractionCache
from deckpager.config import Settings
from deckpager.errors import AnalysisError, SchemaValidationError
from deckpager.extract.client import (
    PRICING,
    AnthropicExtractor,
    FakeExtractor,
    build_tool,
    estimate_cost,
    parse_tool_payload,
)
from deckpager.extract.pipeline import cache_options, cost_line, extract_one_pager
from deckpager.extract.prompts import SYSTEM_PROMPT, TOOL_NAME, build_user_blocks, slide_text
from deckpager.ingest import ingest_deck
from deckpager.models import NOT_A_DECK, OnePagerDraft

GOOD_PAYLOAD: dict[str, Any] = {
    "company_name": {"value": "Helion Bio", "confidence": 0.98, "source_slides": [1]},
    "problem": {
        "value": "Checkpoint inhibitors fail in 80% of solid tumor patients.",
        "confidence": 0.9,
        "source_slides": [2],
    },
    "key_strengths": {
        "value": ["Oral dosing, no cold chain", "Two prior INDs led by the CEO", "UCSD ties"],
        "confidence": 0.7,
        "source_slides": [3, 4],
    },
    "key_risks": {
        "value": ["No human data yet", "Commercial lead seat is open", "No lead investor"],
        "confidence": 0.8,
        "source_slides": [4, 5],
    },
}

#: Rejected by the schema: spec §6 wants exactly three risks.
BAD_PAYLOAD: dict[str, Any] = {
    "company_name": {"value": "Helion Bio", "confidence": 0.98, "source_slides": [1]},
    "key_risks": {"value": ["Only one risk"], "confidence": 0.8, "source_slides": [4]},
}


class _Stream:
    """Stands in for the SDK's streaming context manager."""

    def __init__(self, response: Any) -> None:
        self._response = response

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        return iter(())

    def get_final_message(self) -> Any:
        return self._response


class StubClient:
    """An Anthropic client that replays scripted responses and records what it was sent."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: Any) -> _Stream:
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("the extractor made more API calls than the test scripted")
        return _Stream(self._responses.pop(0))

    @property
    def call_count(self) -> int:
        return len(self.requests)


def tool_response(
    payload: dict[str, Any],
    *,
    stop_reason: str = "tool_use",
    input_tokens: int = 24_310,
    output_tokens: int = 1_842,
) -> Any:
    """A response whose single content block is a call to the extraction tool."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        content=[
            SimpleNamespace(type="tool_use", name=TOOL_NAME, id="toolu_01", input=payload)
        ],
    )


def text_response(stop_reason: str = "end_turn") -> Any:
    """A response that never called the tool."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
        content=[SimpleNamespace(type="text", text="I would rather write prose.")],
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(anthropic_api_key="sk-ant-test")


def _extractor(client: StubClient, settings: Settings, **kwargs: Any) -> AnthropicExtractor:
    return AnthropicExtractor(settings, client=client, **kwargs)  # type: ignore[arg-type]


class TestToolContract:
    def test_the_schema_is_generated_from_the_model(self) -> None:
        tool = build_tool()
        assert tool["name"] == TOOL_NAME
        assert set(tool["input_schema"]["properties"]) == set(OnePagerDraft.model_fields)

    def test_the_tool_call_is_forced_and_single(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        """Spec §8: structured output, not JSON fished out of prose."""
        client = StubClient(tool_response(GOOD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        _extractor(client, settings).extract(deck)

        choice = client.requests[0]["tool_choice"]
        assert choice["type"] == "tool"
        assert choice["name"] == TOOL_NAME
        # Without this the model can emit several parallel tool blocks whose partial JSON
        # splices into an unparseable payload.
        assert choice["disable_parallel_tool_use"] is True

    def test_the_system_prompt_is_the_one_from_the_spec(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(tool_response(GOOD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        _extractor(client, settings).extract(deck)
        assert client.requests[0]["system"][0]["text"] == SYSTEM_PROMPT

    def test_a_well_formed_response_validates(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(tool_response(GOOD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        result = _extractor(client, settings).extract(deck)
        assert result.draft.company_name.value == "Helion Bio"
        assert result.draft.company_name.source_slides == [1]
        assert client.call_count == 1


class TestCorrectionRetry:
    def test_a_schema_violation_gets_exactly_one_retry(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        """Spec §8: one correction turn with the errors fed back, then fail loudly."""
        announced: list[str] = []
        client = StubClient(tool_response(BAD_PAYLOAD), tool_response(GOOD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        result = _extractor(client, settings, on_retry=announced.append).extract(deck)

        assert client.call_count == 2
        assert result.draft.company_name.value == "Helion Bio"
        assert len(announced) == 1
        assert "key_risks" in announced[0]

    def test_the_retry_hands_the_errors_back_as_a_tool_result(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(tool_response(BAD_PAYLOAD), tool_response(GOOD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        _extractor(client, settings).extract(deck)

        second = client.requests[1]["messages"]
        assert second[-1]["role"] == "user"
        block = second[-1]["content"][0]
        assert block["type"] == "tool_result"
        assert block["is_error"] is True
        assert block["tool_use_id"] == "toolu_01"

    def test_failing_twice_raises_with_both_reports(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(tool_response(BAD_PAYLOAD), tool_response(BAD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(SchemaValidationError) as caught:
            _extractor(client, settings).extract(deck)
        message = str(caught.value)
        assert "First attempt" in message and "Second attempt" in message
        assert client.call_count == 2

    def test_it_never_retries_a_third_time(self, settings: Settings, sample_pdf: Path) -> None:
        """The stub raises if asked for a third response, so this asserts the ceiling."""
        client = StubClient(tool_response(BAD_PAYLOAD), tool_response(BAD_PAYLOAD))
        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(SchemaValidationError):
            _extractor(client, settings).extract(deck)


class TestApiFailures:
    def test_a_refusal_is_reported_as_one(self, settings: Settings, sample_pdf: Path) -> None:
        refusal = SimpleNamespace(
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="cyber"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=0),
            content=[],
        )
        client = StubClient(refusal)
        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(AnalysisError, match="declined"):
            _extractor(client, settings).extract(deck)

    def test_hitting_the_output_limit_names_the_setting_to_raise(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(text_response(stop_reason="max_tokens"))
        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(AnalysisError, match="DECKPAGER_MAX_TOKENS"):
            _extractor(client, settings).extract(deck)

    def test_prose_instead_of_a_tool_call_is_an_error(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        client = StubClient(text_response())
        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(AnalysisError, match="no `submit_one_pager` tool call"):
            _extractor(client, settings).extract(deck)

    def test_the_sdk_is_configured_for_four_attempts(self, settings: Settings) -> None:
        """Spec §8: exponential backoff on 429/5xx, four attempts. The SDK owns the backoff."""
        extractor = AnthropicExtractor(settings)
        assert extractor._client.max_retries == 3

    def test_an_exhausted_rate_limit_says_the_cache_makes_retrying_cheap(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        import httpx

        class Exploding(StubClient):
            def _stream(self, **kwargs: Any) -> _Stream:
                request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                raise anthropic.RateLimitError(
                    "rate limited",
                    response=httpx.Response(429, request=request),
                    body=None,
                )

        deck = ingest_deck(sample_pdf, settings)
        with pytest.raises(AnalysisError, match="cache"):
            _extractor(Exploding(), settings).extract(deck)


class TestPayloadParsing:
    def test_an_empty_payload_is_reported(self) -> None:
        assert parse_tool_payload("")[1] is not None

    def test_truncated_json_blames_the_token_budget(self) -> None:
        _, error = parse_tool_payload('{"company_name": {"value": "Helion')
        assert error is not None and "token budget" in error

    def test_concatenated_objects_are_caught(self) -> None:
        """The SDK would flatten these into one plausible, wrong dict."""
        _, error = parse_tool_payload('{"a": 1}{"b": 2}')
        assert error is not None and "concatenated" in error

    def test_a_json_array_is_not_an_object(self) -> None:
        _, error = parse_tool_payload("[1, 2, 3]")
        assert error is not None and "not an object" in error


class TestPrompt:
    def test_every_slide_is_delimited_by_number(self, settings: Settings, sample_pdf: Path) -> None:
        """Every citation is a slide number, so the numbers have to be unmissable."""
        deck = ingest_deck(sample_pdf, settings)
        text = slide_text(deck)
        for index in range(1, deck.slide_count + 1):
            assert f"--- SLIDE {index} ---" in text

    def test_a_pdf_within_the_limits_is_sent_whole(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        deck = ingest_deck(sample_pdf, settings)
        blocks = build_user_blocks(deck)
        assert blocks[0]["type"] == "document"
        assert blocks[0]["source"]["media_type"] == "application/pdf"

    def test_rasterized_slides_are_sent_as_images_when_the_file_cannot_be(
        self, settings: Settings, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deckpager.ingest.pdf.MAX_NATIVE_PDF_PAGES", 2)
        deck = ingest_deck(sample_pdf, settings)
        blocks = build_user_blocks(deck)
        assert any(block["type"] == "image" for block in blocks)

    def test_speaker_notes_reach_the_model(self, settings: Settings, sample_pptx: Path) -> None:
        """Notes are where the real numbers often hide."""
        deck = ingest_deck(sample_pptx, settings)
        assert "[speaker notes]" in slide_text(deck)

    def test_charted_slides_are_pointed_out(
        self, settings: Settings, image_heavy_pdf: Path
    ) -> None:
        deck = ingest_deck(image_heavy_pdf, settings)
        assert "[this slide contains a chart]" in slide_text(deck)

    def test_a_wordless_slide_says_so_rather_than_looking_empty(
        self, settings: Settings, image_heavy_pdf: Path
    ) -> None:
        deck = ingest_deck(image_heavy_pdf, settings)
        assert "[no extractable text on this slide]" in slide_text(deck)


class TestCost:
    def test_a_known_model_is_priced(self) -> None:
        cost = estimate_cost("claude-opus-5", 1_000_000, 100_000)
        assert cost is not None
        assert cost == pytest.approx(5.00 + 2.50)

    def test_an_unknown_model_reports_no_cost_rather_than_a_wrong_one(self) -> None:
        assert estimate_cost("claude-from-the-future", 1_000, 1_000) is None

    def test_the_spec_default_model_is_priced(self) -> None:
        assert "claude-sonnet-4-6" in PRICING

    def test_usage_is_totalled_across_a_correction_retry(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        """A retry is not free, and the cost line must not pretend it was."""
        client = StubClient(
            tool_response(BAD_PAYLOAD, input_tokens=1_000, output_tokens=100),
            tool_response(GOOD_PAYLOAD, input_tokens=1_200, output_tokens=120),
        )
        deck = ingest_deck(sample_pdf, settings)
        result = _extractor(client, settings).extract(deck)
        assert result.usage.input_tokens == 2_200
        assert result.usage.output_tokens == 220


class TestPipelineAndCache:
    def test_the_result_carries_its_provenance(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        one_pager = extract_one_pager(
            sample_pdf,
            settings=settings,
            extractor=FakeExtractor(GOOD_PAYLOAD),
            cache=ExtractionCache(tmp_path),
        )
        assert one_pager.provenance.source_filename == sample_pdf.name
        assert one_pager.provenance.source_page_count == 5
        assert one_pager.provenance.model == settings.model
        assert one_pager.provenance.cached is False

    def test_a_second_run_reads_the_cache_instead_of_calling(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        cache = ExtractionCache(tmp_path)
        first = FakeExtractor(GOOD_PAYLOAD)
        extract_one_pager(sample_pdf, settings=settings, extractor=first, cache=cache)

        class Forbidden(FakeExtractor):
            def extract(self, deck: Any) -> Any:
                raise AssertionError("a cache hit must not reach the extractor")

        second = extract_one_pager(
            sample_pdf,
            settings=settings,
            extractor=Forbidden(GOOD_PAYLOAD),
            cache=cache,
        )
        assert second.provenance.cached is True
        assert second.company_name.value == "Helion Bio"

    def test_no_cache_pays_again(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        cache = ExtractionCache(tmp_path)
        extract_one_pager(
            sample_pdf, settings=settings, extractor=FakeExtractor(GOOD_PAYLOAD), cache=cache
        )
        second = FakeExtractor(GOOD_PAYLOAD)
        extract_one_pager(
            sample_pdf,
            settings=settings,
            extractor=second,
            cache=cache,
            use_cache=False,
        )
        assert len(second.calls) == 1

    def test_a_cached_record_is_revalidated_on_the_way_out(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """A record written by an older build can pass the version check and still not fit."""
        cache = ExtractionCache(tmp_path)
        extract_one_pager(
            sample_pdf, settings=settings, extractor=FakeExtractor(GOOD_PAYLOAD), cache=cache
        )
        record = next(tmp_path.glob("*/*.json"))
        broken = json.loads(record.read_text(encoding="utf-8"))
        broken["payload"]["key_risks"]["value"] = ["only one"]
        record.write_text(json.dumps(broken), encoding="utf-8")

        with pytest.raises(Exception, match="key_risks"):
            extract_one_pager(
                sample_pdf,
                settings=settings,
                extractor=FakeExtractor(GOOD_PAYLOAD),
                cache=cache,
            )

    def test_the_cache_key_ignores_settings_that_do_not_change_the_answer(
        self, settings: Settings
    ) -> None:
        keys = set(cache_options(settings))
        assert "effort" in keys and "max_slides" in keys
        assert "provider" not in keys

    def test_the_cost_line_reports_a_cache_hit_as_free(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        cache = ExtractionCache(tmp_path)
        extract_one_pager(
            sample_pdf, settings=settings, extractor=FakeExtractor(GOOD_PAYLOAD), cache=cache
        )
        hit = extract_one_pager(
            sample_pdf, settings=settings, extractor=FakeExtractor(GOOD_PAYLOAD), cache=cache
        )
        assert "no tokens spent" in cost_line(hit, 0.2)

    def test_a_document_that_is_not_a_deck_is_flagged(
        self, settings: Settings, sample_pdf: Path, tmp_path: Path
    ) -> None:
        payload = {"missing_information": {"value": [NOT_A_DECK], "confidence": 1.0}}
        one_pager = extract_one_pager(
            sample_pdf,
            settings=settings,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path),
        )
        assert not one_pager.is_pitch_deck
