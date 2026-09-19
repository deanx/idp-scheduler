"""OTLP trace + dataset-run linkage (T-01.3.10a, ADR-0005 #6 F3).

Empirically probed 2026-09-19 against the live self-hosted Langfuse
4.38.0 (events_only): a *manual* OTel span + ``POST
/api/public/dataset-run-items`` link does NOT make the run appear under
the dataset's Experiments tab (``GET /api/public/experiments``), even
with the same ``traceId`` attached to a score and the public
``LangfuseOtelSpanAttributes.EXPERIMENT_*`` attributes set by hand. Only
the SDK's own ``Langfuse.run_experiment(...)`` — which drives the
per-item task execution itself and sets private linkage internals we
should not reimplement — produced a run visible in
``GET /api/public/experiments`` (confirmed within 5s). This module wraps
that proven path rather than reinventing it from OTel primitives.

``Langfuse.flush()`` was also probed against an unreachable host: it
never raises — export failures are only logged (WARNING/ERROR) by the
``opentelemetry.exporter.otlp.proto.http.trace_exporter`` logger and
silently swallowed. TP-43 forbids best-effort flush, so ``flush()``
here watches that logger during the flush call and raises
``FlushFailedError`` if it emitted an ERROR record — the only failure
signal the OTel SDK's ``BatchSpanProcessor.force_flush()`` surfaces
(its own return value is True even on export failure, also probed).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from idp_regression.platform.errors import FlushFailedError

#: The OTel OTLP HTTP exporter's logger — the only observable signal of
#: an export failure once inside Langfuse.flush() (probed 2026-09-19; the
#: SDK's own flush()/force_flush() never raise or return False on export
#: failure).
OTLP_EXPORTER_LOGGER_NAME = "opentelemetry.exporter.otlp.proto.http.trace_exporter"


class Flushable(Protocol):
    def flush(self) -> None: ...


class ExperimentRunner(Protocol):
    def run_experiment(
        self,
        *,
        name: str,
        run_name: str,
        data: list[Any],
        task: Callable[..., Any],
        max_concurrency: int = 1,
    ) -> Any: ...


class TracingClient(Flushable, ExperimentRunner, Protocol):
    """The seam this module depends on — mocked in unit tests. The real
    ``langfuse.Langfuse`` client naturally satisfies this structurally."""


class _ExportFailureWatcher(logging.Handler):
    """Records whether the OTLP exporter logged an ERROR during flush()."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failed = False

    def emit(self, record: logging.LogRecord) -> None:
        self.failed = True


def flush_or_raise(tracing_client: Flushable) -> None:
    """Flush the tracing client; raise ``FlushFailedError`` if the OTLP
    exporter logged an export failure during the flush (TP-43 — never
    best-effort)."""
    watcher = _ExportFailureWatcher()
    exporter_logger = logging.getLogger(OTLP_EXPORTER_LOGGER_NAME)
    exporter_logger.addHandler(watcher)
    try:
        tracing_client.flush()
    finally:
        exporter_logger.removeHandler(watcher)
    if watcher.failed:
        raise FlushFailedError(
            "OTLP span export failed during flush() "
            "(see the opentelemetry exporter's own ERROR log for detail)"
        )


def run_dataset_experiment(
    tracing_client: ExperimentRunner,
    *,
    run_name: str,
    dataset_items: list[Any],
    task: Callable[..., Any],
) -> Any:
    """Run ``task`` once per dataset item under a single named experiment,
    visible in the dataset's Experiments tab (live-proven path — see the
    module docstring for why the manual span+dataset-run-item approach
    was rejected). ``max_concurrency=1`` keeps iteration sequential so
    the orchestrator's abort-on-failure semantics (INV-06) are not
    undermined by concurrent task execution.
    """
    return tracing_client.run_experiment(
        name=run_name,
        run_name=run_name,
        data=dataset_items,
        task=task,
        max_concurrency=1,
    )
