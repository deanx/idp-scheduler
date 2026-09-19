"""T-01.2.1/.2.3/.2.7 — MuleSoftIDPAdapter: submit (not retried), poll with
a configurable terminal-status allowlist, monotonic-clock budget math, a
per-document timing metric (INV-07, NFR N1/BR9)."""

from __future__ import annotations

import contextlib
import inspect
import logging
import pathlib
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import (
    IDPAmbiguousStatusError,
    IDPAuthenticationError,
    IDPConfigurationError,
    IDPExecutionFailedError,
    IDPPollHardFailureError,
    IDPPollTimeoutError,
    IDPSubmitError,
    IDPTransportError,
)
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter


def _advancing_clock(step: float = 0.1) -> Callable[[], float]:
    """A fake clock that advances by ``step`` on every call (Atchim
    suggestion) — used as the default so a test that doesn't care about
    exact timing still can't hang on a frozen clock; only tests that pin
    specific deadline math use ``_clock_from`` instead."""
    state = {"now": 0.0}

    def clock() -> float:
        state["now"] += step
        return state["now"]

    return clock


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
        clock=clock or _advancing_clock(),
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


def test_two_extract_calls_reuse_the_cached_token_one_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 12: adapter-level (not just TokenCache-unit-
    # level) proof that two extract() calls share one token fetch.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    token_fetch_calls: list[int] = []

    def counting_post_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        token_fetch_calls.append(1)
        return 200, {"access_token": "tok-1", "expires_in": 300}

    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0]))
    monkeypatch.setattr(transport, "post_json", counting_post_json)
    adapter.extract(str(doc), "action-1", "v1")
    adapter.extract(str(doc), "action-1", "v1")
    assert len(token_fetch_calls) == 1


def test_poll_transport_error_keeps_polling_within_budget_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 13a: a transient poll-level transport error is
    # retried within the same poll budget (ADR-0004 #6, bounded by the
    # #2/#3 timeout — no separate abort code, the poll deadline itself is
    # the bound), not treated as an immediate hard failure.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    call_count = {"n": 0}

    def flaky_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IDPTransportError("transient connection reset")
        return 200, {"status": "SUCCEEDED", "pages": []}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    assert call_count["n"] == 2  # one transient failure, one success


def test_poll_transport_error_eventually_times_out_if_never_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def always_failing_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        raise IDPTransportError("connection reset")

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=3.0,
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0, 4.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json", always_failing_get_json)
    with pytest.raises(IDPPollTimeoutError):
        adapter.extract(str(doc), "action-1", "v1")


def test_poll_non_dict_body_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 13b.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        poll_results=[(200, "not a dict")],  # type: ignore[list-item]
        clock=_clock_from([0.0, 0.0, 0.5]),
    )
    with pytest.raises(IDPAmbiguousStatusError):
        adapter.extract(str(doc), "action-1", "v1")


def test_submit_response_missing_id_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 13c.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(monkeypatch, submit_result=(202, {"not_id": "exec-1"}))
    with pytest.raises(IDPSubmitError):
        adapter.extract(str(doc), "action-1", "v1")


def test_token_response_missing_access_token_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 13d.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch, fetch_token_result=(200, {"token_type": "bearer", "expires_in": 300})
    )
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")


