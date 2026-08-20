"""The one-pager output contract.

This module is the single source of truth for what deckpager extracts. The JSON schema
handed to the API is generated from `OnePagerDraft.model_json_schema()` — never hand-written
— so the schema the model is held to and the model this code validates against cannot drift
apart.

Two rules from the spec are enforced here rather than requested politely in the prompt:

* Every extracted value is wrapped in `Field`, which carries the slide numbers it came from
  and how confident the model was. A bare value with no provenance cannot be represented.
* Character and list limits (spec §6) are part of the schema, so the model sees them in the
  tool definition and a violation fails validation instead of overflowing the page later.

`Field` is spelled with `Generic[T]` rather than the `class Field[T]` syntax the spec uses,
because that syntax is a SyntaxError before Python 3.12 and spec §3 sets the floor at 3.11.
The resulting schema is identical.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

import pydantic
from pydantic import BaseModel, ConfigDict, StringConstraints, conlist, model_validator

T = TypeVar("T")

Stage = Literal[
    "Pre-Seed",
    "Seed",
    "Series A",
    "Series B",
    "Series C+",
    "Growth",
    "Unknown",
]

#: What the model must say when handed something that is not a pitch deck (spec §8).
NOT_A_DECK = "Document does not appear to be a pitch deck"

#: Below this, a field is flagged in the rendered one-pager (spec §9). The CLI can override
#: it per run; this is the default the spec names.
DEFAULT_MIN_CONFIDENCE = 0.6

# Character limits from spec §6, named so the limit appears once and reads as a rule.
Line90 = Annotated[str, StringConstraints(max_length=90)]
Text200 = Annotated[str, StringConstraints(max_length=200)]
Text220 = Annotated[str, StringConstraints(max_length=220)]
Text320 = Annotated[str, StringConstraints(max_length=320)]
Bullet70 = Annotated[str, StringConstraints(max_length=70)]
Short = Annotated[str, StringConstraints(max_length=120)]


class Field(BaseModel, Generic[T]):
    """One extracted value, with the evidence for it.

    `value` is None when the deck does not support the field. That is a finding, not a
    failure: spec §3 forbids a plausible guess, and the renderer prints an em dash. A
    populated value with an empty `source_slides` is the shape this wrapper exists to make
    impossible to produce accidentally.
    """

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    confidence: float = pydantic.Field(default=0.0, ge=0.0, le=1.0)
    source_slides: list[int] = pydantic.Field(default_factory=list)
    note: str | None = pydantic.Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_provenance(self) -> Field[T]:
        if self.value is None:
            return self
        if any(slide < 1 for slide in self.source_slides):
            raise ValueError("source_slides are 1-based slide numbers; values must be >= 1")
        return self

    @property
    def is_present(self) -> bool:
        """Whether the deck supported this field at all."""
        return self.value is not None

    def is_low_confidence(self, threshold: float = DEFAULT_MIN_CONFIDENCE) -> bool:
        """Whether this field should render with the low-confidence marker (spec §9)."""
        return self.is_present and self.confidence < threshold


class Metric(BaseModel):
    """One traction number, kept in the units the deck displayed it in."""

    model_config = ConfigDict(extra="forbid")

    label: Short
    value: Short
    period: Short | None = None
    source_slide: int | None = pydantic.Field(default=None, ge=1)


class TeamMember(BaseModel):
    """One person, as the deck presents them."""

    model_config = ConfigDict(extra="forbid")

    name: Short
    role: Short
    background: Line90 | None = None


# --- currency ---------------------------------------------------------------------------

#: Multipliers for the suffixes decks actually use. Ordered longest-first at match time so
#: "bn" is not read as "b" followed by junk.
_MAGNITUDES: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}

#: Currency marks seen on the front of a number. USD needs no note; the rest are recorded
#: and *not* converted, per spec §8 — inventing an exchange rate would be a fabricated
#: figure, and the rate on the day the deck was written is not knowable from the deck.
_CURRENCY_SIGNS: dict[str, str] = {
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "₹": "INR",
    "inr": "INR",
    "c$": "CAD",
    "cad": "CAD",
    "a$": "AUD",
    "aud": "AUD",
}

_AMOUNT = re.compile(
    r"^\s*(?P<sign>-)?\s*"
    r"(?P<currency>us\$|c\$|a\$|usd|eur|gbp|jpy|inr|cad|aud|[$€£¥₹])?\s*"
    r"(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<magnitude>mm|bn|[kmb])?\s*"
    r"(?P<trailing>usd|eur|gbp|jpy|inr|cad|aud)?\s*$",
    re.IGNORECASE,
)


def parse_amount(raw: str) -> tuple[int | None, str | None]:
    """Read a money string as (amount, currency-note).

    Handles the forms decks are written in: `$1.2M`, `1.2m`, `USD 1,200,000`, `€900k`.
    Returns the amount as an integer of whatever currency was written, and a note naming
    that currency when it is not USD. The value is never converted — spec §8 keeps the
    number and records the currency, because a conversion would be a figure that appears
    nowhere in the deck.

    Returns `(None, None)` for anything that is not a single amount, so a caller can leave
    the field null rather than guess.
    """
    match = _AMOUNT.match(raw)
    if match is None:
        return None, None

    number = float(match.group("number").replace(",", ""))
    magnitude = (match.group("magnitude") or "").lower()
    number *= _MAGNITUDES.get(magnitude, 1)
    if match.group("sign"):
        number = -number

    written = (match.group("currency") or match.group("trailing") or "").lower()
    currency = _CURRENCY_SIGNS.get(written)
    note = None if currency in (None, "USD") else f"stated in {currency}; not converted"
    return int(round(number)), note


class MoneyField(Field[int]):
    """An amount in whole units, tolerant of the string forms a model may hand back.

    The prompt asks for an integer. Models mostly comply, and when one does not, the
    failure is `"$1.2M"` rather than nonsense — so it is parsed rather than bounced into a
    retry. A string that does not parse becomes null, never a guess.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        value = data.get("value")
        if not isinstance(value, str):
            return data
        amount, note = parse_amount(value)
        coerced = dict(data)
        coerced["value"] = amount
        if amount is None:
            coerced["confidence"] = 0.0
        elif note and not coerced.get("note"):
            coerced["note"] = note
        return coerced


