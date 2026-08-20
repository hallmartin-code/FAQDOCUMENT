"""Emailing each generated one-pager, via Resend.

This is the one place deckpager talks to a service other than the Anthropic API, and it is
deliberately the least load-bearing code in the project: **a send that fails must never fail
a run.** The PDF is the product. The email is a notification about the product, and a
notification that could destroy the thing it notifies you about would be a bad trade.

So every failure here is caught, described, and returned as an `EmailOutcome` for the caller
to report. Nothing in this module raises.

Resend's API is a single JSON POST, so it goes through `urllib` rather than adding an HTTP
client or the `resend` SDK to the dependency list. Swap to the SDK if this ever needs
batching, tags, or webhooks.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deckpager.config import Settings
from deckpager.models import DEFAULT_MIN_CONFIDENCE, Faq
from deckpager.questions import QUESTION_COUNT

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the type checker
    from deckpager.pipeline import RunResult

RESEND_ENDPOINT = "https://api.resend.com/emails"

#: Resend sits behind Cloudflare, which rejects urllib's default agent string with a
#: 403 and Cloudflare error 1010 — a bot-signature block that reads nothing like an
#: auth or domain failure. Identifying ourselves is what makes the request pass.
USER_AGENT = "deckpager/1.0 (+https://tencapital.group)"

#: Resend caps a message at 40 MB. A one-pager is tens of kilobytes and the extraction JSON
#: is smaller, so this only bites if someone attaches something unexpected.
MAX_ATTACHMENT_BYTES = 35 * 1024 * 1024

TIMEOUT_S = 20.0


@dataclass(frozen=True)
class EmailOutcome:
    """What happened when the result was mailed. Never an exception."""

    sent: bool
    detail: str
    message_id: str | None = None

    @classmethod
    def skipped(cls, why: str) -> EmailOutcome:
        return cls(sent=False, detail=why)


def describe(settings: Settings) -> str:
    """One line saying what will happen on the next run, for `check` and the README."""
    if not settings.resend_api_key:
        return "off — RESEND_API_KEY is not set, so no results are emailed"
    if not settings.report_email_to:
        return "off — RESEND_API_KEY is set but no recipient is configured"
    return (
        f"on — every result is emailed to {settings.report_email_to} "
        f"from {settings.report_email_from}"
    )


def subject_for(faq: Faq) -> str:
    """`{Company} — TEN Capital investor FAQ`, or the file name when unknown."""
    name = faq.company_name.value or Path(faq.provenance.source_filename).stem
    answered = faq.answered_count()
    return f"{name} - TEN Capital investor FAQ ({answered}/{QUESTION_COUNT} answered)"


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p style='margin:0;color:#6b7280'>—</p>"
    rows = "".join(f"<li style='margin:0 0 4px'>{escape(item)}</li>" for item in items)
    return f"<ul style='margin:0;padding-left:18px'>{rows}</ul>"


def _coverage_bar(faq: Faq, threshold: float) -> str:
    """The three numbers that tell a partner whether this deck is worth a meeting."""
    answered = faq.answered_count()
    cells = [
        ("Answered", f"{answered} of {QUESTION_COUNT}"),
        ("Unanswered", str(QUESTION_COUNT - answered)),
        ("Low confidence", str(len(faq.low_confidence_entries(threshold)))),
    ]
    return "".join(
        f"<td style='padding:8px 12px;border-right:1px solid #d8dce3;vertical-align:top'>"
        f"<div style='font-size:10px;letter-spacing:.06em;color:#6b7280;text-transform:uppercase'>"
        f"{escape(label)}</div>"
        f"<div style='font-size:14px;color:#14181f;margin-top:2px'>{escape(value)}</div>"
        f"</td>"
        for label, value in cells
    )


def build_html(faq: Faq, result: RunResult, threshold: float) -> str:
    """The email body: coverage first, then the questions the document cannot answer.

    A partner reading this on a phone needs two things before deciding whether to open
    the attachment — how much of the deck actually answered the standard questions, and
    what they will have to ask the founders. Everything else is in the PDF.
    """
    provenance = faq.provenance
    unanswered = faq.unanswered()
    flagged = faq.low_confidence_entries(threshold)

    caveats: list[str] = []
    if not faq.is_pitch_deck:
        caveats.append("This document does not read as a pitch deck.")
    caveats += list(provenance.ingest_warnings) + list(provenance.citation_warnings)
    if flagged:
        caveats.append(
            f"{len(flagged)} answer(s) below {threshold:.0%} confidence: "
            + ", ".join(entry.question.text for entry in flagged)
        )

    company = escape(faq.company_name.value or "Unnamed company")
    tagline = escape(faq.tagline.value or "")
    open_items = (
        _bullets([question.text for question in unanswered])
        if unanswered
        else "<p style='margin:0;color:#6b7280'>None — the document addresses all "
        f"{QUESTION_COUNT} questions.</p>"
    )

    return f"""<div style="font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#14181f;max-width:640px">
  <div style="border-bottom:2px solid #1f3864;padding-bottom:12px;margin-bottom:18px">
    <div style="font:600 22px/1.2 Georgia,serif">{company}</div>
    <div style="color:#6b7280;font-size:14px;margin-top:4px">{tagline}</div>
    <div style="color:#6b7280;font-size:12px;margin-top:6px">TEN Capital investor FAQ</div>
  </div>

  <table style="width:100%;border-collapse:collapse;background:#eef2f8;border-radius:6px;margin-bottom:20px">
    <tr>{_coverage_bar(faq, threshold)}</tr>
  </table>

  <div style="margin-bottom:18px">
    <div style="font:600 13px/1.3 Georgia,serif;color:#1f3864;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Ask the founders</div>
    {open_items}
  </div>

  <div style="background:#f6f5f2;border-radius:6px;padding:14px 16px;margin-bottom:18px">
    <div style="font-style:italic;font-size:12px;color:#6b7280;margin-bottom:8px">
      TEN Capital analysis — AI-generated. Every answer in the attached FAQ cites the
      slide or section it came from; answers are not the founders' words unless quoted.
    </div>
    {_bullets(caveats) if caveats else "<div style='font-size:13px;color:#6b7280'>No caveats: nothing was flagged.</div>"}
  </div>

  <div style="font-size:12px;color:#6b7280;border-top:1px solid #d8dce3;padding-top:12px">
    The full FAQ is attached as a PDF.<br>
    Source: {escape(provenance.source_filename)} · {provenance.source_page_count} slides ·
    {escape(provenance.model)} · {escape(result.summary)}<br>
    Generated by TEN Capital · Internal use only.
  </div>
