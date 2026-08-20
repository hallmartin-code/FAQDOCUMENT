"""The Claude call: forced tool use, retries, and cost accounting.

Every API interaction in deckpager goes through `Extractor`. `AnthropicExtractor` talks to
the Claude API; `FakeExtractor` replays a recorded payload, which is how the pipeline is
tested without a network call.

Two retry mechanisms sit on top of each other and are not the same thing. Transport
failures — 429, 5xx, dropped connections — are retried by the SDK with exponential backoff,
configured here to spec §8's four attempts. A schema violation is not a transport failure:
the API succeeded and the model was wrong, so it gets exactly one correction turn with the
validation errors handed back, and then the run fails loudly.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
from pydantic import ValidationError

from deckpager.config import Settings
from deckpager.errors import AnalysisError, SchemaValidationError
from deckpager.extract.prompts import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_retry_blocks,
    build_user_blocks,
    system_blocks,
)
from deckpager.ingest.models import Deck
from deckpager.models import FaqDraft, tool_schema

#: Spec §8: four attempts on 429/5xx. The SDK owns the backoff, which is exponential with
#: jitter and honours a Retry-After header — better behaviour than a hand-rolled loop, and
#: one fewer thing here to get wrong.
MAX_ATTEMPTS = 4

#: USD per million tokens, input / output. A local table cannot help going stale, so an
#: unknown model reports no cost rather than a confidently wrong one.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """What the call cost, or None if this model is not in the table."""
    price = PRICING.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


@dataclass
class Usage:
    """What one extraction consumed."""

    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float | None = None


@dataclass
class Extraction:
    """A validated draft and what it took to get it."""

    draft: FaqDraft
    usage: Usage = field(default_factory=Usage)


class Extractor(Protocol):
    """Anything that can turn a deck into a validated one-pager draft."""

    def extract(self, deck: Deck) -> Extraction:
        """Produce a schema-valid draft for `deck`."""
        ...


def build_tool() -> dict[str, Any]:
    """The tool definition, with its input schema generated from the pydantic models."""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": tool_schema(),
    }


def format_validation_errors(exc: ValidationError) -> str:
    """Render pydantic errors as a numbered list the model can act on."""
    lines: list[str] = []
    for number, error in enumerate(exc.errors(), start=1):
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{number}. {location}: {error['msg']}")
    return "\n".join(lines)


def parse_tool_payload(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Strictly parse one tool block's accumulated JSON.

    Returns `(payload, None)` or `(None, message)`. The SDK parses tool input leniently, so
    a truncated payload — or one the model emitted as several concatenated objects — comes
    back as a plausible but wrongly-flattened dict. Parsing strictly turns that into one
    actionable message instead of dozens of misleading schema violations pointing at fields
    the model never got wrong.
    """
    text = raw.strip()
    if not text:
        return None, "The tool call carried an empty payload."
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        try:
            _, end = decoder.raw_decode(text)
        except ValueError:
            return None, (
                f"The payload was not valid JSON ({exc}). If the summary was long, the "
                f"output token budget may have run out mid-response."
            )
        return None, (
            f"The payload contained {len(text) - end} characters of trailing data after the "
            f"first complete JSON object. Send the entire summary as exactly one JSON "
            f"object, not several concatenated objects."
        )
    if not isinstance(payload, dict):
        return None, f"The payload was a JSON {type(payload).__name__}, not an object."
    return payload, None


@dataclass
class _Attempt:
    """One model round trip: what it called the tool with, and whether that was usable."""

    tool_use_id: str
    content: list[Any]
    payload: dict[str, Any] | None
    error: str | None
    input_tokens: int
    output_tokens: int


