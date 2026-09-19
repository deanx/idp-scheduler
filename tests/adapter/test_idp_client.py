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
    IDPAmbiguousStatusError,
    IDPAuthenticationError,
    IDPExecutionFailedError,
    IDPPollHardFailureError,
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


def test_poll_missing_status_key_aborts_immediately_does_not_poll_to_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ADR-0004 #17: a missing/null/non-string status must abort immediately,
    # never be treated as "unknown, keep polling" (Atchim R4).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(200, {"pages": []})] * 5,  # no "status" key at all
        poll_timeout_seconds=3.0,
        clock=_clock_from([0.0, 0.0, 1.0]),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(IDPAmbiguousStatusError):
        adapter.extract(str(doc), "action-1", "v1")


@pytest.mark.parametrize("bad_status", [None, 42, "", []])
def test_poll_null_or_non_string_status_aborts_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_status: object
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(200, {"status": bad_status, "pages": []})],
        poll_timeout_seconds=3.0,
        clock=_clock_from([0.0, 0.0, 1.0]),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(IDPAmbiguousStatusError):
        adapter.extract(str(doc), "action-1", "v1")


def test_poll_unknown_but_present_status_keeps_polling_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An unknown status (present, non-empty, but not in either allowlist)
    # is NOT ambiguous — ADR-0004 #17 says keep polling, it may not be
    # terminal yet.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[
            (200, {"status": "SOME_UNKNOWN_STATUS", "pages": []}),
            (200, {"status": "SUCCEEDED", "pages": []}),
        ],
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"


@pytest.mark.parametrize("http_status", [401, 403])
def test_poll_401_and_403_both_raise_auth_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(http_status, {"error": "expired"})],
        clock=_clock_from([0.0, 0.0, 0.5]),
    )
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")


@pytest.mark.parametrize("http_status", [404, 400, 500, 503])
def test_poll_other_non_2xx_raises_hard_failure_immediately_not_polled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    # ADR-0004 #5: a 404/400/500/etc on the poll request is a hard failure,
    # aborted immediately — never kept-polling, and the body's "status" (if
    # any) is never read as the execution status (Atchim R5).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def counting_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        calls.append(1)
        return http_status, {"status": "SUCCEEDED", "error": "not found"}

    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 0.5]))
    monkeypatch.setattr(transport, "get_json", counting_get_json)
    with pytest.raises(IDPPollHardFailureError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert excinfo.value.http_status == http_status
    assert len(calls) == 1  # aborted immediately, not polled again


def test_success_statuses_must_be_a_subset_of_terminal_statuses() -> None:
    with pytest.raises(ValueError, match="subset"):
        MuleSoftIDPAdapter(
            client_id="cid",
            client_secret="csecret",
            region="us-east-2",
            org_id="org-1",
            terminal_statuses={"SUCCEEDED"},
            success_statuses={"SUCCEEDED", "DONE"},  # DONE not in terminal_statuses
        )


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


def test_default_clock_and_sleep_are_time_monotonic_and_time_sleep_not_time_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Kills the "default clock/sleep silently falls back to time.time"
    # mutant (Atchim R8): construct the adapter (and TokenCache inside it)
    # WITHOUT passing clock/sleep — the real defaults must be exercised,
    # and time.time() must explode if consulted.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def boom() -> float:
        raise AssertionError("time.time() must never be the default clock (INV-07)")

    monkeypatch.setattr("time.time", boom)
    monkeypatch.setattr(
        transport,
        "post_json",
        lambda *a, **kw: (200, {"access_token": "tok-1", "expires_in": 300}),
    )
    monkeypatch.setattr(
        transport, "post_multipart_file", lambda *a, **kw: (202, {"id": "exec-1"})
    )
    monkeypatch.setattr(
        transport, "get_json", lambda *a, **kw: (200, {"status": "SUCCEEDED", "pages": []})
    )
    adapter = MuleSoftIDPAdapter(
        client_id="cid",
        client_secret="csecret",
        region="us-east-2",
        org_id="org-1",
        terminal_statuses={"SUCCEEDED"},
        success_statuses={"SUCCEEDED"},
        poll_interval_seconds=0.01,
    )
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"


def test_token_cache_default_clock_is_time_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    from idp_regression.adapter.token_cache import TokenCache

    def boom() -> float:
        raise AssertionError("time.time() must never be TokenCache's default clock (INV-07)")

    monkeypatch.setattr("time.time", boom)
    cache = TokenCache(fetch=lambda: ("tok-1", 300.0), refresh_margin_seconds=60.0)
    assert cache.get() == "tok-1"


def test_secrets_never_appear_in_a_submit_failure_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def failing_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        # Simulates a defense-in-depth scenario: even if some upstream
        # exception carried a raw secret (a transport bug, a third-party
        # lib, ...), _submit must not propagate it via the log OR the
        # exception chain (Atchim R8).
        raise IDPTransportError(
            "POST .../executions failed: connection reset while sending "
            "Authorization: Bearer tok-1"
        )

    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(transport, "post_multipart_file", failing_submit)
    with caplog.at_level(logging.ERROR), pytest.raises(IDPSubmitError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert "tok-1" not in caplog.text
    assert "csecret" not in caplog.text
    assert "tok-1" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert "tok-1" not in repr(excinfo.value.__cause__)
    assert excinfo.value.__context__ is None
    assert "tok-1" not in repr(excinfo.value.__context__)
