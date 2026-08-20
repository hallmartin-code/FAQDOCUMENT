# Decisions

Design decisions taken where the build spec was silent, plus suggestions deliberately
not acted on. Newest phase last.

Phases named "deckdd" and "pitchlens" below predate this repository: deckpager is a fork
of pitchlens, and those entries are kept as the record of why the inherited code looks the
way it does. They are not renamed, because renaming them would falsify the history.

---

## Phase 1 — Skeleton

### D1. Dedicated git repository scoped to `deckdd/`

The enclosing git repository resolves to `C:/Users/user` — the whole home directory,
with every file untracked. Committing build gates there would stage AppData, browser
profiles, and unrelated personal documents. Initialized a repository rooted at `deckdd/`
instead, so "commit at each gate" stays scoped to the project.

### D2. Model default is `claude-opus-5`, read from config only

`config.DEFAULT_MODEL = "claude-opus-5"`, overridable via `DECKDD_MODEL` or `--model`.
The ID was verified against the current Anthropic model list rather than recalled — it is
the current Opus-tier model, supports native PDF `document` content blocks, forced
`tool_choice`, and a 1M context window. No call site hard-codes a model string.

### D3. `ANTHROPIC_API_KEY` is read un-prefixed

Every other setting uses the `DECKDD_` prefix. The API key is aliased to the bare
`ANTHROPIC_API_KEY` so the app picks up the same variable the Anthropic SDK and other
tooling already use, rather than forcing a duplicate `DECKDD_ANTHROPIC_API_KEY`.

### D4. Error classes carry their own exit code

`DeckddError.exit_code` lets the CLI map any failure to a distinct shell exit status
(config 2, ingest 3, analysis 4, schema 5, render 6) without the CLI knowing the taxonomy.
Keeps §1's "fail loudly" requirement in one place.

### D5. `pypdf` added as a dev-only dependency

Not in the spec's dependency list. Needed by `tests/test_onepager_fit.py` to count pages
in the rendered PDF and by `tests/test_parity.py` to extract PDF text — ReportLab writes
PDFs but cannot read them back. Dev extra only; never imported by `src/`.

### D6. `pipeline.py` holds thin stage functions the CLI calls

The CLI imports pipeline functions lazily inside each command body. This keeps
`deckdd version` (and `--help`) from importing PyMuPDF, ReportLab, and the Anthropic SDK,
which dominate startup time.

---

## Phase 2 — Ingestion

### D7. `Deck` carries a `warnings` list

The spec's `Deck` model has no warnings field, but `RunMeta.ingest_warnings` has to be
populated from somewhere. Adding `Deck.warnings: list[str]` keeps ingestion a pure
`path -> Deck` function instead of returning a tuple, and the pipeline copies it verbatim
into `meta.ingest_warnings`.

### D8. `import pymupdf`, not `import fitz`

PyMuPDF 1.28.2 emits a deprecation warning for the legacy `fitz` alias: *"The `fitz` API is
deprecated and will be removed in future. Use `import pymupdf` instead."* Trusted the
library over the habit and used `pymupdf` throughout.

### D9. Rasterized pages are clamped to a 1568 px long edge

Spec says "~150 DPI". A 13.33in-wide slide at 150 DPI is 2000 px, which the API downsamples
anyway — the extra pixels only burn the 5 MB image budget. `render_page_png` renders at
150 DPI but clamps zoom so the long edge never exceeds 1568 px.

### D10. Image caps drop images; they never drop slides

The spec's caps (60 slides, ~5 MB images) are request budgets, and its stated remedy is
"drop images from the least text-dense slides first". Slide *text* is always sent in full —
dropping a slide entirely would break the model's slide-number citations and silently
shrink the evidence base. Only images are shed, and every drop is listed by slide index in
`deck.warnings`.

> ⚠️ **Flagged for review — implemented as specified, but the ranking may be backwards.**
> "Least text-dense first" means an image-only chart slide with almost no extractable text
> loses its image *before* a text-heavy slide whose image adds little. Those sparse slides
> are usually the ones where the image carries all the information. If you agree, the fix is
> one line in `router.apply_caps` (invert the sort key). Left as written pending your call.

### D11. PPTX title falls back to the first text line

