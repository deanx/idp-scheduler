"""Record-after experiment linkage (ADR-0005 Decision #9; closes Atchim
S-01.3 R6, specifies the R5 fix).

Empirically probed 2026-09-19 against the live self-hosted Langfuse
4.38.0 (events_only): only ``Langfuse.run_experiment(data=…, task=…)``
makes a run appear under the dataset's Experiments tab
(``GET /api/public/experiments``); a manual OTel span + ``POST
/api/public/dataset-run-items`` link does not, even after 80s (see the
history of this module / DEBT-15/16 for the rejected approaches).

``run_experiment`` wraps items in ``asyncio.gather(return_exceptions=True)``
(logs+swallows task exceptions), calls ``flush()`` internally, and logs
+swallows a failed ``dataset_run_items.create`` on the ``langfuse`` logger
— none of these failures raise. So this module never runs the
orchestrator's per-document loop through ``run_experiment`` (that would
break INV-06/INV-08, ADR-0005 #9 rejected option (b)). Instead
``record_experiment`` REPLAYS already-computed, already-gated results
through a pure, total ``task`` purely to get the Experiments-tab-visible
linkage (``trace_id``/``dataset_run_id``) — and detects every failure
mode via (1) a dual log watcher spanning the whole call and (2) a
structural check on the returned ``item_results``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from idp_regression.platform.errors import ExperimentRecordFailedError, FlushFailedError

#: The OTel OTLP HTTP exporter's logger — an export failure (including one
#: from run_experiment's own internal flush(), or a background
#: BatchSpanProcessor thread mid-run) is only observable here (probed
#: 2026-09-19: Langfuse.flush()/force_flush() never raise or return False
#: on export failure).
OTLP_EXPORTER_LOGGER_NAME = "opentelemetry.exporter.otlp.proto.http.trace_exporter"

#: The langfuse SDK's own logger (and its submodules, via propagation) —
#: a failed `dataset_run_items.create` call (R5c) is logged here, not on
#: the OTLP exporter logger.
LANGFUSE_SDK_LOGGER_NAME = "langfuse"


@dataclass(frozen=True)
class ExperimentItem:
    """Adapter-private, duck-typed item ``run_experiment`` reads by
    attribute access (``id``, ``dataset_id``, ``input``, ``expected_output``,
    ``metadata``) — ADR-0005 #9. Never a real SDK ``DatasetItemClient``
    (that would force a second fetch, violating INV-04's single-fetch
    rule) and never crosses the ``PlatformAdapter`` Protocol (NFR N24).
    """

    id: str
    dataset_id: Any
    input: dict[str, Any]
    expected_output: dict[str, Any]
    metadata: None = None


class ExperimentRunner(Protocol):
    def run_experiment(
        self,
        *,
        name: str,
        run_name: str,
        data: list[ExperimentItem],
        task: Callable[..., Any],
        max_concurrency: int = 1,
        metadata: dict[str, str] | None = None,
    ) -> Any: ...

    def flush(self) -> None: ...


class _FailureWatcher(logging.Handler):
    """Records only THAT an ERROR was logged — never the message itself,
    since a message may carry an API error body (NFR N5, INV-02)."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failed = False

    def emit(self, record: logging.LogRecord) -> None:
        self.failed = True


