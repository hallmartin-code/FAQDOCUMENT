"""Ingestion tests: both format paths, the router, and the caps logic."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from pitchlens.errors import IngestError, UnsupportedFormatError
from pitchlens.ingest.models import Deck, Slide, SlideAsset, normalize_text
from pitchlens.ingest.pdf import load_pdf
from pitchlens.ingest.pptx import find_soffice, load_pptx
from pitchlens.ingest.router import apply_caps, load_deck


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
        assert all(s.notes is None for s in deck.slides)

    def test_falls_back_to_rasters_when_over_the_native_limits(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pitchlens.ingest.pdf.MAX_NATIVE_PDF_PAGES", 2)
        deck = load_pdf(sample_pdf, want_images=True)
        assert deck.raw_pdf_b64 is None
        assert all(s.asset is not None for s in deck.slides)
        assert deck.image_bytes > 0
        assert any("falling back to rasterized page images" in w for w in deck.warnings)

    def test_fallback_respects_no_images(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pitchlens.ingest.pdf.MAX_NATIVE_PDF_PAGES", 2)
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
        assert deck.slides[0].notes is not None
        assert "Speaker notes for slide 1" in deck.slides[0].notes

    def test_warns_visibly_when_libreoffice_is_missing(
        self, sample_pptx: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pitchlens.ingest.pptx.find_soffice", lambda: None)
        deck = load_pptx(sample_pptx, want_images=True)
        assert any("LibreOffice" in w for w in deck.warnings)
        assert all(s.asset is None for s in deck.slides)

    def test_no_images_flag_skips_conversion_entirely(
        self, sample_pptx: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> str:
            raise AssertionError("soffice must not be probed when want_images is False")

        monkeypatch.setattr("pitchlens.ingest.pptx.find_soffice", _boom)
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
        with pytest.raises(UnsupportedFormatError, match="neither a PDF nor a PPTX"):
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
        apply_caps(deck, max_slides=60, max_image_bytes=5_000_000)
        assert all(s.asset is not None for s in deck.slides)
        assert deck.warnings == []

    def test_image_budget_drops_least_text_dense_first(self) -> None:
        slides = [
            Slide(index=1, text="a" * 10, asset=_fake_asset(900)),  # sparsest -> drops first
            Slide(index=2, text="a" * 500, asset=_fake_asset(900)),
            Slide(index=3, text="a" * 1000, asset=_fake_asset(900)),
        ]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=60, max_image_bytes=2_000_000)
        assert deck.slides[0].asset is None
        assert deck.slides[1].asset is not None
        assert deck.slides[2].asset is not None
        assert deck.image_bytes <= 2_000_000

    def test_slide_cap_limits_how_many_slides_carry_images(self) -> None:
        slides = [Slide(index=i, text="a" * (i * 10), asset=_fake_asset(1)) for i in range(1, 11)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=4, max_image_bytes=5_000_000)
        assert sum(1 for s in deck.slides if s.asset is not None) == 4
        # The four densest slides keep their images.
        assert [s.index for s in deck.slides if s.asset is not None] == [7, 8, 9, 10]

    def test_slide_text_is_never_dropped(self) -> None:
        slides = [Slide(index=i, text=f"slide {i}", asset=_fake_asset(1)) for i in range(1, 11)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=2, max_image_bytes=1)
        assert deck.slide_count == 10
        assert all(s.text for s in deck.slides)

    def test_drops_are_never_silent(self) -> None:
        slides = [Slide(index=i, text="a" * i, asset=_fake_asset(900)) for i in range(1, 11)]
        deck = self._deck(slides)
        apply_caps(deck, max_slides=60, max_image_bytes=1_000_000)
        assert any("dropped images from" in w for w in deck.warnings)
        assert any("slides" in w for w in deck.warnings)


class TestNormalizeText:
    def test_collapses_horizontal_whitespace(self) -> None:
        assert normalize_text("Series   A\t\tround") == "Series A round"

    def test_preserves_line_structure(self) -> None:
        assert normalize_text("Title\n\nBody") == "Title\n\nBody"

    def test_collapses_long_blank_runs(self) -> None:
        assert normalize_text("A\n\n\n\n\nB") == "A\n\nB"
