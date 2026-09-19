"""Typed platform errors — mappable to the orchestrator's abort-reason taxonomy.

Never include a raw HTTP response body or an Authorization header in an
error message: golden-shaped values or the platform's 400 validation body
can carry sensitive content (NFR N5, INV-02), and the response is redacted
at the logging boundary before any of it reaches an exception message.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base for typed platform-adapter errors."""


class DatasetFetchFailedError(PlatformError):
    """``get_dataset`` failed (network/404/auth) — maps to abort `dataset_fetch_failed`."""


class ScoreWriteFailedError(PlatformError):
    """A score write failed after the adapter's own bounded retry (REG-03,
    F-2; ADR-0005 #9 — the score_id is deterministic, so a retry is an
    upsert). The adapter already exhausted 5xx/transport retries or hit a
    non-retried 4xx before raising; the orchestrator treats this as
    `hard_failure` and aborts, it does not retry `record_run` itself."""


class FlushFailedError(PlatformError):
    """``flush()`` failed — maps to abort `flush_failed`. Never best-effort."""


class RunStatusWriteFailedError(PlatformError):
    """The run_status marker write failed."""


class TracingNotConfiguredError(PlatformError):
    """A tracing-dependent operation (T-01.3.10a) was called without a
    configured ``tracing_client`` (OTLP/v4 SDK)."""


class ExperimentRecordFailedError(PlatformError):
    """``record_run`` (ADR-0005 Decision #9) failed: the ``langfuse``
    logger recorded an ERROR (item/run-item-create failure), the task
    itself failed (defense-in-depth path), or the post-call structural
    check (item count / trace_id / dataset_run_id) didn't hold. Never
    retried — see ADR-0005 #9 flow step 4."""


class TransportError(PlatformError):
    """The raw-REST transport failed before an HTTP status was even
    returned (timeout, connection error) — R7. Callers map this to their
    own typed error (``DatasetFetchFailedError``, etc.)."""