# --- the one-pager ------------------------------------------------------------------------


class OnePagerDraft(BaseModel):
    """Everything the model is asked to produce. Provenance is added by the pipeline."""

    model_config = ConfigDict(extra="forbid")

    # Header block
    company_name: Field[Short] = Field()
    tagline: Field[Line90] = Field()
    website: Field[Short] = Field()
    hq_location: Field[Short] = Field()
    founded_year: Field[int] = Field()
    sector: Field[Short] = Field()
    sub_sector: Field[Short] = Field()
    stage: Field[Stage] = Field()

    # The ask
    raise_amount_usd: MoneyField = MoneyField()
    pre_money_valuation_usd: MoneyField = MoneyField()
    instrument: Field[Short] = Field()
    amount_committed_usd: MoneyField = MoneyField()
    min_check_usd: MoneyField = MoneyField()
    close_date: Field[Short] = Field()

    # Business
    problem: Field[Text320] = Field()
    solution: Field[Text320] = Field()
    business_model: Field[Text220] = Field()
    go_to_market: Field[Text220] = Field()
    use_of_funds: Field[conlist(Bullet70, max_length=4)] = Field()  # type: ignore[valid-type]

    # Traction
    traction_metrics: Field[conlist(Metric, max_length=6)] = Field()  # type: ignore[valid-type]

    # Market
    tam_usd: MoneyField = MoneyField()
    sam_usd: MoneyField = MoneyField()
    som_usd: MoneyField = MoneyField()
    market_note: Field[Text220] = Field()

    # Team
    team: Field[conlist(TeamMember, max_length=4)] = Field()  # type: ignore[valid-type]

    # Competition
    competitors: Field[conlist(Short, max_length=5)] = Field()  # type: ignore[valid-type]
    differentiation: Field[Text200] = Field()

    # Analyst layer — deckpager's judgment, not the deck's claims. Labelled as such in the
    # rendered one-pager so it can never be read as something the founders said.
    key_strengths: Field[conlist(Line90, min_length=3, max_length=3)] = Field()  # type: ignore[valid-type]
    key_risks: Field[conlist(Line90, min_length=3, max_length=3)] = Field()  # type: ignore[valid-type]
    missing_information: Field[conlist(Text200, max_length=5)] = Field()  # type: ignore[valid-type]

    @property
    def is_pitch_deck(self) -> bool:
        """False when the model reported that the document is not a pitch deck (spec §8)."""
        listed = self.missing_information.value or []
        return not (self.company_name.value is None and NOT_A_DECK in listed)

    def fields(self) -> dict[str, Field[object]]:
        """Every extracted field by name, for counting and for rendering."""
        return {
            name: getattr(self, name)
            for name in type(self).model_fields
            if isinstance(getattr(self, name), Field)
        }

    def low_confidence_fields(
        self, threshold: float = DEFAULT_MIN_CONFIDENCE
    ) -> list[str]:
        """Names of the populated fields below the flag threshold — the footer counter."""
        return [
            name
            for name, field in self.fields().items()
            if field.is_low_confidence(threshold)
        ]

    def cited_slides(self) -> set[int]:
        """Every slide number cited anywhere, for checking citations against the deck."""
        return {slide for field in self.fields().values() for slide in field.source_slides}


class Provenance(BaseModel):
    """Where this one-pager came from and what it cost. Written by the pipeline."""

    model_config = ConfigDict(extra="forbid")

    source_filename: str
    source_page_count: int = pydantic.Field(ge=0)
    extracted_at: datetime
    model: str
    input_tokens: int = pydantic.Field(default=0, ge=0)
    output_tokens: int = pydantic.Field(default=0, ge=0)
    estimated_cost_usd: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        description="None when this model is not in the local price table.",
    )
    cached: bool = pydantic.Field(
        default=False, description="Whether this came from the extraction cache."
    )
    ingest_warnings: list[str] = pydantic.Field(default_factory=list)
    truncations: list[str] = pydantic.Field(
        default_factory=list,
        description="What the renderer shortened to make the page fit (spec §9).",
    )


class OnePager(OnePagerDraft):
    """A draft plus its provenance. This is what is written to disk and rendered."""

    provenance: Provenance

    @classmethod
    def from_draft(cls, draft: OnePagerDraft, provenance: Provenance) -> OnePager:
        """Stamp a draft with provenance without re-validating the model output."""
        return cls.model_construct(**draft.__dict__, provenance=provenance)


def tool_schema() -> dict[str, object]:
    """The JSON schema handed to the API as the extraction tool's `input_schema`.

    The draft, not the full one-pager: provenance is measured by the pipeline — token
    counts, cost, the filename — and asking the model for it would invite it to invent
    numbers that we already know exactly.
    """
    return OnePagerDraft.model_json_schema()
