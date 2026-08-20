"""The TEN Capital house style, as code.

Values come from `templates/onepager.md` §3 and are the single source of truth for both
the one-pager and (later) the memo. Nothing here knows about a specific company or run.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor

# --- Brand palette ------------------------------------------------------------

NAVY = HexColor("#1F3864")
BLUE = HexColor("#2E75B6")
GREY = HexColor("#555555")
ZEBRA_FILL = HexColor("#F2F2F2")
RULE_LIGHT = HexColor("#DDDDDD")
WHITE = HexColor("#FFFFFF")
BLACK = HexColor("#000000")

#: The only hues that carry meaning rather than hierarchy.
VERDICT_FILL = {
    "ADVANCE_TO_PARTNER_MEETING": HexColor("#2E7D32"),
    "MORE_DILIGENCE": HexColor("#B26A00"),
    "PASS": HexColor("#8C1D18"),
}
VERDICT_LABEL = {
    "ADVANCE_TO_PARTNER_MEETING": "ADVANCE",
    "MORE_DILIGENCE": "MORE DILIGENCE",
    "PASS": "PASS",
}
RISK_FILL = {
    "Low": HexColor("#2E7D32"),
    "Medium": HexColor("#B26A00"),
    "High": HexColor("#8C1D18"),
    "Critical": HexColor("#8C1D18"),
}

# --- Typography ---------------------------------------------------------------
# Arial is not bundled with ReportLab; Helvetica is metrically compatible and is what
# every PDF viewer substitutes for Arial anyway. Two weights, one family, per the template.

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

SIZE_COMPANY = 16
SIZE_ONELINER = 9
SIZE_META = 8
SIZE_CHIP = 9
SIZE_CONFIDENCE = 7.5
SIZE_ZONE_HEADING = 9.5
SIZE_BULLET = 8.5
SIZE_RISK = 8
SIZE_SCORE = 8
SIZE_OVERALL = 30
SIZE_QUESTION = 8
SIZE_PROVENANCE = 6.5

# --- Page -----------------------------------------------------------------------

MARGIN = 36.0  # 0.5 inch
LEFT_COLUMN_RATIO = 0.62
COLUMN_GAP = 14.0
RULE_WIDTH = 0.5

#: Resolved from the repo root; the renderer degrades gracefully when it is absent.
LOGO_PATH = Path(__file__).resolve().parents[3] / "assets" / "TEN_Capital_logo_footer.png"

DOCUMENT_TITLE = "TEN Capital Investment Screening"
FOOTER_ATTRIBUTION = "Compiled on {date} by TEN Capital Network"
