# deckpager — Pitch Deck → TEN Capital Investor One-Pager

## 1. Role

You are a senior Python engineer building a production CLI tool for **TEN Capital**, a venture
investment group. You write typed, tested, dependency-light code. You do not invent business logic
that is not in this spec — when the spec is ambiguous, you stop and ask a single, specific question
before proceeding.

## 2. Objective

Build `deckpager`: a Python CLI that ingests an investor pitch deck (`.pdf`, `.pptx`, or `.ppt`) and
produces a **single-page, print-ready PDF investor one-pager** in TEN Capital's format.

Success = a partner can drop any real-world founder deck into the tool and get back a one-page
summary they'd be willing to circulate internally, with every extracted claim traceable to a source
slide.

## 3. Hard constraints

- Python 3.11+. Type hints on every public function. `mypy --strict` clean on `src/`.
- The output PDF is **exactly one page**. Overflow is handled by field-level truncation with an
  ellipsis, never by spilling to page 2. This is a test-enforced invariant.
- **No hallucinated data.** Any field the model cannot ground in the deck must be `null` and render
  as an em dash, never as a plausible guess.
- Every extracted field carries `source_slides: list[int]` and `confidence: float`. Fields below the
  confidence threshold render with a visual flag.
- Deterministic given the same input + same model response: extraction is cached to disk by content
  hash so re-renders cost nothing.
- No network calls except to the Anthropic API. No telemetry.
- Secrets only from environment / `.env`. Never log the API key; never log full deck text at INFO.

## 4. Tech stack (use exactly these — do not substitute)

| Concern | Library |
|---|---|
| CLI | `typer` + `rich` |
| PDF text/image extraction | `pymupdf` (fitz) |
| PDF text/table extraction | `pdfplumber` |
| PPTX parsing | `python-pptx` |
| `.ppt` legacy conversion | LibreOffice headless (`soffice --headless --convert-to pptx`) |
| LLM | `anthropic` (official SDK) |
| Schema/validation | `pydantic` v2 |
| PDF rendering | `jinja2` + `weasyprint` |
| Config | `pydantic-settings` + `.env` |
| Testing | `pytest`, `pytest-cov`, `pypdf` (page-count assertions) |
| Packaging | `pyproject.toml`, `uv` if available else `venv` + `pip` |

If WeasyPrint's system deps (`pango`, `cairo`, `gdk-pixbuf`) are unavailable, implement the renderer
behind a `Renderer` Protocol and add a ReportLab-Platypus implementation as `--engine reportlab`.
Detect and report missing deps with an actionable install message, not a stack trace.

## 5. Repository layout

```
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── README.md
├── src/deckpager/
│   ├── cli.py                 # typer app
│   ├── config.py              # pydantic-settings
│   ├── doctor.py              # environment checks behind `deckpager check`
│   ├── models.py              # pydantic schema for OnePager
│   ├── ingest/{base,pdf,pptx,legacy_ppt}.py
│   ├── extract/{client,prompts,pipeline}.py
│   ├── render/{html,pdf}.py + templates/onepager.html.j2 + assets/
│   └── cache.py               # content-hash keyed json cache
└── tests/{fixtures,test_ingest,test_extract,test_render,test_cli}.py
```

## 6. Data model (`models.py`)

pydantic v2. Every non-trivial field uses this wrapper:

```python
class Field[T](BaseModel):
    value: T | None
    confidence: float = 0.0          # 0.0-1.0
    source_slides: list[int] = []
    note: str | None = None          # e.g. "inferred from footer"
```

`OnePager` fields:

**Header** — `company_name: Field[str]`, `tagline: Field[str]` (<= 90 chars), `website`,
`hq_location`, `founded_year: Field[int]`, `sector`, `sub_sector`,
`stage: Field[Literal["Pre-Seed","Seed","Series A","Series B","Series C+","Growth","Unknown"]]`.

**The ask** — `raise_amount_usd: Field[int]`, `pre_money_valuation_usd: Field[int]`,
`instrument: Field[str]` (SAFE / priced equity / convertible note / revenue share),
`amount_committed_usd: Field[int]`, `min_check_usd: Field[int]`, `close_date: Field[str]`.

**Business** — `problem` (<= 320), `solution` (<= 320), `business_model` (<= 220), `go_to_market`
(<= 220), `use_of_funds: Field[list[str]]` (<= 4 bullets, <= 70 chars each).

**Traction** — `traction_metrics: Field[list[Metric]]`:

