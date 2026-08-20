# TEN Capital — Investor One-Pager Template

Structure and field map for every one-pager deckpager generates. Company-agnostic: nothing
here is specific to a deck, a company, or a run. The renderer owns *structure and formatting
only*; all content arrives through the data contract in §4.

This document is a contract, not documentation. `tests/test_template_spec.py` asserts that
the field map, the vocabularies, and the reduction ladder below match
`src/deckpager/models.py`, `src/deckpager/render/style.py`, and
`src/deckpager/render/onepager.py`. If the code changes and this file does not, the tests
fail.

Placeholders are written `{field_name}`. Every one of them resolves to a `Field` from the
extraction, and every one may be null.

---

## 1. The hard constraint

**One page. Always.** Not "usually one page", not "one page for a short deck". A second page
is a defect, not an overflow: the artifact exists to be read at a glance in a partner
meeting, and a two-page one-pager is a memo.

Overflow is resolved by the reduction ladder in §6, which ends in field-level truncation with
an ellipsis. It never spills to a second page, and it never silently drops content — every
reduction is reported to the caller and recorded in `provenance.truncations`.

The renderer measures **geometry**, not page count. ReportLab paints past the bottom edge
without ever starting a second page, so a page count of 1 is no evidence that anything fits.

---

## 2. Document map

Single page, five bands, no page breaks. The body is a two-column band between the ask strip
and the analyst block.

| Order | Band | Width | Height | Content |
|---|---|---|---|---|
| 1 | Header | full | 62pt | Logo · company name · tagline · website · sector/stage/HQ chips |
| 2 | Ask strip | full | 38pt | Five label-value cells on a tint |
| 3 | Body — left | 58% | flexible | Problem · Solution · Business Model · Go-to-Market · Use of Funds |
| 4 | Body — right | 42% | flexible | Traction · Market · Team · Competition |
| 5 | Analyst block | full | measured | Strengths · Risks · Request From Founder |
| 6 | Footer rule | full | 14pt | Provenance line and the low-confidence count |

Page: US Letter default, A4 via `--paper a4`. Margins 0.5in on all sides. Column gap 14pt.
The body takes whatever the fixed bands leave; the analyst block is measured from its content
and pinned above the footer.

The bands are drawn as part of the page body, not as running headers: there is only one page,
so there is nothing to run.

---

## 3. Formatting specification

`src/deckpager/render/style.py` is the single source of truth for every value in this
section. Re-skinning the document is an edit to that one file.

### Palette

Three values doing three jobs, plus two tints for the bands that recede. A fourth hue has to
earn its place by meaning something.

| Token | Hex | Used for |
|---|---|---|
| `INK` | `#14181F` | Body text and values. Not pure black — #000 blooms against 8pt type in print |
| `ACCENT` | `#1F3864` | Section headings, rules, bullets, chip text |
| `MUTED` | `#6B7280` | Labels, tagline, provenance, and the em dash that means "the deck is silent" |
| `TINT_ASK` | `#EEF2F8` | Ask strip, chips, metric tiles |
| `TINT_ANALYST` | `#F6F5F2` | Analyst block — deliberately a different tint from the ask strip |
| `RULE` | `#D8DCE3` | Hairlines |

### Type

One serif for headings, one sans for body (both ReportLab base-14, so no font file has to
resolve at render time).

| Token | Face | Size | Used for |
|---|---|---|---|
| `SIZE_COMPANY` | serif bold | 22pt | Company name |
| `SIZE_TAGLINE` | sans | 10pt | Tagline |
| `SIZE_ASK_VALUE` | serif bold | 11pt | Ask cell values, TAM/SAM/SOM figures |
| `SIZE_METRIC_VALUE` | sans bold | 11pt | Metric tile values |
| `SIZE_HEADING` | serif bold | 9.5pt | Section headings |
| `SIZE_BODY` | sans | 8pt | All body prose and bullets |
| `SIZE_CHIP` | sans | 7.5pt | Header chips, website |
| `SIZE_LABEL` | sans | 6.5pt | Cell labels, tile labels, analyst-block label |
| `SIZE_FOOTER` | sans | 6.5pt | Provenance line |
| `SIZE_DAGGER` | sans | 5.5pt | Low-confidence marker, raised 3pt |

