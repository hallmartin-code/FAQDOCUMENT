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

---

## deckpager Phase 2 - Schema and cache

### DP15. The Field wrapper is written with Generic, not the syntax the spec shows

Spec 6 writes the wrapper as a PEP 695 generic class. That syntax is a SyntaxError
before Python 3.12, and spec 3 sets the floor at 3.11. The TypeVar form produces an
identical model and an identical JSON schema, so the spec is met in substance on every
interpreter it claims to support. Switch the day the floor moves to 3.12.

### DP16. The model fills a draft; the pipeline stamps the provenance

OnePagerDraft is the tool contract. OnePager is a draft plus a Provenance block. The
split exists because spec 6 lists token counts, cost, and the source filename among the
fields, and those are things this code measures exactly. Putting them in the tool schema
would invite the model to invent numbers we already know - the precise failure that
spec 3 is written to prevent.

### DP17. Money is parsed, foreign currency is noted, and nothing is ever converted

The prompt asks for integer USD. When a model hands back a money string instead,
MoneyField parses it rather than burning a correction retry on a value that is perfectly
legible. A string that does not parse to a single amount becomes null at zero confidence
- never a midpoint, never a guess. A range is not a number.

A non-USD amount keeps its stated value and gains a note naming the currency, per spec 8.
Converting would require an exchange rate for the day the deck was written, which is not
in the deck; the converted figure would appear nowhere in the source and would be exactly
the kind of fabrication the whole design is built to refuse.

### DP18. Exactly three strengths and three risks, enforced strictly

Spec 6 says exactly three of each, so the list constraint rejects two as firmly as four.
This is the one limit likely to cost a correction retry in practice - a model that finds
only two specific risks would rather say so. Loosening it to a maximum would be quieter
and would also let the analyst block render short and ragged, so the strict reading
stands. Revisit if Phase 3 shows retries being spent on it.

### DP19. The cache key covers everything that steers the answer

Deck bytes, model ID, the prompt, the tool schema, and the ingest budgets that decide
which slides the model sees. Change any one and the key changes. The deck is keyed by
content hash rather than path or mtime, so the same deck re-sent under a new filename
hits, and a file touched by a sync client does not miss.

Every fault is a miss: unreadable directory, truncated record, record from an older
format, record that is not an object. A cache that can break a run is a liability, and
the cost of a miss is one extraction.

What lands on disk is derived from a confidential deck, so it goes to the platform user
cache directory rather than the project or the deck folder. The check command prints the
path and the entry count so nobody has to read the source to find it.

### DP20. The schema command prints through print, not rich

It is machine output. Redirecting it to a file has to produce something parseable, and
rich would wrap long lines and colour the punctuation. An indent of 0 gives one line.

### DP21. The unwired provider seam is gone (resolves DP7)

src/deckpager/llm/ and the providers command have been deleted, with the test module
that covered them. Nothing imported the package outside itself and its own tests: the
wired-provider set was empty, so get_provider refused every backend including the fake
one, and its last branch imported a module that was never written. Coverage put
llm/fake.py at 0 percent.

The live path is analysis/client.py, and spec 4 names one LLM. The provider setting
survives as a config knob with a pipeline guard that refuses anything but anthropic,
which is honest about the state of things; removing the field as well would touch config
precedence tests for no gain today.

---

## deckpager Phase 3 - Extraction

### DP22. extract/ is a new package beside analysis/, not a rewrite of it

analysis/ is the inherited path and still speaks the old Assessment schema, which the
old renderer needs. extract/ speaks OnePager. They coexist for two phases; Phase 5 wires
the new renderer and deletes the old pair. Rewriting analysis/ in place would have meant
a half-migrated module that neither schema could rely on.

### DP23. A transitional extract command

Spec 13 asks for the raw JSON to be reviewed before a renderer exists to hide it, and
spec 10 has no extract command. So there is one now, documented in its own help text as
transitional, and Phase 5 folds it into render. The alternative - pointing render at the
new pipeline and having it announce that PDFs arrive in two phases - is a worse thing to
hand someone.

