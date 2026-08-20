"""The analyzer interface and its two implementations.

Every API interaction in the project goes through `Analyzer`. `AnthropicAnalyzer` talks to
the Claude API; `FakeAnalyzer` replays a recorded fixture, which is how the pipeline is
tested without a network call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anthropic
from pydantic import ValidationError

from deckpager.analysis.prompts import (
    TOOL_NAME,
    build_retry_blocks,
    build_system_blocks,
    build_user_blocks,
)
from deckpager.analysis.schema import AssessmentDraft, assessment_tool_schema
from deckpager.config import Settings
from deckpager.errors import AnalysisError, SchemaValidationError
from deckpager.ingest.models import Deck

TOOL_DESCRIPTION = (
    "Submit the completed investment-grade due diligence assessment. This is the only way "
    "to return your analysis; every field is validated on receipt."
)


class Analyzer(Protocol):
    """Anything that can turn a deck into a validated assessment draft."""

    def analyze(self, deck: Deck, *, context: str | None = None) -> AssessmentDraft:
        """Produce a schema-valid assessment for `deck`."""
        ...


def build_tool() -> dict[str, Any]:
    """The tool definition, with its input schema generated from the Pydantic models."""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": assessment_tool_schema(),
    }


def _format_validation_errors(exc: ValidationError) -> str:
    """Render Pydantic errors as a numbered list the model can act on."""
    lines: list[str] = []
    for number, error in enumerate(exc.errors(), start=1):
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{number}. {location}: {error['msg']}")
    return "\n".join(lines)


def parse_tool_payload(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Strictly parse one tool block's accumulated JSON.

    Returns `(payload, None)` on success or `(None, message)` on failure. The SDK parses
    tool input leniently, so a payload that is truncated — or that the model emitted as
    several concatenated objects — comes back as a plausible but wrongly-flattened dict.
    Parsing strictly turns that into one actionable message instead of dozens of misleading
    schema violations pointing at fields the model never got wrong.
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
                f"The payload was not valid JSON ({exc}). If the assessment was long, the "
                f"output token budget may have run out mid-response."
            )
        return None, (
            f"The payload contained {len(text) - end} characters of trailing data after the "
            f"first complete JSON object. Send the entire assessment as exactly one JSON "
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


class AnthropicAnalyzer:
    """Calls the Claude API with the tool-use contract and validates the result."""

    def __init__(
        self,
        settings: Settings,
        client: anthropic.Anthropic | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or anthropic.Anthropic(api_key=settings.require_api_key())
        self._on_retry = on_retry

    def analyze(self, deck: Deck, *, context: str | None = None) -> AssessmentDraft:
        """Run the assessment, retrying once with the failure fed back to the model."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": build_user_blocks(deck, context)}
        ]

        first = self._call(messages)
        errors = self._problem(first)
        if errors is None:
            assert first.payload is not None  # noqa: S101 - narrowed by _problem
            return AssessmentDraft.model_validate(first.payload)

        if self._on_retry is not None:
            self._on_retry(errors)

        messages.append({"role": "assistant", "content": first.content})
        messages.append({"role": "user", "content": build_retry_blocks(first.tool_use_id, errors)})

        second = self._call(messages)
        second_errors = self._problem(second)
        if second_errors is None:
            assert second.payload is not None  # noqa: S101 - narrowed by _problem
            return AssessmentDraft.model_validate(second.payload)

        raise SchemaValidationError(
            "The model returned output that does not match the assessment schema, twice.\n"
            "First attempt:\n"
            f"{errors}\n"
            "Second attempt:\n"
            f"{second_errors}"
        )

    @staticmethod
    def _problem(attempt: _Attempt) -> str | None:
        """Describe what is wrong with an attempt, or None if it is usable."""
        if attempt.payload is None:
            return attempt.error
        try:
            AssessmentDraft.model_validate(attempt.payload)
        except ValidationError as exc:
            return _format_validation_errors(exc)
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
                system=build_system_blocks(),  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
                tools=[build_tool()],  # type: ignore[list-item]
                # disable_parallel_tool_use is load-bearing: without it the model can emit
                # dozens of parallel submit_assessment blocks, splicing partial JSON objects
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
                f"The model declined to analyze this deck (refusal category: {category}). "
                f"No memo was produced."
            )

        truncated = response.stop_reason == "max_tokens"

        for index, block in enumerate(response.content):
            if block.type != "tool_use" or block.name != TOOL_NAME:
                continue
            raw = "".join(raw_by_index.get(index, []))
            payload, error = parse_tool_payload(raw) if raw else (dict(block.input), None)
            if error is not None and truncated:
                error = (
                    f"{error} The response hit the {self.settings.max_tokens}-token output "
                    f"limit — be more concise, or the operator should raise DECKPAGER_MAX_TOKENS."
                )
            return _Attempt(
                tool_use_id=block.id, content=list(response.content), payload=payload, error=error
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


class FakeAnalyzer:
    """Replays a recorded assessment fixture. Used by the test suite; never hits the network."""

    def __init__(self, fixture: Path | AssessmentDraft) -> None:
        if isinstance(fixture, AssessmentDraft):
            self._draft = fixture
        else:
            payload = json.loads(Path(fixture).read_text(encoding="utf-8"))
            # `meta` and `scoring` are stamped by the pipeline, never by a model.
            for key in ("meta", "scoring"):
                payload.pop(key, None)
            self._draft = AssessmentDraft.model_validate(payload)
        self.calls: list[tuple[Deck, str | None]] = []

    def analyze(self, deck: Deck, *, context: str | None = None) -> AssessmentDraft:
        """Return the recorded draft, recording the call for assertions."""
        self.calls.append((deck, context))
        return self._draft.model_copy(deep=True)
