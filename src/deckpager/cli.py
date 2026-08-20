"""`deckpager` command line interface."""

from __future__ import annotations

from enum import IntEnum
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from deckpager import __version__
from deckpager.config import get_settings
from deckpager.doctor import CheckResult, has_blocking_failure, run_checks


class ExitCode(IntEnum):
    """Process exit codes. Documented in the README — do not renumber."""

    OK = 0
    BAD_INPUT = 1
    EXTRACTION_FAILED = 2
    RENDER_FAILED = 3
    CONFIG_ERROR = 4


app = typer.Typer(
    name="deckpager",
    help="Turn an investor pitch deck into a one-page TEN Capital investor one-pager.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"deckpager {__version__}")
        raise typer.Exit(ExitCode.OK)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """TEN Capital deck-to-one-pager tool."""


def _render_check_table(results: list[CheckResult]) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("check")
    table.add_column("status", width=8)
    table.add_column("detail", overflow="fold")
    for result in results:
        if result.ok:
            status = "[green]ok[/green]"
        elif result.required:
            status = "[red]FAIL[/red]"
        else:
            status = "[yellow]warn[/yellow]"
        detail = result.detail if result.ok or not result.fix else f"{result.detail} - {result.fix}"
        table.add_row(escape(result.name), status, escape(detail))
    return table


@app.command()
def check() -> None:
    """Verify the environment: API key, LibreOffice, PDF rendering engine."""
    settings = get_settings()
    results = run_checks(settings)
    console.print(_render_check_table(results))
    if has_blocking_failure(results):
        err_console.print("[red]Environment is not ready.[/red] Fix the FAIL rows above and re-run.")
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    warnings = sum(1 for r in results if not r.ok)
    suffix = f" ({warnings} warning{'s' if warnings != 1 else ''})" if warnings else ""
    console.print(f"[green]Environment ready.[/green]{suffix}")
