"""The web app: the upload gate, the job lifecycle, and what it refuses to leak.

The pipeline itself is stubbed. These tests are about the server — that it rejects what it
says it rejects, that a job's public shape never carries a path or a key, and that a
failing deck fails the job rather than the process.
"""

from __future__ import annotations

import re

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app refuses to boot without one; these tests never spend it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """A client whose jobs land in a scratch directory, with email off by default.

    RESEND_API_KEY is cleared deliberately: once a real key lives in the developer's
    .env, an inherited one silently flips the page into "email configured" mode and the
    unconfigured-deployment tests assert against a deployment that no longer exists.
    """
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    # An empty value, not a deleted one: Settings also reads .env, which the deleted
    # variable would not shadow. An explicit empty env var outranks the file and reads
    # as "no key" to email_enabled.
    monkeypatch.setenv("RESEND_API_KEY", "")
    import importlib

    import app as app_module

    module = importlib.reload(app_module)
    with TestClient(module.app) as test_client:
        test_client.module = module  # type: ignore[attr-defined]
        yield test_client


def stub_run(monkeypatch: pytest.MonkeyPatch, module: Any, **overrides: Any) -> None:
    """Replace the pipeline with one that writes plausible artifacts and returns fast."""
    import json as json_lib
    from types import SimpleNamespace

    def fake_run(deck_path: Path, **kwargs: Any) -> Any:
        pdf = kwargs["out_pdf"]
        js = kwargs["out_json"]
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 pretend")
        js.write_text(json_lib.dumps({"company_name": {"value": "Helion Bio"}}), encoding="utf-8")
        one_pager = SimpleNamespace(
            company_name=SimpleNamespace(value=overrides.get("company", "Helion Bio")),
            tagline=SimpleNamespace(value="Reprogramming macrophages"),
            raise_amount_usd=SimpleNamespace(value=4_000_000),
            pre_money_valuation_usd=SimpleNamespace(value=16_000_000),
            amount_committed_usd=SimpleNamespace(value=1_100_000),
            stage=SimpleNamespace(value="Seed"),
            close_date=SimpleNamespace(value="Q3 2026"),
            low_confidence_fields=lambda _t: overrides.get("flagged", ["website"]),
            is_pitch_deck=overrides.get("is_pitch_deck", True),
            provenance=SimpleNamespace(ingest_warnings=[], citation_warnings=[]),
        )
        return SimpleNamespace(
            one_pager=one_pager,
            pdf=pdf,
            json=js,
            truncations=overrides.get("truncations", []),
            summary="Extracted in 8.2s · 24,310 in / 1,842 out · ~$0.11",
            email=overrides.get("email"),
        )

    import deckpager.pipeline as pipeline

    monkeypatch.setattr(pipeline, "run", fake_run)


def upload(client: TestClient, name: str = "deck.pdf", body: bytes = b"%PDF-1.4 x") -> Any:
    return client.post("/api/render", files={"deck": (name, body, "application/pdf")})


def wait_for_done(client: TestClient, job_id: str, tries: int = 200) -> dict[str, Any]:
    """Poll until the background job settles."""
    import time

    for _ in range(tries):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["stage"] in ("done", "failed"):
            return payload
        time.sleep(0.02)
    raise AssertionError("the job never settled")


class TestHealth:
    def test_healthz_reports_configuration_without_leaking_it(self, client: TestClient) -> None:
        payload = client.get("/healthz").json()
        assert payload["ok"] is True
        assert payload["api_key_configured"] is True
        assert "sk-ant" not in str(payload)

    def test_healthz_says_whether_the_app_is_open(self, client: TestClient) -> None:
        """An open deployment spends the operator's key; it must be visible that it is."""
        assert client.get("/healthz").json()["auth_enabled"] is False

    def test_the_app_refuses_to_boot_without_a_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import importlib

        import app as app_module

        module = importlib.reload(app_module)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"), TestClient(module.app):
            pass


