"""deckpager web app.

Upload a deck, get the FAQ. Shaped for Railway: FastAPI + uvicorn, background jobs,
a TTL sweep, and a concurrency cap.

A run takes minutes, not seconds — a 30-slide deck took 101s against Opus 5 at effort high.
So the upload returns a job id immediately and the browser polls. Holding the HTTP request
open would hit Railway's proxy timeout and lose work that has already been paid for.

The job runs through `deckpager.pipeline.run`, the same function the CLI calls, so a deck
analysed in the browser and a deck analysed at a terminal produce identical output.

Environment
-----------
    ANTHROPIC_API_KEY   required — the app refuses to start without it
    APP_PASSWORD        strongly recommended — when set, the whole app sits behind HTTP
                        Basic. Unset means anyone with the URL can spend your API key.
    APP_USERNAME        optional — defaults to "ten"; only used when APP_PASSWORD is set
    MAX_CONCURRENT_JOBS optional — default 2
    JOB_TTL_MINUTES     optional — default 180
    MAX_UPLOAD_MB       optional — default 25
    JOBS_DIR            optional — defaults to a temp directory
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from deckpager import __version__
from deckpager.config import load_settings
from deckpager.errors import DeckpagerError
from deckpager.ingest.router import SUPPORTED_SUFFIXES
from deckpager.models import DEFAULT_MIN_CONFIDENCE

APP_USERNAME = os.getenv("APP_USERNAME", "ten")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
JOBS_DIR = Path(os.getenv("JOBS_DIR") or Path(tempfile.gettempdir()) / "deckpager-jobs")
JOB_TTL = timedelta(minutes=int(os.getenv("JOB_TTL_MINUTES", "180")))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
WEB_DIR = Path(__file__).parent / "web"

Stage = Literal[
    "queued", "ingesting", "extracting", "cached", "rendering", "emailing", "done", "failed"
]

_basic = HTTPBasic(auto_error=False)
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


@dataclass
class Job:
    """One run. Held in memory; the artifacts live on disk under JOBS_DIR."""

    id: str
    filename: str
    stage: Stage = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    company: str | None = None
    tagline: str | None = None
    summary: str | None = None
    flagged: list[str] = field(default_factory=list)
    truncations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_pitch_deck: bool = True
    emailed: str | None = None
    emailed_ok: bool = False
    facts: list[tuple[str, str | None]] = field(default_factory=list)

    @property
    def directory(self) -> Path:
        return JOBS_DIR / self.id

    def public(self) -> dict[str, Any]:
        """What the browser polls for. Never a path, never a key."""
        return {
            "id": self.id,
            "filename": self.filename,
            "stage": self.stage,
            "error": self.error,
            "company": self.company,
            "tagline": self.tagline,
            "summary": self.summary,
            "flagged": self.flagged,
            "truncations": self.truncations,
            "warnings": self.warnings,
            "is_pitch_deck": self.is_pitch_deck,
            "emailed": self.emailed,
            "emailed_ok": self.emailed_ok,
            "facts": self.facts,
        }


JOBS: dict[str, Job] = {}


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """HTTP Basic gate — active only when APP_PASSWORD is set.

    Optional rather than mandatory so a deployment can be opened deliberately, but every
    unauthenticated request spends the deployment's API key, so leaving it unset is a
    decision and not a default worth drifting into. `/healthz` reports which it is.
    """
    if not APP_PASSWORD:
        return
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username, APP_USERNAME)
        and secrets.compare_digest(credentials.password, APP_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Not authorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def sweep_expired() -> None:
    """Drop jobs past their TTL and delete their artifacts.

    Uploaded decks are confidential and the analyses derived from them are too, so they do
    not sit on the server indefinitely waiting for someone to remember them.
    """
    cutoff = datetime.now(UTC) - JOB_TTL
    for job_id, job in list(JOBS.items()):
        if job.created_at < cutoff:
            shutil.rmtree(job.directory, ignore_errors=True)
            JOBS.pop(job_id, None)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Fail at boot on a misconfiguration rather than on the first upload."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. deckpager cannot analyze anything without it — "
            "set it as a Railway service variable (or in .env locally) and redeploy."
        )
    yield
    shutil.rmtree(JOBS_DIR, ignore_errors=True)


app = FastAPI(title="deckpager", version=__version__, lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Railway's healthcheck. Reports configuration without leaking any of it."""
    return {
        "ok": True,
        "version": __version__,
        "api_key_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        "auth_enabled": bool(APP_PASSWORD),
        "jobs": len(JOBS),
    }


def _facts(faq: Any) -> list[tuple[str, str | None]]:
    """The result panel: how much of the FAQ the document could actually answer."""
    from deckpager.questions import QUESTION_COUNT

    answered = faq.answered_count()
    return [
        ("Answered", f"{answered} of {QUESTION_COUNT}"),
        ("Unanswered", str(QUESTION_COUNT - answered)),
        ("Sector", faq.sector.value),
        ("Stage", faq.stage.value),
    ]

