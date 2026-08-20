"""Environment preflight: the graded checks and the `deckpager check` exit contract."""

from __future__ import annotations

import builtins
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from deckpager import paths, preflight
from deckpager.cli import app
from deckpager.config import Settings
from deckpager.errors import EXIT_CONFIG
from deckpager.preflight import Status, check_api_key, check_python, run_checks

SECRET = "sk-ant-do-not-print-me"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Same isolation as the config suite: no developer key or .env leaks in."""
    for name in list(os.environ):
        if name.startswith("DECKPAGER_"):
            monkeypatch.delenv(name, raising=False)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    paths.clear_caches()
    yield
    paths.clear_caches()


def test_python_version_passes_on_the_running_interpreter() -> None:
    assert check_python().status is Status.OK


def test_missing_api_key_is_blocking_and_names_the_variable() -> None:
    result = check_api_key(Settings())
    assert result.status is Status.FAIL
    assert result.blocking
    assert "ANTHROPIC_API_KEY" in result.detail


def test_present_api_key_is_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key's presence is reported; the key itself must not appear anywhere."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    result = check_api_key(Settings())
    assert result.status is Status.OK
    assert SECRET not in result.detail
    assert SECRET not in (result.fix or "")


def test_absent_libreoffice_warns_rather_than_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """.ppt is one format of three — its converter being missing must not fail a run."""
    monkeypatch.setattr(preflight, "find_soffice", lambda: None)
    result = preflight.check_soffice()
    assert result.status is Status.WARN
    assert not result.blocking
    assert result.fix


def test_absent_weasyprint_warns_and_points_at_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def missing(name: str, *args: object, **kwargs: object) -> object:
        if name == "weasyprint":
            raise ImportError("No module named 'weasyprint'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", missing)
    result = preflight.check_weasyprint()
    assert result.status is Status.WARN
    assert "weasyprint" in (result.fix or "")


def test_missing_native_libraries_are_reported_as_a_warning_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError at import is the GTK-not-installed case, and it must not escape."""
    real_import = builtins.__import__

    def no_gtk(name: str, *args: object, **kwargs: object) -> object:
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_gtk)
    result = preflight.check_weasyprint()
    assert result.status is Status.WARN
    assert "libgobject" in result.detail


def test_reportlab_the_default_engine_is_available() -> None:
    assert preflight.check_reportlab().status is Status.OK


def test_check_exits_with_the_config_code_when_something_blocks() -> None:
    """No API key set by the fixture, so `check` must refuse with exit code 4."""
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == EXIT_CONFIG


def test_check_exits_zero_when_only_optional_pieces_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(preflight, "find_soffice", lambda: None)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0
    assert SECRET not in result.output


def test_run_checks_covers_every_check_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    names = [r.name for r in run_checks(Settings())]
    assert names == sorted(set(names), key=names.index)  # no duplicates
    assert {"python", "anthropic api key", "config/", "prompts/"} <= set(names)