### DP24. Two retry mechanisms, deliberately not the same one

Transport failures (429, 5xx, dropped connections) are retried by the SDK with
exponential backoff and Retry-After handling, configured to spec 8 four attempts via
max_retries=3. A schema violation is not a transport failure: the API succeeded and the
model was wrong, so it gets exactly one correction turn carrying the pydantic errors as
a tool_result, and then the run fails loudly with both reports.

### DP25. The correction retry resends the whole deck, and that is most of what it costs

Measured on the AccuBreath deck: 123,863 input tokens for a run that should have been
about 62,000, because the retry resends the document block. The retry roughly doubles
the input cost of a run, and input is nearly all of the cost on a 30-page deck.

A cache_control breakpoint on the document block would make the retry read cached input
at a tenth of the price. It is not on, because it is not free: a cache write costs 1.25x,
so on the runs that do not retry it is a 25 percent input premium. One deck is not enough
evidence about how often the retry fires. Revisit once more decks have gone through.

The system prompt is not marked cacheable either, and that is a bug that was there and is
now fixed: at roughly 348 tokens it is a third of the minimum cacheable prefix, so the
breakpoint would silently never have cached anything.

### DP26. What the first real run actually broke on (DP18 was wrong)

DP18 predicted the exactly-three rule on strengths and risks would be the limit that
cost a correction retry. It was not. The first run against a real deck failed validation
on four 90-character overruns - one strength and all three risks - and passed the
exactly-three rule cleanly. The 90-character cap is the tight one, because a specific
risk about this company is hard to state in 90 characters.

The limits stay as specified: they exist so the analyst block fits the page, and the
correction turn is the mechanism for enforcing them. Worth watching whether the retry
fires on this for most decks, in which case the cost in DP25 argues for either a longer
cap or an explicit instruction in the prompt.

### DP27. An unknown model reports no cost rather than a wrong one

PRICING is a local table, and a local price table cannot help going stale. A model that
is not in it gets estimated_cost_usd of None and a cost line that says so, instead of a
confident number computed from a rate that may have changed.

---

## deckpager Phase 4 - The one-pager layout

### DP28. The old renderer moved aside rather than being edited

render/onepager.py and render/theme.py became legacy_onepager.py and legacy_theme.py,
and the new pair took the good names. Both speak different schemas, both work, and the
legacy pair dies with the old pipeline in Phase 5. Editing the old renderer in place
would have produced a module that neither schema could rely on.

### DP29. Measure and draw are the same code path

Every drawing method takes an optional canvas and returns the height it consumed. With
None it measures; with a canvas it draws exactly what it measured. The alternative -
a measuring function beside a drawing function - is two implementations of one layout
that drift apart, and the drift lands as a two-page one-pager.

### DP30. A rung that does not reduce the overflow is reverted

The page is two independent columns and most of spec 9 truncation ladder only shortens
one of them. The first render of a real deck showed the cost: go-to-market and the
business model were both truncated to relieve pressure in the *right* column, which they
cannot affect, and the page still ended up at 7pt type. Each rung is now measured, and
kept only if it actually helps. Same order, same priorities, no content thrown away for
nothing.

### DP31. And what the page does not need is given back

The rungs are coarse - one diligence request is about 20 points - so the first layout
that fits usually overshoots. After a fit, each applied reduction is offered back, newest
first, and kept only if the page still fits without it. On the AccuBreath deck that
returned the market note. Cost: a handful of measurements, no extra rendering.

### DP32. The ladder needed rungs spec 9 does not list

Spec 9 names four: go-to-market, business model, competition, market note. Measured on a
real deck, the analyst block came out at 187pt against the roughly 101pt that spec 9
budgets for it, and the right column wanted 482pt against 389pt available. None of the
four rungs can close that, because the analyst block and the traction tiles are not among
them.

