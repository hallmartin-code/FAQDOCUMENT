"""The output contract.

These models are the single source of truth. The JSON schema handed to the API is
generated from `AssessmentDraft.model_json_schema()` — it is never hand-written, so the
schema and the models cannot drift.

Evidence discipline is enforced here, not requested politely in the prompt: a FACT without
a quote, or an INFERENCE without slide references, fails validation and triggers a retry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Basis = Literal["FACT", "INFERENCE", "SPECULATION"]
RiskLevel = Literal["Low", "Medium", "High", "Critical"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
Priority = Literal["Critical", "High", "Medium"]

#: The screening verdict. A bool cannot express MORE_DILIGENCE, which is the most common
#: real outcome of a first screen — most decks are neither backable nor dead on the deck.
#: Named `Verdict` because `Recommendation` is already the actionable-step model below;
#: the *field* is `ic_view.recommendation`, per the spec.
Verdict = Literal["ADVANCE_TO_PARTNER_MEETING", "MORE_DILIGENCE", "PASS"]

#: Risk categories the one-pager has room to print. The rest are assessed and stored, and
#: appear in the full memo. See templates/onepager.md §4.2.
ONEPAGER_RISKS: tuple[str, ...] = (
    "Execution",
    "Technology",
    "Commercialization",
    "Regulatory",
    "Go-to-Market",
)

#: The eleven scorecard rows, in the order they must appear.
SCORECARD_ORDER: tuple[str, ...] = (
    "Founder",
    "Executive Team",
    "Scientific Credibility",
    "Commercial Readiness",
    "Leadership",
    "Vision",
    "Storytelling",
    "Execution Capability",
    "Capital Efficiency",
    "Fundraising Readiness",
    "Overall Investability",
)

#: Risk categories the investor-perspective section must rate. Extra risks are allowed.
RISK_ORDER: tuple[str, ...] = (
    "Execution",
    "Technology",
    "Commercialization",
    "Regulatory",
    "Go-to-Market",
    "Leadership Scalability",
    "Talent Attraction",
)


class _Strict(BaseModel):
    """Base config: reject unknown keys so a drifting model payload fails loudly."""

    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    """One claim, classified by how well the deck supports it."""

    claim: str = Field(max_length=400)
    basis: Basis
    slide_refs: list[int] = Field(default_factory=list)
    quote: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def _check_basis_requirements(self) -> Self:
        if self.basis == "FACT":
            if not self.quote or not self.quote.strip():
                raise ValueError(
                    "basis=FACT requires `quote` with the specific language or figure "
                    "copied from the deck."
                )
            if not self.slide_refs:
                raise ValueError("basis=FACT requires at least one entry in `slide_refs`.")
        elif self.basis == "INFERENCE":
            if not self.slide_refs:
                raise ValueError(
                    "basis=INFERENCE requires `slide_refs` naming the slide(s) you reasoned from."
                )
        elif self.slide_refs:
            raise ValueError(
                "basis=SPECULATION must have an empty `slide_refs` — speculation is your "
                "judgment where the deck is silent. Use INFERENCE if the deck supports it."
            )
        if any(ref < 1 for ref in self.slide_refs):
            raise ValueError("`slide_refs` are 1-based slide numbers; values must be >= 1.")
        return self


class Score(_Strict):
    """One scorecard row.

    `value` is None when the deck gives no basis for the category. That is a finding, not
    a failure: a null is excluded from the weighted overall, the remaining weights
    renormalize, and the category is named in `data_gaps`. Scoring an unevidenced category
    would be the model inventing a team slide.
    """

    name: str
    value: int | None = Field(default=None, ge=1, le=10)
    justification: str = Field(max_length=200)


class Risk(_Strict):
    """A rated risk with its supporting evidence."""

    name: str
    level: RiskLevel
    rationale: str = Field(max_length=800)
    evidence: list[Evidence] = Field(default_factory=list)


class SectionAssessment(_Strict):
    """A narrative section with rated strengths and weaknesses."""

    narrative: str = Field(max_length=4000)
    strengths: list[Evidence] = Field(default_factory=list)
    weaknesses: list[Evidence] = Field(default_factory=list)
    rating: int = Field(ge=1, le=10)


class DiligenceQuestion(_Strict):
    """A question specific enough that a vague answer is obviously vague."""

    question: str = Field(max_length=500)
    why_it_matters: str = Field(max_length=400)
    priority: Priority


class Recommendation(_Strict):
    """An actionable step, separated by whether it improves the company or the story."""

    target: Literal["company", "narrative"]
    action: str = Field(max_length=400)
    priority: Priority
    rationale: str = Field(max_length=400)


class ICView(_Strict):
    """The investment committee view."""

    biggest_strengths: list[str] = Field(max_length=5)
    biggest_concerns: list[str] = Field(max_length=5)
    diligence_questions: list[DiligenceQuestion] = Field(default_factory=list)
    recommendation: Verdict
    advance_rationale: str = Field(max_length=800)
    confidence: Confidence


class DealTerms(_Strict):
    """What the deck says about the round. Every field is optional — most decks omit some,
    and an absent term renders as a stated gap rather than a blank."""

    ask: str | None = Field(default=None, max_length=120)
    sector: str | None = Field(default=None, max_length=120)
    deck_date: str | None = Field(default=None, max_length=60)


class ScoringReport(BaseModel):
    """The weighted overall and the model's disagreement with it.

    Both numbers survive into the output. The gap between what the model called and what
    the house weights produce is a signal about the analysis, so it is recorded rather
    than reconciled away.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    computed_overall: float | None = None
    model_overall: int | None = None
    score_divergence: bool = False
    weights_version: str | None = None
    confidence_downgraded_from: Confidence | None = None
    confidence_downgrade_reason: str | None = None

    @property
    def divergence_note(self) -> str | None:
        """The provenance-line fragment stating both numbers, when they disagree."""
        if not self.score_divergence or self.computed_overall is None:
            return None
        return f"score divergence: model {self.model_overall}, computed {self.computed_overall:.1f}"


