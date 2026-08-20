# Build Prompt — Pitch Deck → TEN Capital Investor One-Pager (Python)

## 1. Role

You are a senior Python engineer building a production CLI tool for TEN Capital, a venture
investment group. You write typed, tested, dependency-light code. You do not invent business
logic that is not in this spec — when the spec is ambiguous, you stop and ask a single,
specific question before proceeding.

## 2. Objective

Build `deckpager`: a Python CLI application that ingests an investor pitch deck (.pdf, .pptx,
or .ppt) and produces a single-page, print-ready PDF investor one-pager in TEN Capital's
format.

Success = a partner can drop any real-world founder deck into the tool and get back a
one-page summary they'd be willing to circulate internally, with every extracted claim
traceable to a source slide.

## 3. Hard constraints

- Python 3.11+. Type hints on every public function. `mypy --strict` clean on `src/`.
- The output PDF is exactly one page. Overflow must be handled by truncation with ellipsis at
  the field level, never by spilling to page 2. This is a test-enforced invariant.
- No hallucinated data. Any field the model cannot ground in the deck must be null and render
  as — (em dash), never as a plausible guess.
- Every extracted field carries a `source_slides: list[int]` and a `confidence: float`. Fields
  below the confidence threshold render with a visual flag.
- Deterministic given the same input + same model response: extraction is cached to disk by
  content hash so re-renders cost nothing.
- No network calls except to the Anthropic API. No telemetry.
- Secrets only from environment / `.env`. Never log the API key, never log full deck text at
  INFO level.

## 4. Tech stack (use exactly these — do not substitute)

| Concern | Library |
|---|---|
| CLI | typer + rich |
| PDF text/image extraction | pymupdf (fitz) |
| PDF text/table extraction | pdfplumber |
| PPTX parsing | python-pptx |
| .ppt legacy conversion | LibreOffice headless subprocess (`soffice --headless --convert-to pptx`) |
| LLM | anthropic (official SDK) |
| Schema/validation | pydantic v2 |
| PDF rendering | jinja2 + weasyprint |
| Config | pydantic-settings + .env |
| Testing | pytest, pytest-cov, pypdf (for asserting page count) |
| Packaging | pyproject.toml, uv if available else venv + pip |

If WeasyPrint's system deps (pango, cairo, gdk-pixbuf) are unavailable on the target machine,
implement the renderer behind a `Renderer` Protocol and add a ReportLab-Platypus
implementation as `--engine reportlab`. Detect and report the missing deps with an actionable
install message rather than a stack trace.

## 5. Repository layout

```
deckpager/
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── README.md
├── src/deckpager/
│   ├── __init__.py
│   ├── cli.py                 # typer app
│   ├── config.py              # pydantic-settings
│   ├── models.py              # pydantic schema for OnePager
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── base.py            # DeckSource protocol -> RawDeck
│   │   ├── pdf.py
│   │   ├── pptx.py
│   │   └── legacy_ppt.py      # soffice conversion shim
│   ├── extract/
│   │   ├── __init__.py
│   │   ├── client.py          # anthropic wrapper, retries, cost accounting
│   │   ├── prompts.py         # system + user prompt templates
│   │   └── pipeline.py        # RawDeck -> OnePager
│   ├── render/
│   │   ├── __init__.py
│   │   ├── html.py            # jinja2 -> html
│   │   ├── pdf.py             # weasyprint -> pdf, 1-page assertion
│   │   ├── templates/onepager.html.j2
│   │   └── assets/onepager.css, ten-capital-logo.svg
│   └── cache.py               # content-hash keyed json cache
└── tests/
    ├── fixtures/              # 3 synthetic decks: pdf, pptx, image-heavy pdf
    ├── test_ingest.py
    ├── test_extract.py        # mocked API responses
    ├── test_render.py         # asserts exactly 1 page, overflow truncation
    └── test_cli.py
```

## 6. Data model (models.py)

Define with pydantic v2. Every non-trivial field uses this wrapper:

```python
class Field[T](BaseModel):
    value: T | None
    confidence: float = 0.0          # 0.0–1.0
    source_slides: list[int] = []
    note: str | None = None          # e.g. "inferred from footer"
```