class TestUploadGate:
    def test_an_unsupported_extension_is_refused(self, client: TestClient) -> None:
        response = upload(client, "notes.txt", b"just text")
        assert response.status_code == 400
        assert "not supported" in response.json()["detail"]

    def test_an_oversized_deck_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client.module, "MAX_UPLOAD_BYTES", 10)  # type: ignore[attr-defined]
        response = upload(client, "deck.pdf", b"x" * 4096)
        assert response.status_code == 413
        assert "larger than" in response.json()["detail"]

    def test_a_bad_paper_size_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/render",
            files={"deck": ("deck.pdf", b"%PDF", "application/pdf")},
            data={"paper": "foolscap"},
        )
        assert response.status_code == 400

    def test_a_confidence_outside_zero_to_one_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/render",
            files={"deck": ("deck.pdf", b"%PDF", "application/pdf")},
            data={"min_confidence": "4"},
        )
        assert response.status_code == 400

    def test_a_rejected_upload_leaves_nothing_behind(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(client.module, "MAX_UPLOAD_BYTES", 10)  # type: ignore[attr-defined]
        upload(client, "deck.pdf", b"x" * 4096)
        jobs_dir = Path(os.environ["JOBS_DIR"])
        assert not any(jobs_dir.rglob("upload*")) if jobs_dir.exists() else True


class TestJobLifecycle:
    def test_a_run_produces_both_artifacts(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        job_id = upload(client).json()["id"]
        payload = wait_for_done(client, job_id)

        assert payload["stage"] == "done"
        assert payload["company"] == "Helion Bio"
        assert client.get(f"/api/jobs/{job_id}/pdf").status_code == 200
        assert client.get(f"/api/jobs/{job_id}/json").status_code == 200

    def test_the_download_is_named_for_the_company(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It lands in a folder of other companies' one-pagers."""
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        job_id = upload(client).json()["id"]
        wait_for_done(client, job_id)
        disposition = client.get(f"/api/jobs/{job_id}/pdf").headers["content-disposition"]
        assert "Helion_Bio-onepager.pdf" in disposition

    def test_the_uploaded_deck_is_deleted_once_it_has_been_read(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The founder's original does not need to sit on the server."""
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        job_id = upload(client).json()["id"]
        wait_for_done(client, job_id)
        assert not (Path(os.environ["JOBS_DIR"]) / job_id / "upload.pdf").exists()

    def test_a_failing_deck_fails_the_job_not_the_server(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import deckpager.pipeline as pipeline
        from deckpager.errors import IngestError

        def boom(*_a: object, **_k: object) -> None:
            raise IngestError("deck.pdf is password-protected; decrypt it before analysis.")

        monkeypatch.setattr(pipeline, "run", boom)
        job_id = upload(client).json()["id"]
        payload = wait_for_done(client, job_id)

        assert payload["stage"] == "failed"
        assert "password-protected" in payload["error"]
        assert client.get("/healthz").json()["ok"] is True

    def test_an_unexpected_error_is_still_only_a_failed_job(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import deckpager.pipeline as pipeline

        def boom(*_a: object, **_k: object) -> None:
            raise ZeroDivisionError("something nobody predicted")

        monkeypatch.setattr(pipeline, "run", boom)
        payload = wait_for_done(client, upload(client).json()["id"])
        assert payload["stage"] == "failed"
        assert "Unexpected error" in payload["error"]

    def test_downloads_are_refused_while_a_job_is_running(self, client: TestClient) -> None:
        job = client.module.Job(id="pending", filename="deck.pdf")  # type: ignore[attr-defined]
        client.module.JOBS["pending"] = job  # type: ignore[attr-defined]
        assert client.get("/api/jobs/pending/pdf").status_code == 409

    def test_an_unknown_job_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nosuchjob").status_code == 404

    def test_an_unknown_artifact_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        job_id = upload(client).json()["id"]
        wait_for_done(client, job_id)
        assert client.get(f"/api/jobs/{job_id}/docx").status_code == 404


class TestEmailReporting:
    def test_a_send_is_reported_to_the_browser(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deckpager.mailer import EmailOutcome

        stub_run(
            monkeypatch,
            client.module,  # type: ignore[attr-defined]
            email=EmailOutcome(sent=True, detail="emailed to Info@tencapital.group"),
        )
        payload = wait_for_done(client, upload(client).json()["id"])
        assert payload["emailed"] == "emailed to Info@tencapital.group"

    def test_a_failed_send_is_reported_but_the_job_still_succeeds(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PDF is the product; the email is a notification about it."""
        from deckpager.mailer import EmailOutcome

        stub_run(
            monkeypatch,
            client.module,  # type: ignore[attr-defined]
            email=EmailOutcome(sent=False, detail="Resend rejected the message (403)"),
        )
        job_id = upload(client).json()["id"]
        payload = wait_for_done(client, job_id)
        assert payload["stage"] == "done"
        assert "403" in payload["emailed"]
        assert client.get(f"/api/jobs/{job_id}/pdf").status_code == 200

    def test_the_ask_reaches_the_result_panel(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The panel shows the same five cells as the PDF ask strip."""
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        payload = wait_for_done(client, upload(client).json()["id"])
        labels = [label for label, _ in payload["facts"]]
        assert labels == ["Raise", "Pre-money", "Committed", "Stage", "Close"]
        assert [v for _, v in payload["facts"]] == ["$4M", "$16M", "$1.1M", "Seed", "Q3 2026"]

class TestLeakage:
    def test_the_job_payload_carries_no_path_and_no_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_run(monkeypatch, client.module)  # type: ignore[attr-defined]
        payload = wait_for_done(client, upload(client).json()["id"])
        blob = str(payload)
        assert "sk-ant" not in blob
        assert os.environ["JOBS_DIR"] not in blob

    def test_a_document_that_is_not_a_deck_is_reported_as_such(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_run(monkeypatch, client.module, is_pitch_deck=False)  # type: ignore[attr-defined]
        payload = wait_for_done(client, upload(client).json()["id"])
        assert payload["is_pitch_deck"] is False


class TestAuth:
    def test_a_password_closes_the_whole_app(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APP_PASSWORD", "letmein")
        monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
        import importlib

        import app as app_module

        module = importlib.reload(app_module)
        with TestClient(module.app) as closed:
            assert closed.get("/").status_code == 401
            assert closed.post("/api/render").status_code == 401
            # The healthcheck stays open, or Railway cannot see the service.
            assert closed.get("/healthz").status_code == 200
            assert closed.get("/healthz").json()["auth_enabled"] is True
            assert closed.get("/", auth=("ten", "letmein")).status_code == 200

    def test_the_wrong_password_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APP_PASSWORD", "letmein")
        monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
        import importlib

        import app as app_module

        module = importlib.reload(app_module)
        with TestClient(module.app) as closed:
            assert closed.get("/", auth=("ten", "wrong")).status_code == 401


class TestIndexPage:
    def test_the_page_advertises_the_limits_the_server_enforces(
        self, client: TestClient
    ) -> None:
        """A page promising a file type the router rejects is worse than no page."""
        html = client.get("/").text
        assert str(client.module.MAX_UPLOAD_MB) in html  # type: ignore[attr-defined]
        for suffix in (".pdf", ".pptx", ".ppt"):
            assert suffix in html

    def test_the_page_advertises_exactly_what_the_router_accepts(
        self, client: TestClient
    ) -> None:
        """A page offering a type the router refuses wastes an upload and a partner's time.

        Written when .docx was refused and the design mock offered it anyway. Now that the
        ingest layer accepts .docx, the assertion is the invariant rather than the list:
        the page and the router agree, whatever the set happens to be.
        """
        from deckpager.ingest.router import SUPPORTED_SUFFIXES

        html = client.get("/").text
        advertised = re.search(r'accept="([^"]*)"', html)
        assert advertised is not None
        assert set(advertised.group(1).split(",")) == set(SUPPORTED_SUFFIXES)

    def test_every_placeholder_is_substituted(self, client: TestClient) -> None:
        html = client.get("/").text
        for token in (
            "__ACCEPT__",
            "__ACCEPT_JSON__",
            "__ACCEPT_LABEL__",
            "__MAX_MB__",
            "__MAX_BYTES__",
            "__VERSION__",
            "__AUTH__",
            "__EMAIL_DISCLOSURE__",
            "__TTL_HOURS__",
        ):
            assert token not in html, f"{token} was never substituted"

    def test_the_page_says_whether_access_is_controlled(self, client: TestClient) -> None:
        assert "<code>off</code>" in client.get("/").text

    def test_an_unconfigured_deployment_does_not_promise_email(
        self, client: TestClient
    ) -> None:
        """The mock hardcoded a recipient. A page must not claim a send that cannot happen."""
        html = client.get("/").text
        assert "Nothing is emailed" in html
        assert "@" not in html.split("disclosure")[1].split("</div>")[0]

    def test_a_configured_deployment_names_the_real_recipient(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
        import importlib

        import app as app_module

        module = importlib.reload(app_module)
        with TestClient(module.app) as configured:
            assert "Info@tencapital.group" in configured.get("/").text
