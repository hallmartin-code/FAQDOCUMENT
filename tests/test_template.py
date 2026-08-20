"""The one-pager template is a contract, not documentation.

`templates/onepager.md` states the document structure the renderer lays out: the bands, the
field map, the vocabularies, the palette, and the reduction ladder. If `models.py`,
`style.py`, or `onepager.py` changes and the template does not, the renderer is being built
against a stale map — so the drift is caught here rather than discovered in a partner
meeting.

Every assertion below reads the template as text and checks it against the code. None of
them check prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckpager.models import DEFAULT_MIN_CONFIDENCE, OnePagerDraft, Stage
from deckpager.render import style as s
from deckpager.render.onepager import RENDERED_FIELDS, PageLayout

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "onepager.md"

#: Fields that are extracted but have no place on the page. The template names them in §4.7;
#: the renderer excludes them from RENDERED_FIELDS. Both have to agree.
NOT_RENDERED = ("founded_year", "sub_sector", "min_check_usd")


@pytest.fixture(scope="module")
def template() -> str:
    assert TEMPLATE.is_file(), f"{TEMPLATE} is missing"
    return TEMPLATE.read_text(encoding="utf-8")


def placeholders(text: str) -> set[str]:
    """Every `{field_name}` in the template, ignoring format specs like `{x:%d %b %Y}`."""
    return {match.split(":")[0] for match in re.findall(r"\{([a-z_]+[^}]*)\}", text)}


class TestFieldMap:
    def test_every_extracted_field_appears_in_the_template(self, template: str) -> None:
        """A field the template never mentions is a field nobody decided where to put."""
        documented = placeholders(template)
        missing = set(OnePagerDraft.model_fields) - documented
        assert not missing, f"the template does not account for: {sorted(missing)}"

    def test_the_template_invents_no_fields(self, template: str) -> None:
        known = set(OnePagerDraft.model_fields) | {
            "source_filename",
            "extracted_at",
            "field_name",
            "n",
            "threshold",
        }
        invented = placeholders(template) - known
        assert not invented, f"the template names fields that do not exist: {sorted(invented)}"

    def test_the_unrendered_fields_are_declared_in_both_places(self, template: str) -> None:
        for name in NOT_RENDERED:
            assert name not in RENDERED_FIELDS, f"{name} is rendered but §4.7 says it is not"
            assert name in template, f"§4.7 does not name {name}"

    def test_every_rendered_field_is_a_real_field(self) -> None:
        unknown = set(RENDERED_FIELDS) - set(OnePagerDraft.model_fields)
        assert not unknown, f"RENDERED_FIELDS names non-fields: {sorted(unknown)}"

    def test_rendered_plus_unrendered_covers_the_schema(self) -> None:
        assert set(RENDERED_FIELDS) | set(NOT_RENDERED) == set(OnePagerDraft.model_fields)


class TestVocabularies:
    def test_the_stage_vocabulary_matches_the_schema(self, template: str) -> None:
        from typing import get_args

        match = re.search(r"closed vocabulary:\s*\*\*(.+?)\*\*", template, re.S)
        assert match, "the template states no stage vocabulary"
        listed = {part.strip() for part in match.group(1).replace("\n", " ").split("·")}
        assert listed == set(get_args(Stage))

    def test_the_section_headings_match_what_the_renderer_draws(self, template: str) -> None:
        for heading in (
            "PROBLEM",
            "SOLUTION",
            "BUSINESS MODEL",
            "GO-TO-MARKET",
            "USE OF FUNDS",
            "TRACTION",
            "MARKET",
            "TEAM",
            "COMPETITION",
            "STRENGTHS",
            "RISKS",
            "REQUEST FROM FOUNDER",
        ):
            assert heading in template, f"the template omits the {heading} section"

    def test_the_ask_cells_match_the_strip(self, template: str) -> None:
        for label in ("RAISE", "PRE-MONEY", "INSTRUMENT", "COMMITTED", "CLOSE"):
            assert label in template


class TestFormattingContract:
    def test_the_palette_hexes_are_the_ones_in_style(self, template: str) -> None:
        for token in (s.INK, s.ACCENT, s.MUTED, s.TINT_ASK, s.TINT_ANALYST, s.RULE):
            assert token.hexval()[2:].upper() in template.upper(), f"{token} is not documented"

    def test_the_type_scale_is_the_one_in_style(self, template: str) -> None:
        for size in (
            s.SIZE_COMPANY,
            s.SIZE_TAGLINE,
            s.SIZE_ASK_VALUE,
            s.SIZE_HEADING,
            s.SIZE_BODY,
            s.SIZE_CHIP,
        ):
            rendered = f"{size:g}pt"
            assert rendered in template, f"the template does not state {rendered}"

    def test_the_column_split_is_the_one_in_style(self, template: str) -> None:
        left = f"{s.LEFT_COLUMN_RATIO:.0%}"
        right = f"{1 - s.LEFT_COLUMN_RATIO:.0%}"
        assert left in template and right in template

    def test_the_band_heights_are_the_ones_in_style(self, template: str) -> None:
        for height in (s.HEIGHT_HEADER, s.HEIGHT_ASK, s.HEIGHT_FOOTER):
            assert f"{height:g}pt" in template

    def test_the_confidence_threshold_is_the_default(self, template: str) -> None:
        assert f"{DEFAULT_MIN_CONFIDENCE:.2f}" in template

    def test_the_null_and_flag_marks_are_documented(self, template: str) -> None:
        assert s.EMPTY in template
        assert s.DAGGER in template

    def test_the_analyst_label_is_quoted_exactly(self, template: str) -> None:
        assert s.ANALYST_LABEL in template

    def test_the_footer_line_is_quoted_exactly(self, template: str) -> None:
        assert "Generated by TEN Capital" in template
        assert "Internal use only" in template


class TestLadderContract:
    def test_the_type_floor_matches_the_ladder(self, template: str) -> None:
        assert "7.5pt" in template

    def test_the_default_layout_matches_the_documented_starting_point(self) -> None:
        layout = PageLayout()
        assert layout.body_pt == s.SIZE_BODY
        assert layout.leading == s.LEADING
        assert layout.text_scale == 1.0
        assert (layout.requests, layout.metrics, layout.people) == (5, 6, 4)

    def test_the_documented_caps_match_the_schema(self, template: str) -> None:
        """The ladder starts at the schema caps; if one moves, both must move."""
        layout = PageLayout()
        arrow = chr(0x2192)
        for label, start, floor in (
            ("Diligence requests", layout.requests, 3),
            ("Traction tiles", layout.metrics, 4),
            ("Team", layout.people, 3),
        ):
            steps = list(range(start, floor - 1, -1))
            rung = f"{label} " + f" {arrow} ".join(str(n) for n in steps)
            assert rung in template, f"the ladder table does not state: {rung}"

    def test_the_revert_and_restore_rules_are_stated(self, template: str) -> None:
        """These two rules are why the ladder does not discard content pointlessly."""
        assert "does not reduce the measured overflow is reverted" in template
        assert "offered back" in template


class TestValidationContract:
    def test_the_checklist_covers_the_guarantees_the_tests_enforce(self, template: str) -> None:
        for claim in (
            "exactly one page",
            "Measured overflow",
            "source slide number",
            "provenance.truncations",
        ):
            assert claim in template, f"the validation checklist omits {claim!r}"

    def test_the_template_carries_no_company_data(self, template: str) -> None:
        """It is a structure template. A company name in it would become a default."""
        for leaked in ("AccuBreath", "Helion", "Spencer", "Madsen", "SIRP", "ventilat"):
            assert leaked.lower() not in template.lower(), f"{leaked} leaked into the template"