`slide.shapes.title` is `None` on blank layouts, which real decks use constantly. When
there is no title placeholder, the PPTX path reuses the PDF path's `first_line_title`, so
both formats produce comparable `Slide.title` values.

### D12. Extension/content mismatch is a hard error

A `.pptx` file whose bytes are a PDF (or vice versa) raises `UnsupportedFormatError` rather
than being silently routed on magic bytes. A mismatch usually means a bad export or a
renamed file, and guessing risks analyzing the wrong document. Case-only differences
(`deck.PDF`) are fine.

### Suggestions not acted on

- **CI config / GitHub Actions** — explicitly out of scope per §9. The gate commands
  (`ruff check && mypy src && pytest --cov`) are trivially wirable into CI later.
- **Packaging a Windows executable** — not requested; `pip install -e` plus the `deckdd`
  console script covers the stated usage.

---

## Phase 4 — Rename to `pitchlens`

The `pitchlens` build spec describes this same product with a wider scope: multiple LLM
providers, a scorecard-driven one-pager with a hard single-page guarantee, a Streamlit UI,
and batch mode. Rather than start a 15th sibling project under `Claude Cowork folder`,
`deckdd` was renamed and becomes the base. Entries D1–D12 above are the historical record
and still describe this codebase; they keep their original `deckdd` names.

### D13. Repository relocated to `Claude Cowork folder/pitchlens/`

It was nested under `FounderTeamAnalysis/`, an unrelated parent that held nothing else.
The move is a directory move, so all history from D1's project-rooted repository survives
(`git log` still reaches `5584c56 Phase 1: skeleton`). `FounderTeamAnalysis/` is left in
place, now empty.

The move was interrupted partway by a Windows lock and completed by restoring the tracked
tree from the object store; `samples/AccuBreath_Deck.pdf` is gitignored and was copied
across by hand. Working-tree line endings are now CRLF where they were LF, a side effect of
the re-checkout. Content is byte-identical modulo that.

### D14. Identifier rename is mechanical and total

`deckdd` → `pitchlens`, `DECKDD_` → `PITCHLENS_`, `DeckddError` → `PitchlensError`,
console script `deckdd` → `pitchlens`. No compatibility shim or alias is kept: nothing
depends on this package yet, and a deprecated alias would outlive its usefulness.

### D15b. The persona that shipped was not the persona in the spec

Externalizing `SYSTEM_PROMPT` was supposed to be a move, not a rewrite. It is a rewrite:
the prompt in `prompts.py` and Appendix A of the pitchlens spec are different documents,
and the differences are the ones that matter most.

The old prompt had **no scoring anchors** — no definition of what a 5 or an 8 means — which
is the single largest source of score drift between runs on the same deck. It had **no
null-handling rule**, so a deck with no team slide gets a plausible invented one rather
than a `data_gaps` entry. And it instructed the opposite of the spec on the headline
number: *"Overall Investability is your judgment, not an average"*, where §6 computes it
from `weights.toml` and treats a large model/computed gap as a `score_divergence` warning.

`prompts/analyst_system.md` is Appendix A verbatim. Any deck analyzed before this commit
was scored against a different rubric and is not comparable to one analyzed after it.

### D15. The three pre-existing mypy errors are fixed, not silenced wholesale

`mypy src` reported six errors on arrival. All were type-stub friction rather than defects:
PyMuPDF's `Document` is iterable at runtime but not in its stubs (fixed by indexing
`document[i]`, which is also more explicit); `BaseShape.text_frame` needs the same
`type: ignore[attr-defined]` the neighbouring branches already carry; `anthropic` 0.122
now types `output_config`, so its `type: ignore` had gone stale. The gate is clean from
here, so a new error means new code.

---

## Phase 5 — M0: config, prompts, and the provider seam

### D16. `config/` and `prompts/` are data at the repo root, resolved by a search path

They sit at the repository root rather than inside the package because an analyst edits
them — a weight, a rubric line — and should not have to find a `site-packages` directory
to do it. `paths.py` resolves each by: an explicit `PITCHLENS_CONFIG_DIR` /
`PITCHLENS_PROMPTS_DIR` override, then the repo root (the path that exists under the
editable install this project actually uses), then a copy bundled in the wheel. When none
resolve, the error names the environment variable to set instead of reporting a missing
file.

### D17. The output contract stays generated in Python; the persona does not