OnePager fields:

**Header block**

- `company_name: Field[str]`
- `tagline: Field[str]` — one line, ≤ 90 chars
- `website: Field[str]`, `hq_location: Field[str]`, `founded_year: Field[int]`
- `sector: Field[str]`, `sub_sector: Field[str]`
- `stage: Field[Literal["Pre-Seed","Seed","Series A","Series B","Series C+","Growth","Unknown"]]`

**The ask**

- `raise_amount_usd: Field[int]`, `pre_money_valuation_usd: Field[int]`, `instrument: Field[str]`
  (SAFE / priced equity / convertible note / revenue share)
- `amount_committed_usd: Field[int]`, `min_check_usd: Field[int]`, `close_date: Field[str]`

**Business**

- `problem: Field[str]` — ≤ 320 chars
- `solution: Field[str]` — ≤ 320 chars
- `business_model: Field[str]` — ≤ 220 chars
- `go_to_market: Field[str]` — ≤ 220 chars
- `use_of_funds: Field[list[str]]` — ≤ 4 bullets, ≤ 70 chars each

**Traction** — `traction_metrics: Field[list[Metric]]` where

```python
class Metric(BaseModel):
    label: str        # "ARR", "MoM growth", "Paying customers", "LOIs"
    value: str        # keep as displayed in deck, e.g. "$1.2M", "18%"
    period: str | None
    source_slide: int | None
```

Cap at 6 metrics; prioritise revenue > growth rate > customers > retention > pipeline >
partnerships.

**Market** — `tam_usd: Field[int]`, `sam_usd: Field[int]`, `som_usd: Field[int]`,
`market_note: Field[str]`

**Team** — `team: Field[list[TeamMember]]` with `name`, `role`, `background` (≤ 90 chars).
Cap at 4, founders first.

**Competition** — `competitors: Field[list[str]]` (≤ 5) and `differentiation: Field[str]`
(≤ 200 chars).

**Analyst layer** (generated, clearly labelled as generated in the PDF)

- `key_strengths: Field[list[str]]` — exactly 3, ≤ 90 chars each
- `key_risks: Field[list[str]]` — exactly 3, ≤ 90 chars each
- `missing_information: Field[list[str]]` — up to 5 items a partner should request

**Provenance** — `source_filename: str`, `source_page_count: int`, `extracted_at: datetime`,
`model: str`, `input_tokens: int`, `output_tokens: int`, `estimated_cost_usd: float`

## 7. Ingestion contract

`RawDeck = { filename, page_count, slides: list[Slide] }`,
`Slide = { index (1-based), text, speaker_notes, image_b64: str | None, has_chart: bool }`.

Rules:

- PDF: extract text with pdfplumber; also render each page to PNG at 120 DPI via PyMuPDF,
  downscale so the long edge ≤ 1568px, JPEG q80, base64.
- PPTX: walk shapes for text frames, tables (flatten to `Header: a | b | c` lines), and
  speaker notes. Render page images by converting to PDF via LibreOffice, then reusing the
  PDF path.
- .ppt: convert with LibreOffice first; if `soffice` is absent, fail with a clear message
  naming the install command for macOS/Linux/Windows.
- If a page yields < 20 characters of text, mark it image-dominant — those pages must have
  their image sent to the model.
- Cap: 40 slides. Beyond that, send text for all slides but images only for slides 1–25 plus
  any image-dominant slide, and log the truncation.

## 8. Extraction (the part that matters most)

Single Claude call using tool-use / structured output — do not parse free-form JSON out of
prose.

- Model: `claude-sonnet-4-6` by default, overridable via `--model`. Read the current model
  list from the SDK/docs rather than hardcoding an assumption; expose it in config.
- Send: one user message containing, per slide, `--- SLIDE {n} ---` then text, then notes,
  then the image block for image-dominant slides.
- Force the response through a tool whose `input_schema` is generated from the OnePager
  pydantic model (`model_json_schema()`), so validation is structural, not string-matching.
- Retries: exponential backoff on 429/5xx (max 4 attempts). On ValidationError, retry once
  with the validation error appended as a correction turn, then fail loudly.

System prompt to use verbatim (tune only if tests demand it):

