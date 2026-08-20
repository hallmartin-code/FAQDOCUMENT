"""The provider contract.

Every model call in pitchlens goes through `LLMProvider`. The interface is deliberately
narrow — one method that takes a prompt and a Pydantic model and returns an instance of
that model — so that swapping Anthropic for Ollama changes nothing above this layer.

Providers that support native structured output should use it. Those that do not get the
JSON schema injected into the prompt and are held to the same contract: `complete_json`
returns a validated model or raises. Repair is the provider's job, not the caller's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

#: PEP 695 syntax would read better, but the project supports 3.11.
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Usage:
    """What one provider call consumed.

    `cost_usd` is None when the provider does not publish per-token pricing, or when the
    model is not in the configured price table. A missing cost is reported as unknown
    rather than as zero — a zero would quietly understate a run's expense.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    #: One entry per round trip, including repairs, in order.
    calls: int = 1

    def __add__(self, other: Usage) -> Usage:
        """Accumulate usage across the calls that make up one run."""
        if not isinstance(other, Usage):  # pragma: no cover - defensive
            return NotImplemented
        costs = [c for c in (self.cost_usd, other.cost_usd) if c is not None]
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=sum(costs) if costs else None,
            calls=self.calls + other.calls,
        )

    def summary(self) -> str:
        """One line for the run log."""
        cost = f", ${self.cost_usd:.4f}" if self.cost_usd is not None else ""
        return f"{self.calls} call(s), {self.input_tokens} in / {self.output_tokens} out{cost}"


@dataclass(frozen=True)
class ProviderStatus:
    """What `pitchlens providers` prints for one backend."""

    name: str
    ready: bool
    detail: str
    model: str | None = None
    vision: bool = False
    notes: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    """A model backend that can return a validated Pydantic object."""

    #: Registry key — matches the `--provider` value.
    name: str

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        images: list[Path] | None = None,
        max_retries: int = 2,
    ) -> tuple[T, Usage]:
        """Return an instance of `schema`, repairing up to `max_retries` times.

        Raises `AnalysisError` if the backend fails, or `SchemaValidationError` if the
        output still does not validate after the final repair. On a hard schema failure
        the raw response is written to disk for debugging before raising.
        """

    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether slide images can be attached to `complete_json`."""

    def status(self) -> ProviderStatus:
        """Report readiness without making a billable call. Overridden per provider."""
        return ProviderStatus(
            name=self.name, ready=True, detail="ready", vision=self.supports_vision()
        )
