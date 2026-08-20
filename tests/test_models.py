"""The FAQ contract: the Field wrapper, and the completeness rule.

The rules worth testing are the ones that protect the reader — an answer cannot exist
without the evidence for it, and a document cannot come back with the awkward questions
quietly missing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deckpager.models import (
    DEFAULT_MIN_CONFIDENCE,
    NOT_A_DECK,
    AnswerText,
    FaqDraft,
    FaqEntry,
    Field,
    tool_schema,
)
from deckpager.questions import QUESTION_COUNT, QUESTION_IDS


def _entries(count: int = QUESTION_COUNT) -> list[FaqEntry]:
    return [FaqEntry(question_id=qid) for qid in QUESTION_IDS[:count]]


class TestCompleteness:
    """A fixed question set is only worth having if it cannot be dodged."""

    def test_all_twenty_questions_are_required(self) -> None:
        with pytest.raises(ValidationError):
            FaqDraft(entries=_entries(19))

    def test_a_repeated_question_is_refused(self) -> None:
        entries = _entries()
        entries[5] = FaqEntry(question_id=entries[0].question_id)
        with pytest.raises(ValidationError) as exc:
            FaqDraft(entries=entries)
        assert "repeated" in str(exc.value) or "no answer for" in str(exc.value)

    def test_an_unknown_question_id_is_refused_with_the_valid_ones(self) -> None:
        with pytest.raises(ValidationError) as exc:
            FaqEntry(question_id="what-is-your-favourite-colour")
        assert "not a TEN Capital question id" in str(exc.value)

    def test_entries_render_in_catalogue_order_whatever_order_they_arrive_in(self) -> None:
        entries = _entries()
        draft = FaqDraft(entries=list(reversed(entries)))
        assert [e.question_id for e in draft.ordered_entries()] == list(QUESTION_IDS)


class TestProvenance:
    def test_an_answer_defaults_to_unanswered(self) -> None:
        draft = FaqDraft(entries=_entries())
        assert draft.answered_count() == 0
        assert len(draft.unanswered()) == QUESTION_COUNT

    def test_slide_numbers_are_one_based(self) -> None:
        with pytest.raises(ValidationError):
            Field[AnswerText](value="An answer.", confidence=0.9, source_slides=[0])

    def test_cited_slides_gathers_answers_and_header_fields(self) -> None:
        entries = _entries()
        entries[0] = FaqEntry(
            question_id=QUESTION_IDS[0],
            answer=Field[AnswerText](value="Yes.", confidence=0.9, source_slides=[4, 9]),
        )
        draft = FaqDraft(
            company_name=Field(value="Acme", confidence=1.0, source_slides=[1]),
            entries=entries,
        )
        assert draft.cited_slides() == {1, 4, 9}


class TestConfidence:
    def test_low_confidence_answers_are_counted(self) -> None:
        entries = _entries()
        entries[0] = FaqEntry(
            question_id=QUESTION_IDS[0],
            answer=Field[AnswerText](value="Maybe.", confidence=0.3, source_slides=[2]),
        )
        draft = FaqDraft(entries=entries)
        assert [e.question_id for e in draft.low_confidence_entries()] == [
            QUESTION_IDS[0]
        ]

    def test_an_unanswered_question_is_not_low_confidence(self) -> None:
        """Silence is a finding, not a weak answer — counting it as one double-reports."""
        draft = FaqDraft(entries=_entries())
        assert draft.low_confidence_entries(DEFAULT_MIN_CONFIDENCE) == []


class TestNotAPitchDeck:
    def test_a_clean_draft_reads_as_a_pitch_deck(self) -> None:
        assert FaqDraft(entries=_entries()).is_pitch_deck is True

    def test_the_refusal_field_flips_it(self) -> None:
        draft = FaqDraft(
            not_a_pitch_deck_reason=Field(value=NOT_A_DECK, confidence=1.0),
            entries=_entries(),
        )
        assert draft.is_pitch_deck is False


class TestSchema:
    def test_the_schema_pins_the_question_ids(self) -> None:
        """The model picks ids from an enum, so it cannot invent a question."""
        schema = tool_schema()
        enum = schema["$defs"]["FaqEntry"]["properties"]["question_id"]["enum"]  # type: ignore[index]
        assert enum == list(QUESTION_IDS)

    def test_the_schema_fixes_the_entry_count(self) -> None:
        entries = tool_schema()["properties"]["entries"]  # type: ignore[index]
        assert entries["minItems"] == entries["maxItems"] == QUESTION_COUNT
