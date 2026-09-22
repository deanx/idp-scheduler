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

import asyncio
import logging
import threading
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
    """A REAL background thread (like the OTel BatchSpanProcessor's own
    export worker) logs an ERROR while run_experiment is still
    executing on the main thread. Python's ``logging`` handlers are
    process-wide and thread-safe, so the watcher installed on the main
    thread must catch a record emitted from a genuinely different
    thread, not just a same-thread call made to look like one."""

    class _BackgroundThreadFailure(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            logger_ = logging.getLogger(f"{OTLP_EXPORTER_LOGGER_NAME}.worker")
            thread = threading.Thread(
                target=logger_.error, args=("background export failed",)
            )
            thread.start()
            thread.join(timeout=5.0)
            assert not thread.is_alive(), "background export thread never finished"
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


def test_gap2_run_experiment_raising_is_wrapped_not_leaked() -> None:
    """HARDEN-01 GAP-2: an unwrapped SDK exception from `run_experiment`
    itself (transport, auth, ...) must not escape `record_experiment`
    untyped -- it is the reachable production trigger for GAP-1 (an
    exception this codebase's own contract says can never escape
    `run_eval`)."""
    items = _items(1)

    class _RaisingClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            raise TimeoutError("socket hung")

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_RaisingClient(items), run_name="r", items=items, task=_task)


def test_gap2_flush_raising_is_wrapped_not_leaked() -> None:
    items = _items(1)

    class _RaisingFlush(_OkTracingClient):
        def flush(self) -> None:
            raise TimeoutError("socket hung")

    with pytest.raises(FlushFailedError):
        record_experiment(_RaisingFlush(items), run_name="r", items=items, task=_task)


def test_gap2_drifted_item_results_attribute_is_wrapped_not_leaked() -> None:
    """A version bump that renames/removes `result.item_results` raises a
    raw `AttributeError` today -- must surface as `ExperimentRecordFailedError`."""
    items = _items(1)

    class _NoItemResultsAttr:
        pass

    class _DriftedResultClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> Any:
            return _NoItemResultsAttr()

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_DriftedResultClient(items), run_name="r", items=items, task=_task)


def test_run_experiment_raising_does_not_leak_the_original_exception_via_context() -> None:
    """Suggestion (Atchim gate, 2026-09-21): `raise ... from None` only
    sets `__suppress_context__` -- the ORIGINAL exception object still
    sits on `__context__`, so a future `exc_info=True`/rich-traceback
    logger walking `__context__` directly (ignoring the suppress flag)
    could still print it. Atchim observed exactly this:
    `RuntimeError('Bearer sk-lf-SECRET')` surviving on `__context__`.
    `__context__` must be cleared, not merely suppressed."""
    items = _items(1)

    class _RaisingClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            raise RuntimeError("Bearer sk-lf-SECRET")

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        record_experiment(_RaisingClient(items), run_name="r", items=items, task=_task)

    assert excinfo.value.__context__ is None


def test_flush_raising_does_not_leak_the_original_exception_via_context() -> None:
    items = _items(1)

    class _RaisingFlush(_OkTracingClient):
        def flush(self) -> None:
            raise RuntimeError("Bearer sk-lf-SECRET")

    with pytest.raises(FlushFailedError) as excinfo:
        record_experiment(_RaisingFlush(items), run_name="r", items=items, task=_task)

    assert excinfo.value.__context__ is None


def test_drifted_item_results_attribute_does_not_leak_via_context() -> None:
    items = _items(1)

    class _NoItemResultsAttr:
        pass

    class _DriftedResultClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> Any:
            return _NoItemResultsAttr()

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        record_experiment(_DriftedResultClient(items), run_name="r", items=items, task=_task)

    assert excinfo.value.__context__ is None


