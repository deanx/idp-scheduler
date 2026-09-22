"""Abort-reason taxonomy for `run_eval` (T-01.4.2, ADR-0004 Containment,
ADR-0005 Decisions #8/#9).

`RunAborted` is raised by every guard in `run_eval`'s flow -- the
pre-run chain (`get_dataset` -> schema-drift -> empty-set -> N28
structural validation -> `golden_version`/`run_id`), the per-document
loop (`extract` -> `classify` -> `overall_gate` -> `build_score_inputs`,
ADR-0005 #9 step 2/3), and the single post-loop `record_run` (#9 step
4) -- pinned by `docs/specs/S-01.4-KICKOFF.md`. `run_eval` (`facade.py`)
catches it at the top level and converts it into a non-zero exit
(CT-04) -- no `RunAborted`, or any other exception, may escape
`run_eval` itself. The abort reason is logged for observability; it
does NOT fragment the exit-code namespace (ADR-0004 Sec. Exit-code
contract stays 0/non-zero).

**Reason -> exit mapping (this batch, T-01.4.6/.2 complete):**
- `dataset_fetch_failed` / `schema_drift` / `empty_set` /
  `malformed_golden` -- pre-run guards (unchanged from the prior batch).
- `auth_failure` -- `IDPAuthenticationError` (initial or mid-run refresh
  auth failure, ADR-0004 #7).
- `unknown_status_timeout` -- `IDPPollTimeoutError` (poll budget expired
  before a terminal status arrived, ADR-0004 #17).
- `hard_failure` -- every other typed `IDPAdapterError` (submit failure,
  a non-2xx poll response, retry-budget exhaustion, a terminal
  non-success status, an ambiguous/missing poll status), and any
  `record_run` raise OTHER than `FlushFailedError`
  (`ExperimentRecordFailedError` / `ScoreWriteFailedError`, ADR-0005 #9
  step 4: "any other platform error -> hard_failure").
- `malformed_actual` -- `MalformedIDPOutputError` (raised by
  `adapter.normalize()` inside `extract()`) or `MalformedActualError`
  (raised by the classifier's own N22 defense-in-depth check) -- an
  actual shape the pre-run N28 golden check could never have caught,
  because it validates the golden, not what the IDP call returns.
- `flush_failed` -- `FlushFailedError` from `record_run` (ADR-0005 #9
  step 4, superseding ADR-0004 #13's bounded flush retry -- never
  retried).
- `path_containment_violation` -- `facade._PathContainmentViolation`: a
  `document_id` (golden-set content) resolved outside `IDP_DOCUMENT_DIR`
  -- an absolute path, `..` traversal, or a symlink escape (security fix,
  2026-09-21; N28 validates JSON shape only, never filesystem safety).
  Raised BEFORE any IDP call for that document -- the adapter never sees
  the escaped path.
- `quota_ceiling_exceeded` -- ADR-0004 A10 (2026-09-22): the dataset's
  item count exceeds `--max-documents-per-run` (an MVP guard rail, NOT
  derived from the org's real IDP allotment -- see A10). A PRE-RUN guard
  (sits after `get_dataset`/`empty_set`/N28, before `run_id` exists), so
  per A7 it never reaches `RunAborted` -- `run_eval` logs this reason
  directly, writes no `run_status` marker, and returns 1. Listed here
  anyway (Hyrum's-Law note, A10): `AbortReason` is a `Literal`, so this
  is an ADDITIVE member for producers, but it BREAKS any consumer doing
  exhaustive matching -- the taxonomy must be treated as open.
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
    "malformed_actual",
    "flush_failed",
    "path_containment_violation",
    "quota_ceiling_exceeded",
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
