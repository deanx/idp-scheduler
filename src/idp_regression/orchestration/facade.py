"""`run_eval` facade (T-01.4.1, ADR-0004).

⚠️ **Slice boundary (S-01.4-KICKOFF.md): this batch builds ONLY the
pre-run entry checks** -- `load_dotenv()` first (INV-05), fail-closed
credential VALIDATION with no client construction (NFR N6, DEBT-30 --
see `bootstrap.py`), and run-id/experiment-name composition (T-01.4.13,
DEBT-19). The per-document run loop (get_dataset,
schema-drift, empty-set, N28 validation, the IDP/classify/record loop) is
T-01.4.2 onward and is deliberately NOT built here -- `run_eval` raises
`NotImplementedError` once the pre-run checks pass, so the boundary is
explicit rather than a silently-incomplete "success".
"""

from __future__ import annotations

import logging

from idp_regression.adapter.errors import IDPConfigurationError
from idp_regression.adapter.idp_client import make_idp_adapter
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.orchestration.bootstrap import (
    MissingCredentialError,
    validate_platform_credentials,
)
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.run_naming import compose_experiment_name, generate_run_id

logger = logging.getLogger(__name__)


def run_eval(action_id: str, version: str, run_name: str) -> int:
    """Run the baseline regression for `action_id` at `version` over the
    configured golden set, writing per-field + gate scores to a run
    derived from `run_name` on the platform (ADR-0004).

    Returns a process exit code: `0` on success, non-zero on any abort.

    ⚠️ **Slice boundary (see module docstring): this is not yet true.**
    This function currently ALWAYS raises `NotImplementedError` once its
    pre-run checks pass — there is no success path yet, because the
    per-document run loop (T-01.4.2 onward) is not built. Once it is,
    every path returns an `int` as documented above and this warning is
    removed.

    `load_dotenv()` runs FIRST, before any credential is read or any SDK
    client is constructed (ADR-0004 Flow step 1, INV-05) -- every
    adapter/platform factory this function calls documents that it never
    calls `load_dotenv()` itself; this function is that caller.

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
    except (RuntimeError, IDPConfigurationError) as exc:
        # Both already name only the offending variable, never a value
        # (INV-02) -- see make_idp_adapter's `_require`/`_timing_env`.
        logger.error("run_eval: %s", exc)
        return 1

    run_id = generate_run_id()
    experiment_name = compose_experiment_name(run_name, run_id)
    logger.info(
        "run_eval: pre-run checks passed run=%s experiment=%s action=%s version=%s",
        sanitize_for_log(run_name),
        sanitize_for_log(experiment_name),
        sanitize_for_log(action_id),
        sanitize_for_log(version),
    )

    raise NotImplementedError(
        "run_eval: pre-run credential validation is complete (T-01.4.1); "
        "the per-document run loop -- including constructing and holding "
        "the platform client -- is built in T-01.4.2 onward "
        "(see docs/specs/S-01.4-KICKOFF.md)"
    )
