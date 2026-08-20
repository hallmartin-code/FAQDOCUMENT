"""The system prompt, the deck payload, and the correction turn.

The prompt is a module constant rather than an editable file because it is hashed into the
cache key — an edit has to invalidate cached extractions, and a constant beside the code
that hashes it is harder to change by accident.

The questions themselves live in `deckpager.questions` and are rendered into the user
message, not the system prompt: they change more often than the rules for answering them,
and the rules are what must stay stable.
"""

from __future__ import annotations

from typing import Any

from deckpager.ingest.models import Deck
from deckpager.questions import QUESTIONS

#: The only way the model may answer. Named for what it does, because the name is the last
#: thing the model reads before it starts writing.
TOOL_NAME = "submit_faq"

TOOL_DESCRIPTION = (
    "Submit the completed investor FAQ for this document. This is the only way to return "
    "your analysis. Every answer is validated on receipt: all twenty question ids must be "
    "present exactly once, and every answer that is not null must carry the slide numbers "
    "it came from."
)

SYSTEM_PROMPT = """\
You are an investment analyst at TEN Capital. You are given a startup pitch deck or company
document, and you answer a fixed set of twenty diligence questions about it for an internal
investor FAQ.

Rules:

- Answer only from the document. If it does not address a question, set that answer's value
to null and confidence to 0. Never guess a number, a valuation, a customer count, a name,
or a date. An unanswered question is a finding a partner needs, not a gap to paper over.
- Answer every one of the twenty questions. You may not drop, merge, or reword a question
because the document is thin on it.
- For every answer that is not null, record the 1-based slide or section numbers it came
from in source_slides.
- confidence reflects how explicitly the document supports the answer: 0.9-1.0 stated
verbatim; 0.6-0.89 stated but ambiguous or split across slides; 0.3-0.59 inferred from
context; below 0.3 - prefer null.
- Write each answer as prose a partner can read aloud in a meeting: two to five sentences,
specific, with the document's own figures and units. Keep the currency the document used
and do not convert it.
- Carry the founders' framing without adopting their salesmanship. Report a claim as a
claim: "the deck states", "the deck projects". Do not repeat an adjective the document has
not evidenced.
- Where the document contradicts itself, say so in the answer and cite both slides. A
contradiction between two slides is one of the most useful things this FAQ can surface.
- One question asks what material risk the document leaves unaddressed. That answer is
your analysis rather than the document's claims, and it must be specific to this company -
reject generic risks like "execution risk" or "competitive market".
- If the document is not a pitch deck or company document, set company_name to null, put
"Document does not appear to be a pitch deck" in not_a_pitch_deck_reason, and leave every
answer null.\
"""


#: Spec 11 requires a non-English deck to be processed normally, with the language
#: recorded in missing_information. Spec 8 freezes the system prompt and says nothing
#: about language, so the instruction goes in the user message instead: the two sections
#: are only satisfiable together if it lives in the half that is not verbatim.
LANGUAGE_RULE = (
    "If this document is not written in English, analyze it exactly as you would an "
    "English one and write every answer in English. Name the language in the note field "
    "of the first answer, in the form: Document is written in <language>; figures and "
    "claims were read in translation."
)


def question_brief() -> str:
    """The twenty questions and what a complete answer to each contains.

    Rendered into the user message rather than the system prompt: the questions are the
    variable part of this job and the rules for answering them are the stable part, and
    the stable half is what benefits from sitting still.
    """
    lines: list[str] = []
    stage = ""
    for question in QUESTIONS:
        if question.stage != stage:
            stage = question.stage
            lines.append(f"\n[{stage}]")
        lines.append(f"  {question.id}: {question.text}")
        lines.append(f"      A complete answer covers: {question.guidance}")
    return chr(10).join(lines).strip()


def system_blocks() -> list[dict[str, Any]]:
    """The system prompt as content blocks.

    Deliberately not marked with `cache_control`. The minimum cacheable prefix is around
    1024 tokens and this prompt is roughly a third of that, so a breakpoint here would
    silently never cache - configuration that looks like an optimization and is not.
    The block worth caching is the deck itself; see DP25.
    """
    return [{"type": "text", "text": SYSTEM_PROMPT}]


def unit_label(deck: Deck) -> str:
    """What one indexed piece of this source is called.

    A Word document has no slides, and calling its sections "slides" in the payload
    invites the model to cite page numbers that do not exist. The schema field is still
    `source_slides` — the instruction below says plainly what the numbers refer to.
    """
    return "SECTION" if deck.source_format == "docx" else "SLIDE"


def slide_text(deck: Deck) -> str:
    """The deck as text, one delimited section per slide (spec §8).

    The delimiter is the spec's, and it is load-bearing rather than decorative: every
    citation the model makes is a slide number, so the numbers have to be unmissable in
    the payload it reads.
    """
    unit = unit_label(deck)
    sections: list[str] = []
    for slide in deck.slides:
        parts = [f"--- {unit} {slide.index} ---"]
        if slide.has_chart:
            parts.append("[this slide contains a chart]")
        parts.append(slide.text or f"[no extractable text on this {unit.lower()}]")
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
            blocks.append(
                {"type": "text", "text": f"--- {unit_label(deck)} {slide.index} (image) ---"}
            )
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

    if deck.source_format == "docx":
        # A memo is not a deck. Told it is one, the model hunts for slides that do not
        # exist and cites numbers a reader cannot locate.
        instruction = (
            f"Here is {deck.source_path.name}, a written document (an executive summary, "
            f"memo, or similar), split into {deck.slide_count} numbered sections at its "
            f"headings. It is not a slide deck. Record section numbers in `source_slides` "
            f"- that field carries whatever unit this source is numbered in. Then call "
            f"`{TOOL_NAME}` exactly once with the completed FAQ.\n\n"
            f"{LANGUAGE_RULE}"
            f"\n\n{slide_text(deck)}"
        )
    else:
        instruction = (
            f"Here is {deck.source_path.name}, a {deck.slide_count}-slide pitch deck. "
            f"The extracted text of every slide follows. Read it together with the attached "
            f"pages, then call `{TOOL_NAME}` exactly once with the completed FAQ.\n\n"
            f"{LANGUAGE_RULE}"
            f"\n\n{slide_text(deck)}"
        )
    instruction += (
        "\n\nAnswer these twenty questions, using the id shown for each:\n\n"
        + question_brief()
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