Line height 1.30 for body prose.

---

## 4. Field map

Every field below is a `Field` carrying `value`, `confidence`, `source_slides`, and an
optional `note`. Character and list limits are enforced by the schema, not by the renderer —
a violation fails validation at extraction time rather than overflowing the page.

### 4.1 Header

| Placeholder | Limit | Rendering |
|---|---|---|
| `{company_name}` | 120 chars | 22pt serif bold. Shrinks to fit beside the chips; falls back to "Unnamed company" when null |
| `{tagline}` | 90 chars | 10pt, one line, truncated to the available width |
| `{website}` | 120 chars | 7.5pt, inline after the tagline, separated by a middot |
| `{sector}` · `{stage}` · `{hq_location}` | 120 / vocabulary / 120 | Right-aligned chips, in that order. A null chip is omitted rather than shown empty |

`{stage}` is a closed vocabulary: **Pre-Seed · Seed · Series A · Series B · Series C+ ·
Growth · Unknown**.

### 4.2 Ask strip

Five cells, evenly spaced, in this order. Amounts render as `$2M`, `$750K`, `$1.2B`.

| Cell label | Placeholder | Notes |
|---|---|---|
| RAISE | `{raise_amount_usd}` | Integer USD |
| PRE-MONEY | `{pre_money_valuation_usd}` | Integer USD |
| INSTRUMENT | `{instrument}` | Wraps to two lines; the cell a partner reads most carefully |
| COMMITTED | `{amount_committed_usd}` | Integer USD |
| CLOSE | `{close_date}` | As stated in the deck, not normalized to a date |

A non-USD amount keeps its stated value and records the currency in `note`. It is never
converted: the exchange rate is not in the deck.

### 4.3 Body — left column

| Section heading | Placeholder | Limit |
|---|---|---|
| PROBLEM | `{problem}` | 320 chars |
| SOLUTION | `{solution}` | 320 chars |
| BUSINESS MODEL | `{business_model}` | 220 chars |
| GO-TO-MARKET | `{go_to_market}` | 220 chars |
| USE OF FUNDS | `{use_of_funds}` | ≤ 4 bullets, 70 chars each |

`{problem}` and `{solution}` preserve the founders' own framing, compressed but not
editorialized.

### 4.4 Body — right column

| Section heading | Placeholder | Limit | Rendering |
|---|---|---|---|
| TRACTION | `{traction_metrics}` | ≤ 6 | 2-up grid of tiles. Each tile is `value` over `label · period` |
| MARKET | `{tam_usd}` `{sam_usd}` `{som_usd}` | — | Three figures across one row, labelled TAM / SAM / SOM |
| | `{market_note}` | 220 chars | Beneath the figures |
| TEAM | `{team}` | ≤ 4 | `name` bold, then `role — background`. Founders first |
| COMPETITION | `{competitors}` | ≤ 5 | Joined with middots on one run |
| | `{differentiation}` | 200 chars | Beneath the competitor list |

A metric tile sizes its type to its value: display size for something numeral-shaped, body
size for a sentence. The schema permits a 120-character metric value and real decks use it.

Metric priority when more than six are found: **revenue > growth rate > customers >
retention > pipeline > partnerships.**

### 4.5 Analyst block

Three columns, on its own tint, labelled *TEN Capital analysis — AI-generated* in italics.
This block is deckpager's judgment, not the deck's claims, and its styling exists so it can
never be read as something the founders wrote.

