"""The investor FAQ, rendered to PDF with ReportLab.

The one-pager it replaces was a fixed grid on a single page, and every hard problem in it
came from that constraint. Twenty questions and answers are a flowing document instead, so
this is Platypus: the content decides the page count, and nothing is truncated to make it
fit. What was a layout problem becomes an editorial one — every answer is printed in full,
and a document that says little produces a short FAQ rather than a padded one.

Two things carry over unchanged, because they are the point of the product rather than
decoration:

* Provenance under every answer. A reader can take any sentence back to the slide it came
  from, and an answer below the confidence threshold is marked.
* Silence is printed. A question the document does not address says so in the reader's
  face, and is collected again at the end as the diligence list.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
)

from deckpager.errors import RenderError
from deckpager.models import (
    DEFAULT_MIN_CONFIDENCE,
    UNANSWERED,
    Faq,
    FaqEntry,
)
from deckpager.render import style as s
from deckpager.render.base import Paper

#: The page sizes the CLI will accept, named once so the flag and the renderer agree.
PAGE_SIZES: dict[str, tuple[float, float]] = {"letter": LETTER, "a4": A4}

#: Page furniture. The footer follows the TEN Capital house standard: one centred line
#: carrying the document title, the page number, the compilation credit, and the mark.
FOOTER_HEIGHT = 30.0
TITLE = "Investor FAQ"


def _page_size(paper: Paper) -> tuple[float, float]:
    try:
        return PAGE_SIZES[paper]
    except KeyError:
        raise RenderError(
            f"Unknown paper {paper!r}. Choose one of: {', '.join(sorted(PAGE_SIZES))}."
        ) from None


def _styles() -> dict[str, ParagraphStyle]:
    """Every paragraph style, derived from the shared palette rather than hard-coded."""
    return {
        "company": ParagraphStyle(
            "company",
            fontName=s.SERIF_BOLD,
            fontSize=s.SIZE_COMPANY,
            leading=s.SIZE_COMPANY * 1.15,
            textColor=s.INK,
        ),
        "tagline": ParagraphStyle(
            "tagline",
            fontName=s.SANS,
            fontSize=s.SIZE_TAGLINE,
            leading=s.SIZE_TAGLINE * 1.35,
            textColor=s.MUTED,
            spaceAfter=4,
        ),
        "chips": ParagraphStyle(
            "chips",
            fontName=s.SANS,
            fontSize=s.SIZE_CHIP,
            leading=s.SIZE_CHIP * 1.4,
            textColor=s.ACCENT,
            spaceAfter=10,
        ),
        "coverage": ParagraphStyle(
            "coverage",
            fontName=s.SANS_BOLD,
            fontSize=s.SIZE_BODY,
            leading=s.SIZE_BODY * 1.5,
            textColor=s.INK,
            backColor=s.TINT_ASK,
            borderPadding=(6, 8, 6, 8),
            spaceAfter=16,
        ),
        "stage": ParagraphStyle(
            "stage",
            fontName=s.SANS_BOLD,
            fontSize=s.SIZE_LABEL + 1,
            leading=(s.SIZE_LABEL + 1) * 1.4,
            textColor=s.ACCENT,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "question": ParagraphStyle(
            "question",
            fontName=s.SERIF_BOLD,
            fontSize=s.SIZE_HEADING,
            leading=s.SIZE_HEADING * 1.3,
            textColor=s.INK,
            spaceAfter=3,
        ),
        "answer": ParagraphStyle(
            "answer",
            fontName=s.SANS,
            fontSize=s.SIZE_BODY + 0.5,
            leading=(s.SIZE_BODY + 0.5) * s.LEADING * 1.15,
            textColor=s.INK,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "unanswered": ParagraphStyle(
            "unanswered",
            fontName=s.SANS_ITALIC,
            fontSize=s.SIZE_BODY + 0.5,
            leading=(s.SIZE_BODY + 0.5) * s.LEADING * 1.15,
            textColor=s.MUTED,
            spaceAfter=2,
        ),
        "provenance": ParagraphStyle(
            "provenance",
            fontName=s.SANS,
            fontSize=s.SIZE_LABEL,
            leading=s.SIZE_LABEL * 1.6,
            textColor=s.MUTED,
            spaceAfter=10,
        ),
        "note": ParagraphStyle(
            "note",
            fontName=s.SANS_ITALIC,
            fontSize=s.SIZE_LABEL,
            leading=s.SIZE_LABEL * 1.6,
            textColor=s.MUTED,
            spaceAfter=10,
        ),
        "open": ParagraphStyle(
            "open",
            fontName=s.SANS,
            fontSize=s.SIZE_BODY,
            leading=s.SIZE_BODY * 1.5,
            textColor=s.INK,
            leftIndent=10,
            spaceAfter=3,
        ),
        "closing": ParagraphStyle(
            "closing",
            fontName=s.SANS_ITALIC,
            fontSize=s.SIZE_LABEL,
            leading=s.SIZE_LABEL * 1.6,
            textColor=s.MUTED,
            spaceBefore=8,
        ),
    }


def escape(text: str) -> str:
    """Escape the three characters ReportLab reads as markup in a Paragraph."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def citation(entry: FaqEntry, threshold: float) -> str:
    """The provenance line under an answer."""
    slides = entry.answer.source_slides
    if not slides:
        return ""
    label = "Slide" if len(slides) == 1 else "Slides"
    line = f"{label} {', '.join(str(n) for n in sorted(slides))}"
    if entry.answer.is_low_confidence(threshold):
        line += f"  {s.DAGGER} low confidence"
    if entry.answer.note:
        line += f"  ·  {escape(entry.answer.note)}"
    return line


