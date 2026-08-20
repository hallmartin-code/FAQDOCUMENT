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
def image_heavy_pdf() -> Path:
    """A three-page PDF of charts and diagrams with almost no text."""
    return FIXTURES / "image_heavy_deck.pdf"


@pytest.fixture
def frozen_now() -> datetime:
    """The injectable run timestamp used by deterministic rendering tests."""
    return FROZEN_NOW


@pytest.fixture
def faq_factory():  # type: ignore[no-untyped-def]
    """Build a valid `Faq` with control over which questions were answered.

    Every test that needs a document needs all twenty entries present, because the model
    refuses a partial set — so building one by hand in each test file would be twenty
    lines of noise repeated six times.
    """
    from deckpager.models import AnswerText, Faq, FaqDraft, FaqEntry, Field, Provenance
    from deckpager.questions import QUESTION_IDS

    def build(
        *,
        answered: int = len(QUESTION_IDS),
        confidence: float = 0.9,
        company: str | None = "Northwind Robotics",
        answer_text: str = "The deck states this, with figures on the slide cited.",
        **provenance: object,
    ) -> Faq:
        entries = []
        for position, question_id in enumerate(QUESTION_IDS):
            if position < answered:
                entries.append(
                    FaqEntry(
                        question_id=question_id,
                        answer=Field[AnswerText](
                            value=answer_text,
                            confidence=confidence,
                            source_slides=[position + 1],
                        ),
                    )
                )
            else:
                entries.append(FaqEntry(question_id=question_id))

        draft = FaqDraft(
            company_name=Field(
                value=company, confidence=1.0, source_slides=[1] if company else []
            ),
            tagline=Field(
                value="Warehouse automation for mid-market distributors.",
                confidence=0.9,
                source_slides=[1],
            ),
            sector=Field(value="Industrial Robotics", confidence=0.8, source_slides=[2]),
            stage=Field(value="Seed", confidence=0.7, source_slides=[12]),
            entries=entries,
        )
        defaults: dict[str, object] = {
            "source_filename": "northwind.pdf",
            "source_page_count": 18,
            "extracted_at": FROZEN_NOW,
            "model": "claude-opus-5",
        }
        defaults.update(provenance)
        return Faq.from_draft(draft, Provenance(**defaults))  # type: ignore[arg-type]

    return build