```python
class Metric(BaseModel):
    label: str        # "ARR", "MoM growth", "Paying customers", "LOIs"
    value: str        # as displayed in the deck, e.g. "$1.2M", "18%"
    period: str | None
    source_slide: int | None
```

Cap at 6; prioritise revenue > growth rate > customers > retention > pipeline > partnerships.

**Market** — `tam_usd`, `sam_usd`, `som_usd`, `market_note`.

**Team** — `team: Field[list[TeamMember]]` with `name`, `role`, `background` (<= 90 chars). Cap 4,
founders first.

**Competition** — `competitors: Field[list[str]]` (<= 5), `differentiation: Field[str]` (<= 200).

**Analyst layer** (generated, labelled as generated in the PDF) — `key_strengths` exactly 3 (<= 90
chars each), `key_risks` exactly 3, `missing_information` up to 5.

**Provenance** — `source_filename`, `source_page_count`, `extracted_at`, `model`, `input_tokens`,
`output_tokens`, `estimated_cost_usd`.

## 7. Ingestion contract

`RawDeck` = `{ filename, page_count, slides: list[Slide] }`;
`Slide` = `{ index (1-based), text, speaker_notes, image_b64: str | None, has_chart: bool }`.

- PDF: text via `pdfplumber`; page images via PyMuPDF at 120 DPI, long edge <= 1568px, JPEG q80, b64.
- PPTX: walk shapes for text frames, tables (flatten to `Header: a | b | c`), speaker notes. Page
  images via LibreOffice to PDF, then reuse the PDF path.
- `.ppt`: convert with LibreOffice first; if `soffice` is absent, fail with the install command for
  macOS/Linux/Windows.
- A page with < 20 characters of text is image-dominant — its image **must** be sent to the model.
- Cap 40 slides. Beyond that: text for all slides, images for slides 1-25 plus any image-dominant
  slide; log the truncation.

## 8. Extraction

Single Claude call using **tool-use / structured output** — never parse free-form JSON out of prose.

- Model overridable via `--model`; read the current model list from the SDK/docs rather than
  hardcoding an assumption; expose it in config.
- Send one user message containing, per slide, `--- SLIDE {n} ---` then text, notes, and the image
  block for image-dominant slides.
- Force the response through a tool whose `input_schema` comes from `OnePager.model_json_schema()`.
- Retries: exponential backoff on 429/5xx (max 4 attempts). On `ValidationError`, retry **once** with
  the validation error appended as a correction turn, then fail loudly.

**System prompt (verbatim — tune only if tests demand it):**

> You are an investment analyst at TEN Capital extracting structured data from a startup pitch deck
> for an internal one-page summary.
>
> Rules:
> 1. Extract only what the deck states or directly implies. If a field is not supported by the deck,
>    set `value` to null and `confidence` to 0. Never guess a number, a valuation, a customer count,
>    or a name.
> 2. For every populated field, record the 1-based slide numbers it came from in `source_slides`.
> 3. `confidence` reflects how explicitly the deck supports the value: 0.9-1.0 stated verbatim;
>    0.6-0.89 stated but ambiguous or split across slides; 0.3-0.59 inferred from context; below
>    0.3 — prefer null.
> 4. Normalize currency to integer USD (`$1.2M` becomes 1200000). If the deck uses another currency,
>    keep the value and note the currency in `note`; do not convert.
> 5. Preserve the founders' own framing in `problem` and `solution` — compress, do not editorialize.
>    Respect the stated character limits.
> 6. `key_strengths`, `key_risks`, and `missing_information` are your analysis, not the deck's
>    claims. Risks must be specific to this company — reject generic risks like "execution risk" or
>    "competitive market". `missing_information` lists diligence items a partner would need before an
>    investment committee discussion.
> 7. If the document is not a pitch deck, set `company_name.value` to null and put "Document does not
>    appear to be a pitch deck" as the only entry in `missing_information`.

## 9. One-pager layout spec

US Letter, portrait, 0.5in margins, one page. Top to bottom:

1. **Header bar** (~0.9in) — TEN Capital logo left; company name (22pt bold) + tagline (10pt)
   centre-left; sector / stage / HQ chips right.
2. **The Ask strip** (~0.5in, tinted) — Raise / Pre-money / Instrument / Committed / Close date as 5
   evenly spaced label-value cells.
3. **Two-column body** (~7.2in), left 58% / right 42%:
   - Left: Problem, Solution, Business Model, Go-to-Market, Use of Funds.
   - Right: Traction (metric tiles, 2-up), Market (TAM/SAM/SOM), Team, Competition.
