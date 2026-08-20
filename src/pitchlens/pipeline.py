"""End-to-end orchestration: ingest -> analyze -> validate -> render."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from rich.console import Console

from pitchlens.analysis.client import Analyzer, AnthropicAnalyzer
from pitchlens.analysis.grounding import ground
from pitchlens.analysis.schema import Assessment, RunMeta
from pitchlens.config import Settings, load_settings
from pitchlens.errors import ConfigError, PitchlensError, RenderError, SchemaValidationError
from pitchlens.ingest.router import load_deck
from pitchlens.render.base import Paper

_SLUG_STRIP = re.compile(r"[^\w\s-]")
_SLUG_SPACE = re.compile(r"[\s_-]+")

#: pitchlens outputs are a one-page PDF, an optional multi-page memo, and the JSON.
#: The .docx path was deckdd's format and is not part of this product.
VALID_PAPER = ("letter", "a4")


def slugify(name: str) -> str:
    """Turn a company name into a filename stem: punctuation stripped, spaces to underscores."""
    cleaned = _SLUG_SPACE.sub("_", _SLUG_STRIP.sub("", name).strip())
    return cleaned.strip("_") or "Assessment"


def check_paper(paper: str) -> str:
    """Validate the paper size before anything expensive runs."""
    if paper not in VALID_PAPER:
        raise RenderError(f"Unknown --paper {paper!r}. Choose one of: {', '.join(VALID_PAPER)}")
    return paper


def default_stem(assessment: Assessment, source_dir: Path) -> Path:
    """`{CompanyName}` alongside the input deck; the renderer appends `_onepager`."""
    return source_dir / slugify(assessment.company_name)


def analyze_deck(
    *,
    deck_path: Path,
    context: str | None,
    settings: Settings,
    analyzer: Analyzer | None = None,
    now: datetime | None = None,
    console: Console | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> Assessment:
    """Ingest, analyze, ground, and stamp provenance. No rendering.

    `on_stage` is called as each stage begins, so a caller that is not a terminal — the
    web app — can show real progress. A run takes minutes; a progress bar that sits on one
    stage the whole time is worse than none.
    """
    say = console.print if console else (lambda *_a, **_k: None)
    stage = on_stage or (lambda _name: None)

    stage("extracting")
    say(f"[dim]ingesting[/dim] {deck_path.name}")
    deck = load_deck(
        deck_path,
        want_images=not settings.no_images,
        max_slides=settings.max_slides,
        max_image_bytes=settings.max_image_bytes,
    )
    say(f"[green]OK[/green] {deck.slide_count} slides ({deck.source_format})")
    for warning in deck.warnings:
        say(f"[yellow]warning:[/yellow] {warning}")

    # The LLMProvider adapters land in M2 (fake) and M3 (the rest). Until then the
    # Anthropic path runs through AnthropicAnalyzer below. Refuse any other selection
    # rather than honouring `--provider` in `providers` but ignoring it here.
    if analyzer is None and settings.provider != "anthropic":
        raise ConfigError(
            f"--provider {settings.provider} is not wired into the analysis pipeline yet "
            f"(the adapters arrive in milestones M2 and M3).\n"
            f"Run with --provider anthropic, or `pitchlens providers` to see the state of each."
        )

    def _announce_retry(errors: str) -> None:
        say("[yellow]schema validation failed; retrying once with the errors fed back:[/yellow]")
        say(f"[dim]{errors}[/dim]")

    engine = analyzer or AnthropicAnalyzer(settings, on_retry=_announce_retry)
    stage("analyzing")
    say(f"[dim]calling model[/dim] {settings.model} (effort={settings.effort})")
    draft = engine.analyze(deck, context=context)

    stage("grounding")
    say("[dim]validating evidence against the deck[/dim]")
    grounding_warnings = ground(draft, deck)
    for warning in grounding_warnings:
        say(f"[dim]{warning}[/dim]")

    meta = RunMeta(
        model=settings.model,
        provider=settings.provider,
        source_filename=deck_path.name,
        sha256=file_sha256(deck_path),
        slide_count=deck.slide_count,
        ingest_warnings=list(deck.warnings),
        grounding_warnings=grounding_warnings,
        generated_at=now or datetime.now(UTC),
    )
    return Assessment.from_draft(draft, meta)


def file_sha256(path: Path) -> str | None:
    """Hash the deck so the one-pager can identify exactly which file was analyzed.

    Two versions of a deck usually share a filename; the hash is what tells a reader
    whether the memo on their desk was written against the deck in their inbox.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None  # the provenance line states "sha256 unavailable" rather than failing
    return digest.hexdigest()


