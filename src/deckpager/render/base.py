"""The renderer contract, and the registry of engines that satisfy it.

The renderer sits behind a Protocol so a second engine can exist, and a missing native
dependency is reported with an actionable install message rather than a stack trace. This
module is both halves.

One engine is implemented: ReportLab, which is pure Python and therefore works wherever
deckpager installs. WeasyPrint is registered but not built — see `WeasyPrintEngine` for
why, and for what it tells the user instead of failing obscurely.

The contract lost a method when the one-pager became an FAQ. `overflow` existed to measure
how far content exceeded a single page so the fitting ladder could truncate until it fit;
a twenty-question document paginates instead, so nothing is truncated and nothing needs
measuring. `page_count` remains, because the tests still assert on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from deckpager.errors import RenderError
from deckpager.models import DEFAULT_MIN_CONFIDENCE, Faq

Paper = Literal["letter", "a4"]
EngineName = Literal["reportlab", "weasyprint"]

#: Registry order is preference order: the first usable engine is the default.
ENGINE_NAMES: tuple[str, ...] = ("reportlab", "weasyprint")


@runtime_checkable
class Renderer(Protocol):
    """Anything that can turn an FAQ into a document."""

    name: str

    def preflight(self) -> list[str]:
        """Problems that would stop this engine rendering; empty when it is usable.

        Called before any paid work, so a missing native dependency is reported once with
        a fix rather than surfacing as a stack trace after an extraction has been bought.
        """
        ...

    def render(
        self,
        faq: Faq,
        destination: Path,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        """Write the document."""
        ...

    def page_count(self, document: Path) -> int:
        """Count the pages of a document this engine produced."""
        ...


class WeasyPrintEngine:
    """The engine the original spec named first, and the one this build does not implement.

    Refusing loudly is the honest behaviour here. WeasyPrint needs pango, cairo, and
    gdk-pixbuf as native libraries; none are present on the target machine, so a renderer
    written against it could not be run or tested — and an unverified second layout that
    silently drifts from the ReportLab one is worse than not having one.

    `preflight` still does the real work: it distinguishes "the package is not installed"
    from "the package is installed but its native libraries are missing", because those
    have different fixes and the second one is the confusing one.
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

    def render(
        self,
        faq: Faq,
        destination: Path,
        *,
        paper: Paper = "letter",
        threshold: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Path:
        raise self._refuse()

    def page_count(self, document: Path) -> int:
        raise self._refuse()


def get_engine(name: str) -> Renderer:
    """Build an engine by name, refusing an unknown one with the list of real ones."""
    from deckpager.render.faq import FaqRenderer

    if name == "reportlab":
        return FaqRenderer()
    if name == "weasyprint":
        return WeasyPrintEngine()
    raise RenderError(
        f"Unknown --engine {name!r}. Choose one of: {', '.join(ENGINE_NAMES)}."
    )


def default_engine() -> Renderer:
    """The engine used when none is asked for: the one that always works."""
    from deckpager.render.faq import FaqRenderer

    return FaqRenderer()
