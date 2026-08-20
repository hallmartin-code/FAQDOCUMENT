"""The one-page guarantee.

The single hardest constraint in the spec, so it is tested against the shapes most likely
to break it: an overstuffed assessment, a very long company name, many null scores, and a
deck large enough that the provenance line grows. If any of these produce two pages, the
artifact has failed at its job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from pitchlens.analysis.schema import SCORECARD_ORDER, Assessment, AssessmentDraft, RunMeta
from pitchlens.errors import OnePagerOverflowError
from pitchlens.render.base import Layout
from pitchlens.render.fit import fit_to_one_page
from pitchlens.render.onepager import OnePagerRenderer, _first_clause, _truncate_words

FIXTURES = Path(__file__).parent / "fixtures"


def _assessment(path: Path = FIXTURES / "sample_assessment.json", **meta_overrides) -> Assessment:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("meta", None)
    payload.pop("scoring", None)
    draft = AssessmentDraft.model_validate(payload)
    fields: dict = {
        "model": "claude-opus-5",
        "provider": "anthropic",
        "source_filename": "deck.pdf",
        "sha256": "a" * 64,
        "slide_count": 18,
        "generated_at": datetime(2026, 1, 15, tzinfo=UTC),
    }
    fields.update(meta_overrides)
    return Assessment.from_draft(draft, RunMeta(**fields))


def _render(assessment: Assessment, tmp_path: Path, paper: str = "letter") -> Path:
    """Render through the fitting ladder, exactly as the pipeline does."""
    renderer = OnePagerRenderer()
    destination = tmp_path / "out_onepager.pdf"
    document, _layout, _notes = fit_to_one_page(
        overflow=lambda layout: renderer.overflow(assessment, layout, paper),
        render=lambda layout: renderer.render_onepager(
            assessment, destination, paper=paper, layout=layout
        ),
    )
    return document


def _pages(document: Path) -> int:
    return len(PdfReader(str(document)).pages)


class TestOnePageGuarantee:
    def test_ordinary_assessment_fits(self, tmp_path: Path) -> None:
        assert _pages(_render(_assessment(), tmp_path)) == 1

    def test_overstuffed_assessment_fits(self, tmp_path: Path) -> None:
        """The fixture deliberately runs every field to its limit."""
        assert _pages(_render(_assessment(FIXTURES / "overstuffed_assessment.json"), tmp_path)) == 1

    @pytest.mark.parametrize("paper", ["letter", "a4"])
    def test_both_paper_sizes_fit(self, tmp_path: Path, paper: str) -> None:
        assert _pages(_render(_assessment(), tmp_path, paper=paper)) == 1

    @pytest.mark.parametrize("length", [40, 90])
    def test_long_company_names_fit(self, tmp_path: Path, length: int) -> None:
        """A 90-character name must not push the verdict chip off the page."""
        a = _assessment()
        a.company_name = "Q" * length
        assert _pages(_render(a, tmp_path)) == 1

    def test_large_slide_count_fits(self, tmp_path: Path) -> None:
        """A 50-slide deck lengthens the provenance line; it must still fit."""
        a = _assessment(slide_count=50)
        assert _pages(_render(a, tmp_path)) == 1

    def test_all_scores_null_fits(self, tmp_path: Path) -> None:
        """An unscoreable deck renders eleven empty states, not a crash."""
        a = _assessment()
        for row in a.scorecard:
            row.value = None
        a.overall_investability = None
        assert _pages(_render(a, tmp_path)) == 1


class TestEmptyStates:
    def test_null_score_renders_insufficient_data(self, tmp_path: Path) -> None:
        a = _assessment()
        a.scorecard[0].value = None
        text = PdfReader(str(_render(a, tmp_path))).pages[0].extract_text()
        assert "insufficient data" in text

    def test_absent_deal_terms_are_stated_not_blank(self, tmp_path: Path) -> None:
        text = PdfReader(str(_render(_assessment(), tmp_path))).pages[0].extract_text()
        assert "Ask not stated in deck" in text
        assert "Sector not stated" in text

    def test_missing_sha256_is_stated(self, tmp_path: Path) -> None:
        a = _assessment()
        a.meta.sha256 = None
        text = PdfReader(str(_render(a, tmp_path))).pages[0].extract_text()
        assert "sha256 unavailable" in text


class TestProvenance:
    def test_evidence_summary_counts_every_claim(self, tmp_path: Path) -> None:
        a = _assessment()
        text = PdfReader(str(_render(a, tmp_path))).pages[0].extract_text()
        claims = a.all_evidence()
        verified = sum(1 for c in claims if c.basis == "FACT")
        assert f"Evidence: {len(claims)} claims" in text
        assert f"{verified} verified" in text

    def test_provenance_names_model_and_provider(self, tmp_path: Path) -> None:
        text = PdfReader(str(_render(_assessment(), tmp_path))).pages[0].extract_text()
        assert "claude-opus-5 via anthropic" in text

    def test_sha256_is_truncated_to_twelve(self, tmp_path: Path) -> None:
        text = PdfReader(str(_render(_assessment(), tmp_path))).pages[0].extract_text()
        assert "sha256 " + "a" * 12 in text
        assert "a" * 20 not in text


class TestVerdictChip:
    @pytest.mark.parametrize(
        ("verdict", "label"),
        [
            ("ADVANCE_TO_PARTNER_MEETING", "ADVANCE"),
            ("MORE_DILIGENCE", "MORE DILIGENCE"),
            ("PASS", "PASS"),
        ],
    )
    def test_each_verdict_renders_its_label(self, tmp_path: Path, verdict: str, label: str) -> None:
        a = _assessment()
        a.ic_view.recommendation = verdict  # type: ignore[assignment]
        text = PdfReader(str(_render(a, tmp_path))).pages[0].extract_text()
        assert label in text


class TestFittingLadder:
    def test_no_reductions_when_it_already_fits(self) -> None:
        document, layout, notes = fit_to_one_page(
            overflow=lambda _layout: 0.0, render=lambda _layout: Path("x.pdf")
        )
        assert notes == []
        assert layout == Layout()

    def test_reductions_are_applied_in_the_specified_order(self) -> None:
        """Cheapest to the reader first: prose, then questions, then type."""
        seen: list[Layout] = []

        def overflow(layout: Layout) -> float:
            seen.append(layout)
            return 60.0  # never fits, so every rung is exercised in order

        with pytest.raises(OnePagerOverflowError):
            fit_to_one_page(overflow=overflow, render=lambda _layout: Path("x.pdf"))

        assert seen[0] == Layout()
        assert seen[1].summary_words is not None, "summary is reduced first"
        # Questions are only touched after the summary has hit its floor.
        first_question_cut = next(i for i, s in enumerate(seen) if s.diligence_questions < 5)
        assert seen[first_question_cut - 1].summary_words == 80

    def test_floors_are_respected(self) -> None:
        seen: list[Layout] = []
        with pytest.raises(OnePagerOverflowError):
            fit_to_one_page(
                overflow=lambda layout: (seen.append(layout), 60.0)[1],
                render=lambda _layout: Path("x"),
            )
        assert min(s.body_pt for s in seen) == 8.0, "body must not go below 8pt"
        assert min(s.diligence_questions for s in seen) == 3, "at least 3 questions survive"
        assert min(s.summary_words or 999 for s in seen) == 80, "summary floor is 80 words"
        assert min(s.line_height for s in seen) == 1.2

    def test_overflow_error_says_what_was_tried(self) -> None:
        with pytest.raises(OnePagerOverflowError) as excinfo:
            fit_to_one_page(overflow=lambda _l: 90.0, render=lambda _l: Path("x.pdf"))
        message = str(excinfo.value)
        assert "overflows by 90pt" in message
        assert "truncate the executive summary" in message
        assert "--full" in message  # points at the way out


class TestTextHelpers:
    def test_truncate_cuts_at_a_sentence_boundary(self) -> None:
        text = "One sentence here. Two sentence here. Three sentence here."
        assert _truncate_words(text, 5).endswith(".")
        assert "Three" not in _truncate_words(text, 5)

    def test_truncate_leaves_short_text_alone(self) -> None:
        assert _truncate_words("Short.", 100) == "Short."

    def test_first_clause_keeps_a_whole_clause(self) -> None:
        assert _first_clause("Runway is short; the plan is credible.") == "Runway is short."


class TestScorecardOrder:
    def test_every_category_but_overall_is_a_sidebar_row(self, tmp_path: Path) -> None:
        text = PdfReader(str(_render(_assessment(), tmp_path))).pages[0].extract_text()
        for name in SCORECARD_ORDER:
            if name == "Overall Investability":
                continue
            assert name in text, f"{name} missing from the scorecard"