`prompts/analyst_system.md` is product data. `OUTPUT_CONTRACT` is not: it enumerates the
exact eleven scorecard rows and the `FACT`/`INFERENCE`/`SPECULATION` field rules that
`schema.py` validates, built from the same constants the validators import. Moving it to
a file would let the prompt and the validators drift, and a drift there costs a wasted
model call per run. It contains no analytical judgment — only mechanical field rules —
so it does not fall under the spec's "no analytical opinion in Python" rule.

The prompt files use `<!-- pitchlens:section NAME -->` delimiters so each file can carry
an editing note for the analyst that never reaches the model: everything before the first
marker is discarded on load.

### D18. `ready` means configured; whether an adapter exists is a separate fact

`pitchlens providers` reports two independent things. `ready` answers the spec's
reachability question — is the SDK installed, is the key set, is the Ollama daemon up —
and is checked without a billable call: a present key is reported as present, never
verified, because verifying costs a request. Whether the `LLMProvider` adapter has been
written yet rides in `notes` ("adapter arrives in milestone M3"). Collapsing the two would
mean reporting a correctly-configured Anthropic key as "not ready", which is not true and
is not useful.

### D19. `--provider` is refused where it is not honoured, never ignored

The adapters land in M2 (`fake`) and M3 (the rest), but `--provider` is accepted by the
CLI now. Rather than let `--provider fake` parse cleanly and then silently run Anthropic,
`analyze_deck` raises a `ConfigError` naming the milestone for any selection it cannot
honour. Likewise `get_provider` refuses anything outside `_WIRED` instead of failing later
on an `ImportError` for a module that does not exist yet.

### D20. Weights cover ten categories, not eleven

`Overall Investability` is the weighted output of the other ten, so giving it a weight of
its own would be circular. `load_weights` enforces exactly the ten, all non-negative,
summing to 1.0 — and reports the actual total when they do not, because "they sum to
1.04" is a fixable message and "invalid weights" is not.

### D21. stdout is forced to UTF-8 on Windows

The console defaults to a legacy code page, which rendered the em-dashes in the prompt
files and provider messages as `?`. `cli.py` reconfigures stdout and stderr to UTF-8
before anything writes. Company names from real decks will hit this constantly.

---

## Phase 6 — M2 render, live API, and the web app

### D22. The one-page guarantee was measuring the wrong thing

The first implementation rendered a PDF and counted its pages. That is vacuous for this
layout: the one-pager is drawn at absolute coordinates, so ReportLab paints text past the
bottom edge of the sheet without ever starting a second page. `page_count` returned 1 on a
document that visibly ran off the page, the fitting ladder never fired, and the guarantee
held only because nothing ever tested it against content long enough to overflow.

A live API run exposed it immediately — real risk rationales are roughly three times longer
than the synthetic fixture's. `fit.py` now measures geometry (points of overflow) and only
the winning layout is written to disk.

Two ladder rungs were also no-ops on real output. `_truncate_words` returned its input
unchanged when the summary had no sentence break, and `_first_clause` did the same when a
rationale had no clause separator — so the two cheapest reductions consumed attempts
without shrinking anything. Both now fall back to a word-boundary cut. On the live
assessment the risk-reason rung alone takes 629pt of overflow to zero.

The lesson generalizes: a guarantee tested only against fixtures you wrote is a guarantee
about your fixtures.

### D23. Length limits are generated into the prompt from the schema

The first live run failed after two Opus calls and five minutes because the model wrote a
900-character `advance_rationale` against an 800-character ceiling it had never been told
about. `OUTPUT_CONTRACT` now reads `max_length` off the Pydantic models and states every
ceiling. A limit the model cannot see is a limit it cannot respect, and each violation
costs a full round trip.

### D24. FastAPI, not Streamlit, and background jobs rather than a long request

The build spec named Streamlit. Railway is the deployment target, and `PitchdeckAnalyzer`
already runs there on FastAPI + uvicorn + NIXPACKS with a `/healthz` check and HTTP Basic —
a pattern proven in this environment. Streamlit would have meant websockets through
Railway's proxy, no real auth story, and a session model that fights background work.

A run takes two to five minutes, so `/api/analyze` returns a job id and the browser polls.
Holding the request open would lose to a proxy timeout after the API call had already been
paid for.

### D25. The deployment is open by request, and the door is left ajar deliberately

