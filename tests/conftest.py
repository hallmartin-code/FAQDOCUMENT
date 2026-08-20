"""Shared test fixtures.

No test in this suite makes a network call. All API interaction goes through the
`Analyzer` protocol, and tests bind the recorded-fixture fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

#: Frozen timestamp so rendered documents are byte-comparable across runs.
FROZEN_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_pdf() -> Path:
    """A real five-page PDF deck."""
    return FIXTURES / "sample_deck.pdf"


@pytest.fixture
def sample_pptx() -> Path:
    """A real five-slide PPTX deck with speaker notes."""
    return FIXTURES / "sample_deck.pptx"


@pytest.fixture
def frozen_now() -> datetime:
    """The injectable run timestamp used by deterministic rendering tests."""
    return FROZEN_NOW
