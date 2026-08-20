"""Legacy .ppt ingestion, and the one place that knows about LibreOffice.

The binary .ppt format has no maintained Python reader, so the only honest path is to hand
it to LibreOffice and read the .pptx that comes back. That makes `soffice` a hard
requirement for this format and an optional one for everything else, which is why locating
it, naming its install command, and running it all live here rather than in three places.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from deckpager.errors import IngestError
from deckpager.ingest.models import Deck

#: Seconds to wait for LibreOffice before giving up. Conversions of large decks are slow,
#: and a half-finished conversion is worse than a clear timeout.
SOFFICE_TIMEOUT_S = 180

#: Where LibreOffice puts `soffice` when it is not on PATH. Checked in order.
SOFFICE_FALLBACKS: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
)

#: Per-platform install line. Spec §11 asks that the .ppt failure name the command for
#: macOS, Linux, and Windows — so when the platform is unrecognized, all three are printed.
SOFFICE_INSTALL: dict[str, str] = {
    "Windows": "winget install TheDocumentFoundation.LibreOffice",
    "Darwin": "brew install --cask libreoffice",
    "Linux": "sudo apt install libreoffice-impress   (or the distro equivalent)",
}


def find_soffice() -> str | None:
    """Locate the LibreOffice CLI, checking PATH then the usual install roots."""
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in SOFFICE_FALLBACKS:
        if Path(candidate).is_file():
            return candidate
    return None


def install_hint() -> str:
    """The install command for this platform, or all of them if it is unrecognized."""
    specific = SOFFICE_INSTALL.get(platform.system())
    if specific:
        return specific
    return "; ".join(f"{system}: {command}" for system, command in SOFFICE_INSTALL.items())


def require_soffice(reason: str) -> str:
    """Return the LibreOffice path, or explain what to install and why it is needed."""
    soffice = find_soffice()
    if soffice is None:
        raise IngestError(
            f"LibreOffice is required to {reason}, and `soffice` was not found.\n"
            f"Install it with: {install_hint()}\n"
            f"Or re-export the deck as .pptx or .pdf and use that instead."
        )
    return soffice


def convert(source: Path, target: str, out_dir: Path, soffice: str) -> Path:
    """Convert `source` to `target` format inside `out_dir`, returning the produced file.

    Raises RuntimeError rather than IngestError: `load_pptx` treats a failed rasterization
    as a degradation and catches it, while `load_ppt` treats a failed conversion as fatal.
    That distinction belongs to the caller, not here.
    """
    result = subprocess.run(  # noqa: S603 - soffice path is resolved, not user-supplied
        [
            soffice,
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(out_dir),
            str(source),
        ],
        capture_output=True,
        timeout=SOFFICE_TIMEOUT_S,
        check=False,
    )
    produced = sorted(out_dir.glob(f"*.{target}"))
    if result.returncode != 0 or not produced:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"no .{target} produced"
        raise RuntimeError(f"LibreOffice conversion failed: {detail}")
    return produced[0]


def load_ppt(path: Path, *, want_images: bool) -> Deck:
    """Read a legacy .ppt by converting it to .pptx first.

    The returned Deck reports the original .ppt path and format; the intermediate file is a
    detail of how it was read, and a slide citation should point at the deck the partner
    was actually sent.
    """
    from deckpager.ingest.pptx import load_pptx

    soffice = require_soffice(f"read the legacy PowerPoint file {path.name}")

    with tempfile.TemporaryDirectory(prefix="deckpager-ppt-") as tmp:
        try:
            converted = convert(path, "pptx", Path(tmp), soffice)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            raise IngestError(
                f"LibreOffice could not convert {path.name}: {exc}\n"
                f"The file may be corrupt or password-protected. Opening it in PowerPoint "
                f"and re-saving as .pptx usually fixes it."
            ) from exc
        deck = load_pptx(converted, want_images=want_images)

    deck.source_path = path
    deck.source_format = "ppt"
    deck.warnings.insert(
        0, f"{path.name} is a legacy .ppt; LibreOffice converted it to .pptx first."
    )
    return deck