4. **Analyst footer block** (~1.4in) — Strengths / Risks / Missing Information in three columns,
   visually distinct (lighter background, italic label "TEN Capital analysis — AI-generated").
5. **Footer rule** — `Generated by TEN Capital · {source_filename} · {extracted_at:%d %b %Y} ·
   Internal use only` plus a small "n fields low-confidence" counter.

Typography: one serif for headings, one sans for body; 22/11/9.5/8pt. Palette: near-black text, one
TEN Capital accent, one neutral gray. All colours and the logo path live in CSS custom properties at
the top of `onepager.css` so re-skinning is a one-file change.

Rendering rules:
- Null values render as an em dash in a muted colour.
- `confidence < 0.6` gets a subtle superscript dagger; a footnote explains it.
- Overflow: measure and progressively truncate the lowest-priority fields (go-to-market, then
  business model, then competition, then market note) until the render is one page. Log every
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

Exit codes: `0` ok, `1` bad input/unsupported file, `2` extraction failed after retries, `3` render
failed, `4` config/env problem. Print a cost line on success:
`Extracted in 8.2s · 24,310 in / 1,842 out · ~$0.11`.

## 11. Error handling

Every user-caused failure produces a one-sentence, actionable message — no tracebacks unless `-v`.
Cover explicitly: encrypted PDF, scanned/no-text PDF, corrupt file, unsupported extension, missing
API key, rate limit exhausted, schema-invalid model output twice, a 200-slide deck, and a
non-English deck (proceed, note the language in `missing_information`).

## 12. Tests

- `test_ingest`: each fixture yields the expected slide count; image-dominant detection fires on the
  image-heavy fixture; the `.ppt` path skips gracefully when `soffice` is absent.
- `test_extract`: with a mocked Anthropic client, a well-formed tool response validates; a malformed
  one triggers exactly one correction retry; currency normalization (`$1.2M`, `1.2m`,
  `USD 1,200,000`, 900k in euros) is table-tested.
- `test_render`: **`pypdf` reports exactly 1 page for all fixtures, including a deliberately
  overstuffed OnePager instance** — the headline test; nulls render as an em dash; the
  low-confidence marker appears.
- `test_cli`: exit codes for each failure mode; `--json` writes valid JSON matching the schema.
- Coverage >= 80% on `src/deckpager/`.

## 13. Build order — stop for review after each phase

- **Phase 0** — Scaffold: `pyproject.toml`, package skeleton, `deckpager check`, `make test`.
- **Phase 1** — Ingestion for PDF and PPTX + fixtures. No LLM. `deckpager render --dry-run` prints
  the parsed `RawDeck` summary.
- **Phase 2** — `models.py` + JSON schema generation + cache layer. `deckpager schema` works.
- **Phase 3** — Extraction against the real API; run once on a real deck and show the raw JSON before
  building the renderer.
- **Phase 4** — HTML/CSS template with hardcoded sample data; iterate on the PDF visually until the
  layout is right. **Before wiring real data in.**
- **Phase 5** — Wire extraction to render, implement overflow truncation, full test suite green.
- **Phase 6** — `batch`, README with install + usage + cost-per-deck, `.env.example`.

At each phase boundary: run the tests, show the diff, summarize what changed in <= 5 lines, and wait.

## 14. Working style

- Small commits, conventional-commit messages, one concern per commit.
- Never edit more than 3 files without showing the plan first.
- Prefer stdlib. Ask before adding any dependency not listed in section 4.
- If a test fails, fix the code — never delete or weaken the assertion, especially the one-page one.
- When spec and reality conflict, say so and propose the tradeoff. Do not silently work around it.

---

## Environment notes (this machine, verified 2026-08-20)

- Python **3.14.6** only (no 3.11/3.12); no `uv` — use `python -m venv .venv` + pip.
- Every dependency in section 4 installs cleanly on 3.14 (pymupdf 1.28.2, weasyprint 69,
  anthropic 0.125).
- **LibreOffice is not installed** — `.ppt` and `.pptx` page images are unavailable until it is.
- **WeasyPrint imports but cannot load GTK** (`libgobject-2.0-0`) — on this machine the ReportLab
  engine from section 4's fallback clause is the only working renderer. Keep the `Renderer` Protocol
  split.
- Venv lives at `.venv/`; run tools as `./.venv/Scripts/python.exe -m <tool>`.
- Console output must stay ASCII: this terminal is cp1252 and mangles em dashes. Em dashes belong in
  the PDF, not in `rich` console strings. Escape `[...]` in rich output (markup).
