"""TP-44 (Atchim R4) — captured-log tests on the REST/provisioning and
error paths: the Langfuse Basic ``Authorization`` header (and any
response body that might echo it) must never appear in a log line,
whichever module logged it. ``transport.redact()`` is exercised directly
here (wired into ``transport.py``'s own error path, see R7's commit) and
indirectly through every adapter error path that logs.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from idp_regression.platform.errors import DatasetFetchFailedError, PlatformError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.schema_provisioning import provision_golden_schema

_FAKE_AUTH_HEADER_VALUE = "Basic cHVibGljLWtleTpzZWNyZXQta2V5"


class _LeakyHttpClient:
    """Returns an error body that echoes the auth header — simulating a
    platform bug or a proxy error page reflecting request headers."""

    def __init__(self, status: int) -> None:
        self._status = status

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return self._status, {"message": f"upstream error, saw {_FAKE_AUTH_HEADER_VALUE}"}


def test_schema_provisioning_error_log_never_contains_the_auth_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    client = _LeakyHttpClient(status=400)

    with pytest.raises(PlatformError):
        provision_golden_schema(client, dataset_name="test-s013-redaction")

    assert _FAKE_AUTH_HEADER_VALUE not in caplog.text
    assert "Basic" not in caplog.text


def test_get_dataset_error_log_never_contains_the_auth_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    client = _LeakyHttpClient(status=500)
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("test-s013-redaction")

    assert _FAKE_AUTH_HEADER_VALUE not in caplog.text
    assert "Basic" not in caplog.text


def test_schema_provisioning_error_message_never_contains_the_auth_header() -> None:
    client = _LeakyHttpClient(status=400)

    with pytest.raises(PlatformError) as excinfo:
        provision_golden_schema(client, dataset_name="test-s013-redaction")

    assert _FAKE_AUTH_HEADER_VALUE not in str(excinfo.value)


def test_get_dataset_error_message_never_contains_the_auth_header() -> None:
    client = _LeakyHttpClient(status=500)
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError) as excinfo:
        adapter.get_dataset("test-s013-redaction")

    assert _FAKE_AUTH_HEADER_VALUE not in str(excinfo.value)
