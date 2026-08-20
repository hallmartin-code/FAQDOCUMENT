"""The one-pager schema: the Field wrapper, the spec limits, and currency handling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from deckpager.models import (
    NOT_A_DECK,
    Field,
    Metric,
    OnePager,
    OnePagerDraft,
    Provenance,
    TeamMember,
    parse_amount,
    tool_schema,
)


def _field(value: object, confidence: float = 0.9, slides: list[int] | None = None) -> dict:
    return {"value": value, "confidence": confidence, "source_slides": slides or [1]}


class TestFieldWrapper:
    def test_an_unsupported_field_is_null_not_absent(self) -> None:
        """Spec §3: what the deck does not say must be null, never a plausible guess."""
        draft = OnePagerDraft()
        assert draft.company_name.value is None
        assert draft.company_name.confidence == 0.0
        assert draft.company_name.source_slides == []
        assert not draft.company_name.is_present

    def test_defaults_are_not_shared_between_instances(self) -> None:
        """A shared mutable default would leak one deck's citations into the next."""
        first, second = OnePagerDraft(), OnePagerDraft()
        first.company_name.source_slides.append(3)
        assert second.company_name.source_slides == []

    def test_confidence_outside_zero_to_one_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Field[str].model_validate({"value": "x", "confidence": 1.4})

    def test_slide_numbers_are_one_based(self) -> None:
        with pytest.raises(ValidationError, match="1-based"):
            Field[str].model_validate({"value": "x", "source_slides": [0]})

    def test_low_confidence_only_applies_to_populated_fields(self) -> None:
        """A null field is not 'low confidence' — it is absent, and renders as an em dash."""
        absent = Field[str]()
        assert not absent.is_low_confidence()
        weak = Field[str].model_validate(_field("Helion Bio", confidence=0.4))
        assert weak.is_low_confidence()
        assert not weak.is_low_confidence(threshold=0.3)

    def test_the_flag_counter_names_the_weak_fields(self) -> None:
        draft = OnePagerDraft.model_validate(
            {
                "company_name": _field("Helion Bio", confidence=0.95),
                "hq_location": _field("San Diego, CA", confidence=0.35),
                "sector": _field("Biotech", confidence=0.5),
            }
        )
        assert sorted(draft.low_confidence_fields()) == ["hq_location", "sector"]

    def test_cited_slides_gathers_every_citation(self) -> None:
        draft = OnePagerDraft.model_validate(
            {
                "company_name": _field("Helion Bio", slides=[1]),
                "problem": _field("Checkpoint inhibitors fail.", slides=[2, 3]),
            }
        )
        assert draft.cited_slides() == {1, 2, 3}


class TestSpecLimits:
    def test_tagline_is_capped_at_ninety_characters(self) -> None:
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"tagline": _field("x" * 91)})

    def test_problem_is_capped_at_three_hundred_and_twenty(self) -> None:
        OnePagerDraft.model_validate({"problem": _field("x" * 320)})
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"problem": _field("x" * 321)})

    def test_use_of_funds_takes_at_most_four_bullets(self) -> None:
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"use_of_funds": _field(["a", "b", "c", "d", "e"])})

    def test_traction_is_capped_at_six_metrics(self) -> None:
        metric = {"label": "ARR", "value": "$1.2M"}
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"traction_metrics": _field([metric] * 7)})

    def test_team_is_capped_at_four(self) -> None:
        person = {"name": "Dr. Marisol Reyes", "role": "CEO"}
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"team": _field([person] * 5)})

    def test_strengths_and_risks_must_be_exactly_three(self) -> None:
        """Spec §6 says exactly three. Two is as much a violation as four."""
        OnePagerDraft.model_validate({"key_strengths": _field(["a", "b", "c"])})
        for count in (2, 4):
            with pytest.raises(ValidationError):
                OnePagerDraft.model_validate({"key_risks": _field(["r"] * count)})

    def test_unknown_keys_are_refused(self) -> None:
        """A drifting model payload must fail loudly, not be silently dropped."""
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"revenue_multiple": _field(12)})

    def test_stage_is_a_closed_set(self) -> None:
        OnePagerDraft.model_validate({"stage": _field("Series A")})
        with pytest.raises(ValidationError):
            OnePagerDraft.model_validate({"stage": _field("Series Q")})


