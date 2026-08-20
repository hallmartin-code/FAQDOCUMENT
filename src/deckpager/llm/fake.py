"""A provider that never touches the network.

This is what makes layout work, CI, and the whole render path free and fast: the fitting
ladder needs dozens of renders to tune, and none of them should cost an API call. It
replays a recorded fixture, so the pipeline above it runs exactly as it would against a
real model.

It is a `FakeProvider`, not a mock: it returns schema-valid output through the same
`complete_json` contract every other provider implements, and it fails the same way when
the fixture does not validate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from deckpager.errors import SchemaValidationError
from deckpager.llm.base import LLMProvider, ProviderStatus, Usage

T = TypeVar("T", bound=BaseModel)

#: Shipped alongside the test fixtures so `--provider fake` works from a clean checkout.
DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sample_assessment.json"
)


class FakeProvider(LLMProvider):
    """Replays a recorded assessment. Records its calls so tests can assert on them."""

    name = "fake"

    def __init__(self, fixture: Path | BaseModel | dict[str, Any] | None = None) -> None:
        self._fixture = fixture if fixture is not None else DEFAULT_FIXTURE
        self.calls: list[tuple[str, str, int]] = []

    def supports_vision(self) -> bool:
        """False — there is no model here to look at anything."""
        return False

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        images: list[Path] | None = None,
        max_retries: int = 2,
    ) -> tuple[T, Usage]:
        """Return the recorded payload validated against `schema`."""
        self.calls.append((system, user, len(images or [])))
        payload = self._payload()
        try:
            parsed = schema.model_validate(payload)
        except ValidationError as exc:
            raise SchemaValidationError(
                f"The recorded fixture does not validate against {schema.__name__}:\n{exc}\n"
                f"Regenerate it with `python tests/fixtures/make_assessments.py` — the "
                f"schema has moved on since the fixture was written."
            ) from exc
        # Token counts are stated as zero rather than invented: nothing was spent, and a
        # plausible-looking fake number would corrupt any cost reporting built on Usage.
        return parsed, Usage(input_tokens=0, output_tokens=0, cost_usd=0.0)

    def _payload(self) -> dict[str, Any]:
        """The recorded payload, with any pipeline-owned keys stripped."""
        if isinstance(self._fixture, BaseModel):
            payload = self._fixture.model_dump(mode="json")
        elif isinstance(self._fixture, dict):
            payload = dict(self._fixture)
        else:
            path = Path(self._fixture)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise SchemaValidationError(
                    f"Could not read the fake provider's fixture at {path}: {exc}"
                ) from exc
        # `meta` and `scoring` are stamped by the pipeline, never by a model.
        for key in ("meta", "scoring"):
            payload.pop(key, None)
        return payload

    def status(self) -> ProviderStatus:
        """Always ready; that is the point of it."""
        return ProviderStatus(
            name=self.name,
            ready=True,
            detail="replays a recorded fixture; never hits the network",
            model="(fixture)",
            vision=False,
        )
