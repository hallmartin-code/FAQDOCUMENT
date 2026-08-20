"""Batch runs, the engine seam, and the non-English rule.

The three things spec §10, §4, and §11 ask for that the single-deck path does not cover.
No model call is made anywhere here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from deckpager.cache import ExtractionCache
from deckpager.cli import app
from deckpager.config import Settings
from deckpager.errors import EXIT_BAD_INPUT, EXIT_RENDER_FAILED, IngestError, RenderError
from deckpager.extract.client import FakeExtractor
from deckpager.extract.prompts import LANGUAGE_RULE, SYSTEM_PROMPT, build_user_blocks
from deckpager.ingest import ingest_deck
from deckpager.pipeline import find_decks, run_batch
from deckpager.render.base import ENGINE_NAMES, Renderer, WeasyPrintEngine, get_engine
from deckpager.render.faq import FaqRenderer

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


@pytest.fixture
def payload() -> dict[str, Any]:
    data = json.loads((FIXTURES / "sample_faq.json").read_text(encoding="utf-8"))
    data.pop("provenance")
    return data


@pytest.fixture
def settings() -> Settings:
    return Settings(anthropic_api_key="sk-ant-test", resend_api_key=None)


@pytest.fixture
def deck_dir(tmp_path: Path, sample_pdf: Path, sample_pptx: Path) -> Path:
    """Three real decks in one directory, plus a file the router must ignore."""
    directory = tmp_path / "inbox"
    directory.mkdir()
    shutil.copy(sample_pdf, directory / "alpha.pdf")
    shutil.copy(sample_pptx, directory / "beta.pptx")
    shutil.copy(sample_pdf, directory / "gamma.pdf")
    (directory / "notes.txt").write_text("not a deck", encoding="utf-8")
    (directory / "archive").mkdir()
    shutil.copy(sample_pdf, directory / "archive" / "old.pdf")
    return directory


class TestFindDecks:
    def test_it_finds_only_supported_files(self, deck_dir: Path) -> None:
        assert [p.name for p in find_decks(deck_dir)] == ["alpha.pdf", "beta.pptx", "gamma.pdf"]

    def test_it_does_not_recurse(self, deck_dir: Path) -> None:
        """A deck folder usually has an archive nobody meant to re-analyze, and a previous
        run's output would otherwise be picked up as input."""
        assert "old.pdf" not in [p.name for p in find_decks(deck_dir)]

    def test_the_order_is_stable(self, deck_dir: Path) -> None:
        assert find_decks(deck_dir) == find_decks(deck_dir)


