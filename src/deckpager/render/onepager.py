"""The one-pager, drawn with ReportLab.

Every drawing method takes an optional canvas and returns the height it consumed. Called
with `None` it measures without drawing; called with a canvas it draws exactly what it
measured. Measurement and rendering therefore cannot disagree — the failure that makes a
one-page guarantee vacuous is a layout that measures one thing and paints another.

Nothing here reads a colour, a font, or a band height from anywhere but `style`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from deckpager.errors import RenderError
from deckpager.models import DEFAULT_MIN_CONFIDENCE, Field, Metric, OnePager, TeamMember
from deckpager.render import style as s

Paper = Literal["letter", "a4"]

#: Annotated because ReportLab ships no stubs: without this the page dimensions
#: arrive as Any and every measurement downstream silently loses its type.
PAGE_SIZES: dict[str, tuple[float, float]] = {"letter": letter, "a4": A4}

#: The fields this layout puts on the page. The footer counts low-confidence fields
#: from this set only - flagging a field the reader cannot find is worse than silence.
RENDERED_FIELDS: tuple[str, ...] = (
    "company_name",
    "tagline",
    "website",
    "sector",
    "stage",
    "hq_location",
    "raise_amount_usd",
    "pre_money_valuation_usd",
    "instrument",
    "amount_committed_usd",
    "close_date",
    "problem",
    "solution",
    "business_model",
    "go_to_market",
    "use_of_funds",
    "traction_metrics",
    "tam_usd",
    "sam_usd",
    "som_usd",
    "market_note",
    "team",
    "competitors",
    "differentiation",
    "key_strengths",
    "key_risks",
    "missing_information",
)


@dataclass(frozen=True)
class PageLayout:
    """The knobs the fitting ladder turns. One instance is one attempt at the page.

    The drop order is spec §9's, lowest-priority first: go-to-market, then business model,
    then competition, then the market note. Typography moves only after prose has been
    given up, because a smaller page is worse to read than a shorter one.
    """

    drop_go_to_market: bool = False
    drop_business_model: bool = False
    drop_competitors: bool = False
    drop_market_note: bool = False
    #: How many diligence requests survive into the analyst block. Spec 6 allows 5.
    requests: int = 5
    #: How many traction tiles survive. Spec 6 caps the extraction at 6.
    metrics: int = 6
    #: How many people survive. Spec 6 caps the extraction at 4.
    people: int = 4
    #: Fraction of each prose field that survives. Spec 3 requires overflow to be
    #: handled by field-level truncation with an ellipsis rather than a second page,
    #: and this is that lever: the last resort, and the one that cannot fail to work.
    text_scale: float = 1.0
    body_pt: float = s.SIZE_BODY
    leading: float = s.LEADING

    def describe(self) -> list[str]:
        """What was given up, one line each, for the provenance record."""
        notes: list[str] = []
        if self.drop_go_to_market:
            notes.append("go-to-market truncated")
        if self.drop_business_model:
            notes.append("business model truncated")
        if self.drop_competitors:
            notes.append("competitor list truncated")
        if self.drop_market_note:
            notes.append("market note truncated")
        if self.requests < 5:
            notes.append(f"diligence requests cut to {self.requests}")
        if self.metrics < 6:
            notes.append(f"traction tiles cut to {self.metrics}")
        if self.people < 4:
            notes.append(f"team cut to {self.people}")
        if self.text_scale < 1.0:
            notes.append(f"prose truncated to {self.text_scale:.0%} of its length")
        if self.body_pt != s.SIZE_BODY:
            notes.append(f"body set to {self.body_pt}pt")
        if self.leading != s.LEADING:
            notes.append(f"line height tightened to {self.leading}")
        return notes


def money(value: int | None) -> str | None:
    """Render an amount the way a partner reads it: $2M, $750K, $5.5M."""
    if value is None:
        return None
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B".replace(".0B", "B")
    if magnitude >= 1_000_000:
        return f"${value / 1_000_000:.1f}M".replace(".0M", "M")
    if magnitude >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,}"


def ellipsize(text: str, limit: int) -> str:
    """Cut to `limit` characters at a word boundary, with an ellipsis (spec §3)."""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 0)].rstrip()
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return f"{cut}…"


class OnePagerRenderer:
    """Draws an OnePager onto a single page."""

    name = "reportlab"

    def preflight(self) -> list[str]:
        """ReportLab is pure Python, so the only real risk is the optional logo asset."""
        problems: list[str] = []
        try:
            import reportlab  # noqa: F401
        except ImportError:  # pragma: no cover - dependency is declared
            problems.append("reportlab is not installed. Run `pip install -e .` in the root.")
        return problems

    def page_count(self, document: Path) -> int:
        """Count pages by reading the produced PDF back."""
        from pypdf import PdfReader

        try:
            return len(PdfReader(str(document)).pages)
        except Exception as exc:  # pypdf raises assorted types on malformed files
            raise RenderError(f"Could not read back the PDF {document}: {exc}") from exc

    # --- geometry ---------------------------------------------------------------------

    def overflow(
        self,
        one_pager: OnePager,
        layout: PageLayout,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> float:
        """Points by which the content exceeds the page, or 0 when it fits.

        Geometry, not page count. ReportLab paints past the bottom edge without ever
        starting a second page, so a page count of 1 is no evidence that anything fits.
        """
        width, height = PAGE_SIZES[paper]
        content_width = width - 2 * s.MARGIN
        left_width, right_width = self._column_widths(content_width)

        body_top = height - s.MARGIN - s.HEIGHT_HEADER - s.GAP_BAND - s.HEIGHT_ASK - s.GAP_BAND
        analyst_height = self._analyst_block(None, one_pager, layout, 0, 0, content_width)
        body_floor = s.MARGIN + s.HEIGHT_FOOTER + analyst_height + s.GAP_BAND
        available = body_top - body_floor

        needed = max(
            self._left_column(None, one_pager, layout, 0, 0, left_width, threshold),
            self._right_column(None, one_pager, layout, 0, 0, right_width, threshold),
        )
        return max(0.0, needed - available)

    def _column_widths(self, content_width: float) -> tuple[float, float]:
        """Left 58%, right 42% (spec §9), with the gap taken off the top."""
        usable = content_width - s.COLUMN_GAP
        left = usable * s.LEFT_COLUMN_RATIO
        return left, usable - left

    # --- rendering --------------------------------------------------------------------

    def render(
        self,
        one_pager: OnePager,
        destination: Path,
        *,
        paper: Paper = "letter",
        layout: PageLayout | None = None,
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        """Write the one-pager. Exactly one page, always."""
        layout = layout or PageLayout()
        width, height = PAGE_SIZES[paper]
        content_width = width - 2 * s.MARGIN
        left_width, right_width = self._column_widths(content_width)

        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas = Canvas(str(destination), pagesize=(width, height))
        canvas.setTitle(f"{self._company(one_pager)} — TEN Capital one-pager")
        canvas.setAuthor("TEN Capital Network")

        top = height - s.MARGIN
        self._header(canvas, one_pager, s.MARGIN, top, content_width, threshold)

        ask_top = top - s.HEIGHT_HEADER - s.GAP_BAND
        self._ask_strip(canvas, one_pager, s.MARGIN, ask_top, content_width, threshold)

        body_top = ask_top - s.HEIGHT_ASK - s.GAP_BAND
        analyst_height = self._analyst_block(None, one_pager, layout, 0, 0, content_width)
        analyst_top = s.MARGIN + s.HEIGHT_FOOTER + analyst_height

        self._left_column(canvas, one_pager, layout, s.MARGIN, body_top, left_width, threshold)
        self._right_column(
            canvas,
            one_pager,
            layout,
            s.MARGIN + left_width + s.COLUMN_GAP,
            body_top,
            right_width,
            threshold,
        )
        self._analyst_block(canvas, one_pager, layout, s.MARGIN, analyst_top, content_width)
        self._footer(canvas, one_pager, s.MARGIN, s.MARGIN, content_width, threshold)

        canvas.showPage()
        canvas.save()
        return destination

    # --- header -----------------------------------------------------------------------

    def _company(self, one_pager: OnePager) -> str:
        """The company name, or an honest placeholder when the deck never said."""
        return one_pager.company_name.value or "Unnamed company"

    def _header(
        self,
        canvas: Canvas,
        one_pager: OnePager,
        x: float,
        top: float,
        width: float,
        threshold: float,
    ) -> None:
        """Logo left, name and tagline centre-left, sector/stage/HQ chips right."""
        chips = self._chips(one_pager, threshold)
        chip_width = self._chips_width(chips)
        text_x = x
        if s.LOGO_PATH.is_file():
            logo_width = s.LOGO_HEIGHT * s.LOGO_RATIO
            canvas.drawImage(
                str(s.LOGO_PATH),
                x,
                top - s.LOGO_HEIGHT - 1,
                width=logo_width,
                height=s.LOGO_HEIGHT,
                mask="auto",
            )
            text_x = x + logo_width + 14

        name_width = width - (text_x - x) - chip_width - 12
        name = self._company(one_pager)
        while stringWidth(name, s.SERIF_BOLD, s.SIZE_COMPANY) > name_width and len(name) > 4:
            name = ellipsize(name, len(name) - 2)
        canvas.setFillColor(s.INK)
        canvas.setFont(s.SERIF_BOLD, s.SIZE_COMPANY)
        canvas.drawString(text_x, top - s.SIZE_COMPANY + 1, name)

        baseline = top - s.SIZE_COMPANY - 12
        tagline = one_pager.tagline.value
        cursor = text_x
        if tagline:
            canvas.setFillColor(s.MUTED)
            canvas.setFont(s.SANS, s.SIZE_TAGLINE)
            line = simpleSplit(tagline, s.SANS, s.SIZE_TAGLINE, name_width)[0]
            canvas.drawString(cursor, baseline, line)
            cursor += float(stringWidth(line, s.SANS, s.SIZE_TAGLINE))
            if one_pager.tagline.is_low_confidence(threshold):
                self._dagger(canvas, cursor, baseline)
                cursor += 5

        website = one_pager.website.value
        if website:
            canvas.setFillColor(s.MUTED)
            canvas.setFont(s.SANS, s.SIZE_CHIP)
            separator = "  ·  " if tagline else ""
            canvas.drawString(cursor, baseline, f"{separator}{website}")
            cursor += float(stringWidth(f"{separator}{website}", s.SANS, s.SIZE_CHIP))
            if one_pager.website.is_low_confidence(threshold):
                self._dagger(canvas, cursor, baseline)

        self._draw_chips(canvas, chips, x + width, top - s.SIZE_COMPANY + 1)

        canvas.setStrokeColor(s.ACCENT)
        canvas.setLineWidth(s.RULE_WIDTH * 2)
        rule_y = top - s.HEIGHT_HEADER + 10
        canvas.line(x, rule_y, x + width, rule_y)

    def _chips(self, one_pager: OnePager, threshold: float) -> list[str]:
        """Sector · stage · HQ, skipping whatever the deck did not say.

        A weak chip carries the dagger inline. The footer counts it, so it has to be
        findable on the page.
        """
        chips: list[str] = []
        for field in (one_pager.sector, one_pager.stage, one_pager.hq_location):
            if not field.value:
                continue
            text = ellipsize(str(field.value), 30)
            if field.is_low_confidence(threshold):
                text = f"{text}{s.DAGGER}"
            chips.append(text)
        return chips

    def _chips_width(self, chips: list[str]) -> float:
        """How much room the chip row needs, so the company name can have the rest."""
        if not chips:
            return 0.0
        boxes = sum(float(stringWidth(c, s.SANS, s.SIZE_CHIP)) + 12 for c in chips)
        return boxes + 5 * (len(chips) - 1)

    def _draw_chips(self, canvas: Canvas, chips: list[str], right: float, y: float) -> None:
        """Right-aligned, so the row stays flush to the margin however many there are."""
        for chip in reversed(chips):
            box_width = stringWidth(chip, s.SANS, s.SIZE_CHIP) + 12
            left = right - box_width
            canvas.setFillColor(s.TINT_ASK)
            canvas.roundRect(left, y - 3, box_width, 14, 3, stroke=0, fill=1)
            canvas.setFillColor(s.ACCENT)
            canvas.setFont(s.SANS, s.SIZE_CHIP)
            canvas.drawString(left + 6, y + 1, chip)
            right = left - 5

    # --- the ask ----------------------------------------------------------------------

    def _ask_strip(
        self,
        canvas: Canvas,
        one_pager: OnePager,
        x: float,
        top: float,
        width: float,
        threshold: float,
    ) -> None:
        """Five evenly spaced label-value cells on a tinted band (spec §9)."""
        canvas.setFillColor(s.TINT_ASK)
        canvas.rect(x, top - s.HEIGHT_ASK, width, s.HEIGHT_ASK, stroke=0, fill=1)

        cells: list[tuple[str, Field[Any]]] = [
            ("RAISE", one_pager.raise_amount_usd),
            ("PRE-MONEY", one_pager.pre_money_valuation_usd),
            ("INSTRUMENT", one_pager.instrument),
            ("COMMITTED", one_pager.amount_committed_usd),
            ("CLOSE", one_pager.close_date),
        ]
        cell_width = width / len(cells)
        for index, (label, field) in enumerate(cells):
            cell_x = x + index * cell_width + 9
            usable = cell_width - 18

            canvas.setFillColor(s.MUTED)
            canvas.setFont(s.SANS, s.SIZE_LABEL)
            canvas.drawString(cell_x, top - 13, label)

            value = field.value
            text = money(value) if isinstance(value, int) else (
                str(value) if value is not None else None
            )
            baseline = top - 28

            if text is None:
                canvas.setFillColor(s.MUTED)
                canvas.setFont(s.SANS, s.SIZE_ASK_VALUE)
                canvas.drawString(cell_x, baseline, s.EMPTY)
                continue

            # Shrink, then wrap, then clip. An amount stays on one line at full size; an
            # instrument - `Convertible debt, 6% interest, 20% discount, $5M cap` - is the
            # cell that needs two, and it is the cell a partner reads most carefully.
            font = s.SERIF_BOLD
            size = s.SIZE_ASK_VALUE
            while float(stringWidth(text, font, size)) > usable and size > 7.5:
                size -= 0.5
            wrapped = simpleSplit(text, font, size, usable)
            lines = wrapped[:2]
            if len(wrapped) > 2 and lines:
                # Say so. A cell that stops mid-clause without a mark reads as the whole
                # of the deal terms, which is the one thing it must not do.
                lines[-1] = ellipsize(lines[-1], max(len(lines[-1]) - 2, 4))

            canvas.setFillColor(s.INK)
            canvas.setFont(font, size)
            leading = size * 1.05
            first_baseline = baseline + (leading / 2 if len(lines) > 1 else 0)
            for index, line in enumerate(lines):
                canvas.drawString(cell_x, first_baseline - index * leading, line)
            if field.is_low_confidence(threshold) and lines:
                self._dagger(
                    canvas,
                    cell_x + float(stringWidth(lines[-1], font, size)),
                    first_baseline - (len(lines) - 1) * leading,
                )

    def _dagger(self, canvas: Canvas, x: float, baseline: float) -> None:
        """The low-confidence marker (spec §9), raised like a footnote reference."""
        canvas.setFillColor(s.MUTED)
        canvas.setFont(s.SANS, s.SIZE_DAGGER)
        canvas.drawString(x + 1, baseline + s.DAGGER_RISE, s.DAGGER)

    # --- shared blocks ----------------------------------------------------------------

    def _heading(self, canvas: Canvas | None, x: float, top: float, text: str) -> float:
        """A section heading. Returns the height consumed."""
        if canvas is not None:
            canvas.setFillColor(s.ACCENT)
            canvas.setFont(s.SERIF_BOLD, s.SIZE_HEADING)
            canvas.drawString(x, top - s.SIZE_HEADING, text)
        return s.SIZE_HEADING + 4

    def _paragraph(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        text: str | None,
        layout: PageLayout,
        *,
        low_confidence: bool = False,
    ) -> float:
        """Wrapped body text, or the em dash that means the deck was silent."""
        leading = layout.body_pt * layout.leading
        if text is None:
            if canvas is not None:
                canvas.setFillColor(s.MUTED)
                canvas.setFont(s.SANS, layout.body_pt)
                canvas.drawString(x, top - layout.body_pt, s.EMPTY)
            return leading

        lines = simpleSplit(self._scale(text, layout), s.SANS, layout.body_pt, width)
        if canvas is not None:
            canvas.setFillColor(s.INK)
            canvas.setFont(s.SANS, layout.body_pt)
            for index, line in enumerate(lines):
                canvas.drawString(x, top - layout.body_pt - index * leading, line)
            if low_confidence and lines:
                self._dagger(
                    canvas,
                    x + stringWidth(lines[-1], s.SANS, layout.body_pt),
                    top - layout.body_pt - (len(lines) - 1) * leading,
                )
        return leading * len(lines)

    def _section(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        heading: str,
        field: Field[str],
        layout: PageLayout,
        threshold: float,
    ) -> float:
        """Heading plus one paragraph — the shape of most of the left column."""
        used = self._heading(canvas, x, top, heading)
        used += self._paragraph(
            canvas,
            x,
            top - used,
            width,
            field.value,
            layout,
            low_confidence=field.is_low_confidence(threshold),
        )
        return used + 8

    def _bullets(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        items: list[str],
        layout: PageLayout,
    ) -> float:
        """A bulleted list, wrapped and hanging-indented."""
        leading = layout.body_pt * layout.leading
        indent = 9.0
        used = 0.0
        for item in items:
            lines = simpleSplit(self._scale(item, layout), s.SANS, layout.body_pt, width - indent)
            if canvas is not None:
                canvas.setFillColor(s.ACCENT)
                canvas.setFont(s.SANS, layout.body_pt)
                canvas.drawString(x, top - used - layout.body_pt, "•")
                canvas.setFillColor(s.INK)
                for index, line in enumerate(lines):
                    canvas.drawString(
                        x + indent, top - used - layout.body_pt - index * leading, line
                    )
            used += leading * len(lines) + 1.5
        return used

    # --- left column ------------------------------------------------------------------

    def _left_column(
        self,
        canvas: Canvas | None,
        one_pager: OnePager,
        layout: PageLayout,
        x: float,
        top: float,
        width: float,
        threshold: float,
    ) -> float:
        """Problem, solution, model, go-to-market, use of funds (spec §9)."""
        used = self._section(
            canvas, x, top, width, "PROBLEM", one_pager.problem, layout, threshold
        )
        used += self._section(
            canvas, x, top - used, width, "SOLUTION", one_pager.solution, layout, threshold
        )

        model = self._maybe_shorten(one_pager.business_model, layout.drop_business_model, 110)
        used += self._section(
            canvas, x, top - used, width, "BUSINESS MODEL", model, layout, threshold
        )

        gtm = self._maybe_shorten(one_pager.go_to_market, layout.drop_go_to_market, 110)
        used += self._section(
            canvas, x, top - used, width, "GO-TO-MARKET", gtm, layout, threshold
        )

        used += self._heading(canvas, x, top - used, "USE OF FUNDS")
        funds = one_pager.use_of_funds.value or []
        if funds:
            used += self._bullets(canvas, x, top - used, width, funds, layout)
        else:
            used += self._paragraph(canvas, x, top - used, width, None, layout)
        return used

    @staticmethod
    def _scale(text: str, layout: PageLayout) -> str:
        """Apply the global truncation scale to one string (spec 3)."""
        if layout.text_scale >= 1.0:
            return text
        return ellipsize(text, max(int(len(text) * layout.text_scale), 12))

    @staticmethod
    def _maybe_shorten(field: Field[str], drop: bool, limit: int) -> Field[str]:
        """Apply a truncation rung to one field without mutating the source document."""
        if not drop or not field.value:
            return field
        return field.model_copy(update={"value": ellipsize(field.value, limit)})

    # --- right column -----------------------------------------------------------------

    def _right_column(
        self,
        canvas: Canvas | None,
        one_pager: OnePager,
        layout: PageLayout,
        x: float,
        top: float,
        width: float,
        threshold: float,
    ) -> float:
        """Traction, market, team, competition (spec §9)."""
        used = self._heading(canvas, x, top, "TRACTION")
        metrics = (one_pager.traction_metrics.value or [])[: layout.metrics]
        used += self._metric_tiles(canvas, x, top - used, width, metrics, layout)
        used += 8

        used += self._heading(canvas, x, top - used, "MARKET")
        used += self._market(canvas, one_pager, x, top - used, width, layout, threshold)
        used += 8

        used += self._heading(canvas, x, top - used, "TEAM")
        used += self._team(canvas, x, top - used, width, one_pager.team.value or [], layout)
        used += 8

        used += self._heading(canvas, x, top - used, "COMPETITION")
        used += self._competition(canvas, one_pager, layout, x, top - used, width, threshold)
        return used

    def _metric_tiles(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        metrics: list[Metric],
        layout: PageLayout,
    ) -> float:
        """Traction as a 2-up grid of tiles (spec §9)."""
        if not metrics:
            return self._paragraph(canvas, x, top, width, None, layout)

        gap = 6.0
        tile_width = (width - gap) / 2
        used = 0.0
        for row in range((len(metrics) + 1) // 2):
            pair = metrics[row * 2 : row * 2 + 2]
            heights = [
                self._tile(canvas, x + column * (tile_width + gap), top - used, tile_width, m, layout)
                for column, m in enumerate(pair)
            ]
            used += max(heights) + gap
        return used

    def _tile(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        metric: Metric,
        layout: PageLayout,
    ) -> float:
        """One metric in a tile.

        The schema allows a 120-character value, and real decks use it: `Working device:
        feasibility and functionality proven` is a traction metric too. A tile that only
        knows how to set big numerals clips those mid-word, so the type size follows the
        value - display size for something numeral-shaped, body size for a sentence.
        """
        value = self._scale(metric.value, layout)
        numeral = len(value) <= 16
        font = s.SANS_BOLD
        size = s.SIZE_METRIC_VALUE if numeral else s.SIZE_BODY
        value_lines = simpleSplit(value, font, size, width - 12)[:3]

        label = self._scale(metric.label, layout)
        if metric.period:
            label = f"{label} · {metric.period}"
        label_lines = simpleSplit(label, s.SANS, s.SIZE_LABEL, width - 12)[:2]

        leading_value = size * 1.16
        leading_label = s.SIZE_LABEL * 1.3
        top_pad = 6 + size
        height = top_pad + leading_value * (len(value_lines) - 1) + 4
        height += leading_label * len(label_lines) + 6

        if canvas is not None:
            canvas.setFillColor(s.TINT_ASK)
            canvas.roundRect(x, top - height, width, height, 3, stroke=0, fill=1)
            canvas.setFillColor(s.INK)
            canvas.setFont(font, size)
            for index, line in enumerate(value_lines):
                canvas.drawString(x + 6, top - top_pad - index * leading_value, line)
            canvas.setFillColor(s.MUTED)
            canvas.setFont(s.SANS, s.SIZE_LABEL)
            base = top - top_pad - leading_value * (len(value_lines) - 1) - 10
            for index, line in enumerate(label_lines):
                canvas.drawString(x + 6, base - index * leading_label, line)
        return height

    def _market(
        self,
        canvas: Canvas | None,
        one_pager: OnePager,
        x: float,
        top: float,
        width: float,
        layout: PageLayout,
        threshold: float,
    ) -> float:
        """TAM / SAM / SOM across one row, with the note beneath."""
        cells = [
            ("TAM", one_pager.tam_usd),
            ("SAM", one_pager.sam_usd),
            ("SOM", one_pager.som_usd),
        ]
        cell_width = width / 3
        if canvas is not None:
            for index, (label, field) in enumerate(cells):
                cell_x = x + index * cell_width
                canvas.setFillColor(s.MUTED)
                canvas.setFont(s.SANS, s.SIZE_LABEL)
                canvas.drawString(cell_x, top - 6, label)
                text = money(field.value)
                canvas.setFillColor(s.INK if text else s.MUTED)
                canvas.setFont(s.SANS_BOLD, s.SIZE_ASK_VALUE)
                canvas.drawString(cell_x, top - 19, text or s.EMPTY)
                if text and field.is_low_confidence(threshold):
                    self._dagger(
                        canvas,
                        cell_x + stringWidth(text, s.SANS_BOLD, s.SIZE_ASK_VALUE),
                        top - 19,
                    )
        used = 23.0

        note = self._maybe_shorten(one_pager.market_note, layout.drop_market_note, 90)
        if note.value:
            used += self._paragraph(
                canvas,
                x,
                top - used,
                width,
                note.value,
                layout,
                low_confidence=note.is_low_confidence(threshold),
            )
        return used

    def _team(
        self,
        canvas: Canvas | None,
        x: float,
        top: float,
        width: float,
        team: list[TeamMember],
        layout: PageLayout,
    ) -> float:
        """One person per entry: name in bold, then role and background."""
        team = team[: layout.people]
        if not team:
            return self._paragraph(canvas, x, top, width, None, layout)

        leading = layout.body_pt * layout.leading
        used = 0.0
        for member in team:
            if canvas is not None:
                canvas.setFillColor(s.INK)
                canvas.setFont(s.SANS_BOLD, layout.body_pt)
                canvas.drawString(x, top - used - layout.body_pt, member.name)
            name_width = stringWidth(member.name, s.SANS_BOLD, layout.body_pt)

            detail = member.role
            if member.background:
                detail = f"{detail} — {member.background}"
            lines = simpleSplit(self._scale(detail, layout), s.SANS, layout.body_pt, width - name_width - 6)
            first, rest = (lines[0], lines[1:]) if lines else ("", [])
            if canvas is not None:
                canvas.setFillColor(s.MUTED)
                canvas.setFont(s.SANS, layout.body_pt)
                canvas.drawString(x + name_width + 6, top - used - layout.body_pt, first)
                for index, line in enumerate(rest, start=1):
                    canvas.drawString(x, top - used - layout.body_pt - index * leading, line)
            used += leading * (1 + len(rest)) + 2
        return used

    def _competition(
        self,
        canvas: Canvas | None,
        one_pager: OnePager,
        layout: PageLayout,
        x: float,
        top: float,
        width: float,
        threshold: float,
    ) -> float:
        """Who else is here, then why this company says it wins."""
        competitors = one_pager.competitors.value or []
        if layout.drop_competitors:
            competitors = competitors[:3]

        used = self._paragraph(
            canvas, x, top, width, " · ".join(competitors) if competitors else None, layout
        )

        differentiation = one_pager.differentiation
        if differentiation.value:
            used += 3
            used += self._paragraph(
                canvas,
                x,
                top - used,
                width,
                differentiation.value,
                layout,
                low_confidence=differentiation.is_low_confidence(threshold),
            )
        return used

    # --- analyst block ----------------------------------------------------------------

    def _analyst_block(
        self,
        canvas: Canvas | None,
        one_pager: OnePager,
        layout: PageLayout,
        x: float,
        top: float,
        width: float,
    ) -> float:
        """Strengths, risks, and what to ask for — visibly not the founders' claims.

        Tinted differently from the ask strip and labelled in italics, because the one thing
        this block must never do is read as something the deck said.
        """
        columns = [
            ("STRENGTHS", one_pager.key_strengths.value or []),
            ("RISKS", one_pager.key_risks.value or []),
            ("REQUEST FROM FOUNDER", (one_pager.missing_information.value or [])[: layout.requests]),
        ]
        gap = 10.0
        column_width = (width - 2 * gap - 20) / 3
        label_height = s.SIZE_LABEL + 10

        heights: list[float] = []
        for _, items in columns:
            height = s.SIZE_HEADING + 4
            for item in items:
                lines = simpleSplit(self._scale(item, layout), s.SANS, layout.body_pt, column_width)
                height += layout.body_pt * layout.leading * len(lines) + 2
            heights.append(height)
        total = label_height + (max(heights) if heights else 0.0) + 12

        if canvas is not None:
            canvas.setFillColor(s.TINT_ANALYST)
            canvas.rect(x, top - total, width, total, stroke=0, fill=1)
            canvas.setStrokeColor(s.ACCENT)
            canvas.setLineWidth(s.RULE_WIDTH)
            canvas.line(x, top, x + width, top)

            canvas.setFillColor(s.MUTED)
            canvas.setFont(s.SANS_ITALIC, s.SIZE_LABEL)
            canvas.drawString(x + 10, top - 12, s.ANALYST_LABEL)

            for index, (heading, items) in enumerate(columns):
                column_x = x + 10 + index * (column_width + gap)
                used = label_height
                used += self._heading(canvas, column_x, top - used, heading)
                for item in items:
                    used += self._paragraph(
                        canvas, column_x, top - used, column_width, item, layout
                    )
                    used += 2
        return total

    # --- footer -----------------------------------------------------------------------

    def _footer(
        self,
        canvas: Canvas,
        one_pager: OnePager,
        x: float,
        bottom: float,
        width: float,
        threshold: float,
    ) -> None:
        """The provenance rule, plus the count of flagged fields (spec §9)."""
        provenance = one_pager.provenance
        canvas.setFillColor(s.MUTED)
        canvas.setFont(s.SANS, s.SIZE_FOOTER)
        canvas.drawString(
            x,
            bottom,
            s.FOOTER_TEMPLATE.format(
                filename=provenance.source_filename,
                date=provenance.extracted_at.strftime("%d %b %Y"),
            ),
        )

        shown = set(RENDERED_FIELDS)
        weak = sum(
            1 for name in one_pager.low_confidence_fields(threshold) if name in shown
        )
        if weak:
            footnote = s.DAGGER_FOOTNOTE.format(dagger=s.DAGGER, threshold=threshold)
            canvas.drawRightString(x + width, bottom, f"{weak} field(s) {footnote}")


def _restore_what_was_not_needed(
    layout: PageLayout,
    applied: list[dict[str, Any]],
    measure: Callable[[PageLayout], float],
) -> PageLayout:
    """Undo any reduction the page turns out not to need.

    The rungs are coarse - one diligence request is about 20 points - so the first layout
    that fits usually overshoots and leaves a band of white above the analyst block. Each
    applied reduction is offered back, newest first, and kept only if the page still fits
    without it. Cheap: a handful of measurements, no rendering.
    """
    defaults = PageLayout()
    for change in reversed(applied):
        restored = replace(layout, **{k: getattr(defaults, k) for k in change})
        if measure(restored) <= 0:
            layout = restored
    return layout

def fit_and_render(
    one_pager: OnePager,
    destination: Path,
    *,
    paper: Paper = "letter",
    threshold: float = DEFAULT_MIN_CONFIDENCE,
    renderer: OnePagerRenderer | None = None,
) -> tuple[Path, list[str]]:
    """Reduce until the content fits, then render once. Returns the file and what was cut.

    The ladder is spec §9's, and the reductions are measured rather than hoped for: each
    rung is applied, the overflow re-measured, and only the layout that actually fits is
    written to disk. If every rung is spent and it still overflows, that is an error — a
    two-page one-pager is the silent failure this exists to prevent.
    """
    engine = renderer or OnePagerRenderer()

    def measure(candidate: PageLayout) -> float:
        return engine.overflow(one_pager, candidate, paper=paper, threshold=threshold)

    def draw(candidate: PageLayout) -> Path:
        return engine.render(
            one_pager, destination, paper=paper, layout=candidate, threshold=threshold
        )

    layout = PageLayout()
    applied: list[dict[str, Any]] = []
    excess = measure(layout)
    if excess <= 0:
        return draw(layout), []

    # Spec 9 order: prose an analyst can infer goes first, typography last. A rung that
    # does not reduce the overflow is reverted rather than kept, because the page is two
    # independent columns and most rungs only shorten one of them. Keeping an ineffective
    # rung would throw away the go-to-market line to relieve pressure in the right column,
    # which costs the reader a section and buys nothing.
    for change in (
        {"drop_go_to_market": True},
        {"drop_business_model": True},
        {"drop_competitors": True},
        {"drop_market_note": True},
        {"requests": 4},
        {"requests": 3},
        {"metrics": 5},
        {"metrics": 4},
        {"people": 3},
        {"body_pt": 7.5},
        {"leading": 1.18},
        {"text_scale": 0.75},
        {"text_scale": 0.55},
        {"text_scale": 0.4},
        {"text_scale": 0.28},
        {"text_scale": 0.18},
        {"text_scale": 0.12},
    ):
        candidate = replace(layout, **change)
        reduced = measure(candidate)
        if reduced >= excess:
            continue
        layout, excess = candidate, reduced
        applied.append(change)
        if excess <= 0:
            layout = _restore_what_was_not_needed(layout, applied, measure)
            return draw(layout), layout.describe()

    tried = '; '.join(layout.describe())
    raise RenderError(
        f"The one-pager still overflows by {excess:.0f}pt with every reduction applied. "
        f"Tried, in order: {tried}. "
        f"The content is too long for one page at a readable size - the usual cause is a "
        f"problem or solution far over the character limits. Check the extraction JSON."
    )
