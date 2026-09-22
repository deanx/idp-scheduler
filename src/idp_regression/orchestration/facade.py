"""`run_eval` facade (T-01.4.1, T-01.4.11, T-01.4.2, T-01.4.5, T-01.4.6,
ADR-0004, ADR-0005 Decision #8).

⚠️ **Slice boundary (S-01.4-KICKOFF.md), updated for this batch.** This
function now builds the ENTIRE pre-run chain, in the pinned order
(S-01.4-KICKOFF.md, TP-40)::

    load_dotenv() -> credential validation (N6) -> IDP config validation
    -> platform construction -> get_dataset (dataset_fetch_failed)
    -> schema-drift check (schema_drift) -> empty-set guard (empty_set)
    -> N28 structural validation (malformed_golden)
    -> golden_version = hash_dataset(...) + run_id/experiment_name

**This is a change from the T-01.4.1 batch's boundary**, which
deliberately did NOT construct a platform client (DEBT-30/C-1/R-3 fix).
That constraint applied ONLY to the credential-presence check itself --
it was never a claim that `run_eval` would stay client-free forever.
Fetching the golden dataset (T-01.4.11 onward) genuinely needs a real
`PlatformAdapter`, so `make_platform()` is now called here, strictly
AFTER credential validation has already confirmed presence (defence in
depth: a missing/empty var is still caught before this point, so this
call should never itself hit a bare `KeyError`).

**Still NOT built**: the per-document loop -- `extract` -> `classify` ->
`overall_gate` -> `DocumentRecord` accumulation -> the single post-loop
`record_run` -- and the abort reasons that belong to it (`hard_failure`,
`auth_failure`, `unknown_status_timeout`, `flush_failed`). `run_eval`
raises `NotImplementedError` once every pre-run check passes, so that
boundary stays explicit rather than a silently-incomplete "success"
(T-01.4.3a onward).
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from idp_regression.adapter.errors import IDPConfigurationError
from idp_regression.adapter.idp_client import make_idp_adapter
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.orchestration.bootstrap import (
    MissingCredentialError,
    validate_platform_credentials,
)
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.errors import RunAborted
from idp_regression.orchestration.prerun import (
    check_empty_set,
    check_schema_drift,
    validate_golden_set,
)
from idp_regression.orchestration.run_naming import compose_experiment_name, generate_run_id
from idp_regression.platform import make_platform
from idp_regression.platform.errors import DatasetFetchFailedError, PlatformConfigurationError
from idp_regression.platform.hashing import hash_dataset

logger = logging.getLogger(__name__)

#: The golden dataset name (config, not secret -- like `IDP_DOCUMENT_DIR`,
#: ADR-0004's ".env holds only environment facts" list). ⚠️ **Design call
#: for review**: no prior task pinned this name anywhere in the codebase
#: or its docs, so this is a new, minimal, fail-closed convention (N6
#: shape: missing/empty -> non-zero exit, clear message, zero network
#: calls) rather than a silent default -- a wrong silent default here
#: would run the regression against the wrong golden set with no error.
GOLDEN_DATASET_NAME_VAR = "GOLDEN_DATASET_NAME"


def run_eval(action_id: str, version: str, run_name: str) -> int:
    """Run the baseline regression for `action_id` at `version` over the
    configured golden set, writing per-field + gate scores to a run
    derived from `run_name` on the platform (ADR-0004).

    Returns a process exit code: `0` on success, non-zero on any abort.

    ⚠️ **Slice boundary (see module docstring): this is not yet fully
    true.** This function currently ALWAYS raises `NotImplementedError`
    once every pre-run check passes — there is no success path yet,
    because the per-document run loop (T-01.4.3a onward) is not built.
    Once it is, every path returns an `int` as documented above and this
    warning is removed.

    `load_dotenv()` runs FIRST, before any credential is read or any SDK
    client is constructed (ADR-0004 Flow step 1, INV-05). Credential
    PRESENCE is validated (`bootstrap.validate_platform_credentials`, N6)
    before `make_platform()` is ever called, so a missing/empty var is
    still caught with zero network calls -- `make_platform()` itself is
    now called here (updated 2026-09-21, this batch: T-01.4.11 needs a
    real `PlatformAdapter` to fetch the golden dataset), strictly after
    that presence check, as defence in depth against the one thing
    presence validation cannot catch: a value that resolves but is
    internally contradictory (`PlatformConfigurationError`, the platform's
    own base-URL/host split-brain guard; N24 -- this module names no
    vendor).

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
        make_idp_adapter()
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

    try:
        platform = make_platform()
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

    raise NotImplementedError(
        "run_eval: the pre-run chain is complete (T-01.4.1/.11/.2/.5/.6) -- "
        "get_dataset, schema-drift, empty-set, N28 structural validation, "
        "golden_version + run_id derivation; the per-document run loop "
        "(extract -> classify -> overall_gate -> DocumentRecord "
        "accumulation -> a single post-loop record_run) is built in "
        "T-01.4.3a onward (see docs/specs/S-01.4-KICKOFF.md)"
    )
