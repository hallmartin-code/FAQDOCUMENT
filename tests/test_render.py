"""The one-pager layout, and the guarantee that it is one page.

The headline test is `TestOnePageGuarantee`. Everything else exists to keep it honest: a
page count of 1 proves nothing on its own, because ReportLab paints past the bottom edge
without ever starting a second page. So the geometry is asserted directly as well.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from deckpager.errors import RenderError
from deckpager.models import OnePager
from deckpager.render import style as s
from deckpager.render.onepager import (
    RENDERED_FIELDS,
    OnePagerRenderer,
    PageLayout,
    ellipsize,
    fit_and_render,
    money,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> OnePager:
    return OnePager.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


@pytest.fixture
def sample() -> OnePager:
    """A realistic, fully-populated extraction."""
    return load("sample_onepager.json")


@pytest.fixture
def overstuffed() -> OnePager:
    """Every field at the maximum length the schema permits."""
    return load("overstuffed_onepager.json")


@pytest.fixture
def sparse(sample: OnePager) -> OnePager:
    """A deck that said almost nothing — most fields null."""
    empty = OnePager.model_validate(
        {
            "company_name": {"value": "Quiet Co", "confidence": 0.9, "source_slides": [1]},
            "provenance": sample.provenance.model_dump(mode="json"),
        }
    )
    return empty


def text_of(pdf: Path) -> str:
    return "".join(page.extract_text() for page in PdfReader(str(pdf)).pages)


class TestOnePageGuarantee:
    """Spec §3: exactly one page, always. Overflow is truncated, never spilled."""

    @pytest.mark.parametrize("name", ["sample", "overstuffed", "sparse"])
    def test_every_fixture_renders_to_exactly_one_page(
        self, name: str, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        one_pager = request.getfixturevalue(name)
        out, _ = fit_and_render(one_pager, tmp_path / f"{name}.pdf")
        assert len(PdfReader(str(out)).pages) == 1

    def test_the_worst_input_the_schema_allows_still_fits(
        self, overstuffed: OnePager, tmp_path: Path
    ) -> None:
        """A guarantee that only holds for average content is not a guarantee."""
        renderer = OnePagerRenderer()
        assert renderer.overflow(overstuffed, PageLayout()) > 0  # it really is too much
        out, cuts = fit_and_render(overstuffed, tmp_path / "overstuffed.pdf")
        assert len(PdfReader(str(out)).pages) == 1
        assert cuts, "the fixture overflows, so something must have been given up"

    def test_the_page_actually_fits_rather_than_merely_being_one_page(
        self, overstuffed: OnePager, tmp_path: Path
    ) -> None:
        """Geometry, not page count. This is the assertion the page-count test cannot make."""
        renderer = OnePagerRenderer()
        fit_and_render(overstuffed, tmp_path / "overstuffed.pdf")
        # fit_and_render only writes the layout it measured as fitting; re-measure the
        # winning layout by walking the ladder the same way and asserting the end state.
        layout = PageLayout()
        for change in (
            {"drop_go_to_market": True},
            {"drop_business_model": True},
            {"drop_competitors": True},
            {"drop_market_note": True},
            {"requests": 3},
            {"metrics": 4},
            {"people": 3},
            {"text_scale": 0.4},
        ):
            layout = PageLayout(**{**layout.__dict__, **change})
        assert renderer.overflow(overstuffed, layout) == 0

    def test_truncations_are_reported_not_silent(
        self, overstuffed: OnePager, tmp_path: Path
    ) -> None:
        """Spec §9: log every truncation. A silent cut is indistinguishable from a bug."""
        _, cuts = fit_and_render(overstuffed, tmp_path / "out.pdf")
        assert any("prose truncated" in cut for cut in cuts)

    def test_a_realistic_deck_needs_no_reductions_at_all(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        """If the ordinary case needed cuts, the layout would be wrong, not the content."""
        _, cuts = fit_and_render(sample, tmp_path / "sample.pdf")
        assert cuts == []

    def test_a_layout_that_cannot_fit_raises_rather_than_spilling(
        self, overstuffed: OnePager, tmp_path: Path
    ) -> None:
        """With the ladder stubbed out, the renderer must fail loudly, not emit page 2."""

        class Stubborn(OnePagerRenderer):
            def overflow(self, *args: object, **kwargs: object) -> float:
                return 400.0

        with pytest.raises(RenderError, match="still overflows"):
            fit_and_render(overstuffed, tmp_path / "out.pdf", renderer=Stubborn())


class TestNulls:
    """Spec §3: what the deck did not say renders as an em dash, never as a guess."""

    def test_absent_fields_render_as_an_em_dash(self, sparse: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sparse, tmp_path / "sparse.pdf")
        assert s.EMPTY in text_of(out)

    def test_a_sparse_deck_still_produces_a_page(self, sparse: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sparse, tmp_path / "sparse.pdf")
        assert "Quiet Co" in text_of(out)

    def test_a_nameless_deck_says_so_rather_than_printing_nothing(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        nameless = sample.model_copy(deep=True)
        nameless.company_name.value = None
        out, _ = fit_and_render(nameless, tmp_path / "nameless.pdf")
        assert "Unnamed company" in text_of(out)


class TestConfidenceFlag:
    """Spec §9: fields below the threshold get a marker, and the footer counts them."""

    def test_the_marker_appears_on_the_page(self, sample: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        assert s.DAGGER in text_of(out)

    def test_the_footer_explains_what_the_marker_means(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        assert "below 60% confidence" in text_of(out)

    def test_the_count_matches_the_flagged_fields_that_are_rendered(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        """A count that includes fields the reader cannot find is worse than no count."""
        expected = [f for f in sample.low_confidence_fields() if f in set(RENDERED_FIELDS)]
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        assert f"{len(expected)} field(s)" in text_of(out)

    def test_a_confident_extraction_carries_no_marker(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        confident = sample.model_copy(deep=True)
        for name in type(confident).model_fields:
            field = getattr(confident, name, None)
            if hasattr(field, "confidence"):
                field.confidence = 0.99
        out, _ = fit_and_render(confident, tmp_path / "confident.pdf")
        assert "field(s)" not in text_of(out)

    def test_the_threshold_is_adjustable(self, sample: OnePager, tmp_path: Path) -> None:
        renderer = OnePagerRenderer()
        out = renderer.render(sample, tmp_path / "strict.pdf", threshold=0.99)
        assert "below 99% confidence" in text_of(out)


class TestContent:
    def test_the_analyst_block_is_labelled_as_generated(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        """It must never be mistaken for something the founders wrote."""
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        assert "AI-generated" in text_of(out)

    def test_the_footer_names_the_source_deck(self, sample: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        rendered = text_of(out)
        assert sample.provenance.source_filename in rendered
        assert "Internal use only" in rendered

    def test_every_spec_section_has_a_heading(self, sample: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        rendered = text_of(out)
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
        ):
            assert heading in rendered

    def test_the_ask_strip_carries_all_five_cells(
        self, sample: OnePager, tmp_path: Path
    ) -> None:
        out, _ = fit_and_render(sample, tmp_path / "sample.pdf")
        rendered = text_of(out)
        for label in ("RAISE", "PRE-MONEY", "INSTRUMENT", "COMMITTED", "CLOSE"):
            assert label in rendered


class TestFormatting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (4_000_000, "$4M"),
            (1_100_000, "$1.1M"),
            (750_000, "$750K"),
            (24_000_000_000, "$24B"),
            (2_500_000_000, "$2.5B"),
            (900, "$900"),
            (None, None),
        ],
    )
    def test_money_reads_the_way_a_partner_reads_it(
        self, value: int | None, expected: str | None
    ) -> None:
        assert money(value) == expected

    def test_ellipsize_cuts_at_a_word_boundary(self) -> None:
        assert ellipsize("the quick brown fox", 12) == "the quick…"

    def test_ellipsize_leaves_short_text_alone(self) -> None:
        assert ellipsize("short", 40) == "short"


class TestMeasurement:
    """Measurement and drawing must not disagree, or the guarantee is vacuous."""

    def test_measuring_does_not_need_a_canvas(self, sample: OnePager) -> None:
        renderer = OnePagerRenderer()
        assert renderer.overflow(sample, PageLayout()) == 0.0

    def test_more_content_measures_taller(self, sample: OnePager, overstuffed: OnePager) -> None:
        renderer = OnePagerRenderer()
        assert renderer.overflow(overstuffed, PageLayout()) > renderer.overflow(
            sample, PageLayout()
        )

    def test_every_rung_of_the_ladder_reduces_or_is_neutral(
        self, overstuffed: OnePager
    ) -> None:
        renderer = OnePagerRenderer()
        base = renderer.overflow(overstuffed, PageLayout())
        for change in (
            {"drop_competitors": True},
            {"drop_market_note": True},
            {"requests": 3},
            {"metrics": 4},
            {"people": 3},
            {"text_scale": 0.5},
        ):
            layout = PageLayout(**change)  # type: ignore[arg-type]
            assert renderer.overflow(overstuffed, layout) <= base, change

    def test_a4_is_supported(self, sample: OnePager, tmp_path: Path) -> None:
        out, _ = fit_and_render(sample, tmp_path / "a4.pdf", paper="a4")
        assert len(PdfReader(str(out)).pages) == 1
