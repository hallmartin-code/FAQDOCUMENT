"""Schema tests: the validators must actually reject bad model output."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from deckpager.analysis import prompts
from deckpager.analysis.prompts import OUTPUT_CONTRACT, TOOL_NAME
from deckpager.analysis.schema import (
    RISK_ORDER,
    SCORECARD_ORDER,
    Assessment,
    AssessmentDraft,
    Evidence,
    RunMeta,
    assessment_tool_schema,
)
from deckpager.errors import ConfigError
from deckpager.paths import read_prompt, require_sections


def _evidence(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim": "Founding scientist holds a UCSD professorship.",
        "basis": "FACT",
        "slide_refs": [4],
        "quote": "Dr. Peter Kwan, CSO - Professor of Immunology, UCSD.",
    }
    return {**base, **overrides}


def _section() -> dict[str, Any]:
    return {
        "narrative": "Two-person scientific founding team with no commercial leadership.",
        "strengths": [_evidence()],
        "weaknesses": [
            _evidence(
                claim="No commercial lead named.",
                basis="INFERENCE",
                slide_refs=[4],
                quote=None,
            )
        ],
        "rating": 5,
    }


def _scorecard(overall: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "value": overall if name == "Overall Investability" else 5,
            "justification": f"{name} is at the median venture bar.",
        }
        for name in SCORECARD_ORDER
    ]


def _risks() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "level": "High",
            "rationale": f"{name} risk is elevated at this stage.",
            "evidence": [_evidence(basis="SPECULATION", slide_refs=[], quote=None)],
        }
        for name in RISK_ORDER
    ]


def draft_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal payload that validates, so each test can break exactly one thing."""
    base: dict[str, Any] = {
        "company_name": "Helion Bio",
        "one_line_description": "Macrophage reprogramming for solid tumors.",
        "stage_signal": "Seed",
        "founder": _section(),
        "team": _section(),
        "risks": _risks(),
        "ic_view": {
            "biggest_strengths": ["Credible scientific founder"],
            "biggest_concerns": ["No commercial leadership"],
            "diligence_questions": [
                {
                    "question": "Who owns the composition-of-matter IP for HLN-101?",
                    "why_it_matters": "University ownership would change the deal entirely.",
                    "priority": "Critical",
                }
            ],
            "recommendation": "MORE_DILIGENCE",
            "advance_rationale": "Team gaps outweigh the science at this price.",
            "confidence": "MEDIUM",
        },
        "scorecard": _scorecard(),
        "overall_investability": 5,
        "recommendations": [
            {
                "target": "company",
                "action": "Hire a full-time commercial lead before the Series A.",
                "priority": "High",
                "rationale": "No named commercial owner for the next milestone.",
            }
        ],
        "executive_summary": "Credible science, incomplete team, no lead investor.",
    }
    return {**base, **overrides}


class TestEvidenceDiscipline:
    def test_valid_fact_is_accepted(self) -> None:
        assert Evidence(**_evidence()).basis == "FACT"

    def test_fact_without_quote_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="basis=FACT requires `quote`"):
            Evidence(**_evidence(quote=None))

    def test_fact_with_blank_quote_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="basis=FACT requires `quote`"):
            Evidence(**_evidence(quote="   "))

    def test_fact_without_slide_refs_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="basis=FACT requires at least one"):
            Evidence(**_evidence(slide_refs=[]))

    def test_inference_without_slide_refs_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="basis=INFERENCE requires `slide_refs`"):
            Evidence(**_evidence(basis="INFERENCE", slide_refs=[], quote=None))

    def test_inference_without_quote_is_fine(self) -> None:
        assert Evidence(**_evidence(basis="INFERENCE", quote=None)).quote is None

    def test_speculation_with_slide_refs_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SPECULATION must have an empty"):
            Evidence(**_evidence(basis="SPECULATION", slide_refs=[2], quote=None))

    def test_speculation_with_no_refs_is_accepted(self) -> None:
        item = Evidence(**_evidence(basis="SPECULATION", slide_refs=[], quote=None))
        assert item.slide_refs == []

    def test_zero_or_negative_slide_refs_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="1-based slide numbers"):
            Evidence(**_evidence(slide_refs=[0]))

    def test_unknown_basis_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(**_evidence(basis="HUNCH"))

    def test_overlong_claim_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(**_evidence(claim="x" * 401))


