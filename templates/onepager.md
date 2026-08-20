# TEN Capital — Investment Screening One-Pager Template

Structure and field map for every one-pager the deckpager app generates. Company-agnostic:
nothing here is specific to a deck, a company, or a run. The renderer owns *structure and
formatting only*; all content arrives through the data contract in §4.

Derived from the TEN Capital pitch-deck analysis template (`PitchdeckAnalyzer/report_template.py`),
which supplies the house furniture — palette, type scale, footer, table styling, fixed-row
discipline. The **field set is different**: that document critiques a deck section by section;
this one states an investment view. The document map, formatting spec, and validation contract
carry over; the deck-section / design-dimension / slide-outline vocabularies do not.

To be implemented in `src/deckpager/render/onepager_template.py` (milestone M2). Running that
module writes `_onepager_preview.pdf`, an empty copy of the layout, so the design can be
inspected with no API call and no deck.

---

## 1. The hard constraint

**One page. Always.** Not "usually one page", not "one page for a short deck". A second page
is a defect, not an overflow — the artifact exists to be read in a partner meeting at a glance,
and a two-page screening memo is a memo, not a screen.

The renderer measures the produced PDF and applies the reduction ladder in §5 until the page
count is 1, or raises `OnePagerOverflowError` with a diagnostic. It never silently emits page 2.
The multi-page treatment is a separate artifact (`--full`) with its own template.

---

## 2. Document map

Single page, four zones, no page breaks. Left/right are a two-column band between the header
and the footer.

| Order | Zone | Width | Content |
|---|---|---|---|
| 1 | Header band | full | Company name, stage/round, ask, sector, deck date, analysis date; recommendation chip + confidence, right-aligned |
| 2 | Left column | ~62% | Executive summary · Top 3 Strengths · Top 3 Concerns · Risk row |
| 3 | Right sidebar | ~38% | 11-category scorecard, then Overall Investability as a large figure |
| 4 | Footer | full | Top 5 diligence questions, then the provenance line |

Page: US Letter default, A4 via `--paper a4`. Margins 0.5" all sides (tighter than the house
0.75" — a one-pager buys its space back from the margins before it buys it from the content).
Header/footer are drawn as part of the page body, not as Word-style running headers: there is
only one page, so there is nothing to run.

---

## 3. Formatting specification

Inherits the TEN Capital palette and Arial type family. Sizes are compressed relative to the
multi-page house document, and the body size is the first thing the fitting ladder touches.

### Palette

| Token | Hex | Used for |
|---|---|---|
| `NAVY` | `#1F3864` | Company name, zone rules, scorecard bar fill, table header fill |
| `BLUE` | `#2E75B6` | Zone headings, sub-labels, Overall Investability figure |
| `GREY` | `#555555` | Metadata, provenance line, footer, `insufficient data` markers |
| `ZEBRA_FILL` | `#F2F2F2` | Alternating scorecard rows, risk-row banding |
| `WHITE` | `#FFFFFF` | Page ground, reversed table header text |

Recommendation chip fills — the only colour in the document that carries meaning rather than
hierarchy, so it is the only place a non-brand hue is permitted:

| Recommendation | Fill | Text |
|---|---|---|
| `ADVANCE_TO_PARTNER_MEETING` | `#2E7D32` green | White |
| `MORE_DILIGENCE` | `#B26A00` amber | White |
| `PASS` | `#8C1D18` red | White |

Risk levels use the same three fills as a small square swatch, not as filled cells: the risk row
must stay ink-light.

### Typography

One family (Arial), two weights (regular, bold). No italics except the `insufficient data`
marker. No third weight, no second family.

