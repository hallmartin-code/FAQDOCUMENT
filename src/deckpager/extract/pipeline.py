"""Deck on disk to validated one-pager, with the cache in front of the paid call."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from deckpager.cache import ExtractionCache, cache_key
from deckpager.config import Settings
from deckpager.errors import IngestError
from deckpager.extract.client import AnthropicExtractor, Extraction, Extractor, Usage
from deckpager.extract.prompts import SYSTEM_PROMPT
from deckpager.ingest import ingest_deck
from deckpager.models import OnePager, OnePagerDraft, Provenance, tool_schema

#: Called with a short label as each stage begins, so a caller can show real progress.
StageHook = Callable[[str], None]


def cache_options(settings: Settings) -> dict[str, object]:
    """The settings that change what the model is shown, and therefore what it answers.

    Only these. The output path and the confidence threshold change what happens to the
    result, not what the result is, so they must not fragment the cache.
    """
    return {
        "effort": settings.effort,
        "max_tokens": settings.max_tokens,
        "max_slides": settings.max_slides,
        "max_image_slides": settings.max_image_slides,
        "max_image_bytes": settings.max_image_bytes,
        "no_images": settings.no_images,
    }


def check_citations(draft: OnePagerDraft, slide_count: int) -> list[str]:
    """Report any slide citation that does not point at a slide in this deck.

    The schema can enforce that a citation is a positive integer; it cannot know how many
    slides the deck had. A citation past the end is the cheapest available signal that a
    field was reasoned about rather than read, so it is recorded rather than corrected.
    """
    stray = sorted(n for n in draft.cited_slides() if n > slide_count)
    if not stray:
        return []
    return [
        f"Cited slide(s) {stray} do not exist: the deck has {slide_count}. "
        f"Treat the fields citing them with care."
    ]

def extract_one_pager(
    deck_path: Path,
    *,
    settings: Settings,
    extractor: Extractor | None = None,
    cache: ExtractionCache | None = None,
    use_cache: bool = True,
    now: datetime | None = None,
    on_stage: StageHook | None = None,
    on_retry: Callable[[str], None] | None = None,
) -> OnePager:
    """Ingest a deck, extract it (or read the cache), and stamp the result with provenance."""
    stage = on_stage or (lambda _name: None)

    try:
        deck_bytes = deck_path.read_bytes()
    except OSError as exc:
        raise IngestError(f"Could not read {deck_path}: {exc}") from exc

    stage("ingesting")
    deck = ingest_deck(deck_path, settings)

    store = cache if cache is not None else ExtractionCache(enabled=use_cache)
    store.enabled = store.enabled and use_cache
    key = cache_key(
        deck_bytes=deck_bytes,
        model=settings.model,
        prompt=SYSTEM_PROMPT,
        schema=tool_schema(),
        options=cache_options(settings),
    )

    cached = store.get(key)
    if cached is not None:
        stage("cached")
        # Validated on the way out, not trusted because it is ours: a record written by an
        # older build can satisfy the version check and still not fit today's schema.
        result = Extraction(draft=OnePagerDraft.model_validate(cached), usage=Usage())
        was_cached = True
    else:
        stage("extracting")
        engine = extractor or AnthropicExtractor(settings, on_retry=on_retry)
        result = engine.extract(deck)
        store.put(key, result.draft.model_dump(mode="json"))
        was_cached = False

    provenance = Provenance(
        source_filename=deck_path.name,
        source_page_count=deck.slide_count,
        extracted_at=now or datetime.now(UTC),
        model=settings.model,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        estimated_cost_usd=result.usage.cost_usd,
        cached=was_cached,
        ingest_warnings=list(deck.warnings),
        citation_warnings=check_citations(result.draft, deck.slide_count),
    )
    return OnePager.from_draft(result.draft, provenance)


def cost_line(one_pager: OnePager, seconds: float) -> str:
    """The spec §10 success line: how long, how many tokens, roughly how much."""
    provenance = one_pager.provenance
    if provenance.cached:
        return f"Read from cache in {seconds:.1f}s · no tokens spent"
    cost = provenance.estimated_cost_usd
    money = f"~${cost:.2f}" if cost is not None else f"cost unknown for {provenance.model}"
    return (
        f"Extracted in {seconds:.1f}s · {provenance.input_tokens:,} in / "
        f"{provenance.output_tokens:,} out · {money}"
    )
