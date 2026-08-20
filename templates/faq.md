# TEN Capital — Investor FAQ Template

Structure and question map for every FAQ deckpager generates. Company-agnostic: nothing here
is specific to a deck, a company, or a run. The renderer owns *structure and formatting
only*; all content arrives through the data contract in §4.

This document is a contract, not documentation. `tests/test_template.py` asserts that the
question catalogue, the vocabularies, and the fixed strings below match
`src/deckpager/questions.py`, `src/deckpager/models.py`, and `src/deckpager/render/faq.py`.
If the code changes and this file does not, the tests fail.

---

## 1. The hard constraint

**Every question is answered or explicitly marked unanswered. Always.**

This replaces the one-page rule of the one-pager it succeeds. A twenty-question document
paginates; nothing is truncated to make it fit, because an answer shortened to save a page
destroys the figure or caveat the reader needed. The document is exactly as long as the
answers require.

What is enforced instead:

- All twenty catalogue ids appear exactly once. A deck cannot be handed back with the
  awkward questions quietly dropped — the schema refuses the payload.
- An unanswered question prints `Not addressed in the document.` in the reader's face, and
  is collected again at the end as the diligence list.
- A document that answers little produces a **short** FAQ. Padding an empty deck into a
  substantial-looking document would misrepresent it.

---

## 2. Document map

Flowing document, US Letter or A4 portrait, 0.5in margins.

| Order | Block | Content |
|---|---|---|
| 1 | Masthead | Company name · tagline · sector/stage chips |
| 2 | Coverage bar | `{n} of 20 questions answered by the document` · unanswered · low confidence |
| 3 | Question body | Ten stage groups, two questions each, in catalogue order |
| 4 | Open diligence items | Every unanswered question, gathered as the founder-call agenda |
| 5 | Closing notes | Dagger footnote · AI-generated disclosure · citation warnings |
| 6 | Page footer | House standard, repeated on every page |

Each question in block 3 renders as:

```
{QUESTION TEXT}                     serif bold, 9.5pt
{answer}                            sans, 8.5pt — or the unanswered line in muted italic
Slides {n, n}  † low confidence     muted, 6.5pt — omitted when there are no citations
```

A question is never separated from its answer by a page break.

---

## 3. The twenty questions

Fixed. The same questions are put to every deck, so two companies are comparable and a
question the deck cannot answer stays visible instead of being replaced by one it can.

Ids are permanent: changing one invalidates every cached extraction that used it.

| # | Stage | id | Question |
|---|---|---|---|
| 1 | Positioning | `positioning-what` | What does the company do, in one or two sentences? |
| 2 | Positioning | `positioning-why-now` | Why now? What has changed that makes this possible or urgent? |
| 3 | Problem | `problem-size` | What problem is being solved, and how large is it? |
| 4 | Problem | `problem-status-quo` | How is this problem handled today, who pays, and what does that cost? |
| 5 | Solution | `solution-what` | What is the product, and how does it work? |
| 6 | Solution | `solution-evidence` | What evidence is there that it works? |
| 7 | Team | `team-who` | Who is on the team, and why are they the ones to build this? |
| 8 | Team | `team-gaps` | What roles or capabilities are missing, and who are the key partners? |
| 9 | Market | `market-size` | How large is the market, and how was that number reached? |
| 10 | Market | `market-model` | How does the company make money? |
| 11 | Differentiation | `diff-competition` | Who else does this, and why is this better? |
| 12 | Differentiation | `diff-moat` | What protects the company from being copied? |
| 13 | Traction | `traction-todate` | What has been achieved so far? |
| 14 | Traction | `traction-validation` | Who outside the company has validated this? |
| 15 | Risk | `risk-primary` | What are the main risks the deck identifies, and how are they mitigated? |
| 16 | Risk | `risk-unaddressed` | What material risk does the deck leave unaddressed? |
| 17 | Deal | `deal-ask` | What is being raised, on what terms, and at what valuation? |
| 18 | Deal | `deal-use` | What will the money be used for, and how far does it get the company? |
| 19 | Commitment | `commit-founder` | What have the founders personally committed? |
| 20 | Commitment | `commit-exit` | What is the exit path, and who are the plausible acquirers? |

One answer — `risk-unaddressed` — is TEN Capital's analysis rather than the document's
claims. The closing note says so, so it can never be read as something the founders wrote.

---

## 4. Data contract

Every answer arrives as a `Field`:

| Key | Meaning |
|---|---|
| `value` | The answer, ≤ 1200 characters. `null` means the document does not address it. |
| `confidence` | 0.0–1.0. Below 0.6 renders the dagger. |
| `source_slides` | 1-based slide or section numbers. Never empty for a populated answer. |
| `note` | Optional qualifier: a currency, a language, a contradiction between slides. |

Header fields — `company_name`, `tagline`, `sector`, `stage` — use the same wrapper.
`not_a_pitch_deck_reason` is populated only when the document is not a company document, in
which case every answer is null.

Confidence bands, as given to the model: 0.9–1.0 stated verbatim; 0.6–0.89 stated but
ambiguous or split across slides; 0.3–0.59 inferred from context; below 0.3 prefer null.

---

## 5. Vocabularies

`stage` is one of: `Pre-Seed`, `Seed`, `Series A`, `Series B`, `Series C+`, `Growth`,
`Unknown`.

---

## 6. Fixed strings

| String | Where |
|---|---|
| `Not addressed in the document.` | Any question the document does not answer |
| `OPEN DILIGENCE ITEMS` | Heading of the gathered unanswered list |
| `Investor FAQ` | Document title, and the first cell of the page footer |
| `Compiled on {date} by TEN Capital Network` | Page footer, house standard |
| `Internal use only` | Page footer, second line |
| `†` | Low-confidence marker |

---

## 7. Palette and type

All colours and sizes live in `src/deckpager/render/style.py`; nothing below that file
hard-codes one. Near-black ink, one TEN Capital accent, one neutral grey, one tint for the
coverage bar. One serif for questions, one sans for answers.
