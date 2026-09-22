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
import urllib.parse
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
from idp_regression.adapter.idp_client import (
    MAX_RETRY_AFTER_SECONDS,
    MuleSoftIDPAdapter,
    _parse_retry_after_seconds,
)


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
    poll_interval_seconds: float = 10.0,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    terminal_statuses: set[str] | None = None,
    success_statuses: set[str] | None = None,
    poll_retry_max_attempts: int = 3,
    random_func: Callable[[], float] | None = None,
) -> MuleSoftIDPAdapter:
    monkeypatch.setattr(
        transport, "post_json", lambda *a, **kw: fetch_token_result  # noqa: ARG005
    )
    monkeypatch.setattr(
        transport, "post_multipart_file", lambda *a, **kw: submit_result  # noqa: ARG005
    )

    def _padded(
        result: tuple[int, dict[str, Any]] | tuple[int, dict[str, Any], dict[str, str]],
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        # Most tests supply a plain (status, body) 2-tuple; pad it with
        # empty response headers so this helper stays the only place that
        # knows about get_json_with_headers's 3-tuple shape (T-01.4.3a/b).
        if len(result) == 2:
            return result[0], result[1], {}
        return result

    default_poll_result = (200, {"status": "SUCCEEDED", "fields": {}, "tables": {}})
    results = [_padded(r) for r in (poll_results or [default_poll_result])]
    poll_iter = iter(results)
    last_poll_result = results[-1]

    def fake_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        nonlocal last_poll_result
        with contextlib.suppress(StopIteration):
            last_poll_result = next(poll_iter)
        return last_poll_result

    monkeypatch.setattr(transport, "get_json_with_headers", fake_get_json)

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
        poll_retry_max_attempts=poll_retry_max_attempts,
        clock=clock or _advancing_clock(),
        sleep=sleep or sleeps.append,
        random_func=random_func or (lambda: 0.5),
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


def test_poll_get_url_carries_value_only_false_query_param(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Defect (2026-09-22 architecture-adherence review): the Anypoint IDP
    # execution-result GET returns value-only output by default (bare
    # scalars, e.g. {"total": "1150.00"}), and normalize() REQUIRES the
    # full {"value": ..., "confidence": ...} cell shape (invalid_cell on a
    # missing "value" key) -- so every real poll would fail to normalize
    # unless the GET explicitly asks for the full shape via
    # ?valueOnly=false. Pin the literal query parameter on the URL the
    # adapter actually requests -- a mutant dropping it must go RED.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    requested_urls: list[str] = []

    def capturing_get_json(
        url: str, *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        requested_urls.append(url)
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 0.5, 1.0]),
    )
    monkeypatch.setattr(transport, "get_json_with_headers", capturing_get_json)
    adapter.extract(str(doc), "action-1", "v1")

    assert requested_urls, "get_json_with_headers was never called"
    (url,) = requested_urls
    assert url.startswith(
        "https://idp-rt.us-east-2.anypoint.mulesoft.com/api/v1"
        "/organizations/org-1/actions/action-1/versions/v1/executions/exec-1?"
    ), url
    parsed_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert parsed_query == {"valueOnly": ["false"]}, parsed_query


