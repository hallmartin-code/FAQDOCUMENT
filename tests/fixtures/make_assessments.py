"""Regenerate the assessment JSON fixtures.

Run from the repo root:  python tests/fixtures/make_assessments.py

`sample_assessment.json` is a typical assessment whose FACT quotes are copied verbatim from
`sample_deck.pdf`, so grounding tests exercise the real matcher against a real deck.
`overstuffed_assessment.json` pushes every string to its max_length and every list to its
cap — it is what `test_onepager_fit.py` uses to prove page 1 cannot overflow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitchlens.analysis.schema import RISK_ORDER, SCORECARD_ORDER, Assessment

HERE = Path(__file__).parent
FROZEN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def ev(claim: str, basis: str, refs: list[int], quote: str | None = None) -> dict[str, Any]:
    return {"claim": claim, "basis": basis, "slide_refs": refs, "quote": quote}


def _sample() -> dict[str, Any]:
    scores = {
        "Founder": (6, "Genentech pedigree and two INDs, but no company-building track record."),
        "Executive Team": (4, "Two scientists; the commercial seat is explicitly empty."),
        "Scientific Credibility": (7, "Named mechanism plus a UCSD immunology professor as CSO."),
        "Commercial Readiness": (2, "No revenue, no named customers, no partner, no pricing."),
        "Leadership": (5, "CEO has functional leadership experience, not P&L ownership."),
        "Vision": (6, "Clear thesis on an under-attacked compartment of the tumor."),
        "Storytelling": (5, "Clean arc, but the ask slide raises more questions than it closes."),
        "Execution Capability": (4, "Only in vitro data; no IND-enabling work completed yet."),
        "Capital Efficiency": (5, "Cannot be assessed — no spend history is disclosed."),
        "Fundraising Readiness": (3, "No lead investor and no disclosed terms at seed."),
        "Overall Investability": (4, "Good science, incomplete team, unpriced round, no lead."),
    }
    return {
        "company_name": "Helion Bio",
        "one_line_description": "Small-molecule macrophage reprogramming for solid tumors.",
        "stage_signal": "Seed",
        "founder": {
            "narrative": (
                "Dr. Marisol Reyes presents as a credible scientific operator: twelve years at "
                "Genentech and two INDs led is a real, checkable claim, and it maps directly to "
                "the company's next milestone, which is IND-enabling toxicology. What the deck "
                "does not show is company-building. There is no evidence she has hired outside "
                "her function, carried a P&L, closed a financing, or run a commercial "
                "negotiation. Founder-market fit on the science is strong; founder-market fit "
                "on the business of a venture-backed biotech is unproven."
            ),
            "strengths": [
                ev(
                    "CEO has directly relevant drug-development experience at a top-tier biotech.",
                    "FACT",
                    [4],
                    "Dr. Marisol Reyes, CEO - 12 years at Genentech, led two INDs.",
                ),
                ev(
                    "Prior IND leadership de-risks the specific milestone this round funds.",
                    "INFERENCE",
                    [4, 5],
                ),
            ],
            "weaknesses": [
                ev(
                    "No evidence of prior company-building, hiring, or fundraising experience.",
                    "SPECULATION",
                    [],
                ),
                ev(
                    "The deck names no board, no advisors, and no prior investors.",
                    "INFERENCE",
                    [4],
                ),
            ],
            "rating": 6,
        },
        "team": {
            "narrative": (
                "The bench is two scientists and an acknowledged hole. Dr. Peter Kwan brings "
                "genuine academic credibility as a UCSD immunology professor, which supports "
                "the mechanism but says nothing about translational execution, and the deck "
                "does not state whether he is full-time. The commercial lead role is disclosed "
                "as open. Crediting the team for disclosing the gap is fair; the gap is still "
                "the gap. For an $4M seed reaching only to IND-enabling toxicology this is "
                "survivable, but there is no named regulatory or CMC owner anywhere in the deck."
            ),
            "strengths": [
                ev(
                    "Scientific founder holds a named academic appointment in the relevant field.",
                    "FACT",
                    [4],
                    "Dr. Peter Kwan, CSO - Professor of Immunology, UCSD.",
                ),
                ev(
                    "The team acknowledges its own gap rather than papering over it.",
                    "FACT",
                    [4],
                    "Commercial lead role is currently open.",
                ),
            ],
            "weaknesses": [
                ev(
                    "No regulatory, CMC, or manufacturing owner is named anywhere in the deck.",
                    "INFERENCE",
                    [4, 5],
                ),
                ev(
                    "CSO's full-time versus advisory status is not stated.",
                    "INFERENCE",
                    [4],
                ),
                ev(
                    "A first commercial hire at seed stage is typically 9-12 months from close.",
                    "SPECULATION",
                    [],
                ),
            ],
            "rating": 4,
        },
        "risks": [
            {
                "name": "Execution",
                "level": "High",
                "rationale": (
                    "The round funds IND-enabling toxicology on an 18-month runway with no "
                    "named regulatory or CMC owner. The plan is credible; the staffing is not."
                ),
                "evidence": [
                    ev(
                        "Milestone and runway are stated but not reconciled to named owners.",
                        "FACT",
                        [5],
                        "Raising $4M seed to complete IND-enabling toxicology.",
                    )
                ],
            },
            {
                "name": "Technology",
                "level": "High",
                "rationale": (
                    "Repolarization is shown in vitro only. The gap between an in vitro "
                    "phenotype switch and in vivo tumor clearance is where most macrophage "
                    "programs have died."
                ),
                "evidence": [
                    ev(
                        "Efficacy evidence is limited to in vitro repolarization.",
                        "FACT",
                        [3],
                        "Repolarizes M2 macrophages to an M1 phenotype in vitro.",
                    )
                ],
            },
            {
                "name": "Commercialization",
                "level": "Critical",
                "rationale": (
                    "No pricing, no payor path, no partner, and no commercial owner. Nothing "
                    "in the deck addresses who buys this or at what price."
                ),
                "evidence": [
                    ev("The deck contains no commercial or pricing slide.", "INFERENCE", [1, 5])
                ],
            },
            {
                "name": "Regulatory",
                "level": "High",
                "rationale": (
                    "An oral small molecule has a conventional path, but no pre-IND meeting, "
                    "no indication selection, and no regulatory strategy is described."
                ),
                "evidence": [
                    ev(
                        "Oral dosing implies a conventional small-molecule regulatory path.",
                        "FACT",
                        [3],
                        "Oral dosing; no cold chain required.",
                    )
                ],
            },
            {
                "name": "Go-to-Market",
                "level": "Critical",
                "rationale": (
                    "Pre-clinical companies exit via partnership. The deck names no target "
                    "acquirer, no pharma conversation, and no partnering strategy."
                ),
                "evidence": [ev("No partnering or exit path is described.", "SPECULATION", [])],
            },
            {
                "name": "Leadership Scalability",
                "level": "Medium",
                "rationale": (
                    "A two-person scientific team is adequate through tox, but there is no "
                    "evidence either founder has scaled an organization past that point."
                ),
                "evidence": [
                    ev("Team slide lists two people and one open role.", "INFERENCE", [4])
                ],
            },
            {
                "name": "Talent Attraction",
                "level": "Medium",
                "rationale": (
                    "San Diego is a deep biotech talent market and the Genentech and UCSD "
                    "affiliations help, but an unpriced round with no lead weakens the pitch."
                ),
                "evidence": [
                    ev(
                        "Company is located in an established biotech hub.",
                        "FACT",
                        [1],
                        "Seed round | $4M | San Diego, CA",
                    )
                ],
            },
        ],
        "ic_view": {
            "biggest_strengths": [
                "Founding scientist with a verifiable academic appointment in the exact field.",
                "CEO has led two INDs — the specific milestone this round funds.",
                "Mechanism targets a compartment checkpoint inhibitors demonstrably miss.",
                "Oral small molecule avoids cold chain and biologics manufacturing risk.",
            ],
            "biggest_concerns": [
                "No in vivo efficacy data whatsoever; the entire case rests on in vitro.",
                "No commercial, regulatory, or CMC owner named anywhere in the deck.",
                "No lead investor and no disclosed valuation or instrument terms.",
                "18-month runway is tight against an IND-enabling tox package.",
                "No IP position is stated — composition of matter is never mentioned.",
            ],
            "diligence_questions": [
                {
                    "question": (
                        "Who owns the composition-of-matter IP for HLN-101 today, and if it is "
                        "UCSD, what are the exact license terms and milestone obligations?"
                    ),
                    "why_it_matters": (
                        "University ownership with unfavorable terms changes the return profile "
                        "and can block a partnership entirely."
                    ),
                    "priority": "Critical",
                },
                {
                    "question": (
                        "What in vivo data exists today, in which tumor model, and what was the "
                        "effect size relative to checkpoint-inhibitor control?"
                    ),
                    "why_it_matters": (
                        "In vitro repolarization is where most macrophage programs look good and "
                        "then fail. No in vivo signal means this is a science project."
                    ),
                    "priority": "Critical",
                },
                {
                    "question": (
                        "Walk me through the $4M budget line by line against the 18-month "
                        "runway — what specifically does not get done if tox takes 24 months?"
                    ),
                    "why_it_matters": (
                        "Reveals whether the milestone-to-runway reconciliation is real or a "
                        "round number chosen to look fundable."
                    ),
                    "priority": "Critical",
                },
                {
                    "question": (
                        "Is Dr. Kwan full-time, and what does his UCSD appointment allow in "
                        "terms of time commitment and IP assignment?"
                    ),
                    "why_it_matters": (
                        "A part-time CSO with a competing academic lab is a different company "
                        "than the one this deck implies."
                    ),
                    "priority": "High",
                },
                {
                    "question": (
                        "Which pharma partners have you spoken to, and what specifically did "
                        "they say they would need to see to engage?"
                    ),
                    "why_it_matters": (
                        "The only exit for a pre-clinical asset is partnership. Named, specific "
                        "feedback separates a real BD process from a hoped-for one."
                    ),
                    "priority": "High",
                },
                {
                    "question": (
                        "Who is your first commercial or regulatory hire, when do they start, "
                        "and is that cost inside the $4M?"
                    ),
                    "why_it_matters": (
                        "Tests whether the acknowledged gap has an actual plan and a budget "
                        "line, or is simply disclosed and deferred."
                    ),
                    "priority": "Medium",
                },
            ],
            "recommendation": "MORE_DILIGENCE",
            "advance_rationale": (
                "The science is interesting and the founding scientist is credible, but there "
                "is no in vivo data, no stated IP position, no commercial or regulatory owner, "
                "and no lead investor. Any one of those is workable at seed; all four together "
                "means we would be underwriting a science project at a price nobody has set. "
                "Revisit on in vivo efficacy data plus a clean IP answer."
            ),
            "confidence": "MEDIUM",
        },
        "scorecard": [
            {"name": name, "value": scores[name][0], "justification": scores[name][1]}
            for name in SCORECARD_ORDER
        ],
        "overall_investability": 4,
        "recommendations": [
            {
                "target": "company",
                "action": "Generate in vivo efficacy data in at least one syngeneic tumor model.",
                "priority": "Critical",
                "rationale": "In vitro repolarization alone will not clear any serious IC.",
            },
            {
                "target": "company",
                "action": "Resolve and document the composition-of-matter IP position in writing.",
                "priority": "Critical",
                "rationale": "An unstated IP position is assumed to be a bad one.",
            },
            {
                "target": "company",
                "action": "Name a regulatory or CMC owner, even fractional, before the raise.",
                "priority": "High",
                "rationale": "The round funds IND-enabling work with nobody named to run it.",
            },
            {
                "target": "narrative",
                "action": "Add an IP slide stating patent type, jurisdiction, and expiry.",
                "priority": "Critical",
                "rationale": "Its absence is the first thing every investor will ask about.",
            },
            {
                "target": "narrative",
                "action": "Replace the in vitro claim with a data slide showing the actual assay.",
                "priority": "High",
                "rationale": "A claim without a figure reads as a claim you cannot show.",
            },
            {
                "target": "narrative",
                "action": "Reconcile the $4M ask to the 18-month runway line by line on one slide.",
                "priority": "High",
                "rationale": "Round numbers without a build-up signal the ask was reverse-engineered.",
            },
            {
                "target": "narrative",
                "action": "State the instrument and terms; 'no lead' is not a substitute for terms.",
                "priority": "Medium",
                "rationale": "Investors cannot evaluate a deal whose price is undisclosed.",
            },
        ],
        "executive_summary": (
            "Helion Bio is a San Diego seed-stage biotech raising $4M to take HLN-101, an oral "
            "SIRP-alpha antagonist, through IND-enabling toxicology. The mechanism targets "
            "tumor-associated macrophages, a compartment checkpoint inhibitors demonstrably "
            "miss, and the founding team pairs a Genentech drug developer who has led two INDs "
            "with a UCSD immunology professor. That is a real foundation. Against it: efficacy "
            "evidence is in vitro only, no IP position is stated anywhere in the deck, the "
            "commercial seat is openly vacant, no regulatory or CMC owner is named, and the "
            "round has no lead and no disclosed terms. We would not advance this to a partner "
            "meeting today. It becomes interesting on in vivo efficacy data in a syngeneic "
            "model plus a clean composition-of-matter answer."
        ),
        "meta": {
            "model": "claude-opus-5",
            "source_filename": "sample_deck.pdf",
            "slide_count": 5,
            "ingest_warnings": [],
            "grounding_warnings": [
                "Grounding: 7/7 FACT claim(s) verified against extracted slide text."
            ],
            "generated_at": FROZEN.isoformat(),
        },
    }


def _overstuffed() -> dict[str, Any]:
    """Every string at its max_length and every list at its cap."""

    def fill(n: int, seed: str) -> str:
        body = (seed + " ") * (n // (len(seed) + 1) + 2)
        return body[:n].strip()

    long_ev = ev(
        fill(400, "an exhaustively long evidentiary claim about the team"),
        "FACT",
        [1, 2, 3],
        fill(600, "quoted deck language repeated at length"),
    )
    spec_ev = ev(
        fill(400, "an exhaustively long speculative claim about the market"), "SPECULATION", []
    )

    return {
        "company_name": fill(200, "Extraordinarily Long Company Name Holdings International"),
        "one_line_description": fill(300, "an extremely long one line description of the business"),
        "stage_signal": fill(200, "Seed extension bridging to a priced Series A"),
        "founder": {
            "narrative": fill(4000, "a very long founder narrative paragraph that keeps going"),
            "strengths": [long_ev] * 6,
            "weaknesses": [long_ev, spec_ev] * 3,
            "rating": 10,
        },
        "team": {
            "narrative": fill(4000, "a very long management team narrative that keeps going"),
            "strengths": [long_ev] * 6,
            "weaknesses": [long_ev, spec_ev] * 3,
            "rating": 1,
        },
        "risks": [
            {
                "name": name,
                "level": level,
                "rationale": fill(800, f"a very long rationale for the {name} risk rating"),
                "evidence": [long_ev, spec_ev],
            }
            for name, level in zip(
                [*RISK_ORDER, "Reimbursement", "Concentration", "Supply Chain"],
                [
                    "Critical",
                    "High",
                    "Critical",
                    "High",
                    "Critical",
                    "Medium",
                    "High",
                    "Critical",
                    "High",
                    "Medium",
                ],
                strict=True,
            )
        ],
        "ic_view": {
            "biggest_strengths": [
                fill(180, f"an extremely long strength statement number {i}") for i in range(5)
            ],
            "biggest_concerns": [
                fill(180, f"an extremely long concern statement number {i}") for i in range(5)
            ],
            "diligence_questions": [
                {
                    "question": fill(500, f"an extremely long diligence question number {i}"),
                    "why_it_matters": fill(400, "an extremely long explanation of why it matters"),
                    "priority": ["Critical", "High", "Medium"][i % 3],
                }
                for i in range(12)
            ],
            "recommendation": "ADVANCE_TO_PARTNER_MEETING",
            "advance_rationale": fill(800, "an extremely long rationale for advancing this deal"),
            "confidence": "LOW",
        },
        "scorecard": [
            {
                "name": name,
                "value": 10,
                "justification": fill(200, f"a very long justification for the {name} score"),
            }
            for name in SCORECARD_ORDER
        ],
        "overall_investability": 10,
        "recommendations": [
            {
                "target": ["company", "narrative"][i % 2],
                "action": fill(400, f"an extremely long recommended action number {i}"),
                "priority": ["Critical", "High", "Medium"][i % 3],
                "rationale": fill(400, "an extremely long rationale for this recommendation"),
            }
            for i in range(14)
        ],
        "executive_summary": fill(1200, "an extremely long executive summary that runs to the cap"),
        "meta": {
            "model": "claude-opus-5",
            "source_filename": fill(120, "a_very_long_source_deck_filename_that_goes_on"),
            "slide_count": 60,
            "ingest_warnings": [fill(200, "a long ingest warning about dropped slide images")] * 3,
            "grounding_warnings": [
                fill(200, "a long grounding warning about downgraded facts"),
                "Grounding: 3/24 FACT claim(s) verified against extracted slide text.",
            ],
            "generated_at": FROZEN.isoformat(),
        },
    }


def write(name: str, payload: dict[str, Any]) -> None:
    """Validate through the models, then write formatted JSON."""
    assessment = Assessment.model_validate(payload)
    target = HERE / name
    target.write_text(
        assessment.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    write("sample_assessment.json", _sample())
    write("overstuffed_assessment.json", _overstuffed())