class TestScorecard:
    def test_canonical_scorecard_is_accepted(self) -> None:
        assert len(AssessmentDraft(**draft_payload()).scorecard) == 11

    def test_ten_item_scorecard_is_rejected(self) -> None:
        short = _scorecard()[:-1]
        with pytest.raises(ValidationError, match="exactly these 11 names"):
            AssessmentDraft(**draft_payload(scorecard=short, overall_investability=5))

    def test_twelve_item_scorecard_is_rejected(self) -> None:
        long = [*_scorecard(), {"name": "Moat", "value": 4, "justification": "Thin."}]
        with pytest.raises(ValidationError, match="unexpected"):
            AssessmentDraft(**draft_payload(scorecard=long))

    def test_duplicate_scorecard_names_are_rejected(self) -> None:
        dupes = [*_scorecard()[:-1], {"name": "Founder", "value": 5, "justification": "Dup."}]
        with pytest.raises(ValidationError, match="duplicate names"):
            AssessmentDraft(**draft_payload(scorecard=dupes))

    def test_out_of_order_scorecard_is_rejected(self) -> None:
        shuffled = list(reversed(_scorecard()))
        with pytest.raises(ValidationError, match="wrong order"):
            AssessmentDraft(**draft_payload(scorecard=shuffled))

    def test_score_outside_one_to_ten_is_rejected(self) -> None:
        bad = _scorecard()
        bad[0]["value"] = 11
        with pytest.raises(ValidationError):
            AssessmentDraft(**draft_payload(scorecard=bad))

    def test_overall_may_diverge_from_the_scorecard_row(self) -> None:
        """Disagreement is a signal, not a schema error.

        The headline number is computed from config/weights.toml. When the model's own
        call differs from the weighted one, `scoring.py` records `score_divergence` and
        both numbers reach the one-pager. Rejecting the payload here would discard the
        disagreement and spend a repair turn hiding it.
        """
        draft = AssessmentDraft(**draft_payload(scorecard=_scorecard(8), overall_investability=5))
        assert draft.overall_investability == 5
        assert draft.scorecard[-1].value == 8

    def test_null_score_is_accepted(self) -> None:
        """A category the deck gives no basis for scores null, never a guess."""
        gapped = _scorecard()
        gapped[3]["value"] = None
        draft = AssessmentDraft(
            **draft_payload(scorecard=gapped, data_gaps=["No pricing appears anywhere."])
        )
        assert draft.scorecard[3].value is None
        assert draft.data_gaps == ["No pricing appears anywhere."]


class TestRisks:
    def test_all_required_categories_present_is_accepted(self) -> None:
        assert len(AssessmentDraft(**draft_payload()).risks) == len(RISK_ORDER)

    def test_missing_required_risk_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Missing"):
            AssessmentDraft(**draft_payload(risks=_risks()[:-1]))

    def test_duplicate_risk_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate names"):
            AssessmentDraft(**draft_payload(risks=[*_risks(), _risks()[0]]))

    def test_extra_risk_categories_are_allowed(self) -> None:
        extra = {
            "name": "Reimbursement",
            "level": "Critical",
            "rationale": "No payor pathway is described.",
            "evidence": [],
        }
        draft = AssessmentDraft(**draft_payload(risks=[*_risks(), extra]))
        assert draft.risks[-1].name == "Reimbursement"

    def test_unknown_risk_level_is_rejected(self) -> None:
        bad = _risks()
        bad[0]["level"] = "Severe"
        with pytest.raises(ValidationError):
            AssessmentDraft(**draft_payload(risks=bad))


class TestICView:
    def test_more_than_five_strengths_is_rejected(self) -> None:
        payload = draft_payload()
        payload["ic_view"]["biggest_strengths"] = [f"s{i}" for i in range(6)]
        with pytest.raises(ValidationError):
            AssessmentDraft(**payload)

    def test_more_than_five_concerns_is_rejected(self) -> None:
        payload = draft_payload()
        payload["ic_view"]["biggest_concerns"] = [f"c{i}" for i in range(6)]
        with pytest.raises(ValidationError):
            AssessmentDraft(**payload)

    def test_unknown_confidence_is_rejected(self) -> None:
        payload = draft_payload()
        payload["ic_view"]["confidence"] = "Total"
        with pytest.raises(ValidationError):
            AssessmentDraft(**payload)