def _run_job(job: Job, deck_path: Path, paper: str, min_confidence: float) -> None:
    """The run itself, in a worker thread. Never touches the event loop."""
    from deckpager.pipeline import run

    try:
        settings = load_settings()

        def set_stage(name: str) -> None:
            job.stage = name  # type: ignore[assignment]

        result = run(
            deck_path,
            settings=settings,
            out_pdf=job.directory / "faq.pdf",
            out_json=job.directory / "faq.json",
            paper=paper,  # type: ignore[arg-type]
            min_confidence=min_confidence,
            on_stage=set_stage,
        )

        faq = result.faq
        job.company = faq.company_name.value
        job.tagline = faq.tagline.value
        job.summary = result.summary
        job.flagged = [
            name
            for name in faq.low_confidence_fields(min_confidence)

        ]
        job.truncations = list(result.truncations)
        job.warnings = list(faq.provenance.ingest_warnings) + list(
            faq.provenance.citation_warnings
        )
        job.is_pitch_deck = faq.is_pitch_deck
        job.facts = _facts(faq)
        if result.email is not None:
            job.emailed = result.email.detail
            job.emailed_ok = result.email.sent
        job.stage = "done"
    except DeckpagerError as exc:
        job.stage, job.error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad deck must not take the server down
        job.stage, job.error = "failed", f"Unexpected error: {exc}"
    finally:
        # The deck itself goes as soon as it has been read. The artifacts stay until the
        # TTL sweep; the founder's original does not need to.
        deck_path.unlink(missing_ok=True)


async def _dispatch(job: Job, deck_path: Path, paper: str, min_confidence: float) -> None:
    """Queue behind the concurrency cap, then run off the event loop."""
    async with _semaphore:
        await asyncio.to_thread(_run_job, job, deck_path, paper, min_confidence)


@app.post("/api/render", dependencies=[Depends(require_auth)])
async def render(
    deck: UploadFile = File(...),
    paper: str = Form(default="letter"),
    min_confidence: float = Form(default=DEFAULT_MIN_CONFIDENCE),
) -> JSONResponse:
    """Accept a deck and start a job. Returns immediately with an id to poll."""
    sweep_expired()

    suffix = Path(deck.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"{suffix or 'That file type'} is not supported. "
            f"Upload one of: {', '.join(sorted(SUPPORTED_SUFFIXES))}.",
        )
    if paper not in ("letter", "a4"):
        raise HTTPException(status_code=400, detail="paper must be 'letter' or 'a4'.")
    if not 0.0 <= min_confidence <= 1.0:
        raise HTTPException(status_code=400, detail="min_confidence must be between 0 and 1.")

    job = Job(id=uuid.uuid4().hex[:12], filename=deck.filename or "deck")
    job.directory.mkdir(parents=True, exist_ok=True)
    deck_path = job.directory / f"upload{suffix}"

    # The cleanup happens after the handle is closed, not inside the `with`. Windows
    # refuses to delete an open file, and `ignore_errors=True` swallowed the refusal —
    # so a rejected oversize upload used to leave the partial deck on the server.
    written = 0
    too_big = False
    with deck_path.open("wb") as handle:
        while chunk := await deck.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                too_big = True
                break
            handle.write(chunk)

    if too_big:
        shutil.rmtree(job.directory, ignore_errors=True)
        raise HTTPException(
            status_code=413,
            detail=f"Deck is larger than {MAX_UPLOAD_MB} MB. "
            f"Export a smaller PDF, or raise MAX_UPLOAD_MB.",
        )

    JOBS[job.id] = job
    asyncio.create_task(_dispatch(job, deck_path, paper, min_confidence))
    return JSONResponse(job.public(), status_code=202)


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def job_status(job_id: str) -> dict[str, Any]:
    """Poll a job."""
    if (job := JOBS.get(job_id)) is None:
        raise HTTPException(status_code=404, detail="No such job — it may have expired.")
    return job.public()


@app.get("/api/jobs/{job_id}/{artifact}", dependencies=[Depends(require_auth)])
def download(job_id: str, artifact: str) -> FileResponse:
    """Download the FAQ PDF or the extraction JSON."""
    if (job := JOBS.get(job_id)) is None:
        raise HTTPException(status_code=404, detail="No such job — it may have expired.")
    if job.stage != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.stage}, not done.")

    names = {"pdf": "faq.pdf", "json": "faq.json"}
    if artifact not in names:
        raise HTTPException(status_code=404, detail="Ask for 'pdf' or 'json'.")

    path = job.directory / names[artifact]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That artifact was not produced.")

    stem = (job.company or Path(job.filename).stem or "deck").replace(" ", "_")
    return FileResponse(
        path,
        media_type="application/pdf" if artifact == "pdf" else "application/json",
        filename=f"{stem}-FAQ.{artifact}",
    )


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index() -> str:
    """The upload page, with its limits substituted from the live configuration.

    A page that advertises a file type the router rejects, a size cap the server does
    not enforce, or an email nobody configured is worse than no page at all — so the
    accepted types, the limits, the retention window, and the mail disclosure all come
    from the same values the server uses, rather than being written into the HTML.
    """

    accept = sorted(SUPPORTED_SUFFIXES)
    settings = load_settings()
    disclosure = (
        f"A copy of every generated FAQ is emailed to "
        f"<code>{settings.report_email_to}</code>."
        if settings.email_enabled
        else "Nothing is emailed: no Resend key is configured on this deployment."
    )
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for token, value in {
        "__ACCEPT__": ",".join(accept),
        "__ACCEPT_JSON__": json.dumps(accept),
        "__ACCEPT_LABEL__": " · ".join(accept),
        "__MAX_MB__": str(MAX_UPLOAD_MB),
        "__MAX_BYTES__": str(MAX_UPLOAD_BYTES),
        "__VERSION__": __version__,
        "__AUTH__": "on" if APP_PASSWORD else "off",
        "__EMAIL_DISCLOSURE__": disclosure,
        "__TTL_HOURS__": f"{JOB_TTL.total_seconds() / 3600:.0f}",
    }.items():
        html = html.replace(token, value)
    return html
