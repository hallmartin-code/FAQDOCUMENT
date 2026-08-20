"""PPTX ingestion.

Text and speaker notes come from python-pptx. Visuals require a LibreOffice conversion to
PDF followed by PyMuPDF rasterization; when `soffice` is absent we degrade to text-only and
say so loudly, because chart-heavy slides will be under-read.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

import pymupdf  # PyMuPDF; the legacy `fitz` alias is deprecated as of 1.28
from pptx import Presentation
from pptx.shapes.base import BaseShape

from deckpager.errors import IngestError
from deckpager.ingest.legacy_ppt import convert, find_soffice
from deckpager.ingest.models import Deck, Slide, SlideAsset, normalize_text
from deckpager.ingest.pdf import first_line_title, flatten_table, render_page_image


def _shape_text(shape: BaseShape) -> list[str]:
    """Pull text out of a shape, descending into groups and tables."""
    parts: list[str] = []
    if shape.shape_type is not None and shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
        for member in shape.shapes:  # type: ignore[attr-defined]
            parts.extend(_shape_text(member))
        return parts
    if getattr(shape, "has_table", False):
        rows: list[list[str | None]] = [
            [cell.text for cell in row.cells]
            for row in shape.table.rows  # type: ignore[attr-defined]
        ]
        parts.extend(flatten_table(rows))
        return parts
    if shape.has_text_frame and shape.text_frame.text.strip():  # type: ignore[attr-defined]
        parts.append(shape.text_frame.text)  # type: ignore[attr-defined]
    return parts


def _has_chart(shape: BaseShape) -> bool:
    """Whether a shape is a chart, or contains one.

    Exact, unlike the PDF path: a PPTX chart is a first-class object and python-pptx
    says so. Charts pasted in as pictures are not detected, and cannot be.
    """
    if getattr(shape, "has_chart", False):
        return True
    if shape.shape_type is not None and shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
        return any(_has_chart(m) for m in shape.shapes)  # type: ignore[attr-defined]
    return False


def _rasterize_via_libreoffice(path: Path, soffice: str) -> list[bytes]:
    """Convert the PPTX to PDF with LibreOffice, then rasterize each page to PNG."""
    with tempfile.TemporaryDirectory(prefix="deckpager-pptx-") as tmp:
        produced = convert(path, "pdf", Path(tmp), soffice)
        with pymupdf.open(produced) as document:
            return [render_page_image(page) for page in document]


def load_pptx(path: Path, *, want_images: bool) -> Deck:
    """Read a PPTX into a Deck, rasterizing slides when LibreOffice is available."""
    try:
        presentation = Presentation(str(path))
    except Exception as exc:  # python-pptx raises package-specific errors for bad files
        raise IngestError(f"{path.name} is not a readable PPTX: {exc}") from exc

    warnings: list[str] = []
    slides: list[Slide] = []
    for number, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        charted = False
        for shape in slide.shapes:
            parts.extend(_shape_text(shape))
            charted = charted or _has_chart(shape)
        body = normalize_text("\n".join(parts))

        # Prefer the title placeholder; many real decks use plain text boxes on blank
        # layouts, so fall back to the first line the same way the PDF path does.
        title_shape = slide.shapes.title
        title = title_shape.text_frame.text.strip()[:200] if title_shape is not None else ""
        if not title:
            title = first_line_title(body) or ""

        notes: str | None = None
        if slide.has_notes_slide:
            raw_notes = slide.notes_slide.notes_text_frame.text
            notes = normalize_text(raw_notes) or None

        slides.append(
            Slide(
                index=number,
                title=title or None,
                text=body,
                speaker_notes=notes,
                asset=None,
                has_chart=charted,
            )
        )

    if not slides:
        raise IngestError(f"{path.name} contains no slides.")

    if want_images:
        soffice = find_soffice()
        if soffice is None:
            warnings.append(
                "LibreOffice (soffice) not found on PATH — slides were not rasterized. "
                "Chart-heavy and image-only slides may be under-read; install LibreOffice "
                "or pass --no-images to silence this."
            )
        else:
            try:
                pages = _rasterize_via_libreoffice(path, soffice)
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
                warnings.append(
                    f"Slide rasterization failed ({exc}); analysis proceeds on text only "
                    f"and chart-heavy slides may be under-read."
                )
            else:
                if len(pages) != len(slides):
                    warnings.append(
                        f"LibreOffice produced {len(pages)} pages for {len(slides)} slides; "
                        f"images were matched positionally and may be offset."
                    )
                for slide_model, png in zip(slides, pages, strict=False):
                    slide_model.asset = SlideAsset(
                        media_type="image/jpeg",
                        data_b64=base64.standard_b64encode(png).decode("ascii"),
                    )

    return Deck(
        source_path=path,
        source_format="pptx",
        slides=slides,
        raw_pdf_b64=None,
        warnings=warnings,
    )
