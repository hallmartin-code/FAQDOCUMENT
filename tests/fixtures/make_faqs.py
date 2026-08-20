"""Regenerate the FAQ JSON fixtures.

Run from the repo root:  python tests/fixtures/make_faqs.py

Two documents, for two jobs:

* `sample_faq.json` is a realistic extraction: most questions answered, a few not, one
  answer weak. It is what the layout is designed against and what `deckpager redraw` is
  demonstrated on.
* `maximal_faq.json` is every answer at the maximum length the schema permits. No real
  deck produces it. It exists so pagination and the page furniture are exercised against
  the worst input the schema can express rather than a typical one.

Both are committed so the test suite never depends on this script.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from deckpager.models import AnswerText  # noqa: E402
from deckpager.questions import QUESTIONS  # noqa: E402

HERE = Path(__file__).parent
FROZEN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
MAX_ANSWER = AnswerText.__metadata__[0].max_length  # type: ignore[attr-defined]


def field(
    value: Any,
    confidence: float = 0.9,
    slides: list[int] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """One extracted value with its evidence."""
    return {
        "value": value,
        "confidence": 0.0 if value is None else confidence,
        "source_slides": [] if value is None else (slides or [1]),
        "note": note,
    }


#: Answers for the realistic fixture. A company that exists nowhere, so nothing here can be
#: mistaken for a real diligence document. Four questions are deliberately unanswered.
ANSWERS: dict[str, tuple[str | None, float, list[int]]] = {
    "positioning-what": (
        "Helion Bio is developing an oral small-molecule therapy for treatment-resistant "
        "hypertension. The deck describes a once-daily tablet targeting a renal sodium "
        "channel, positioned as an add-on to standard therapy rather than a replacement.",
        0.95,
        [1, 4],
    ),
    "positioning-why-now": (
        "Slide 3 cites the 2025 guideline change lowering the treatment-resistant "
        "threshold, which the deck states expands the eligible population by 40%. No "
        "source is given for the 40% figure.",
        0.7,
        [3],
    ),
    "problem-size": (
        "The deck states 12 million US patients have treatment-resistant hypertension, "
        "that 8 million remain uncontrolled on three or more agents, and that uncontrolled "
        "hypertension costs the US system $52B a year (slide 2). None of the three figures "
        "carries a citation.",
        0.9,
        [2],
    ),
    "problem-status-quo": (
        "Current care is escalating combinations of diuretics, ACE inhibitors and calcium "
        "channel blockers, described on slide 2 as failing in roughly a third of patients. "
        "Payors are named as commercial insurers and Medicare; no cost per patient is "
        "given.",
        0.75,
        [2, 5],
    ),
    "solution-what": (
        "A once-daily oral tablet inhibiting a renal sodium channel, described on slides 4 "
        "and 6. The deck states the mechanism is distinct from existing diuretics because "
        "it acts distally, and claims this avoids the potassium loss that limits current "
        "agents.",
        0.9,
        [4, 6],
    ),
    "solution-evidence": (
        "Preclinical only. Slide 7 reports a 22 mmHg systolic reduction in a rat model "
        "(n=40) over 14 days, and slide 8 states IND-enabling toxicology is complete. No "
        "human data is presented anywhere in the deck.",
        0.95,
        [7, 8],
    ),
    "team-who": (
        "Dr Amara Osei, founder and CSO, is described as the inventor of the compound with "
        "18 years in renal pharmacology (slide 14). The CEO, Jonas Reeve, is listed as "
        "part-time until close of the round.",
        0.85,
        [14],
    ),
    "team-gaps": (
        "No clinical development lead and no regulatory lead are named, for a company whose "
        "next milestone is an IND filing. The deck names a CRO partner but not a "
        "manufacturer.",
        0.6,
        [14, 15],
    ),
    "market-size": (
        "Slide 9 gives a $9.4B TAM and a $2.1B SAM. The TAM is derived from patient count "
        "multiplied by an assumed $2,400 annual price; the SAM derivation is not shown.",
        0.8,
        [9],
    ),
    "market-model": (
        "Specialty pharmacy distribution at an assumed $2,400 per patient per year "
        "(slide 9). The deck models 60% gross margin at scale but does not state COGS.",
        0.7,
        [9, 10],
    ),
    "diff-competition": (
        "Slide 11 names two competitors — Vertex Renal and an unnamed Phase 2 asset — and "
        "differentiates on the distal mechanism and once-daily dosing. The comparison is "
        "asserted rather than shown against published data.",
        0.65,
        [11],
    ),
    "diff-moat": (
        "One composition-of-matter application filed in the US and EU (slide 12), stated as "
        "pending rather than granted. Expiry is given as 2044. No method-of-use filings are "
        "mentioned.",
        0.85,
        [12],
    ),
    "traction-todate": (
        "A $1.1M NIH SBIR Phase I award (slide 13), IND-enabling toxicology complete "
        "(slide 8), and the patent filing on slide 12. No revenue, no clinical work.",
        0.9,
        [8, 12, 13],
    ),
    "traction-validation": (
        "The SBIR award is the only third-party validation in the deck. Slide 15 lists two "
        "academic advisors but no customers, partners, or committed investors.",
        0.55,
        [13, 15],
    ),
    "risk-primary": (
        "Slide 16 names clinical risk (no human data), regulatory timing, and financing "
        "risk, and states mitigations of an experienced CRO and a staged raise.",
        0.85,
        [16],
    ),
    "risk-unaddressed": (
        "The deck does not reconcile the 18-month runway on slide 17 with the 26-month "
        "timeline to Phase 1 readout on slide 18 — a gap of roughly eight months with no "
        "stated bridge.",
        0.8,
        [17, 18],
    ),
    "deal-ask": (
        "Raising $6M on a SAFE with a $24M post-money cap and a 20% discount (slide 17). "
        "$1.4M is stated as committed. No lead investor is named.",
        0.9,
        [17],
    ),
    "deal-use": (
        "Slide 17 allocates 55% to IND-enabling work and Phase 1, 25% to CMC, and 20% to "
        "operations, stating an 18-month runway. The Phase 1 readout on slide 18 sits "
        "beyond that runway.",
        0.85,
        [17, 18],
    ),
    "commit-founder": (None, 0.0, []),
    "commit-exit": (None, 0.0, []),
}

#: Two further questions left unanswered, to exercise the diligence list.
ANSWERS["team-gaps"] = ANSWERS["team-gaps"]
ANSWERS["market-model"] = ANSWERS["market-model"]


def build_sample() -> dict[str, Any]:
    """A realistic extraction: most questions answered, some not, one weak."""
    return {
        "company_name": field("Helion Bio", 1.0, [1]),
        "tagline": field("Oral therapy for treatment-resistant hypertension.", 0.95, [1]),
        "sector": field("Biotechnology — cardiovascular", 0.9, [1]),
        "stage": field("Seed", 0.8, [17]),
        "not_a_pitch_deck_reason": field(None),
        "entries": [
            {
                "question_id": question.id,
                "answer": field(*ANSWERS[question.id]),
            }
            for question in QUESTIONS
        ],
        "provenance": {
            "source_filename": "helion_bio_seed_deck.pdf",
            "source_page_count": 19,
            "extracted_at": FROZEN.isoformat(),
            "model": "claude-opus-5",
            "input_tokens": 41_200,
            "output_tokens": 5_100,
            "estimated_cost_usd": 0.33,
            "cached": False,
            "ingest_warnings": [],
            "citation_warnings": [],
            "truncations": [],
        },
    }


def build_maximal() -> dict[str, Any]:
    """Every answer at the schema's ceiling — the worst input the contract allows."""
    filler = (
        "The deck states this at the maximum length the schema permits, with figures, "
        "caveats, and a citation of the slide it came from, repeated until the limit. "
    )
    body = (filler * 40)[:MAX_ANSWER]
    document = build_sample()
    document["company_name"] = field("A" * 120, 1.0, [1])
    document["tagline"] = field("T" * 90, 1.0, [1])
    document["entries"] = [
        {
            "question_id": question.id,
            "answer": field(body, 0.5, [1, 2, 3, 4, 5]),
        }
        for question in QUESTIONS
    ]
    document["provenance"]["source_filename"] = "maximal_deck.pdf"
    return document


if __name__ == "__main__":
    for name, document in (
        ("sample_faq.json", build_sample()),
        ("maximal_faq.json", build_maximal()),
    ):
        path = HERE / name
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")