class FaqRenderer:
    """The ReportLab engine. Pure Python, so it runs wherever deckpager installs."""

    name = "reportlab"

    def preflight(self) -> list[str]:
        """Problems that would stop this engine; empty when usable."""
        problems: list[str] = []
        if not s.LOGO_PATH.is_file():
            problems.append(
                f"The TEN Capital mark is missing from {s.LOGO_PATH}. The FAQ still "
                f"renders; its footer will carry the wordmark without the logo."
            )
        return problems

    def page_count(self, document: Path) -> int:
        """Count the pages of a rendered document."""
        from pypdf import PdfReader

        return len(PdfReader(str(document)).pages)

    def render(
        self,
        faq: Faq,
        destination: Path,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        """Write the FAQ. The document is as long as the answers require."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        width, height = _page_size(paper)
        styles = _styles()

        doc = BaseDocTemplate(
            str(destination),
            pagesize=(width, height),
            leftMargin=s.MARGIN,
            rightMargin=s.MARGIN,
            topMargin=s.MARGIN,
            bottomMargin=s.MARGIN + FOOTER_HEIGHT,
            title=f"{self._company(faq)} - {TITLE}",
            author="TEN Capital Network",
            subject=TITLE,
        )
        frame = Frame(
            s.MARGIN,
            s.MARGIN + FOOTER_HEIGHT,
            width - 2 * s.MARGIN,
            height - 2 * s.MARGIN - FOOTER_HEIGHT,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        doc.addPageTemplates(
            [
                PageTemplate(
                    id="faq",
                    frames=[frame],
                    onPage=lambda canvas, _doc: self._footer(canvas, faq, width),
                )
            ]
        )
        doc.build(self._story(faq, styles, threshold))
        return destination

    # --- content ------------------------------------------------------------------

    def _company(self, faq: Faq) -> str:
        return faq.company_name.value or Path(faq.provenance.source_filename).stem

    def _story(
        self, faq: Faq, styles: dict[str, ParagraphStyle], threshold: float
    ) -> list[Any]:
        story: list[Any] = []
        story.extend(self._masthead(faq, styles, threshold))

        stage = ""
        for entry in faq.ordered_entries():
            question = entry.question
            block: list[Any] = []
            if question.stage != stage:
                stage = question.stage
                block.append(Paragraph(escape(stage.upper()), styles["stage"]))
            block.append(Paragraph(escape(question.text), styles["question"]))

            if entry.is_answered:
                block.append(
                    Paragraph(escape(entry.answer.value or ""), styles["answer"])
                )
                line = citation(entry, threshold)
                block.append(
                    Paragraph(line, styles["provenance"])
                    if line
                    else Spacer(1, 8)
                )
            else:
                block.append(Paragraph(escape(UNANSWERED), styles["unanswered"]))
                block.append(Spacer(1, 8))

            # A question orphaned from its answer by a page break is a document that
            # cannot be read aloud, which is the one thing this format has to support.
            story.append(KeepTogether(block))

        story.extend(self._open_items(faq, styles))
        story.extend(self._closing(faq, styles, threshold))
        return story

    def _masthead(
        self, faq: Faq, styles: dict[str, ParagraphStyle], threshold: float
    ) -> list[Any]:
        """Company, tagline, and the coverage line that frames everything below it."""
        parts: list[Any] = [Paragraph(escape(self._company(faq)), styles["company"])]
        if faq.tagline.value:
            parts.append(Paragraph(escape(faq.tagline.value), styles["tagline"]))

        chips = [c for c in (faq.sector.value, faq.stage.value) if c]
        if chips:
            parts.append(
                Paragraph(escape("  ·  ".join(chips)), styles["chips"])
            )

        answered = faq.answered_count()
        total = len(faq.entries)
        flagged = len(faq.low_confidence_entries(threshold))
        summary = f"{answered} of {total} questions answered by the document"
        if answered < total:
            summary += f"  ·  {total - answered} unanswered"
        if flagged:
            summary += f"  ·  {flagged} low confidence"
        parts.append(Paragraph(escape(summary), styles["coverage"]))
        return parts

    def _open_items(self, faq: Faq, styles: dict[str, ParagraphStyle]) -> list[Any]:
        """The unanswered questions, collected as the diligence list.

        They are already printed in place, but scattered through twenty answers they are
        easy to miss. Gathered at the end they become the agenda for the founder call.
        """
        unanswered = faq.unanswered()
        if not unanswered:
            return [
                Paragraph("OPEN DILIGENCE ITEMS", styles["stage"]),
                Paragraph(
                    "None. The document addresses all twenty questions.",
                    styles["open"],
                ),
            ]
        # When nothing was answered, every question is already printed above with its
        # own "not addressed" line. Repeating the whole catalogue here pads a document
        # that should be conspicuously short — the finding is that the deck is empty, and
        # a longer PDF argues the opposite.
        if len(unanswered) == len(faq.entries):
            return [
                Paragraph("OPEN DILIGENCE ITEMS", styles["stage"]),
                Paragraph(
                    escape(
                        "The document does not address any of the twenty questions. "
                        "Every one of them is open; they are listed in full above."
                    ),
                    styles["answer"],
                ),
            ]

        items: list[Any] = [
            Paragraph("OPEN DILIGENCE ITEMS", styles["stage"]),
            Paragraph(
                escape(
                    f"{len(unanswered)} of the twenty questions are not addressed "
                    f"anywhere in the document. Ask the founders:"
                ),
                styles["answer"],
            ),
            Spacer(1, 4),
        ]
        items.extend(
            Paragraph(f"•&nbsp;&nbsp;{escape(question.text)}", styles["open"])
            for question in unanswered
        )
        return items

    def _closing(
        self, faq: Faq, styles: dict[str, ParagraphStyle], threshold: float
    ) -> list[Any]:
        """The dagger footnote and the AI-generated disclosure."""
        lines = [
            f"{s.DAGGER} marks an answer below {threshold:.0%} confidence — verify before "
            f"relying on it."
        ]
        lines.append(
            "Answers are generated by TEN Capital's analysis tool from the document named "
            "below, and cite the slide or section each claim came from. They are not the "
            "founders' words unless quoted."
        )
        if faq.provenance.citation_warnings:
            lines.append(
                "Citation warnings: " + escape("; ".join(faq.provenance.citation_warnings))
            )
        return [Spacer(1, 10), *[Paragraph(line, styles["closing"]) for line in lines]]

    # --- page furniture -----------------------------------------------------------

    def _footer(self, canvas: Canvas, faq: Faq, width: float) -> None:
        """The TEN Capital footer: title, page number, credit, mark. Centred, 7pt."""
        canvas.saveState()
        compiled = faq.provenance.extracted_at or datetime.now()
        text = (
            f"{TITLE}      {canvas.getPageNumber()}      "
            f"Compiled on {compiled:%d %b %Y} by TEN Capital Network"
        )
        canvas.setFont(s.SANS, 7)
        canvas.setFillColor(s.MUTED)

        logo_width = 0.0
        if s.LOGO_PATH.is_file():
            logo_width = s.LOGO_HEIGHT * s.LOGO_RATIO
        text_width = canvas.stringWidth(text, s.SANS, 7)
        gap = 8.0 if logo_width else 0.0
        start = (width - (text_width + gap + logo_width)) / 2
        baseline = s.MARGIN + 8

        canvas.drawString(start, baseline, text)
        if logo_width:
            canvas.drawImage(
                str(s.LOGO_PATH),
                start + text_width + gap,
                baseline - 4,
                width=logo_width,
                height=s.LOGO_HEIGHT,
                mask="auto",
            )

        canvas.setFont(s.SANS, 6)
        canvas.drawCentredString(
            width / 2,
            baseline - 10,
            f"{faq.provenance.source_filename}  ·  Internal use only",
        )
        canvas.restoreState()


def render_faq(
    faq: Faq,
    destination: Path,
    *,
    paper: Paper = "letter",
    threshold: float = DEFAULT_MIN_CONFIDENCE,
) -> Path:
    """Render an FAQ with the default engine."""
    return FaqRenderer().render(faq, destination, paper=paper, threshold=threshold)
