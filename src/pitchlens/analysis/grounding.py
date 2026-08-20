"""Post-hoc evidence validation.

The schema guarantees a FACT *has* a quote and slide references. Grounding checks the
quote is actually on the slide it cites. Unverifiable FACTs are downgraded to INFERENCE
rather than deleted — the claim may still be sound, but the memo must not present it as
something the deck demonstrably says.
"""

from __future__ import annotations

import re

from pitchlens.analysis.schema import AssessmentDraft, Evidence
from pitchlens.ingest.models import Deck

#: Fraction of the quote's tokens that must appear on the cited slide.
#: Below 1.0 to tolerate ligatures, hyphenation, and column-order noise from extraction.
OVERLAP_THRESHOLD = 0.6

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens; punctuation and layout artifacts are discarded."""
    return _TOKEN.findall(text.lower())


def overlap_ratio(quote: str, slide_text: str) -> float:
    """Fraction of the quote's distinct tokens that appear in the slide's text."""
    quote_tokens = set(_tokens(quote))
    if not quote_tokens:
        return 0.0
    slide_tokens = set(_tokens(slide_text))
    return len(quote_tokens & slide_tokens) / len(quote_tokens)


def _collect(draft: AssessmentDraft) -> list[Evidence]:
    """Every Evidence object in the draft, so each can be checked exactly once."""
    items: list[Evidence] = []
    for section in (draft.founder, draft.team):
        items.extend(section.strengths)
        items.extend(section.weaknesses)
    for risk in draft.risks:
        items.extend(risk.evidence)
    return items


def ground(draft: AssessmentDraft, deck: Deck) -> list[str]:
    """Verify every claim against the deck, mutating the draft in place.

    Returns the warnings destined for `RunMeta.grounding_warnings`.
    """
    slide_text = {s.index: s.text for s in deck.slides}
    max_index = deck.slide_count

    bad_refs = 0
    downgraded_out_of_range = 0
    downgraded_unverified: list[str] = []
    unverifiable_no_text = 0
    verified = 0
    facts = 0

    for item in _collect(draft):
        in_range = [ref for ref in item.slide_refs if 1 <= ref <= max_index]
        if len(in_range) != len(item.slide_refs):
            bad_refs += len(item.slide_refs) - len(in_range)
            item.slide_refs = in_range

        if not item.slide_refs and item.basis in {"FACT", "INFERENCE"}:
            # Every cited slide was out of range; nothing is left to stand on.
            item.basis = "SPECULATION"
            item.quote = None
            downgraded_out_of_range += 1
            continue

        if item.basis != "FACT":
            continue

        facts += 1
        cited = "\n".join(slide_text.get(ref, "") for ref in item.slide_refs)
        if not cited.strip():
            # Image-only or scanned slide: nothing local to check against. Leave the FACT
            # alone rather than gutting the memo on a deck we simply cannot read.
            unverifiable_no_text += 1
            continue

        if overlap_ratio(item.quote or "", cited) >= OVERLAP_THRESHOLD:
            verified += 1
        else:
            item.basis = "INFERENCE"
            downgraded_unverified.append(f"slide {item.slide_refs[0]}: {item.claim[:70]}")

    warnings: list[str] = []
    if bad_refs:
        warnings.append(
            f"{bad_refs} slide reference(s) pointed outside the deck's 1-{max_index} range "
            f"and were removed."
        )
    if downgraded_out_of_range:
        warnings.append(
            f"{downgraded_out_of_range} claim(s) cited only out-of-range slides and were "
            f"reclassified as SPECULATION."
        )
    if downgraded_unverified:
        preview = "; ".join(downgraded_unverified[:5])
        more = (
            f" (+{len(downgraded_unverified) - 5} more)" if len(downgraded_unverified) > 5 else ""
        )
        warnings.append(
            f"{len(downgraded_unverified)} FACT(s) could not be matched to the cited slide "
            f"text and were downgraded to INFERENCE: {preview}{more}"
        )
    if unverifiable_no_text:
        warnings.append(
            f"{unverifiable_no_text} FACT(s) cite slides with no extractable text "
            f"(image-only or scanned) and could not be verified locally."
        )
    warnings.append(
        f"Grounding: {verified}/{facts} FACT claim(s) verified against extracted slide text."
    )
    return warnings


def verified_fact_summary(warnings: list[str]) -> str:
    """Pull the one-line grounding note the one-pager footer prints."""
    for warning in warnings:
        if warning.startswith("Grounding: "):
            return warning
    return "Grounding: not evaluated."
