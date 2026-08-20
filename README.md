# deckpager

Turn an investor pitch deck (`.pdf`, `.pptx`, `.ppt`) into a **single-page, print-ready TEN Capital
investor one-pager**, with every extracted claim traceable to a source slide.

Status: **Phase 0 — scaffold.** `deckpager check` works; ingestion, extraction and rendering land in
later phases. The build plan lives in [CLAUDE.md](CLAUDE.md).

## Install

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,reportlab]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"      # macOS / Linux
cp .env.example .env        # then add your ANTHROPIC_API_KEY
```

## Verify the environment

```bash
python -m deckpager check
```

Reports Python version, API key, cache directory, LibreOffice (needed only for `.ppt` and for `.pptx`
page images) and the available PDF engine. WeasyPrint needs `pango`/`cairo`/`gdk-pixbuf`; where those
are unavailable (notably bare Windows) install the `reportlab` extra and use `--engine reportlab`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | bad input / unsupported file |
| 2 | extraction failed after retries |
| 3 | render failed |
| 4 | config or environment problem |

## Development

```bash
make test        # pytest
make cov         # pytest with coverage
make lint        # mypy --strict
```

Secrets come from the environment or `.env` only. `.env` is git-ignored; the API key is held as a
`SecretStr` and is never logged.