So the ladder gained: diligence requests 5 -> 3, traction tiles 6 -> 4, team 4 -> 3. All
are content reductions of the same kind as the four, all are logged, and all come before
typography - which now floors at 7.5pt rather than 7pt, because the inherited renderer
had already established 8pt as the readable minimum across a meeting table.

### DP33. The last rung is the one spec 3 actually asks for

Every field at its schema maximum overflows the page by 970pt. No combination of dropping
sections closes that: the schema permits roughly 2.7 times what one page holds. The
ladder therefore ends with a global text scale that ellipsizes every prose field to a
fraction of its length - which is exactly what spec 3 requires, truncation with an
ellipsis at the field level rather than a second page.

It is the last resort and it always works, so the one-page guarantee now holds for any
schema-valid document rather than for typical ones. The overstuffed fixture exists to
keep that true.

### DP34. The flag count only counts what is on the page

The first render said 2 fields were flagged and showed no marker anywhere: one was the
stage, printed as a chip with no room for a dagger, and the other was the website, which
spec 9 header does not include at all. A count the reader cannot reconcile is worse than
no count.

Chips now carry the dagger inline, the website rides beside the tagline (spec 6 puts it
in the header block, spec 9 just does not say where), and the footer counts only fields
in RENDERED_FIELDS.

### DP35. Traction tiles size their type to the value

Spec 9 asks for metric tiles, which implies numerals. Real extractions do not oblige:
the schema allows a 120-character value and AccuBreath returned `Working device:
feasibility and functionality proven`. Setting that at display size clipped it mid-word.
A tile now uses display size for something numeral-shaped and body size for a sentence.

---

## deckpager Phase 4b - The document structure template

### DP36. The template is a contract, and a test enforces it

templates/onepager.md states the document structure the app generates: the five bands,
the field map with every limit, the closed vocabularies, the palette, the type scale, and
the reduction ladder. It is company-agnostic - placeholders only, no deck, no company, no
run.

tests/test_template.py checks it against models.py, style.py, and onepager.py rather than
trusting it. Every extracted field must appear in the field map, the palette hexes and
type sizes must be the ones in style.py, the stage vocabulary must match the Literal, and
the ladder table must state the same caps the code starts from. Changing the accent colour
without updating the template fails the suite - verified by doing it.

A template that is only documentation goes stale in a month and then quietly misleads the
next person who builds against it.

### DP37. Three extracted fields have nowhere to go, and the template says so

founded_year, sub_sector, and min_check_usd are extracted, validated, and written to the
JSON, but spec 9 gives them no place on the page. Rather than leave that as an accident,
4.7 names them, RENDERED_FIELDS excludes them, and a test asserts the two lists partition
the schema exactly. Adding one to the layout is now a change that cannot be made in only
one place.

It is also a question for the operator: three fields are being paid for and not shown.

### DP38. The legacy template moved aside with its renderer

The inherited templates/onepager.md described the Assessment document, not this one. It
became legacy_onepager.md alongside legacy_onepager.py and legacy_theme.py, and its test
became test_legacy_template_spec.py. All four go together in Phase 5.

---

## deckpager Phase 5 - Wiring, and the web app

### DP39. One run function, two callers

pipeline.run is what the CLI and the web app both call. A deck analysed in a browser and
the same deck analysed at a terminal go through byte-identical code, and there is one
place to change when the sequence changes. The callers supply only somewhere to put the
files and a way to hear about progress.

### DP40. The legacy Assessment stack is gone

Deleted: analysis/ (client, grounding, prompts, schema), render/legacy_onepager.py,
render/legacy_theme.py, render/base.py, render/fit.py, the old pipeline, prompts/,
config/weights.toml, templates/legacy_onepager.md, and five test modules. Roughly 2,000
lines. Nothing in the current path referenced any of it.

