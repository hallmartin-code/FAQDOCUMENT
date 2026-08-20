"""The format-neutral deck model every ingestion path produces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Horizontal whitespace, including the non-breaking / figure spaces PDF extraction emits.
_WS = re.compile("[ \t    ]+")
_BLANKS = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Collapse horizontal whitespace and runs of blank lines, preserving line structure.

    Line structure survives because grounding compares the model's quotes against this
    text, and extraction artifacts are mostly horizontal (double spaces, tabs, NBSP).
    """
    lines = [_WS.sub(" ", line).strip() for line in raw.replace("\r\n", "\n").split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


#: A slide yielding fewer characters than this is image-dominant: whatever it says, it
#: says with pixels. Spec §7 fixes the threshold at 20.
IMAGE_DOMINANT_TEXT_CHARS = 20


class SlideAsset(BaseModel):
    """A rasterized slide image, ready to be sent as an image content block."""

    media_type: Literal["image/png", "image/jpeg"] = "image/jpeg"
    data_b64: str

    @property
    def byte_size(self) -> int:
        """Approximate decoded size of the image in bytes."""
        return (len(self.data_b64) * 3) // 4


class Slide(BaseModel):
    """One slide or page of the deck."""

    index: int = Field(ge=1, description="1-based; this is the 'Slide N' the model must cite.")
    title: str | None = None
    text: str = ""
    speaker_notes: str | None = None
    asset: SlideAsset | None = None
    has_chart: bool = Field(
        default=False,
        description=(
            "Whether the slide carries a chart. Exact for PPTX, where python-pptx knows; "
            "a heuristic for PDF, where nothing in the format says so."
        ),
    )

    @property
    def image_b64(self) -> str | None:
        """The slide image as base64, or None. The spec §7 view of `asset`."""
        return self.asset.data_b64 if self.asset is not None else None

    @property
    def text_density(self) -> int:
        """Character count of body text plus notes — used to rank slides under the image cap."""
        return len(self.text) + len(self.speaker_notes or "")

    @property
    def image_dominant(self) -> bool:
        """Whether this slide's content is carried by the image rather than the text.

        Speaker notes are excluded deliberately: a slide can be a full-bleed diagram with a
        page of notes behind it, and it is still a slide the model has to *look* at.
        """
        return len(self.text) < IMAGE_DOMINANT_TEXT_CHARS


class Deck(BaseModel):
    """A pitch deck, normalized across source formats."""

    source_path: Path
    source_format: Literal["pdf", "pptx", "ppt"]
    slides: list[Slide] = Field(default_factory=list)
    raw_pdf_b64: str | None = Field(
        default=None,
        description="Set only for the native-PDF fast path; None when falling back to images.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Ingestion warnings; copied verbatim into Assessment.meta.ingest_warnings.",
    )

    @property
    def slide_count(self) -> int:
        """Number of slides extracted from the source."""
        return len(self.slides)

    @property
    def image_bytes(self) -> int:
        """Total approximate decoded size of all attached slide images."""
        return sum(s.asset.byte_size for s in self.slides if s.asset is not None)

    @property
    def image_dominant_slides(self) -> list[int]:
        """1-based indices of the slides that must be seen, not just read."""
        return [s.index for s in self.slides if s.image_dominant]