def test_extracted_values_never_appear_in_logs_or_stdout_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # /test Scenario B item 10: the extract() happy path must never leak an
    # extracted field/table/prompt value into a log line or std{out,err} —
    # plant sentinel values (PII/financial-shaped) in the raw poll response
    # and assert none of them appear anywhere in captured output.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    sentinel_field = "SSN-SENTINEL-486-27-9310"
    sentinel_table_cell = "ACCT-SENTINEL-9981273"
    sentinel_prompt_answer = "SENTINEL-VENDOR-Umbrella-Corp"
    raw_success_body = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "fields": {"ssn": {"value": sentinel_field, "confidence": 0.9}},
                "tables": {
                    "line_items": [
                        {"account": {"value": sentinel_table_cell, "confidence": 0.9}}
                    ]
                },
                "prompts": [
                    {
                        "prompt": "vendor name?",
                        "answer": {"value": sentinel_prompt_answer, "confidence": 0.9},
                    }
                ],
            }
        ],
    }
    adapter = _adapter(monkeypatch, poll_results=[(200, raw_success_body)])
    with caplog.at_level(logging.DEBUG):
        out = adapter.extract(str(doc), "action-1", "v1")
    assert out["fields"]["ssn"]["value"] == sentinel_field  # sanity: it really extracted

    captured = capsys.readouterr()
    for sentinel in (sentinel_field, sentinel_table_cell, sentinel_prompt_answer):
        assert sentinel not in caplog.text
        assert sentinel not in captured.out
        assert sentinel not in captured.err


def test_timing_metric_elapsed_value_equals_the_fake_clock_delta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # /test Scenario B item 9: the log line existing isn't enough — the
    # logged elapsed_seconds value must equal clock()-at-end minus
    # clock()-at-start, not some other number (e.g. wall-clock time.time(),
    # or a constant, or the poll-only duration).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    # calls: token.get() now=0.0, token._refresh() expires_at=0.0,
    # extract() start=2.0, poll iter1 now=2.0 (succeeds immediately),
    # extract() end (elapsed calc)=9.5 -> elapsed = 9.5 - 2.0 = 7.5.
    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 2.0, 2.0, 9.5]))
    with caplog.at_level(logging.INFO):
        adapter.extract(str(doc), "action-1", "v1")
    timing_records = [r for r in caplog.records if "idp_extraction_timing" in r.message]
    assert len(timing_records) == 1
    match = re.search(r"elapsed_seconds=\"?(\d+\.\d+)\"?", timing_records[0].message)
    assert match, timing_records[0].message
    assert float(match.group(1)) == pytest.approx(7.5)


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


@pytest.mark.parametrize(
    "bad_expires_in",
    [float("nan"), float("inf"), float("-inf"), -1.0, 0.0, "not-a-number", 10**20],
)
def test_invalid_expires_in_raises_typed_auth_error_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_expires_in: object
) -> None:
    # /test Scenario B item 3: NaN/inf/negative/zero/non-numeric/huge must
    # never silently become expires_at = nan (or some other unusable
    # deadline) — fail-closed instead.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        fetch_token_result=(200, {"access_token": "tok-1", "expires_in": bad_expires_in}),
    )
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")


def test_nan_expires_in_does_not_produce_a_token_that_never_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A NaN expires_in must never silently produce expires_at = nan — every
    # future `now >= expires_at - margin` comparison would be False (NaN
    # comparisons are always False), so a bad token would be cached forever
    # and never refresh again. Since _fetch_token rejects NaN outright, no
    # token is ever cached from it — repeated extract() calls keep failing
    # fail-closed rather than silently succeeding on a permanently-stale token.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        fetch_token_result=(200, {"access_token": "tok-1", "expires_in": float("nan")}),
    )
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")
    with pytest.raises(IDPAuthenticationError):
        adapter.extract(str(doc), "action-1", "v1")


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


_BAD_TIMING_VALUES: list[object] = [
    float("nan"),
    float("inf"),
    float("-inf"),
    0.0,
    -1.0,
    "not-a-number",
]


def _adapter_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "client_id": "cid",
        "client_secret": "csecret",
        "region": "us-east-2",
        "org_id": "org-1",
        "terminal_statuses": {"SUCCEEDED"},
        "success_statuses": {"SUCCEEDED"},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("bad_value", _BAD_TIMING_VALUES)
