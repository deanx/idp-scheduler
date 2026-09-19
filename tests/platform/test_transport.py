"""transport.py — timeout (R7) + redact() wiring (R4/TP-44)."""

from __future__ import annotations

import logging

import pytest

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

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _raise_timeout)

    with pytest.raises(TransportError):
        client.request("GET", "/api/public/v2/datasets/x")


def test_url_error_raises_typed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = UrllibHttpClient("http://localhost:1", "pub", "sec")

    def _raise_url_error(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _raise_url_error)

    with pytest.raises(TransportError):
        client.request("GET", "/api/public/v2/datasets/x")


def test_socket_timeout_message_never_leaks_the_auth_header(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    client = UrllibHttpClient("http://localhost:1", "pub", "topsecret")

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _raise_timeout)

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

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _raise_timeout)

    with pytest.raises(TransportError):
        client.request("GET", malicious_path)

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"
