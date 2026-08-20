"""Deck in, one-pager out. The whole job, in one place.

`run` is what both callers use — the CLI and the web app — so a deck analysed through the
browser and a deck analysed at a terminal go through byte-identical code. All either caller
supplies is somewhere to put the files and a way to hear about progress.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from deckpager.cache import ExtractionCache
from deckpager.config import Settings
from deckpager.extract.client import Extractor
from deckpager.extract.pipeline import cost_line, extract_one_pager
from deckpager.models import DEFAULT_MIN_CONFIDENCE, OnePager
from deckpager.render.onepager import Paper, fit_and_render

#: Called with a short stage name as each stage begins.
StageHook = Callable[[str], None]


@dataclass
class RunResult:
    """Everything a caller needs to report on a finished run."""

    one_pager: OnePager
    pdf: Path
    json: Path
    seconds: float = 0.0
    truncations: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """The spec §10 success line."""
        return cost_line(self.one_pager, self.seconds)


def default_stem(deck_path: Path, one_pager: OnePager) -> str:
    """`{CompanyName}-onepager`, falling back to the deck's own name.

    Named for the company rather than the upload, because the file lands in a folder of
    other companies' one-pagers, and `deck-onepager.pdf` is not findable there.
    """
    name = one_pager.company_name.value or deck_path.stem
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in name).strip()
    return f"{cleaned.replace(' ', '_') or deck_path.stem}-onepager"


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
) -> RunResult:
    """Ingest, extract, render. Returns the artifacts and what the run cost."""
    started = time.monotonic()
    stage = on_stage or (lambda _name: None)

    one_pager = extract_one_pager(
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
    stem = default_stem(deck_path, one_pager)
    directory = out_dir or deck_path.parent
    pdf_path = out_pdf or directory / f"{stem}.pdf"
    json_path = out_json or directory / f"{stem}.json"

    pdf_path, truncations = fit_and_render(
        one_pager, pdf_path, paper=paper, threshold=min_confidence
    )

    # Recorded on the document, not only returned: someone reading the JSON months later
    # needs to know the page they were handed was shortened, and by what.
    one_pager.provenance.truncations = list(truncations)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(one_pager.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return RunResult(
        one_pager=one_pager,
        pdf=pdf_path,
        json=json_path,
        seconds=time.monotonic() - started,
        truncations=list(truncations),
    )