Asked how the app should be reached, the operator chose their own key with no
authentication, after being told that a public Railway URL with a loaded key means anyone
who finds it spends the credits. That is their call and it is implemented as asked.

`APP_PASSWORD` support is nonetheless present and unset: closing the app later is one
Railway variable, not a code change. `MAX_CONCURRENT_JOBS` (default 2) and `MAX_UPLOAD_MB`
(default 40) bound the blast radius without contradicting the decision. Unlike
`PitchdeckAnalyzer`, a missing `ANTHROPIC_API_KEY` fails at boot rather than on first
upload, so a misconfigured deploy is obvious immediately.

### D26. `analyze_deck` reports stages through a callback

The web app needs the four stages §8 specifies (Extracting → Analyzing → Grounding →
Rendering), and the pipeline previously reported progress only by printing to a rich
Console. Adding an `on_stage` callback keeps the pipeline free of any opinion about who is
watching, and stops the progress bar sitting on one stage for four minutes.

### D27. `.docx` output is dropped

`deckdd` rendered PDF and DOCX. The pitchlens spec's outputs are a one-page PDF, an optional
multi-page memo, and the JSON — no DOCX anywhere. `--format pdf|docx|both` is replaced by
`--paper letter|a4`, and `run`/`analyze` collapse into the single `analyze` command §8
describes.

---

## deckpager Phase 0 — Fork and scaffold

### DP1. Forked pitchlens rather than starting clean-room

pitchlens already implements deck ingestion, forced-tool-use extraction, and — the part
that is genuinely hard to get right — a one-page guarantee that measures geometric overflow
instead of counting pages. `render/fit.py` records why that distinction matters: ReportLab
paints past the bottom edge without ever starting a second page, so `len(reader.pages) == 1`
is not evidence that anything fits. A clean-room build following the spec's test list would
have written that vacuous assertion.

The first commit here is the unmodified fork, so every later diff separates deckpager's own
work from what it inherited.

### DP2. Dropped the web app, the mailer, and the Railway files

`app.py`, `web/`, `mailer.py`, `Procfile`, `railway.json`, and `requirements.txt` are the
deployed-service surface of pitchlens. deckpager's spec §10 is a CLI. Carrying a FastAPI
app and an SMTP client that nothing calls would mean maintaining — and type-checking — a
product that does not exist. `python-docx`, `fastapi`, and `httpx` left with them; none had
a remaining import.

### DP3. Exit codes remapped to the five in spec §10

pitchlens used seven codes (config 2, ingest 3, analysis 4, schema 5, render 6, overflow 7).
The spec names five: 1 bad input, 2 extraction failed, 3 render failed, 4 config. They are
now module constants in `errors.py` rather than literals on each class, so the mapping is
readable in one place. `SchemaValidationError` shares the extraction code deliberately: by
the time it escapes, the correction retry is spent, and from the shell's point of view what
failed is the extraction.

### DP4. mypy runs `--strict`, with the vendor exemption scoped to vendors

Spec §3 requires it; the fork was on `strict = false`. Ten errors surfaced, all real and all
small. Four were calls into PyMuPDF and python-pptx, which ship unannotated callables — the
fix is `untyped_calls_exclude = ["pymupdf", "pptx"]`, which exempts calls *into* those
packages while leaving our own code fully checked. A blanket `disallow_untyped_calls = false`
would have exempted our code too.

### DP5. `deckpager check` grades its results; only failures set the exit code

Three states, not two. A missing GTK stack means the optional WeasyPrint engine is
unavailable while the default ReportLab engine is fine; a missing LibreOffice means `.ppt`
is unavailable while `.pdf` and `.pptx` are fine. Reporting either as a failure would train
the user to ignore the command. Only `Status.FAIL` — no API key, no data directories, an
interpreter below 3.11 — exits 4.

The check reports that the API key is *set* and which source it came from. It never prints
the key, and a test asserts the key does not appear in the command's output.

### DP6. No Makefile

Spec §13 asks for a CI-less `make test`. `make` is not installed on the target Windows
machine and is not part of Git Bash, so a Makefile would be a file that cannot be run.
Tests run with `pytest` (or `.venv/Scripts/python -m pytest`). Revisit if the project
acquires a Unix or WSL development path.

### DP7. The inherited `llm/` provider seam is dead code, left in place pending a decision

