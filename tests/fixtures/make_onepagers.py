"""Regenerate the one-pager JSON fixtures.

Run from the repo root:  python tests/fixtures/make_onepagers.py

Two documents, for two jobs:

* `sample_onepager.json` is a realistic, fully-populated extraction. It is what the layout
  is designed against and what `deckpager redraw` is demonstrated on.
* `overstuffed_onepager.json` is every field at the maximum length the schema permits, with
  every list at its cap. No real deck produces it. It exists so the one-page guarantee is
  tested against the worst input the schema can express rather than against a typical one —
  a guarantee that only holds for average content is not a guarantee.

Both are committed so the test suite never depends on this script.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent

FROZEN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def field(value: Any, confidence: float = 0.9, slides: list[int] | None = None, note: str | None = None) -> dict:
    """One extracted field with its evidence."""
    payload: dict[str, Any] = {
        "value": value,
        "confidence": confidence,
        "source_slides": slides or [1],
    }
    if note:
        payload["note"] = note
    return payload


def provenance(filename: str, pages: int) -> dict:
    return {
        "source_filename": filename,
        "source_page_count": pages,
        "extracted_at": FROZEN.isoformat(),
        "model": "claude-opus-5",
        "input_tokens": 24_310,
        "output_tokens": 1_842,
        "estimated_cost_usd": 0.11,
        "cached": False,
        "ingest_warnings": [],
        "truncations": [],
    }


def sample() -> dict:
    """A realistic one-pager, with two fields deliberately below the flag threshold."""
    return {
        "company_name": field("Helion Bio", 0.98, [1]),
        "tagline": field("Reprogramming macrophages to clear solid tumors", 0.95, [1]),
        "website": field("helionbio.com", 0.45, [17], "Inferred from the CEO email domain"),
        "hq_location": field("San Diego, CA", 0.9, [1]),
        "founded_year": field(2023, 0.8, [10]),
        "sector": field("Biotech", 0.92, [1]),
        "sub_sector": field("Solid tumor immuno-oncology", 0.85, [3]),
        "stage": field("Seed", 0.5, [12], "Inferred from round size and stated milestones"),
        "raise_amount_usd": field(4_000_000, 0.95, [12]),
        "pre_money_valuation_usd": field(16_000_000, 0.9, [12]),
        "instrument": field("SAFE, 20% discount, $16M cap", 0.9, [12]),
        "amount_committed_usd": field(1_100_000, 0.85, [12]),
        "min_check_usd": field(50_000, 0.7, [12]),
        "close_date": field("Q3 2026", 0.8, [12]),
        "problem": field(
            "Checkpoint inhibitors fail in 80% of solid tumor patients. Tumor-associated "
            "macrophages actively suppress the T-cell response, and no approved therapy "
            "targets the macrophage compartment directly.",
            0.93,
            [2, 3],
        ),
        "solution": field(
            "HLN-101 is an oral small-molecule SIRP-alpha antagonist that repolarizes M2 "
            "macrophages to an M1 phenotype in vitro, restoring T-cell activity. No cold "
            "chain, once-daily dosing.",
            0.9,
            [4, 5],
        ),
        "business_model": field(
            "Out-license after Phase 1b to a large-cap oncology partner; upfront plus "
            "development and sales milestones, tiered royalties on net sales.",
            0.75,
            [8],
        ),
        "go_to_market": field(
            "Two academic sites for the Phase 1a, expanding to six community oncology "
            "centers; KOL-led publication strategy ahead of partnering conversations.",
            0.7,
            [9],
        ),
        "use_of_funds": field(
            [
                "IND-enabling toxicology and CMC",
                "GMP manufacture of first clinical batch",
                "Phase 1a start at two academic sites",
                "Two additional research FTEs",
            ],
            0.88,
            [12],
        ),
        "traction_metrics": field(
            [
                {"label": "Tumor growth inhibition", "value": "68%", "period": "Murine CT26", "source_slide": 6},
                {"label": "Non-dilutive funding", "value": "$1.4M", "period": "NIH SBIR Ph I", "source_slide": 7},
                {"label": "Committed this round", "value": "$1.1M", "period": None, "source_slide": 12},
                {"label": "Composition-of-matter patents", "value": "2 granted", "period": "US + EP", "source_slide": 11},
                {"label": "Pharma diligence conversations", "value": "3 under NDA", "period": None, "source_slide": 9},
                {"label": "Cash runway at close", "value": "18 months", "period": None, "source_slide": 12},
            ],
            0.85,
            [6, 7, 9, 11, 12],
        ),
        "tam_usd": field(24_000_000_000, 0.7, [6], "Global solid-tumor immunotherapy, sourced to EvaluatePharma"),
        "sam_usd": field(6_400_000_000, 0.65, [6]),
        "som_usd": field(480_000_000, 0.6, [6]),
        "market_note": field(
            "Checkpoint-refractory solid tumors only; assumes 12% penetration of second-line "
            "patients at parity pricing with existing IO combinations.",
            0.6,
            [6],
        ),
        "team": field(
            [
                {"name": "Dr. Marisol Reyes", "role": "CEO", "background": "12 years at Genentech; led two INDs to first-in-human"},
                {"name": "Dr. Peter Kwan", "role": "CSO", "background": "Professor of Immunology, UCSD; inventor of the core chemistry"},
                {"name": "Ana Duarte", "role": "VP Development", "background": "Ran CMC for three oncology programs at Arcus"},
                {"name": "Commercial lead", "role": "Open", "background": "Search opens after the Phase 1a start"},
            ],
            0.9,
            [10],
        ),
        "competitors": field(
            ["ALX Oncology", "Pfizer (Trillium)", "Gilead (Forty Seven)", "Arcus Biosciences"],
            0.8,
            [9],
        ),
        "differentiation": field(
            "Oral rather than infused, and macrophage-directed rather than CD47-blocking, "
            "which avoids the anemia that has limited the class.",
            0.75,
            [9],
        ),
        "key_strengths": field(
            [
                "Two granted composition-of-matter patents in US and EP",
                "$1.4M non-dilutive NIH funding already secured",
                "CEO has taken two prior programs into first-in-human",
            ],
            0.8,
            [7, 10, 11],
        ),
        "key_risks": field(
            [
                "No human data; efficacy rests on one murine tumor model",
                "Commercial lead seat open with no named search firm",
                "$4M is short of the stated 18-month runway to Phase 1a",
            ],
            0.75,
            [6, 10, 12],
        ),
        "missing_information": field(
            [
                "Full cap table including the prior SAFE and its conversion terms",
                "IND-enabling toxicology protocol and the agreed FDA pre-IND feedback",
                "CMC readiness: contract manufacturer, batch size, and cost per batch",
                "Named lead investor for the current round, and the terms already agreed",
                "Second animal model or any human ex-vivo data supporting the mechanism",
            ],
            0.85,
            [10, 11, 12],
        ),
        "provenance": provenance("helion_bio_seed_deck.pdf", 14),
    }


def overstuffed() -> dict:
    """Every field at its schema maximum. No real deck produces this."""

    def filler(length: int, word: str = "consideration") -> str:
        """Exactly `length` characters — the schema maximum, not one over it."""
        unit = f"{word} "
        return (unit * (length // len(unit) + 2))[: length - 1] + "."

    return {
        "company_name": field(filler(120, "Extraordinarily"), 0.4, [1]),
        "tagline": field(filler(90, "unabbreviated"), 0.4, [1]),
        "website": field(filler(120, "subdomain"), 0.4, [1]),
        "hq_location": field(filler(120, "municipality"), 0.4, [1]),
        "founded_year": field(2019, 0.4, [1]),
        "sector": field(filler(120, "classification"), 0.4, [1]),
        "sub_sector": field(filler(120, "subclassification"), 0.4, [1]),
        "stage": field("Series C+", 0.4, [1]),
        "raise_amount_usd": field(987_654_321, 0.4, [1]),
        "pre_money_valuation_usd": field(9_876_543_210, 0.4, [1]),
        "instrument": field(filler(120, "convertible"), 0.4, [1]),
        "amount_committed_usd": field(876_543_210, 0.4, [1]),
        "min_check_usd": field(9_876_543, 0.4, [1]),
        "close_date": field(filler(120, "provisionally"), 0.4, [1]),
        "problem": field(filler(320), 0.4, [1]),
        "solution": field(filler(320), 0.4, [1]),
        "business_model": field(filler(220), 0.4, [1]),
        "go_to_market": field(filler(220), 0.4, [1]),
        "use_of_funds": field([filler(70) for _ in range(4)], 0.4, [1]),
        "traction_metrics": field(
            [
                {
                    "label": filler(120, "measurement"),
                    "value": filler(120, "quantification"),
                    "period": filler(120, "throughout"),
                    "source_slide": 1,
                }
                for _ in range(6)
            ],
            0.4,
            [1],
        ),
        "tam_usd": field(999_000_000_000, 0.4, [1]),
        "sam_usd": field(888_000_000_000, 0.4, [1]),
        "som_usd": field(777_000_000_000, 0.4, [1]),
        "market_note": field(filler(220), 0.4, [1]),
        "team": field(
            [
                {
                    "name": filler(120, "Bartholomew"),
                    "role": filler(120, "Vice-President"),
                    "background": filler(90, "distinguished"),
                }
                for _ in range(4)
            ],
            0.4,
            [1],
        ),
        "competitors": field([filler(120, "Incumbent") for _ in range(5)], 0.4, [1]),
        "differentiation": field(filler(200), 0.4, [1]),
        "key_strengths": field([filler(90, "advantage") for _ in range(3)], 0.4, [1]),
        "key_risks": field([filler(90, "exposure") for _ in range(3)], 0.4, [1]),
        "missing_information": field([filler(200, "outstanding") for _ in range(5)], 0.4, [1]),
        "provenance": provenance("overstuffed_deck.pdf", 40),
    }


if __name__ == "__main__":
    for name, builder in (("sample_onepager", sample), ("overstuffed_onepager", overstuffed)):
        target = HERE / f"{name}.json"
        target.write_text(json.dumps(builder(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {target}")