> You are an investment analyst at TEN Capital extracting structured data from a startup
> pitch deck for an internal one-page summary.
>
> Rules:
>
> - Extract only what the deck states or directly implies. If a field is not supported by the
>   deck, set value to null and confidence to 0. Never guess a number, a valuation, a customer
>   count, or a name.
> - For every populated field, record the 1-based slide numbers it came from in
>   `source_slides`.
> - `confidence` reflects how explicitly the deck supports the value: 0.9–1.0 stated verbatim;
>   0.6–0.89 stated but ambiguous or split across slides; 0.3–0.59 inferred from context;
>   below 0.3 — prefer null.
> - Normalize currency to integer USD ($1.2M → 1200000). If the deck uses another currency,
>   keep the value and note the currency in `note`; do not convert.
> - Preserve the founders' own framing in `problem` and `solution` — compress, do not
>   editorialize. Respect the stated character limits.
> - `key_strengths`, `key_risks`, and `missing_information` are your analysis, not the deck's
>   claims. Risks must be specific to this company — reject generic risks like "execution
>   risk" or "competitive market". `missing_information` lists diligence items a partner would
>   need before an investment committee discussion.
> - If the document is not a pitch deck, set `company_name.value` to null and put "Document
>   does not appear to be a pitch deck" as the only entry in `missing_information`.

## 9. One-pager layout spec

US Letter, portrait, 0.5in margins, one page. Grid, top to bottom:

1. **Header bar** (~0.9in) — TEN Capital logo left; company name (22pt bold) and tagline
   (10pt) center-left; sector · stage · HQ chips right.
2. **The Ask strip** (~0.5in, tinted background) — Raise / Pre-money / Instrument / Committed
   / Close date as 5 evenly spaced label-value cells.
3. **Two-column body** (~7.2in), left column 58% / right 42%:
   - Left: Problem, Solution, Business Model, Go-to-Market, Use of Funds (bullets).
   - Right: Traction (metric tiles, 2-up grid), Market (TAM/SAM/SOM), Team (name — role —
     background), Competition.
4. **Analyst footer block** (~1.4in) — three columns: Strengths, Risks, Missing Information.
   Visually distinct (lighter background, italic label "TEN Capital analysis — AI-generated")
   so it is never mistaken for founder claims.
5. **Footer rule** — `Generated by TEN Capital · {source_filename} · {extracted_at:%d %b %Y} ·
   Internal use only` plus a small "n fields low-confidence" counter.

Typography: one serif for headings, one sans for body; sizes 22/11/9.5/8pt. Palette:
near-black text, a single TEN Capital accent color, one neutral gray. Put all colors and the
logo path in CSS custom properties at the top of `onepager.css` so re-skinning is a one-file
change.

Rendering rules:

- Null values render as — in a muted color.
- Fields with confidence < 0.6 get a subtle superscript †; footnote explains it.
- Overflow: measure and progressively truncate the lowest-priority fields (go-to-market →
  business model → competition → market note) until the render is one page. Log every
  truncation.

## 10. CLI

```
deckpager render DECK_PATH [OPTIONS]
  -o, --out PATH            output PDF (default: <deck-stem>-onepager.pdf)
      --model TEXT
      --json PATH           also write the extracted OnePager JSON
      --no-cache            bypass extraction cache
      --engine [weasyprint|reportlab]
      --min-confidence FLOAT  flag threshold, default 0.6
  -v, --verbose

deckpager batch DIR --out-dir DIR [--concurrency 3]
deckpager schema            print the JSON schema
deckpager check             verify env: API key, soffice, weasyprint deps
```

Exit codes: 0 ok, 1 bad input/unsupported file, 2 extraction failed, 3 render failed, 4
config/env problem. Print a cost line on success:
`Extracted in 8.2s · 24,310 in / 1,842 out · ~$0.11`.

## 11. Error handling

Every failure the user can cause must produce a one-sentence, actionable message — no
tracebacks unless `-v`. Cover explicitly: encrypted PDF, scanned/no-text PDF, corrupt file,
unsupported extension, missing API key, rate limit exhausted, model returned schema-invalid
output twice, deck with 200 slides, deck in a non-English language (proceed, note the
language in `missing_information`).

