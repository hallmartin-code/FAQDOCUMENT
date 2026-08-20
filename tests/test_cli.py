"""CLI surface and exit codes."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from deckpager import __version__
from deckpager.cli import ExitCode, app
from deckpager.doctor import CheckResult
from deckpager.config import reset_settings_cache

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == ExitCode.OK
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "check" in result.stdout


def test_check_fails_without_api_key() -> None:
    result = runner.invoke(app, ["check"])
    assert result.exit_code == ExitCode.CONFIG_ERROR


def test_check_passes_when_all_required_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit code is driven by the checks, not by this machine's GTK install."""
    healthy = [
        CheckResult(name="anthropic api key", ok=True, detail="set"),
        CheckResult(name="libreoffice (soffice)", ok=False, detail="not found", required=False, fix="install it"),
    ]
    monkeypatch.setattr("deckpager.cli.run_checks", lambda _settings: healthy)
    reset_settings_cache()
    result = runner.invoke(app, ["check"])
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "anthropic api key" in result.stdout
    assert "1 warning" in result.stdout


def test_check_escapes_square_brackets_in_fix_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extras like deckpager[reportlab] must survive rich markup parsing."""
    results = [
        CheckResult(name="reportlab", ok=False, detail="not installed", required=False, fix='pip install "deckpager[reportlab]"'),
        CheckResult(name="anthropic api key", ok=True, detail="set"),
    ]
    monkeypatch.setattr("deckpager.cli.run_checks", lambda _settings: results)
    result = runner.invoke(app, ["check"])
    flattened = "".join(result.stdout.split())
    assert "reportlab]" in flattened
