"""CLI surface: the command set, the dry run, and the exit contract.

No test here makes a model call. `--dry-run` must not need an API key at all, which is
half the point of it — an analyst can check what a deck parses to before deciding whether
the deck is worth paying to analyze.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from deckpager import paths
from deckpager.cli import app
from deckpager.errors import EXIT_BAD_INPUT

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """No developer key, no .env, no config override leaks into these assertions."""
    for name in list(os.environ):
        if name.startswith("DECKPAGER_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    paths.clear_caches()
    yield
    paths.clear_caches()


class TestCommandSurface:
    def test_the_spec_commands_are_all_present(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in ("render", "check", "redraw"):
            assert command in result.output

    def test_render_takes_a_deck_not_an_analysis(self) -> None:
        """Spec §10: `render DECK_PATH`. The JSON re-render lives at `redraw`."""
        result = runner.invoke(app, ["render", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output


class TestDryRun:
    def test_reads_a_pdf_without_an_api_key(self, sample_pdf: Path) -> None:
        result = runner.invoke(app, ["render", str(sample_pdf), "--dry-run"])
        assert result.exit_code == 0
        assert "5 slides" in result.output
        assert "Helion Bio" in result.output

    def test_reads_a_pptx_and_counts_its_speaker_notes(self, sample_pptx: Path) -> None:
        result = runner.invoke(app, ["render", str(sample_pptx), "--dry-run"])
        assert result.exit_code == 0
        assert "PPTX" in result.output

    def test_flags_image_dominant_slides(self, image_heavy_pdf: Path) -> None:
        result = runner.invoke(app, ["render", str(image_heavy_pdf), "--dry-run"])
        assert result.exit_code == 0
        assert "image-dominant" in result.output
        assert "chart" in result.output

    def test_reports_that_a_pdf_goes_natively(self, sample_pdf: Path) -> None:
        result = runner.invoke(app, ["render", str(sample_pdf), "--dry-run"])
        assert "natively" in result.output

    def test_never_calls_the_model(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guarantee that makes --dry-run free. Asserted, not assumed."""
        import deckpager.analysis.client as client

        def explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("--dry-run must not construct an analyzer")

        monkeypatch.setattr(client.AnthropicAnalyzer, "__init__", explode)
        result = runner.invoke(app, ["render", str(sample_pdf), "--dry-run"])
        assert result.exit_code == 0


class TestExitCodes:
    def test_a_missing_deck_is_bad_input(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["render", str(tmp_path / "absent.pdf"), "--dry-run"])
        assert result.exit_code == EXIT_BAD_INPUT

    def test_an_unsupported_format_is_bad_input(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes.txt"
        notes.write_text("just some text", encoding="utf-8")
        result = runner.invoke(app, ["render", str(notes), "--dry-run"])
        assert result.exit_code == EXIT_BAD_INPUT

    def test_a_corrupt_pdf_is_bad_input(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 and then nothing that parses")
        result = runner.invoke(app, ["render", str(broken), "--dry-run"])
        assert result.exit_code == EXIT_BAD_INPUT

    def test_failures_print_one_line_not_a_traceback(self, tmp_path: Path) -> None:
        """Spec §11: a user-caused failure is a sentence, not a stack trace."""
        result = runner.invoke(app, ["render", str(tmp_path / "absent.pdf"), "--dry-run"])
        assert "Traceback" not in result.output
        assert "error:" in result.output
