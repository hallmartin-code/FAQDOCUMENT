# pitchlens

Turns a pitch deck into a one-page investment screening memo whose every factual claim has
been checked against the deck's own text.

The evidence discipline is the point. The model tags each assertion `FACT`, `INFERENCE`, or
`SPECULATION`; a `FACT` must carry a verbatim quote and a slide number, and `grounding.py`
verifies that quote against the locally extracted slide text. Anything that fails is
downgraded to `INFERENCE` and counted. The one-pager's footer states the tally, so a reader
can tell at a glance how much of the memo the deck actually supports.

Outputs, per run:

| File | What it is |
|---|---|
| `<name>_onepager.pdf` | Single-page screening memo. Never two pages — see below. |
| `<name>_analysis.json` | The validated analysis, including every claim and its slide refs. |

---

## 60-second quickstart

```bash
git clone <this repo> && cd pitchlens
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# Render a one-pager with no API key and no network at all:
pitchlens render tests/fixtures/sample_assessment.json -o ./demo

# Analyze a real deck (needs a key):
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
pitchlens analyze path/to/deck.pdf
```

`pitchlens render` is the fast loop: it re-renders a saved analysis with no model call, so
layout work costs nothing.

---

## CLI

```
pitchlens analyze DECK [-o STEM] [--paper letter|a4] [--context TEXT]
                       [--provider anthropic|fake] [--model NAME] [--no-images] [--json PATH]
pitchlens render ANALYSIS.json [-o STEM] [--paper letter|a4]
pitchlens providers
pitchlens version
```

`--context` is background for the analyst — an intro source, a stage, a prior conversation.
It is explicitly framed to the model as *not* part of the deck, so it can never be cited as
evidence or supply a figure the slides do not show.

A run takes **two to five minutes** on Opus 5 at `effort=high`. That is the model thinking,
not the app hanging.

---

## Web app

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Upload a deck, watch the four stages, download the PDF and JSON. Because a run takes
minutes, the upload returns a job id immediately and the browser polls — holding an HTTP
request open that long loses to every proxy in between.

The page states the accepted file types, the size cap, and the data-handling disclosure
**from live server configuration**, not from text written into the template. It therefore
cannot advertise a file type the router rejects, or promise an email that is not
configured. Turning email on (`REPORT_EMAIL_TO`) rewrites the disclosure to name the
recipient; leaving it off states plainly that nothing is emailed anywhere.

### Deploying to Railway

1. Push this repository to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, and pick it. `railway.json` and
   `Procfile` are already here, so NIXPACKS builds from `requirements.txt` and starts
   uvicorn with a `/healthz` healthcheck.
3. Add one variable under **Variables**:

   ```
   ANTHROPIC_API_KEY = sk-ant-...
   ```

   The app **refuses to boot** without it, rather than accepting uploads and failing on
   each one after you have already waited.
4. **Generate Domain** under Settings → Networking.

Optional variables, all with working defaults:

| Variable | Default | What it does |
|---|---|---|
| `APP_PASSWORD` | *(unset)* | Puts the whole app behind HTTP Basic. **See the warning below.** |
| `APP_USERNAME` | `ten` | Only used when `APP_PASSWORD` is set. |
| `MAX_CONCURRENT_JOBS` | `2` | Analyses running at once. Each is a paid API call. |
| `JOB_TTL_MINUTES` | `180` | How long finished reports stay downloadable. |
| `MAX_UPLOAD_MB` | `25` | Upload ceiling, shown on the page. |
| `REPORT_EMAIL_TO` | *(unset)* | Email a copy of every one-pager here. Unset = no email. |
| `SMTP_HOST` / `SMTP_FROM` | *(unset)* | Required when `REPORT_EMAIL_TO` is set. |
| `SMTP_USER` / `SMTP_PASSWORD` | *(unset)* | SMTP auth. Gmail needs an App Password. |
| `PITCHLENS_MODEL` | `claude-opus-5` | Overrides `config/default.toml`. |
| `PITCHLENS_EFFORT` | `high` | `low` is much faster and cheaper; quality drops. |

> **This deployment is configured open.** With `APP_PASSWORD` unset, anyone who reaches the
> URL can upload a deck and spend your Anthropic credits, and Railway domains are
> guessable. Setting `APP_PASSWORD` closes it with no code change and no redeploy of the
> image — just add the variable. `MAX_CONCURRENT_JOBS` bounds the blast radius but does not
> remove it.

Uploads and generated reports live in the container's temp directory and are swept on the
TTL, so a Railway restart loses in-flight jobs. That is deliberate — nothing about a deck
should outlive the session that analyzed it.

---

## The one-page guarantee

A second page is a defect, not an overflow. `render/fit.py` measures how far the content
exceeds the page and applies reductions in a fixed order, cheapest to the reader first:

1. Truncate the executive summary at a sentence boundary (floor: 80 words)
2. Drop diligence questions 5 → 4 (floor: 3)
3. Shorten risk reasons to their first clause
4. Step the body font 9 → 8.5 → 8pt (floor: 8pt)
5. Tighten line-height 1.35 → 1.2

If it still overflows, the run raises `OnePagerOverflowError` naming what was tried. It
never silently emits page 2. Whatever was given up is recorded in `meta.method_notes`, so
you can tell a summary that ran short from one that was trimmed to fit.

It measures geometry, not page count: the layout is drawn at absolute coordinates, so
ReportLab will happily paint past the bottom edge without ever starting a second page.

---

## Configuration

| File | Purpose |
|---|---|
| `config/default.toml` | Provider, model, effort, ingestion limits. |
| `config/weights.toml` | The ten scorecard weights. Analyst-editable; must sum to 1.0. |
| `prompts/analyst_system.md` | The venture-partner persona. Edit it without touching Python. |
| `templates/onepager.md` | The document structure: zones, fields, formatting, vocabularies. |

Settings resolve highest-precedence first: CLI flag → `PITCHLENS_*` environment variable →
`.env` → `config/default.toml` → the field default.

---

## Troubleshooting

**`ANTHROPIC_API_KEY is not set`** — put it in `.env` beside the project, or export it. Run
`pitchlens providers` to see what the app thinks is configured.

**A run failed with a schema error after two attempts** — the model wrote past a field's
length ceiling twice. The limits are generated into the prompt from the schema, so this
should be rare; if it recurs on a particular deck, the analysis JSON is not written but the
error names the offending fields.

**`OnePagerOverflowError`** — the analysis is far longer than the field limits intend. Check
the risk rationales and executive summary in the JSON.

**`.ppt` (old PowerPoint) is rejected** — only `.pdf` and `.pptx` are supported today.
Re-export from PowerPoint as `.pptx`, or convert with LibreOffice:
`soffice --headless --convert-to pptx deck.ppt`.

**A scanned deck scores mostly nulls** — there is no OCR yet, so an image-only deck yields
no extractable text and therefore nothing to ground quotes against. The one-pager will say
so rather than inventing scores.

**Windows console shows `?` instead of dashes** — fixed in `cli.py`, which forces UTF-8. If
you see it in your own scripts, that is your terminal's code page.

---

## Development

```bash
pytest                  # 180 tests, no API key needed — everything runs offline
ruff check . && ruff format .
mypy src
```

Tests never hit the network. `--provider fake` replays a recorded fixture through the same
`LLMProvider` contract the real backends implement.

`DECISIONS.md` records design decisions taken where the build spec was silent, and the
suggestions deliberately not acted on.
