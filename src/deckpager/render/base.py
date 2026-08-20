"""The renderer contract, and the registry of engines that satisfy it.

Spec §4 asks for the renderer to sit behind a Protocol so a second engine can exist, and
for a missing native dependency to be reported with an actionable install message rather
than a stack trace. This module is both halves.

One engine is implemented: ReportLab, which is pure Python and therefore works wherever
deckpager installs. WeasyPrint is registered but not built — see `WeasyPrintEngine` for
why, and for what it tells the user instead of failing obscurely.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from deckpager.errors import RenderError
from deckpager.models import DEFAULT_MIN_CONFIDENCE, OnePager

if TYPE_CHECKING:  # pragma: no cover - onepager imports this module back
    from deckpager.render.onepager import PageLayout

Paper = Literal["letter", "a4"]
EngineName = Literal["reportlab", "weasyprint"]

#: Registry order is preference order: the first usable engine is the default.
ENGINE_NAMES: tuple[str, ...] = ("reportlab", "weasyprint")


@runtime_checkable
class Renderer(Protocol):
    """Anything that can turn a one-pager into a single-page document."""

    name: str

    def preflight(self) -> list[str]:
        """Problems that would stop this engine rendering; empty when it is usable.

        Called before any paid work, so a missing native dependency is reported once with
        a fix rather than surfacing as a stack trace after an extraction has been bought.
        """
        ...

    def overflow(
        self,
        one_pager: OnePager,
        layout: PageLayout,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> float:
        """Points by which the content exceeds one page, or 0 when it fits."""
        ...

    def render(
        self,
        one_pager: OnePager,
        destination: Path,
        *,
        paper: Paper = "letter",
        layout: PageLayout | None = None,
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        """Write the document. Must not silently emit a second page."""
        ...

    def page_count(self, document: Path) -> int:
        """Count the pages of a document this engine produced."""
        ...


class WeasyPrintEngine:
    """The engine spec §4 names first, and the one this build does not implement.

    Refusing loudly is the honest behaviour here. WeasyPrint needs pango, cairo, and
    gdk-pixbuf as native libraries; none are present on the target machine, so a renderer
    written against it could not be run or tested — and an unverified second layout that
    silently drifts from `templates/onepager.md` is worse than not having one.

    `preflight` still does the real work spec §4 asks for: it distinguishes "the package is
    not installed" from "the package is installed but its native libraries are missing",
    because those have different fixes and the second one is the confusing one.
    """

    name = "weasyprint"

    def preflight(self) -> list[str]:
        """What is missing, in the order it would have to be fixed."""
        problems: list[str] = []
        try:
            import weasyprint  # noqa: F401
        except ImportError:
            problems.append(
                'WeasyPrint is not installed. Run: pip install -e ".[weasyprint]"'
            )
        except OSError as exc:
            problems.append(
                f"WeasyPrint is installed but its native libraries are missing ({exc}). "
                f"Install the GTK stack: on Windows the GTK3 runtime installer, on macOS "
                f"`brew install pango cairo gdk-pixbuf libffi`, on Debian/Ubuntu "
                f"`apt install libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf-2.0-0`."
            )
        problems.append(
            "The WeasyPrint layout is not implemented in this build. The ReportLab engine "
            "produces the same document; run without --engine, or with --engine reportlab."
        )
        return problems

    def _refuse(self) -> RenderError:
        return RenderError(
            "--engine weasyprint is not available.\n" + "\n".join(self.preflight())
        )

    def overflow(
        self,
        one_pager: OnePager,
        layout: PageLayout,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> float:
        raise self._refuse()

    def render(
        self,
        one_pager: OnePager,
        destination: Path,
        *,
        paper: Paper = "letter",
        layout: PageLayout | None = None,
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        raise self._refuse()

    def page_count(self, document: Path) -> int:
        raise self._refuse()


def get_engine(name: str) -> Renderer:
    """Build an engine by name, refusing an unknown one with the list of real ones."""
    from deckpager.render.onepager import OnePagerRenderer

    if name == "reportlab":
        return OnePagerRenderer()
    if name == "weasyprint":
        return WeasyPrintEngine()
    raise RenderError(
        f"Unknown --engine {name!r}. Choose one of: {', '.join(ENGINE_NAMES)}."
    )


def default_engine() -> Renderer:
    """The engine used when none is asked for: the one that always works."""
    from deckpager.render.onepager import OnePagerRenderer

    return OnePagerRenderer()
