"""Format dispatch and request-budget enforcement."""

from __future__ import annotations

from pathlib import Path

from deckpager.errors import IngestError, UnsupportedFormatError
from deckpager.ingest.models import Deck
from deckpager.ingest.pdf import load_pdf
from deckpager.ingest.pptx import load_pptx

#: File types the ingest layer accepts. Exported so the CLI, the web upload gate, and the
#: error messages below all state the same set — three hardcoded copies would drift.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".pdf", ".pptx"})

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"


def _sniff(path: Path) -> str:
    """Identify the file from its magic bytes, ignoring the extension."""
    try:
        head = path.open("rb").read(8)
    except OSError as exc:
        raise IngestError(f"Could not read {path}: {exc}") from exc
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "pptx"
    return "unknown"


def _summarize(indices: list[int], limit: int = 12) -> str:
    """Render a slide-index list compactly for a warning message."""
    shown = ", ".join(str(i) for i in indices[:limit])
    return shown if len(indices) <= limit else f"{shown}, +{len(indices) - limit} more"


def apply_caps(deck: Deck, *, max_slides: int, max_image_bytes: int) -> Deck:
    """Enforce the per-request slide and image budgets, in place.

    Slide text is never discarded. When the budgets are exceeded, images are dropped from
    the least text-dense slides first, and every drop is recorded in `deck.warnings`.
    """
    if deck.slide_count > max_slides:
        deck.warnings.append(
            f"Deck has {deck.slide_count} slides, above the {max_slides}-slide request cap; "
            f"all slide text is still sent, but at most {max_slides} slides can carry images."
        )

    with_images = [s for s in deck.slides if s.asset is not None]
    if not with_images:
        return deck

    # Least text-dense first, then highest slide index, so ties drop from the back.
    order = sorted(with_images, key=lambda s: (s.text_density, -s.index))
    dropped: list[int] = []

    excess = len(with_images) - max_slides
    for slide in order[: max(excess, 0)]:
        slide.asset = None
        dropped.append(slide.index)

    if deck.image_bytes > max_image_bytes:
        for slide in order:
            if deck.image_bytes <= max_image_bytes:
                break
            if slide.asset is None:
                continue
            slide.asset = None
            dropped.append(slide.index)

    if dropped:
        kept = sum(1 for s in deck.slides if s.asset is not None)
        deck.warnings.append(
            f"Image budget exceeded ({max_image_bytes / 1_000_000:.1f} MB / {max_slides} slides); "
            f"dropped images from {len(dropped)} slide(s): {_summarize(sorted(dropped))}. "
            f"{kept} slide image(s) retained."
        )
    return deck


def load_deck(
    path: Path,
    *,
    want_images: bool = True,
    max_slides: int = 60,
    max_image_bytes: int = 5_000_000,
) -> Deck:
    """Load a deck from disk, dispatching on suffix and magic bytes."""
    if not path.exists():
        raise IngestError(f"No such file: {path}")
    if not path.is_file():
        raise IngestError(f"Not a file: {path}")

    suffix = path.suffix.lower()
    sniffed = _sniff(path)

    if sniffed == "unknown":
        raise UnsupportedFormatError(
            f"{path.name} is neither a PDF nor a PPTX (unrecognized file header). "
            f"Supported formats: {_supported()}"
        )
    if suffix in SUPPORTED_SUFFIXES and _expected_magic(suffix) != sniffed:
        raise UnsupportedFormatError(
            f"{path.name} has a {suffix} extension but its contents are {sniffed.upper()}. "
            f"Rename or re-export the file so the extension matches."
        )
    if suffix not in SUPPORTED_SUFFIXES and sniffed == "pptx":
        # A bare zip header is ambiguous; only trust it when the extension agrees.
        raise UnsupportedFormatError(
            f"{path.name} looks like a ZIP archive. Supported formats: {_supported()}"
        )

    deck = (
        load_pdf(path, want_images=want_images)
        if sniffed == "pdf"
        else load_pptx(path, want_images=want_images)
    )
    return apply_caps(deck, max_slides=max_slides, max_image_bytes=max_image_bytes)


def _supported() -> str:
    """The accepted suffixes, for an error message."""
    return ", ".join(sorted(SUPPORTED_SUFFIXES))


def _expected_magic(suffix: str) -> str:
    """Map a supported suffix to the format its magic bytes should report."""
    return "pdf" if suffix == ".pdf" else "pptx"
