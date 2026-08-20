"""Native-PDF ingestion.

Preferred path: hand the whole file to the API as a `document` content block so layout,
charts, and figures survive. Local extraction runs alongside it regardless, because
`grounding.py` needs per-slide text to verify the model's slide citations, and the dry-run
summary needs it to say anything useful about the deck.

Two libraries, deliberately. pdfplumber reads text and tables — it keeps reading order and
recovers a table as rows rather than as jumbled words. PyMuPDF rasterizes pages and reports
vector drawing counts, which pdfplumber does not do as well. The cost is that the file is
parsed twice; that is a few hundred milliseconds against a model call measured in seconds,
and it buys the table structure the spec (§4) asks for by name.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pdfplumber
import pymupdf  # PyMuPDF; the legacy `fitz` alias is deprecated as of 1.28

from deckpager.errors import IngestError
from deckpager.ingest.models import Deck, Slide, SlideAsset, normalize_text

#: Rasterization density for the image fallback path (spec §7).
RASTER_DPI = 120
#: JPEG quality for rasterized pages (spec §7).
RASTER_JPEG_QUALITY = 80
#: Hard ceiling on a rasterized page's long edge, in pixels. Above roughly this width the
#: API downsamples anyway, so larger renders only cost bytes against the image budget.
MAX_IMAGE_LONG_EDGE = 1568
#: Above this many pages the deck is not sent as a native PDF document block.
MAX_NATIVE_PDF_PAGES = 100
#: Above this raw size the base64-encoded document block would approach the request limit.
MAX_NATIVE_PDF_BYTES = 20_000_000

#: Vector drawing operations at or above which a page is called charted. Nothing in the
#: PDF format declares "this is a chart", so this is a heuristic, calibrated against a real
#: deck rather than guessed: prose pages there ran 2-6 paths, pages carrying a diagram or
#: a bar chart ran 11-26, and the synthetic chart fixture runs 24-60. Fifteen sits in the
#: gap with margin on both sides. It colours the prompt and the dry-run summary; nothing
#: depends on it being right.
CHART_DRAWING_PATHS = 15


def first_line_title(text: str) -> str | None:
    """Take the first non-empty line as a title. Shared with the PPTX path."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def render_page_image(page: pymupdf.Page, dpi: int = RASTER_DPI) -> bytes:
    """Rasterize a single page to JPEG bytes at `dpi`, clamped to MAX_IMAGE_LONG_EDGE."""
    zoom = dpi / 72.0
    long_edge_pt = max(page.rect.width, page.rect.height)
    if long_edge_pt > 0:
        zoom = min(zoom, MAX_IMAGE_LONG_EDGE / long_edge_pt)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return bytes(pixmap.tobytes("jpeg", jpg_quality=RASTER_JPEG_QUALITY))


def flatten_table(rows: list[list[str | None]]) -> list[str]:
    """Render an extracted table as one line per row: `label: a | b | c` (spec §7).

    The first cell becomes the row label because that is where pitch decks put it — the
    metric, the year, the competitor name. Empty rows and empty cells are dropped rather
    than rendered as a run of pipes.
    """
    lines: list[str] = []
    for row in rows:
        cells = [(cell or "").strip().replace("\n", " ") for cell in row]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        lines.append(cells[0] if len(cells) == 1 else f"{cells[0]}: {' | '.join(cells[1:])}")
    return lines


def page_text(page: pdfplumber.page.Page) -> str:
    """Text for one page, with any tables restated in row form beneath it.

    The table rows do duplicate words already present in the running text. That is the
    point: `extract_text` flattens a table into reading order and loses which number
    belonged to which column, so the structured restatement is what makes a traction or
    competitor table legible to the model.
    """
    body = page.extract_text() or ""
    table_lines: list[str] = []
    for table in page.extract_tables():
        table_lines.extend(flatten_table(table))
    if table_lines:
        body = f"{body}\n[table]\n" + "\n".join(table_lines)
    return normalize_text(body)


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

        try:
            plumber = pdfplumber.open(path)
        except Exception as exc:  # pdfplumber surfaces pdfminer's assorted parse errors
            raise IngestError(f"{path.name} could not be parsed for text: {exc}") from exc

        slides: list[Slide] = []
        with plumber:
            # Indexed rather than `enumerate(document)`: PyMuPDF's Document is iterable at
            # runtime but its stubs do not declare it, and the loop variable lands as
            # `Never`.
            for number in range(1, document.page_count + 1):
                page = document[number - 1]
                text = page_text(plumber.pages[number - 1])
                asset: SlideAsset | None = None
                if want_images and not native_ok:
                    image = render_page_image(page)
                    asset = SlideAsset(
                        media_type="image/jpeg",
                        data_b64=base64.standard_b64encode(image).decode("ascii"),
                    )
                slides.append(
                    Slide(
                        index=number,
                        title=first_line_title(text),
                        text=text,
                        speaker_notes=None,
                        asset=asset,
                        has_chart=len(page.get_drawings()) >= CHART_DRAWING_PATHS,
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
