"""deckpager command line interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from deckpager import __version__
from deckpager.errors import EXIT_BAD_INPUT, DeckpagerError
from deckpager.ingest.models import Deck
from deckpager.models import DEFAULT_MIN_CONFIDENCE
from deckpager.pipeline import BatchEntry, RunResult
from deckpager.render.base import get_engine
from deckpager.render.onepager import PAGE_SIZES, RENDERED_FIELDS, Paper

# Windows consoles still default to a legacy code page, which turns the em-dashes and
# arrows in prompts, warnings, and company names into replacement characters — or raises
# UnicodeEncodeError outright. Force UTF-8 before anything writes.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="deckpager",
    help="Investment-grade pitch deck due diligence engine.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)

PaperOpt = Annotated[
    str,
    typer.Option("--paper", help="Page size for the one-pager: letter or a4."),
]
StemOpt = Annotated[
    Path | None,
    typer.Option("-o", "--out", help="Output stem; _onepager.pdf and _analysis.json are appended."),
]
ContextOpt = Annotated[
    str | None,
    typer.Option("--context", help="Operator context, e.g. 'Series A, biotech, $12M raise'."),
]
ModelOpt = Annotated[
    str | None,
    typer.Option("--model", help="Override the model ID (default from DECKPAGER_MODEL)."),
]
ProviderOpt = Annotated[
    str | None,
    typer.Option(
        "--provider",
        help="LLM backend: anthropic, openai, ollama, or fake. Overrides DECKPAGER_PROVIDER.",
    ),
]
DryRunOpt = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Parse the deck and print what was read. No model call, no cost.",
    ),
]
EngineOpt = Annotated[
    str | None,
    typer.Option(
        "--engine",
        help="Render engine: reportlab (default) or weasyprint.",
    ),
]
NoEmailOpt = Annotated[
    bool,
    typer.Option(
        "--no-email",
        help="Do not email the result, even when RESEND_API_KEY is set.",
    ),
]
NoCacheOpt = Annotated[
    bool,
    typer.Option(
        "--no-cache",
        help="Ignore the extraction cache and pay for a fresh call.",
    ),
]
NoImagesOpt = Annotated[
    bool,
    typer.Option("--no-images", help="Skip slide rasterization; analyze text only."),
]


def _fail(exc: DeckpagerError) -> None:
    """Print a human-readable error and exit with the error's code."""
    err_console.print(f"[bold red]error:[/bold red] {exc}")
    raise typer.Exit(code=exc.exit_code)


def _paper(value: str) -> Paper:
    """Validate --paper before anything expensive runs, and narrow it for the renderer."""
    if value not in PAGE_SIZES:
        err_console.print(
            f"[bold red]error:[/bold red] Unknown --paper {value!r}. "
            f"Choose one of: {', '.join(sorted(PAGE_SIZES))}."
        )
        raise typer.Exit(code=EXIT_BAD_INPUT)
    return cast(Paper, value)

def _announce_schema_retry(errors: str) -> None:
    """Say when the model failed validation and is being given one correction turn."""
    err_console.print("[yellow]schema validation failed; retrying once with the errors fed back:[/yellow]")
    err_console.print(f"[dim]{escape(errors)}[/dim]")

def _print_deck_summary(deck: Deck) -> None:
    """Print what ingestion actually read, one row per slide.

    This is the whole payload of --dry-run: before spending money on a deck, an analyst
    can see whether the text came out, which slides will be sent as pictures, and what
    the budgets threw away.
    """
    from rich.table import Table

    header = (
        f"[bold]{deck.source_path.name}[/bold]  ·  {deck.source_format.upper()}  ·  "
        f"{deck.slide_count} {'sections' if deck.source_format == 'docx' else 'slides'}"
    )
    if deck.raw_pdf_b64 is not None:
        header += "  ·  sent natively as a PDF document"
    console.print(header)

    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("title")
    table.add_column("chars", justify="right")
    table.add_column("notes", justify="right")
    table.add_column("image")
    table.add_column("flags")
    for slide in deck.slides:
        flags = []
        if slide.image_dominant:
            flags.append("[yellow]image-dominant[/yellow]")
        if slide.has_chart:
            flags.append("chart")
        table.add_row(
            str(slide.index),
            escape((slide.title or "-")[:44]),
            str(len(slide.text)),
            str(len(slide.speaker_notes or "")) if slide.speaker_notes else "-",
            f"{slide.asset.byte_size / 1000:.0f} kB" if slide.asset else "-",
            " ".join(flags),
        )
    console.print(table)

    images = sum(1 for s in deck.slides if s.asset is not None)
    text_chars = sum(len(s.text) for s in deck.slides)
    console.print(
        f"[dim]{text_chars:,} characters of text · {images} slide image(s) · "
        f"{deck.image_bytes / 1_000_000:.1f} MB of images[/dim]"
    )
    for warning in deck.warnings:
        err_console.print(f"[yellow]warning:[/yellow] {escape(warning)}")