class TestCurrency:
    """Spec §12 names these four forms by hand; the rest guard the edges around them."""

    @pytest.mark.parametrize(
        ("written", "amount", "currency"),
        [
            ("$1.2M", 1_200_000, None),
            ("1.2m", 1_200_000, None),
            ("USD 1,200,000", 1_200_000, None),
            ("€900k", 900_000, "EUR"),
            ("$4M", 4_000_000, None),
            ("4,000,000", 4_000_000, None),
            ("$750K", 750_000, None),
            ("2.5bn", 2_500_000_000, None),
            ("£1.5m", 1_500_000, "GBP"),
            ("900k EUR", 900_000, "EUR"),
            ("US$3M", 3_000_000, None),
        ],
    )
    def test_reads_the_forms_decks_are_written_in(
        self, written: str, amount: int, currency: str | None
    ) -> None:
        parsed, note = parse_amount(written)
        assert parsed == amount
        if currency is None:
            assert note is None
        else:
            assert note is not None and currency in note

    @pytest.mark.parametrize(
        "written",
        ["not a number", "", "roughly $4-6M", "TBD", "$", "1.2 million dollars"],
    )
    def test_refuses_to_guess_at_anything_else(self, written: str) -> None:
        assert parse_amount(written) == (None, None)

    def test_a_foreign_currency_is_never_converted(self) -> None:
        """Spec §8: keep the number, note the currency. A rate we invent is a fabrication."""
        draft = OnePagerDraft.model_validate({"raise_amount_usd": _field("€900k")})
        assert draft.raise_amount_usd.value == 900_000
        assert draft.raise_amount_usd.note is not None
        assert "EUR" in draft.raise_amount_usd.note
        assert "not converted" in draft.raise_amount_usd.note

    def test_a_money_string_is_coerced_rather_than_bounced(self) -> None:
        draft = OnePagerDraft.model_validate({"raise_amount_usd": _field("$4M")})
        assert draft.raise_amount_usd.value == 4_000_000

    def test_an_unparseable_amount_becomes_null_at_zero_confidence(self) -> None:
        draft = OnePagerDraft.model_validate({"raise_amount_usd": _field("roughly $4-6M")})
        assert draft.raise_amount_usd.value is None
        assert draft.raise_amount_usd.confidence == 0.0

    def test_an_integer_passes_through_untouched(self) -> None:
        draft = OnePagerDraft.model_validate({"tam_usd": _field(12_000_000_000)})
        assert draft.tam_usd.value == 12_000_000_000
        assert draft.tam_usd.note is None


class TestNotAPitchDeck:
    def test_the_spec_marker_is_recognized(self) -> None:
        draft = OnePagerDraft.model_validate(
            {"missing_information": {"value": [NOT_A_DECK], "confidence": 1.0}}
        )
        assert not draft.is_pitch_deck

    def test_a_real_deck_is_not_mistaken_for_one(self) -> None:
        draft = OnePagerDraft.model_validate({"company_name": _field("Helion Bio")})
        assert draft.is_pitch_deck

    def test_a_nameless_deck_alone_is_still_a_deck(self) -> None:
        """A deck whose name could not be read is not the same as a document that is not a deck."""
        draft = OnePagerDraft.model_validate({"problem": _field("Checkpoint inhibitors fail.")})
        assert draft.is_pitch_deck


class TestProvenance:
    def test_a_draft_is_stamped_without_revalidation(self) -> None:
        draft = OnePagerDraft.model_validate({"company_name": _field("Helion Bio")})
        provenance = Provenance(
            source_filename="deck.pdf",
            source_page_count=5,
            extracted_at=datetime(2026, 1, 15, tzinfo=UTC),
            model="claude-opus-5",
            input_tokens=24_310,
            output_tokens=1_842,
            estimated_cost_usd=0.11,
        )
        one_pager = OnePager.from_draft(draft, provenance)
        assert one_pager.company_name.value == "Helion Bio"
        assert one_pager.provenance.model == "claude-opus-5"
        assert one_pager.provenance.cached is False

    def test_a_one_pager_round_trips_through_json(self) -> None:
        provenance = Provenance(
            source_filename="deck.pdf",
            source_page_count=5,
            extracted_at=datetime(2026, 1, 15, tzinfo=UTC),
            model="claude-opus-5",
        )
        original = OnePager.from_draft(
            OnePagerDraft.model_validate(
                {
                    "company_name": _field("Helion Bio"),
                    "traction_metrics": _field(
                        [Metric(label="ARR", value="$1.2M").model_dump()]
                    ),
                    "team": _field([TeamMember(name="Reyes", role="CEO").model_dump()]),
                }
            ),
            provenance,
        )
        restored = OnePager.model_validate_json(original.model_dump_json())
        assert restored == original


class TestToolSchema:
    def test_every_field_reaches_the_model(self) -> None:
        schema = tool_schema()
        assert set(schema["properties"]) == set(OnePagerDraft.model_fields)

    def test_provenance_is_not_asked_of_the_model(self) -> None:
        """Token counts and cost are measured here; asking would invite invented numbers."""
        assert "provenance" not in tool_schema()["properties"]

    def test_the_schema_forbids_extra_keys(self) -> None:
        assert tool_schema()["additionalProperties"] is False

    def test_the_limits_are_visible_in_the_schema(self) -> None:
        """The model should see the caps in the tool definition, not just be told in prose."""
        schema = tool_schema()
        rendered = str(schema)
        assert "maxItems" in rendered
        assert "maxLength" in rendered
