"""deckpager command line interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from deckpager import __version__
from deckpager.errors import DeckpagerError

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
NoImagesOpt = Annotated[
    bool,
    typer.Option("--no-images", help="Skip slide rasterization; analyze text only."),
]


def _fail(exc: DeckpagerError) -> None:
    """Print a human-readable error and exit with the error's code."""
    err_console.print(f"[bold red]error:[/bold red] {exc}")
    raise typer.Exit(code=exc.exit_code)


@app.command()
def analyze(
    deck: Annotated[Path, typer.Argument(help="Path to the pitch deck (.pdf or .pptx).")],
    out: StemOpt = None,
    paper: PaperOpt = "letter",
    context: ContextOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    no_images: NoImagesOpt = False,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Override where the analysis JSON is written."),
    ] = None,
) -> None:
    """Analyze a deck: write the one-pager PDF and the analysis JSON."""
    from deckpager.pipeline import run_pipeline

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
def render(
    assessment: Annotated[Path, typer.Argument(help="Path to an assessment JSON file.")],
    out: StemOpt = None,
    paper: PaperOpt = "letter",
) -> None:
    """Render the one-pager from an existing assessment JSON. No model call."""
    from deckpager.pipeline import run_render

    try:
        run_render(assessment=assessment, out_stem=out, paper=paper, console=console)
    except DeckpagerError as exc:
        _fail(exc)


@app.command()
def providers(model: ModelOpt = None) -> None:
    """List the configured LLM backends and check whether each is usable."""
    from rich.table import Table

    from deckpager.config import load_settings
    from deckpager.llm.registry import describe_all

    try:
        settings = load_settings(model=model)
        statuses = describe_all(settings)
    except DeckpagerError as exc:
        _fail(exc)
        return

    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("status")
    for status in statuses:
        mark = "[green]OK[/green]" if status.ready else "[yellow]--[/yellow]"
        detail = status.detail
        if status.notes:
            detail = f"{detail} [dim]({'; '.join(status.notes)})[/dim]"
        name = f"[bold]{status.name}[/bold]" if "selected" in status.notes else status.name
        table.add_row(mark, name, status.model or "-", detail)
    console.print(table)
    console.print(
        f"\n[dim]Selected: {settings.provider} (--provider > DECKPAGER_PROVIDER > "
        f"config/default.toml). Vision-capable backends attach slide images.[/dim]"
    )


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
