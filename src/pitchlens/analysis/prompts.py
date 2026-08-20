"""Request assembly.

The analyst persona is NOT in this file. It lives in `prompts/analyst_system.md`, is
versioned alongside the code, and is editable by an analyst without touching Python. The
deck payload and repair turns come from `prompts/extraction_user.md` and
`prompts/repair_user.md`. This module loads those files and assembles them into API
content blocks.

`OUTPUT_CONTRACT` is the one piece of prompt text still generated in code, deliberately:
it names the exact enum values and scorecard rows that `schema.py` enforces, built from
the same constants the validators use, so the prompt and the validators cannot drift. It
states mechanical field rules, not analytical judgment.
"""

from __future__ import annotations

from string import Template
from typing import Any

from pydantic import BaseModel

from pitchlens.analysis.schema import (
    RISK_ORDER,
    SCORECARD_ORDER,
    AssessmentDraft,
    DiligenceQuestion,
    Evidence,
    ICView,
    Recommendation,
    Risk,
    Score,
    SectionAssessment,
)
from pitchlens.ingest.models import Deck
from pitchlens.paths import require_sections

TOOL_NAME = "submit_assessment"

#: Max repair turns after the initial attempt, mirrored into the repair prompt's text.
MAX_REPAIRS = 2


def system_prompt() -> str:
    """The venture-partner persona, verbatim from `prompts/analyst_system.md`."""
    from pitchlens.paths import read_prompt

    return read_prompt("analyst_system.md")


#: Text fields whose length the validators enforce, as (model, field) pairs. The limits
#: themselves are read off the models — stating a number here would be the drift this
#: module exists to prevent.
_LENGTH_FIELDS: tuple[tuple[type[BaseModel], str, str], ...] = (
    (AssessmentDraft, "executive_summary", "executive_summary"),
    (ICView, "advance_rationale", "ic_view.advance_rationale"),
    (SectionAssessment, "narrative", "founder.narrative / team.narrative"),
    (Evidence, "claim", "every evidence `claim`"),
    (Evidence, "quote", "every evidence `quote`"),
    (Risk, "rationale", "every risk `rationale`"),
    (Score, "justification", "every scorecard `justification`"),
    (DiligenceQuestion, "question", "every diligence `question`"),
    (Recommendation, "action", "every recommendation `action`"),
)


def _max_length(model: type[BaseModel], field: str) -> int | None:
    """Read a field's max_length off the model, so the prompt cannot drift from it."""
    for meta in model.model_fields[field].metadata:
        if (limit := getattr(meta, "max_length", None)) is not None:
            return int(limit)
    return None


def _length_limits() -> str:
    """The character ceilings, generated from the schema.

    Omitting these was a real failure: without them the model wrote a 900-character IC
    rationale, failed validation twice, and burned two Opus calls and five minutes before
    the run died. A limit the model is never told is a limit it cannot respect.
    """
    lines: list[str] = []
    for model, field, label in _LENGTH_FIELDS:
        if (limit := _max_length(model, field)) is not None:
            lines.append(f"  {label}: at most {limit} characters")
    return "\n".join(lines)


