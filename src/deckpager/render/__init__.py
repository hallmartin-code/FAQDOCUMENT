"""Rendering: the engine contract, the ReportLab engine, and the fitting ladder."""

from __future__ import annotations

from deckpager.render.base import (
    ENGINE_NAMES,
    EngineName,
    Paper,
    Renderer,
    default_engine,
    get_engine,
)
from deckpager.render.onepager import OnePagerRenderer, PageLayout, fit_and_render

__all__ = [
    "ENGINE_NAMES",
    "EngineName",
    "OnePagerRenderer",
    "PageLayout",
    "Paper",
    "Renderer",
    "default_engine",
    "fit_and_render",
    "get_engine",
]
