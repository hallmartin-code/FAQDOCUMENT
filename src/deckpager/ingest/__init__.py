"""Reading a deck off disk into the format-neutral Deck model."""

from __future__ import annotations

from pathlib import Path

from deckpager.config import Settings
from deckpager.ingest.models import Deck
from deckpager.ingest.router import load_deck

__all__ = ["Deck", "ingest_deck", "load_deck"]


def ingest_deck(deck_path: Path, settings: Settings) -> Deck:
    """Read a deck and apply the request budgets. No model call, no cost.

    The one place the ingest budgets are read off Settings, so --dry-run and a paid run
    see byte-identical decks. A dry run that read the deck differently would be worth
    nothing.
    """
    return load_deck(
        deck_path,
        want_images=not settings.no_images,
        max_slides=settings.max_slides,
        max_image_slides=settings.max_image_slides,
        max_image_bytes=settings.max_image_bytes,
    )