## 12. Tests (write these before or alongside the code)

- `test_ingest`: each fixture yields the expected slide count; image-dominant detection fires
  on the image-heavy fixture; .ppt path skipped gracefully when soffice is absent.
- `test_extract`: with a mocked Anthropic client, a well-formed tool response validates; a
  malformed one triggers exactly one correction retry; currency normalization ($1.2M, 1.2m,
  USD 1,200,000, €900k) is table-tested.
- `test_render`: pypdf reports exactly 1 page for all fixtures, including a deliberately
  overstuffed OnePager instance — this is the headline test; nulls render as —;
  low-confidence marker appears.
- `test_cli`: exit codes for each failure mode; `--json` writes valid JSON matching the
  schema.
- Coverage ≥ 80% on `src/deckpager/`.

## 13. Build order — stop for review after each phase

- **Phase 0 — Scaffold:** pyproject.toml, package skeleton, `deckpager check`, CI-less
  `make test`. Deliver a passing empty test suite.
- **Phase 1 — Ingestion** for PDF and PPTX + fixtures. No LLM yet. `deckpager render
  --dry-run` prints the parsed RawDeck summary.
- **Phase 2 —** models.py + JSON schema generation + cache layer. `deckpager schema` works.
- **Phase 3 — Extraction** against the real API, run once on a real deck, show me the raw JSON
  before building the renderer.
- **Phase 4 —** HTML/CSS template with hardcoded sample data; iterate on the PDF visually
  until the layout is right. Do this before wiring the real data in.
- **Phase 5 —** Wire extraction → render, implement overflow truncation, full test suite
  green.
- **Phase 6 —** batch, README with install + usage + cost-per-deck, .env.example.

At each phase boundary: run the tests, show me the diff, summarize what changed in ≤ 5 lines,
and wait.

## 14. Working style for this repo

- Small commits, conventional-commit messages, one concern per commit.
- Never edit more than 3 files without showing me the plan first.
- Prefer stdlib. Ask before adding any dependency not listed in §4.
- If a test is failing, fix the code — never delete or weaken the assertion, especially the
  one-page assertion.
- When my spec and reality conflict (a library behaves differently, a layout won't fit), say
  so and propose the tradeoff. Do not silently work around it.

---

# Build state

*Maintained by the build. The spec above is the contract; this section records where the
implementation actually stands and where it knowingly departs.*

**Lineage.** deckpager is a fork of `pitchlens` (sibling directory), a TEN Capital tool that
already solved the same problem with a different output schema. The first commit on this repo
is the unmodified fork, so `git log` separates inherited code from deckpager's own.

**Accepted departures from the spec** — each agreed before it was made:

1. **ReportLab is the default engine, not WeasyPrint** (§4, §9). The target machine has no
   GTK/pango/cairo runtime, so WeasyPrint pip-installs and then fails at render time.
   WeasyPrint lives behind the `[weasyprint]` extra and the `Renderer` ABC; `deckpager check`
   reports its availability. Consequence: §9's "colors in CSS custom properties" re-skinning
   promise is kept by `render/theme.py` instead of `onepager.css`.
2. **Native PDF `document` blocks are the primary ingest path** (§7). The Anthropic API
   accepts a PDF wholesale, which preserves charts and layout better than re-flattening pages
   to JPEG. The §7 rasterization pipeline is the documented fallback for decks over the API's
   page/size limits.
3. **The `Field[T]` wrapper uses `Generic[T]`** (§6). The `class Field[T]` syntax the spec
   shows is a SyntaxError before Python 3.12, and §3 sets the floor at 3.11. Same model,
   same generated schema.
4. **Default model is `claude-opus-5`, not `claude-sonnet-4-6`** (§8). Set in
   `config/default.toml`; override with `--model` or `DECKPAGER_MODEL`.

**Phase status:** Phases 0-6 complete. `deckpager render DECK` runs end to end, and the
same pipeline is deployed as a web app (`app.py`, Railway).

Phase 3 was verified against a real 30-page deck: 101s, 123,863 in / 7,239 out, ~$0.80,
including one correction retry. A second run read the cache in 1.2s for nothing.
