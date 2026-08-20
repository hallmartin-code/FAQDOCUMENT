# deckpager

Turns a pitch deck into a one-page TEN Capital investor one-pager, with every extracted
claim traceable to the slide it came from.

The discipline is the point. Every field carries the slide numbers it came from and how
confident the model was; anything the deck does not say comes back null and prints as an em
dash rather than a plausible guess. Fields below the confidence threshold are flagged on the
page and counted in the footer, so a partner can see at a glance how much of the summary the
deck actually supports.

Outputs, per run:

| File | What it is |
|---|---|
| `<Company>-onepager.pdf` | The one-pager. Exactly one page, always — see below. |
| `<Company>-onepager.json` | The validated extraction: every field, its confidence, its source slides. |

The document structure is specified in [`templates/onepager.md`](templates/onepager.md) and
enforced by `tests/test_template.py`.

---

## 60-second quickstart

```bash
git clone <this repo> && cd deckpager
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
deckpager check                                     # verify the environment first

deckpager render path/to/deck.pdf                   # deck -> PDF + JSON
```

`deckpager render deck.pdf --dry-run` parses the deck and prints what it read, with no API
key and no cost. Run it first on an unfamiliar deck.

---

## Commands

```
deckpager render DECK   Deck -> one-pager PDF and JSON. The main command.
    -o, --out PATH          Output PDF (default: <Company>-onepager.pdf beside the deck)
        --json PATH         Output JSON path
        --model TEXT        Override the model
        --paper letter|a4
        --min-confidence F  Flag threshold, default 0.60
        --no-cache          Ignore the extraction cache and pay for a fresh call
        --no-images         Skip slide rasterization
        --dry-run           Parse and report only. No model call, no cost.
        --no-email          Do not email the result, even when configured
    -v, --verbose           Full traceback on failure

deckpager extract DECK  Extraction only, no PDF.
deckpager redraw JSON   Re-render a PDF from a saved extraction. No model call.
deckpager schema        Print the JSON schema the model is held to.
deckpager check         Verify the environment: API key, config, engines, LibreOffice.
```

Exit codes: `0` ok · `1` bad input · `2` extraction failed · `3` render failed ·
`4` configuration problem.

---

## What a run costs

Measured against a real 30-page deck on `claude-opus-5`:

| | |
|---|---|
| Time | ~100 seconds |
| Tokens | 123,863 in / 7,239 out |
| Cost | **~$0.80**, including one correction retry |
| Second run on the same deck | 1.2 seconds, **$0.00** |

Input dominates, because the whole PDF goes to the model so it can see charts and layout.
A correction retry re-sends it, which roughly doubles the cost of a run — that is what the
$0.80 above includes.

**The cache makes iteration free.** Extractions are keyed by deck content hash plus every
input that steers the answer (model, prompt, tool schema, ingest budgets), so re-rendering
the same deck costs nothing. `--no-cache` forces a fresh call. Change the model or the
prompt and the key changes on its own.

A cheaper model is `--model claude-sonnet-4-6`, at roughly 60% of the cost. The default is
Opus because a hallucinated valuation reaching a partner costs more than the difference.

---

## The one-page guarantee

The output is one page. Always. Overflow is resolved by a reduction ladder that drops the
lowest-priority content first, then tightens typography, then truncates prose with an
ellipsis — never by spilling onto a second page.

Two details make it real rather than aspirational:

* **It measures geometry, not page count.** ReportLab paints past the bottom edge without
  ever starting a second page, so a page count of 1 proves nothing on its own.
* **It is tested against the worst input the schema can express** — every field at its
  maximum length, which overflows by 970 points — not just against typical decks.

Every reduction applied is reported to the caller and recorded in `provenance.truncations`.

---

## Web app

The same pipeline, in a browser: upload a deck, watch the stages, download the PDF and JSON.

```bash
pip install -e ".[web]"
uvicorn app:app --reload --port 8000
```

### Deploying to Railway

1. Push this repository to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, and pick it. `railway.json` and
   `Procfile` configure the build, the start command, and the `/healthz` healthcheck; the
   Nixpacks builder installs from `requirements.txt`.
3. Under **Variables**, set:

   | Variable | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | your key from console.anthropic.com |
   | `APP_PASSWORD` | a password — **see the warning below** |
   | `APP_USERNAME` | optional, defaults to `ten` |
   | `MAX_UPLOAD_MB` | optional, defaults to 25 |
   | `MAX_CONCURRENT_JOBS` | optional, defaults to 2 |
   | `JOB_TTL_MINUTES` | optional, defaults to 180 |