def test_invalid_submit_timeout_raises_typed_config_error_at_construction(
    bad_value: object,
) -> None:
    # QA F-1: nan/inf/-inf/0/negative/non-numeric must never reach a real
    # timeout call (idp_client.py:66-67, :288-293) — validated at
    # construction, not discovered mid-poll.
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(submit_timeout_seconds=bad_value))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", _BAD_TIMING_VALUES)
def test_invalid_poll_timeout_raises_typed_config_error_at_construction(
    bad_value: object,
) -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(poll_timeout_seconds=bad_value))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", _BAD_TIMING_VALUES)
def test_invalid_poll_interval_raises_typed_config_error_at_construction(
    bad_value: object,
) -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(poll_interval_seconds=bad_value))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), "not-a-number"])
def test_invalid_token_refresh_margin_raises_typed_config_error_at_construction(
    bad_value: object,
) -> None:
    # The refresh margin may legitimately be 0 (refresh right at expiry) —
    # only nan/inf/non-numeric are rejected here, not 0.
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(token_refresh_margin_seconds=bad_value))  # type: ignore[arg-type]


def test_token_refresh_margin_of_zero_is_accepted() -> None:
    MuleSoftIDPAdapter(**_adapter_kwargs(token_refresh_margin_seconds=0.0))  # type: ignore[arg-type]


def test_negative_token_refresh_margin_raises_typed_config_error() -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(token_refresh_margin_seconds=-1.0))  # type: ignore[arg-type]


def test_absurdly_large_poll_timeout_raises_typed_config_error() -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(poll_timeout_seconds=1e20))  # type: ignore[arg-type]


def test_nan_poll_timeout_poll_terminates_instead_of_looping_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # QA F-1 regression: Zangado reproduced 1,000 polls over 20,040
    # simulated seconds with no timeout when IDP_EXECUTION_TIMEOUT_SECONDS
    # was nan/inf (`now >= deadline` is always False when deadline is
    # nan). Construction itself must reject it — this test kills a "remove
    # the isfinite check" mutant by asserting construction raises rather
    # than letting a broken adapter be built at all.
    with pytest.raises(IDPConfigurationError):
        _adapter(monkeypatch, poll_timeout_seconds=float("nan"))


def test_poll_budget_is_measured_from_before_submit_not_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /test Scenario B item 8: ADR-0004 says the poll-wall-clock budget
    # covers submit + all polls cumulatively — `start` must be captured
    # BEFORE `_submit()` runs. Kills a "move `start` after `_submit`"
    # mutant: simulate submit consuming 8 of a 10s budget (via 8 calls to
    # the shared injected clock from inside the mocked submit call) — the
    # correct code should then time out almost immediately (~1 get_json
    # call); if `start` were captured after submit, the budget would reset
    # to a fresh 10s and get_json would be called many more times.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    ticks = {"n": 0}

    def clock() -> float:
        ticks["n"] += 1
        return float(ticks["n"])

    def slow_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        for _ in range(8):
            clock()
        return 202, {"id": "exec-1"}

    get_json_calls: list[int] = []

    def fake_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        get_json_calls.append(1)
        return 200, {"status": "RUNNING", "pages": []}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        poll_interval_seconds=1.0,
        clock=clock,
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "post_multipart_file", slow_submit)
    monkeypatch.setattr(transport, "get_json", fake_get_json)
    with pytest.raises(IDPPollTimeoutError):
        adapter.extract(str(doc), "action-1", "v1")
    assert len(get_json_calls) <= 2, get_json_calls


