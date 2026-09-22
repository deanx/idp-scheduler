"""`run_eval` facade (T-01.4.1, T-01.4.11, T-01.4.2, T-01.4.5, T-01.4.6,
ADR-0004, ADR-0005 Decisions #8/#9).

⚠️ **Slice boundary (S-01.4-KICKOFF.md), updated for this batch.** This
function now builds the FULL run: the pre-run chain, the per-document
loop, and the post-loop record phase (S-01.4-KICKOFF.md, TP-40;
ADR-0005 #9)::

    load_dotenv() -> credential validation (N6) -> IDP config validation
    -> IDP_DOCUMENT_DIR validation -> platform construction
    -> get_dataset (dataset_fetch_failed) -> schema-drift (schema_drift)
    -> empty-set guard (empty_set) -> N28 structural validation
       (malformed_golden)
    -> quota-ceiling pre-flight guard (quota_ceiling_exceeded, ADR-0004
       A10, 2026-09-22) -- zero quota spent on a refusal
    -> golden_version = hash_dataset(...) + run_id/experiment_name
    -> per document, sequentially: resolve document_id -> path
       -> extract -> classify -> overall_gate -> build_score_inputs
       -> append DocumentRecord (NO platform write in this loop, #9 step 2;
          any typed failure aborts the WHOLE run immediately, INV-06)
    -> a single post-loop record_run(...) (#9 step 4)
    -> mark_run_status("complete", ...) (best-effort, #9 step 5)
    -> exit 0 iff every gate was PASS and no error occurred (INV-08: the
       exit code is a pure function of the in-process gates, never of
       what landed on the platform)

**This is a change from the T-01.4.1 batch's boundary**, which
deliberately did NOT construct a platform client (DEBT-30/C-1/R-3 fix).
That constraint applied ONLY to the credential-presence check itself --
it was never a claim that `run_eval` would stay client-free forever.

**Still NOT built** (next batch, T-01.4.7/.9): the CT-04 contract
test and the e2e harness. Observability (T-01.4.8, NFR N10) IS built as
of this batch -- a run-start line (item count), a run-end line (outcome,
exit code, pass/fail counts, elapsed on a monotonic clock -- emitted at
EVERY exit point, not only on success), and per-document elapsed
attached to the SAME log line that already carries `document_id` (never
a separate untethered timing line). Hard rule, asserted by
`tests/orchestration/test_facade.py`: no golden value, extracted value,
token or path ever reaches any of these lines.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, cast

from idp_regression.adapter.errors import (
    IDPAdapterError,
    IDPAuthenticationError,
    IDPConfigurationError,
    IDPExecutionFailedError,
    IDPPollTimeoutError,
    MalformedIDPOutputError,
)
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter, make_idp_adapter
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.classifier.gate import classify, overall_gate
from idp_regression.classifier.types import MalformedActualError, MalformedGoldenError
from idp_regression.orchestration.bootstrap import (
    MissingCredentialError,
    validate_platform_credentials,
)
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.errors import AbortReason, RunAborted
from idp_regression.orchestration.log_sanitize import (
    format_execution_failed_status_for_log,
    frame_location,
)
from idp_regression.orchestration.prerun import (
    check_empty_set,
    check_schema_drift,
    validate_golden_set,
)
from idp_regression.orchestration.run_naming import compose_experiment_name, generate_run_id
from idp_regression.platform import make_platform
from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    ExperimentRecordFailedError,
    FlushFailedError,
    PlatformConfigurationError,
    ScoreWriteFailedError,
)
from idp_regression.platform.hashing import hash_dataset
from idp_regression.platform.scoring import build_score_inputs
from idp_regression.platform.types import DocumentRecord, PlatformAdapter, RunMetadata

logger = logging.getLogger(__name__)

#: ⚠️ Updated 2026-09-22 (user decision): `GOLDEN_DATASET_NAME` is no
#: longer read anywhere in this codebase, not even as a fallback --
#: `--dataset` is a required CLI flag with no environment fallback
#: (`cli.py::main`), and `run_eval` itself takes `dataset_name` as a
#: required parameter. `GOLDEN_DATASET_NAME` survives only as a
#: test-harness convenience var (see `.env.example`), unread by the app.
#: The `GOLDEN_DATASET_NAME_VAR` constant that used to live here (ADR-0004
#: amendment T-01.4.12 A6 / DEBT-48) is retired along with the fallback it
#: named -- nothing in `src/` reads it anymore.

#: The local directory holding the document files under test
#: (ADR-0004 Flow step 5a: "the orchestrator resolves `IDP_DOCUMENT_DIR /
#: {item.document_id}` to a local path"). Read and validated fail-closed
#: (N6 shape), before any network call.
IDP_DOCUMENT_DIR_VAR = "IDP_DOCUMENT_DIR"


class _PathContainmentViolation(Exception):
    """Raised by `_resolve_document_path` when `document_id` would resolve
    outside `IDP_DOCUMENT_DIR` -- an absolute `document_id`, a `..`
    traversal, or a symlink escape (security fix, 2026-09-21: N28
    validates JSON shape only, never filesystem safety). Carries the
    offending `document_id` as `.document_id` -- callers must route it
    through `sanitize_for_log` before it ever reaches a log line or
    exception message (INV-02); the resolved/candidate path itself is
    NEVER attached to this exception or logged anywhere."""

    def __init__(self, document_id: str) -> None:
        super().__init__("document_id resolved outside IDP_DOCUMENT_DIR")
        self.document_id = document_id


def _resolve_document_path(document_dir: str, document_id: str) -> str:
    """Resolve `document_id` to a local path under `IDP_DOCUMENT_DIR`
    (ADR-0004 Flow step 5a). The orchestrator owns this resolution; the
    adapter takes only an already-resolved path (INV-01 -- the platform
    stores `document_id` only, and no path blob ever reaches it; this
    function is purely local composition plus a containment check, no
    I/O of its own beyond `os.path.realpath`'s symlink resolution).

    **Containment (security fix, 2026-09-21)**: `document_id` is platform
    (golden-set) content -- data a Curator or anyone with platform write
    access controls. ⚠️ Corrected 2026-09-21 (R-1 gate finding): this
    docstring used to claim `document_id` was "already pre-run-validated
    as a non-empty string by `validate_golden_set` (N28)" -- that was
    false. `validate_golden_set` (`prerun.py`) validates `item["golden"]`
    only; it never inspects `document_id`, so a non-`str`, empty, or
    NUL-containing `document_id` reached here unvalidated and escaped as
    a raw `TypeError`/`ValueError` (2026-09-21 live repro). This
    function's FIRST statement below now rejects exactly those three
    shapes itself, as `_PathContainmentViolation`, before any `os.path`
    call. Both the configured root and the candidate are then resolved
    to real absolute paths (`os.path.realpath` -- this also
    resolves a symlink to its real target, so a symlink planted *inside*
    `document_dir` that points *outside* it is caught, not just a literal
    `..` in `document_id`), and the candidate must land strictly inside
    the root (`root + os.sep` prefix -- the root itself is never a valid
    resolution, since it is a directory, not a document). An absolute
    `document_id` is rejected up front for a clearer failure signal, even
    though the containment check below would also catch it (`os.path.join`
    discards `document_dir` entirely when its second argument is
    absolute, so an unchecked absolute `document_id` would otherwise
    resolve to itself verbatim).

    Raises `_PathContainmentViolation` (never returns a path outside the
    root) -- the caller (`run_eval`'s per-document loop) converts this
    into a typed `path_containment_violation` abort (CT-04: 0-vs-non-zero
    exit-code contract stays intact) and logs the `document_id` only via
    `sanitize_for_log`, never the resolved/candidate path.
    """
    if not isinstance(document_id, str) or not document_id or "\x00" in document_id:
        # R-1: reject non-`str`, empty, and NUL-containing `document_id`
        # up front -- none of these are safe to hand to `os.path.isabs`/
        # `os.path.realpath` below, which raise raw `TypeError`/
        # `ValueError` on exactly these shapes instead of the typed
        # `_PathContainmentViolation` this function otherwise always
        # raises. `document_id` may not be a `str` at all here (the type
        # hint is aspirational, not enforced at this boundary), so no
        # f-string/`sanitize_for_log` call touches it before this check.
        raise _PathContainmentViolation(document_id if isinstance(document_id, str) else "")

    if os.path.isabs(document_id):
        raise _PathContainmentViolation(document_id)

    root = os.path.realpath(document_dir)
    candidate = os.path.realpath(os.path.join(document_dir, document_id))
    if not candidate.startswith(root + os.sep):
        raise _PathContainmentViolation(document_id)
    return candidate


def _validate_dataset_shape(dataset: object) -> None:
    """HARDEN-01 GAP-1 (2026-09-21): the orchestrator used to trust the
    `Dataset`/`DatasetItem` shape the `PlatformAdapter` returned at face
    value -- a non-list `items`, a missing `items` key, an item missing
    `document_id`, or a non-`str` `document_id` all raised a raw
    `KeyError`/`TypeError`/`AttributeError` deep inside `check_empty_set`,
    `validate_golden_set`, or the per-document loop, escaping `run_eval`
    entirely (breaking its own `-> int` contract, per Branca's `/harden`
    report). Validated here, BEFORE the schema-drift/empty-set/N28 chain
    even runs, and mapped to `DatasetFetchFailedError` -- the same
    reason `get_dataset` itself already uses for a malformed response,
    since a structurally-invalid dataset is exactly that class of
    failure regardless of which layer detects it. This checks SHAPE
    only (types, key presence) -- it never inspects `golden` content
    (N28's job) and never a `value` (INV-02)."""
    if not isinstance(dataset, dict):
        raise DatasetFetchFailedError("get_dataset returned a non-dict dataset")
    items = dataset.get("items")
    if not isinstance(items, list):
        raise DatasetFetchFailedError("dataset 'items' is missing or not a list")
    for item in items:
        if not isinstance(item, dict):
            raise DatasetFetchFailedError("a dataset item is not a dict")
        if not isinstance(item.get("item_id"), str) or not item["item_id"]:
            raise DatasetFetchFailedError("a dataset item has a missing/non-string item_id")
        if not isinstance(item.get("document_id"), str) or not item["document_id"]:
            raise DatasetFetchFailedError("a dataset item has a missing/non-string document_id")


def _mark_run_status_best_effort(
    platform: PlatformAdapter,
    run_id: str,
    status: str,
    *,
    action_id: str,
    action_version: str,
    golden_version: str,
    golden_dataset_name: str,
) -> None:
    """ADR-0004 #14 (Atchim non-blocking debt, carried forward by
    ADR-0005 #9 steps 3/4/5): the `run_status` marker write is
    best-effort -- ONE attempt, never retried. The exit code is the CI
    gate's truth regardless of whether this marker lands on the
    platform; a failure here must never itself abort or crash
    `run_eval`. Never logs the caught exception's message (INV-02 -- a
    platform error body could echo request content).

    `golden_dataset_name` (A6 / DEBT-48): the fourth INV-04 field,
    carried alongside the other three so the marker also answers "which
    named golden set was this run measured against".

    ⚠️ Widened 2026-09-21 (Atchim gate, GAP-5's fifth site,
    live-reproduced): this guarded only `except Exception`, unlike every
    other catch-all in this module (all widened for GAP-5 to also catch
    `asyncio.CancelledError`, a `BaseException` subclass). The tail
    `status="complete"` call site (after a fully successful run) sits
    OUTSIDE every try-block in `run_eval`, so a `CancelledError` raised
    here on that call escaped `run_eval` raw. Now matches every other
    catch-all's shape: `KeyboardInterrupt`/`SystemExit` still propagate,
    everything else is swallowed best-effort."""
    try:
        platform.mark_run_status(
            run_id,
            status,  # type: ignore[arg-type]
            action_id=action_id,
            action_version=action_version,
            golden_version=golden_version,
            golden_dataset_name=golden_dataset_name,
        )
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 - best-effort by design, must never raise
        logger.warning(
            "run_eval: mark_run_status(%s) failed (best-effort, not retried)",
            sanitize_for_log(status),
        )


#: ADR-0004 A10 (2026-09-22), MVP override of A10's own "required, no
#: default" text (user decision): a round, obviously-arbitrary number so
#: its arbitrariness is impossible to miss -- this bounds a runaway loop
#: (an unexpectedly huge golden set, a future resubmit path), it is NOT
#: derived from the org's real IDP allotment, and it must never be
#: presented as a real quota control. See `cli.py`'s `--max-documents-
#: per-run` help text and the run-start log line, which always records
#: the value actually in force.
DEFAULT_MAX_DOCUMENTS_PER_RUN = 1000


def run_eval(
    action_id: str,
    version: str,
    run_name: str,
    dataset_name: str,
    org_id: str,
    max_documents_per_run: int = DEFAULT_MAX_DOCUMENTS_PER_RUN,
) -> int:
    """Run the baseline regression for `action_id` at `version` over the
    named golden set (`dataset_name`), writing per-field + gate scores to
    a run derived from `run_name` on the platform (ADR-0004, ADR-0005 #9).

    `dataset_name` (ADR-0004 amendment T-01.4.12 A6 / DEBT-48; tightened
    2026-09-22 -- user decision): a required, plain parameter -- like
    `action_id`/`version` above, this function does NOT read
    `GOLDEN_DATASET_NAME` from the environment, and neither does the CLI
    anymore: `--dataset` is a required flag with no env fallback
    (`cli.py::main`), exactly mirroring `--action`'s own required-flag
    shape. A caller of this public function directly (bypassing the CLI)
    must supply `dataset_name` explicitly.

    `org_id` (ADR-0004 A9, 2026-09-22 -- amends A8): likewise a required,
    plain parameter, passed straight through to `make_idp_adapter(org_id)`
    below -- `IDP_ORG_ID` is read by no production code path anymore. An
    action is addressed by `(org, action, version)`, and the credential
    alone cannot tell a wrong org id from a right one (IDP access is
    granted at the business-group level) -- so the org must be visible in
    the invocation, exactly like `action_id`/`dataset_name`.

    `max_documents_per_run` (ADR-0004 A10, 2026-09-22): a pre-flight
    ceiling on documents submitted this run, checked once `get_dataset`
    has returned and before any IDP submit call (a refusal costs zero
    quota). Defaults to `DEFAULT_MAX_DOCUMENTS_PER_RUN` -- an MVP guard
    rail against a runaway loop, deliberately NOT derived from the org's
    real IDP allotment (see the constant's own docstring and `cli.py`'s
    `--max-documents-per-run` help text).

    Returns a process exit code: `0` iff every document's gate was
    `PASS` and no error occurred anywhere in the run; non-zero on any
    gate `FAIL` or any abort (CT-04 -- 0 vs non-zero only, the abort
    *reason* is logged for observability and never fragments the exit
    code, `orchestration/errors.py`).

    `load_dotenv()` runs FIRST, before any credential is read or any SDK
    client is constructed (ADR-0004 Flow step 1, INV-05). Credential
    PRESENCE is validated (`bootstrap.validate_platform_credentials`, N6)
    before `make_platform()` is ever called, so a missing/empty var is
    still caught with zero network calls -- `make_platform()` itself is
    called strictly after that presence check, as defence in depth
    against the one thing presence validation cannot catch: a value that
    resolves but is internally contradictory (`PlatformConfigurationError`,
    the platform's own base-URL/host split-brain guard; N24 -- this
    module names no vendor).

    Callers are expected to have already validated `action_id` (UUID) and
    `version` (`^[A-Za-z0-9._-]{1,64}$`) at the CLI boundary
    (ADR-0004 amendment 2026-09-19) -- this function trusts its inputs
    and does not re-validate their shape.
    """
    started_at = time.monotonic()

    def _log_run_end(
        outcome: str, exit_code: int, *, pass_count: int = 0, fail_count: int = 0
    ) -> None:
        """T-01.4.8 (NFR N10): emitted at EVERY exit point of `run_eval`,
        not only on success (Zangado's S-01.2 note) -- `outcome`/
        `exit_code`/the counts are fixed, non-secret values, and
        `elapsed_seconds` comes from stdlib's monotonic clock, so this
        line needs no `sanitize_for_log` pass (nothing here is a golden
        value, an extracted value, a token, or a path)."""
        logger.info(
            "run_eval: run_end outcome=%s exit_code=%s pass_count=%d fail_count=%d "
            "elapsed_seconds=%.3f",
            outcome,
            exit_code,
            pass_count,
            fail_count,
            time.monotonic() - started_at,
        )

    # C-1 (Atchim gate, 2026-09-21) widened after a `/harden` re-run
    # (Branca) found the first merge (`get_dataset` +
    # shape/schema/empty-set/N28) closed only ONE of five raw-escape
    # seams: `make_platform`, `make_idp_adapter` and
    # `validate_platform_credentials` each still sat under their OWN
    # narrow tuple with no catch-all, and the window between the two
    # former try-blocks -- `hash_dataset`, `generate_run_id`,
    # `compose_experiment_name` -- had no try at all. ONE try-block now
    # spans every pre-run step, `validate_platform_credentials` through
    # `compose_experiment_name`, so an untyped exception raised ANYWHERE
    # in that chain (a transport timeout, a version-drift attribute
    # error, ...) is caught by the SAME trailing catch-all instead of
    # escaping through whichever seam's tuple didn't happen to name it.
    # No `run_id` exists at any point in this block (it is the last
    # thing generated inside it), so no branch here ever writes the
    # best-effort marker -- unlike the in-loop/record-phase catch-all
    # further down, which always has a `run_id` to mark.
    #
    # ⚠️ Widened again 2026-09-21 (Atchim gate, live-reproduced): this try
    # used to START one line too late -- `load_dotenv()` itself sat
    # OUTSIDE it, so an unreadable/undecodable `.env` (e.g. a
    # permission-denied `OSError`) escaped `run_eval` raw. It also used
    # to END too early, right after `compose_experiment_name()` -- the
    # "pre-run checks passed" log line below (specifically its own
    # `len(dataset["items"])` call) sat in the WINDOW between this
    # try-block and the per-document loop's, with only `_abort`'s
    # definition next to it. The try now starts at `load_dotenv()` and
    # ends only after that log line is fully emitted, so nothing sits
    # between the two catch-alls except the `_abort` closure's own
    # definition (never executed at definition time).
    try:
        load_dotenv()

        validate_platform_credentials()

        idp_adapter: MuleSoftIDPAdapter = make_idp_adapter(org_id)

        # A6: `dataset_name` is caller-supplied now (see the docstring
        # above) -- still fail-closed on an empty/whitespace-only value,
        # the same N6 shape the env-read version used, so a caller that
        # passes "" through doesn't reach any network call either. Not
        # an exception (nothing to catch below) -- a plain early return.
        dataset_name = dataset_name.strip()
        if not dataset_name:
            logger.error("run_eval: dataset_name must not be empty")
            _log_run_end("aborted", 1)
            return 1

        document_dir = (os.environ.get(IDP_DOCUMENT_DIR_VAR) or "").strip()
        if not document_dir:
            logger.error("run_eval: missing required env var %s", IDP_DOCUMENT_DIR_VAR)
            _log_run_end("aborted", 1)
            return 1

        platform: PlatformAdapter = make_platform()

        dataset = platform.get_dataset(dataset_name)
        # GAP-1: validate shape BEFORE trusting it structurally anywhere
        # else -- same except-clause, same reason, as a malformed
        # `get_dataset` response.
        _validate_dataset_shape(dataset)
        # Pinned pre-run order (S-01.4-KICKOFF.md, TP-40): schema-drift,
        # THEN empty-set, THEN N28 structural validation. An empty
        # dataset whose schema ALSO drifted reports schema_drift,
        # because check_schema_drift runs first and never looks at
        # `items`.
        check_schema_drift(dataset)
        check_empty_set(dataset)
        validate_golden_set(dataset)

        # ADR-0004 A10 (2026-09-22): pre-flight IDP quota ceiling -- item
        # count is known now (get_dataset already ran) and no submit has
        # happened yet, so a refusal here costs ZERO quota. Deliberately
        # placed BEFORE golden_version/run_id below (no run exists yet),
        # so this is a pre-run guard per A7: log, run_end outcome=aborted,
        # return 1, NO run_status marker -- not a RunAborted/`_abort()`
        # call, which both require a `run_id` to mark.
        #
        # ⚠️ User override of A10's own text (2026-09-22, MVP decision):
        # A10 specifies this ceiling as REQUIRED with NO DEFAULT ("a
        # guessed ceiling is worse than none because it looks like a
        # control"). The user overrode that for the MVP: `--max-
        # documents-per-run` is OPTIONAL with a high, deliberately
        # arbitrary default (`DEFAULT_MAX_DOCUMENTS_PER_RUN`, `cli.py`) --
        # this bounds a runaway loop (a golden set that unexpectedly holds
        # thousands of items, or a future resubmit path), it is NOT
        # derived from the org's real IDP allotment and must never be
        # presented as one. B-3 (`/signoff`, N27) stays OPEN as a
        # validation point, not closed by this default.
        item_count = len(dataset["items"])
        if item_count > max_documents_per_run:
            logger.error(
                "run_eval: quota_ceiling_exceeded item_count=%d max_documents_per_run=%d",
                item_count,
                max_documents_per_run,
            )
            _log_run_end("aborted", 1)
            return 1

        # T-01.4.6 (INV-04): golden_version is a content hash over the
        # SAME `dataset["items"]` object just validated above -- no
        # second fetch (TOCTOU guard, ADR-0005 #7). `hash_dataset` takes
        # `list[dict[str, Any]]` (it hashes whatever it is given
        # verbatim, no opinion on item shape -- see its own docstring);
        # `DatasetItem` is structurally a dict, so this is a
        # shape-preserving cast, not an unsafe one.
        golden_version = hash_dataset(cast(list[dict[str, Any]], dataset["items"]))
        run_id = generate_run_id()
        experiment_name = compose_experiment_name(run_name, run_id)

        logger.info(
            "run_eval: pre-run checks passed run=%s experiment=%s action=%s "
            "version=%s golden_version=%s golden_dataset_name=%s items=%d "
            "max_documents_per_run=%d",
            sanitize_for_log(run_name),
            sanitize_for_log(experiment_name),
            sanitize_for_log(action_id),
            sanitize_for_log(version),
            sanitize_for_log(golden_version),
            sanitize_for_log(dataset_name),
            len(dataset["items"]),
            max_documents_per_run,
        )
    except MissingCredentialError as exc:
        logger.error("run_eval: missing required env var %s", exc.variable_name)
        _log_run_end("aborted", 1)
        return 1
    except (RuntimeError, IDPConfigurationError, ValueError, PlatformConfigurationError) as exc:
        # `ValueError` closes a Required gate finding (2026-09-21,
        # live-reproduced): `MuleSoftIDPAdapter.__init__` raises a raw
        # `ValueError` when `success_statuses` is not a subset of
        # `terminal_statuses` (adapter/idp_client.py:109), which escaped
        # this except-block and broke the `-> int` / ADR-0004 exit-code
        # contract.
        #
        # ⚠️ Corrected 2026-09-21 (Branca `/harden` GAP-6, live-reproduced,
        # 8 cases): this clause used to log `sanitize_for_log(str(exc))`
        # on the assumption that only two vetted constructors
        # (`make_idp_adapter`'s and `make_platform`'s own guards) ever
        # raised these types here -- true when this except-clause was
        # first written, but 7d7aed3 later widened the SAME try-block to
        # span the whole pre-run chain, including `check_schema_drift`
        # and `validate_golden_set`, both of which handle golden content
        # and can raise a bare `RuntimeError`/`ValueError` built from it.
        # `sanitize_for_log` only escapes/quotes (`json.dumps`) -- it does
        # NOT redact -- so a golden value or a credential sentinel
        # embedded in one of those messages reached the log verbatim
        # aside from quoting. `RecursionError` (a `RuntimeError`
        # subclass) made this worse: a stack overflow deep in golden
        # validation landed in THIS clause, not the safe type-name-only
        # catch-all below. Now logs only `type(exc).__name__` plus the
        # last traceback frame's location (R-2 shape, same as every
        # catch-all in this function) -- never `str(exc)`, for ANY
        # raiser of these four types, vetted or not.
        logger.error(
            "run_eval: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1)
        return 1
    except DatasetFetchFailedError as exc:
        logger.error("run_eval: dataset_fetch_failed: %s", sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1)
        return 1
    except RunAborted as exc:
        logger.error("run_eval: %s: %s", exc.reason, sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1)
        return 1
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - HARDEN-01 GAP-1/GAP-5
        # `type(exc).__name__` plus the last traceback frame's location
        # (R-2) only, never `str(exc)` (INV-02: an unanticipated
        # exception's message is not vetted the way every typed one in
        # this codebase is).
        #
        # ⚠️ Widened 2026-09-21 (Branca `/harden` GAP-5): `asyncio.
        # CancelledError` is a `BaseException` subclass (Python 3.8+), NOT
        # an `Exception` subclass -- it used to slip straight through
        # `except Exception`, escaping this catch-all raw. Added
        # explicitly (never a bare `except BaseException`, which would
        # also swallow `KeyboardInterrupt`/`SystemExit` -- those must
        # keep propagating).
        logger.error(
            "run_eval: unexpected pre-run error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1)
        return 1

    def _abort(reason: AbortReason, document_id: str | None, detail: str) -> RunAborted:
        """Write the best-effort `run_status=aborted` marker (a run now
        exists -- `run_id` was just generated above, unlike the pre-run
        guards) then build (not raise -- see call sites) the `RunAborted`
        to propagate, so every caller's line is `raise _abort(...)` and
        stays a single statement, consistent with the pre-run guards'
        style. `detail` MUST already be `sanitize_for_log`-clean; this
        function logs it via a `%s` placeholder, never interpolates it
        into the exception message itself (INV-02 -- see `RunAborted`'s
        own docstring)."""
        _mark_run_status_best_effort(
            platform,
            run_id,
            "aborted",
            action_id=action_id,
            action_version=version,
            golden_version=golden_version,
            golden_dataset_name=dataset_name,
        )
        logger.error(
            "run_eval: %s document_id=%s detail=%s",
            reason,
            sanitize_for_log(document_id) if document_id is not None else "<none>",
            sanitize_for_log(detail),
        )
        return RunAborted(reason, f"per-document/record-phase abort: {reason}")

    # ADR-0005 #9 step 2: sequential, in-process loop. NO platform write
    # happens inside it -- every gate is computed and every DocumentRecord
    # is fully built BEFORE record_run is ever called once, after the
    # loop (INV-08). ADR-0005 #9 step 3 / INV-06: any typed failure here
    # aborts the WHOLE run immediately -- no remaining document is
    # processed, and the loop's own `except` blocks below are the only
    # path out of it.
    records: list[DocumentRecord] = []
    any_gate_failed = False
    passed_count = 0
    failed_count = 0
    submits_made = 0
    try:
        for item in dataset["items"]:
            document_started_at = time.monotonic()
            document_id = item["document_id"]
            golden = item["golden"]
            try:
                document_path = _resolve_document_path(document_dir, document_id)
            except _PathContainmentViolation as exc:
                raise _abort(
                    "path_containment_violation", exc.document_id, "containment check failed"
                ) from None

            # ADR-0004 A10: "documents submitted, counted at the submit
            # call site" -- one `extract()` call is one IDP execution is
            # one document, so the counter increments immediately before
            # the call that actually submits, not before path resolution
            # (a path-containment rejection never reaches the IDP) and
            # not derived from `len(dataset["items"])`, so any future
            # resubmit/retry path is counted by construction. The
            # assertion is a BUG DETECTOR, not a policy (A10): the
            # pre-flight check above already refused any dataset whose
            # item count exceeds the ceiling, so `submits_made` can never
            # legitimately reach it mid-loop -- if it ever fires, the
            # pre-flight computation was wrong, not the operator's budget.
            submits_made += 1
            assert submits_made <= max_documents_per_run, (
                "internal invariant violated: submits_made exceeded "
                "max_documents_per_run despite the pre-flight guard"
            )
            try:
                actual = idp_adapter.extract(document_path, action_id, version)
            except IDPAuthenticationError as exc:
                raise _abort("auth_failure", document_id, str(exc)) from None
            except IDPPollTimeoutError as exc:
                raise _abort("unknown_status_timeout", document_id, str(exc)) from None
            except MalformedIDPOutputError as exc:
                raise _abort("malformed_actual", document_id, f"{exc.reason}: {exc}") from None
            except IDPExecutionFailedError as exc:
                # `.status` is IDP-controlled -- already sanitized at
                # construction (adapter/errors.py) and again here through
                # the shared T-01.4.14 helper (defense in depth, N5).
                raise _abort(
                    "hard_failure", document_id, format_execution_failed_status_for_log(exc)
                ) from None
            except IDPAdapterError as exc:
                # Every other typed adapter error: submit failure, a
                # non-2xx poll response, retry-budget exhaustion, or an
                # ambiguous/missing poll status.
                raise _abort("hard_failure", document_id, str(exc)) from None

            try:
                verdicts = classify(golden, actual)
                gate = overall_gate(verdicts)
            except MalformedGoldenError as exc:  # pragma: no cover
                # Coverage audit gap 2 (2026-09-21): provably unreachable,
                # not merely untested -- `validate_golden_set` (N28,
                # `prerun.py`) runs `validate_golden_structure` (the
                # `is`-identical alias of this SAME `_validate_golden`
                # function `classify()` calls) over EVERY item's `golden`
                # in the pre-run chain, BEFORE the per-document loop ever
                # starts, and aborts `malformed_golden` on the first
                # failure. So by the time this line runs, `golden` has
                # already passed the exact validator `classify()` is
                # about to run again -- it cannot raise here. Kept only
                # as defense-in-depth against N28/N22 ever drifting apart
                # (they can't, by construction: `validate_golden_structure
                # = _validate_golden`), not because this branch is
                # expected to fire. `except MalformedActualError` right
                # below IS reachable and IS pinned by
                # `test_facade.py::test_run_eval_aborts_malformed_actual_from_classify`
                # -- `actual` comes from a live per-document IDP
                # extraction, never pre-validated by any N28-equivalent
                # pass.
                raise _abort("malformed_golden", document_id, str(exc)) from None
            except MalformedActualError as exc:
                raise _abort("malformed_actual", document_id, str(exc)) from None

            if gate == "FAIL":
                any_gate_failed = True
                failed_count += 1
            else:
                passed_count += 1

            scores = build_score_inputs(
                golden=golden,
                verdicts=verdicts,
                gate=gate,
                run_id=run_id,
                document_id=document_id,
            )
            records.append(
                {"item_id": item["item_id"], "document_id": document_id, "scores": scores}
            )
            # T-01.4.8: elapsed for THIS document, on the SAME log line
            # that already carries its document_id (Zangado's S-01.2 note
            # -- a timing line must be linked to the document it
            # describes, not a separate untethered one).
            logger.info(
                "run_eval: document processed document_id=%s gate=%s elapsed_seconds=%.3f",
                sanitize_for_log(document_id),
                gate,
                time.monotonic() - document_started_at,
            )

        # ADR-0005 #9 step 4: a single record_run call, after the loop.
        metadata: RunMetadata = {
            "action_id": action_id,
            "action_version": version,
            "golden_version": golden_version,
            "golden_dataset_name": dataset_name,
        }
        try:
            platform.record_run(
                dataset_name=dataset_name,
                run_name=experiment_name,
                run_id=run_id,
                records=records,
                metadata=metadata,
            )
        except FlushFailedError as exc:
            raise _abort("flush_failed", None, str(exc)) from None
        except (ExperimentRecordFailedError, ScoreWriteFailedError) as exc:
            # ADR-0005 #9 step 4: "any other platform error -> hard_failure".
            raise _abort("hard_failure", None, str(exc)) from None
    except RunAborted as exc:
        logger.error("run_eval: %s: %s", exc.reason, sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1, pass_count=passed_count, fail_count=failed_count)
        return 1
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - HARDEN-01 GAP-1/GAP-5
        # Unlike the pre-run catch-all above, `run_id` DOES exist here
        # (generated before this try-block) -- ADR-0004 #14 says every
        # run that exists gets a best-effort `aborted` marker, so this
        # writes one, exactly like every `_abort()` call site above,
        # before ever logging or returning. `type(exc).__name__` plus the
        # last traceback frame's location (R-2) only, never `str(exc)`
        # (INV-02 -- an untyped exception's message is not vetted the way
        # every typed one this codebase raises is; it could echo IDP or
        # platform response content). Widened 2026-09-21 (Branca `/harden`
        # GAP-5) to also catch `asyncio.CancelledError` -- see the
        # pre-run catch-all's comment above for why.
        _mark_run_status_best_effort(
            platform,
            run_id,
            "aborted",
            action_id=action_id,
            action_version=version,
            golden_version=golden_version,
            golden_dataset_name=dataset_name,
        )
        logger.error(
            "run_eval: unexpected error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1, pass_count=passed_count, fail_count=failed_count)
        return 1

    # ADR-0005 #9 step 5: mark_run_status("complete", ...), best-effort --
    # INV-08 holds trivially, the exit code below never reads this marker
    # or anything else from the platform, only the in-process gates.
    _mark_run_status_best_effort(
        platform,
        run_id,
        "complete",
        action_id=action_id,
        action_version=version,
        golden_version=golden_version,
        golden_dataset_name=dataset_name,
    )

    exit_code = 1 if any_gate_failed else 0
    _log_run_end(
        "gate_failed" if any_gate_failed else "success",
        exit_code,
        pass_count=passed_count,
        fail_count=failed_count,
    )
    return exit_code