class AnthropicExtractor:
    """Calls the Claude API with the tool-use contract and validates the result."""

    def __init__(
        self,
        settings: Settings,
        client: anthropic.Anthropic | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.require_api_key("anthropic"),
            max_retries=MAX_ATTEMPTS - 1,
        )
        self._on_retry = on_retry

    def extract(self, deck: Deck) -> Extraction:
        """Run the extraction, retrying once with the failure fed back to the model."""
        started = time.monotonic()
        messages: list[dict[str, Any]] = [{"role": "user", "content": build_user_blocks(deck)}]

        first = self._call(messages)
        errors = self._problem(first)
        if errors is None:
            return self._finish(first, None, started)

        if self._on_retry is not None:
            self._on_retry(errors)

        messages.append({"role": "assistant", "content": first.content})
        messages.append({"role": "user", "content": build_retry_blocks(first.tool_use_id, errors)})

        second = self._call(messages)
        second_errors = self._problem(second)
        if second_errors is None:
            return self._finish(second, first, started)

        raise SchemaValidationError(
            "The model returned output that does not match the one-pager schema, twice.\n"
            "First attempt:\n"
            f"{errors}\n"
            "Second attempt:\n"
            f"{second_errors}"
        )

    def _finish(
        self, attempt: _Attempt, previous: _Attempt | None, started: float
    ) -> Extraction:
        """Validate the winning attempt and total up what the whole run consumed."""
        assert attempt.payload is not None  # noqa: S101 - narrowed by _problem
        input_tokens = attempt.input_tokens + (previous.input_tokens if previous else 0)
        output_tokens = attempt.output_tokens + (previous.output_tokens if previous else 0)
        return Extraction(
            draft=FaqDraft.model_validate(attempt.payload),
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                seconds=time.monotonic() - started,
                cost_usd=estimate_cost(self.settings.model, input_tokens, output_tokens),
            ),
        )

    @staticmethod
    def _problem(attempt: _Attempt) -> str | None:
        """Describe what is wrong with an attempt, or None if it is usable."""
        if attempt.payload is None:
            return attempt.error
        try:
            FaqDraft.model_validate(attempt.payload)
        except ValidationError as exc:
            return format_validation_errors(exc)
        return None

    def _call(self, messages: list[dict[str, Any]]) -> _Attempt:
        """One API round trip.

        Tool JSON is accumulated per content-block index. A response can contain more than
        one tool block, and concatenating their deltas would splice unrelated JSON together.
        """
        raw_by_index: dict[int, list[str]] = {}
        try:
            with self._client.messages.stream(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=system_blocks(),  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
                tools=[build_tool()],  # type: ignore[list-item]
                # disable_parallel_tool_use is load-bearing: without it the model can emit
                # several parallel submit_faq blocks, splicing partial JSON objects
                # together into an unparseable payload.
                tool_choice={
                    "type": "tool",
                    "name": TOOL_NAME,
                    "disable_parallel_tool_use": True,
                },
                output_config={"effort": self.settings.effort},
            ) as stream:
                for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "input_json_delta"
                    ):
                        raw_by_index.setdefault(event.index, []).append(event.delta.partial_json)
                response = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise AnalysisError(
                f"The Claude API rate limit is still exhausted after {MAX_ATTEMPTS} attempts. "
                f"Wait and re-run — the extraction cache means a later retry costs nothing "
                f"extra for decks that already succeeded. ({exc.message})"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise AnalysisError(
                f"The Claude API rejected the request ({exc.status_code}): {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AnalysisError(f"Could not reach the Claude API: {exc}") from exc

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) or "unspecified"
            raise AnalysisError(
                f"The model declined to summarize this deck (refusal category: {category}). "
                f"No one-pager was produced."
            )

        truncated = response.stop_reason == "max_tokens"
        usage = response.usage

        for index, block in enumerate(response.content):
            if block.type != "tool_use" or block.name != TOOL_NAME:
                continue
            raw = "".join(raw_by_index.get(index, []))
            payload, error = parse_tool_payload(raw) if raw else (dict(block.input), None)
            if error is not None and truncated:
                error = (
                    f"{error} The response hit the {self.settings.max_tokens}-token output "
                    f"limit — be more concise, or raise DECKPAGER_MAX_TOKENS."
                )
            return _Attempt(
                tool_use_id=block.id,
                content=list(response.content),
                payload=payload,
                error=error,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )

        if truncated:
            raise AnalysisError(
                f"The model hit the {self.settings.max_tokens}-token output limit before "
                f"calling `{TOOL_NAME}`. Raise DECKPAGER_MAX_TOKENS and retry."
            )
        kinds = ", ".join(block.type for block in response.content) or "none"
        raise AnalysisError(
            f"The model returned no `{TOOL_NAME}` tool call (content blocks: {kinds})."
        )


class FakeExtractor:
    """Replays a recorded payload. Used by the test suite; never touches the network."""

    def __init__(self, payload: dict[str, Any] | FaqDraft) -> None:
        self._draft = (
            payload
            if isinstance(payload, FaqDraft)
            else FaqDraft.model_validate(payload)
        )
        self.calls: list[Deck] = []

    def extract(self, deck: Deck) -> Extraction:
        """Return the recorded draft, recording the call for assertions."""
        self.calls.append(deck)
        return Extraction(draft=self._draft.model_copy(deep=True))