def load_assessment(path: Path) -> Assessment:
    """Read an assessment JSON from disk, failing loudly if it does not validate."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PitchlensError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PitchlensError(f"{path.name} is not valid JSON: {exc}") from exc
    try:
        return Assessment.model_validate(payload)
    except ValidationError as exc:
        raise SchemaValidationError(
            f"{path.name} is not a valid pitchlens assessment:\n{exc}"
        ) from exc


def render_assessment(
    assessment: Assessment,
    stem: Path,
    *,
    paper: str = "letter",
    console: Console | None = None,
) -> list[Path]:
    """Render the one-pager, fitting it to a single page or failing loudly."""
    from pitchlens.render.fit import fit_to_one_page
    from pitchlens.render.onepager import OnePagerRenderer

    say = console.print if console else (lambda *_a, **_k: None)
    size = cast(Paper, check_paper(paper))
    renderer = OnePagerRenderer()
    if problems := renderer.preflight():
        raise RenderError("\n".join(problems))

    destination = stem.with_name(f"{stem.name}_onepager.pdf")
    say(f"[dim]rendering[/dim] {destination.name}")

    document, _layout, notes = fit_to_one_page(
        overflow=lambda layout: renderer.overflow(assessment, layout, size),
        render=lambda layout: renderer.render_onepager(
            assessment, destination, paper=size, layout=layout
        ),
    )
    for note in notes:
        say(f"[dim]{note}[/dim]")
        assessment.meta.method_notes.append(note)
    return [document]


def _report(assessment: Assessment, written: list[Path], console: Console) -> None:
    """Print the success summary: paths, score, verdict, warnings."""
    colour = {
        "ADVANCE_TO_PARTNER_MEETING": "green",
        "MORE_DILIGENCE": "yellow",
        "PASS": "red",
    }[assessment.ic_view.recommendation]
    verdict = f"[{colour}]{assessment.ic_view.recommendation}[/{colour}]"
    console.print()
    for path in written:
        console.print(f"[green]wrote[/green] {path}")
    score = assessment.headline_score()
    headline = f"{score:.1f}/10" if score is not None else "not scored"
    console.print(
        f"Overall Investability: [bold]{headline}[/bold]   "
        f"Verdict: {verdict}   Confidence: {assessment.ic_view.confidence}"
    )
    for warning in assessment.meta.ingest_warnings:
        console.print(f"[yellow]ingest warning:[/yellow] {warning}")
    for warning in assessment.meta.grounding_warnings:
        if not warning.startswith("Grounding: "):
            console.print(f"[yellow]grounding warning:[/yellow] {warning}")


def run_pipeline(
    *,
    deck: Path,
    out_stem: Path | None,
    paper: str,
    context: str | None,
    provider: str | None,
    model: str | None,
    no_images: bool,
    console: Console,
    json_out: Path | None = None,
) -> None:
    """`pitchlens analyze` — ingest, analyze, and render in one pass."""
    check_paper(paper)  # validate before paying for inference
    settings = load_settings(provider=provider, model=model, no_images=no_images or None)
    assessment = analyze_deck(deck_path=deck, context=context, settings=settings, console=console)
    stem = out_stem or default_stem(assessment, deck.resolve().parent)
    written = render_assessment(assessment, stem, paper=paper, console=console)
    # JSON is written after rendering so it captures the fitting notes.
    json_path = json_out or stem.with_name(f"{stem.name}_analysis.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(assessment.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _report(assessment, [json_path, *written], console)


def run_analyze(
    *,
    deck: Path,
    json_out: Path,
    context: str | None,
    provider: str | None,
    model: str | None,
    no_images: bool,
    console: Console,
) -> None:
    """`pitchlens analyze` — write the assessment JSON and stop."""
    settings = load_settings(provider=provider, model=model, no_images=no_images or None)
    assessment = analyze_deck(deck_path=deck, context=context, settings=settings, console=console)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(assessment.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _report(assessment, [json_out], console)


def run_render(
    *,
    assessment: Path,
    out_stem: Path | None,
    paper: str,
    console: Console,
) -> None:
    """`pitchlens render` — turn an existing assessment JSON into the one-pager."""
    check_paper(paper)
    parsed = load_assessment(assessment)
    stem = out_stem or default_stem(parsed, assessment.resolve().parent)
    written = render_assessment(parsed, stem, paper=paper, console=console)
    _report(parsed, written, console)
