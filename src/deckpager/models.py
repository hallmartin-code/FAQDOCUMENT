"""The FAQ output contract.

This module is the single source of truth for what deckpager extracts. The JSON schema
handed to the API is generated from `FaqDraft.model_json_schema()` — never hand-written —
so the schema the model is held to and the model this code validates against cannot drift
apart.

Two rules are enforced here rather than requested politely in the prompt:

* Every answer is wrapped in `Field`, which carries the slide numbers it came from and how
  confident the model was. An answer with no provenance cannot be represented.
* The twenty questions are fixed. `entries` must contain each catalogue id exactly once,
  so a deck cannot be handed back with the awkward questions quietly dropped — the model
  must answer every question or record that the document does not address it.

`Field` is spelled with `Generic[T]` rather than the `class Field[T]` syntax, because that
syntax is a SyntaxError before Python 3.12. The resulting schema is identical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

import pydantic
from pydantic import BaseModel, ConfigDict, StringConstraints, conlist, model_validator

from deckpager.questions import QUESTION_COUNT, QUESTION_IDS, Question, by_id

T = TypeVar("T")

Stage = Literal[
    "Pre-Seed",
    "Seed",
    "Series A",
    "Series B",
    "Series C+",
    "Growth",
    "Unknown",
]

#: What the model must say when handed something that is not a pitch deck.
NOT_A_DECK = "Document does not appear to be a pitch deck"

#: Below this, an answer is flagged in the rendered FAQ. The CLI can override per run.
DEFAULT_MIN_CONFIDENCE = 0.6

#: What the FAQ prints where the document says nothing. Not an em dash: a reader of a
#: question-and-answer document needs a sentence, and this sentence is the finding.
UNANSWERED = "Not addressed in the document."

Line90 = Annotated[str, StringConstraints(max_length=90)]
Text200 = Annotated[str, StringConstraints(max_length=200)]
Short = Annotated[str, StringConstraints(max_length=120)]

#: An answer is a short paragraph. Long enough to carry figures and their caveats, short
#: enough that twenty of them stay readable in one sitting.
#:
#: 900 was the first guess and it was wrong: on a real 30-slide deck the model exceeded it
#: on two of twenty answers, which cost a full correction retry — and a retry re-sends the
#: whole deck, so the cheapest possible fix is a limit the good answers fit inside.
AnswerText = Annotated[str, StringConstraints(max_length=1200)]


class Field(BaseModel, Generic[T]):
    """One extracted value, with the evidence for it.

    `value` is None when the document does not support it. That is a finding, not a
    failure: a plausible guess is forbidden. A populated value with an empty
    `source_slides` is the shape this wrapper exists to make impossible to produce
    accidentally.
    """

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    confidence: float = pydantic.Field(default=0.0, ge=0.0, le=1.0)
    source_slides: list[int] = pydantic.Field(default_factory=list)
    note: str | None = pydantic.Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_provenance(self) -> Field[T]:
        if self.value is None:
            return self
        if any(slide < 1 for slide in self.source_slides):
            raise ValueError("source_slides are 1-based numbers; values must be >= 1")
        return self

    @property
    def is_present(self) -> bool:
        """Whether the document supported this at all."""
        return self.value is not None

    def is_low_confidence(self, threshold: float = DEFAULT_MIN_CONFIDENCE) -> bool:
        """Whether this should render with the low-confidence marker."""
        return self.is_present and self.confidence < threshold


class FaqEntry(BaseModel):
    """One question's answer, pinned to a catalogue id."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = pydantic.Field(
        json_schema_extra={"enum": list(QUESTION_IDS)},
        description="The TEN Capital question this answers. Use each id exactly once.",
    )
    answer: Field[AnswerText] = pydantic.Field(
        default_factory=lambda: Field[AnswerText]()
    )

    @model_validator(mode="after")
    def _known_id(self) -> FaqEntry:
        # KeyError travels straight out of pydantic; only ValueError becomes a
        # ValidationError, and only a ValidationError reaches the correction retry. An
        # invented question id has to be a correctable mistake, not a crash.
        try:
            by_id(self.question_id)
        except KeyError as exc:
            raise ValueError(str(exc.args[0])) from None
        return self

    @property
    def question(self) -> Question:
        """The catalogue entry this answers."""
        return by_id(self.question_id)

    @property
    def is_answered(self) -> bool:
        """Whether the document actually answered this question."""
        return self.answer.is_present


