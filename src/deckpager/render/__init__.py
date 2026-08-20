"""Rendering: the engine contract and the ReportLab FAQ engine."""

from __future__ import annotations

from deckpager.render.base import (
    ENGINE_NAMES,
    EngineName,
    Paper,
    Renderer,
    default_engine,
    get_engine,
)
from deckpager.render.faq import FaqRenderer, render_faq

__all__ = [
    "ENGINE_NAMES",
    "EngineName",
    "FaqRenderer",
    "Paper",
    "Renderer",
    "default_engine",
    "get_engine",
    "render_faq",
]
