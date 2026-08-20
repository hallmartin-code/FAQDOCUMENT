"""The one-page guarantee.

Fitting is not a hope. The renderer produces a document, this module counts the pages, and
if there is more than one it applies reductions in a fixed order and measures again. The
order is by cost to the reader: prose an analyst can infer goes before questions that change
a decision, which go before typography, which goes before failing the run.

If every rung is exhausted and the page still overflows, `OnePagerOverflowError` says what
was tried and what was still too long. It never returns a two-page one-pager.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pitchlens.errors import OnePagerOverflowError
from pitchlens.render.base import Layout

#: The reduction ladder, in the order it is applied. Each entry is a label and a function
#: that returns the next layout to try, or None when that rung has nothing left to give.
LADDER: tuple[tuple[str, Callable[[Layout], Layout | None]], ...] = (
    (
        "truncate the executive summary at a sentence boundary",
        lambda layout: _step_summary(layout),
    ),
    (
        "drop the last diligence question",
        lambda layout: (
            Layout(**{**layout.__dict__, "diligence_questions": layout.diligence_questions - 1})
            if layout.diligence_questions > 3
            else None
        ),
    ),
    (
        "shorten risk reasons to their first clause",
        lambda layout: (
            None
            if layout.short_risk_reasons
            else Layout(**{**layout.__dict__, "short_risk_reasons": True})
        ),
    ),
    (
        "step the body font down",
        lambda layout: _step_body(layout),
    ),
    (
        "tighten the line height",
        lambda layout: (
            None if layout.line_height <= 1.2 else Layout(**{**layout.__dict__, "line_height": 1.2})
        ),
    ),
)

#: Executive summary word floor. Below this the summary stops being a summary.
SUMMARY_FLOOR = 80
#: Word counts the summary steps through on its way to the floor.
_SUMMARY_STEPS = (110, 95, SUMMARY_FLOOR)
#: Body sizes, in order. 8pt is the floor: smaller is not readable across a meeting table.
_BODY_STEPS = (9.0, 8.5, 8.0)


def _step_summary(layout: Layout) -> Layout | None:
    """Next shorter summary length, or None at the floor."""
    current = layout.summary_words
    for step in _SUMMARY_STEPS:
        if current is None or step < current:
            return Layout(**{**layout.__dict__, "summary_words": step})
    return None


def _step_body(layout: Layout) -> Layout | None:
    """Next smaller body size, or None at the 8pt floor."""
    for step in _BODY_STEPS:
        if step < layout.body_pt:
            return Layout(**{**layout.__dict__, "body_pt": step})
    return None


def fit_to_one_page(
    overflow: Callable[[Layout], float],
    render: Callable[[Layout], Path],
    *,
    start: Layout | None = None,
    max_attempts: int = 24,
) -> tuple[Path, Layout, list[str]]:
    """Reduce until the content fits, then render once.

    `overflow` returns how many points the content exceeds the page by, and zero when it
    fits. It measures geometry rather than counting pages, because the one-pager is drawn
    at absolute coordinates: ReportLab paints past the bottom edge without ever starting a
    second page, so a page count of 1 is no evidence that anything fits. That mistake made
    the first version of this guarantee vacuous.

    Measuring before rendering also means only the winning layout is written to disk.

    Returns the document, the layout that fit, and the notes describing what was given up.
    """
    layout = start or Layout()
    attempted: list[str] = []
    excess = overflow(layout)
    if excess <= 0:
        return render(layout), layout, []

    for label, step in LADDER:
        while len(attempted) < max_attempts:
            reduced = step(layout)
            if reduced is None:
                break  # this rung is spent; move to the next
            layout = reduced
            attempted.append(label)
            excess = overflow(layout)
            if excess <= 0:
                return render(layout), layout, [f"One-pager fitting: {layout.describe()}."]

    raise OnePagerOverflowError(
        f"The one-pager still overflows by {excess:.0f}pt with every reduction applied.\n"
        f"Tried, in order: {'; '.join(dict.fromkeys(attempted)) or 'nothing — the ladder is empty'}.\n"
        f"Final layout: {layout.describe()}.\n"
        f"The content is too long for one page at a readable size. The usual cause is risk "
        f"rationales or an executive summary far over their stated limits — check the "
        f"analysis JSON, and re-run with --full for the multi-page memo."
    )