| Element | Size | Weight | Colour | Alignment |
|---|---|---|---|---|
| Company name | 16pt | Bold | `NAVY` | Left |
| One-line description | 9pt | Regular | `GREY` | Left |
| Header metadata (stage, ask, sector, dates) | 8pt | Regular | `GREY` | Left |
| Recommendation chip | 9pt | Bold | White on fill | Centre |
| Confidence label | 7.5pt | Regular | `GREY` | Centre |
| Zone heading | 9.5pt | Bold | `BLUE` | Left |
| Body / executive summary | **9pt** | Regular | Black | Left |
| Strength / concern bullet | 8.5pt | Regular | Black | Left |
| Risk row label | 8pt | Bold | Black | Left |
| Risk row reason | 8pt | Regular | `GREY` | Left |
| Scorecard label | 8pt | Regular | Black | Left |
| Scorecard value | 8pt | Bold | `NAVY` | Right |
| Overall Investability figure | 30pt | Bold | `BLUE` | Centre |
| Diligence question | 8pt | Regular | Black | Left |
| Provenance line | 6.5pt | Regular | `GREY` | Left |

Body line-height starts at 1.35 and is the fifth rung of the fitting ladder.

### Rules and ink

Thin rules only: 0.5pt, `NAVY` under the header band, `#DDDDDD` between the columns and above
the footer. No table borders anywhere on the one-pager — the multi-page document uses bordered
tables, but at this density borders read as noise. No gradient fills, no drop shadows, no chart
furniture. The scorecard bar glyph is a single flat `NAVY` rectangle on a `ZEBRA_FILL` track.

### Footer

The house footer applies, at 6.5pt rather than 7pt:

```
[Document Title]   [PAGE#]   Compiled on [DATE] by TEN Capital Network   [logo]
```

centred, Open Sans, `GREY`, logo `assets/TEN_Capital_logo_footer.png` (631 × 232 px RGBA) at
0.67" × 0.25". `[PAGE#]` resolves to `1` and is retained rather than dropped, so a one-pager
filed alongside a `--full` memo carries the same furniture.

Open Sans may not be installed on the rendering host. The renderer falls back to Arial and
records the substitution in `method_notes` rather than failing the run or silently swapping to
a default the layout was not measured against.

---

## 4. Fields, by zone

Every field below maps to a path in the validated `Assessment` (`src/deckpager/analysis/schema.py`).
The renderer reads only from this contract — it never reformats, re-ranks, or re-words. Anything
the model did not supply renders as a defined empty state, never as an invented value.

### 4.1 Header band

| Field | Type | Source | Empty state |
|---|---|---|---|
| `company_name` | text | `company_name` | Required; run fails without it |
| `one_line_description` | text | `one_line_description` | Omit the line |
| `stage_signal` | text | `stage_signal` | Omit the chip-row entry |
| `round_ask` | text | `deal.ask` *(field pending — see §7)* | `Ask not stated in deck` |
| `sector` | text | `deal.sector` *(field pending — see §7)* | `Sector not stated` |
| `deck_date` | text | `deal.deck_date` *(field pending — see §7)* | `Deck undated` |
| `analysis_date` | text | `meta.generated_at`, `Month D, YYYY` | Required; always present |
| `recommendation` | enum | `ic_view.recommendation` | Required |
| `confidence` | enum | `ic_view.confidence` | Required |

`recommendation` and `confidence` render as one unit: the chip, with the confidence word beneath
it. When confidence has been auto-downgraded by the gates in §6, the label carries a trailing
dagger (`MEDIUM†`) and the reason joins the provenance line. A downgrade is never silent.

### 4.2 Left column

| Field | Type | Source | Shape |
|---|---|---|---|
| `executive_summary` | text | `executive_summary` | 100–130 words, prose, no bullets. Truncated at a sentence boundary by the fitting ladder; floor 80 words |
| `top_strengths` | list[text] | `ic_view.biggest_strengths[:3]` | Exactly 3 when available; fewer renders fewer, never pads |
| `top_concerns` | list[text] | `ic_view.biggest_concerns[:3]` | Same |
| `risk_row` | list[risk] | `risks`, filtered and ordered by §4.5 | Exactly 5 rows |

Each `risk_row` entry:

