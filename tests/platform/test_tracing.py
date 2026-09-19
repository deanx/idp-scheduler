"""ADR-0005 Decision #9 — record-after experiment linkage (R5, R6).

R5 false negatives Atchim listed, each with its own test:
  (a) run_experiment calls flush() internally, outside a narrower watcher
      — the watcher here spans the WHOLE call (run_experiment + a
      trailing explicit flush()), so an internal-flush failure is caught.
  (b) batches exported by the background thread during the run are not
      watched by a narrower window — handlers are process-wide for the
      whole call's duration, so this is covered structurally by (a)'s fix.
  (c) dataset-run-item failures are logged on the `langfuse` logger, not
      the OTLP exporter logger — a second watcher on `langfuse` is added.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest

from idp_regression.platform.errors import ExperimentRecordFailedError, FlushFailedError
from idp_regression.platform.tracing import (
    LANGFUSE_SDK_LOGGER_NAME,
    OTLP_EXPORTER_LOGGER_NAME,
    ExperimentItem,
    record_experiment,
)


@dataclass(frozen=True)
class _FakeItemResult:
    item: Any
    trace_id: str | None
    dataset_run_id: str | None


class _FakeResult:
    def __init__(self, item_results: list[_FakeItemResult]) -> None:
        self.item_results = item_results


def _items(n: int) -> list[ExperimentItem]:
    return [
        ExperimentItem(
            id=f"item-{i}", dataset_id="ds", input={"document_id": f"doc-{i}"}, expected_output={}
        )
        for i in range(n)
    ]


class _OkTracingClient:
    def __init__(self, items: list[ExperimentItem], dataset_run_id: str = "run-abc") -> None:
        self._items = items
        self._dataset_run_id = dataset_run_id
        self.flushed = False

    def run_experiment(self, **kwargs: object) -> _FakeResult:
        return _FakeResult(
            [
                _FakeItemResult(
                    item=item, trace_id=f"trace-{item.id}", dataset_run_id=self._dataset_run_id
                )
                for item in self._items
            ]
        )

    def flush(self) -> None:
        self.flushed = True


def _task(*, item: object, **kwargs: object) -> dict[str, bool]:
    return {"ok": True}


def test_happy_path_returns_item_id_to_trace_id_map() -> None:
    items = _items(2)
    client = _OkTracingClient(items)

    trace_ids = record_experiment(client, run_name="test-s013-run", items=items, task=_task)

    assert trace_ids == {"item-0": "trace-item-0", "item-1": "trace-item-1"}
    assert client.flushed is True  # R5(a): a trailing explicit flush() is part of the call


def test_r5a_watcher_catches_an_error_logged_during_run_experiment_itself() -> None:
    """run_experiment calls flush() internally — the watcher must be up
    for the whole call, not just the trailing explicit flush()."""

    class _InternalFlushFailure(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            logging.getLogger(OTLP_EXPORTER_LOGGER_NAME).error(
                "Failed to export span batch due to timeout, max retries or shutdown."
            )
            return super().run_experiment(**kwargs)

    items = _items(1)
    with pytest.raises(FlushFailedError):
        record_experiment(_InternalFlushFailure(items), run_name="r", items=items, task=_task)


def test_r5b_watcher_catches_a_background_thread_export_error_mid_run() -> None:
    """Simulates a background BatchSpanProcessor thread logging an ERROR
    while run_experiment is still executing (not at the trailing flush)."""

    class _BackgroundThreadFailure(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            # A background export thread can log independently of the
            # foreground call — the watcher is process-wide, so it still
            # catches this even though nothing here calls flush().
            logging.getLogger(f"{OTLP_EXPORTER_LOGGER_NAME}.worker").error(
                "background export failed"
            )
            return super().run_experiment(**kwargs)

    items = _items(1)
    with pytest.raises(FlushFailedError):
        record_experiment(_BackgroundThreadFailure(items), run_name="r", items=items, task=_task)


def test_r5c_langfuse_logger_error_raises_experiment_record_failed() -> None:
    """dataset-run-item creation failures are logged on the `langfuse`
    logger (or a submodule of it), not the OTLP exporter logger."""

    class _DatasetRunItemFailure(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            logging.getLogger(f"{LANGFUSE_SDK_LOGGER_NAME}._client.client").error(
                "Failed to create dataset run item: 404 Dataset item not found"
            )
            return super().run_experiment(**kwargs)

    items = _items(1)
    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_DatasetRunItemFailure(items), run_name="r", items=items, task=_task)


def test_structural_check_short_item_results_raises() -> None:
    items = _items(2)

    class _ShortResults(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            full = super().run_experiment(**kwargs)
            return _FakeResult(full.item_results[:1])  # dropped one item silently

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_ShortResults(items), run_name="r", items=items, task=_task)


def test_structural_check_missing_trace_id_raises() -> None:
    items = _items(1)

    class _MissingTraceId(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            return _FakeResult(
                [_FakeItemResult(item=items[0], trace_id=None, dataset_run_id="r1")]
            )

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_MissingTraceId(items), run_name="r", items=items, task=_task)


def test_structural_check_null_dataset_run_id_raises() -> None:
    items = _items(1)

    class _NullDatasetRunId(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            return _FakeResult(
                [_FakeItemResult(item=items[0], trace_id="t1", dataset_run_id=None)]
            )

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_NullDatasetRunId(items), run_name="r", items=items, task=_task)


def test_structural_check_mismatched_dataset_run_ids_raises() -> None:
    items = _items(2)

    class _MismatchedRunIds(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            return _FakeResult(
                [
                    _FakeItemResult(item=items[0], trace_id="t0", dataset_run_id="run-a"),
                    _FakeItemResult(item=items[1], trace_id="t1", dataset_run_id="run-b"),
                ]
            )

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_MismatchedRunIds(items), run_name="r", items=items, task=_task)


def test_watcher_handlers_are_removed_after_the_call() -> None:
    items = _items(1)
    otlp_logger = logging.getLogger(OTLP_EXPORTER_LOGGER_NAME)
    langfuse_logger = logging.getLogger(LANGFUSE_SDK_LOGGER_NAME)
    otlp_before = len(otlp_logger.handlers)
    langfuse_before = len(langfuse_logger.handlers)

    record_experiment(_OkTracingClient(items), run_name="r", items=items, task=_task)

    assert len(otlp_logger.handlers) == otlp_before
    assert len(langfuse_logger.handlers) == langfuse_before


def test_watcher_never_captures_the_log_message_only_the_logger_name() -> None:
    """The watcher records only that a failure happened, never the log
    message text — messages may carry API error bodies (NFR N5, INV-02)."""
    items = _items(1)

    class _LeakySimulatedFailure(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            logging.getLogger(LANGFUSE_SDK_LOGGER_NAME).error(
                "leak test: Authorization: Basic cHVibGljOnNlY3JldA=="
            )
            return super().run_experiment(**kwargs)

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        record_experiment(_LeakySimulatedFailure(items), run_name="r", items=items, task=_task)

    assert "Basic" not in str(excinfo.value)
    assert "cHVibGljOnNlY3JldA==" not in str(excinfo.value)
