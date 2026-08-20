"""Deck in, one-pager out. The whole job, in one place.

`run` is what both callers use — the CLI and the web app — so a deck analysed through the
browser and a deck analysed at a terminal go through byte-identical code. All either caller
supplies is somewhere to put the files and a way to hear about progress.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from deckpager.cache import ExtractionCache
from deckpager.config import Settings
from deckpager.errors import EXIT_BAD_INPUT, DeckpagerError, IngestError, RenderError
from deckpager.extract.client import Extractor
from deckpager.extract.pipeline import cost_line, extract_faq
from deckpager.ingest.router import SUPPORTED_SUFFIXES
from deckpager.mailer import EmailOutcome
from deckpager.models import DEFAULT_MIN_CONFIDENCE, Faq
from deckpager.render.base import Paper, Renderer, default_engine, get_engine

#: Called with a short stage name as each stage begins.
StageHook = Callable[[str], None]


@dataclass
class RunResult:
    """Everything a caller needs to report on a finished run."""

    faq: Faq
    pdf: Path
    json: Path
    seconds: float = 0.0
    truncations: list[str] = field(default_factory=list)
    email: EmailOutcome | None = None

    @property
    def summary(self) -> str:
        """The spec §10 success line."""
        return cost_line(self.faq, self.seconds)


def default_stem(deck_path: Path, faq: Faq) -> str:
    """`{CompanyName}-FAQ`, falling back to the deck's own name.

    Named for the company rather than the upload, because the file lands in a folder of
    other companies' FAQs, and `deck-FAQ.pdf` is not findable there.
    """
    name = faq.company_name.value or deck_path.stem
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in name).strip()
    return f"{cleaned.replace(' ', '_') or deck_path.stem}-FAQ"


#: Guards the filename reservation below. A batch runs several decks at once, and two
#: threads computing the same free name at the same time would both think it was free.
_NAME_LOCK = threading.Lock()


def reserve_stem(directory: Path, stem: str, deck: Path) -> str:
    """Claim a stem no other output is using, and create both files to hold it.

    Outputs are named for the company, which is the right name in a folder of other
    companies. It is not unique: two versions of one deck, or a PDF and a PPTX of the
    same pitch, extract to the same company and the second would overwrite the first
    without saying so. The deck filename disambiguates, and a counter after that.

    Single-deck runs do not use this - re-rendering the same deck should overwrite its
    own previous output, which is what makes a re-run idempotent.
    """
    directory.mkdir(parents=True, exist_ok=True)
    candidates = [stem, f'{stem}-{deck.stem}'] + [
        f'{stem}-{deck.stem}-{n}' for n in range(2, 100)
    ]
    with _NAME_LOCK:
        for candidate in candidates:
            pdf = directory / f'{candidate}.pdf'
            js = directory / f'{candidate}.json'
            if pdf.exists() or js.exists():
                continue
            pdf.touch()
            js.touch()
            return candidate
    raise RenderError(
        f'Could not find a free filename for {stem!r} in {directory}. '
        f'Clear the directory or use a different --out-dir.'
    )

def run(
    deck_path: Path,
    *,
    settings: Settings,
    out_dir: Path | None = None,
    out_pdf: Path | None = None,
    out_json: Path | None = None,
    paper: Paper = "letter",
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    use_cache: bool = True,
    extractor: Extractor | None = None,
    cache: ExtractionCache | None = None,
    now: datetime | None = None,
    on_stage: StageHook | None = None,
    on_retry: Callable[[str], None] | None = None,
    send_email: bool = True,
    engine: str | Renderer | None = None,
    unique_names: bool = False,
) -> RunResult:
    """Ingest, extract, render. Returns the artifacts and what the run cost."""
    started = time.monotonic()
    stage = on_stage or (lambda _name: None)

    faq = extract_faq(
        deck_path,
        settings=settings,
        extractor=extractor,
        cache=cache,
        use_cache=use_cache,
        now=now,
        on_stage=stage,
        on_retry=on_retry,
    )

    stage("rendering")
    directory = out_dir or deck_path.parent
    stem = default_stem(deck_path, faq)
    if unique_names:
        stem = reserve_stem(directory, stem, deck_path)
    pdf_path = out_pdf or directory / f"{stem}.pdf"
    json_path = out_json or directory / f"{stem}.json"

    # `None` means the default engine. fit_and_render used to resolve this; calling
    # renderer.render directly means the default has to be resolved here instead.
    if isinstance(engine, str):
        renderer = get_engine(engine)
    else:
        renderer = engine or default_engine()
    # No fitting ladder: the FAQ paginates, so no answer is ever shortened to fit.
    # `truncations` stays in the result shape because callers and the web UI read it.
    truncations: list[str] = []
    pdf_path = renderer.render(
        faq,
        pdf_path,
        paper=paper,
        threshold=min_confidence,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(faq.model_dump_json(indent=2) + "\n", encoding="utf-8")

    result = RunResult(
        faq=faq,
        pdf=pdf_path,
        json=json_path,
        seconds=time.monotonic() - started,
        truncations=list(truncations),
    )

    # Last, and never fatal. The PDF is the product; the email is a notification about
    # it, and a notification that could fail the run it reports on would be a bad trade.
    if send_email and settings.email_enabled:
        from deckpager import mailer

        stage("emailing")
        try:
            result.email = mailer.send(
                faq, result, settings, threshold=min_confidence
            )
        except Exception as exc:  # noqa: BLE001 - see below
            # mailer.send promises never to raise, and a promise is not a guarantee: a
            # bug in it would otherwise destroy a run that has already succeeded and
            # already been paid for. The artifacts are on disk by this point.
            result.email = EmailOutcome(
                sent=False, detail=f"Emailing the result raised {type(exc).__name__}: {exc}"
            )

    return result

@dataclass
class BatchEntry:
    """One deck in a batch run: what it produced, or why it did not."""

    deck: Path
    result: RunResult | None = None
    error: str | None = None
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None


@dataclass
class BatchReport:
    """Everything a caller needs to report on a directory of decks."""

    entries: list[BatchEntry] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def succeeded(self) -> list[BatchEntry]:
        return [e for e in self.entries if e.ok]

    @property
    def failed(self) -> list[BatchEntry]:
        return [e for e in self.entries if not e.ok]

    @property
    def cost_usd(self) -> float:
        """What the batch actually spent. A cache hit contributes nothing."""
        return sum(
            e.result.faq.provenance.estimated_cost_usd or 0.0
            for e in self.succeeded
            if e.result is not None
        )

    @property
    def exit_code(self) -> int:
        """The worst failure, so a config problem is not hidden by a bad deck.

        Zero only when every deck succeeded: a batch that half-worked is not a success,
        and a script that treats it as one will quietly ship a partial set.
        """
        return max((e.exit_code for e in self.failed), default=0)


def find_decks(directory: Path) -> list[Path]:
    """Every supported deck directly inside `directory`, in a stable order.

    Not recursive. A batch that walked subdirectories would pick up the one-pagers it
    had already written on a previous run, and a deck folder usually has an archive
    subfolder nobody meant to re-analyze.
    """
    from deckpager.ingest.router import SUPPORTED_SUFFIXES

    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def run_batch(
    directory: Path,
    *,
    settings: Settings,
    out_dir: Path,
    concurrency: int = 3,
    on_start: Callable[[Path], None] | None = None,
    on_finish: Callable[[BatchEntry], None] | None = None,
    **run_options: Any,
) -> BatchReport:
    """Analyze every deck in a directory. One bad deck never stops the others.

    Threads rather than processes: the work is a long HTTP call plus some PDF parsing,
    both of which release the GIL, and threads keep the extraction cache and the error
    types shared rather than pickled across a process boundary.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not directory.is_dir():
        raise IngestError(f"Not a directory: {directory}")

    decks = find_decks(directory)
    if not decks:
        raise IngestError(
            f"No decks in {directory}. Looked for: "
            f"{', '.join(sorted(SUPPORTED_SUFFIXES))}."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    def analyze(deck: Path) -> BatchEntry:
        if on_start is not None:
            on_start(deck)
        try:
            result = run(
                deck,
                settings=settings,
                out_dir=out_dir,
                unique_names=True,
                **run_options,
            )
            entry = BatchEntry(deck=deck, result=result)
        except DeckpagerError as exc:
            entry = BatchEntry(deck=deck, error=str(exc), exit_code=exc.exit_code)
        except Exception as exc:  # noqa: BLE001 - one deck must not end the batch
            entry = BatchEntry(
                deck=deck, error=f"Unexpected error: {exc}", exit_code=EXIT_BAD_INPUT
            )
        if on_finish is not None:
            on_finish(entry)
        return entry

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        entries = list(pool.map(analyze, decks))

    return BatchReport(entries=entries, seconds=time.monotonic() - started)
