"""Format dispatch and request-budget enforcement."""

from __future__ import annotations

import zipfile
from pathlib import Path

from deckpager.errors import IngestError, UnsupportedFormatError
from deckpager.ingest.docx import load_docx
from deckpager.ingest.legacy_ppt import load_ppt
from deckpager.ingest.models import Deck, Slide
from deckpager.ingest.pdf import load_pdf
from deckpager.ingest.pptx import load_pptx

#: File types the ingest layer accepts. Exported so the CLI, the error messages below, and
#: any future upload gate all state the same set — three hardcoded copies would drift.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".pdf", ".pptx", ".ppt", ".docx"})

#: Spec §7 budgets: at most 40 slides in a request, images for the first 25 of them.
DEFAULT_MAX_SLIDES = 40
DEFAULT_MAX_IMAGE_SLIDES = 25
DEFAULT_MAX_IMAGE_BYTES = 5_000_000

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"
#: Legacy .ppt is an OLE2 compound file, the same container Word 97 and .xls use.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _sniff(path: Path) -> str:
    """Identify the file from its magic bytes, ignoring the extension."""
    try:
        head = path.open("rb").read(8)
    except OSError as exc:
        raise IngestError(f"Could not read {path}: {exc}") from exc
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return _sniff_zip(path)
    if head.startswith(OLE2_MAGIC):
        return "ppt"
    return "unknown"


def _sniff_zip(path: Path) -> str:
    """Tell an OOXML container apart by what is inside it.

    PPTX and DOCX are both ZIPs with identical magic bytes, so the header alone cannot
    choose between them — the part names can.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return "unknown"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "word/document.xml" in names:
        return "docx"
    return "unknown"


def _summarize(indices: list[int], limit: int = 12) -> str:
    """Render a slide-index list compactly for a warning message."""
    shown = ", ".join(str(i) for i in indices[:limit])
    return shown if len(indices) <= limit else f"{shown}, +{len(indices) - limit} more"


def _shed_order(slides: list[Slide]) -> list[Slide]:
    """Slides ordered by how little it costs to drop their image.

    Image-dominant slides sort last. They are the ones whose text is empty — dropping their
    image sends the model a blank slide, which is worse than sending nothing at all. The
    inherited ordering was text-density ascending, which put those slides *first*.
    """
    return sorted(slides, key=lambda s: (s.image_dominant, s.text_density, -s.index))


def apply_caps(
    deck: Deck,
    *,
    max_slides: int = DEFAULT_MAX_SLIDES,
    max_image_slides: int = DEFAULT_MAX_IMAGE_SLIDES,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> Deck:
    """Enforce the per-request slide and image budgets, in place.

    Slide text is never discarded — spec §7 keeps text for every slide however long the
    deck is, and rations only the images. Images are kept for the first `max_image_slides`
    slides plus every image-dominant slide anywhere in the deck, because a slide with no
    text is a slide the model can only read by looking at it. Anything the byte budget
    still cannot afford is shed cheapest-first, and every drop is recorded in
    `deck.warnings`.
    """
    if deck.slide_count > max_slides:
        deck.warnings.append(
            f"Deck has {deck.slide_count} slides, above the {max_slides}-slide request cap; "
            f"all slide text is still sent, but only the first {max_image_slides} slides "
            f"and any image-dominant slide can carry an image."
        )

    with_images = [s for s in deck.slides if s.asset is not None]
    if not with_images:
        _warn_about_blind_slides(deck)
        return deck

    dropped: list[int] = []

    # Rung 1: the positional rule. Late slides are appendices far more often than they are
    # the thesis; an image-dominant slide is exempt wherever it sits.
    for slide in with_images:
        if slide.index > max_image_slides and not slide.image_dominant:
            slide.asset = None
            dropped.append(slide.index)

    # Rung 2: the byte budget, shed cheapest-first.
    if deck.image_bytes > max_image_bytes:
        for slide in _shed_order([s for s in deck.slides if s.asset is not None]):
            if deck.image_bytes <= max_image_bytes:
                break
            slide.asset = None
            dropped.append(slide.index)

    if dropped:
        kept = sum(1 for s in deck.slides if s.asset is not None)
        deck.warnings.append(
            f"Image budget applied ({max_image_bytes / 1_000_000:.1f} MB, images for slides "
            f"1-{max_image_slides} plus image-dominant slides); dropped images from "
            f"{len(dropped)} slide(s): {_summarize(sorted(dropped))}. "
            f"{kept} slide image(s) retained."
        )
    _warn_about_blind_slides(deck)
    return deck


def _warn_about_blind_slides(deck: Deck) -> None:
    """Say so when a slide the model can only read as a picture has no picture.

    Spec 7 requires an image-dominant slide to reach the model as an image. A PDF sent
    natively satisfies that by sending the whole file. A PPTX ingested without
    LibreOffice does not: those slides arrive as an empty string, and the model has
    nothing to go on. Silence there would look like a deck that simply said nothing.
    """
    if deck.raw_pdf_b64 is not None:
        return
    blind = [s.index for s in deck.slides if s.image_dominant and s.asset is None]
    if not blind:
        return
    deck.warnings.append(
        f"{len(blind)} slide(s) carry no extractable text and no image: "
        f"{_summarize(blind)}. The model will have nothing to read for them."
    )


def load_deck(
    path: Path,
    *,
    want_images: bool = True,
    max_slides: int = DEFAULT_MAX_SLIDES,
    max_image_slides: int = DEFAULT_MAX_IMAGE_SLIDES,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
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
            f"{path.name} is not a PDF, PPTX, PPT, or DOCX (unrecognized file header). "
            f"Supported formats: {_supported()}"
        )
    if suffix in SUPPORTED_SUFFIXES and _expected_magic(suffix) != sniffed:
        raise UnsupportedFormatError(
            f"{path.name} has a {suffix} extension but its contents are {sniffed.upper()}. "
            f"Rename or re-export the file so the extension matches."
        )
    if suffix not in SUPPORTED_SUFFIXES and sniffed in ("pptx", "ppt", "docx"):
        # An OLE2 header stays ambiguous — .xls and .doc share it with .ppt. The OOXML
        # containers no longer are: _sniff_zip has read the part names.
        container = "Office document" if sniffed != "ppt" else "legacy Office document"
        raise UnsupportedFormatError(
            f"{path.name} looks like a {container}. Supported formats: {_supported()}"
        )

    loaders = {"pdf": load_pdf, "pptx": load_pptx, "ppt": load_ppt, "docx": load_docx}
    deck = loaders[sniffed](path, want_images=want_images)
    return apply_caps(
        deck,
        max_slides=max_slides,
        max_image_slides=max_image_slides,
        max_image_bytes=max_image_bytes,
    )


def _supported() -> str:
    """The accepted suffixes, for an error message."""
    return ", ".join(sorted(SUPPORTED_SUFFIXES))


def _expected_magic(suffix: str) -> str:
    """Map a supported suffix to the format its magic bytes should report."""
    return {".pdf": "pdf", ".pptx": "pptx", ".ppt": "ppt", ".docx": "docx"}[suffix]
