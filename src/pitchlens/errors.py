"""Error hierarchy for pitchlens.

Every failure mode the user can hit exits non-zero with a human-readable message.
`exit_code` is what the CLI hands back to the shell.
"""

from __future__ import annotations


class PitchlensError(Exception):
    """Base class for every pitchlens failure."""

    exit_code: int = 1


class ConfigError(PitchlensError):
    """Missing or invalid configuration (e.g. no ANTHROPIC_API_KEY)."""

    exit_code = 2


class IngestError(PitchlensError):
    """The deck could not be read, parsed, or converted."""

    exit_code = 3


class UnsupportedFormatError(IngestError):
    """The file is not a PDF or PPTX."""

    exit_code = 3


class AnalysisError(PitchlensError):
    """The model call failed, refused, or returned unusable output."""

    exit_code = 4


class SchemaValidationError(AnalysisError):
    """The model's tool payload did not validate against the Assessment schema."""

    exit_code = 5


class RenderError(PitchlensError):
    """A renderer could not produce its output."""

    exit_code = 6


class OnePagerOverflowError(RenderError):
    """The one-pager could not be fitted onto one page.

    Distinct from RenderError because it is not an engine failure — the document rendered
    fine, it was just too long. Emitting page 2 would be the silent failure this exists to
    prevent, so the run fails loudly instead.
    """

    exit_code = 7
