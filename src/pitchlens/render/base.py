"""The renderer contract.

One method: given an assessment and a destination, write a document. The engine behind it
is swappable — ReportLab today, WeasyPrint if the GTK stack is ever available — because the
fitting ladder and the field map live above this line, not inside an engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pitchlens.analysis.schema import Assessment

Paper = Literal["letter", "a4"]


@dataclass(frozen=True)
class Layout:
    """The knobs the fitting ladder turns. One instance is one attempt at the page."""

    #: Words the executive summary is truncated to. None means "as written".
    summary_words: int | None = None
    #: How many diligence questions survive to the footer.
    diligence_questions: int = 5
    #: Whether risk reasons are cut to their first clause.
    short_risk_reasons: bool = False
    #: Body text size in points.
    body_pt: float = 9.0
    #: Multiplier on body size for leading.
    line_height: float = 1.35

    def describe(self) -> str:
        """A one-line record of what was given up, for `method_notes`."""
        parts: list[str] = []
        if self.summary_words is not None:
            parts.append(f"summary truncated to ~{self.summary_words} words")
        if self.diligence_questions < 5:
            parts.append(f"diligence questions cut to {self.diligence_questions}")
        if self.short_risk_reasons:
            parts.append("risk reasons shortened to first clause")
        if self.body_pt != 9.0:
            parts.append(f"body set to {self.body_pt}pt")
        if self.line_height != 1.35:
            parts.append(f"line-height tightened to {self.line_height}")
        return "; ".join(parts) if parts else "no reductions applied"


class Renderer(ABC):
    """Anything that can turn an assessment into a document on disk."""

    name: str

    @abstractmethod
    def render_onepager(
        self,
        assessment: Assessment,
        destination: Path,
        *,
        paper: Paper = "letter",
        layout: Layout | None = None,
    ) -> Path:
        """Write a single-page screening memo. Must not silently emit a second page."""

    @abstractmethod
    def page_count(self, document: Path) -> int:
        """Count the pages of a document this renderer produced."""

    @abstractmethod
    def preflight(self) -> list[str]:
        """Return problems that would stop this engine rendering, empty if it is usable.

        Called at startup so a missing native dependency is reported once, with a fix,
        rather than surfacing as a stack trace after a paid analysis has already run.
        """
