"""T-01.3.10a — OTLP trace + dataset-run linkage (TP-43 flush_failed).

Live-probed 2026-09-19: ``Langfuse.flush()`` never raises on export
failure (only logs via the OTLP exporter's own logger) and
``BatchSpanProcessor.force_flush()`` returns True even when export
failed — so ``flush_or_raise`` watches the exporter's logger as the only
observable failure signal. Manual span + dataset-run-item linkage does
NOT make a run appear in ``GET /api/public/experiments`` (probed); only
``Langfuse.run_experiment(...)`` does — ``run_dataset_experiment`` wraps
that proven path.
"""

from __future__ import annotations

import logging

import pytest

from idp_regression.platform.errors import FlushFailedError
from idp_regression.platform.tracing import (
    OTLP_EXPORTER_LOGGER_NAME,
    flush_or_raise,
    run_dataset_experiment,
)


class _OkTracingClient:
    def flush(self) -> None:
        return None

    def run_experiment(self, **kwargs: object) -> str:
        return "ok"


class _FailingExportTracingClient:
    """Simulates what the real SDK does: flush() logs an ERROR via the
    OTLP exporter's logger and returns normally (no exception)."""

    def flush(self) -> None:
        logging.getLogger(OTLP_EXPORTER_LOGGER_NAME).error(
            "Failed to export span batch due to timeout, max retries or shutdown."
        )


def test_flush_or_raise_succeeds_when_no_export_error_is_logged() -> None:
    flush_or_raise(_OkTracingClient())  # must not raise


def test_flush_or_raise_raises_flush_failed_when_exporter_logs_an_error() -> None:
    with pytest.raises(FlushFailedError):
        flush_or_raise(_FailingExportTracingClient())


def test_flush_or_raise_never_leaks_a_secret_via_the_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)

    class _LeakySimulatedFailure:
        def flush(self) -> None:
            logging.getLogger(OTLP_EXPORTER_LOGGER_NAME).error(
                "export failed: Authorization: Basic cHVibGljOnNlY3JldA=="
            )

    with pytest.raises(FlushFailedError) as excinfo:
        flush_or_raise(_LeakySimulatedFailure())

    # Our own exception message never echoes the exporter's log content —
    # only the exporter's own log line (outside our control) might, so
    # this asserts OUR error-raising code doesn't add to the leak.
    assert "Basic" not in str(excinfo.value)


def test_flush_or_raise_removes_its_log_handler_after_use() -> None:
    logger_ = logging.getLogger(OTLP_EXPORTER_LOGGER_NAME)
    before = len(logger_.handlers)
    flush_or_raise(_OkTracingClient())
    assert len(logger_.handlers) == before


def test_run_dataset_experiment_delegates_with_sequential_max_concurrency() -> None:
    calls: list[dict[str, object]] = []

    class _RecordingTracingClient:
        def run_experiment(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return "experiment-result"

    def task(*, item: object) -> object:
        return item

    result = run_dataset_experiment(
        _RecordingTracingClient(),
        run_name="test-s013-run-1",
        dataset_items=[{"id": "item-1"}],
        task=task,
    )

    assert result == "experiment-result"
    assert calls[0]["run_name"] == "test-s013-run-1"
    assert calls[0]["max_concurrency"] == 1
    assert calls[0]["task"] is task