def test_poll_get_url_carries_value_only_false_on_every_url_across_an_auth_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Independent-review follow-up (2026-09-22, S-1): the prior pin only
    ever observed the FIRST GET's URL (a fixture that succeeds immediately,
    `(url,) = requested_urls`). `_poll()` builds ``url`` once and every
    retry path re-uses that same local, so today's code is correct -- but a
    future refactor moving URL construction INTO
    `_poll_get_with_auth_retry` could drop `?valueOnly=false` on only the
    RETRIED GET (the slow-extraction path most likely to actually retry)
    and this suite would stay green. Forces a 401-then-200 sequence (the
    auth-refresh retry path in `_poll_get_with_auth_retry` itself) and
    asserts the query param on BOTH captured URLs, plus that a retry
    actually fired -- otherwise this would pass vacuously."""
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    requested_urls: list[str] = []

    def capturing_get_json(
        url: str, *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        requested_urls.append(url)
        if len(requested_urls) == 1:
            return 401, {"error": "expired"}, {}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 0.5, 1.0, 1.5, 2.0]),
    )
    monkeypatch.setattr(transport, "get_json_with_headers", capturing_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")

    assert out["status"] == "SUCCEEDED"
    assert len(requested_urls) > 1, "the auth retry never fired -- test would be vacuous"
    for url in requested_urls:
        parsed_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert parsed_query == {"valueOnly": ["false"]}, (url, requested_urls)


def test_poll_get_url_carries_value_only_false_on_every_url_across_a_transient_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same S-1 gap as the auth-retry sibling above, for the OTHER retry
    path: the outer `while True` loop in `_poll()` re-reading the same
    ``url`` local across a 429-then-200 sequence. Forces a transient retry
    and asserts the query param on every captured URL, plus that a retry
    actually fired."""
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    requested_urls: list[str] = []

    def flaky_get_json(
        url: str, *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        requested_urls.append(url)
        if len(requested_urls) == 1:
            return 429, {"error": "rate limited"}, {}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")

    assert out["status"] == "SUCCEEDED"
    assert len(requested_urls) > 1, "the transient retry never fired -- test would be vacuous"
    for url in requested_urls:
        parsed_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert parsed_query == {"valueOnly": ["false"]}, (url, requested_urls)


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

    def flaky_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IDPTransportError("transient connection reset")
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    assert call_count["n"] == 2  # one transient failure, one success


def test_poll_transport_error_eventually_times_out_if_never_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")

    def always_failing_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        raise IDPTransportError("connection reset")

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=3.0,
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 3.0, 4.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", always_failing_get_json)
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
        poll_results=[(200, {"status": "DONE", "fields": {}, "tables": {}})],
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
        poll_results=[(200, {"status": "SUCCEEDED", "fields": {}, "tables": {}})],
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
            (200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}),
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
            (200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}),
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


@pytest.mark.parametrize("http_status", [401, 403])
def test_poll_401_or_403_refresh_then_retry_succeeds_and_invalidates_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    """Coverage audit gap 1 (2026-09-21, DEBT-21 / ADR-0004 #7): only the
    401/403 -> error path was ever pinned for `_poll_get_with_auth_retry`
    -- the auditor deleted the entire refresh-and-retry block and 467
    tests still passed. This pins the SUCCESS branch: a mid-poll 401/403
    triggers exactly one `TokenCache.invalidate()`, one token refresh,
    the SAME GET retried, and the run continues to completion on the
    retried response. Confirmed RED against the block-deleted code."""
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    token_fetch_calls: list[int] = []

    def counting_post_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any]]:
        token_fetch_calls.append(1)
        return 200, {"access_token": f"tok-{len(token_fetch_calls)}", "expires_in": 300}

    adapter = _adapter(
        monkeypatch,
        poll_results=[
            (http_status, {"error": "expired"}),
            (200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}),
        ],
        clock=_clock_from([0.0, 0.0, 0.5, 1.0, 1.5, 2.0]),
    )
    monkeypatch.setattr(transport, "post_json", counting_post_json)

    invalidate_calls: list[int] = []
    original_invalidate = adapter._token_cache.invalidate

    def counting_invalidate() -> None:
        invalidate_calls.append(1)
        original_invalidate()

    monkeypatch.setattr(adapter._token_cache, "invalidate", counting_invalidate)

    out = adapter.extract(str(doc), "action-1", "v1")

    assert out["status"] == "SUCCEEDED"
    assert len(invalidate_calls) == 1
    # One fetch for the initial token, one refresh triggered by the 401/403.
    assert len(token_fetch_calls) == 2


