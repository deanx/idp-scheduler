#!/usr/bin/env bash
# Local `launchd` entry point for a scheduled `run_eval` (POC/MVP path,
# user decision 2026-09-23: "I want to run everything local" -- this
# replaces GitHub Actions as architecture step 1's *scheduled* invoker;
# `.github/workflows/regression-run.yml` stays for when a hosted
# Langfuse + secrets exist, see the note at its top).
#
# Reuses `scripts/run_eval_local.sh` verbatim -- this wrapper adds only
# what a scheduled, terminal-less invocation needs on top of it:
#
#   1. Overlap prevention -- `/usr/bin/lockf` (ships with macOS; BSD
#      advisory locking per flock(2)). Chosen over a lease/PID-file
#      because the kernel releases the lock the instant the holding
#      process dies, so there is no stale-lease heuristic to tune and
#      no way for a crashed prior run to wedge every run after it --
#      the same reasoning ADR-0006 SS B.2 used for the same overlap
#      problem in the (separate, not-yet-built) unattended-watch story.
#      `-t 0`: fail immediately rather than queue, because a queued
#      second run is exactly the pile-up this guard exists to prevent.
#      `lockf`'s own exit code on a busy lock is EX_TEMPFAIL (75,
#      sysexits(3)) -- that is how this script tells "someone else is
#      already running" apart from "the run itself failed".
#
#   2. A dated log file per invocation (`logs/scheduled-runs/`) --
#      `configure_logging()` writes to stderr (landed 318c9ff) and a
#      launchd job has no terminal to show it to, so both the wrapper's
#      own narration and the CLI's stderr are redirected there.
#      `IDP_REGRESSION_LOG_LEVEL` (see CLAUDE.md) still overrides the
#      level; nothing here changes what gets logged, only where.
#
#   3. Failure visibility -- `run_eval` returns non-zero for a failing
#      gate AND for an aborted run alike (CT-04: the exit code
#      deliberately does not distinguish them, the reason is in the
#      log). There is no CI dashboard for a local POC, so failure must
#      surface without anyone having to remember to open a log:
#      a macOS user notification (`osascript display notification`) is
#      the human-facing signal -- it appears without anyone polling
#      anything, which a marker file alone does not. A same-named
#      marker file is ALSO written next to the log purely as a durable,
#      script-checkable record (a notification banner can be dismissed
#      unread) -- it is bookkeeping for the one mechanism above, not a
#      second competing mechanism. A lock-busy SKIP is not a failure
#      (see point 1's contract below) and raises neither.
#
#   4. Run naming -- `run_eval_local.sh` already generates a
#      second-resolution UTC timestamp per invocation (`local-...`);
#      `lockf -t 0` means two scheduled ticks can never be in flight at
#      the same second in the first place, so a naming collision in
#      Langfuse's run list cannot happen.
#
# Skip contract: a run that finds the lock held exits 0 and says why in
# the log -- CLAUDE.md's own instruction: "a scheduler that alerts on
# 'previous run still going' trains people to ignore it."
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$(pwd)"

LOCK_FILE="${REPO_ROOT}/.idp-regression-scheduled-run.lock"
LOG_DIR="${REPO_ROOT}/logs/scheduled-runs"
mkdir -p "${LOG_DIR}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/run-${TS}.log"
FAILURE_MARKER="${LOG_DIR}/FAILED-${TS}.marker"

#: sysexits(3) EX_TEMPFAIL -- what `lockf` exits with when the lock is
#: already held and `-t 0` gives up immediately, per lockf(1).
readonly EX_TEMPFAIL=75

{
  echo "=== idp-regression scheduled run: started ${TS} (UTC) ==="
  echo "log file: ${LOG_FILE}"
  echo "lock file: ${LOCK_FILE}"
} >>"${LOG_FILE}" 2>&1

# `-k`: keep the lock file after the command exits (removing it would
# race a second `lockf` creating a *different* inode for the same path
# between our unlink and its open -- keeping it is the documented-safe
# choice for repeated scheduled invocations, per lockf(1)'s own
# "guarantee lock ordering" example).
# `-s`: silent -- `lockf`'s own "already locked" line would otherwise
# land in the log looking like an error; this script narrates the
# skip itself, in its own words, below.
/usr/bin/lockf -k -s -t 0 "${LOCK_FILE}" \
  "${REPO_ROOT}/scripts/run_eval_local.sh" >>"${LOG_FILE}" 2>&1
status=$?

if [ "${status}" -eq "${EX_TEMPFAIL}" ]; then
  echo "=== SKIPPED: another scheduled run already holds ${LOCK_FILE} -- exiting 0 by design, this is not a failure ===" >>"${LOG_FILE}"
  exit 0
fi

echo "=== idp-regression scheduled run: finished ${TS} (UTC), exit=${status} ===" >>"${LOG_FILE}"

if [ "${status}" -ne 0 ]; then
  {
    echo "run_eval exited ${status} -- per CT-04 this means EITHER the regression gate FAILed OR the run ABORTed before a gate could be computed; see ${LOG_FILE} for which."
  } >"${FAILURE_MARKER}"
  osascript -e "display notification \"exit ${status} -- see ${LOG_FILE}\" with title \"IDP Regression scheduled run FAILED\" subtitle \"gate FAIL or run aborted (CT-04)\"" >/dev/null 2>&1 || true
fi

exit "${status}"
