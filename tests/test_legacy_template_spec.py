"""The one-pager template is a contract, not documentation.

`templates/legacy_onepager.md` states the fixed vocabularies the renderer will lay out. If the
framework changes in `schema.py` or `weights.toml` and the template is not updated, the
renderer gets built against a stale field map — so the drift is caught here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckpager.analysis.schema import RISK_ORDER, SCORECARD_ORDER
from deckpager.config import load_weights

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "legacy_onepager.md"

#: The five risk categories the one-pager has room for. The other two are assessed and
#: stored, and appear in the --full memo; see the template's §4.2.
ONEPAGER_RISKS = (
    "Execution",
    "Technology",
    "Commercialization",
    "Regulatory",
    "Go-to-Market",
)


@pytest.fixture(scope="module")
def template_text() -> str:
    assert TEMPLATE.is_file(), f"{TEMPLATE} is missing"
    return TEMPLATE.read_text(encoding="utf-8")


def _vocabulary(text: str, label: str) -> list[str]:
    """Pull one `**Label ...:** a · b · c` list out of the fixed-vocabularies section."""
    match = re.search(rf"\*\*{re.escape(label)}[^*]*:\*\*(.+?)(?=\n\n)", text, re.S)
    assert match, f"the template has no vocabulary line for {label!r}"
    return [item.strip() for item in match.group(1).replace("\n", " ").split("·")]


class TestVocabulariesMatchTheSchema:
    def test_scorecard_rows_match_in_content_and_order(self, template_text: str) -> None:
        assert _vocabulary(template_text, "Scorecard rows") == list(SCORECARD_ORDER)

    def test_onepager_risks_are_a_subset_of_the_schema(self, template_text: str) -> None:
        listed = _vocabulary(template_text, "Risk categories on the one-pager")
        assert listed == list(ONEPAGER_RISKS)
        assert set(listed) <= set(RISK_ORDER)

    def test_held_risks_account_for_the_remainder(self, template_text: str) -> None:
        """Every assessed category is either rendered or explicitly held — none dropped."""
        rendered = _vocabulary(template_text, "Risk categories on the one-pager")
        held = _vocabulary(template_text, "Risk categories assessed but held for the memo")
        assert set(rendered) | set(held) == set(RISK_ORDER)
        assert not set(rendered) & set(held)

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Risk levels", ["Low", "Medium", "High", "Critical"]),
            ("Recommendations", ["ADVANCE_TO_PARTNER_MEETING", "MORE_DILIGENCE", "PASS"]),
            ("Confidence", ["HIGH", "MEDIUM", "LOW"]),
            ("Evidence bases", ["FACT", "INFERENCE", "SPECULATION"]),
        ],
    )
    def test_enum_vocabularies(self, template_text: str, label: str, expected: list) -> None:
        assert _vocabulary(template_text, label) == expected


class TestTemplateCoverage:
    def test_every_weighted_category_has_a_scorecard_row(self, template_text: str) -> None:
        rows = set(_vocabulary(template_text, "Scorecard rows"))
        assert set(load_weights()) <= rows

    def test_the_reduction_ladder_is_stated_in_order(self, template_text: str) -> None:
        """The order is the guarantee — it is what the reader loses, cheapest first."""
        ladder = [
            "Truncate the executive summary",
            "Drop diligence questions",
            "Shorten each risk reason",
            "Step body 9pt",
            "Tighten line-height",
        ]
        positions = [template_text.index(step) for step in ladder]
        assert positions == sorted(positions)

    def test_overflow_raises_rather_than_spilling(self, template_text: str) -> None:
        assert "OnePagerOverflowError" in template_text
        assert "never silently emits page 2" in template_text

    def test_footer_logo_asset_exists(self, template_text: str) -> None:
        assert "assets/TEN_Capital_logo_footer.png" in template_text
        assert (TEMPLATE.parents[1] / "assets" / "TEN_Capital_logo_footer.png").is_file()

    def test_template_carries_no_company_specific_content(self, template_text: str) -> None:
        """It is a skeleton: no client names, no sample figures, no example verdicts."""
        for name in ("Aether", "AccuBreath", "Qualisure", "Solaris", "Delvify", "SKYCORP"):
            assert name not in template_text
