"""T-01.2.8 — raw-HTTP transport: bounded timeout, redaction, no raw
``urllib`` exception escapes (NFR N5/N23, ADR-0002 §Threat model)."""

from __future__ import annotations

import email.message
import http.client
import http.server
import io
import json
import logging
import threading
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {"a": 1}, timeout_seconds=5.0)
    assert status == 200
    assert body == {"ok": True}


def test_get_json_sends_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str | None] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen["auth"] = req.get_header("Authorization")
        return _FakeResponse(200, json.dumps({"status": "RUNNING"}).encode())

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    status, body = transport.get_json("https://x/y", timeout_seconds=5.0)
    assert status == 401
    assert body == {"error": "bad creds"}


def test_timeout_raises_typed_transport_error_not_a_raw_urllib_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_url_error_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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


def test_redact_strips_form_encoded_client_secret() -> None:
    text = "grant_type=client_credentials&client_id=c1&client_secret=topsecret&x=1"
    redacted = transport.redact(text)
    assert "topsecret" not in redacted


def test_redact_strips_form_encoded_access_token() -> None:
    text = "response body: access_token=abc.def.ghi&token_type=bearer"
    redacted = transport.redact(text)
    assert "abc.def.ghi" not in redacted


def test_redact_strips_json_access_token_field() -> None:
    text = '{"access_token":"abc.def.ghi","expires_in":300}'
    redacted = transport.redact(text)
    assert "abc.def.ghi" not in redacted


def test_redact_strips_client_secret_with_escaped_quote_inside_the_value() -> None:
    # A naive `[^"]*` value pattern stops at the first embedded `"`, even
    # when it's backslash-escaped, leaking the remainder of the secret.
    text = '{"client_secret":"a\\"bsecret"}'
    redacted = transport.redact(text)
    assert "bsecret" not in redacted


def test_redact_strips_access_token_with_escaped_quote_inside_the_value() -> None:
    text = '{"access_token":"a\\"bsecret"}'
    redacted = transport.redact(text)
    assert "bsecret" not in redacted


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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)


def test_non_utf8_response_body_raises_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, b"\xff\xfe not valid utf-8")

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
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

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    with caplog.at_level(logging.ERROR), pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)
    assert len(caplog.records) == 1
    rendered = caplog.records[0].getMessage()
    # No raw newline reached the rendered log line (json.dumps escapes it).
    assert "\n" not in rendered
    # The embedded quote/newline is escaped inline, not split into a forged
    # second field.
    assert '\\" injected=\\"1\\nforged_field=evil' in rendered


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """A minimal local HTTP server recording the Authorization header of
    every request it receives, answering with a fixed status (QA F-2)."""

    received_auth_headers: list[str | None] = []
    response_status = 200

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
        self.received_auth_headers.append(self.headers.get("Authorization"))
        self.send_response(self.response_status)
        if self.response_status in (301, 302, 303, 307, 308):
            self.send_header("Location", self.redirect_location)  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:  # silence stderr noise
        return


def _start_server(
    *, response_status: int = 200, redirect_location: str = ""
) -> http.server.HTTPServer:
    handler_cls = type(
        "_Handler",
        (_RecordingHandler,),
        {
            "received_auth_headers": [],
            "response_status": response_status,
            "redirect_location": redirect_location,
        },
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class _TrackedCloseBytesIO(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.closed_count = 0

    def close(self) -> None:
        self.closed_count += 1
        super().close()


def test_3xx_http_error_response_is_closed_before_the_typed_error_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Atchim suggestion: the unread 3xx HTTPError's underlying fp must be
    # closed (releases the socket/file) rather than left dangling since
    # its body is intentionally never read.
    fp = _TrackedCloseBytesIO(b"")

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(
            req.full_url, 302, "Found", email.message.Message(), fp
        )

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.get_json("https://x/y", timeout_seconds=5.0)
    assert fp.closed_count == 1


def test_redirect_response_raises_typed_error_and_the_token_never_reaches_the_target() -> None:
    # Two REAL local servers (not mocks) — server B would receive the
    # Bearer token if the real urllib opener followed the 302 from server
    # A. QA F-2: it must not.
    server_b = _start_server(response_status=200)
    try:
        port_b = server_b.server_address[1]
        server_a = _start_server(
            response_status=302,
            redirect_location=f"http://127.0.0.1:{port_b}/other",
        )
        try:
            port_a = server_a.server_address[1]
            with pytest.raises(IDPTransportError) as excinfo:
                transport.get_json(
                    f"http://127.0.0.1:{port_a}/x",
                    timeout_seconds=5.0,
                    headers={"Authorization": "Bearer redirect-test-secret-xyz"},
                )
            assert "redirect-test-secret-xyz" not in str(excinfo.value)
            assert "redirect-test-secret-xyz" not in repr(excinfo.value.__cause__)
            assert "redirect-test-secret-xyz" not in repr(excinfo.value.__context__)
            handler_a_cls = server_a.RequestHandlerClass
            assert handler_a_cls.received_auth_headers == [  # type: ignore[attr-defined]
                "Bearer redirect-test-secret-xyz"
            ]
        finally:
            server_a.shutdown()
            server_a.server_close()
    finally:
        server_b.shutdown()
        server_b.server_close()
    handler_b_cls = server_b.RequestHandlerClass
    assert handler_b_cls.received_auth_headers == []  # type: ignore[attr-defined]


def test_empty_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeResponse:
        return _FakeResponse(204, b"")

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    status, body = transport.post_json("https://x/y", {}, timeout_seconds=5.0)
    assert status == 204
    assert body is None