def test_poll_second_get_after_401_uses_the_refreshed_token_not_the_old_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Atchim re-gate (DEBT-46) — a mutant that discards
    ``TokenCache.get()``'s return value and replays the OLD Bearer token on
    the retried GET survives 274/274. This is the same defect class as
    DEBT-21, one level deeper: the retry HAPPENS, but nothing proves it
    carries the NEW token. Pins the second GET's ``Authorization`` header
    against the first — they must differ, and the second must carry the
    freshly-fetched token."""
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    token_fetch_calls: list[int] = []

    def counting_post_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any]]:
        token_fetch_calls.append(1)
        return 200, {"access_token": f"tok-{len(token_fetch_calls)}", "expires_in": 300}

    captured_auth_headers: list[str] = []
    poll_call_count = {"n": 0}

    def counting_get_json(
        url: str, *, timeout_seconds: float, headers: dict[str, str]
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        poll_call_count["n"] += 1
        captured_auth_headers.append(headers["Authorization"])
        if poll_call_count["n"] == 1:
            return 401, {"error": "expired"}, {}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 0.5, 1.0, 1.5, 2.0]),
    )
    monkeypatch.setattr(transport, "post_json", counting_post_json)
    monkeypatch.setattr(transport, "get_json_with_headers", counting_get_json)

    out = adapter.extract(str(doc), "action-1", "v1")

    assert out["status"] == "SUCCEEDED"
    assert len(captured_auth_headers) == 2
    assert captured_auth_headers[0] == "Bearer tok-1"
    assert captured_auth_headers[1] == "Bearer tok-2"
    assert captured_auth_headers[0] != captured_auth_headers[1]


def test_poll_deadline_still_fires_after_retries_have_consumed_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Atchim re-gate (DEBT-46) — mutant
    ``if now >= deadline and poll_retry_count == 0:`` survives 274/274.
    ADR-0004 #3 says the retry budget is INCLUDED in, never extends, the
    per-document timeout — but only the zero-retry path pinned that. Here
    two 5xx retries consume time (not the retry-attempts budget: default
    max_attempts=3, only 2 used) before the clock crosses the deadline;
    the real code must still raise ``IDPPollTimeoutError`` on the very
    next loop iteration, with retry attempts still remaining. Fails FAST
    under the mutant (a 3rd, then 4th, 500 keeps arriving until the
    retry-budget itself exhausts and ``IDPPollHardFailureError`` fires
    instead — a different exception, not a 120s pytest-timeout hang)."""
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def always_transient_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        return 500, {"error": "transient"}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        clock=_clock_from([0.0, 0.0, 1.0, 2.0, 2.0, 3.0, 3.0, 11.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", always_transient_get_json)

    with pytest.raises(IDPPollTimeoutError):
        adapter.extract(str(doc), "action-1", "v1")

    # Only 2 GETs happened (2 retries) — the default poll_retry_max_attempts
    # is 3, so the retry-attempts budget still had headroom: the timeout
    # fired on the deadline alone, not because attempts ran out.
    assert len(calls) == 2


@pytest.mark.parametrize("http_status", [404, 400])
def test_poll_non_429_4xx_raises_hard_failure_immediately_not_polled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    # ADR-0004 #5/#6: a 404/400/etc on the poll request is STILL a hard
    # failure, aborted immediately — never retried, and the body's "status"
    # (if any) is never read as the execution status (Atchim R5). Only
    # 429/5xx (below) are bounded-retried per DEBT-24/ADR-0004 #6 — this is
    # the revised, narrower form of what
    # test_poll_other_non_2xx_raises_hard_failure_immediately_not_polled
    # used to pin over [404, 400, 500, 503] before the retry work landed.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def counting_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        return http_status, {"status": "SUCCEEDED", "error": "not found"}, {}

    adapter = _adapter(monkeypatch, clock=_clock_from([0.0, 0.0, 0.5]))
    monkeypatch.setattr(transport, "get_json_with_headers", counting_get_json)
    with pytest.raises(IDPPollHardFailureError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert excinfo.value.http_status == http_status
    assert len(calls) == 1  # aborted immediately, not polled again, not retried


@pytest.mark.parametrize("http_status", [500, 503])
def test_poll_5xx_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    # DEBT-24 / ADR-0004 #6: a transient 5xx on the poll request is retried
    # within the poll budget, bounded backoff — not an immediate abort.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def flaky_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        if len(calls) == 1:
            return http_status, {"error": "transient"}, {}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    assert len(calls) == 2  # one transient 5xx, one success


@pytest.mark.parametrize("http_status", [500, 429])
def test_poll_5xx_or_429_retry_budget_exhaustion_raises_hard_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http_status: int
) -> None:
    # DEBT-24 / ADR-0004 #3/#6: "a retry budget that exhausts before
    # terminal status is a hard failure -> abort". Default max-attempts is
    # 3, so a 4th consecutive transient failure exhausts the budget.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def always_transient_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        return http_status, {"error": "transient"}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=3600.0,  # large enough that the retry budget,
        # not the deadline, is what exhausts first
        clock=_clock_from([0.0, 0.0] + [float(i) for i in range(1, 20)]),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", always_transient_get_json)
    with pytest.raises(IDPPollHardFailureError) as excinfo:
        adapter.extract(str(doc), "action-1", "v1")
    assert excinfo.value.http_status == http_status
    assert len(calls) == 4  # default poll_retry_max_attempts=3 -> 4 total attempts


def test_poll_429_honours_sane_retry_after_capped_to_remaining_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ADR-0004 #3: "429 honors Retry-After capped at the remaining poll
    # budget (do not extend the budget)". Retry-After=1000s is far bigger
    # than the remaining budget, so the honoured sleep must be clamped down
    # to what's left, never extending the absolute poll deadline.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []
    captured_sleeps: list[float] = []

    def flaky_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        if len(calls) == 1:
            return 429, {"error": "rate limited"}, {"retry-after": "1000"}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=captured_sleeps.append,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    assert captured_sleeps, "sleep was never called"
    # deadline(10.0) - now(1.0, after the 429) = 9.0 remaining budget cap.
    assert all(s <= 9.0 for s in captured_sleeps), captured_sleeps
    assert captured_sleeps[0] < 1000.0  # NOT the raw, un-capped Retry-After


def test_poll_429_with_insane_retry_after_falls_back_to_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An IDP-controlled Retry-After that isn't a sane non-negative number
    # (HTTP-date form, negative, huge) must never be honoured verbatim —
    # fall back to the exponential-backoff computation instead (QA F-1
    # posture extended to an untrusted response header).
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []
    captured_sleeps: list[float] = []

    def flaky_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        if len(calls) == 1:
            return 429, {"error": "rate limited"}, {"retry-after": "not-a-number"}
        return 200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=60.0,
        clock=_clock_from([0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
        sleep=captured_sleeps.append,
        random_func=lambda: 1.0,  # deterministic: full jitter ceiling
    )
    monkeypatch.setattr(transport, "get_json_with_headers", flaky_get_json)
    out = adapter.extract(str(doc), "action-1", "v1")
    assert out["status"] == "SUCCEEDED"
    # attempt 1: base(1.0) * 2**0 = 1.0, random_func()=1.0 -> sleep == 1.0,
    # nowhere near the bogus "not-a-number" header nor the 8s cap.
    assert captured_sleeps == pytest.approx([1.0])


def test_poll_retry_backoff_is_bounded_by_max_attempts_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The max-attempts budget is config (ADR-0004 #3), not hard-coded —
    # construction-time override changes when the hard failure fires.
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    calls: list[int] = []

    def always_503(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(1)
        return 503, {"error": "unavailable"}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=3600.0,
        clock=_clock_from([0.0, 0.0] + [float(i) for i in range(1, 10)]),
        sleep=lambda _seconds: None,
        poll_retry_max_attempts=1,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", always_503)
    with pytest.raises(IDPPollHardFailureError):
        adapter.extract(str(doc), "action-1", "v1")
    assert len(calls) == 2  # max_attempts=1 -> 2 total attempts


@pytest.mark.parametrize(
    "raw_value",
    ["-5", "-0.001", "nan", "inf", "-inf", str(MAX_RETRY_AFTER_SECONDS + 1)],
)
def test_parse_retry_after_seconds_rejects_negative_nonfinite_and_over_cap(
    raw_value: str,
) -> None:
    """Atchim re-gate (DEBT-46) — mutant ``if False:`` on the
    finite/negative/cap guard survives 274/274; only the non-numeric
    branch (``float()`` raising) was ever covered. A ``Retry-After: -5``
    or ``nan`` must fall back to None (exponential backoff), never reach
    ``self._sleep()`` as a raw negative/NaN duration."""
    assert _parse_retry_after_seconds(raw_value) is None


def test_parse_retry_after_seconds_accepts_the_boundary_value_at_the_cap() -> None:
    # The guard is `> MAX_RETRY_AFTER_SECONDS`, so the cap itself is honoured
    # (inclusive) — distinguishes the real guard from an off-by-one mutant.
    assert _parse_retry_after_seconds(str(MAX_RETRY_AFTER_SECONDS)) == pytest.approx(
        MAX_RETRY_AFTER_SECONDS
    )


def test_poll_retry_sleep_seconds_ignores_retry_after_header_on_a_non_429_status() -> None:
    """Atchim re-gate (DEBT-46) — mutant ``if status_code == 429`` ->
    ``if True`` survives 274/274: a 500 response's own ``Retry-After``
    header (which a real server has no ADR-0004-sanctioned reason to send
    on a 5xx) would otherwise be honoured verbatim instead of falling
    through to exponential backoff. Calls the private helper directly —
    fast, no clock/sleep plumbing needed."""
    adapter = MuleSoftIDPAdapter(
        client_id="cid",
        client_secret="csecret",
        region="us-east-2",
        org_id="org-1",
        terminal_statuses={"SUCCEEDED"},
        success_statuses={"SUCCEEDED"},
        random_func=lambda: 1.0,  # deterministic: full-jitter ceiling
    )
    sleep_seconds = adapter._poll_retry_sleep_seconds(
        attempt=1,
        status_code=500,
        headers={"retry-after": "50"},
        remaining_budget=100.0,
    )
    # base(1.0) * 2**0 = 1.0, random_func()=1.0 -> 1.0, capped by the 100.0
    # remaining budget -- NOT the header's 50.0, which only a 429 may honour.
    assert sleep_seconds == pytest.approx(1.0)


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


@pytest.mark.parametrize("below_floor", [0.1, 1.0, 3.0, 9.0, 9.999])
def test_poll_interval_below_the_10_second_floor_raises_typed_config_error(
    below_floor: float,
) -> None:
    # Rate-protection floor (2026-09-22 architecture-adherence review): a
    # 3s poll against a long extraction is ~40 requests/document. Rejected
    # fail-closed at construction, same typed-error shape as the existing
    # MAX_* bounds (QA F-1 precedent), never discovered mid-poll.
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(poll_interval_seconds=below_floor))  # type: ignore[arg-type]


def test_poll_interval_at_the_10_second_floor_is_accepted() -> None:
    MuleSoftIDPAdapter(**_adapter_kwargs(poll_interval_seconds=10.0))  # type: ignore[arg-type]


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


@pytest.mark.parametrize(
    "kwarg",
    ["submit_timeout_seconds", "poll_timeout_seconds", "poll_interval_seconds"],
)
def test_timing_param_at_the_3600_second_cap_is_accepted(kwarg: str) -> None:
    # Atchim suggestion: pin the exact boundary, not just "absurdly large"
    # — kills a "cap 1h -> 2h" survivor (the max_value constant silently
    # loosened would not be caught by a value that's still comfortably
    # under 2h too).
    MuleSoftIDPAdapter(**_adapter_kwargs(**{kwarg: 3600.0}))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwarg",
    ["submit_timeout_seconds", "poll_timeout_seconds", "poll_interval_seconds"],
)
def test_timing_param_just_above_the_3600_second_cap_is_rejected(kwarg: str) -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(**{kwarg: 3600.1}))  # type: ignore[arg-type]


def test_token_refresh_margin_at_the_3600_second_cap_is_accepted() -> None:
    MuleSoftIDPAdapter(**_adapter_kwargs(token_refresh_margin_seconds=3600.0))  # type: ignore[arg-type]


def test_token_refresh_margin_just_above_the_3600_second_cap_is_rejected() -> None:
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(token_refresh_margin_seconds=3600.1))  # type: ignore[arg-type]


_HUGE_INT = 10**400


@pytest.mark.parametrize(
    "kwarg",
    [
        "submit_timeout_seconds",
        "poll_timeout_seconds",
        "poll_interval_seconds",
        "token_refresh_margin_seconds",
    ],
)
def test_huge_int_timing_param_raises_typed_config_error_not_raw_overflow(kwarg: str) -> None:
    # Coverage-audit defect: _validate_timing's bare float(value) raises a
    # raw OverflowError for an int too large to represent as a float
    # (float(10**400)) — must be a typed IDPConfigurationError instead,
    # with the value never in the message.
    with pytest.raises(IDPConfigurationError) as excinfo:
        MuleSoftIDPAdapter(**_adapter_kwargs(**{kwarg: _HUGE_INT}))  # type: ignore[arg-type]
    assert str(_HUGE_INT) not in str(excinfo.value)


@pytest.mark.parametrize(
    "kwarg",
    [
        "submit_timeout_seconds",
        "poll_timeout_seconds",
        "poll_interval_seconds",
        "token_refresh_margin_seconds",
    ],
)
@pytest.mark.parametrize("bad_bool", [True, False])
def test_bool_timing_param_raises_typed_config_error(kwarg: str, bad_bool: bool) -> None:
    # bool is an int subclass in Python — isinstance(True, (int, float)) is
    # True, so this must be checked explicitly.
    with pytest.raises(IDPConfigurationError):
        MuleSoftIDPAdapter(**_adapter_kwargs(**{kwarg: bad_bool}))  # type: ignore[arg-type]


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

    def fake_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        get_json_calls.append(1)
        return 200, {"status": "RUNNING", "pages": []}, {}

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        poll_interval_seconds=10.0,
        clock=clock,
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(transport, "post_multipart_file", slow_submit)
    monkeypatch.setattr(transport, "get_json_with_headers", fake_get_json)
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

    def fake_get_json(
        *args: object, **kwargs: object
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        captured_timeouts.append(kwargs["timeout_seconds"])  # type: ignore[arg-type]
        return 200, {"status": "RUNNING", "pages": []}, {}

    def fake_sleep(seconds: float) -> None:
        captured_sleeps.append(seconds)

    adapter = _adapter(
        monkeypatch,
        poll_timeout_seconds=10.0,
        poll_interval_seconds=10.0,
        # calls, in order: token.get() now, token._refresh() expires_at,
        # extract() start, poll iter1 now (=8.0, remaining=2.0), sleep-calc
        # now (=8.0, remaining=2.0), poll iter2 now (=11.0, past deadline).
        clock=_clock_from([0.0, 0.0, 0.0, 8.0, 8.0, 11.0]),
        sleep=fake_sleep,
    )
    monkeypatch.setattr(transport, "get_json_with_headers", fake_get_json)
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
        transport,
        "get_json_with_headers",
        lambda *a, **kw: (200, {"status": "SUCCEEDED", "fields": {}, "tables": {}}, {}),
    )
    adapter = MuleSoftIDPAdapter(
        client_id="cid",
        client_secret="csecret",
        region="us-east-2",
        org_id="org-1",
        terminal_statuses={"SUCCEEDED"},
        success_statuses={"SUCCEEDED"},
        # >= MIN_POLL_INTERVAL_SECONDS; the real time.sleep default is never
        # actually invoked here since the first poll returns SUCCEEDED.
        poll_interval_seconds=10.0,
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