class TestBatch:
    def test_every_deck_produces_a_faq(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert len(report.succeeded) == 3
        assert report.exit_code == 0
        assert all(entry.result is not None and entry.result.pdf.is_file() for entry in report.entries)

    def test_one_bad_deck_does_not_stop_the_others(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """The whole point of a batch: a partial result beats an aborted run."""
        (deck_dir / "broken.pdf").write_bytes(b"%PDF-1.4 and then nothing that parses")

        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert len(report.succeeded) == 3
        assert len(report.failed) == 1
        assert report.failed[0].deck.name == "broken.pdf"

    def test_a_partial_batch_is_not_a_success(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """A script that treats a half-finished batch as done ships a partial set."""
        (deck_dir / "broken.pdf").write_bytes(b"%PDF-1.4 nope")
        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert report.exit_code == EXIT_BAD_INPUT

    def test_the_worst_failure_wins_the_exit_code(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """A config problem must not be hidden behind a merely unreadable deck."""

        class Failing(FakeExtractor):
            def extract(self, deck: Any) -> Any:
                raise RenderError("the renderer fell over")

        (deck_dir / "broken.pdf").write_bytes(b"%PDF-1.4 nope")
        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=Failing(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert report.exit_code == EXIT_RENDER_FAILED

    def test_an_unexpected_error_is_contained_to_its_deck(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        class Exploding(FakeExtractor):
            def extract(self, deck: Any) -> Any:
                raise ZeroDivisionError("nobody predicted this")

        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=Exploding(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert len(report.failed) == 3
        assert all("Unexpected error" in (e.error or "") for e in report.failed)

    def test_outputs_are_named_for_the_company_not_the_upload(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """Three decks land in one folder; `alpha-faq.pdf` is not findable there."""
        out = tmp_path / "out"
        run_batch(
            deck_dir,
            settings=settings,
            out_dir=out,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert (out / "Helion_Bio-FAQ.pdf").is_file()

    def test_two_decks_from_one_company_do_not_overwrite_each_other(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """Every fixture extracts to the same company. Without disambiguation the last
        deck analyzed silently replaces the others, and a partner is handed one file
        where three were expected.
        """
        out = tmp_path / "out"
        run_batch(
            deck_dir,
            settings=settings,
            out_dir=out,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        pdfs = sorted(p.name for p in out.glob("*.pdf"))
        assert len(pdfs) == 3, pdfs
        assert len(set(pdfs)) == 3
        # The undisambiguated name goes to whichever deck got there first.
        assert "Helion_Bio-FAQ.pdf" in pdfs
        assert all(p.stat().st_size > 0 for p in out.glob("*.pdf"))

    def test_the_json_matches_its_pdf(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """A disambiguated pair must stay a pair."""
        out = tmp_path / "out"
        run_batch(
            deck_dir,
            settings=settings,
            out_dir=out,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        for pdf in out.glob("*.pdf"):
            assert pdf.with_suffix(".json").is_file(), pdf.name

    def test_a_single_deck_run_still_overwrites_its_own_output(
        self, sample_pdf: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """Re-rendering one deck must stay idempotent, not accumulate -2, -3, -4."""
        from deckpager.pipeline import run

        for _ in range(3):
            run(
                sample_pdf,
                settings=settings,
                out_dir=tmp_path / "single",
                extractor=FakeExtractor(payload),
                cache=ExtractionCache(tmp_path / "cache"),
            )
        assert len(list((tmp_path / "single").glob("*.pdf"))) == 1
    def test_the_cost_totals_only_what_was_spent(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """The fake extractor reports no usage, so a cached-or-free batch totals zero."""
        report = run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert report.cost_usd == 0.0

    def test_progress_is_reported_per_deck(
        self, deck_dir: Path, tmp_path: Path, settings: Settings, payload: dict[str, Any]
    ) -> None:
        """A long batch that prints nothing is indistinguishable from a hung one."""
        started: list[Path] = []
        finished: list[Any] = []
        run_batch(
            deck_dir,
            settings=settings,
            out_dir=tmp_path / "out",
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
            on_start=started.append,
            on_finish=finished.append,
        )
        assert len(started) == 3
        assert len(finished) == 3

    def test_an_empty_directory_says_what_it_looked_for(
        self, tmp_path: Path, settings: Settings
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(IngestError, match=r"\.pdf"):
            run_batch(empty, settings=settings, out_dir=tmp_path / "out")

    def test_a_file_where_a_directory_was_expected(
        self, sample_pdf: Path, tmp_path: Path, settings: Settings
    ) -> None:
        with pytest.raises(IngestError, match="Not a directory"):
            run_batch(sample_pdf, settings=settings, out_dir=tmp_path / "out")


class TestBatchCli:
    def test_it_reports_and_exits_zero_when_every_deck_works(
        self, deck_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
    ) -> None:
        import deckpager.pipeline as pipeline

        real = pipeline.run_batch
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(
            pipeline,
            "run_batch",
            lambda directory, **kw: real(
                directory,
                extractor=FakeExtractor(payload),
                cache=ExtractionCache(tmp_path / "cache"),
                **kw,
            ),
        )
        result = runner.invoke(
            app, ["batch", str(deck_dir), "--out-dir", str(tmp_path / "out")]
        )
        assert result.exit_code == 0
        assert "3 of 3" in result.output

    def test_a_missing_directory_is_bad_input(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["batch", str(tmp_path / "absent"), "--out-dir", str(tmp_path / "out")]
        )
        assert result.exit_code == EXIT_BAD_INPUT


class TestEngineSeam:
    def test_the_reportlab_engine_satisfies_the_protocol(self) -> None:
        assert isinstance(FaqRenderer(), Renderer)

    def test_the_weasyprint_engine_satisfies_the_protocol(self) -> None:
        """It refuses to render, but it must still be a renderer to be selectable."""
        assert isinstance(WeasyPrintEngine(), Renderer)

    def test_the_default_engine_is_the_one_that_always_works(self) -> None:
        assert get_engine("reportlab").name == "reportlab"

    def test_an_unknown_engine_names_the_real_ones(self) -> None:
        with pytest.raises(RenderError, match="reportlab"):
            get_engine("postscript")

    def test_both_engines_are_registered(self) -> None:
        assert set(ENGINE_NAMES) == {"reportlab", "weasyprint"}

    def test_weasyprint_reports_what_is_missing_rather_than_crashing(self) -> None:
        """Spec §4: an actionable install message, not a stack trace."""
        problems = WeasyPrintEngine().preflight()
        assert problems
        assert any("not implemented" in p for p in problems)

    def test_asking_for_weasyprint_refuses_with_the_reason(
        self, tmp_path: Path
    ) -> None:
        from deckpager.models import Faq

        faq = Faq.model_validate(
            json.loads((FIXTURES / "sample_faq.json").read_text(encoding="utf-8"))
        )
        with pytest.raises(RenderError, match="not available"):
            WeasyPrintEngine().render(faq, tmp_path / "out.pdf")

    def test_the_cli_refuses_an_unknown_engine(self, sample_pdf: Path, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["redraw", str(FIXTURES / "sample_faq.json"), "-o", str(tmp_path / "x.pdf"),
             "--engine", "postscript"],
        )
        assert result.exit_code == EXIT_RENDER_FAILED

    def test_redraw_honours_the_reportlab_engine(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["redraw", str(FIXTURES / "sample_faq.json"), "-o", str(tmp_path / "x.pdf"),
             "--engine", "reportlab"],
        )
        assert result.exit_code == 0
        assert (tmp_path / "x.pdf").is_file()


class TestLanguageRule:
    def test_the_verbatim_system_prompt_is_untouched(self) -> None:
        """Spec §8 froze it, so the language instruction cannot live there."""
        assert "language" not in SYSTEM_PROMPT.lower()
        assert "english" not in SYSTEM_PROMPT.lower()

    def test_the_rule_reaches_the_model_in_the_user_message(
        self, settings: Settings, sample_pdf: Path
    ) -> None:
        deck = ingest_deck(sample_pdf, settings)
        blocks = build_user_blocks(deck)
        text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
        assert LANGUAGE_RULE in text

    def test_the_rule_asks_for_the_language_in_a_note(self) -> None:
        """Spec §11: proceed, and note the language in missing_information."""
        assert "note field" in LANGUAGE_RULE
        assert "English" in LANGUAGE_RULE