config.load_weights and paths.prompts_dir went with them: the scorecard weights and the
externalized prompt files were both Assessment-era, and the extraction prompt now lives
beside the code that hashes it into the cache key (DP28).

### DP41. Grounding became a citation check

The old grounding.py verified quoted text against the deck. The new schema has no quotes
- it has slide citations - so the equivalent check is narrower and much cheaper: a cited
slide number past the end of the deck is recorded in provenance.citation_warnings and
surfaced by both callers. The schema can enforce that a citation is a positive integer;
only the pipeline knows how many slides there were.

### DP42. Railway deployment, with the app open by default and saying so

Procfile, railway.json, requirements.txt, runtime.txt. Jobs run in a worker thread behind
a concurrency cap, because a run takes minutes and holding the HTTP request open would
hit the platform proxy timeout and lose work that has already been paid for. The browser
polls.

APP_PASSWORD is optional rather than mandatory, which is a real risk: unset, anyone with
the URL can spend the deployment key. It stays optional so a deployment can be opened
deliberately, and /healthz reports auth_enabled so the state is checkable without logging
in. The README leads with the warning.

### DP43. A rejected oversize upload was leaving the partial deck on the server

Found by the test written for it. The cleanup ran shutil.rmtree while the file handle was
still open; Windows refuses to delete an open file and ignore_errors=True swallowed the
refusal, so the partial upload survived. On a server that is a confidential document
persisting after the request that created it was refused.

The write loop now breaks, closes, and then deletes. Ported from a codebase where the
same code had presumably been running on Linux, where it happens to work.

### DP44. Two things the deployed environment cannot do

The extraction cache is per-container, so the first run of a deck after a redeploy pays
full price; a mounted volume plus DECKPAGER_CACHE_DIR fixes it if that matters. And .ppt
will not convert, because the Nixpacks image has no LibreOffice. Both are in the README
rather than left to be discovered.

---

## deckpager - Emailing each result

### DP45. A second network call, requested and recorded

Spec 3 says no network calls except to the Anthropic API. Emailing results is a second
one, asked for deliberately. It is confined to mailer.py, it is off unless a key is set,
and it happens after both artifacts are already on disk. Recording it here rather than
quietly widening the constraint.

### DP46. urllib, not the resend SDK

Resend API is a single JSON POST. Spec 14 says prefer stdlib and ask before adding a
dependency, and urllib does this in twenty lines with no new package to install, pin, or
audit. The SDK is the better choice the day this needs batching, tags, or webhooks.

### DP47. The key is the switch

No separate enable flag. Sending is on when RESEND_API_KEY and a recipient are both
present, and off otherwise. Two ways to disable one feature means someone eventually sets
one and not the other and then spends an afternoon wondering why nothing arrives.

The recipient has a default (Info@tencapital.group) because it is a fixed address for
this team. The key never has a default.

### DP48. A failed send must never fail a run

The PDF is the product. The email is a notification about the product, and a notification
that could destroy the thing it notifies you about would be a bad trade. So: mailer.send
catches everything and returns an EmailOutcome rather than raising, the send happens after
both artifacts are written, and the CLI, the browser, and the job payload all report the
failure without changing the exit code.

The pipeline also wraps the call in its own try/except. mailer.send promises never to
raise; a promise is not a guarantee, and a bug in it would otherwise destroy a run that
had already succeeded and already been paid for. Verified against a live 403 from Resend:
warning printed, both files written, exit 0.

### DP49. The email body is escaped, because it is model output

A company name reaches the HTML from an extraction, which reaches it from a founder PDF.
Every interpolated value goes through html.escape. A test puts a script tag in the company
name and asserts it comes out inert.

### DP50. Sending will not work until the domain is verified

Resend rejects any send from an unverified domain, so tencapital.group needs its DNS
records published before anything arrives. The rejection is reported back with Resend own
explanation, which is the only place that particular failure is legible. The README says
so, and names onboarding@resend.dev as the way to test the wiring first.
