"""The FAQ layout.

The one-page guarantee this file used to defend is gone with the one-pager: twenty
questions paginate, and truncating an answer to save a page would destroy the thing the
document exists to carry. What replaces it is the guarantee that nothing is lost — every
question appears, every answer appears in full, and silence is printed rather than
skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from deckpager.errors import RenderError
from deckpager.models import UNANSWERED
from deckpager.questions import QUESTIONS, QUESTION_COUNT
from deckpager.render.faq import FaqRenderer, citation, escape


def _text(pdf: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(str(pdf)).pages)


@pytest.fixture
def rendered(tmp_path: Path, faq_factory: Any) -> tuple[Path, Any]:
    faq = faq_factory(answered=15)
    out = FaqRenderer().render(faq, tmp_path / "faq.pdf")
    return out, faq


class TestNothingIsLost:
    """The contract that replaced the one-page rule."""

    def test_every_question_appears(self, rendered: tuple[Path, Any]) -> None:
        pdf, _ = rendered
        text = " ".join(_text(pdf).split())
        missing = [q.text for q in QUESTIONS if " ".join(q.text.split()) not in text]
        assert missing == []

    def test_answers_are_printed_in_full(self, tmp_path: Path, faq_factory: Any) -> None:
        """No ellipsis, no fitting ladder: a long answer sets the page count instead."""
        sentence = "The deck states a specific and rather long claim on this subject. "
        faq = faq_factory(answer_text=(sentence * 15)[:1190])
        pdf = FaqRenderer().render(faq, tmp_path / "long.pdf")
        text = " ".join(_text(pdf).split())
        assert text.count("The deck states a specific") >= QUESTION_COUNT
        assert "…" not in text and "..." not in text

    def test_unanswered_questions_say_so(self, rendered: tuple[Path, Any]) -> None:
        pdf, faq = rendered
        text = _text(pdf)
        assert UNANSWERED.rstrip(".") in text
        assert text.count(UNANSWERED.rstrip(".")) >= len(faq.unanswered())

    def test_the_open_items_list_repeats_every_unanswered_question(
        self, rendered: tuple[Path, Any]
    ) -> None:
        """Scattered through twenty answers, silence is easy to miss; gathered, it is the
        agenda for the founder call."""
        pdf, faq = rendered
        text = " ".join(_text(pdf).split())
        assert "OPEN DILIGENCE ITEMS" in text
        for question in faq.unanswered():
            assert " ".join(question.text.split()) in text


class TestPagination:
    def test_a_full_faq_runs_to_several_pages(self, rendered: tuple[Path, Any]) -> None:
        pdf, _ = rendered
        assert len(PdfReader(str(pdf)).pages) >= 2

    def test_a_sparse_document_produces_a_shorter_faq(
        self, tmp_path: Path, faq_factory: Any
    ) -> None:
        """A deck that says little must not be padded into looking substantial.

        Measured in rendered text rather than pages: page count is too coarse to express
        this — with short answers a 0-answer and a 20-answer FAQ both land on two pages,
        and the property being defended is that empty stays visibly empty.
        """
        sparse = FaqRenderer().render(faq_factory(answered=0), tmp_path / "sparse.pdf")
        full = FaqRenderer().render(faq_factory(answered=20), tmp_path / "full.pdf")
        assert len(_text(sparse)) < len(_text(full))

    def test_a4_is_accepted_and_an_unknown_paper_is_refused(
        self, tmp_path: Path, faq_factory: Any
    ) -> None:
        FaqRenderer().render(faq_factory(), tmp_path / "a4.pdf", paper="a4")
        with pytest.raises(RenderError, match="Unknown paper"):
            FaqRenderer().render(faq_factory(), tmp_path / "no.pdf", paper="foolscap")


class TestProvenanceOnThePage:
    def test_each_answer_carries_its_slides(self, rendered: tuple[Path, Any]) -> None:
        pdf, _ = rendered
        assert "Slide 1" in _text(pdf)

    def test_low_confidence_is_marked(self, tmp_path: Path, faq_factory: Any) -> None:
        faq = faq_factory(confidence=0.3)
        pdf = FaqRenderer().render(faq, tmp_path / "weak.pdf")
        assert "low confidence" in _text(pdf)

    def test_a_confident_answer_is_not_marked(
        self, tmp_path: Path, faq_factory: Any
    ) -> None:
        faq = faq_factory(confidence=0.95)
        pdf = FaqRenderer().render(faq, tmp_path / "strong.pdf")
        assert "low confidence" not in _text(pdf)

    def test_the_citation_line_is_empty_without_slides(self, faq_factory: Any) -> None:
        """An answer with no provenance prints no citation rather than an empty label."""
        faq = faq_factory()
        entry = faq.ordered_entries()[0]
        entry.answer.source_slides = []
        assert citation(entry, 0.6) == ""


class TestFooterAndSafety:
    def test_the_footer_follows_the_house_standard(
        self, rendered: tuple[Path, Any]
    ) -> None:
        text = _text(rendered[0])
        assert "Investor FAQ" in text
        assert "Compiled on" in text and "TEN Capital Network" in text
        assert "Internal use only" in text

    def test_markup_in_an_answer_cannot_break_the_document(
        self, tmp_path: Path, faq_factory: Any
    ) -> None:
        """ReportLab reads a Paragraph as markup; a deck quoting <b> must not crash it."""
        faq = faq_factory(answer_text="Margins rose <b>40%</b> & held. 5 > 3.")
        pdf = FaqRenderer().render(faq, tmp_path / "markup.pdf")
        assert "40%" in _text(pdf)

    def test_escape_covers_the_three_characters_that_matter(self) -> None:
        assert escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"

    def test_the_company_falls_back_to_the_filename(
        self, tmp_path: Path, faq_factory: Any
    ) -> None:
        faq = faq_factory(company=None, source_filename="unnamed_deck.pdf")
        pdf = FaqRenderer().render(faq, tmp_path / "noname.pdf")
        assert "unnamed_deck" in _text(pdf)
