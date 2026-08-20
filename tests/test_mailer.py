"""Emailing results through Resend.

Nothing here touches the network. The two things that matter are asserted directly: the
Resend API key never appears in anything the code produces, and a send that fails does not
fail the run it was reporting on.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from deckpager import mailer
from deckpager.config import Settings
from deckpager.mailer import (
    EmailOutcome,
    build_html,
    build_payload,
    build_text,
    describe,
    send,
    subject_for,
)
from deckpager.models import OnePager
from deckpager.pipeline import RunResult

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "re_do_not_print_me_0123456789"


@pytest.fixture
def one_pager() -> OnePager:
    return OnePager.model_validate(
        json.loads((FIXTURES / "sample_onepager.json").read_text(encoding="utf-8"))
    )


@pytest.fixture
def result(one_pager: OnePager, tmp_path: Path) -> RunResult:
    pdf = tmp_path / "Helion_Bio-onepager.pdf"
    js = tmp_path / "Helion_Bio-onepager.json"
    pdf.write_bytes(b"%PDF-1.4 pretend this is the one-pager")
    js.write_text(one_pager.model_dump_json(), encoding="utf-8")
    return RunResult(
        one_pager=one_pager,
        pdf=pdf,
        json=js,
        seconds=8.2,
        truncations=["market note truncated"],
    )


@pytest.fixture
def enabled() -> Settings:
    return Settings(
        anthropic_api_key="sk-ant-test",
        resend_api_key=SECRET,
        report_email_to="Info@tencapital.group",
        report_email_from="deckpager@tencapital.group",
    )


@pytest.fixture
def disabled() -> Settings:
    return Settings(anthropic_api_key="sk-ant-test", resend_api_key=None)


class TestSwitch:
    def test_it_is_off_without_a_key(self, disabled: Settings) -> None:
        """Sending mail is opt-in. An unconfigured deployment must not try."""
        assert disabled.email_enabled is False
        assert "off" in describe(disabled)

    def test_a_key_turns_it_on(self, enabled: Settings) -> None:
        assert enabled.email_enabled is True
        assert "Info@tencapital.group" in describe(enabled)

    def test_describing_it_never_prints_the_key(self, enabled: Settings) -> None:
        assert SECRET not in describe(enabled)

    def test_it_is_off_with_a_key_but_no_recipient(self) -> None:
        settings = Settings(
            anthropic_api_key="sk-ant-test", resend_api_key=SECRET, report_email_to=""
        )
        assert settings.email_enabled is False

    def test_sending_while_disabled_is_a_skip_not_an_error(
        self, one_pager: OnePager, result: RunResult, disabled: Settings
    ) -> None:
        outcome = send(one_pager, result, disabled)
        assert outcome.sent is False
        assert "RESEND_API_KEY" in outcome.detail


class TestMessage:
    def test_the_subject_names_the_company(self, one_pager: OnePager) -> None:
        assert subject_for(one_pager) == "Helion Bio — TEN Capital one-pager"

    def test_the_subject_falls_back_to_the_deck_name(self, one_pager: OnePager) -> None:
        one_pager.company_name.value = None
        assert "helion_bio_seed_deck" in subject_for(one_pager)

    def test_the_body_carries_the_analyst_layer(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        html = build_html(one_pager, result, 0.6)
        for item in (one_pager.key_strengths.value or []) + (one_pager.key_risks.value or []):
            assert item in html

    def test_the_body_labels_the_analysis_as_generated(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        """The same rule as the page: it must never read as something the founders wrote."""
        assert "AI-generated" in build_html(one_pager, result, 0.6)
        assert "not the deck" in build_text(one_pager, result)

    def test_the_body_reports_what_was_truncated(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        assert "market note truncated" in build_html(one_pager, result, 0.6)

    def test_the_body_reports_flagged_fields(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        html = build_html(one_pager, result, 0.6)
        assert "below 60% confidence" in html

    def test_a_clean_run_says_so_rather_than_showing_an_empty_box(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        for name in type(one_pager).model_fields:
            field = getattr(one_pager, name, None)
            if hasattr(field, "confidence"):
                field.confidence = 0.99
        result.truncations = []
        assert "No caveats" in build_html(one_pager, result, 0.6)

    def test_company_names_are_escaped(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        """A company name is model output landing in HTML. It gets escaped."""
        one_pager.company_name.value = "<script>alert(1)</script>"
        html = build_html(one_pager, result, 0.6)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_the_text_alternative_is_readable_without_html(
        self, one_pager: OnePager, result: RunResult
    ) -> None:
        text = build_text(one_pager, result)
        assert "Helion Bio" in text
        assert "STRENGTHS" in text
        assert "<" not in text


class TestPayload:
    def test_the_pdf_is_attached(
        self, one_pager: OnePager, result: RunResult, enabled: Settings
    ) -> None:
        payload = build_payload(one_pager, result, enabled)
        names = [a["filename"] for a in payload["attachments"]]
        assert result.pdf.name in names

    def test_the_json_attachment_can_be_turned_off(
        self, one_pager: OnePager, result: RunResult, enabled: Settings
    ) -> None:
        without = enabled.model_copy(update={"email_attach_json": False})
        payload = build_payload(one_pager, result, without)
        assert [a["filename"] for a in payload["attachments"]] == [result.pdf.name]

    def test_a_missing_artifact_is_skipped_rather_than_crashing(
        self, one_pager: OnePager, result: RunResult, enabled: Settings
    ) -> None:
        result.pdf.unlink()
        payload = build_payload(one_pager, result, enabled)
        assert result.pdf.name not in [a["filename"] for a in payload["attachments"]]

    def test_an_oversized_attachment_is_dropped(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(mailer, "MAX_ATTACHMENT_BYTES", 4)
        assert build_payload(one_pager, result, enabled)["attachments"] == []

    def test_several_recipients_are_split(
        self, one_pager: OnePager, result: RunResult, enabled: Settings
    ) -> None:
        many = enabled.model_copy(
            update={"report_email_to": "Info@tencapital.group, hall@tencapital.group"}
        )
        payload = build_payload(one_pager, result, many)
        assert payload["to"] == ["Info@tencapital.group", "hall@tencapital.group"]

    def test_the_payload_never_contains_the_key(
        self, one_pager: OnePager, result: RunResult, enabled: Settings
    ) -> None:
        """The key belongs in the Authorization header and nowhere else."""
        assert SECRET not in json.dumps(build_payload(one_pager, result, enabled))


class TestSending:
    def _stub(
        self, monkeypatch: pytest.MonkeyPatch, response: Any = None, error: Exception | None = None
    ) -> list[Any]:
        """Replace urlopen, capturing the request the mailer built."""
        captured: list[Any] = []

        class Response:
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(response or {"id": "msg_123"}).encode()

        def fake_urlopen(request: Any, timeout: float = 0) -> Response:
            captured.append(request)
            if error is not None:
                raise error
            return Response()

        monkeypatch.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_a_successful_send_reports_the_message_id(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._stub(monkeypatch)
        outcome = send(one_pager, result, enabled)
        assert outcome.sent is True
        assert outcome.message_id == "msg_123"
        assert "Info@tencapital.group" in outcome.detail

    def test_the_key_travels_in_the_authorization_header(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured = self._stub(monkeypatch)
        send(one_pager, result, enabled)
        assert captured[0].headers["Authorization"] == f"Bearer {SECRET}"
        assert captured[0].full_url == mailer.RESEND_ENDPOINT

    def test_a_rejection_explains_itself_without_raising(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unverified sending domain is reported here and nowhere else."""
        import io

        error = urllib.error.HTTPError(
            mailer.RESEND_ENDPOINT,
            403,
            "Forbidden",
            {},  # type: ignore[arg-type]
            io.BytesIO(json.dumps({"message": "The tencapital.group domain is not verified."}).encode()),
        )
        self._stub(monkeypatch, error=error)
        outcome = send(one_pager, result, enabled)
        assert outcome.sent is False
        assert "not verified" in outcome.detail
        assert SECRET not in outcome.detail

    def test_an_unreachable_service_is_reported_not_raised(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._stub(monkeypatch, error=urllib.error.URLError("no route to host"))
        outcome = send(one_pager, result, enabled)
        assert outcome.sent is False
        assert "Could not reach Resend" in outcome.detail

    def test_a_timeout_is_reported_not_raised(
        self,
        one_pager: OnePager,
        result: RunResult,
        enabled: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._stub(monkeypatch, error=TimeoutError("timed out"))
        assert send(one_pager, result, enabled).sent is False


class TestNeverFailsTheRun:
    """The whole design constraint, asserted end to end."""

    def test_a_mailer_that_raises_does_not_break_a_run(
        self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mailer.send promises never to raise. This asserts the pipeline does not
        depend on that promise being kept.
        """
        import json as json_lib

        from deckpager.cache import ExtractionCache
        from deckpager.extract.client import FakeExtractor
        from deckpager.pipeline import run

        def explode(*_a: object, **_k: object) -> EmailOutcome:
            raise RuntimeError("Resend fell over in a way nobody predicted")

        monkeypatch.setattr(mailer, "send", explode)

        payload = json_lib.loads(
            (FIXTURES / "sample_onepager.json").read_text(encoding="utf-8")
        )
        payload.pop("provenance")

        result = run(
            sample_pdf,
            settings=Settings(
                anthropic_api_key="sk-ant-test",
                resend_api_key=SECRET,
                report_email_to="Info@tencapital.group",
            ),
            out_dir=tmp_path,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )

        assert result.pdf.is_file()
        assert result.json.is_file()
        assert result.email is not None
        assert result.email.sent is False
        assert "RuntimeError" in result.email.detail
    def test_a_failed_send_still_leaves_the_artifacts(
        self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json as json_lib

        from deckpager.cache import ExtractionCache
        from deckpager.extract.client import FakeExtractor
        from deckpager.pipeline import run

        monkeypatch.setattr(
            mailer.urllib.request,
            "urlopen",
            lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("down")),
        )
        payload = json_lib.loads(
            (FIXTURES / "sample_onepager.json").read_text(encoding="utf-8")
        )
        payload.pop("provenance")

        result = run(
            sample_pdf,
            settings=Settings(
                anthropic_api_key="sk-ant-test",
                resend_api_key=SECRET,
                report_email_to="Info@tencapital.group",
            ),
            out_dir=tmp_path,
            extractor=FakeExtractor(payload),
            cache=ExtractionCache(tmp_path / "cache"),
        )
        assert result.pdf.is_file()
        assert result.email is not None
        assert result.email.sent is False
