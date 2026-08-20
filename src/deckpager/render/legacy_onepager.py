"""The one-page screening memo, drawn with ReportLab.

Implements `templates/onepager.md`: four zones, the field map in §4, the empty states in
§4.3, and the fixed vocabularies in §4.5. It draws exactly what the assessment contains —
it never re-ranks, re-words, or fills a gap with a plausible value.

Layout is absolute rather than flowed. A one-pager has a known shape and a hard page
budget, so placing frames directly is both simpler to reason about and cheaper to measure
than driving a flowable document and discovering the overflow afterwards.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from deckpager.analysis.schema import Assessment, Risk, Score
from deckpager.errors import RenderError
from deckpager.render import legacy_theme as t
from deckpager.render.base import Layout, Paper, Renderer

PAGE_SIZES = {"letter": LETTER, "a4": A4}

INSUFFICIENT = "—  insufficient data"

#: Tightest legal gap between left-column blocks. Anything less and the headings stop
#: reading as separators.
_MIN_BLOCK_GAP = 8.0


#: Words a risk reason is capped at once the ladder decides to shorten them. A rationale
#: with no clause separator at all still has to get shorter, or the rung is a no-op.
_RISK_REASON_WORDS = 28


def _cut_words(text: str, limit: int) -> str:
    """Hard cut at a word boundary. The fallback when there is nothing better to cut on."""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(",;:—-") + "…"


def _first_clause(text: str) -> str:
    """The first clause of a rationale, for the compressed risk row.

    Guarded on word count, not characters: "Runway is short" is a whole clause at 15
    characters, while a 25-character fragment ending on a stray comma is not.

    Falls back to a word cut. A model that writes an 800-character rationale as one
    unpunctuated clause would otherwise sail through this rung unchanged, which is exactly
    the case the rung exists for.
    """
    for separator in (" — ", "; ", ", "):
        head, found, _ = text.partition(separator)
        if found and len(head.split()) >= 3:
            return _cut_words(head.rstrip(".") + ".", _RISK_REASON_WORDS)
    sentence, found, _ = text.partition(". ")
    candidate = (sentence + ".") if found else text
    return _cut_words(candidate, _RISK_REASON_WORDS)


def _truncate_words(text: str, limit: int | None) -> str:
    """Cut to `limit` words, preferring a sentence boundary.

    Falls back to a word cut when the text has no usable sentence break — a summary
    written as one long sentence must still shrink, or the ladder's first and cheapest
    rung silently does nothing.
    """
    if limit is None or len(text.split()) <= limit:
        return text
    kept: list[str] = []
    for sentence in text.replace("\n", " ").split(". "):
        candidate = [*kept, sentence]
        if len(" ".join(candidate).split()) > limit and kept:
            break
        kept = candidate
    joined = ". ".join(s.rstrip(".") for s in kept)
    if joined and len(joined.split()) <= limit:
        return joined + "."
    return _cut_words(text, limit)


class OnePagerRenderer(Renderer):
    """Draws the screening one-pager with ReportLab."""

    name = "reportlab"

    def preflight(self) -> list[str]:
        """ReportLab is pure Python, so the only real risk is the optional logo asset."""
        problems: list[str] = []
        try:
            import reportlab  # noqa: F401
        except ImportError:  # pragma: no cover - dependency is declared
            problems.append(
                "reportlab is not installed. Run `pip install -e .` in the project root."
            )
        return problems

    def page_count(self, document: Path) -> int:
        """Count pages by reading the produced PDF back."""
        try:
            return len(PdfReader(str(document)).pages)
        except Exception as exc:  # pypdf raises assorted types on malformed files
            raise RenderError(f"Could not read back the rendered PDF {document}: {exc}") from exc

    def overflow(self, assessment: Assessment, layout: Layout, paper: Paper = "letter") -> float:
        """Points by which the content exceeds the page. Zero means it fits.

        This, not `page_count`, is what the fitting ladder measures. The layout is drawn
        at absolute coordinates, so ReportLab will happily paint text past the bottom edge
        without ever starting a second page — a page count of 1 is therefore no evidence
        that anything fits. Measuring the geometry directly is the only honest signal.
        """
        width, height = PAGE_SIZES[paper]
        column_width = (width - 2 * t.MARGIN - t.COLUMN_GAP) * t.LEFT_COLUMN_RATIO
        sidebar_width = width - t.MARGIN - (t.MARGIN + column_width + t.COLUMN_GAP)

        available = self._body_top(height) - self._footer_height(assessment, layout, width)
        left = self._left_content_height(assessment, layout, column_width)
        sidebar = self._sidebar_height(assessment, sidebar_width)
        return max(0.0, max(left, sidebar) - available)

    def _body_top(self, height: float) -> float:
        """Where the two columns begin, below the header band."""
        return height - t.MARGIN - t.SIZE_COMPANY - t.SIZE_ONELINER - t.SIZE_META - 25

    def _footer_height(self, a: Assessment, layout: Layout, width: float) -> float:
        """Height of the diligence block plus the provenance line, from the page bottom."""
        usable = width - 2 * t.MARGIN
        questions = a.ic_view.diligence_questions[: layout.diligence_questions]
        provenance_lines = len(
            simpleSplit(" · ".join(self._provenance_parts(a)), t.FONT, t.SIZE_PROVENANCE, usable)
        )
        return (
            t.MARGIN
            + provenance_lines * (t.SIZE_PROVENANCE * 1.25)
            + 15
            + len(questions) * (t.SIZE_QUESTION * 1.3)
            + t.SIZE_ZONE_HEADING
            + 14
        )

    def _left_content_height(self, a: Assessment, layout: Layout, width: float) -> float:
        """Total height the left column needs at its tightest legal spacing."""
        summary = _truncate_words(a.executive_summary, layout.summary_words)
        blocks = [
            self._summary_height(summary, layout, width),
            self._bullets_height(a.ic_view.biggest_strengths[:3], layout, width),
            self._bullets_height(a.ic_view.biggest_concerns[:3], layout, width),
            self._risk_height(a.onepager_risks(), layout, width),
        ]
        return sum(blocks) + _MIN_BLOCK_GAP * len(blocks)

    def _sidebar_height(self, a: Assessment, width: float) -> float:
        """Ten scorecard rows plus the overall block."""
        rows = len([row for row in a.scorecard if row.name != "Overall Investability"])
        return t.SIZE_ZONE_HEADING + 5 + rows * 13.0 + 10 + t.SIZE_OVERALL + 26

    def render_onepager(
        self,
        assessment: Assessment,
        destination: Path,
        *,
        paper: Paper = "letter",
        layout: Layout | None = None,
    ) -> Path:
        """Draw the four zones onto a single page."""
        if paper not in PAGE_SIZES:
            raise RenderError(
                f"Unknown paper size {paper!r}. Choose one of: {', '.join(PAGE_SIZES)}."
            )
        layout = layout or Layout()
        width, height = PAGE_SIZES[paper]
        destination.parent.mkdir(parents=True, exist_ok=True)

        canvas = Canvas(str(destination), pagesize=(width, height))
        canvas.setTitle(f"{assessment.company_name} — {t.DOCUMENT_TITLE}")
        canvas.setAuthor("TEN Capital Network")
        canvas.setSubject(t.DOCUMENT_TITLE)

        body_top = self._header(canvas, assessment, width, height)
        footer_top = self._footer(canvas, assessment, layout, width)

        column_width = (width - 2 * t.MARGIN - t.COLUMN_GAP) * t.LEFT_COLUMN_RATIO
        sidebar_x = t.MARGIN + column_width + t.COLUMN_GAP
        sidebar_width = width - t.MARGIN - sidebar_x

        canvas.setStrokeColor(t.RULE_LIGHT)
        canvas.setLineWidth(t.RULE_WIDTH)
        canvas.line(
            sidebar_x - t.COLUMN_GAP / 2, footer_top + 6, sidebar_x - t.COLUMN_GAP / 2, body_top
        )

        self._left_column(canvas, assessment, layout, t.MARGIN, body_top, column_width, footer_top)
        self._sidebar(canvas, assessment, sidebar_x, body_top, sidebar_width)

        canvas.showPage()
        canvas.save()
        return destination

    # --- Zone 1: header band -------------------------------------------------

    def _header(self, canvas: Canvas, a: Assessment, width: float, height: float) -> float:
        """Company identity left, verdict chip right. Returns the y the body starts at."""
        y = height - t.MARGIN - t.SIZE_COMPANY
        chip_width = 132.0
        text_width = width - 2 * t.MARGIN - chip_width - 12

        canvas.setFillColor(t.NAVY)
        canvas.setFont(t.FONT_BOLD, t.SIZE_COMPANY)
        name = a.company_name
        while canvas.stringWidth(name, t.FONT_BOLD, t.SIZE_COMPANY) > text_width and len(name) > 4:
            name = name[:-2]
        if name != a.company_name:
            name = name.rstrip() + "…"
        canvas.drawString(t.MARGIN, y, name)

        y -= t.SIZE_ONELINER + 3
        canvas.setFillColor(t.GREY)
        canvas.setFont(t.FONT, t.SIZE_ONELINER)
        for line in simpleSplit(a.one_line_description, t.FONT, t.SIZE_ONELINER, text_width)[:1]:
            canvas.drawString(t.MARGIN, y, line)

        y -= t.SIZE_META + 4
        canvas.setFont(t.FONT, t.SIZE_META)
        facts = "   ·   ".join(self._header_facts(a))
        # Truncate rather than let a long sector string run under the verdict chip.
        while canvas.stringWidth(facts, t.FONT, t.SIZE_META) > text_width and len(facts) > 8:
            facts = facts[:-2]
        if facts != "   ·   ".join(self._header_facts(a)):
            facts = facts.rstrip(" ·") + "…"
        canvas.drawString(t.MARGIN, y, facts)

        self._chip(canvas, a, width - t.MARGIN - chip_width, height - t.MARGIN - 26, chip_width)

        rule_y = y - 8
        canvas.setStrokeColor(t.NAVY)
        canvas.setLineWidth(t.RULE_WIDTH)
        canvas.line(t.MARGIN, rule_y, width - t.MARGIN, rule_y)
        return rule_y - 14

    def _header_facts(self, a: Assessment) -> list[str]:
        """Stage, ask, sector, dates — each absent one stated rather than left blank."""
        return [
            a.stage_signal or "Stage not stated",
            a.deal.ask or "Ask not stated in deck",
            a.deal.sector or "Sector not stated",
            a.deal.deck_date or "Deck undated",
            f"Analyzed {a.meta.generated_at:%b %d, %Y}",
        ]

    def _chip(self, canvas: Canvas, a: Assessment, x: float, y: float, width: float) -> None:
        """The verdict chip, with confidence beneath it."""
        verdict = a.ic_view.recommendation
        canvas.setFillColor(t.VERDICT_FILL[verdict])
        canvas.roundRect(x, y, width, 18, 3, stroke=0, fill=1)
        canvas.setFillColor(t.WHITE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_CHIP)
        canvas.drawCentredString(x + width / 2, y + 5.5, t.VERDICT_LABEL[verdict])

        canvas.setFillColor(t.GREY)
        canvas.setFont(t.FONT, t.SIZE_CONFIDENCE)
        # The dagger marks a confidence the gates capped, so a reader can tell a stated
        # MEDIUM from one that was downgraded. The reason rides the provenance line.
        dagger = "†" if a.scoring.confidence_downgraded_from else ""
        canvas.drawCentredString(x + width / 2, y - 9, f"{a.ic_view.confidence}{dagger} confidence")

    # --- Zone 2: left column -------------------------------------------------

    def _left_column(
        self,
        canvas: Canvas,
        a: Assessment,
        layout: Layout,
        x: float,
        top: float,
        width: float,
        floor: float,
    ) -> None:
        """Lay out the four blocks, distributing any leftover height between them.

        Measured before it is drawn. A screening memo that under-fills its page should
        breathe evenly rather than stack at the top and leave a dead band above the
        footer — and the measurement is needed anyway to know whether it fits.
        """
        summary = _truncate_words(a.executive_summary, layout.summary_words)
        risks = a.onepager_risks()

        blocks: list[tuple[str, float]] = [
            ("summary", self._summary_height(summary, layout, width)),
            ("strengths", self._bullets_height(a.ic_view.biggest_strengths[:3], layout, width)),
            ("concerns", self._bullets_height(a.ic_view.biggest_concerns[:3], layout, width)),
            ("risk", self._risk_height(risks, layout, width)),
        ]
        content = sum(height for _, height in blocks)
        slack = max(0.0, (top - floor) - content)
        # Cap the distributed gap: past ~28pt the blocks stop reading as one column.
        gap = min(28.0, max(_MIN_BLOCK_GAP, slack / len(blocks)))

        y = top
        for name, _height in blocks:
            if name == "summary":
                canvas.setFillColor(t.BLACK)
                canvas.setFont(t.FONT, layout.body_pt)
                leading = layout.body_pt * layout.line_height
                for line in simpleSplit(summary, t.FONT, layout.body_pt, width):
                    canvas.drawString(x, y, line)
                    y -= leading
            elif name == "strengths":
                y = self._bullets(
                    canvas, "TOP STRENGTHS", a.ic_view.biggest_strengths[:3], x, y, width, layout
                )
            elif name == "concerns":
                y = self._bullets(
                    canvas, "TOP CONCERNS", a.ic_view.biggest_concerns[:3], x, y, width, layout
                )
            else:
                self._risk_row(canvas, risks, layout, x, y, width)
            y -= gap

    # --- measurement, so the column can be balanced before it is drawn -------

    def _summary_height(self, summary: str, layout: Layout, width: float) -> float:
        lines = simpleSplit(summary, t.FONT, layout.body_pt, width)
        return len(lines) * layout.body_pt * layout.line_height

    def _bullets_height(self, items: list[str], layout: Layout, width: float) -> float:
        leading = t.SIZE_BULLET * layout.line_height
        height = t.SIZE_ZONE_HEADING + 3
        for item in items:
            height += leading * len(simpleSplit(item, t.FONT, t.SIZE_BULLET, width - 10)) + 1.5
        return height + 7

    def _risk_height(self, risks: list[Risk], layout: Layout, width: float) -> float:
        height = t.SIZE_ZONE_HEADING + 4
        for risk in risks:
            lines = self._risk_reason_lines(risk, layout, width)
            height += len(lines) * (t.SIZE_RISK * 1.25) + 4
        return height

    def _risk_reason_lines(self, risk: Risk, layout: Layout, width: float) -> list[str]:
        """Wrap a risk reason to the available width. Never cut mid-word."""
        reason = _first_clause(risk.rationale) if layout.short_risk_reasons else risk.rationale
        reason_width = width - (9 + 92 + 42)
        lines: list[str] = simpleSplit(reason, t.FONT, t.SIZE_RISK, reason_width)
        return lines

    def _bullets(
        self,
        canvas: Canvas,
        heading: str,
        items: list[str],
        x: float,
        y: float,
        width: float,
        layout: Layout,
    ) -> float:
        canvas.setFillColor(t.BLUE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_ZONE_HEADING)
        canvas.drawString(x, y, heading)
        y -= t.SIZE_ZONE_HEADING + 3

        canvas.setFillColor(t.BLACK)
        leading = t.SIZE_BULLET * layout.line_height
        for item in items:
            lines = simpleSplit(item, t.FONT, t.SIZE_BULLET, width - 10)
            canvas.setFont(t.FONT_BOLD, t.SIZE_BULLET)
            canvas.drawString(x, y, "·")
            canvas.setFont(t.FONT, t.SIZE_BULLET)
            for offset, line in enumerate(lines):
                canvas.drawString(x + 8, y - offset * leading, line)
            y -= leading * len(lines) + 1.5
        return y - 7

    def _risk_row(
        self,
        canvas: Canvas,
        risks: list[Risk],
        layout: Layout,
        x: float,
        y: float,
        width: float,
    ) -> None:
        canvas.setFillColor(t.BLUE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_ZONE_HEADING)
        canvas.drawString(x, y, "RISK")
        y -= t.SIZE_ZONE_HEADING + 4

        label_width = 92.0
        for risk in risks:
            canvas.setFillColor(t.RISK_FILL.get(risk.level, t.GREY))
            canvas.rect(x, y - 0.5, 5, 5, stroke=0, fill=1)

            canvas.setFillColor(t.BLACK)
            canvas.setFont(t.FONT_BOLD, t.SIZE_RISK)
            canvas.drawString(x + 9, y, risk.name)

            canvas.setFillColor(t.RISK_FILL.get(risk.level, t.GREY))
            canvas.drawString(x + 9 + label_width, y, risk.level.upper())

            canvas.setFillColor(t.GREY)
            canvas.setFont(t.FONT, t.SIZE_RISK)
            reason_x = x + 9 + label_width + 42
            lines = self._risk_reason_lines(risk, layout, width)
            for offset, line in enumerate(lines):
                canvas.drawString(reason_x, y - offset * (t.SIZE_RISK * 1.25), line)
            y -= len(lines) * (t.SIZE_RISK * 1.25) + 4

    # --- Zone 3: sidebar -----------------------------------------------------

    def _sidebar(self, canvas: Canvas, a: Assessment, x: float, top: float, width: float) -> None:
        y = top
        canvas.setFillColor(t.BLUE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_ZONE_HEADING)
        canvas.drawString(x, y, "SCORECARD")
        y -= t.SIZE_ZONE_HEADING + 5

        rows = [row for row in a.scorecard if row.name != "Overall Investability"]
        for index, row in enumerate(rows):
            y = self._score_row(canvas, row, x, y, width, zebra=index % 2 == 1)

        y -= 10
        self._overall(canvas, a, x, y, width)

    def _score_row(
        self, canvas: Canvas, row: Score, x: float, y: float, width: float, *, zebra: bool
    ) -> float:
        height = 13.0
        if zebra:
            canvas.setFillColor(t.ZEBRA_FILL)
            canvas.rect(x - 2, y - 3, width + 4, height, stroke=0, fill=1)

        canvas.setFillColor(t.BLACK)
        canvas.setFont(t.FONT, t.SIZE_SCORE)
        label = row.name
        while canvas.stringWidth(label, t.FONT, t.SIZE_SCORE) > width - 62 and len(label) > 4:
            label = label[:-2]
        canvas.drawString(x, y, label)

        if row.value is None:
            canvas.setFillColor(t.GREY)
            canvas.setFont(t.FONT_ITALIC, t.SIZE_SCORE - 0.5)
            canvas.drawRightString(x + width, y, INSUFFICIENT)
            return y - height

        track_x = x + width - 58
        track_width = 36.0
        canvas.setFillColor(t.ZEBRA_FILL if not zebra else t.WHITE)
        canvas.rect(track_x, y - 0.5, track_width, 5, stroke=0, fill=1)
        canvas.setFillColor(t.NAVY)
        canvas.rect(track_x, y - 0.5, track_width * (row.value / 10.0), 5, stroke=0, fill=1)

        canvas.setFont(t.FONT_BOLD, t.SIZE_SCORE)
        canvas.drawRightString(x + width, y, f"{row.value}/10")
        return y - height

    def _overall(self, canvas: Canvas, a: Assessment, x: float, y: float, width: float) -> None:
        """The headline figure: the weighted score, not the model's own number."""
        canvas.setStrokeColor(t.RULE_LIGHT)
        canvas.setLineWidth(t.RULE_WIDTH)
        canvas.line(x, y + 20, x + width, y + 20)

        canvas.setFillColor(t.BLUE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_ZONE_HEADING)
        canvas.drawString(x, y + 6, "OVERALL INVESTABILITY")

        score = a.headline_score()
        text = f"{score:.1f}" if score is not None else "—"
        if a.scoring.score_divergence:
            text += "†"
        canvas.setFont(t.FONT_BOLD, t.SIZE_OVERALL)
        canvas.drawString(x, y - t.SIZE_OVERALL + 4, text)

        canvas.setFillColor(t.GREY)
        canvas.setFont(t.FONT, t.SIZE_CONFIDENCE)
        canvas.drawString(
            x + canvas.stringWidth(text, t.FONT_BOLD, t.SIZE_OVERALL) + 5,
            y - t.SIZE_OVERALL + 8,
            "/ 10",
        )

    # --- Zone 4: footer ------------------------------------------------------

    def _footer(self, canvas: Canvas, a: Assessment, layout: Layout, width: float) -> float:
        """Diligence questions above the provenance line. Returns the y they start at."""
        usable = width - 2 * t.MARGIN
        y = t.MARGIN

        self._provenance(canvas, a, t.MARGIN, y, usable)
        y += t.SIZE_PROVENANCE + 7

        canvas.setStrokeColor(t.RULE_LIGHT)
        canvas.setLineWidth(t.RULE_WIDTH)
        canvas.line(t.MARGIN, y, width - t.MARGIN, y)
        y += 8

        questions = [
            q.question for q in a.ic_view.diligence_questions[: layout.diligence_questions]
        ]
        leading = t.SIZE_QUESTION * 1.3
        for number, question in reversed(list(enumerate(questions, start=1))):
            # One line each: the footer is a fixed-height band, and a question that wraps
            # would push the provenance line off the page. Ellipsized at a word boundary
            # so a cut question reads as cut rather than as a sentence that stops.
            lines = simpleSplit(question, t.FONT, t.SIZE_QUESTION, usable - 14)
            text = lines[0] if len(lines) == 1 else _cut_words(question, len(lines[0].split()) - 1)
            canvas.setFillColor(t.NAVY)
            canvas.setFont(t.FONT_BOLD, t.SIZE_QUESTION)
            canvas.drawString(t.MARGIN, y, f"{number}.")
            canvas.setFillColor(t.BLACK)
            canvas.setFont(t.FONT, t.SIZE_QUESTION)
            canvas.drawString(t.MARGIN + 14, y, text)
            y += leading

        canvas.setFillColor(t.BLUE)
        canvas.setFont(t.FONT_BOLD, t.SIZE_ZONE_HEADING)
        canvas.drawString(t.MARGIN, y, "DILIGENCE QUESTIONS")
        return y + t.SIZE_ZONE_HEADING + 6

    def _provenance(self, canvas: Canvas, a: Assessment, x: float, y: float, width: float) -> None:
        """The line that makes the artifact auditable. Never abbreviated to fit."""
        canvas.setFillColor(t.GREY)
        canvas.setFont(t.FONT, t.SIZE_PROVENANCE)
        for offset, line in enumerate(
            simpleSplit(" · ".join(self._provenance_parts(a)), t.FONT, t.SIZE_PROVENANCE, width)
        ):
            canvas.drawString(x, y - offset * (t.SIZE_PROVENANCE * 1.25), line)

    def _provenance_parts(self, a: Assessment) -> list[str]:
        meta = a.meta
        parts = [
            meta.source_filename,
            f"sha256 {meta.sha256[:12]}" if meta.sha256 else "sha256 unavailable",
            f"{meta.slide_count} slides",
            f"{meta.model} via {meta.provider}",
            meta.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            self._evidence_summary(a),
        ]
        if note := a.scoring.divergence_note:
            parts.append(note)
        if reason := a.scoring.confidence_downgrade_reason:
            parts.append(f"confidence capped: {reason}")
        return parts

    def _evidence_summary(self, a: Assessment) -> str:
        """`Evidence: n claims — v verified / i inferred / s speculative`."""
        claims = a.all_evidence()
        verified = sum(1 for c in claims if c.basis == "FACT")
        inferred = sum(1 for c in claims if c.basis == "INFERENCE")
        speculative = sum(1 for c in claims if c.basis == "SPECULATION")
        return (
            f"Evidence: {len(claims)} claims — {verified} verified / "
            f"{inferred} inferred / {speculative} speculative"
        )
