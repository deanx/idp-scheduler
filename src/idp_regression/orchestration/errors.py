"""Abort-reason taxonomy for `run_eval` (T-01.4.2, ADR-0004 Containment,
ADR-0005 Decision #8).

`RunAborted` is raised by each guard in the pre-run chain (`get_dataset`
-> schema-drift -> empty-set -> N28 structural validation ->
`golden_version`/`run_id` -> first IDP call), pinned by
`docs/specs/S-01.4-KICKOFF.md`. `run_eval` (`facade.py`) catches it at
the top level and converts it into a non-zero exit (CT-04) -- no
`RunAborted`, or any other exception, may escape `run_eval` itself. The
abort reason is logged for observability; it does NOT fragment the
exit-code namespace (ADR-0004 Sec. Exit-code contract stays 0/non-zero).

**Slice boundary:** this batch (T-01.4.11/.2/.5/.6) only RAISES the
pre-run reasons -- `dataset_fetch_failed`, `schema_drift`, `empty_set`,
`malformed_golden`. The per-document-loop reasons (`hard_failure`,
`auth_failure`, `unknown_status_timeout`, `flush_failed`) are declared
here for the taxonomy's shape (CT-04, NFR N10) but are not yet raised
anywhere -- that is T-01.4.3a/3b/7 onward.
"""

from __future__ import annotations

from typing import Literal

AbortReason = Literal[
    "dataset_fetch_failed",
    "schema_drift",
    "empty_set",
    "malformed_golden",
    "hard_failure",
    "auth_failure",
    "unknown_status_timeout",
    "flush_failed",
]


class RunAborted(Exception):
    """A pre-run (or, eventually, per-document) guard aborted the run.

    `reason` is one of `AbortReason` (ADR-0004 NFR N10, extended by
    ADR-0005 Decision #8). The message passed to `__init__` must never
    contain a golden value, an extracted value, or a credential (INV-02)
    -- callers route any caller-controlled string through
    `sanitize_for_log` before it reaches this constructor or a log line.
    """

    def __init__(self, reason: AbortReason, message: str) -> None:
        super().__init__(message)
        self.reason: AbortReason = reason
