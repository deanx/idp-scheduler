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
