"""Native-PDF ingestion.

Preferred path: hand the whole file to the API as a `document` content block so layout,
charts, and figures survive. PyMuPDF text extraction runs alongside it regardless, because
`grounding.py` needs local per-slide text to verify the model's slide citations.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf  # PyMuPDF; the legacy `fitz` alias is deprecated as of 1.28

from pitchlens.errors import IngestError
from pitchlens.ingest.models import Deck, Slide, SlideAsset, normalize_text

#: Rasterization density for the image fallback path.
RASTER_DPI = 150
#: Hard ceiling on a rasterized page's long edge, in pixels. Above roughly this width the
#: API downsamples anyway, so larger renders only cost bytes against the image budget.
MAX_IMAGE_LONG_EDGE = 1568
#: Above this many pages the deck is not sent as a native PDF document block.
MAX_NATIVE_PDF_PAGES = 100
#: Above this raw size the base64-encoded document block would approach the request limit.
MAX_NATIVE_PDF_BYTES = 20_000_000


def first_line_title(text: str) -> str | None:
    """Take the first non-empty line as a title. Shared with the PPTX path."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def render_page_png(page: pymupdf.Page, dpi: int = RASTER_DPI) -> bytes:
    """Rasterize a single page to PNG bytes at `dpi`, clamped to MAX_IMAGE_LONG_EDGE."""
    zoom = dpi / 72.0
    long_edge_pt = max(page.rect.width, page.rect.height)
    if long_edge_pt > 0:
        zoom = min(zoom, MAX_IMAGE_LONG_EDGE / long_edge_pt)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return bytes(pixmap.tobytes("png"))


def load_pdf(path: Path, *, want_images: bool) -> Deck:
    """Read a PDF into a Deck.

    Sends the file natively unless it exceeds the API's page/size limits, in which case
    it degrades to rasterized page images and records a warning.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IngestError(f"Could not read {path}: {exc}") from exc

    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises bare exceptions for malformed files
        raise IngestError(f"{path.name} is not a readable PDF: {exc}") from exc

    warnings: list[str] = []
    with document:
        if document.page_count == 0:
            raise IngestError(f"{path.name} contains no pages.")
        if document.needs_pass:
            raise IngestError(f"{path.name} is password-protected; decrypt it before analysis.")

        native_ok = document.page_count <= MAX_NATIVE_PDF_PAGES and len(raw) <= MAX_NATIVE_PDF_BYTES
        if not native_ok:
            warnings.append(
                f"PDF exceeds the native document limits "
                f"({document.page_count} pages, {len(raw) / 1_000_000:.1f} MB); "
                f"falling back to rasterized page images."
            )

        slides: list[Slide] = []
        # Indexed rather than `enumerate(document)`: PyMuPDF's Document is iterable at
        # runtime but its stubs do not declare it, and the loop variable lands as `Never`.
        for number in range(1, document.page_count + 1):
            page = document[number - 1]
            text = normalize_text(page.get_text("text"))
            asset: SlideAsset | None = None
            if want_images and not native_ok:
                png = render_page_png(page)
                asset = SlideAsset(data_b64=base64.standard_b64encode(png).decode("ascii"))
            slides.append(
                Slide(
                    index=number,
                    title=first_line_title(text),
                    text=text,
                    notes=None,
                    asset=asset,
                )
            )

    if not any(s.text for s in slides):
        warnings.append(
            "No extractable text found — the deck is likely scanned images. "
            "Slide citations cannot be verified against local text."
        )

    return Deck(
        source_path=path,
        source_format="pdf",
        slides=slides,
        raw_pdf_b64=base64.standard_b64encode(raw).decode("ascii") if native_ok else None,
        warnings=warnings,
    )
