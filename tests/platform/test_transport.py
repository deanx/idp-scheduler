"""transport.py — timeout (R7) + redact() wiring (R4/TP-44) + no-redirect
opener (DEBT-26, REG-07)."""

from __future__ import annotations

import http.server
import logging
import threading
import urllib.error

import pytest

from idp_regression.platform import transport
from idp_regression.platform.errors import TransportError
from idp_regression.platform.transport import UrllibHttpClient, redact


def test_redact_strips_a_basic_auth_header_value() -> None:
    text = "request failed: Authorization: Basic cHVibGljOnNlY3JldA=="
    assert "cHVibGljOnNlY3JldA==" not in redact(text)
    assert "Basic ***REDACTED***" in redact(text)


def test_client_has_a_configurable_bounded_timeout() -> None:
    client = UrllibHttpClient("http://localhost:1", "pub", "sec", timeout_seconds=5.0)
    assert client._timeout_seconds == 5.0  # noqa: SLF001


def test_client_defaults_to_a_bounded_timeout() -> None:
    client = UrllibHttpClient("http://localhost:1", "pub", "sec")
    assert 0 < client._timeout_seconds <= 60  # noqa: SLF001


def test_socket_timeout_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = UrllibHttpClient("http://localhost:1", "pub", "sec", timeout_seconds=0.01)

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(transport, "_urlopen", _raise_timeout)

    with pytest.raises(TransportError):
        client.request("GET", "/api/public/v2/datasets/x")


def test_url_error_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = UrllibHttpClient("http://localhost:1", "pub", "sec")

    def _raise_url_error(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(transport, "_urlopen", _raise_url_error)

    with pytest.raises(TransportError):
        client.request("GET", "/api/public/v2/datasets/x")


def test_socket_timeout_message_never_leaks_the_auth_header(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    client = UrllibHttpClient("http://localhost:1", "pub", "topsecret")

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(transport, "_urlopen", _raise_timeout)

    with pytest.raises(TransportError) as excinfo:
        client.request("GET", "/api/public/v2/datasets/x")

    assert "topsecret" not in str(excinfo.value)
    assert "topsecret" not in caplog.text


def test_no_source_reference_to_the_v4_trace_ingestion_endpoint() -> None:
    """Gap 8 (TP-38): scores go via /api/public/scores; traces go via
    OTLP (the langfuse SDK's own exporter, confined to make_platform()).
    /api/public/ingestion is the v4 "events_only" score-events-only
    ingestion path this codebase deliberately does NOT use for traces —
    static grep across the whole platform package that no source line
    ever references it (a change that started posting trace data there
    would be a real regression, not something this repo's own REST
    transport should ever need)."""
    import pathlib

    platform_dir = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression" / "platform"
    )
    offenders = [
        str(path)
        for path in platform_dir.rglob("*.py")
        if "/api/public/ingestion" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"unexpected /api/public/ingestion reference(s): {offenders}"


def test_transport_failed_error_log_survives_a_newline_in_the_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """N5 sibling (/test Scenario B follow-up): transport_failed logs
    method/path -- path can carry a caller-controlled dataset name when
    a higher layer builds it unencoded. Same fix (sanitize_for_log)."""
    caplog.set_level(logging.ERROR)
    client = UrllibHttpClient("http://localhost:1", "pub", "sec")
    malicious_path = '/api/public/v2/datasets/ds\ninjected fake log line status=200 dataset="ok"'

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(transport, "_urlopen", _raise_timeout)

    with pytest.raises(TransportError):
        client.request("GET", malicious_path)

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """A minimal local HTTP server recording the Authorization header of
    every request it receives, answering with a fixed status (DEBT-26,
    REG-07 -- same pattern as tests/adapter/test_transport.py)."""

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


def test_redirect_response_raises_typed_error_and_credential_never_reaches_the_target() -> None:
    """DEBT-26 / REG-07: two REAL local servers (not mocks) -- server B
    would receive the Basic auth header (Langfuse credentials) if the
    real urllib opener followed the 302 from server A. It must not."""
    server_b = _start_server(response_status=200)
    try:
        port_b = server_b.server_address[1]
        server_a = _start_server(
            response_status=302,
            redirect_location=f"http://127.0.0.1:{port_b}/other",
        )
        try:
            port_a = server_a.server_address[1]
            client = UrllibHttpClient(
                f"http://127.0.0.1:{port_a}", "pub", "redirect-test-secret-xyz"
            )
            with pytest.raises(TransportError) as excinfo:
                client.request("GET", "/x")
            assert "redirect-test-secret-xyz" not in str(excinfo.value)
            assert "redirect-test-secret-xyz" not in repr(excinfo.value.__cause__)
            assert "redirect-test-secret-xyz" not in repr(excinfo.value.__context__)
            handler_a_cls = server_a.RequestHandlerClass
            assert len(handler_a_cls.received_auth_headers) == 1  # type: ignore[attr-defined]
        finally:
            server_a.shutdown()
            server_a.server_close()
    finally:
        server_b.shutdown()
        server_b.server_close()
    handler_b_cls = server_b.RequestHandlerClass
    assert handler_b_cls.received_auth_headers == []  # type: ignore[attr-defined]