@app.command()
def render(
    deck: Annotated[
        Path, typer.Argument(help="Path to the pitch deck (.pdf, .pptx, or .ppt).")
    ],
    out: Annotated[
        Path | None,
        typer.Option("-o", "--out", help="Output PDF path. Default: <Company>-onepager.pdf"),
    ] = None,
    model: ModelOpt = None,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Also write the extracted one-pager JSON here."),
    ] = None,
    no_cache: NoCacheOpt = False,
    paper: PaperOpt = "letter",
    min_confidence: Annotated[
        float,
        typer.Option("--min-confidence", help="Below this, a field is flagged."),
    ] = DEFAULT_MIN_CONFIDENCE,
    no_images: NoImagesOpt = False,
    engine: EngineOpt = None,
    no_email: NoEmailOpt = False,
    dry_run: DryRunOpt = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Show the full traceback on failure."),
    ] = False,
) -> None:
    """Turn a deck into the one-pager PDF and its JSON."""
    from deckpager.config import load_settings
    from deckpager.ingest import ingest_deck
    from deckpager.pipeline import run

    try:
        settings = load_settings(model=model, no_images=no_images)

        if dry_run:
            _print_deck_summary(ingest_deck(deck, settings))
            return

        result = run(
            deck,
            settings=settings,
            out_pdf=out,
            out_json=json_out,
            paper=_paper(paper),
            min_confidence=min_confidence,
            use_cache=not no_cache,
            on_stage=lambda name: console.print(f"[dim]{name}[/dim] {deck.name}"),
            on_retry=_announce_schema_retry,
            send_email=not no_email,
            engine=engine or "reportlab",
        )
    except DeckpagerError as exc:
        if verbose:
            raise
        _fail(exc)
        return

    _report(result, min_confidence)


def _report(result: RunResult, min_confidence: float) -> None:
    """Everything the operator needs to judge the run, after the files are written."""
    provenance = result.one_pager.provenance
    for warning in provenance.ingest_warnings + provenance.citation_warnings:
        err_console.print(f"[yellow]warning:[/yellow] {escape(warning)}")
    for cut in result.truncations:
        err_console.print(f"[yellow]fitted:[/yellow] {escape(cut)} (to keep it to one page)")

    weak = [
        name
        for name in result.one_pager.low_confidence_fields(min_confidence)
        if name in set(RENDERED_FIELDS)
    ]
    if weak:
        console.print(
            f"[yellow]{len(weak)} field(s) below {min_confidence:.0%} confidence:[/yellow] "
            f"{escape(', '.join(sorted(weak)))}"
        )
    if not result.one_pager.is_pitch_deck:
        err_console.print("[yellow]This document does not read as a pitch deck.[/yellow]")

    console.print(f"[green]wrote[/green] {result.pdf}")
    console.print(f"[green]wrote[/green] {result.json}")
    if result.email is not None:
        if result.email.sent:
            console.print(f"[green]sent[/green] {escape(result.email.detail)}")
        else:
            err_console.print(f"[yellow]email:[/yellow] {escape(result.email.detail)}")
    console.print(f"[dim]{result.summary}[/dim]")

