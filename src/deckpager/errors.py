"""Error hierarchy for deckpager.

Every failure mode the user can hit exits non-zero with a human-readable message.
`exit_code` is what the CLI hands back to the shell, and the codes are the five in the
build spec §10 — a caller scripting `deckpager` distinguishes "your file was wrong" from
"the model failed" from "your environment is not set up" without parsing stderr.

    0  success
    1  bad input: unreadable, corrupt, or unsupported deck
    2  extraction failed after retries
    3  render failed
    4  configuration or environment problem
"""

from __future__ import annotations

#: Bad input.
EXIT_BAD_INPUT = 1
#: Extraction failed after retries.
EXIT_EXTRACTION_FAILED = 2
#: Render failed.
EXIT_RENDER_FAILED = 3
#: Configuration or environment problem.
EXIT_CONFIG = 4


class DeckpagerError(Exception):
    """Base class for every deckpager failure."""

    exit_code: int = EXIT_BAD_INPUT


class ConfigError(DeckpagerError):
    """Missing or invalid configuration (e.g. no ANTHROPIC_API_KEY)."""

    exit_code = EXIT_CONFIG


class IngestError(DeckpagerError):
    """The deck could not be read, parsed, or converted."""

    exit_code = EXIT_BAD_INPUT


class UnsupportedFormatError(IngestError):
    """The file is not a PDF, PPTX, or PPT."""

    exit_code = EXIT_BAD_INPUT


class AnalysisError(DeckpagerError):
    """The model call failed, refused, or returned unusable output."""

    exit_code = EXIT_EXTRACTION_FAILED


class SchemaValidationError(AnalysisError):
    """The model's tool payload did not validate against the OnePager schema.

    Shares the extraction exit code: from the shell's point of view the extraction is what
    failed, and the correction retry has already been spent by the time this escapes.
    """

    exit_code = EXIT_EXTRACTION_FAILED


class RenderError(DeckpagerError):
    """A renderer could not produce its output."""

    exit_code = EXIT_RENDER_FAILED


class OnePagerOverflowError(RenderError):
    """The one-pager could not be fitted onto one page.

    Distinct from RenderError because it is not an engine failure — the document rendered
    fine, it was just too long. Emitting page 2 would be the silent failure this exists to
    prevent, so the run fails loudly instead.
    """

    exit_code = EXIT_RENDER_FAILED
