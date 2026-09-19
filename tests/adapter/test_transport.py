"""T-01.2.8 — raw-HTTP transport: bounded timeout, redaction, no raw
``urllib`` exception escapes (NFR N5/N23, ADR-0002 §Threat model)."""

from __future__ import annotations

import email.message
import http.client
import io
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPTransportError


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            return self._body
        return self._body[:amt]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_post_json_returns_status_and_decoded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        assert req.get_header("Content-type") == "application/json"
        return _FakeResponse(200, json.dumps({"ok": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {"a": 1}, timeout_seconds=5.0)
    assert status == 200
    assert body == {"ok": True}


def test_get_json_sends_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str | None] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen["auth"] = req.get_header("Authorization")
        return _FakeResponse(200, json.dumps({"status": "RUNNING"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body = transport.get_json(
        "https://x/y", timeout_seconds=5.0, headers={"Authorization": "Bearer tok"}
    )
    assert status == 200
    assert body == {"status": "RUNNING"}
    assert seen["auth"] == "Bearer tok"


def test_post_multipart_file_uploads_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF-fake-bytes")
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured["data"] = req.data
        captured["content_type"] = req.get_header("Content-type")
        return _FakeResponse(202, json.dumps({"id": "exec-1"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_multipart_file(
        "https://x/executions", "file", str(doc), timeout_seconds=5.0
    )
    assert status == 202
    assert body == {"id": "exec-1"}
    assert b"%PDF-fake-bytes" in captured["data"]
    assert captured["content_type"].startswith("multipart/form-data; boundary=")


def test_http_error_is_returned_with_its_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "unauthorized",
            email.message.Message(),
            io.BytesIO(b'{"error":"bad creds"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body = transport.get_json("https://x/y", timeout_seconds=5.0)
    assert status == 401
    assert body == {"error": "bad creds"}


def test_timeout_raises_typed_transport_error_not_a_raw_urllib_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_url_error_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_crlf_in_header_value_raises_typed_error_without_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # /test Scenario B item 1: a CR/LF-carrying token makes urlopen() raise
    # a raw ValueError whose message embeds the token (as a bytes repr,
    # literal "\r\n", not real control chars — so it would survive the
    # Bearer-pattern regex too). Preferred fix: don't include the exception
    # text at all on this path (a static message), rather than trust a
    # regex to redact bytes-repr text reliably.
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise ValueError(
            "Invalid header value b'Bearer super-secret-token-123\\r\\nX-Evil: 1'"
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError) as excinfo:
        transport.get_json(
            "https://x/y", timeout_seconds=5.0, headers={"Authorization": "Bearer x"}
        )
    assert "super-secret-token-123" not in str(excinfo.value)
    assert "super-secret-token-123" not in repr(excinfo.value.__cause__)
    assert "super-secret-token-123" not in repr(excinfo.value.__context__)


def test_bearer_token_never_appears_in_a_redacted_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError(
            "connection reset while sending Authorization: Bearer super-secret-token-123"
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError) as excinfo:
        transport.get_json(
            "https://x/y",
            timeout_seconds=5.0,
            headers={"Authorization": "Bearer super-secret-token-123"},
        )
    assert "super-secret-token-123" not in str(excinfo.value)


def test_redact_strips_bearer_header_value() -> None:
    text = "request failed: Authorization: Bearer abc.def.ghi"
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


@pytest.mark.parametrize(
    "exc",
    [
        http.client.RemoteDisconnected("Remote end closed connection"),
        ConnectionResetError("connection reset by peer"),
        http.client.IncompleteRead(b"partial"),
        http.client.HTTPException("generic http exception"),
        OSError("network unreachable"),
    ],
)
def test_transport_level_exceptions_map_to_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_non_utf8_response_body_raises_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, b"\xff\xfe not valid utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_deeply_nested_json_body_raises_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # sys.setrecursionlimit-bound json.loads recursion on deeply nested
    # arrays raises RecursionError, never json.JSONDecodeError.
    nested = "[" * 100_000 + "]" * 100_000

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, nested.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_huge_integer_json_body_raises_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Python's int-string conversion limit turns a huge integer literal in
    # a JSON body into a raw ValueError from json.loads, not JSONDecodeError.
    huge_int_body = ("9" * 10_000).encode()

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, huge_int_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_missing_local_file_raises_typed_error_without_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.pdf"
    with pytest.raises(IDPTransportError) as excinfo:
        transport.post_multipart_file(
            "https://x/executions", "file", str(missing), timeout_seconds=5.0
        )
    assert str(missing) not in str(excinfo.value)
    assert "does-not-exist.pdf" not in str(excinfo.value)


def test_embedded_null_byte_in_document_path_raises_typed_error_without_the_path() -> None:
    # /test Scenario B item 2: open() raises a raw ValueError ("embedded
    # null byte"), not caught by the old `except OSError`, and would carry
    # the path in its message.
    evil_path = "/tmp/invoice\x00.pdf"
    with pytest.raises(IDPTransportError) as excinfo:
        transport.post_multipart_file(
            "https://x/executions", "file", evil_path, timeout_seconds=5.0
        )
    assert evil_path not in str(excinfo.value)
    assert "invoice" not in str(excinfo.value)


def test_multipart_filename_escapes_quotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / 'evil".pdf'
    doc.write_bytes(b"%PDF")
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured["data"] = req.data
        return _FakeResponse(202, json.dumps({"id": "exec-1"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport.post_multipart_file("https://x/executions", "file", str(doc), timeout_seconds=5.0)
    body: bytes = captured["data"]
    assert b'filename="evil\\".pdf"' in body


def test_escape_multipart_filename_strips_crlf_injection() -> None:
    escaped = transport._escape_multipart_filename("evil.pdf\r\nX-Injected: true")
    assert "\r" not in escaped
    assert "\n" not in escaped


def test_escape_multipart_filename_escapes_quotes_and_backslashes() -> None:
    escaped = transport._escape_multipart_filename('a"b\\c')
    assert escaped == 'a\\"b\\\\c'


def test_transport_error_log_line_is_sanitized_against_injection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A quote/newline-shaped exception message must not split or forge a
    # log field — kills the "log the raw message unsanitized" mutant
    # (transport.py:123, Atchim R8).
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError('x" injected="1\nforged_field=evil')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.ERROR), pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)
    assert len(caplog.records) == 1
    rendered = caplog.records[0].getMessage()
    # No raw newline reached the rendered log line (json.dumps escapes it).
    assert "\n" not in rendered
    # The embedded quote/newline is escaped inline, not split into a forged
    # second field.
    assert '\\" injected=\\"1\\nforged_field=evil' in rendered


def test_empty_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(204, b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {}, timeout_seconds=5.0)
    assert status == 204
    assert body is None
