"""The fixed TEN Capital diligence questions.

Twenty questions, two per stage of the Investor Conviction Ladder, asked of every deck in
the same order. Fixed rather than derived, and that is the point: when the same twenty
questions are put to every company, two decks become comparable, and a question the deck
cannot answer stays visible instead of being quietly replaced by one it can.

`guidance` never reaches the reader. It goes to the model as the definition of a complete
answer, so "what protects this" means patent type, jurisdiction, and expiry rather than
whatever the deck happens to boast about.

Changing an `id` invalidates every cached extraction that used it, so ids are permanent
even when the wording of a question is improved.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    """One diligence question and what answering it properly requires."""

    id: str
    stage: str
    text: str
    guidance: str


#: The ladder stages, in the order they appear in the rendered FAQ.
STAGES: tuple[str, ...] = (
    "Positioning",
    "Problem",
    "Solution",
    "Team",
    "Market",
    "Differentiation",
    "Traction",
    "Risk",
    "Deal",
    "Commitment",
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        id="positioning-what",
        stage="Positioning",
        text="What does the company do, in one or two sentences?",
        guidance="The plainest possible statement of the business. No adjectives the deck "
        "has not earned.",
    ),
    Question(
        id="positioning-why-now",
        stage="Positioning",
        text="Why now? What has changed that makes this possible or urgent?",
        guidance="A regulatory, technological, clinical, or market shift the deck names. "
        "If the deck asserts urgency without a cause, say that.",
    ),
    Question(
        id="problem-size",
        stage="Problem",
        text="What problem is being solved, and how large is it?",
        guidance="Quantified where the deck quantifies it: prevalence, deaths, hours lost, "
        "economic cost. Carry the deck's own figures and cite them.",
    ),
    Question(
        id="problem-status-quo",
        stage="Problem",
        text="How is this problem handled today, who pays, and what does that cost?",
        guidance="The incumbent solution, the budget holder, and the cost of the status "
        "quo. Note whether the deck establishes that incumbents have failed.",
    ),
    Question(
        id="solution-what",
        stage="Solution",
        text="What is the product, and how does it work?",
        guidance="Mechanism of action or core technology, and the form the customer "
        "actually receives. Enough that a reader could describe it to someone else.",
    ),
    Question(
        id="solution-evidence",
        stage="Solution",
        text="What evidence is there that it works?",
        guidance="Preclinical, clinical, technical, or customer validation, with the "
        "endpoints and sample sizes the deck states. Distinguish demonstrated from "
        "projected.",
    ),
    Question(
        id="team-who",
        stage="Team",
        text="Who is on the team, and why are they the ones to build this?",
        guidance="Named people, roles, whether full-time, and the specific experience that "
        "bears on this problem. Credentials, not adjectives.",
    ),
    Question(
        id="team-gaps",
        stage="Team",
        text="What roles or capabilities are missing, and who are the key partners?",
        guidance="Unfilled functions relative to the next milestone, plus named CROs, "
        "manufacturers, distributors, or advisors. Silence here is itself a finding.",
    ),
    Question(
        id="market-size",
        stage="Market",
        text="How large is the market, and how was that number reached?",
        guidance="TAM, SAM, and SOM with the methodology and source. State plainly when a "
        "market size is asserted without derivation.",
    ),
    Question(
        id="market-model",
        stage="Market",
        text="How does the company make money?",
        guidance="Pricing, unit economics, margins, and the transaction structure — sale, "
        "licence, subscription, milestone payments.",
    ),
    Question(
        id="diff-competition",
        stage="Differentiation",
        text="Who else does this, and why is this better?",
        guidance="Named competitors and the specific axis of differentiation. Note whether "
        "the comparison is quantified or asserted.",
    ),
    Question(
        id="diff-moat",
        stage="Differentiation",
        text="What protects the company from being copied?",
        guidance="Patents by type, jurisdiction, and expiry; regulatory exclusivity; trade "
        "secrets; network effects. Distinguish composition-of-matter from method-of-use.",
    ),
    Question(
        id="traction-todate",
        stage="Traction",
        text="What has been achieved so far?",
        guidance="Revenue, users, clinical milestones, regulatory clearances, grants, and "
        "non-dilutive funding, each with its date or period.",
    ),
    Question(
        id="traction-validation",
        stage="Traction",
        text="Who outside the company has validated this?",
        guidance="Customers, pilots, LOIs, partners, key opinion leaders, named investors. "
        "Third-party commitment counts for more than internal projection.",
    ),
    Question(
        id="risk-primary",
        stage="Risk",
        text="What are the main risks the deck identifies, and how are they mitigated?",
        guidance="Technical, regulatory, commercial, and financial risks the deck names, "
        "with its stated mitigations.",
    ),
    Question(
        id="risk-unaddressed",
        stage="Risk",
        text="What material risk does the deck leave unaddressed?",
        guidance="Your analysis, not the deck's. Specific to this company — never generic "
        "'execution risk'. Includes internal contradictions between slides.",
    ),
    Question(
        id="deal-ask",
        stage="Deal",
        text="What is being raised, on what terms, and at what valuation?",
        guidance="Amount, instrument, cap, discount, interest, maturity, amount committed, "
        "and named lead. State which of these the deck omits.",
    ),
    Question(
        id="deal-use",
        stage="Deal",
        text="What will the money be used for, and how far does it get the company?",
        guidance="Use of proceeds by line item, the milestone it buys, and whether the "
        "runway reconciles with the milestone timeline. Flag it when they do not.",
    ),
    Question(
        id="commit-founder",
        stage="Commitment",
        text="What have the founders personally committed?",
        guidance="Money invested, salary forgone, time committed, years spent. Say so when "
        "the deck is silent.",
    ),
    Question(
        id="commit-exit",
        stage="Commitment",
        text="What is the exit path, and who are the plausible acquirers?",
        guidance="Named acquirers, comparable transactions with multiples, and the "
        "rationale. A stated ambition to IPO is not an exit path.",
    ),
)

#: Stable ids, in render order. The extraction schema pins answers to exactly these.
QUESTION_IDS: tuple[str, ...] = tuple(question.id for question in QUESTIONS)

#: How many answers a complete FAQ carries.
QUESTION_COUNT = len(QUESTIONS)

_BY_ID = {question.id: question for question in QUESTIONS}


def by_id(question_id: str) -> Question:
    """Look up a question, refusing an id the catalogue does not define."""
    try:
        return _BY_ID[question_id]
    except KeyError:
        raise KeyError(
            f"{question_id!r} is not a TEN Capital question id. "
            f"Known ids: {', '.join(QUESTION_IDS)}"
        ) from None


def stages_in_order() -> list[tuple[str, list[Question]]]:
    """The catalogue grouped by stage, preserving both orders."""
    grouped: dict[str, list[Question]] = {stage: [] for stage in STAGES}
    for question in QUESTIONS:
        grouped[question.stage].append(question)
    return [(stage, grouped[stage]) for stage in STAGES if grouped[stage]]
