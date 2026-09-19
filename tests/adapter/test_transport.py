"""T-01.2.8 — raw-HTTP transport: bounded timeout, redaction, no raw
``urllib`` exception escapes (NFR N5/N23, ADR-0002 §Threat model)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPTransportError


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_post_json_returns_status_and_decoded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        assert req.get_header("Content-type") == "application/json"
        return _FakeResponse(200, json.dumps({"ok": True}).encode())

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {"a": 1}, timeout_seconds=5.0)
    assert status == 200
    assert body == {"ok": True}


def test_get_json_sends_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_urlopen(req, timeout):  # noqa: ANN001
        seen["auth"] = req.get_header("Authorization")
        return _FakeResponse(200, json.dumps({"status": "RUNNING"}).encode())

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    status, body = transport.get_json(
        "https://x/y", timeout_seconds=5.0, headers={"Authorization": "Bearer tok"}
    )
    assert status == 200
    assert body == {"status": "RUNNING"}
    assert seen["auth"] == "Bearer tok"


def test_post_multipart_file_uploads_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF-fake-bytes")
    captured = {}

    def fake_urlopen(req, timeout):  # noqa: ANN001
        captured["data"] = req.data
        captured["content_type"] = req.get_header("Content-type")
        return _FakeResponse(202, json.dumps({"id": "exec-1"}).encode())

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_multipart_file(
        "https://x/executions", "file", str(doc), timeout_seconds=5.0
    )
    assert status == 202
    assert body == {"id": "exec-1"}
    assert b"%PDF-fake-bytes" in captured["data"]
    assert captured["content_type"].startswith("multipart/form-data; boundary=")


def test_http_error_is_returned_with_its_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad creds"}')
        )

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    status, body = transport.get_json("https://x/y", timeout_seconds=5.0)
    assert status == 401
    assert body == {"error": "bad creds"}


def test_timeout_raises_typed_transport_error_not_a_raw_urllib_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        raise TimeoutError("timed out")

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_url_error_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_bearer_token_never_appears_in_a_redacted_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        raise urllib.error.URLError(
            "connection reset while sending Authorization: Bearer super-secret-token-123"
        )

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError) as excinfo:
        transport.get_json(
            "https://x/y", timeout_seconds=5.0, headers={"Authorization": "Bearer super-secret-token-123"}
        )
    assert "super-secret-token-123" not in str(excinfo.value)


def test_redact_strips_bearer_header_value() -> None:
    text = 'request failed: Authorization: Bearer abc.def.ghi'
    redacted = transport.redact(text)
    assert "abc.def.ghi" not in redacted


def test_redact_strips_client_secret_field() -> None:
    text = '{"client_id":"c1","client_secret":"topsecret"}'
    redacted = transport.redact(text)
    assert "topsecret" not in redacted


def test_sanitize_for_log_escapes_embedded_quotes_and_newlines() -> None:
    rendered = transport.sanitize_for_log('x" injected="1\nfield')
    assert "\n" not in rendered
    parsed = json.loads(rendered)
    assert parsed == 'x" injected="1\nfield'


def test_empty_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        return _FakeResponse(204, b"")

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {}, timeout_seconds=5.0)
    assert status == 204
    assert body is None
