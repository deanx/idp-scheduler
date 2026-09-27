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

**Completeness travels with the verdicts (DEBT-91).** An aborted run
writes an artifact too -- whatever was classified before the abort -- and
the original envelope, `{run_id: {document_id: fields}}`, did not say so.
Every reader then recomputed the gate from the documents that happened to
be present, so an abort before the first document read back as "all 0
documents passed" and `show_run` exited 0 on a run whose `run_eval`
exited 1. The envelope is now `ARTIFACT_FORMAT` and records `status`
(`complete` / `aborted`) and, for an abort, a reason from the taxonomy.
Readers go through `parse_run_artifact` + `run_level_gate`, and a run is
`PASS` only when it says it completed: an aborted run, a pre-DEBT-91
artifact that cannot say, or a document whose gate cannot be computed is
`INCOMPLETE`, never `PASS`. A known failure still reads `FAIL`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from typing import Any, Literal, NamedTuple

from idp_regression.classifier.types import VerdictMap
from idp_regression.orchestration.log_sanitize import frame_location

logger = logging.getLogger(__name__)

#: Gitignored (see `.gitignore`) — this path must never enter the repo
#: (INV-01-style leak test: `tests/orchestration/test_run_artifact.py::
#: test_artifact_directory_is_git_ignored`). Relative to the current
#: working directory of whoever invoked the run.
ARTIFACT_DIR_NAME = ".idp-regression-run-artifacts"

#: Same shape as `cli._VERSION_PATTERN`: `write_run_artifact`'s one
#: production caller always passes a `uuid4().hex` (32 lowercase hex
#: chars), but this module does not trust that -- `run_id` lands
#: directly in a filesystem path, so containment against a hostile or
#: malformed value is a property of the writer, not of its caller
#: (independent-review Suggestion 1 on 318c9ff).
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

#: Owner-only. `0700`/`0600` -- the artifact holds exactly the sensitive
#: financial values `CLAUDE.md ## Domain` calls out (bill_to, currency,
#: invoice_date, every expected/actual/confidence pair), and local disk
#: is only a narrower disclosure surface than the platform (ADR-0007) if
#: it is actually private to the invoking user, not merely off the
#: platform.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def artifact_path(run_id: str) -> str:
    """The path a given run's artifact is written to. A pure function of
    `run_id` — never logged itself (a path is not golden/actual/token
    content, but stays out of log lines on principle, like every other
    filesystem path this codebase touches, e.g. `facade._resolve_document_
    path`'s own docstring)."""
    return os.path.join(ARTIFACT_DIR_NAME, f"{run_id}.json")


#: The envelope written since DEBT-91. A top-level `format` key is what
#: tells it apart from the legacy single-key `{run_id: documents}` shape,
#: which `parse_run_artifact` still reads (status unknown).
ARTIFACT_FORMAT = "idp-regression-run-artifact/2"

RunStatus = Literal["complete", "aborted"]
RunGate = Literal["PASS", "FAIL", "INCOMPLETE"]


class RunArtifact(NamedTuple):
    run_id: str
    #: None only for a legacy artifact, which never recorded it.
    status: RunStatus | None
    abort_reason: str | None
    documents: dict[str, Any]


def artifact_envelope(
    run_id: str,
    verdict_maps: dict[str, VerdictMap] | dict[str, Any],
    *,
    status: RunStatus,
    abort_reason: str | None = None,
) -> dict[str, Any]:
    """The JSON document `write_run_artifact` writes. Pure, so a test can
    build a real artifact without re-typing the shape."""
    return {
        "format": ARTIFACT_FORMAT,
        "run_id": run_id,
        "status": status,
        "abort_reason": abort_reason if status == "aborted" else None,
        "documents": verdict_maps,
    }