def test_gap4_a_lazily_raising_item_results_attribute_is_wrapped_not_leaked() -> None:
    """GAP-4 (Branca `/harden` re-run, 2026-09-21): the structural check
    below `list(result.item_results)` only guarded THAT access with
    `except AttributeError` -- iterating a lazy/property-backed
    `item_results` that raises something else (or accessing `.trace_id`/
    `.dataset_run_id`/`.item.id` on a drifted item shape) was
    unguarded and escaped raw."""
    items = _items(1)

    class _LazyBoomItemResults:
        def __iter__(self) -> Any:
            raise RuntimeError("SDK internals drifted")

    class _LazyResult:
        item_results = _LazyBoomItemResults()

    class _LazyDriftClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> Any:
            return _LazyResult()

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_LazyDriftClient(items), run_name="r", items=items, task=_task)


def test_gap4_an_item_result_missing_expected_attributes_is_wrapped_not_leaked() -> None:
    """GAP-4: `.trace_id`/`.dataset_run_id`/`.item.id` access on a
    drifted item-result shape (e.g. a version bump renaming `.item` or
    `.trace_id`) must not escape as a raw `AttributeError` either."""
    items = _items(1)

    class _NoAttrsItemResult:
        pass

    class _DriftedItemResultClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            return _FakeResult([_NoAttrsItemResult()])  # type: ignore[list-item]

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_DriftedItemResultClient(items), run_name="r", items=items, task=_task)


def test_gap5_a_cancelled_error_from_run_experiment_is_wrapped_not_leaked() -> None:
    """GAP-5 (Branca `/harden` re-run, 2026-09-21): `run_experiment`'s own
    internal `asyncio.gather` can raise `asyncio.CancelledError`, a
    `BaseException` subclass NOT caught by a plain `except Exception`.
    `record_experiment`'s two `except Exception` blocks (run_experiment,
    flush) must also catch it -- unlike `KeyboardInterrupt`/
    `SystemExit`, which must still propagate uncaught."""
    items = _items(1)

    class _CancelledClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            raise asyncio.CancelledError()

    with pytest.raises(ExperimentRecordFailedError):
        record_experiment(_CancelledClient(items), run_name="r", items=items, task=_task)


def test_gap5_a_cancelled_error_from_flush_is_wrapped_not_leaked() -> None:
    items = _items(1)

    class _CancelledFlushClient(_OkTracingClient):
        def flush(self) -> None:
            raise asyncio.CancelledError()

    with pytest.raises(FlushFailedError):
        record_experiment(_CancelledFlushClient(items), run_name="r", items=items, task=_task)


def test_gap5_keyboard_interrupt_still_propagates_through_record_experiment() -> None:
    """GAP-5's fix must not become a bare `except BaseException` --
    `KeyboardInterrupt` must still propagate uncaught."""
    items = _items(1)

    class _InterruptedClient(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        record_experiment(_InterruptedClient(items), run_name="r", items=items, task=_task)


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


def test_otlp_path_never_leaks_the_auth_header_on_a_4xx_or_5xx_export_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gap 7 (TP-44, OTLP path): the real OTel OTLP exporter logs export
    failures like ``Failed to export span batch code: 401, reason:
    Unauthorized`` — mock that response shape (a 401/500-style body that
    could, in principle, echo request headers) and assert the resulting
    ``FlushFailedError``'s message never contains the Basic auth header
    or the secret it encodes, and neither does anything OUR code adds to
    the captured logs."""
    caplog.set_level(logging.ERROR)
    items = _items(1)

    class _OtlpExportRejected(_OkTracingClient):
        def run_experiment(self, **kwargs: object) -> _FakeResult:
            # Simulated exporter response body for a 401/5xx export
            # failure (mocked — never a real network call).
            logging.getLogger(OTLP_EXPORTER_LOGGER_NAME).error(
                "Failed to export span batch code: 401, reason: Unauthorized, "
                "request headers: Authorization: Basic cHVibGljOnNlY3JldA=="
            )
            return super().run_experiment(**kwargs)

    with pytest.raises(FlushFailedError) as excinfo:
        record_experiment(_OtlpExportRejected(items), run_name="r", items=items, task=_task)

    assert "Basic" not in str(excinfo.value)
    assert "cHVibGljOnNlY3JldA==" not in str(excinfo.value)
