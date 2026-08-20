"""deckpager command line interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from deckpager import __version__
from deckpager.errors import DeckpagerError
from deckpager.ingest.models import Deck

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
NoImagesOpt = Annotated[
    bool,
    typer.Option("--no-images", help="Skip slide rasterization; analyze text only."),
]


def _fail(exc: DeckpagerError) -> None:
    """Print a human-readable error and exit with the error's code."""
    err_console.print(f"[bold red]error:[/bold red] {exc}")
    raise typer.Exit(code=exc.exit_code)


def _print_deck_summary(deck: Deck) -> None:
    """Print what ingestion actually read, one row per slide.

    This is the whole payload of --dry-run: before spending money on a deck, an analyst
    can see whether the text came out, which slides will be sent as pictures, and what
    the budgets threw away.
    """
    from rich.table import Table

    header = (
        f"[bold]{deck.source_path.name}[/bold]  ·  {deck.source_format.upper()}  ·  "
        f"{deck.slide_count} slides"
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
    out: StemOpt = None,
    paper: PaperOpt = "letter",
    context: ContextOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    no_images: NoImagesOpt = False,
    dry_run: DryRunOpt = False,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Override where the analysis JSON is written."),
    ] = None,
) -> None:
    """Turn a deck into the one-pager PDF and its analysis JSON."""
    from deckpager.config import load_settings
    from deckpager.pipeline import ingest_deck, run_pipeline

    if dry_run:
        try:
            settings = load_settings(provider=provider, model=model, no_images=no_images)
            _print_deck_summary(ingest_deck(deck, settings))
        except DeckpagerError as exc:
            _fail(exc)
        return

    try:
        run_pipeline(
            deck=deck,
            out_stem=out,
            paper=paper,
            context=context,
            provider=provider,
            model=model,
            no_images=no_images,
            json_out=json_out,
            console=console,
        )
    except DeckpagerError as exc:
        _fail(exc)


@app.command()
def redraw(
    assessment: Annotated[Path, typer.Argument(help="Path to an assessment JSON file.")],
    out: StemOpt = None,
    paper: PaperOpt = "letter",
) -> None:
    """Re-render the one-pager from an existing analysis JSON. No model call."""
    from deckpager.pipeline import run_render

    try:
        run_render(assessment=assessment, out_stem=out, paper=paper, console=console)
    except DeckpagerError as exc:
        _fail(exc)

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
