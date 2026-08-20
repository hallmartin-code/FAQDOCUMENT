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
from deckpager.models import DEFAULT_MIN_CONFIDENCE, OnePager
from deckpager.render.onepager import RENDERED_FIELDS, money

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the type checker
    from deckpager.pipeline import RunResult

RESEND_ENDPOINT = "https://api.resend.com/emails"

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


def subject_for(one_pager: OnePager) -> str:
    """`{Company} — TEN Capital one-pager`, or the deck name when the company is unknown."""
    name = one_pager.company_name.value or Path(one_pager.provenance.source_filename).stem
    return f"{name} — TEN Capital one-pager"


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p style='margin:0;color:#6b7280'>—</p>"
    rows = "".join(f"<li style='margin:0 0 4px'>{escape(item)}</li>" for item in items)
    return f"<ul style='margin:0;padding-left:18px'>{rows}</ul>"


def _ask_row(one_pager: OnePager) -> str:
    cells = [
        ("Raise", money(one_pager.raise_amount_usd.value)),
        ("Pre-money", money(one_pager.pre_money_valuation_usd.value)),
        ("Instrument", one_pager.instrument.value),
        ("Committed", money(one_pager.amount_committed_usd.value)),
        ("Close", one_pager.close_date.value),
    ]
    return "".join(
        f"<td style='padding:8px 12px;border-right:1px solid #d8dce3;vertical-align:top'>"
        f"<div style='font-size:10px;letter-spacing:.06em;color:#6b7280;text-transform:uppercase'>"
        f"{escape(label)}</div>"
        f"<div style='font-size:14px;color:#14181f;margin-top:2px'>{escape(value or '—')}</div>"
        f"</td>"
        for label, value in cells
    )


def build_html(one_pager: OnePager, result: RunResult, threshold: float) -> str:
    """The email body: the same shape as the page, so the two are recognisably one thing."""
    flagged = [
        name
        for name in one_pager.low_confidence_fields(threshold)
        if name in set(RENDERED_FIELDS)
    ]
    provenance = one_pager.provenance
    caveats: list[str] = []
    if not one_pager.is_pitch_deck:
        caveats.append("This document does not read as a pitch deck.")
    caveats += list(provenance.ingest_warnings) + list(provenance.citation_warnings)
    caveats += [f"{cut} (to keep it to one page)" for cut in result.truncations]
    if flagged:
        caveats.append(
            f"{len(flagged)} field(s) below {threshold:.0%} confidence: {', '.join(sorted(flagged))}"
        )

    company = escape(one_pager.company_name.value or "Unnamed company")
    tagline = escape(one_pager.tagline.value or "")

    return f"""\
<div style="font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#14181f;max-width:640px">
  <div style="border-bottom:2px solid #1f3864;padding-bottom:12px;margin-bottom:18px">
    <div style="font:600 22px/1.2 Georgia,serif">{company}</div>
    <div style="color:#6b7280;font-size:14px;margin-top:4px">{tagline}</div>
  </div>

  <table style="width:100%;border-collapse:collapse;background:#eef2f8;border-radius:6px;margin-bottom:20px">
    <tr>{_ask_row(one_pager)}</tr>
  </table>

  <div style="display:block;margin-bottom:18px">
    <div style="font:600 13px/1.3 Georgia,serif;color:#1f3864;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Strengths</div>
    {_bullets(one_pager.key_strengths.value or [])}
  </div>
  <div style="margin-bottom:18px">
    <div style="font:600 13px/1.3 Georgia,serif;color:#1f3864;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Risks</div>
    {_bullets(one_pager.key_risks.value or [])}
  </div>
  <div style="margin-bottom:18px">
    <div style="font:600 13px/1.3 Georgia,serif;color:#1f3864;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Request from founder</div>
    {_bullets(one_pager.missing_information.value or [])}
  </div>

  <div style="background:#f6f5f2;border-radius:6px;padding:14px 16px;margin-bottom:18px">
    <div style="font-style:italic;font-size:12px;color:#6b7280;margin-bottom:8px">
      TEN Capital analysis — AI-generated. Strengths, risks, and requests above are
      deckpager's judgment, not the deck's claims.
    </div>
    {_bullets(caveats) if caveats else "<div style='font-size:13px;color:#6b7280'>No caveats: nothing was truncated and nothing was flagged.</div>"}
  </div>

  <div style="font-size:12px;color:#6b7280;border-top:1px solid #d8dce3;padding-top:12px">
    The one-pager PDF is attached.<br>
    Source: {escape(provenance.source_filename)} · {provenance.source_page_count} slides ·
    {escape(provenance.model)} · {escape(result.summary)}<br>
    Generated by TEN Capital · Internal use only.
  </div>
</div>"""


def build_text(one_pager: OnePager, result: RunResult) -> str:
    """A plain-text alternative, for clients that will not render HTML."""
    lines = [
        one_pager.company_name.value or "Unnamed company",
        one_pager.tagline.value or "",
        "",
        f"Raise:      {money(one_pager.raise_amount_usd.value) or '—'}",
        f"Pre-money:  {money(one_pager.pre_money_valuation_usd.value) or '—'}",
        f"Instrument: {one_pager.instrument.value or '—'}",
        f"Committed:  {money(one_pager.amount_committed_usd.value) or '—'}",
        f"Close:      {one_pager.close_date.value or '—'}",
        "",
        "STRENGTHS",
    ]
    lines += [f"  - {item}" for item in one_pager.key_strengths.value or ["—"]]
    lines += ["", "RISKS"]
    lines += [f"  - {item}" for item in one_pager.key_risks.value or ["—"]]
    lines += ["", "REQUEST FROM FOUNDER"]
    lines += [f"  - {item}" for item in one_pager.missing_information.value or ["—"]]
    lines += [
        "",
        "Strengths, risks, and requests are deckpager's analysis, not the deck's claims.",
        "",
        f"Source: {one_pager.provenance.source_filename} "
        f"({one_pager.provenance.source_page_count} slides)",
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
    one_pager: OnePager,
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
        "subject": subject_for(one_pager),
        "html": build_html(one_pager, result, threshold),
        "text": build_text(one_pager, result),
        "attachments": attachments,
    }


def send(
    one_pager: OnePager,
    result: RunResult,
    settings: Settings,
    threshold: float = DEFAULT_MIN_CONFIDENCE,
) -> EmailOutcome:
    """Mail the result. Returns what happened; never raises, never logs the key."""
    if not settings.resend_api_key:
        return EmailOutcome.skipped("RESEND_API_KEY is not set; the result was not emailed")
    if not settings.report_email_to:
        return EmailOutcome.skipped("no recipient configured; the result was not emailed")

    payload = build_payload(one_pager, result, settings, threshold)
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint, not user input
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
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