4. Generate a domain under **Settings → Networking**.

> **Set `APP_PASSWORD`.** Without it the deployment is open, and *anyone with the URL can
> upload decks and spend your API key*. `GET /healthz` reports `auth_enabled`, so you can
> check which state a deployment is in without logging in.

Two things to know about the deployed environment:

* **The extraction cache is per-container.** Railway's filesystem does not survive a
  redeploy, so the first run of a deck after a deploy pays full price. Mount a volume and
  set `DECKPAGER_CACHE_DIR` to a path on it if that matters.
* **`.ppt` will not work there.** Legacy PowerPoint needs LibreOffice, which the Nixpacks
  image does not include. `.pdf` and `.pptx` are unaffected.

---

## Emailing each result

Every generated one-pager can be emailed automatically, with the PDF and the extraction
JSON attached. The body carries the ask, the strengths, the risks, and the diligence
requests, plus anything the run flagged or truncated — enough to triage from the inbox
without opening the attachment.

It is **off until `RESEND_API_KEY` is set.** There is no separate on/off switch, because
two ways to disable a feature means someone eventually sets one and not the other and
then wonders why nothing arrives.

### Setting it up

1. Create an API key at [resend.com/api-keys](https://resend.com/api-keys) with
   **Sending access**.
2. **Verify the sending domain.** In Resend, add `tencapital.group` under Domains and
   publish the DNS records it gives you (an MX and two TXT records, for DKIM and SPF).
   Until this is done Resend rejects every send from `@tencapital.group` — the error is
   reported back to you, but nothing arrives.
3. Set the key locally in `.env`, and on Railway as a service variable:

   ```
   RESEND_API_KEY=re_...
   ```

4. `deckpager check` will then report the feature as on and name the recipient.

To try it before the domain is verified, set
`DECKPAGER_REPORT_EMAIL_FROM=onboarding@resend.dev`. Resend's shared sender only
delivers to the address that owns the Resend account, so it proves the wiring works and
nothing more.

### What happens when it fails

Nothing else does. The PDF is the product and the email is a notification about it, so a
failed send is reported — on the CLI, in the browser, and in the job payload — and the
run still succeeds with its artifacts on disk. `--no-email` suppresses sending for a
single run.

---

## Configuration

Every setting resolves highest-precedence first: CLI flag → environment → `.env` →
`config/default.toml` → the field's own default.

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. Read un-prefixed, so it is the same variable every other tool uses. |
| `DECKPAGER_MODEL` | `claude-opus-5` | Model ID |
| `DECKPAGER_EFFORT` | `high` | Reasoning effort |
| `DECKPAGER_MAX_SLIDES` | `40` | Slide cap per request |
| `DECKPAGER_MAX_IMAGE_SLIDES` | `25` | How many leading slides may carry an image |
| `DECKPAGER_MAX_IMAGE_BYTES` | `5000000` | Total image budget per request |
| `DECKPAGER_CACHE_DIR` | platform cache dir | Where extractions are cached |
| `RESEND_API_KEY` | — | Set it to email every result. Unset means nothing is emailed. |
| `DECKPAGER_REPORT_EMAIL_TO` | `Info@tencapital.group` | Recipient(s), comma-separated |
| `DECKPAGER_REPORT_EMAIL_FROM` | `deckpager@tencapital.group` | Sender; must be on a Resend-verified domain |

Secrets come from the environment or `.env` only. `.env` is gitignored; the API key is never
written to a tracked file, never logged, and never printed — `deckpager check` reports that a
key is *set* and which source it came from, and nothing more.

---

## Confidentiality

Decks are confidential, and so is everything derived from them.

* Uploaded decks are deleted from the server as soon as they have been read.
* Generated artifacts are deleted by the TTL sweep (default 3 hours).
* Extractions cached to disk live in the platform user cache directory, not in the project
  or the deck folder. `deckpager check` prints the path.
* Nothing is sent anywhere except the Anthropic API. There is no telemetry.

---

## Development

```bash
pytest                    # the full suite
mypy                      # --strict, clean
ruff check .
python tests/fixtures/make_fixtures.py     # regenerate the deck fixtures
python tests/fixtures/make_onepagers.py    # regenerate the one-pager fixtures
```

`DECISIONS.md` records every design decision taken where the build spec was silent, plus the
places where the spec and reality conflicted and what was done about it. `CLAUDE.md` is the
build spec itself.
