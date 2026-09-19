"""Typed-error construction hygiene (/test Scenario B, item 6).

``IDPExecutionFailedError.status`` and ``IDPPollTimeoutError.last_status``
are built from IDP-controlled poll-response content — an unbounded or
control-char-laden status string could inject into a log line downstream.
Both are capped/sanitized at construction.
"""

from __future__ import annotations

from idp_regression.adapter.errors import IDPExecutionFailedError, IDPPollTimeoutError


def test_execution_failed_status_is_capped_at_64_chars() -> None:
    huge = "X" * 500
    exc = IDPExecutionFailedError("boom", status=huge)
    assert len(exc.status) <= 64


def test_execution_failed_status_control_chars_are_sanitized() -> None:
    exc = IDPExecutionFailedError("boom", status="FAILED\n\rinjected=1")
    assert "\n" not in exc.status
    assert "\r" not in exc.status


def test_execution_failed_status_normal_value_is_preserved() -> None:
    exc = IDPExecutionFailedError("boom", status="FAILED")
    assert exc.status == "FAILED"


def test_poll_timeout_last_status_is_capped_at_64_chars() -> None:
    huge = "Y" * 500
    exc = IDPPollTimeoutError("boom", last_status=huge)
    assert exc.last_status is not None
    assert len(exc.last_status) <= 64


def test_poll_timeout_last_status_control_chars_are_sanitized() -> None:
    exc = IDPPollTimeoutError("boom", last_status="RUNNING\ninjected=1")
    assert exc.last_status is not None
    assert "\n" not in exc.last_status


def test_poll_timeout_last_status_none_stays_none() -> None:
    exc = IDPPollTimeoutError("boom", last_status=None)
    assert exc.last_status is None
