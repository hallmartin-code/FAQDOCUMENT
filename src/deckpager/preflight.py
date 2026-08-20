"""Environment checks behind `deckpager check`.

The point is to fail *here*, cheaply, rather than after a deck has been parsed and a paid
model call has already been made. Every check answers one question — "will this machine
get through a run?" — and every failure carries the command that fixes it.

Checks are graded, not boolean. A missing GTK stack is not a broken install: it means the
optional WeasyPrint engine is unavailable while the default ReportLab engine is fine. Only
`Status.FAIL` results block a run, and only those set the exit code.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from enum import Enum

from deckpager.config import Settings
from deckpager.errors import ConfigError
from deckpager.ingest.legacy_ppt import find_soffice, install_hint
from deckpager.paths import config_dir

#: The interpreter floor, mirroring `requires-python` in pyproject.toml. Held in a
#: constant rather than compared inline so it reads as the project's declared minimum,
#: not as a version block to be linted away.
MIN_PYTHON: tuple[int, int] = (3, 11)

#: Per-platform install line for the GTK libraries WeasyPrint links against.
_GTK_INSTALL: dict[str, str] = {
    "Windows": (
        "install the GTK3 runtime from "
        "https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases"
    ),
    "Darwin": "brew install pango cairo gdk-pixbuf libffi",
    "Linux": "sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf-2.0-0",
}


class Status(Enum):
    """How a check came out."""

    OK = "ok"
    #: An optional capability is unavailable. Core runs still work.
    WARN = "warn"
    #: A run cannot succeed until this is fixed.
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """One environment check, its verdict, and how to fix it."""

    name: str
    status: Status
    detail: str
    fix: str | None = None

    @property
    def blocking(self) -> bool:
        """Whether this result should stop the run."""
        return self.status is Status.FAIL


def _install_line(table: dict[str, str]) -> str:
    """The install command for this platform, falling back to naming all of them."""
    return table.get(platform.system()) or "; ".join(f"{k}: {v}" for k, v in table.items())


def check_python() -> CheckResult:
    """Refuse to pretend a too-old interpreter will work."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < MIN_PYTHON:
        return CheckResult(
            "python",
            Status.FAIL,
            f"{version} — deckpager needs "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer",
            "Install Python 3.11+ and recreate the virtualenv.",
        )
    return CheckResult("python", Status.OK, version)


def check_api_key(settings: Settings) -> CheckResult:
    """Confirm a key is resolvable without ever printing or logging it."""
    try:
        settings.require_api_key("anthropic")
    except ConfigError:
        return CheckResult(
            "anthropic api key",
            Status.FAIL,
            "ANTHROPIC_API_KEY is not set",
            "Add ANTHROPIC_API_KEY=sk-ant-... to .env, or export it in your shell.",
        )
    # Only the source is reported. The key itself never reaches the terminal or a log.
    source = "environment" if os.environ.get("ANTHROPIC_API_KEY") else ".env"
    return CheckResult("anthropic api key", Status.OK, f"set (from {source})")


def check_email(settings: Settings) -> CheckResult:
    """Report whether results are emailed, and to whom. Never prints the Resend key."""
    from deckpager.mailer import describe

    summary = describe(settings)
    status = Status.OK if settings.email_enabled else Status.WARN
    fix = (
        None
        if settings.email_enabled
        else "Set RESEND_API_KEY to email each result. The sending domain must be verified with Resend."
    )
    return CheckResult("result email", status, summary, fix)

def check_reportlab() -> CheckResult:
    """The default render engine. Pure Python, so this is an import check."""
    from deckpager.render import OnePagerRenderer

    problems = OnePagerRenderer().preflight()
    if problems:
        return CheckResult(
            "render engine: reportlab",
            Status.FAIL,
            "; ".join(problems),
            "Run `pip install -e .` in the project root.",
        )
    return CheckResult("render engine: reportlab", Status.OK, "available (default engine)")


def check_weasyprint() -> CheckResult:
    """The optional engine.

    Two distinct failures, reported distinctly: the package is not installed (the normal
    case — it lives in an extra), or it is installed but its native GTK libraries are not,
    which is what an OSError at import time means. The second is the one that used to
    surface as an unreadable stack trace mid-render.
    """
    try:
        import weasyprint  # noqa: F401
    except ImportError:
        return CheckResult(
            "render engine: weasyprint",
            Status.WARN,
            "not installed (optional)",
            'Run `pip install -e ".[weasyprint]"` to add it.',
        )
    except OSError as exc:
        return CheckResult(
            "render engine: weasyprint",
            Status.WARN,
            f"installed, but its native libraries are missing ({exc})",
            _install_line(_GTK_INSTALL),
        )
    return CheckResult("render engine: weasyprint", Status.OK, "available")


def check_soffice() -> CheckResult:
    """LibreOffice is only needed for legacy .ppt decks, so its absence is a warning."""
    found = find_soffice()
    if found is None:
        return CheckResult(
            "libreoffice (.ppt support)",
            Status.WARN,
            "not found — .pdf and .pptx still work",
            install_hint(),
        )
    return CheckResult("libreoffice (.ppt support)", Status.OK, found)


def check_cache() -> CheckResult:
    """Report where extractions are cached, and whether that directory is writable.

    Worth printing even when it is fine: what lands there is derived from confidential
    decks, so an operator should be able to see the path without reading the source.
    """
    from deckpager.cache import CACHE_DIR_ENV, ExtractionCache

    cache = ExtractionCache()
    entries = len(list(cache.root.glob("*/*.json"))) if cache.root.is_dir() else 0
    probe = cache.put("preflight" + "0" * 55, {"probe": True})
    if not probe:
        return CheckResult(
            "extraction cache",
            Status.WARN,
            f"{cache.root} is not writable; every run will pay for extraction",
            f"Point {CACHE_DIR_ENV} at a writable directory.",
        )
    cache.path_for("preflight" + "0" * 55).unlink(missing_ok=True)
    return CheckResult("extraction cache", Status.OK, f"{cache.root} ({entries} entries)")


def check_data_dirs() -> list[CheckResult]:
    """The operator-editable config/ directory must be locatable."""
    results: list[CheckResult] = []
    for label, resolve in (("config/", config_dir),):
        try:
            results.append(CheckResult(label, Status.OK, str(resolve())))
        except ConfigError as exc:
            results.append(CheckResult(label, Status.FAIL, str(exc).splitlines()[0], None))
    return results


def run_checks(settings: Settings) -> list[CheckResult]:
    """Every check, in the order `deckpager check` prints them."""
    return [
        check_python(),
        check_api_key(settings),
        *check_data_dirs(),
        check_cache(),
        check_email(settings),
        check_reportlab(),
        check_weasyprint(),
        check_soffice(),
    ]
