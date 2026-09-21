"""Run-id generation and platform experiment-name composition.

T-01.4.13 (DEBT-19, NFR N26). Live-confirmed in S-01.3 (TP-37): two
``record_run`` calls with the SAME ``run_name`` but different ``run_id``
merge into ONE experiment on the evaluation platform — ``itemCount`` sums
across invocations rather than producing two separate experiments. Score
collisions do NOT happen (``score_id`` is ``run_id``-scoped), so this is
purely an experiments-tab/observability concern, not a data-safety one —
but a `run_name` generation scheme that ever reuses a name would blend
unrelated runs in the UI (N24: the platform is swappable — see
`idp_regression.platform`, ADR-0005).

The fix is composition: the orchestrator never passes the operator's
``--run`` value straight through as the platform ``run_name``. It always
suffixes it with the first 8 hex chars of a fresh, per-invocation
``run_id`` (ADR-0005 #9 step 4 amendment), so two invocations under the
same ``--run`` value produce two distinct experiments.
"""

from __future__ import annotations

import uuid


def generate_run_id() -> str:
    """A fresh, per-invocation run id — 32 lowercase hex chars (uuid4).

    This is the SAME `run_id` later passed to `platform.record_run(...)`
    (ADR-0005 #9), from which every score id is deterministically derived
    (S-01.3, N26) — generating it once, here, at the top of the run is
    load-bearing, not cosmetic.
    """
    return uuid.uuid4().hex


def compose_experiment_name(run_name: str, run_id: str) -> str:
    """Compose the platform experiment name from the operator's `--run`
    value and this invocation's `run_id` (DEBT-19). A `run_id[:8]`
    collision would only re-merge two experiments in the Experiments tab
    — it can never overwrite a score, since score ids derive from the
    FULL `run_id`, never from `run_name` (S-01.3, N26)."""
    return f"{run_name}-{run_id[:8]}"
