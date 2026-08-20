"""The FAQ template is a contract, not documentation.

`templates/faq.md` states the document structure the renderer lays out: the blocks, the
twenty questions, the data contract, the vocabularies, and the fixed strings. If
`questions.py`, `models.py`, or `render/faq.py` changes and the template does not, the
renderer is being built against a stale map — so the drift is caught here rather than
discovered in a partner meeting.

Every assertion below reads the template as text and checks it against the code. None of
them check prose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deckpager.models import UNANSWERED, AnswerText, FaqDraft, Stage
from deckpager.questions import QUESTIONS, QUESTION_COUNT, STAGES
from deckpager.render import faq as faq_render

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "faq.md"


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


class TestTheTemplateExists:
    def test_it_is_shipped(self) -> None:
        assert TEMPLATE.is_file(), f"{TEMPLATE} is the renderer's contract and must exist"


class TestQuestionCatalogue:
    """The catalogue is the product. Drift here is drift in what TEN Capital asks."""

    def test_every_question_id_is_listed(self, template: str) -> None:
        missing = [q.id for q in QUESTIONS if f"`{q.id}`" not in template]
        assert missing == [], f"template does not document: {missing}"

    def test_every_question_text_is_listed(self, template: str) -> None:
        flat = " ".join(template.split())
        missing = [q.text for q in QUESTIONS if " ".join(q.text.split()) not in flat]
        assert missing == [], f"template wording drifted from questions.py: {missing}"

    def test_the_count_is_stated(self, template: str) -> None:
        assert str(QUESTION_COUNT) in template
        assert QUESTION_COUNT == len(QUESTIONS)

    def test_every_stage_is_named(self, template: str) -> None:
        missing = [stage for stage in STAGES if stage not in template]
        assert missing == []

    def test_no_question_the_code_does_not_define(self, template: str) -> None:
        """A row left behind by a deleted question would advertise one nobody asks."""
        import re

        documented = set(re.findall(r"`([a-z]+-[a-z-]+)`", template))
        known = {q.id for q in QUESTIONS}
        # Ids are the only backticked kebab-case tokens shaped like this in §3.
        assert documented - known - {"source_slides", "not_a_pitch_deck_reason"} <= set()


class TestDataContract:
    def test_the_answer_limit_matches_the_schema(self, template: str) -> None:
        limit = AnswerText.__metadata__[0].max_length  # type: ignore[attr-defined]
        assert str(limit) in template, f"template states a stale answer limit, not {limit}"

    def test_the_stage_vocabulary_matches(self, template: str) -> None:
        for value in Stage.__args__:  # type: ignore[attr-defined]
            assert f"`{value}`" in template

    def test_the_header_fields_are_documented(self, template: str) -> None:
        header = {
            name
            for name in FaqDraft.model_fields
            if name not in {"entries"}
        }
        missing = [name for name in header if f"`{name}`" not in template]
        assert missing == []


class TestFixedStrings:
    def test_the_unanswered_line_matches(self, template: str) -> None:
        assert UNANSWERED in template

    def test_the_document_title_matches(self, template: str) -> None:
        assert faq_render.TITLE in template

    def test_the_dagger_matches(self, template: str) -> None:
        from deckpager.render import style as s

        assert s.DAGGER in template


class TestTheOnePagerIsGone:
    def test_the_superseded_template_is_not_still_shipped(self) -> None:
        """Two structural contracts in the same folder is one too many to keep true."""
        assert not (TEMPLATE.parent / "onepager.md").exists()