class FaqDraft(BaseModel):
    """Everything the model is asked to produce. Provenance is added by the pipeline."""

    model_config = ConfigDict(extra="forbid")

    company_name: Field[Short] = pydantic.Field(default_factory=lambda: Field[Short]())
    tagline: Field[Line90] = pydantic.Field(default_factory=lambda: Field[Line90]())
    sector: Field[Short] = pydantic.Field(default_factory=lambda: Field[Short]())
    stage: Field[Stage] = pydantic.Field(default_factory=lambda: Field[Stage]())

    not_a_pitch_deck_reason: Field[Text200] = pydantic.Field(
        default_factory=lambda: Field[Text200](),
        description=(
            "Populate ONLY when the document is not a pitch deck or company document. "
            f'Then set company_name to null and put "{NOT_A_DECK}" here.'
        ),
    )

    entries: conlist(  # type: ignore[valid-type]
        FaqEntry, min_length=QUESTION_COUNT, max_length=QUESTION_COUNT
    ) = pydantic.Field(
        description=f"Exactly {QUESTION_COUNT} answers, one per question id."
    )

    @model_validator(mode="after")
    def _every_question_once(self) -> FaqDraft:
        """Each catalogue id appears exactly once.

        The whole value of a fixed question set is that a deck cannot dodge a question by
        omitting it. Enforced in the schema so a violation costs one correction retry
        rather than producing a quietly incomplete document.
        """
        seen = [entry.question_id for entry in self.entries]
        duplicates = {qid for qid in seen if seen.count(qid) > 1}
        if duplicates:
            raise ValueError(f"question_id repeated: {', '.join(sorted(duplicates))}")
        missing = [qid for qid in QUESTION_IDS if qid not in seen]
        if missing:
            raise ValueError(f"no answer for: {', '.join(missing)}")
        return self

    @property
    def is_pitch_deck(self) -> bool:
        """False when the model reported the document is not a pitch deck."""
        return self.not_a_pitch_deck_reason.value is None

    def ordered_entries(self) -> list[FaqEntry]:
        """The answers in catalogue order, whatever order the model returned them in."""
        index = {entry.question_id: entry for entry in self.entries}
        return [index[qid] for qid in QUESTION_IDS]

    def header_fields(self) -> dict[str, Field[object]]:
        """The non-answer fields, for counting and rendering."""
        return {
            name: getattr(self, name)
            for name in type(self).model_fields
            if isinstance(getattr(self, name), Field)
        }

    def answered_count(self) -> int:
        """How many of the questions the document actually answered."""
        return sum(1 for entry in self.entries if entry.is_answered)

    def unanswered(self) -> list[Question]:
        """The questions the document does not address — the diligence list."""
        return [
            entry.question for entry in self.ordered_entries() if not entry.is_answered
        ]

    def low_confidence_entries(
        self, threshold: float = DEFAULT_MIN_CONFIDENCE
    ) -> list[FaqEntry]:
        """Answers below the flag threshold."""
        return [
            entry
            for entry in self.ordered_entries()
            if entry.answer.is_low_confidence(threshold)
        ]

    def low_confidence_fields(
        self, threshold: float = DEFAULT_MIN_CONFIDENCE
    ) -> list[str]:
        """Names of everything below the flag threshold — the footer counter."""
        flagged = [
            name
            for name, field in self.header_fields().items()
            if field.is_low_confidence(threshold)
        ]
        flagged.extend(
            entry.question_id for entry in self.low_confidence_entries(threshold)
        )
        return flagged

    def cited_slides(self) -> set[int]:
        """Every slide number cited anywhere, for checking citations against the deck."""
        cited = {
            slide
            for field in self.header_fields().values()
            for slide in field.source_slides
        }
        for entry in self.entries:
            cited.update(entry.answer.source_slides)
        return cited


class Provenance(BaseModel):
    """Where this FAQ came from and what it cost. Written by the pipeline."""

    model_config = ConfigDict(extra="forbid")

    source_filename: str
    source_page_count: int = pydantic.Field(ge=0)
    extracted_at: datetime
    model: str
    input_tokens: int = pydantic.Field(default=0, ge=0)
    output_tokens: int = pydantic.Field(default=0, ge=0)
    estimated_cost_usd: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        description="None when this model is not in the local price table.",
    )
    cached: bool = pydantic.Field(
        default=False, description="Whether this came from the extraction cache."
    )
    ingest_warnings: list[str] = pydantic.Field(default_factory=list)
    citation_warnings: list[str] = pydantic.Field(
        default_factory=list,
        description="Slide citations that do not correspond to a slide in the deck.",
    )
    truncations: list[str] = pydantic.Field(
        default_factory=list,
        description="What the renderer shortened. Kept for pipeline compatibility.",
    )


class Faq(FaqDraft):
    """A draft plus its provenance. This is what is written to disk and rendered."""

    provenance: Provenance

    @classmethod
    def from_draft(cls, draft: FaqDraft, provenance: Provenance) -> Faq:
        """Stamp a draft with provenance without re-validating the model output."""
        return cls.model_construct(**draft.__dict__, provenance=provenance)


def tool_schema() -> dict[str, object]:
    """The JSON schema handed to the API as the extraction tool's `input_schema`.

    The draft, not the full FAQ: provenance is measured by the pipeline — token counts,
    cost, the filename — and asking the model for it would invite it to invent numbers
    that we already know exactly.
    """
    return FaqDraft.model_json_schema()
