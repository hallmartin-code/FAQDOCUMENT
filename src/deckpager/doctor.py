"""Environment verification behind `deckpager check`.

Every check reports an actionable fix rather than a traceback.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from deckpager.config import Settings

#: Where LibreOffice usually lands, per platform. Checked after $PATH.
_SOFFICE_FALLBACKS: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
)

_SOFFICE_INSTALL_HINT = (
    "install LibreOffice - macOS: `brew install --cask libreoffice`; "
    "Debian/Ubuntu: `sudo apt install libreoffice`; "
    "Windows: `winget install TheDocumentFoundation.LibreOffice`"
)


@dataclass(frozen=True)
class CheckResult:
    """One environment check."""

    name: str
    ok: bool
    detail: str
    required: bool = True
    fix: str | None = None


def find_soffice(settings: Settings) -> Path | None:
    """Locate the LibreOffice binary, or None if it is not installed."""
    if settings.soffice_path is not None and settings.soffice_path.exists():
        return settings.soffice_path
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in _SOFFICE_FALLBACKS:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info >= (3, 11)
    return CheckResult(
        name="python",
        ok=ok,
        detail=f"{version}",
        fix=None if ok else "deckpager needs Python 3.11 or newer",
    )


def _check_api_key(settings: Settings) -> CheckResult:
    ok = settings.has_api_key()
    return CheckResult(
        name="anthropic api key",
        ok=ok,
        detail="set" if ok else "not set",
        fix=None if ok else "add ANTHROPIC_API_KEY to .env (see .env.example)",
    )


def _check_cache_dir(settings: Settings) -> CheckResult:
    path = settings.cache_dir
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            name="cache dir",
            ok=False,
            detail=f"{path} is not writable ({exc.strerror or exc})",
            fix="set DECKPAGER_CACHE_DIR to a writable directory",
        )
    return CheckResult(name="cache dir", ok=True, detail=str(path))


def _check_soffice(settings: Settings) -> CheckResult:
    found = find_soffice(settings)
    return CheckResult(
        name="libreoffice (soffice)",
        ok=found is not None,
        detail=str(found) if found else "not found",
        required=False,
        fix=None if found else f"only needed for .ppt and .pptx page images - {_SOFFICE_INSTALL_HINT}",
    )


def _check_weasyprint() -> CheckResult:
    try:
        # WeasyPrint prints an installation banner to stdout when GTK is missing.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import weasyprint  # noqa: F401
    except ImportError:
        return CheckResult(
            name="weasyprint",
            ok=False,
            detail="not installed",
            required=False,
            fix="pip install weasyprint",
        )
    except OSError as exc:
        return CheckResult(
            name="weasyprint",
            ok=False,
            detail=f"installed but system libraries missing ({exc})",
            required=False,
            fix=(
                "install pango/cairo/gdk-pixbuf - macOS: `brew install pango`; "
                "Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`; "
                "Windows: install the GTK3 runtime, or run with `--engine reportlab`"
            ),
        )
    return CheckResult(name="weasyprint", ok=True, detail="importable", required=False)


def _check_reportlab() -> CheckResult:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return CheckResult(
            name="reportlab",
            ok=False,
            detail="not installed",
            required=False,
            fix='pip install "deckpager[reportlab]" to enable `--engine reportlab`',
        )
    return CheckResult(name="reportlab", ok=True, detail="importable", required=False)


def run_checks(settings: Settings) -> list[CheckResult]:
    """Run every environment check, in display order."""
    results = [
        _check_python(),
        _check_api_key(settings),
        _check_cache_dir(settings),
        _check_soffice(settings),
        _check_weasyprint(),
        _check_reportlab(),
    ]
    if not any(r.ok for r in results if r.name in {"weasyprint", "reportlab"}):
        results.append(
            CheckResult(
                name="pdf renderer",
                ok=False,
                detail="no usable rendering engine",
                fix="fix weasyprint's system libraries, or `pip install \"deckpager[reportlab]\"`",
            )
        )
    return results


def has_blocking_failure(results: list[CheckResult]) -> bool:
    """True when a required check failed."""
    return any(r.required and not r.ok for r in results)
