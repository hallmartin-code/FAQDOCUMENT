"""Grounding tests, plus the offline end-to-end pipeline run against FakeAnalyzer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from deckpager.analysis.client import FakeAnalyzer
from deckpager.analysis.grounding import OVERLAP_THRESHOLD, ground, overlap_ratio
from deckpager.analysis.schema import Assessment, AssessmentDraft
from deckpager.config import Settings
from deckpager.ingest.models import Deck, Slide
from deckpager.ingest.router import load_deck
from deckpager.pipeline import analyze_deck, default_stem, load_assessment, slugify

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "sample_assessment.json"


def _deck(*texts: str) -> Deck:
    return Deck(
        source_path=Path("deck.pdf"),
        source_format="pdf",
        slides=[Slide(index=i, text=t) for i, t in enumerate(texts, start=1)],
    )


def _draft(**overrides: object) -> AssessmentDraft:
    """The recorded sample, optionally with fields replaced."""
    payload = load_assessment(SAMPLE).model_dump(mode="json")
    # `meta` and `scoring` are stamped by the pipeline, never produced by a model.
    payload.pop("meta")
    payload.pop("scoring", None)
    payload.update(overrides)
    return AssessmentDraft.model_validate(payload)


class TestOverlapRatio:
    def test_exact_match_is_one(self) -> None:
        assert overlap_ratio("Series A round", "We closed a Series A round.") == 1.0

    def test_no_overlap_is_zero(self) -> None:
        assert overlap_ratio("quantum satellites", "macrophage biology") == 0.0

    def test_tolerates_punctuation_and_case(self) -> None:
        ratio = overlap_ratio("Dr. Kwan, CSO", "dr kwan cso professor of immunology")
        assert ratio == 1.0

    def test_partial_overlap_is_fractional(self) -> None:
        ratio = overlap_ratio("alpha beta gamma delta", "alpha beta")
        assert ratio == pytest.approx(0.5)

    def test_empty_quote_is_zero(self) -> None:
        assert overlap_ratio("", "anything at all") == 0.0


class TestGrounding:
    def test_verified_fact_survives(self) -> None:
        deck = _deck("Raising $4M seed to complete IND-enabling toxicology.")
        draft = _draft()
        draft.founder.strengths[0].basis = "FACT"
        draft.founder.strengths[0].slide_refs = [1]
        draft.founder.strengths[0].quote = "Raising $4M seed to complete IND-enabling toxicology."
        draft.founder.weaknesses = []
        draft.team.strengths = []
        draft.team.weaknesses = []
        for risk in draft.risks:
            risk.evidence = []
        ground(draft, deck)
        assert draft.founder.strengths[0].basis == "FACT"

    def test_unverifiable_fact_is_downgraded_to_inference(self) -> None:
        deck = _deck("Slide one says nothing about revenue at all.")
        draft = _draft()
        target = draft.founder.strengths[0]
        target.basis = "FACT"
        target.slide_refs = [1]
        target.quote = "We booked twelve million dollars of annual recurring revenue."
        warnings = ground(draft, deck)
        assert target.basis == "INFERENCE"
        assert any("downgraded to INFERENCE" in w for w in warnings)

    def test_downgrade_keeps_the_claim_and_the_quote(self) -> None:
        """The claim may still be sound; it just is not something the deck demonstrably says."""
        deck = _deck("nothing relevant here")
        draft = _draft()
        target = draft.founder.strengths[0]
        target.basis = "FACT"
        target.slide_refs = [1]
        target.quote = "an entirely unmatched sentence about revenue"
        original_claim = target.claim
        ground(draft, deck)
        assert target.claim == original_claim
        assert target.quote == "an entirely unmatched sentence about revenue"

    def test_out_of_range_refs_are_removed(self) -> None:
        deck = _deck("only one slide here")
        draft = _draft()
        target = draft.founder.strengths[0]
        target.basis = "INFERENCE"
        target.slide_refs = [1, 99]
        target.quote = None
        warnings = ground(draft, deck)
        assert target.slide_refs == [1]
        assert any("outside the deck's 1-1 range" in w for w in warnings)

    def test_claim_citing_only_out_of_range_slides_becomes_speculation(self) -> None:
        deck = _deck("only one slide here")
        draft = _draft()
        target = draft.founder.strengths[0]
        target.basis = "FACT"
        target.slide_refs = [42]
        target.quote = "something"
        warnings = ground(draft, deck)
        assert target.basis == "SPECULATION"
        assert target.slide_refs == []
        assert target.quote is None
        assert any("reclassified as SPECULATION" in w for w in warnings)

    def test_facts_on_image_only_slides_are_left_alone(self) -> None:
        """A scanned deck must not have its entire memo downgraded."""
        deck = _deck("")
        draft = _draft()
        target = draft.founder.strengths[0]
        target.basis = "FACT"
        target.slide_refs = [1]
        target.quote = "anything at all"
        warnings = ground(draft, deck)
        assert target.basis == "FACT"
        assert any("no extractable text" in w for w in warnings)

    def test_speculation_is_never_touched(self) -> None:
        deck = _deck("some text")
        draft = _draft()
        spec = [e for e in draft.founder.weaknesses if e.basis == "SPECULATION"]
        assert spec, "fixture should contain a SPECULATION item"
        ground(draft, deck)
        assert all(e.basis == "SPECULATION" for e in spec)

    def test_summary_line_is_always_emitted(self) -> None:
        warnings = ground(_draft(), _deck("text"))
        assert any(w.startswith("Grounding: ") for w in warnings)

    def test_sample_fixture_grounds_cleanly_against_the_real_deck(self) -> None:
        """Every FACT in the sample fixture is quoted from the fixture deck verbatim."""
        deck = load_deck(FIXTURES / "sample_deck.pdf", want_images=False)
        draft = _draft()
        warnings = ground(draft, deck)
        assert not any("downgraded to INFERENCE" in w for w in warnings), warnings
        summary = next(w for w in warnings if w.startswith("Grounding: "))
        assert "7/7 FACT" in summary

    def test_threshold_is_the_documented_value(self) -> None:
        assert OVERLAP_THRESHOLD == 0.6


class TestFakeAnalyzer:
    def test_returns_the_recorded_draft(self) -> None:
        analyzer = FakeAnalyzer(SAMPLE)
        draft = analyzer.analyze(_deck("x"), context=None)
        assert draft.company_name == "Helion Bio"

    def test_strips_meta_from_the_fixture(self) -> None:
        """`meta` is provenance the pipeline stamps; the fake must not smuggle it through."""
        analyzer = FakeAnalyzer(SAMPLE)
        assert not hasattr(analyzer.analyze(_deck("x")), "meta")

    def test_records_calls(self) -> None:
        analyzer = FakeAnalyzer(SAMPLE)
        deck = _deck("x")
        analyzer.analyze(deck, context="Series A, biotech")
        assert analyzer.calls == [(deck, "Series A, biotech")]

    def test_hands_out_independent_copies(self) -> None:
        analyzer = FakeAnalyzer(SAMPLE)
        first = analyzer.analyze(_deck("x"))
        first.company_name = "Mutated"
        assert analyzer.analyze(_deck("x")).company_name == "Helion Bio"


class TestPipelineOffline:
    """The full pipeline, end to end, with no network access."""

    def test_produces_a_stamped_assessment(self, sample_pdf: Path, frozen_now: datetime) -> None:
        assessment = analyze_deck(
            deck_path=sample_pdf,
            context="Seed, biotech, $4M raise",
            settings=Settings(anthropic_api_key="unused-by-the-fake"),
            analyzer=FakeAnalyzer(SAMPLE),
            now=frozen_now,
        )
        assert isinstance(assessment, Assessment)
        assert assessment.company_name == "Helion Bio"
        assert assessment.meta.source_filename == "sample_deck.pdf"
        assert assessment.meta.slide_count == 5
        assert assessment.meta.generated_at == frozen_now

    def test_grounding_warnings_reach_meta(self, sample_pdf: Path, frozen_now: datetime) -> None:
        assessment = analyze_deck(
            deck_path=sample_pdf,
            context=None,
            settings=Settings(anthropic_api_key="unused"),
            analyzer=FakeAnalyzer(SAMPLE),
            now=frozen_now,
        )
        assert any(w.startswith("Grounding: ") for w in assessment.meta.grounding_warnings)

    def test_ingest_warnings_reach_meta(
        self, sample_pptx: Path, frozen_now: datetime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deckpager.ingest.pptx.find_soffice", lambda: None)
        assessment = analyze_deck(
            deck_path=sample_pptx,
            context=None,
            settings=Settings(anthropic_api_key="unused"),
            analyzer=FakeAnalyzer(SAMPLE),
            now=frozen_now,
        )
        assert any("LibreOffice" in w for w in assessment.meta.ingest_warnings)

    def test_context_reaches_the_analyzer(self, sample_pdf: Path, frozen_now: datetime) -> None:
        analyzer = FakeAnalyzer(SAMPLE)
        analyze_deck(
            deck_path=sample_pdf,
            context="Series A, biotech, $12M raise",
            settings=Settings(anthropic_api_key="unused"),
            analyzer=analyzer,
            now=frozen_now,
        )
        assert analyzer.calls[0][1] == "Series A, biotech, $12M raise"

    def test_round_trips_through_json(
        self, sample_pdf: Path, frozen_now: datetime, tmp_path: Path
    ) -> None:
        assessment = analyze_deck(
            deck_path=sample_pdf,
            context=None,
            settings=Settings(anthropic_api_key="unused"),
            analyzer=FakeAnalyzer(SAMPLE),
            now=frozen_now,
        )
        target = tmp_path / "out.json"
        target.write_text(assessment.model_dump_json(indent=2), encoding="utf-8")
        assert load_assessment(target) == assessment


class TestOutputNaming:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Helion Bio", "Helion_Bio"),
            ("Acme, Inc.", "Acme_Inc"),
            ("  spaced   out  ", "spaced_out"),
            ("Foo & Bar (Holdings)", "Foo_Bar_Holdings"),
            ("!!!", "Assessment"),
        ],
    )
    def test_slugify(self, name: str, expected: str) -> None:
        assert slugify(name) == expected

    def test_default_stem_sits_beside_the_deck(self, tmp_path: Path) -> None:
        assessment = load_assessment(SAMPLE)
        stem = default_stem(assessment, tmp_path)
        assert stem == tmp_path / "Helion_Bio"


class TestLoadAssessment:
    def test_rejects_malformed_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(Exception, match="not valid JSON"):
            load_assessment(bad)

    def test_rejects_a_valid_json_that_is_not_an_assessment(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"company_name": "X"}', encoding="utf-8")
        with pytest.raises(Exception, match="not a valid deckpager assessment"):
            load_assessment(bad)

    def test_both_fixtures_validate(self) -> None:
        for name in ("sample_assessment.json", "overstuffed_assessment.json"):
            assert load_assessment(FIXTURES / name).meta.model == "claude-opus-5"