| Column heading | Placeholder | Limit |
|---|---|---|
| STRENGTHS | `{key_strengths}` | Exactly 3, 90 chars each |
| RISKS | `{key_risks}` | Exactly 3, 90 chars each |
| REQUEST FROM FOUNDER | `{missing_information}` | ≤ 5, 200 chars each |

Risks must be specific to the company. Generic risks — "execution risk", "competitive
market" — are rejected at extraction.

### 4.6 Footer rule

```
Generated by TEN Capital · {source_filename} · {extracted_at:%d %b %Y} · Internal use only
```

Right-aligned on the same rule: `{n} field(s) † below {threshold} confidence — verify before
relying on it`, omitted entirely when nothing is flagged.

### 4.7 Extracted but not rendered

Three fields are extracted and written to the JSON but have no place on this page:
`{founded_year}`, `{sub_sector}`, `{min_check_usd}`. They are excluded from the
low-confidence count, because flagging a field the reader cannot find is worse than silence.
Adding any of them to the layout means adding it to `RENDERED_FIELDS` in the same change.

---

## 5. Rendering rules

1. **Null renders as an em dash** (`—`) in `MUTED`. Never a plausible guess, never an empty
   space that reads as an oversight.
2. **Low confidence carries a dagger.** A populated field below the threshold (default 0.60,
   `--min-confidence`) gets a raised `†` immediately after its value, wherever it is printed —
   including inside a header chip. The footer explains the marker and counts the flagged
   fields that appear on the page.
3. **Every truncation is reported.** The caller receives the list; it is also recorded in
   `provenance.truncations`. A silent cut is indistinguishable from a bug.
4. **Measure and draw are one code path.** Every drawing method takes an optional canvas and
   returns the height it consumed; with `None` it measures, with a canvas it draws exactly
   what it measured.

---

## 6. Reduction ladder

Applied in order until the content fits. Prose an analyst can infer goes before content that
changes a decision, which goes before typography, which goes before truncating everything.

| # | Rung | Gives up |
|---|---|---|
| 1 | Truncate go-to-market | Left column prose |
| 2 | Truncate business model | Left column prose |
| 3 | Trim the competitor list to 3 | Right column |
| 4 | Truncate the market note | Right column |
| 5–6 | Diligence requests 5 → 4 → 3 | Analyst block |
| 7–8 | Traction tiles 6 → 5 → 4 | Right column |
| 9 | Team 4 → 3 | Right column |
| 10 | Body type 8pt → 7.5pt | Legibility |
| 11 | Line height 1.30 → 1.18 | Legibility |
| 12–17 | Prose scaled to 75% → 55% → 40% → 28% → 18% → 12% of its length | Everything |

Two rules govern the ladder, and both exist because a page is two independent columns:

* **A rung that does not reduce the measured overflow is reverted.** Most rungs shorten only
  one column, so applying one blindly can discard a section without relieving the pressure.
* **After a fit, every applied rung is offered back** — newest first — and kept only if the
  page still fits without it. The rungs are coarse; the first layout that fits usually
  overshoots.

7.5pt is the type floor: below that the page stops being readable across a meeting table.
Rungs 12–17 are the last resort and always succeed, which is what makes the one-page
guarantee hold for any schema-valid document rather than for typical ones.

---

## 7. Validation contract

A generated one-pager is correct when all of the following hold:

- [ ] The PDF has exactly one page.
- [ ] Measured overflow at the chosen layout is zero.
- [ ] Every populated field carries at least one source slide number.
- [ ] Every null field renders as `—`.
- [ ] Every rendered field below the confidence threshold carries a `†`, and the footer count
      equals the number of such fields.
- [ ] The analyst block is visually distinct from the founder-sourced bands and carries its
      AI-generated label.
- [ ] The footer names the source deck and the extraction date.
- [ ] Every reduction applied appears in `provenance.truncations`.

---

## 8. Out of scope

This template covers the single-page screening artifact only. A multi-page memo, a portfolio
comparison sheet, and the investment committee brief are separate artifacts with their own
templates and their own field maps.