| Sub-field | Type | Source |
|---|---|---|
| `name` | enum | `risks[].name` |
| `level` | enum | `risks[].level` |
| `reason` | text | `risks[].rationale`, first clause only |

The risk row shows **5 of the 7** categories the schema requires. `Leadership Scalability` and
`Talent Attraction` are assessed and stored, and appear in the `--full` memo and the JSON, but
not on the one-pager — five is what fits at a readable size. This is a display decision, not an
analytical one; nothing is dropped from the analysis.

### 4.3 Right sidebar

| Field | Type | Source | Shape |
|---|---|---|---|
| `scorecard` | list[score] | `scorecard` | Exactly 11 rows, fixed order (§4.5) |
| `computed_overall` | number | `computed_overall` | The weighted figure from `config/weights.toml` |
| `model_overall` | number | `overall_investability` | The model's own number |
| `score_divergence` | flag | set when \|model − computed\| > 1.0 | Renders a dagger beside the figure |

Each `scorecard` row:

| Sub-field | Type | Source | Empty state |
|---|---|---|---|
| `name` | enum | `scorecard[].name` | Fixed vocabulary |
| `value` | int 1–10 or null | `scorecard[].value` | `—  insufficient data`, italic, `GREY` |
| `bar` | glyph | derived from `value` | No track drawn when null |

The large figure is **`computed_overall`**, not the model's number: the headline is what the
house weights produce. When they disagree by more than 1.0 the figure carries a dagger and the
provenance line states both. A null score is excluded from the weighted mean and the remaining
weights renormalize — an unscored category widens the error bars rather than dragging the total.

### 4.4 Footer

| Field | Type | Source | Shape |
|---|---|---|---|
| `diligence_questions` | list[text] | `ic_view.diligence_questions[:5].question` | Numbered 1–5, terse, one line each. Floor 3 under the fitting ladder |
| `provenance` | composite | see below | Single 6.5pt line |

The provenance line, in order, ` · ` separated:

| Part | Source |
|---|---|
| Deck filename | `meta.source_filename` |
| Deck hash | `meta.sha256[:12]` *(field pending — see §7)* |
| Slide count | `meta.slide_count` |
| Model and provider | `meta.model`, `meta.provider` *(field pending — see §7)* |
| Timestamp | `meta.generated_at`, ISO 8601 |
| Evidence summary | derived from the grounding report |
| Confidence-downgrade reason | present only when confidence was capped |

Evidence summary format, verbatim:

```
Evidence: {n} claims — {verified} verified / {inferred} inferred / {speculative} speculative
```

This line is the reason the artifact can be trusted, and it is the last thing removed. It is not
a rung on the fitting ladder — the exec summary loses words before the provenance loses parts.

### 4.5 Fixed vocabularies

The canonical lists live in `src/deckpager/analysis/schema.py` as `SCORECARD_ORDER` and
`RISK_ORDER`, and in `config/weights.toml`. Change the framework there and both the skeleton and
the rendered document follow. Never restate them in the renderer.

**Scorecard rows, in render order (11):** Founder · Executive Team · Scientific Credibility ·
Commercial Readiness · Leadership · Vision · Storytelling · Execution Capability ·
Capital Efficiency · Fundraising Readiness · Overall Investability

**Risk categories on the one-pager, in render order (5):** Execution · Technology ·
Commercialization · Regulatory · Go-to-Market

**Risk categories assessed but held for the memo (2):** Leadership Scalability · Talent Attraction

**Risk levels:** Low · Medium · High · Critical

**Recommendations:** ADVANCE_TO_PARTNER_MEETING · MORE_DILIGENCE · PASS

**Confidence:** HIGH · MEDIUM · LOW

**Evidence bases:** FACT · INFERENCE · SPECULATION

---

## 5. Fitting ladder

Applied in this fixed order, re-measuring the rendered page count after each step, stopping at
the first configuration that fits. Order is by *cost to the reader*: prose the analyst can infer
goes before questions that change decisions, which go before typography, which goes before
raising an error.