def record_experiment(
    tracing_client: ExperimentRunner,
    *,
    run_name: str,
    items: list[ExperimentItem],
    task: Callable[..., Any],
    metadata: dict[str, str] | None = None,
) -> dict[str, str]:
    """Run ``task`` once per item under a single named experiment and
    return the ``item_id -> trace_id`` mapping for score writes.

    Failure detection (R5), both watched for the WHOLE call
    (``run_experiment`` plus a trailing explicit ``flush()`` — handlers
    are process-wide, so this also catches run_experiment's own internal
    flush and any background-thread export that fires mid-call):
      - an OTLP exporter ERROR -> ``FlushFailedError``;
      - a ``langfuse`` (or submodule) ERROR -> ``ExperimentRecordFailedError``.

    Positive structural check (Decision #9): ``len(item_results) ==
    len(items)``, every ``trace_id`` set, every ``dataset_run_id``
    non-null and identical across items. Any miss ->
    ``ExperimentRecordFailedError``. Never retried by this function —
    the caller (``record_run``) does not retry either (ADR-0005 #9).
    """
    otlp_watcher = _FailureWatcher()
    langfuse_watcher = _FailureWatcher()
    otlp_logger = logging.getLogger(OTLP_EXPORTER_LOGGER_NAME)
    langfuse_logger = logging.getLogger(LANGFUSE_SDK_LOGGER_NAME)
    otlp_logger.addHandler(otlp_watcher)
    langfuse_logger.addHandler(langfuse_watcher)
    # `to_raise` is set INSIDE an except block but raised OUTSIDE it
    # (below, after the `finally`) -- deliberately, to close the
    # `__context__` leak (Atchim suggestion, 2026-09-21): raising a NEW
    # exception WHILE Python is still handling `exc` makes the
    # interpreter attach `exc` to the new exception's `__context__`
    # regardless of `from None` (that only sets `__suppress_context__`,
    # which hides it from DEFAULT traceback printing -- the original
    # exception object, e.g. `RuntimeError('Bearer sk-lf-SECRET')`,
    # still lives on `.__context__` and would survive a custom
    # exc_info-walking logger, which is exactly what Atchim reproduced).
    # No exception is being handled once control reaches the deferred
    # `raise to_raise` below, so `__context__` is naturally `None` there.
    to_raise: Exception | None = None
    result: Any = None
    try:
        try:
            result = tracing_client.run_experiment(
                name=run_name,
                run_name=run_name,
                data=items,
                task=task,
                max_concurrency=1,
                metadata=metadata,
            )
        except (Exception, asyncio.CancelledError) as exc:
            # HARDEN-01 GAP-2 (2026-09-21): `run_experiment` was called
            # with no `except Exception` at all -- any exception the SDK
            # raises (transport, auth) propagated untyped straight out of
            # `record_run` and `run_eval`, the reachable production
            # trigger for GAP-1 (an untyped escape `run_eval`'s own
            # contract says is impossible). `str(exc)` is not
            # interpolated (INV-02: SDK exception text may carry
            # transport/auth response content this codebase never logs).
            # GAP-5 (Branca `/harden` re-run, 2026-09-21): `run_experiment`
            # internally uses `asyncio.gather`, which can raise
            # `asyncio.CancelledError` -- a `BaseException` subclass a
            # plain `except Exception` does NOT catch. Caught explicitly
            # here alongside `Exception`; `KeyboardInterrupt`/`SystemExit`
            # are deliberately NOT in this tuple and still propagate.
            to_raise = ExperimentRecordFailedError(
                f"record_experiment: run_experiment raised {type(exc).__name__}"
            )
        if to_raise is None:
            try:
                tracing_client.flush()
            except (Exception, asyncio.CancelledError) as exc:
                # Same GAP-2/GAP-5 findings, the flush() call -- mapped to
                # FlushFailedError specifically (not
                # ExperimentRecordFailedError) so a raised flush failure
                # is indistinguishable, at the caller, from a LOGGED one
                # (`otlp_watcher.failed` below).
                to_raise = FlushFailedError(
                    f"record_experiment: flush() raised {type(exc).__name__}"
                )
    finally:
        otlp_logger.removeHandler(otlp_watcher)
        langfuse_logger.removeHandler(langfuse_watcher)

    if to_raise is not None:
        raise to_raise

    if otlp_watcher.failed:
        raise FlushFailedError(
            "OTLP span export failed during record_experiment "
            "(see the opentelemetry exporter's own ERROR log for detail)"
        )
    if langfuse_watcher.failed:
        raise ExperimentRecordFailedError(
            "the langfuse SDK logged an ERROR during record_experiment "
            "(see the langfuse logger's own ERROR log for detail)"
        )

    # GAP-4 (Branca `/harden` re-run, 2026-09-21): the structural checks
    # below used to guard ONLY `list(result.item_results)`, with `except
    # AttributeError`. A lazily-raising `item_results` (any exception
    # type), or `.trace_id`/`.dataset_run_id`/`.item.id` access failing
    # on a drifted item shape, were unguarded and escaped raw. The WHOLE
    # block is now one try, and the same deferred-raise shape as above
    # avoids the `__context__` leak for the catch-all branch; the
    # deliberate structural-check raises (item count / missing field /
    # mismatched IDs) are re-raised as-is (they are already
    # `ExperimentRecordFailedError`, already INV-02-safe, and were never
    # raised while handling another exception, so they carry no context
    # to strip).
    structural_error: ExperimentRecordFailedError | None = None
    trace_ids: dict[str, str] = {}
    try:
        item_results = list(result.item_results)

        if len(item_results) != len(items):
            raise ExperimentRecordFailedError(
                f"record_experiment structural check failed: expected {len(items)} "
                f"item results, got {len(item_results)}"
            )

        dataset_run_ids: set[str] = set()
        for item_result in item_results:
            if not item_result.trace_id:
                raise ExperimentRecordFailedError(
                    "record_experiment structural check failed: an item result has no trace_id"
                )
            if not item_result.dataset_run_id:
                raise ExperimentRecordFailedError(
                    "record_experiment structural check failed: "
                    "an item result has no dataset_run_id"
                )
            dataset_run_ids.add(item_result.dataset_run_id)
            trace_ids[item_result.item.id] = item_result.trace_id

        if len(dataset_run_ids) != 1:
            raise ExperimentRecordFailedError(
                "record_experiment structural check failed: item results don't share a "
                f"single dataset_run_id (got {len(dataset_run_ids)} distinct values)"
            )
    except ExperimentRecordFailedError as exc:
        structural_error = exc
    except (Exception, asyncio.CancelledError) as exc:
        # A version bump that renames/removes `item_results`/`.trace_id`/
        # `.dataset_run_id`/`.item.id` (the exact class of drift
        # CLAUDE.md's SDK-internals warning names), or any other
        # unanticipated failure walking this structure, must not leak a
        # raw exception either. `type(exc).__name__` only (INV-02).
        structural_error = ExperimentRecordFailedError(
            f"record_experiment structural check failed: {type(exc).__name__} "
            "(SDK return shape drift?)"
        )

    if structural_error is not None:
        raise structural_error

    return trace_ids
