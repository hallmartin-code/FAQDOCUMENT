"""Rendering: the Renderer contract, the ReportLab engine, and the fitting ladder."""

from __future__ import annotations

from deckpager.render.base import Layout, Paper, Renderer
from deckpager.render.legacy_onepager import OnePagerRenderer

__all__ = ["Layout", "OnePagerRenderer", "Paper", "Renderer"]
