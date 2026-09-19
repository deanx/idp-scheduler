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
    """A score write failed — the orchestrator decides retry-vs-abort `hard_failure`."""


class FlushFailedError(PlatformError):
    """``flush()`` failed — maps to abort `flush_failed`. Never best-effort."""


class RunStatusWriteFailedError(PlatformError):
    """The run_status marker write failed."""