class TestAssessmentEnvelope:
    def test_unknown_top_level_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssessmentDraft(**draft_payload(sector="biotech"))

    def test_overlong_executive_summary_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssessmentDraft(**draft_payload(executive_summary="x" * 1201))

    def test_from_draft_attaches_meta(self) -> None:
        draft = AssessmentDraft(**draft_payload())
        meta = RunMeta(
            model="claude-opus-5",
            source_filename="deck.pdf",
            slide_count=5,
            generated_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        assessment = Assessment.from_draft(draft, meta)
        assert assessment.company_name == "Helion Bio"
        assert assessment.meta.model == "claude-opus-5"
        assert assessment.meta.ingest_warnings == []

    def test_ordered_risks_puts_canonical_first(self) -> None:
        extra = {"name": "Reimbursement", "level": "Low", "rationale": "n/a", "evidence": []}
        draft = AssessmentDraft(**draft_payload(risks=[extra, *_risks()]))
        names = [r.name for r in Assessment.from_draft(draft, _meta()).ordered_risks()]
        assert names[: len(RISK_ORDER)] == list(RISK_ORDER)
        assert names[-1] == "Reimbursement"

    def test_all_evidence_collects_every_item(self) -> None:
        assessment = Assessment.from_draft(AssessmentDraft(**draft_payload()), _meta())
        # 2 founder + 2 team + one per required risk
        assert len(assessment.all_evidence()) == 4 + len(RISK_ORDER)


class TestToolSchema:
    def test_schema_is_generated_from_the_models(self) -> None:
        schema = assessment_tool_schema()
        assert schema["type"] == "object"
        for field in ("company_name", "founder", "team", "risks", "ic_view", "scorecard"):
            assert field in schema["properties"]

    def test_schema_excludes_run_meta(self) -> None:
        """`meta` is provenance the pipeline stamps; the model must not invent it."""
        assert "meta" not in assessment_tool_schema()["properties"]

    def test_schema_forbids_unknown_keys(self) -> None:
        assert assessment_tool_schema()["additionalProperties"] is False

    def test_schema_carries_the_basis_enum(self) -> None:
        evidence = assessment_tool_schema()["$defs"]["Evidence"]
        assert set(evidence["properties"]["basis"]["enum"]) == {
            "FACT",
            "INFERENCE",
            "SPECULATION",
        }


class TestPrompts:
    def test_persona_loads_from_the_prompt_file(self) -> None:
        """The persona is product data on disk, not a literal in Python source."""
        source = Path(prompts.__file__).read_text(encoding="utf-8")
        assert "You are a partner at a top-tier" not in source, (
            "the persona must live in prompts/analyst_system.md, not in prompts.py"
        )
        assert prompts.system_prompt() == read_prompt("analyst_system.md")

    def test_persona_carries_the_load_bearing_rules(self) -> None:
        persona = prompts.system_prompt()
        assert "You are a partner at a top-tier venture capital firm" in persona
        assert "Evidence discipline — the highest-priority rule." in persona
        # Scoring anchors: the highest-leverage defence against score drift between runs.
        assert "5–6 Median seed/Series A deck." in persona
        assert "9–10 Exceptional." in persona
        # Null-handling: without this the model invents a plausible team slide.
        assert "Absence is a finding." in persona
        assert "return null for its score and add a precise entry to data_gaps" in persona

    def test_persona_names_all_eleven_scorecard_categories(self) -> None:
        persona = prompts.system_prompt()
        for name in SCORECARD_ORDER:
            assert name in persona

    def test_contract_lists_every_scorecard_name(self) -> None:
        for name in SCORECARD_ORDER:
            assert name in OUTPUT_CONTRACT

    def test_contract_lists_every_required_risk(self) -> None:
        for name in RISK_ORDER:
            assert name in OUTPUT_CONTRACT

    def test_contract_names_the_tool(self) -> None:
        assert TOOL_NAME in OUTPUT_CONTRACT


class TestPromptFiles:
    """The section-delimited prompt files parse and expose what the code asks for."""

    def test_extraction_sections_resolve(self) -> None:
        sections = require_sections(
            "extraction_user.md", "deck_payload", "operator_context", "instruction"
        )
        assert "${transcript}" in sections["deck_payload"]
        assert "${context}" in sections["operator_context"]
        assert "${tool_name}" in sections["instruction"]

    def test_repair_sections_resolve(self) -> None:
        sections = require_sections("repair_user.md", "tool_result", "instruction")
        assert "${errors}" in sections["tool_result"]
        assert "${attempt}" in sections["instruction"]

    def test_editing_note_never_reaches_the_model(self) -> None:
        """Text before the first section marker documents the file; it is not prompt text."""
        raw = read_prompt("extraction_user.md")
        assert "Sections are delimited by" in raw
        sections = require_sections("extraction_user.md", "deck_payload")
        assert "Sections are delimited by" not in sections["deck_payload"]

    def test_missing_section_names_the_file_and_what_was_found(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            require_sections("extraction_user.md", "no_such_section")
        message = str(excinfo.value)
        assert "no_such_section" in message
        assert "extraction_user.md" in message
        assert "deck_payload" in message  # tells the analyst what does exist


def _meta() -> RunMeta:
    return RunMeta(
        model="claude-opus-5",
        source_filename="deck.pdf",
        slide_count=5,
        generated_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