| # | Reduction | Floor |
|---|---|---|
| 1 | Truncate the executive summary at a sentence boundary | 80 words |
| 2 | Drop diligence questions 5 → 4 | 3 questions |
| 3 | Shorten each risk reason to its first clause | — |
| 4 | Step body 9pt → 8.5pt → 8pt | 8pt |
| 5 | Tighten line-height 1.35 → 1.2 | 1.2 |
| — | Raise `OnePagerOverflowError` with what was tried and what still overflowed | — |

The scorecard, the recommendation chip, the risk categories, and the provenance line are **not**
reducible. They are the screening decision; a one-pager missing them has failed at its job even
if it fits on one page.

Fitting state is recorded in the run's `method_notes` so a reader can tell a summary that ran
short from one that was truncated to fit.

---

## 6. Gates the template assumes

Enforced in `analysis/scoring.py` before the renderer is called. Listed here because the layout
has defined empty and flagged states for each, and a renderer that never sees them will silently
misrepresent the analysis.

| Gate | Condition | Effect on the page |
|---|---|---|
| Null score | Deck gives no basis for a category | `—  insufficient data`; entry added to `data_gaps` |
| Score divergence | \|model − computed\| > 1.0 | Dagger on the figure; both numbers in the provenance line |
| Confidence cap: speculation | >30% of claims are SPECULATION | Confidence downgraded; dagger and reason |
| Confidence cap: gaps | More than 3 scorecard categories null | Confidence downgraded; dagger and reason |
| Grounding downgrade | A FACT quote does not match the cited slide | Claim recorded as INFERENCE; counts shift in the evidence summary |

---

## 7. Fields this template needs that the schema does not yet carry

Flagged rather than invented. Each renders its defined empty state until the field lands, so the
template is implementable today and improves as ingestion and schema catch up.

| Field | Needed for | Lands in |
|---|---|---|
| `deal.ask`, `deal.sector`, `deal.deck_date` | Header band | M2 schema change |
| `meta.sha256` | Provenance line | M1 (ingestion computes it) |
| `meta.provider` | Provenance line | M3 (provider abstraction) |
| `computed_overall`, `score_divergence` | Sidebar figure | M4 (scoring) |
| `data_gaps`, grounding counts | Empty states, evidence summary | M4 (grounding) |

Two vocabulary mismatches between the schema as it stands and the spec this template follows,
both resolved in M2:

| Vocabulary | Schema today | Template and spec §6 |
|---|---|---|
| Confidence | `High` · `Medium` · `Low` | `HIGH` · `MEDIUM` · `LOW` |
| Advance decision | `ic_view.advance_to_partner_meeting: bool` | `ic_view.recommendation`, three-valued |

A boolean cannot express `MORE_DILIGENCE`, which is the most common real outcome of a screen —
so the chip has three states and the schema grows an enum to match.

---

## 8. Using the template

```python
from pathlib import Path
from deckpager.render.onepager_template import (
    blank_onepager_data,
    build_onepager,
    validate_onepager_data,
)

data = blank_onepager_data()  # every key present, pre-expanded to the fixed rows
# ... populate from the validated Assessment ...
problems = validate_onepager_data(data)  # [] means the data fits the template
build_onepager(Path("Company_onepager.pdf"), data, paper="letter")
```

`blank_onepager_data()` returns the full data contract with `[TO BE COMPLETED]` placeholders,
pre-expanded to the fixed rows of the framework (11 scorecard rows, 5 risk rows, 5 diligence
questions), so callers fill values in place rather than reconstructing the shape.

`validate_onepager_data()` checks that every key is present, that the scorecard has exactly
eleven rows in the canonical order, that the risk row has exactly five, that the recommendation
and confidence values are in vocabulary, and that the provenance parts are populated. It returns
a list of problems; empty means the data fits.

`build_onepager()` renders and enforces §1: it applies the §5 ladder, verifies the output is one
page, and raises `OnePagerOverflowError` rather than returning a two-page file.