def test_poll_clamps_per_get_timeout_and_sleep_to_remaining_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Kills both "remove clamp" mutants (idp_client.py per_call_timeout /
    # sleep computation, Atchim round 2): near the deadline, neither the
    # per-GET timeout nor the inter-poll sleep may exceed what's left of
    # the poll budget.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    captured_timeouts: list[float] = []
    captured_sleeps: list[float] = []

    def fake_get_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        captured_timeouts.append(kwargs["timeout_seconds"])  # type: ignore[arg-type]
        return 200, {"status": "RUNNING", "pages": []}

    def fake_sleep(seconds: float) -> None:
        captured_sleeps.append(seconds)

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        poll_interval_seconds=5.0,
        # calls, in order: token.get() now, token._refresh() expires_at,
        # extract() start, poll iter1 now (=8.0, remaining=2.0), sleep-calc
        # now (=8.0, remaining=2.0), poll iter2 now (=11.0, past deadline).
        clock=_clock_from([0.0, 0.0, 0.0, 8.0, 8.0, 11.0]),
        sleep=fake_sleep,
    )
    monkeypatch.setattr(transport, "get_json", fake_get_json)
    with pytest.raises(IDPPollTimeoutError):
        adapter.extract(str(doc), "action-1", "v1")

    remaining_budget = 2.0  # deadline(10.0) - now(8.0)
    assert captured_timeouts, "get_json was never called"
    assert all(t <= remaining_budget for t in captured_timeouts), captured_timeouts
    assert captured_sleeps, "sleep was never called"
    assert all(s <= remaining_budget for s in captured_sleeps), captured_sleeps


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
    # Sanity smoke test: the real defaults, unmocked, still work end to end.
    # NB: patching `time.time` here would NOT catch a "default is time.time"
    # mutant — a function's default argument value is bound to the actual
    # object once, at `def` time, so re-pointing the module attribute
    # afterwards can't affect an already-captured default. The mutant-killing
    # assertions are the signature/grep tests below (Atchim round 2).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

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


def test_adapter_clock_default_parameter_is_time_monotonic() -> None:
    # Kills a "default clock silently changed to time.time" mutant: a
    # default argument is bound once at def-time, so this must inspect the
    # actual bound default object, not probe behavior at call time.
    sig = inspect.signature(MuleSoftIDPAdapter.__init__)
    assert sig.parameters["clock"].default is time.monotonic
    assert sig.parameters["sleep"].default is time.sleep


def test_token_cache_clock_default_parameter_is_time_monotonic() -> None:
    from idp_regression.adapter.token_cache import TokenCache

    sig = inspect.signature(TokenCache.__init__)
    assert sig.parameters["clock"].default is time.monotonic


def test_no_time_time_reference_anywhere_under_adapter_package() -> None:
    # Static gate (Atchim round 2): time.time() must never be consulted for
    # budget math anywhere in the adapter package (INV-07), not just in the
    # two entry points checked above.
    adapter_dir = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression" / "adapter"
    )
    offenders = []
    for path in adapter_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\btime\.time\b", text):
            offenders.append(str(path))
    assert offenders == [], f"time.time() referenced in adapter/: {offenders}"


@pytest.mark.parametrize("bad_token", ["tok\r\ninjected", "tok\nvalue", "tok\x00null"])
def test_access_token_with_control_chars_is_rejected_at_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_token: str
) -> None:
    # /test Scenario B item 1 (primary defense): reject a malformed
    # access_token at fetch time so a CR/LF-carrying token can never reach
    # header construction in the first place (the transport-level
    # ValueError catch is the defense-in-depth backstop, tested in
    # test_transport.py).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    adapter = _adapter(
        monkeypatch,
        fetch_token_result=(200, {"access_token": bad_token, "expires_in": 300}),
    )
    with pytest.raises(IDPAuthenticationError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert bad_token not in str(excinfo.value)


def test_secrets_never_appear_in_a_token_fetch_failure_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Mirrors test_secrets_never_appear_in_a_submit_failure_log for the
    # _fetch_token path — kills a "re-add `from exc`" regression there
    # (Atchim round 2, R8b): if `_fetch_token` went back to
    # `raise IDPAuthenticationError(...) from exc`, the secret-bearing
    # IDPTransportError would reappear in __cause__/__context__.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def failing_post_json(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        raise IDPTransportError(
            "POST .../oauth2/token failed: connection reset while sending "
            "client_secret=csecret"
        )

    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(transport, "post_json", failing_post_json)
    with caplog.at_level(logging.ERROR), pytest.raises(IDPAuthenticationError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert "csecret" not in caplog.text
    assert "csecret" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert "csecret" not in repr(excinfo.value.__cause__)
    assert excinfo.value.__context__ is None
    assert "csecret" not in repr(excinfo.value.__context__)


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
