"""Ingestion tests: both format paths, the router, and the caps logic."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from deckpager.errors import IngestError, UnsupportedFormatError
from deckpager.ingest.legacy_ppt import install_hint, load_ppt
from deckpager.ingest.models import Deck, Slide, SlideAsset, normalize_text
from deckpager.ingest.pdf import flatten_table, load_pdf
from deckpager.ingest.pptx import find_soffice, load_pptx
from deckpager.ingest.router import OLE2_MAGIC, apply_caps, load_deck


def _fake_asset(kilobytes: int) -> SlideAsset:
    """Build an asset of a known approximate decoded size."""
    return SlideAsset(data_b64=base64.standard_b64encode(b"x" * (kilobytes * 1000)).decode())


class TestPdfPath:
    def test_extracts_every_page(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.source_format == "pdf"
        assert deck.slide_count == 5
        assert [s.index for s in deck.slides] == [1, 2, 3, 4, 5]

    def test_prefers_the_native_document_path(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.raw_pdf_b64 is not None
        assert base64.standard_b64decode(deck.raw_pdf_b64) == sample_pdf.read_bytes()
        # Native path sends the file itself; per-page rasters would be redundant bytes.
        assert all(s.asset is None for s in deck.slides)

    def test_extracts_local_text_for_grounding(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert "Helion Bio" in deck.slides[0].text
        assert "80% of solid tumor patients" in deck.slides[1].text
        assert "No lead investor committed" in deck.slides[4].text

    def test_titles_come_from_the_first_line(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.slides[0].title == "Helion Bio"
        assert deck.slides[3].title == "Team"

    def test_pdf_has_no_speaker_notes(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert all(s.speaker_notes is None for s in deck.slides)

    def test_falls_back_to_rasters_when_over_the_native_limits(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deckpager.ingest.pdf.MAX_NATIVE_PDF_PAGES", 2)
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.raw_pdf_b64 is None
        assert all(s.asset is not None for s in deck.slides)
        assert deck.image_bytes > 0
        assert any("falling back to rasterized page images" in w for w in deck.warnings)

    def test_fallback_respects_no_images(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deckpager.ingest.pdf.MAX_NATIVE_PDF_PAGES", 2)
        deck = load_pdf(sample_pdf, want_images=False)
        assert deck.raw_pdf_b64 is None
        assert all(s.asset is None for s in deck.slides)
        assert all(s.text for s in deck.slides)

    def test_rejects_a_non_pdf(self, tmp_path: Path) -> None:
        bogus = tmp_path / "broken.pdf"
        bogus.write_bytes(b"%PDF-1.4 but not really")
        with pytest.raises(IngestError):
            load_pdf(bogus, want_images=False)


class TestPptxPath:
    def test_extracts_every_slide(self, sample_pptx: Path) -> None:
        deck = load_pptx(sample_pptx, want_images=False)
        assert deck.source_format == "pptx"
        assert deck.slide_count == 5
        assert deck.raw_pdf_b64 is None

    def test_extracts_body_text(self, sample_pptx: Path) -> None:
        deck = load_pptx(sample_pptx, want_images=False)
        assert "Reprogramming macrophages" in deck.slides[0].text
        assert "SIRP-alpha antagonist" in deck.slides[2].text

    def test_extracts_speaker_notes(self, sample_pptx: Path) -> None:
        """Speaker notes are where the real numbers often hide — they must survive."""
        deck = load_pptx(sample_pptx, want_images=False)
        assert deck.slides[0].speaker_notes is not None
        assert "Speaker notes for slide 1" in deck.slides[0].speaker_notes

    def test_warns_visibly_when_libreoffice_is_missing(
        self, sample_pptx: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("deckpager.ingest.pptx.find_soffice", lambda: None)
        deck = load_pptx(sample_pptx, want_images=True)
        assert any("LibreOffice" in w for w in deck.warnings)
        assert all(s.asset is None for s in deck.slides)

    def test_no_images_flag_skips_conversion_entirely(
        self, sample_pptx: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> str:
            raise AssertionError("soffice must not be probed when want_images is False")

        monkeypatch.setattr("deckpager.ingest.pptx.find_soffice", _boom)
        deck = load_pptx(sample_pptx, want_images=False)
        assert deck.warnings == []

    @pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
    def test_rasterizes_when_libreoffice_is_available(self, sample_pptx: Path) -> None:
        deck = load_pptx(sample_pptx, want_images=True)
        assert any(s.asset is not None for s in deck.slides)


class TestRouter:
    def test_dispatches_pdf(self, sample_pdf: Path) -> None:
        assert load_deck(sample_pdf, want_images=False).source_format == "pdf"

    def test_dispatches_pptx(self, sample_pptx: Path) -> None:
        assert load_deck(sample_pptx, want_images=False).source_format == "pptx"

    def test_dispatches_on_magic_bytes_not_extension(
        self, sample_pdf: Path, tmp_path: Path
    ) -> None:
        renamed = tmp_path / "deck.PDF"
        renamed.write_bytes(sample_pdf.read_bytes())
        assert load_deck(renamed, want_images=False).source_format == "pdf"

    def test_rejects_unknown_format(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes.txt"
        notes.write_text("just some text", encoding="utf-8")
        with pytest.raises(UnsupportedFormatError, match="not a PDF, PPTX, or PPT"):
            load_deck(notes)

    def test_rejects_extension_content_mismatch(self, sample_pdf: Path, tmp_path: Path) -> None:
        liar = tmp_path / "deck.pptx"
        liar.write_bytes(sample_pdf.read_bytes())
        with pytest.raises(UnsupportedFormatError, match="extension but its contents"):
            load_deck(liar)

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="No such file"):
            load_deck(tmp_path / "absent.pdf")


class TestCaps:
    def _deck(self, slides: list[Slide]) -> Deck:
        return Deck(source_path=Path("x.pptx"), source_format="pptx", slides=slides)

    def test_under_budget_is_untouched(self) -> None:
        deck = self._deck(
            [Slide(index=i, text="body " * 20, asset=_fake_asset(100)) for i in range(1, 4)]
        )
        apply_caps(deck, max_slides=40, max_image_slides=25, max_image_bytes=5_000_000)
        assert all(s.asset is not None for s in deck.slides)
        assert deck.warnings == []

    def test_images_stop_at_the_image_slide_limit(self) -> None:
        """Spec 7: images for the first N slides, text for all of them."""
        slides = [
            Slide(index=i, text="body " * 20, asset=_fake_asset(1)) for i in range(1, 11)
        ]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=40, max_image_slides=4, max_image_bytes=5_000_000)
        assert [s.index for s in deck.slides if s.asset is not None] == [1, 2, 3, 4]
        assert all(s.text for s in deck.slides)

    def test_image_dominant_slides_keep_their_image_wherever_they_sit(self) -> None:
        """A slide with no text is only readable as a picture, so the limit exempts it."""
        slides = [
            Slide(index=i, text="body " * 20, asset=_fake_asset(1)) for i in range(1, 10)
        ]
        slides.append(Slide(index=10, text="", asset=_fake_asset(1)))
        deck = self._deck(slides)
        apply_caps(deck, max_slides=40, max_image_slides=3, max_image_bytes=5_000_000)
        assert [s.index for s in deck.slides if s.asset is not None] == [1, 2, 3, 10]

    def test_byte_budget_sheds_text_heavy_slides_before_image_only_ones(self) -> None:
        """The slide with nothing to read is the last image worth giving up."""
        slides = [
            Slide(index=1, text="", asset=_fake_asset(900)),
            Slide(index=2, text="a" * 500, asset=_fake_asset(900)),
            Slide(index=3, text="a" * 1000, asset=_fake_asset(900)),
        ]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=40, max_image_slides=25, max_image_bytes=2_000_000)
        assert deck.slides[0].asset is not None
        assert deck.image_bytes <= 2_000_000
        assert sum(1 for s in deck.slides if s.asset is None) == 1

    def test_slide_text_is_never_dropped(self) -> None:
        slides = [Slide(index=i, text=f"slide {i}", asset=_fake_asset(1)) for i in range(1, 11)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=2, max_image_slides=2, max_image_bytes=1)
        assert deck.slide_count == 10
        assert all(s.text for s in deck.slides)

    def test_a_deck_over_the_slide_cap_is_reported_not_truncated(self) -> None:
        slides = [Slide(index=i, text="body " * 20) for i in range(1, 51)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=40, max_image_slides=25, max_image_bytes=5_000_000)
        assert deck.slide_count == 50
        assert any("above the 40-slide request cap" in w for w in deck.warnings)

    def test_drops_are_never_silent(self) -> None:
        slides = [Slide(index=i, text="a" * 200, asset=_fake_asset(900)) for i in range(1, 11)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=40, max_image_slides=25, max_image_bytes=1_000_000)
        assert any("dropped images from" in w for w in deck.warnings)

class TestImageDominance:
    """Spec 7: a page under 20 characters of text is read by looking, not reading."""

    def test_fires_on_the_image_heavy_deck(self, image_heavy_pdf: Path) -> None:
        deck = load_pdf(image_heavy_pdf, want_images=True)
        assert deck.image_dominant_slides == [1, 2, 3]

    def test_does_not_fire_on_a_text_deck(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.image_dominant_slides == []

    def test_speaker_notes_do_not_rescue_a_wordless_slide(self) -> None:
        """A full-bleed diagram with a page of notes is still a slide to look at."""
        slide = Slide(index=1, text="", speaker_notes="a" * 500)
        assert slide.image_dominant

    def test_a_pptx_without_libreoffice_says_which_slides_went_blind(self) -> None:
        """The one case where spec 7 cannot be satisfied must not pass silently."""
        slides = [
            Slide(index=1, text="a" * 200),
            Slide(index=2, text=""),
        ]
        deck = Deck(source_path=Path("x.pptx"), source_format="pptx", slides=slides)
        apply_caps(deck)
        assert any("no extractable text and no image" in w for w in deck.warnings)
        assert any(": 2." in w for w in deck.warnings)

    def test_a_natively_sent_pdf_is_not_reported_as_blind(
        self, image_heavy_pdf: Path
    ) -> None:
        """The whole file goes to the model, so wordless pages are still seen."""
        deck = load_deck(image_heavy_pdf, want_images=True)
        assert deck.raw_pdf_b64 is not None
        assert not any("nothing to read" in w for w in deck.warnings)


class TestChartDetection:
    def test_fires_on_drawn_charts(self, image_heavy_pdf: Path) -> None:
        deck = load_pdf(image_heavy_pdf, want_images=False)
        assert all(s.has_chart for s in deck.slides)

    def test_does_not_fire_on_prose_pages(self, sample_pdf: Path) -> None:
        deck = load_pdf(sample_pdf, want_images=False)
        assert not any(s.has_chart for s in deck.slides)

    def test_pptx_without_charts_reports_none(self, sample_pptx: Path) -> None:
        deck = load_pptx(sample_pptx, want_images=False)
        assert not any(s.has_chart for s in deck.slides)


class TestTableFlattening:
    """Spec 7: tables flatten to `Header: a | b | c`, one line per row."""

    def test_labels_the_row_with_its_first_cell(self) -> None:
        rows = [["Metric", "2024", "2025"], ["ARR", "$1.2M", "$4.0M"]]
        assert flatten_table(rows) == [
            "Metric: 2024 | 2025",
            "ARR: $1.2M | $4.0M",
        ]

    def test_drops_empty_rows_and_cells(self) -> None:
        rows = [["ARR", None, "$4.0M"], [None, None], ["", "  "]]
        assert flatten_table(rows) == ["ARR: $4.0M"]

    def test_a_single_cell_row_is_not_given_a_colon(self) -> None:
        assert flatten_table([["Traction"]]) == ["Traction"]

    def test_newlines_inside_a_cell_do_not_break_the_row(self) -> None:
        assert flatten_table([["Gross" + chr(10) + "margin", "78%"]]) == [
            "Gross margin: 78%"
        ]


class TestLegacyPpt:
    def test_router_dispatches_on_the_ole2_header(self, tmp_path: Path) -> None:
        """A .ppt is an OLE2 compound file; the router must recognize it by bytes."""
        legacy = tmp_path / "deck.ppt"
        legacy.write_bytes(OLE2_MAGIC + b"whatever follows")
        with pytest.raises(IngestError):
            load_deck(legacy, want_images=False)

    def test_missing_libreoffice_names_the_install_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec 11: the .ppt failure has to be actionable, not a stack trace."""
        monkeypatch.setattr("deckpager.ingest.legacy_ppt.find_soffice", lambda: None)
        legacy = tmp_path / "deck.ppt"
        legacy.write_bytes(OLE2_MAGIC + b"whatever follows")
        with pytest.raises(IngestError, match="LibreOffice is required") as caught:
            load_ppt(legacy, want_images=False)
        assert install_hint() in str(caught.value)

    def test_an_ole2_file_without_a_ppt_extension_is_refused(self, tmp_path: Path) -> None:
        """.xls and .doc share the header, so the extension has to agree."""
        book = tmp_path / "model.xls"
        book.write_bytes(OLE2_MAGIC + b"whatever follows")
        with pytest.raises(UnsupportedFormatError, match="legacy Office document"):
            load_deck(book, want_images=False)

class TestNormalizeText:
    def test_collapses_horizontal_whitespace(self) -> None:
        assert normalize_text("Series   A\t\tround") == "Series A round"

    def test_preserves_line_structure(self) -> None:
        assert normalize_text("Title\n\nBody") == "Title\n\nBody"

    def test_collapses_long_blank_runs(self) -> None:
        assert normalize_text("A\n\n\n\n\nB") == "A\n\nB"