def _output_contract() -> str:
    """Build the schema-facing addendum from the same constants the validators use."""
    scorecard = "\n".join(f"  {i}. {name}" for i, name in enumerate(SCORECARD_ORDER, 1))
    risks = "\n".join(f"  - {name}" for name in RISK_ORDER)
    limits = _length_limits()
    return f"""\
OUTPUT FORMAT

Return your assessment by calling the `{TOOL_NAME}` tool. Do not write prose outside the tool
call. The following constraints are validated on receipt — a violation is rejected and sent
back to you, so satisfy them the first time.

EVIDENCE OBJECTS
  basis="FACT"        -> `quote` is required and must copy language that actually appears on
                         the cited slide, and `slide_refs` must be non-empty.
  basis="INFERENCE"   -> `slide_refs` must be non-empty (the slides you reasoned from).
                         `quote` is optional.
  basis="SPECULATION" -> `slide_refs` must be EMPTY and `quote` must be omitted.
  `slide_refs` are 1-based slide numbers matching the "Slide N" labels in the transcript below.

SCORECARD — exactly these eleven entries, in this order, no more and no fewer:
{scorecard}
  Score each 1-10, or `null` where the deck gives no basis for the category. A null is a
  finding: name the gap in `data_gaps`. Do not guess a score to avoid leaving one empty.
  `overall_investability` is your own judgment. It is NOT averaged from the rows above and
  it does NOT have to match the "Overall Investability" row — pitchlens computes a weighted
  score separately and records any disagreement rather than rejecting it.

RISKS — you must rate at least these categories, using these exact names:
{risks}
  Additional risk categories may follow. Each risk needs a level, a rationale, and evidence.

IC VIEW
  `recommendation` is one of ADVANCE_TO_PARTNER_MEETING, MORE_DILIGENCE, or PASS.
  `confidence` is one of HIGH, MEDIUM, or LOW, upper-case.
  `biggest_strengths` and `biggest_concerns`: at most five entries each, most important first.
  `diligence_questions`: order them most critical first — the top five are printed on the
  one-pager the investment committee actually reads.

RECOMMENDATIONS
  `target="company"` improves the business; `target="narrative"` improves the fundraising
  story or the deck itself. Prioritize; do not pad the list.

LENGTH LIMITS — these are hard validator ceilings, not style guidance. Exceeding one is
rejected and sent back to you, which wastes a full round trip. Write to fit the first time;
being brief here costs nothing, because the detail belongs in the evidence objects.
{limits}
  The executive summary is printed on a one-page memo at 9pt. Aim for 100-130 words — well
  under the ceiling — and lead with the verdict, not with background.
"""


OUTPUT_CONTRACT = _output_contract()


def build_system_blocks() -> list[dict[str, Any]]:
    """The cached system prompt: persona first, then the output contract."""
    return [
        {"type": "text", "text": system_prompt()},
        {
            "type": "text",
            "text": OUTPUT_CONTRACT,
            # Persona and contract are byte-stable across runs, so the whole prefix caches.
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _transcript(deck: Deck) -> str:
    """Per-slide extracted text, labeled so the model's citations can be verified locally.

    Only the slide labels are built here; the surrounding instructions live in
    `prompts/extraction_user.md` and are substituted around this block.
    """
    chunks: list[str] = []
    for slide in deck.slides:
        chunks.append(f"--- Slide {slide.index} ---")
        if slide.title:
            chunks.append(f"[title] {slide.title}")
        chunks.append(slide.text if slide.text else "[no extractable text on this slide]")
        if slide.notes:
            chunks.append(f"[speaker notes] {slide.notes}")
        chunks.append("")
    return "\n".join(chunks).strip()


def build_user_blocks(deck: Deck, context: str | None = None) -> list[dict[str, Any]]:
    """Assemble the user turn: deck content, extracted transcript, then operator context."""
    blocks: list[dict[str, Any]] = []

    if deck.raw_pdf_b64 is not None:
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": deck.raw_pdf_b64,
                },
            }
        )
    else:
        for slide in deck.slides:
            if slide.asset is None:
                continue
            blocks.append({"type": "text", "text": f"Slide {slide.index}:"})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": slide.asset.media_type,
                        "data": slide.asset.data_b64,
                    },
                }
            )

    sections = require_sections(
        "extraction_user.md", "deck_payload", "operator_context", "instruction"
    )

    blocks.append(
        {
            "type": "text",
            "text": Template(sections["deck_payload"]).substitute(transcript=_transcript(deck)),
        }
    )

    # Context before the instruction: the model should read the analyst's framing before
    # being told to start, not after.
    if context and context.strip():
        blocks.append(
            {
                "type": "text",
                "text": Template(sections["operator_context"]).substitute(context=context.strip()),
            }
        )

    blocks.append(
        {
            "type": "text",
            "text": Template(sections["instruction"]).substitute(
                slide_count=deck.slide_count,
                source_filename=deck.source_path.name,
                tool_name=TOOL_NAME,
            ),
        }
    )
    return blocks


def build_retry_blocks(tool_use_id: str, errors: str, attempt: int = 1) -> list[dict[str, Any]]:
    """The follow-up user turn sent after a schema validation failure.

    A `tool_use` block must be answered by a `tool_result` with the matching id in the very
    next message, so the validation errors are delivered as an errored tool result rather
    than as a bare text turn.
    """
    sections = require_sections("repair_user.md", "tool_result", "instruction")
    return [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": Template(sections["tool_result"]).substitute(errors=errors),
        },
        {
            "type": "text",
            "text": Template(sections["instruction"]).substitute(
                attempt=attempt, max_attempts=MAX_REPAIRS
            ),
        },
    ]
