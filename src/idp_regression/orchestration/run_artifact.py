"""ADR-0007 Option E (**ACCEPTED 2026-09-23**, user decision): the local,
gitignored per-run artifact.

**What this persists.** `classifier.gate.classify()` already returns the
full CT-02 verdict map — `{field_name: {verdict, expected, actual,
confidence, critical, type}}` — for every document. `facade.py`'s
per-document loop used it to compute the gate and `build_score_inputs`,
then dropped it on the floor once those were built. This module writes
that same, already-computed map to a local file — **no new extraction,
no new fetch, no new computation.**

**The platform stays value-free.** `## Domain` and DEBT-18 option B
(user decision 2026-09-19) are UNCHANGED by this ADR and this module: no
actual, expected, or confidence value is written to the evaluation
platform by anything in this codebase. This module writes to a **local
file only** — a different, local-disk trust boundary the ADR analyzed
explicitly (§2 Option A cost analysis, ADR-0007).

**Option D's fingerprint is deferred, not implemented here.** Nothing in
this module changes what reaches the platform's `output` map.

**Path.** One JSON file per run, named after `run_id` (already a
collision-resistant identifier, see `run_naming.generate_run_id`), under
`ARTIFACT_DIR_NAME`, relative to the current working directory the
caller (`cli.main()` / a direct `run_eval()` caller) was invoked from —
i.e. wherever the run actually happened, human laptop or CI runner.

**Retention / deletion policy.** No automatic pruning in this slice —
retention is manual. `ARTIFACT_DIR_NAME` holds nothing but these
per-run JSON files and has no other consumer, so it is always safe to
delete the whole directory, or any single run's file inside it, at any
time an operator is done inspecting it (INV-01-style containment: a
bounded, single-purpose directory, not a general scratch area).

**Publishing this artifact from CI is explicitly out of scope** (ADR-0007
§"What implementation owes"): a CI-run artifact lands on the runner, not
the reviewer's laptop, and publishing it as an org-readable CI build
artifact would be a *worse* disclosure surface than the self-hosted
platform this ADR was written to avoid writing sensitive values to. Only
the run artifact WRITER lives here; nothing in this codebase uploads it
anywhere.

**Failure posture.** Best-effort, mirroring `facade._mark_run_status_
best_effort`: a write failure (disk full, permission denied, a symlink
race) is caught, logged as a warning (type name + frame location only,
never the raw exception message — INV-02, the message could echo a
path), and swallowed. `write_run_artifact` NEVER raises and NEVER
influences `run_eval`'s exit code — the gate is the contract (INV-08,
CT-04), and a debugging convenience must not be able to turn an
otherwise-passing run into a failed one, or vice versa.
"""

from __future__ import annotations

import json
import logging
import os

from idp_regression.classifier.types import VerdictMap
from idp_regression.orchestration.log_sanitize import frame_location

logger = logging.getLogger(__name__)

#: Gitignored (see `.gitignore`) — this path must never enter the repo
#: (INV-01-style leak test: `tests/orchestration/test_run_artifact.py::
#: test_artifact_directory_is_git_ignored`). Relative to the current
#: working directory of whoever invoked the run.
ARTIFACT_DIR_NAME = ".idp-regression-run-artifacts"


def artifact_path(run_id: str) -> str:
    """The path a given run's artifact is written to. A pure function of
    `run_id` — never logged itself (a path is not golden/actual/token
    content, but stays out of log lines on principle, like every other
    filesystem path this codebase touches, e.g. `facade._resolve_document_
    path`'s own docstring)."""
    return os.path.join(ARTIFACT_DIR_NAME, f"{run_id}.json")


def write_run_artifact(run_id: str, verdict_maps: dict[str, VerdictMap]) -> None:
    """Write `verdict_maps` (keyed `document_id -> field -> verdict
    entry`, one entry per document processed so far — a full run's set
    on success, or whatever was collected before an abort) to
    `artifact_path(run_id)`, wrapped one level further by `run_id` itself
    (`{run_id: {document_id: {field: verdict_entry}}}`) so the file's own
    top-level content states which run it is, matching the ADR's literal
    "keyed run_id -> document_id -> field" address, not merely the
    filename.

    Best-effort: see the module docstring's "Failure posture". Never
    raises, never returns a value a caller could branch on — the whole
    point is that nothing about `run_eval`'s outcome can depend on this.
    """
    path = artifact_path(run_id)
    try:
        os.makedirs(ARTIFACT_DIR_NAME, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({run_id: verdict_maps}, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001 - best-effort by design, must never raise
        logger.warning(
            "run_eval: run artifact write failed: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