class RunMeta(BaseModel):
    """Provenance for one analysis run. Filled in by the pipeline, never by the model."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str
    provider: str = "anthropic"
    source_filename: str
    #: First 12 characters are printed on the one-pager: enough to tell two versions of a
    #: deck apart, short enough to sit in a 6.5pt provenance line.
    sha256: str | None = None
    slide_count: int = Field(ge=0)
    ingest_warnings: list[str] = Field(default_factory=list)
    grounding_warnings: list[str] = Field(default_factory=list)
    #: How the run was performed rather than what it found: map-reduce, font substitution,
    #: a truncated executive summary. Lets a reader tell a short summary from a trimmed one.
    method_notes: list[str] = Field(default_factory=list)
    generated_at: datetime


class AssessmentDraft(_Strict):
    """Everything the model produces. `RunMeta` is added by the pipeline afterwards."""

    company_name: str = Field(max_length=200)
    one_line_description: str = Field(max_length=300)
    stage_signal: str | None = Field(default=None, max_length=200)
    deal: DealTerms = Field(default_factory=DealTerms)
    founder: SectionAssessment
    team: SectionAssessment
    risks: list[Risk]
    ic_view: ICView
    scorecard: list[Score]
    overall_investability: int | None = Field(default=None, ge=1, le=10)
    #: Categories and facts the deck gives no basis for. Absence is a finding: this is
    #: where "no CTO is named anywhere in the deck" lands, and more than three entries
    #: caps confidence at MEDIUM.
    data_gaps: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    executive_summary: str = Field(max_length=1200)

    @model_validator(mode="after")
    def _check_scorecard(self) -> Self:
        names = [s.name for s in self.scorecard]
        if len(names) != len(set(names)):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"`scorecard` contains duplicate names: {duplicates}")
        if tuple(names) != SCORECARD_ORDER:
            missing = [n for n in SCORECARD_ORDER if n not in names]
            extra = [n for n in names if n not in SCORECARD_ORDER]
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if extra:
                detail.append(f"unexpected {extra}")
            if not detail:
                detail.append("wrong order")
            raise ValueError(
                f"`scorecard` must contain exactly these 11 names, in this order: "
                f"{list(SCORECARD_ORDER)} ({'; '.join(detail)})"
            )
        return self

    # There is deliberately no validator tying `overall_investability` to the scorecard's
    # "Overall Investability" row. The headline number is computed from config/weights.toml,
    # and a gap between the model's own call and the weighted one is a signal worth
    # reading — `scoring.py` records it as `score_divergence`. Rejecting the payload here
    # would throw away the disagreement and burn a repair turn to hide it.

    @model_validator(mode="after")
    def _check_risks(self) -> Self:
        names = [r.name for r in self.risks]
        if len(names) != len(set(names)):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"`risks` contains duplicate names: {duplicates}")
        missing = [n for n in RISK_ORDER if n not in names]
        if missing:
            raise ValueError(
                f"`risks` must rate every required category. Missing: {missing}. "
                f"Required: {list(RISK_ORDER)}"
            )
        return self


class Assessment(AssessmentDraft):
    """A complete, provenance-stamped assessment. This is what gets written to JSON."""

    meta: RunMeta
    scoring: ScoringReport = Field(default_factory=ScoringReport)

    @classmethod
    def from_draft(
        cls,
        draft: AssessmentDraft,
        meta: RunMeta,
        scoring: ScoringReport | None = None,
    ) -> Assessment:
        """Attach run provenance to a validated model payload."""
        return cls(**draft.model_dump(), meta=meta, scoring=scoring or ScoringReport())

    def ordered_risks(self) -> list[Risk]:
        """Risks in canonical order, with any extra categories appended."""
        by_name = {r.name: r for r in self.risks}
        ordered = [by_name[n] for n in RISK_ORDER if n in by_name]
        ordered.extend(r for r in self.risks if r.name not in RISK_ORDER)
        return ordered

    def onepager_risks(self) -> list[Risk]:
        """The five risk rows the one-pager prints, in template order."""
        by_name = {r.name: r for r in self.risks}
        return [by_name[n] for n in ONEPAGER_RISKS if n in by_name]

    def scorecard_row(self, name: str) -> Score | None:
        """One scorecard row by name, or None if the model omitted it."""
        return next((s for s in self.scorecard if s.name == name), None)

    def headline_score(self) -> float | None:
        """What the one-pager prints large: the weighted figure, falling back to the
        model's own number only while scoring has not run."""
        if self.scoring.computed_overall is not None:
            return self.scoring.computed_overall
        return float(self.overall_investability) if self.overall_investability else None

    def all_evidence(self) -> list[Evidence]:
        """Every Evidence item in the assessment, in document order."""
        items: list[Evidence] = []
        items.extend(self.founder.strengths)
        items.extend(self.founder.weaknesses)
        items.extend(self.team.strengths)
        items.extend(self.team.weaknesses)
        for risk in self.ordered_risks():
            items.extend(risk.evidence)
        return items


def assessment_tool_schema() -> dict[str, Any]:
    """The JSON schema handed to the API, generated from the Pydantic models."""
    return AssessmentDraft.model_json_schema()
