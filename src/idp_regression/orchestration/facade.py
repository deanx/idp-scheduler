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

**Still NOT built** (next batch, T-01.4.7/.8/.9): the CT-04 contract
test, additional observability polish, and the e2e harness.
"""

from __future__ import annotations

import logging
import os
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
from idp_regression.orchestration.log_sanitize import format_execution_failed_status_for_log
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

#: The golden dataset name (config, not secret -- like `IDP_DOCUMENT_DIR`,
#: ADR-0004's ".env holds only environment facts" list). ⚠️ **Design call
#: for review**: no prior task pinned this name anywhere in the codebase
#: or its docs, so this is a new, minimal, fail-closed convention (N6
#: shape: missing/empty -> non-zero exit, clear message, zero network
#: calls) rather than a silent default -- a wrong silent default here
#: would run the regression against the wrong golden set with no error.
GOLDEN_DATASET_NAME_VAR = "GOLDEN_DATASET_NAME"

#: The local directory holding the document files under test
#: (ADR-0004 Flow step 5a: "the orchestrator resolves `IDP_DOCUMENT_DIR /
#: {item.document_id}` to a local path"). Read and validated fail-closed
#: (N6 shape) alongside `GOLDEN_DATASET_NAME_VAR`, before any network call.
IDP_DOCUMENT_DIR_VAR = "IDP_DOCUMENT_DIR"


def _resolve_document_path(document_dir: str, document_id: str) -> str:
    """Resolve `document_id` to a local path under `IDP_DOCUMENT_DIR`
    (ADR-0004 Flow step 5a). The orchestrator owns this resolution; the
    adapter takes only an already-resolved path (INV-01 -- the platform
    stores `document_id` only, and no path blob ever reaches it; this
    function is purely local composition, no I/O of its own).

    ⚠️ **Design call for review**: this is a plain `os.path.join`, the
    minimal convention pinned by ADR-0004. `document_id` is platform
    (golden-set) content, already pre-run-validated as a non-empty string
    by `validate_golden_set` (N28), but that check is about JSON shape,
    not filesystem safety -- it does not rule out e.g. a `document_id`
    containing `../`. No stricter containment (basename-only, a resolved-
    path-stays-under-`document_dir` assertion) is applied here because no
    task pinned one; flagged so a reviewer can decide whether the golden
    set is a trusted-enough input for that gap to be acceptable for MVP.
    """
    return os.path.join(document_dir, document_id)


def _mark_run_status_best_effort(
    platform: PlatformAdapter,
    run_id: str,
    status: str,
    *,
    action_id: str,
    action_version: str,
    golden_version: str,
) -> None:
    """ADR-0004 #14 (Atchim non-blocking debt, carried forward by
    ADR-0005 #9 steps 3/4/5): the `run_status` marker write is
    best-effort -- ONE attempt, never retried. The exit code is the CI
    gate's truth regardless of whether this marker lands on the
    platform; a failure here must never itself abort or crash
    `run_eval`. Never logs the caught exception's message (INV-02 -- a
    platform error body could echo request content)."""
    try:
        platform.mark_run_status(
            run_id,
            status,  # type: ignore[arg-type]
            action_id=action_id,
            action_version=action_version,
            golden_version=golden_version,
        )
    except Exception:  # noqa: BLE001 - best-effort by design, must never raise
        logger.warning(
            "run_eval: mark_run_status(%s) failed (best-effort, not retried)",
            sanitize_for_log(status),
        )


def run_eval(action_id: str, version: str, run_name: str) -> int:
    """Run the baseline regression for `action_id` at `version` over the
    configured golden set, writing per-field + gate scores to a run
    derived from `run_name` on the platform (ADR-0004, ADR-0005 #9).

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
    load_dotenv()

    try:
        validate_platform_credentials()
    except MissingCredentialError as exc:
        logger.error("run_eval: missing required env var %s", exc.variable_name)
        return 1

    try:
        idp_adapter: MuleSoftIDPAdapter = make_idp_adapter()
    except (RuntimeError, IDPConfigurationError, ValueError) as exc:
        # `ValueError` closes a Required gate finding (2026-09-21,
        # live-reproduced): `MuleSoftIDPAdapter.__init__` raises a raw
        # `ValueError` when `success_statuses` is not a subset of
        # `terminal_statuses` (adapter/idp_client.py:109), which escaped
        # this except-block and broke the `-> int` / ADR-0004 exit-code
        # contract. All three exception types already name only the
        # offending variable/config, never a value (INV-02) -- see
        # make_idp_adapter's `_require`/`_timing_env` and
        # MuleSoftIDPAdapter's `_validate_timing`. `str(exc)` is still
        # wrapped in `sanitize_for_log` defensively (reviewer
        # suggestion): today's messages are safe by construction, but
        # this is the one exception-message boundary in this function
        # not otherwise routed through it, and that safety is not
        # guaranteed to hold for every future raiser of these types.
        logger.error("run_eval: %s", sanitize_for_log(str(exc)))
        return 1

    dataset_name = (os.environ.get(GOLDEN_DATASET_NAME_VAR) or "").strip()
    if not dataset_name:
        logger.error("run_eval: missing required env var %s", GOLDEN_DATASET_NAME_VAR)
        return 1

    document_dir = (os.environ.get(IDP_DOCUMENT_DIR_VAR) or "").strip()
    if not document_dir:
        logger.error("run_eval: missing required env var %s", IDP_DOCUMENT_DIR_VAR)
        return 1

    try:
        platform: PlatformAdapter = make_platform()
    except (ValueError, PlatformConfigurationError) as exc:
        # Mirrors the make_idp_adapter except-block above: names only the
        # offending variable/config (INV-02), still routed through
        # sanitize_for_log defensively.
        logger.error("run_eval: %s", sanitize_for_log(str(exc)))
        return 1

    try:
        dataset = platform.get_dataset(dataset_name)
    except DatasetFetchFailedError as exc:
        logger.error("run_eval: dataset_fetch_failed: %s", sanitize_for_log(str(exc)))
        return 1

    # Pinned pre-run order (S-01.4-KICKOFF.md, TP-40): schema-drift, THEN
    # empty-set, THEN N28 structural validation. An empty dataset whose
    # schema ALSO drifted reports schema_drift, because check_schema_drift
    # runs first and never looks at `items`.
    try:
        check_schema_drift(dataset)
        check_empty_set(dataset)
        validate_golden_set(dataset)
    except RunAborted as exc:
        logger.error("run_eval: %s: %s", exc.reason, sanitize_for_log(str(exc)))
        return 1

    # T-01.4.6 (INV-04): golden_version is a content hash over the SAME
    # `dataset["items"]` object just validated above -- no second fetch
    # (TOCTOU guard, ADR-0005 #7). `hash_dataset` takes `list[dict[str,
    # Any]]` (it hashes whatever it is given verbatim, no opinion on item
    # shape -- see its own docstring); `DatasetItem` is structurally a
    # dict, so this is a shape-preserving cast, not an unsafe one.
    golden_version = hash_dataset(cast(list[dict[str, Any]], dataset["items"]))

    run_id = generate_run_id()
    experiment_name = compose_experiment_name(run_name, run_id)
    logger.info(
        "run_eval: pre-run checks passed run=%s experiment=%s action=%s "
        "version=%s golden_version=%s",
        sanitize_for_log(run_name),
        sanitize_for_log(experiment_name),
        sanitize_for_log(action_id),
        sanitize_for_log(version),
        sanitize_for_log(golden_version),
    )

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
    try:
        for item in dataset["items"]:
            document_id = item["document_id"]
            golden = item["golden"]
            document_path = _resolve_document_path(document_dir, document_id)

            try:
                actual = idp_adapter.extract(document_path, action_id, version)
            except IDPAuthenticationError as exc:
                raise _abort("auth_failure", document_id, str(exc)) from None
            except IDPPollTimeoutError as exc:
                raise _abort("unknown_status_timeout", document_id, str(exc)) from None
            except MalformedIDPOutputError as exc:
                raise _abort(
                    "malformed_actual", document_id, f"{exc.reason}: {exc}"
                ) from None
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
            except MalformedGoldenError as exc:
                raise _abort("malformed_golden", document_id, str(exc)) from None
            except MalformedActualError as exc:
                raise _abort("malformed_actual", document_id, str(exc)) from None

            if gate == "FAIL":
                any_gate_failed = True

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
            logger.info(
                "run_eval: document processed document_id=%s gate=%s",
                sanitize_for_log(document_id),
                gate,
            )

        # ADR-0005 #9 step 4: a single record_run call, after the loop.
        metadata: RunMetadata = {
            "action_id": action_id,
            "action_version": version,
            "golden_version": golden_version,
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
    )

    return 1 if any_gate_failed else 0