`src/deckpager/llm/` (base, fake, registry — 364 lines) is a multi-provider abstraction that
was never wired: `_WIRED` is an empty frozenset, so `get_provider` refuses every provider
including `fake`, and its final branch imports `deckpager.llm.anthropic`, a module that does
not exist. The live path is `analysis/client.py` (`Analyzer` / `AnthropicAnalyzer` /
`FakeAnalyzer`), which is what `pipeline.py` actually uses.

deckpager's spec names one LLM (§4) and a CLI without a `providers` command (§10), so the
seam has no future here. It was left untouched in Phase 0 rather than deleted, because
removing a subsystem plus its tests and the `provider` config field is a larger change than
the phase was scoped for.

---

## deckpager Phase 1 - Ingestion

### DP8. pdfplumber reads the text, PyMuPDF draws the pictures

Spec 4 names both libraries, and they are genuinely better at different things.
pdfplumber keeps reading order and recovers a table as rows; PyMuPDF rasterizes and
reports vector path counts. The file is therefore parsed twice, which costs a few
hundred milliseconds against a model call measured in seconds. Worth it: extract_text
alone flattens a traction table into reading order and loses which number belonged to
which column, and that is exactly the table a partner cares about.

Flattened table rows are appended under a `[table]` marker even though they duplicate
words already in the running text. The duplication buys structure the raw text has
already lost.

### DP9. Chart detection is exact for PPTX and a calibrated heuristic for PDF

A PPTX chart is a first-class object and python-pptx says so. A PDF has no such notion,
so the PDF path counts vector drawing operations. The threshold was measured rather than
guessed: on a real deck in the samples folder, prose pages ran 2-6 paths and pages
carrying a diagram or bar chart ran 11-26; the synthetic chart fixture runs 24-60.
`CHART_DRAWING_PATHS = 15` sits in the gap with margin either side.

It is a hint, not a fact. It colours the prompt and the dry-run summary; nothing depends
on it being right, and a chart pasted in as a picture is invisible to both paths.

### DP10. Image rationing exempts image-dominant slides, in both directions

Spec 7 keeps images for slides 1-25 plus any image-dominant slide. Implementing the
positional rule was straightforward; the byte budget underneath it was not. The
inherited shed order was text-density ascending, which drops the *sparsest* slides
first - and an image-dominant slide is by definition the sparsest thing in the deck.
The rule as inherited would have thrown away precisely the images that carry meaning.

`_shed_order` now sorts image-dominant slides last, so they are the final images given
up rather than the first.

### DP11. A slide the model cannot read is now said out loud

There is one case where spec 7 cannot be satisfied: a PPTX ingested on a machine with no
LibreOffice. Its wordless slides reach the model as an empty string with no image behind
them. `_warn_about_blind_slides` names those slide numbers. Without it, a deck whose
middle third is full-bleed diagrams looks to the reader like a deck that said nothing.

A natively sent PDF is exempt: the whole file goes to the model, so those pages are seen.

### DP12. The CLI moved to the spec 10 surface

`analyze` became `render DECK_PATH`, and the old `render` (which took an analysis JSON)
became `redraw`. Spec 10 has no `analyze`, and leaving a `render` that refused decks
would have been the most confusing possible half-measure. `redraw` survives the rename
because it is the zero-cost layout loop, and Phase 4 iterates on layout.

`--dry-run` runs the same `ingest_deck` the paid path runs, needs no API key, and asserts
in tests that no analyzer is ever constructed.

### DP13. JPEG q80 is followed as specified, and it is not always the smaller file

Spec 7 asks for 120 DPI JPEG at quality 80. Measured on the text fixture, that page
encodes to 30 kB as JPEG against 23 kB as PNG - vector text is exactly the content PNG
wins on, and JPEG additionally puts ringing artifacts around small type. The spec was
followed. The alternative, encoding both and keeping the smaller, is a one-line change in
`render_page_image` if the image budget ever becomes the binding constraint.

### DP14. The .ppt success path is unverified on this machine

LibreOffice is not installed here, so `load_ppt` has been exercised only through its
failure path: the router recognizes the OLE2 header, refuses an .xls wearing the same
header, and the missing-soffice error names the install command for the running platform.
The conversion itself is untested. It should be run against a real .ppt before anyone
relies on it.