def parse_run_artifact(data: Any) -> RunArtifact:
    """Read either envelope. Raises `ValueError` on anything else --
    a reader must be able to tell "not an artifact" from "a run"."""
    if isinstance(data, dict) and "format" in data:
        if data.get("format") != ARTIFACT_FORMAT:
            raise ValueError(f"unknown run artifact format {data.get('format')!r}")
        run_id, status, documents = data.get("run_id"), data.get("status"), data.get("documents")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run artifact has no run_id")
        if status not in ("complete", "aborted"):
            raise ValueError(f"run {run_id}: unknown status {status!r}")
        if not isinstance(documents, dict):
            raise ValueError(f"run {run_id} does not hold a document map")
        reason = data.get("abort_reason")
        return RunArtifact(run_id, status, reason if isinstance(reason, str) else None, documents)
    if isinstance(data, dict) and len(data) == 1:
        run_id = next(iter(data))
        documents = data[run_id]
        if not isinstance(documents, dict):
            raise ValueError(f"run {run_id} does not hold a document map")
        return RunArtifact(run_id, None, None, documents)
    raise ValueError("not a run artifact (expected a format envelope or one run id)")


def run_level_gate(document_gates: Iterable[str], status: RunStatus | None) -> RunGate:
    """The one rule every reader applies. `FAIL` if any document failed --
    a known failure stays known however the run ended. Otherwise `PASS`
    only for a run that recorded `complete` and whose every document gate
    is `PASS`; anything else (aborted, legacy/unknown, an uncomputable
    document, no documents at all) is `INCOMPLETE`. Fail-closed: the
    absence of evidence of a failure is not evidence of a pass."""
    gates = list(document_gates)
    if "FAIL" in gates:
        return "FAIL"
    if status == "complete" and gates and all(g == "PASS" for g in gates):
        return "PASS"
    return "INCOMPLETE"


def write_run_artifact(
    run_id: str,
    verdict_maps: dict[str, VerdictMap],
    *,
    status: RunStatus,
    abort_reason: str | None = None,
) -> None:
    """Write `verdict_maps` (keyed `document_id -> field -> verdict
    entry`, one entry per document processed so far — a full run's set
    on success, or whatever was collected before an abort) to
    `artifact_path(run_id)` inside `artifact_envelope`, which names the
    run in the file's own content (not merely the filename) and records
    whether it completed -- `status` is required so no call site can
    forget to say (DEBT-91).

    Best-effort: see the module docstring's "Failure posture". Never
    raises, never returns a value a caller could branch on — the whole
    point is that nothing about `run_eval`'s outcome can depend on this.

    Fail-closed on `run_id`: rejected outright (logged, no write) unless
    it matches `_RUN_ID_PATTERN` — the one shape the real caller
    (`run_naming.generate_run_id`) ever produces. This stays inside the
    same best-effort contract as every other failure here: never raises,
    never changes `run_eval`'s exit code (INV-08, CT-04).

    Writes owner-only (`0700` dir / `0600` file), tightening a
    pre-existing world-readable directory on every call — `os.makedirs`
    does not chmod an already-existing directory, so an install created
    before this fix would otherwise stay wrong forever. Opens with
    `O_NOFOLLOW` so a pre-planted symlink at the target path is refused
    rather than followed (closed defensively; `run_id` is an
    unpredictable uuid4 today, so this leg is not reachable in practice).
    """
    if not _RUN_ID_PATTERN.match(run_id):
        logger.warning(
            "run_eval: run artifact write skipped: run_id failed containment validation"
        )
        return
    path = artifact_path(run_id)
    try:
        os.makedirs(ARTIFACT_DIR_NAME, mode=_DIR_MODE, exist_ok=True)
        os.chmod(ARTIFACT_DIR_NAME, _DIR_MODE)  # makedirs ignores mode for an existing dir
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                artifact_envelope(run_id, verdict_maps, status=status, abort_reason=abort_reason),
                fh,
                indent=2,
                sort_keys=True,
            )
    except Exception as exc:  # noqa: BLE001 - best-effort by design, must never raise
        logger.warning(
            "run_eval: run artifact write failed: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
