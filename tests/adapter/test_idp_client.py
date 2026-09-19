"""T-01.2.1/.2.3/.2.7 — MuleSoftIDPAdapter: submit (not retried), poll with
a configurable terminal-status allowlist, monotonic-clock budget math, a
per-document timing metric (INV-07, NFR N1/BR9)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import (
    IDPAuthenticationError,
    IDPExecutionFailedError,
    IDPPollTimeoutError,
    IDPSubmitError,
    IDPTransportError,
)
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter


def _clock_from(seq: list[float]) -> Callable[[], float]:
    """Returns each value in ``seq`` in order, then repeats the last value
    forever (so a test only needs to pin the values it cares about, not the
    exact total call count)."""
    it = iter(seq)
    last = seq[-1]

    def clock() -> float:
        nonlocal last
        with contextlib.suppress(StopIteration):
            last = next(it)
        return last

    return clock


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetch_token_result: tuple[int, dict[str, Any]] = (
        200,
        {"access_token": "tok-1", "expires_in": 300},
    ),
    submit_result: tuple[int, dict[str, Any]] = (202, {"id": "exec-1"}),
    poll_results: list[tuple[int, dict[str, Any]]] | None = None,
    poll_timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    terminal_statuses: set[str] | None = None,
    success_statuses: set[str] | None = None,
) -> MuleSoftIDPAdapter:
    monkeypatch.setattr(
        transport, "post_json", lambda *a, **kw: fetch_token_result  # noqa: ARG005
    )
    monkeypatch.setattr(
        transport, "post_multipart_file", lambda *a, **kw: submit_result  # noqa: ARG005
    )

    results = poll_results or [(200, {"status": "SUCCEEDED", "pages": []})]
    poll_iter = iter(results)
    last_poll_result = results[-1]

    def fake_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        nonlocal last_poll_result
        with contextlib.suppress(StopIteration):
            last_poll_result = next(poll_iter)
        return last_poll_result

    monkeypatch.setattr(transport, "get_json", fake_get_json)

    sleeps: list[float] = []
    return MuleSoftIDPAdapter(
        client_id="cid",
        client_secret="csecret",
        region="us-east-2",
        org_id="org-1",
        terminal_statuses=(
            terminal_statuses if terminal_statuses is not None else {"SUCCEEDED", "FAILED"}
        ),
        success_statuses=success_statuses if success_statuses is not None else {"SUCCEEDED"},
        submit_timeout_seconds=30.0,
        poll_timeout_seconds=poll_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        clock=clock or (lambda: 0.0),
        sleep=sleep or sleeps.append,
    )


def test_extract_happy_path_returns_normalized_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[
            (
                200,
                {
                    "status": "SUCCEEDED",
                    "pages": [{"fields": {"total": {"value": "1", "confidence": 0.9}}}],
                },
            )
        ],
        clock=_clock_from([0.0, 0.0, 0.5, 1.0]),
    )
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    assert out["fields"]["total"]["value"] == "1"


def test_extract_logs_a_per_document_timing_metric(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 0.5, 1.0]))
    with caplog.at_level(logging.INFO):
        adapter.extract(str(doc), "action-1", "v1")
    assert any("idp_extraction_timing" in r.message for r in caplog.records)


def test_auth_failure_at_run_start_is_fail_closed_no_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def failing_post_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        calls.append(1)
        return 401, {"error": "invalid_client"}

    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(transport, "post_json", failing_post_json)
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")
    assert len(calls) == 1  # no retry


def test_submit_transport_failure_raises_typed_error_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def failing_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        calls.append(1)
        raise IDPTransportError("submit POST timed out")

    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(transport, "post_multipart_file", failing_submit)
    with pytest.raises(IDPSubmitError):
        adapter.extract(str(doc), "action-1", "v1")
    assert len(calls) == 1  # not retried (non-idempotent POST, ADR-0004 #4)


def test_submit_rejected_status_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(monkeypatch, submit_result=(400, {"error": "bad request"}))
    with pytest.raises(IDPSubmitError):
        adapter.extract(str(doc), "action-1", "v1")


def test_poll_returns_on_configured_success_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        terminal_statuses={"DONE"},
        success_statuses={"DONE"},
        poll_results=[(200, {"status": "DONE", "pages": []})],
        clock=_clock_from([0.0, 0.0, 0.5, 1.0]),
    )
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "DONE"


def test_poll_never_hard_codes_succeeded_and_honours_the_configured_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A status that would be success-shaped is rejected because it is NOT
    # in the configured success set (BR9: never a literal comparison).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        terminal_statuses={"SUCCEEDED"},
        success_statuses=set(),  # nothing counts as success
        poll_results=[(200, {"status": "SUCCEEDED", "pages": []})],
        clock=_clock_from([0.0, 0.0, 0.5, 1.0]),
    )
    with pytest.raises(IDPExecutionFailedError):
        adapter.extract(str(doc), "action-1", "v1")


def test_poll_keeps_polling_on_non_terminal_status_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[
            (200, {"status": "RUNNING", "pages": []}),
            (200, {"status": "SUCCEEDED", "pages": []}),
        ],
        # extract: clock() for start, TokenCache._refresh clock(), then poll loop
        # calls clock() each iteration for the deadline check (2 iterations).
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"


def test_poll_timeout_raises_typed_error_with_last_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    # deadline = start(0.0) + poll_timeout(5.0) = 5.0; clock ticks past it.
    adapter = _adapter(
        monkeypatch,
        poll_results=[(200, {"status": "RUNNING", "pages": []})] * 5,
        poll_timeout_seconds=5.0,
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(IDPPollTimeoutError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert excinfo.value.last_status == "RUNNING"


def test_poll_ambiguous_missing_status_does_not_infer_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(200, {"pages": []})] * 5,  # no "status" key at all
        poll_timeout_seconds=3.0,
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0, 4.0]),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(IDPPollTimeoutError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert excinfo.value.last_status is None


def test_poll_mid_run_401_raises_auth_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(401, {"error": "expired"})],
        clock=_clock_from([0.0, 0.0, 0.5]),
    )
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")


def test_uses_monotonic_clock_never_time_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def boom() -> float:
        raise AssertionError("time.time() must never be consulted for budget math (INV-07)")

    monkeypatch.setattr("time.time", boom)
    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 0.5, 1.0]))
    adapter.extract(str(doc), "action-1", "v1")


def test_secrets_never_appear_in_a_submit_failure_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def failing_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        raise IDPTransportError(
            "POST .../executions failed: connection reset while sending "
            "Authorization: Bearer tok-1"
        )

    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(transport, "post_multipart_file", failing_submit)
    with caplog.at_level(logging.ERROR), pytest.raises(IDPSubmitError):
        adapter.extract(str(doc), "action-1", "v1")
    assert "tok-1" not in caplog.text
    assert "csecret" not in caplog.text
