"""The system prompt, the deck payload, and the correction turn.

The system prompt is the one in spec §8, reproduced verbatim. It is kept here as a module
constant rather than in an editable file because the spec calls it out as verbatim text and
because it is hashed into the cache key — an edit has to invalidate cached extractions, and
a constant beside the code that hashes it is harder to change by accident.
"""

from __future__ import annotations

from typing import Any

from deckpager.ingest.models import Deck

#: The only way the model may answer. Named for what it does, because the name is the last
#: thing the model reads before it starts writing.
TOOL_NAME = "submit_one_pager"

TOOL_DESCRIPTION = (
    "Submit the completed one-page summary of this pitch deck. This is the only way to "
    "return your analysis; every field is validated on receipt, and every populated field "
    "must carry the slide numbers it came from."
)

#: Spec §8, verbatim.
SYSTEM_PROMPT = """\
You are an investment analyst at TEN Capital extracting structured data from a startup pitch \
deck for an internal one-page summary.

Rules:

- Extract only what the deck states or directly implies. If a field is not supported by the \
deck, set value to null and confidence to 0. Never guess a number, a valuation, a customer \
count, or a name.
- For every populated field, record the 1-based slide numbers it came from in source_slides.
- confidence reflects how explicitly the deck supports the value: 0.9-1.0 stated verbatim; \
0.6-0.89 stated but ambiguous or split across slides; 0.3-0.59 inferred from context; below \
0.3 - prefer null.
- Normalize currency to integer USD ($1.2M -> 1200000). If the deck uses another currency, \
keep the value and note the currency in note; do not convert.
- Preserve the founders' own framing in problem and solution - compress, do not editorialize. \
Respect the stated character limits.
- key_strengths, key_risks, and missing_information are your analysis, not the deck's claims. \
Risks must be specific to this company - reject generic risks like "execution risk" or \
"competitive market". missing_information lists diligence items a partner would need before \
an investment committee discussion.
- If the document is not a pitch deck, set company_name.value to null and put "Document does \
not appear to be a pitch deck" as the only entry in missing_information.\
"""


def system_blocks() -> list[dict[str, Any]]:
    """The system prompt as content blocks.

    Deliberately not marked with `cache_control`. The minimum cacheable prefix is around
    1024 tokens and this prompt is roughly a third of that, so a breakpoint here would
    silently never cache - configuration that looks like an optimization and is not.
    The block worth caching is the deck itself; see DP25.
    """
    return [{"type": "text", "text": SYSTEM_PROMPT}]


def slide_text(deck: Deck) -> str:
    """The deck as text, one delimited section per slide (spec §8).

    The delimiter is the spec's, and it is load-bearing rather than decorative: every
    citation the model makes is a slide number, so the numbers have to be unmissable in
    the payload it reads.
    """
    sections: list[str] = []
    for slide in deck.slides:
        parts = [f"--- SLIDE {slide.index} ---"]
        if slide.has_chart:
            parts.append("[this slide contains a chart]")
        parts.append(slide.text or "[no extractable text on this slide]")
        if slide.speaker_notes:
            parts.append(f"[speaker notes] {slide.speaker_notes}")
        sections.append("\n".join(parts))
    return "\n\n".join(sections)


def build_user_blocks(deck: Deck) -> list[dict[str, Any]]:
    """The deck as one user message: the file if it can be sent whole, then the text.

    A PDF within the API limits goes as a native `document` block, which preserves layout,
    charts, and figures far better than re-flattening pages to JPEG. When it cannot, the
    rasterized images of the image-dominant slides go instead — those are the slides whose
    meaning is in the pixels, and spec §7 requires them to be seen.
    """
    blocks: list[dict[str, Any]] = []

    if deck.raw_pdf_b64 is not None:
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": deck.raw_pdf_b64,
                },
            }
        )
    else:
        for slide in deck.slides:
            if slide.asset is None:
                continue
            blocks.append({"type": "text", "text": f"--- SLIDE {slide.index} (image) ---"})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": slide.asset.media_type,
                        "data": slide.asset.data_b64,
                    },
                }
            )

    instruction = (
        f"Here is {deck.source_path.name}, a {deck.slide_count}-slide pitch deck. "
        f"The extracted text of every slide follows. Read it together with the attached "
        f"pages, then call `{TOOL_NAME}` exactly once with the one-page summary.\n\n"
        f"{slide_text(deck)}"
    )
    blocks.append({"type": "text", "text": instruction})
    return blocks


def build_retry_blocks(tool_use_id: str, errors: str) -> list[dict[str, Any]]:
    """The correction turn: the failure handed back as a tool result (spec §8).

    Sent as a `tool_result` rather than a fresh user message so the model sees it as the
    outcome of the call it just made, which is what it is.
    """
    return [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": (
                "The submission was rejected. Fix exactly these problems and call the tool "
                "again with the complete summary — not a patch, the whole thing:\n"
                f"{errors}"
            ),
        }
    ]