</div>"""


def build_text(faq: Faq, result: RunResult) -> str:
    """A plain-text alternative, for clients that will not render HTML."""
    answered = faq.answered_count()
    lines = [
        faq.company_name.value or "Unnamed company",
        faq.tagline.value or "",
        "",
        "TEN CAPITAL INVESTOR FAQ",
        f"  Answered:       {answered} of {QUESTION_COUNT}",
        f"  Unanswered:     {QUESTION_COUNT - answered}",
        "",
        "ASK THE FOUNDERS",
    ]
    unanswered = faq.unanswered()
    lines += (
        [f"  - {question.text}" for question in unanswered]
        if unanswered
        else ["  - Nothing: the document addresses all twenty questions."]
    )
    lines += [
        "",
        "Every answer in the attached PDF cites the slide or section it came from.",
        "Answers are TEN Capital analysis, not the deck's own words unless quoted.",
        "",
        f"Source: {faq.provenance.source_filename} "
        f"({faq.provenance.source_page_count} slides)",
        result.summary,
        "Generated by TEN Capital - internal use only.",
    ]
    return "\n".join(lines)


def _attachment(path: Path) -> dict[str, str] | None:
    """One Resend attachment, or None if the file is missing or implausibly large."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_ATTACHMENT_BYTES:
        return None
    return {
        "filename": path.name,
        "content": base64.standard_b64encode(raw).decode("ascii"),
    }


def build_payload(
    faq: Faq,
    result: RunResult,
    settings: Settings,
    threshold: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """The Resend request body. Separated from sending so it can be tested offline."""
    attachments = [a for a in (_attachment(result.pdf),) if a]
    if settings.email_attach_json:
        attachments += [a for a in (_attachment(result.json),) if a]

    return {
        "from": settings.report_email_from,
        "to": [address.strip() for address in settings.report_email_to.split(",") if address.strip()],
        "subject": subject_for(faq),
        "html": build_html(faq, result, threshold),
        "text": build_text(faq, result),
        "attachments": attachments,
    }


def send(
    faq: Faq,
    result: RunResult,
    settings: Settings,
    threshold: float = DEFAULT_MIN_CONFIDENCE,
) -> EmailOutcome:
    """Mail the result. Returns what happened; never raises, never logs the key."""
    if not settings.resend_api_key:
        return EmailOutcome.skipped("RESEND_API_KEY is not set; the result was not emailed")
    if not settings.report_email_to:
        return EmailOutcome.skipped("no recipient configured; the result was not emailed")

    payload = build_payload(faq, result, settings, threshold)
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint, not user input
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        # Resend explains rejections in the body, and the explanation is the useful part:
        # an unverified sending domain says so here and nowhere else.
        detail = exc.read().decode("utf-8", "replace")[:400]
        return EmailOutcome(
            sent=False,
            detail=f"Resend rejected the message ({exc.code}): {_readable(detail)}",
        )
    except urllib.error.URLError as exc:
        return EmailOutcome(sent=False, detail=f"Could not reach Resend: {exc.reason}")
    except (TimeoutError, json.JSONDecodeError, OSError) as exc:
        return EmailOutcome(sent=False, detail=f"Emailing the result failed: {exc}")

    return EmailOutcome(
        sent=True,
        detail=f"emailed to {settings.report_email_to}",
        message_id=str(body.get("id")) if isinstance(body, dict) else None,
    )


def _readable(body: str) -> str:
    """Pull Resend's message out of its JSON error body, falling back to the raw text."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(parsed, dict):
        return str(parsed.get("message") or parsed.get("error") or body)
    return body