@app.command()
def batch(
    directory: Annotated[
        Path, typer.Argument(help="Directory of decks. Not searched recursively.")
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", help="Where the one-pagers are written."),
    ],
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="How many decks to analyze at once."),
    ] = 3,
    model: ModelOpt = None,
    paper: PaperOpt = "letter",
    min_confidence: Annotated[
        float,
        typer.Option("--min-confidence", help="Below this, a field is flagged."),
    ] = DEFAULT_MIN_CONFIDENCE,
    no_cache: NoCacheOpt = False,
    no_images: NoImagesOpt = False,
    engine: EngineOpt = None,
    no_email: NoEmailOpt = False,
) -> None:
    """Analyze every deck in a directory. One bad deck never stops the others."""
    from rich.table import Table

    from deckpager.config import load_settings
    from deckpager.pipeline import run_batch

    try:
        settings = load_settings(model=model, no_images=no_images)
        report = run_batch(
            directory,
            settings=settings,
            out_dir=out_dir,
            concurrency=concurrency,
            paper=_paper(paper),
            min_confidence=min_confidence,
            use_cache=not no_cache,
            send_email=not no_email,
            engine=engine or "reportlab",
            on_start=lambda deck: console.print(f"[dim]start[/dim] {deck.name}"),
            on_finish=_announce_batch_entry,
        )
    except DeckpagerError as exc:
        _fail(exc)
        return

    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("")
    table.add_column("deck")
    table.add_column("company")
    table.add_column("note")
    for entry in report.entries:
        if entry.result is None:
            table.add_row(
                "[red]XX[/red]", escape(entry.deck.name), "-", escape(entry.error or "")
            )
            continue
        one_pager = entry.result.one_pager
        notes = []
        if entry.result.truncations:
            notes.append(f"{len(entry.result.truncations)} fitted")
        if not one_pager.is_pitch_deck:
            notes.append("not a pitch deck")
        table.add_row(
            "[green]OK[/green]",
            escape(entry.deck.name),
            escape(one_pager.company_name.value or "-"),
            escape(", ".join(notes)),
        )
    console.print(table)

    console.print()
    console.print(
        f"[bold]{len(report.succeeded)} of {len(report.entries)}[/bold] deck(s) "
        f"in {report.seconds:.0f}s · ~${report.cost_usd:.2f} · wrote to {out_dir}"
    )
    if report.failed:
        err_console.print(
            f"[bold red]{len(report.failed)} deck(s) failed.[/bold red] "
            f"The rest were written."
        )
        raise typer.Exit(code=report.exit_code)


def _announce_batch_entry(entry: BatchEntry) -> None:
    """One line per deck as it finishes, so a long batch is not a silent wait."""
    if entry.result is None:
        err_console.print(f"[red]failed[/red] {entry.deck.name}: {escape(entry.error or '')}")
        return
    company = entry.result.one_pager.company_name.value or entry.deck.stem
    console.print(f"[green]done[/green] {escape(company)} — {entry.result.pdf.name}")

@app.command()
def extract(
    deck: Annotated[
        Path, typer.Argument(help="Path to the pitch deck (.pdf, .pptx, or .ppt).")
    ],
    json_out: Annotated[
        Path | None,
        typer.Option("--json", "-o", help="Where to write the one-pager JSON."),
    ] = None,
    model: ModelOpt = None,
    no_images: NoImagesOpt = False,
    no_cache: NoCacheOpt = False,
) -> None:
    """Extract a deck to one-pager JSON. No PDF.

    Transitional: this is the Phase 3 deliverable, so the extraction can be reviewed
    before a renderer exists to hide it. Phase 5 folds it into `render`.
    """
    import time

    from deckpager.config import load_settings
    from deckpager.extract.pipeline import cost_line, extract_one_pager

    started = time.monotonic()
    try:
        settings = load_settings(model=model, no_images=no_images)
        one_pager = extract_one_pager(
            deck,
            settings=settings,
            use_cache=not no_cache,
            on_stage=lambda name: console.print(f"[dim]{name}[/dim] {deck.name}"),
            on_retry=_announce_schema_retry,
        )
    except DeckpagerError as exc:
        _fail(exc)
        return

    destination = json_out or deck.with_name(f"{deck.stem}-onepager.json")
    destination.write_text(
        one_pager.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    for warning in one_pager.provenance.ingest_warnings:
        err_console.print(f"[yellow]warning:[/yellow] {escape(warning)}")

    weak = one_pager.low_confidence_fields()
    if weak:
        console.print(
            f"[yellow]{len(weak)} field(s) below the confidence threshold:[/yellow] "
            f"{escape(', '.join(sorted(weak)))}"
        )
    if not one_pager.is_pitch_deck:
        err_console.print("[yellow]This document does not read as a pitch deck.[/yellow]")

    console.print(f"[green]wrote[/green] {destination}")
    console.print(f"[dim]{cost_line(one_pager, time.monotonic() - started)}[/dim]")

@app.command()
def redraw(
    one_pager: Annotated[
        Path, typer.Argument(help="Path to a one-pager JSON file written by `extract`.")
    ],
    out: Annotated[
        Path | None,
        typer.Option("-o", "--out", help="Output PDF path."),
    ] = None,
    paper: PaperOpt = "letter",
    min_confidence: Annotated[
        float,
        typer.Option("--min-confidence", help="Below this, a field is flagged."),
    ] = DEFAULT_MIN_CONFIDENCE,
    engine: EngineOpt = None,
) -> None:
    """Render the one-pager PDF from an existing one-pager JSON. No model call.

    The fast loop for layout work: re-rendering costs nothing, so the page can be
    iterated on without paying for an extraction each time.
    """
    import json

    from deckpager.models import OnePager
    from deckpager.render.onepager import fit_and_render

    try:
        payload = json.loads(one_pager.read_text(encoding="utf-8"))
    except OSError as exc:
        err_console.print(f"[bold red]error:[/bold red] Could not read {one_pager}: {exc}")
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc
    except json.JSONDecodeError as exc:
        err_console.print(f"[bold red]error:[/bold red] {one_pager.name} is not valid JSON: {exc}")
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc

    try:
        document = OnePager.model_validate(payload)
    except ValidationError as exc:
        err_console.print(
            f"[bold red]error:[/bold red] {one_pager.name} is not a deckpager one-pager "
            f"({exc.error_count()} schema problem(s)). Re-run `deckpager extract`."
        )
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc

    destination = out or one_pager.with_name(f"{one_pager.stem}.pdf")
    try:
        written, cuts = fit_and_render(
            document,
            destination,
            paper=_paper(paper),
            threshold=min_confidence,
            renderer=get_engine(engine) if engine else None,
        )
    except DeckpagerError as exc:
        _fail(exc)
        return

    for cut in cuts:
        console.print(f"[yellow]fitted:[/yellow] {escape(cut)}")
    console.print(f"[green]wrote[/green] {written}")

@app.command()
def schema(
    indent: Annotated[
        int,
        typer.Option("--indent", help="JSON indentation. 0 prints one line."),
    ] = 2,
) -> None:
    """Print the JSON schema the model is held to when extracting a one-pager."""
    import json

    from deckpager.models import tool_schema

    # print(), not console.print(): this is machine output. Rich would wrap long lines
    # and colour the punctuation, and `deckpager schema > schema.json` has to work.
    print(json.dumps(tool_schema(), indent=indent or None))


@app.command()
def check() -> None:
    """Verify this machine can run deckpager: API key, data files, engines, LibreOffice."""
    from rich.markup import escape
    from rich.table import Table

    from deckpager.config import load_settings
    from deckpager.errors import EXIT_CONFIG
    from deckpager.preflight import Status, run_checks

    try:
        settings = load_settings()
    except DeckpagerError as exc:
        _fail(exc)
        return

    results = run_checks(settings)

    marks = {
        Status.OK: "[green]OK[/green]",
        Status.WARN: "[yellow]--[/yellow]",
        Status.FAIL: "[red]XX[/red]",
    }
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("")
    table.add_column("check")
    table.add_column("detail")
    for result in results:
        table.add_row(marks[result.status], result.name, escape(result.detail))
    console.print(table)

    # Fixes print below the table rather than in it: they are shell commands, and a
    # wrapped command inside a table cell is a command you cannot copy.
    for result in (r for r in results if r.fix):
        console.print(f"[dim]{result.name}:[/dim] {escape(result.fix or '')}")

    blocking = [r for r in results if r.blocking]
    if blocking:
        err_console.print()
        err_console.print(
            f"[bold red]{len(blocking)} blocking problem(s).[/bold red] "
            f"deckpager cannot run until they are fixed."
        )
        raise typer.Exit(code=EXIT_CONFIG)
    console.print()
    console.print("[green]Ready.[/green]")

@app.command()
def version() -> None:
    """Print the deckpager version."""
    console.print(f"deckpager {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
