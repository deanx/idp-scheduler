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
#      already running" apart from "the run itself failed" apart from
#      "lockf itself could not execute at all" (F-1 below).
#
#   2. A dated log file per invocation (`logs/scheduled-runs/`) --
#      `configure_logging()` writes to stderr (landed 318c9ff) and a
#      launchd job has no terminal to show it to, so both the wrapper's
#      own narration and the CLI's stderr are redirected there.
#      `IDP_REGRESSION_LOG_LEVEL` (see CLAUDE.md) still overrides the
#      level; nothing here changes what gets logged, only where.
#      F-2 hardening (resilience re-review, 2026-09-23): this log
#      inherits whatever `run_eval_local.sh` writes to its own stdout,
#      which can include credential-bearing diagnostics if `.env` is
#      malformed (see that script's M3/F-2 notes) -- `umask 077` plus an
#      unconditional `chmod 0700` on the log directory keep the whole
#      tree non-world/group-readable regardless of the process umask
#      that created it or the directory's prior mode (the launchd
#      plist's own `Umask 0077` does NOT cover this: it applies only to
#      launchd-started runs and only to *new* files, so an
#      already-0755 `logs/` from an earlier run, or a manually
#      `mkdir`'d one, stays 0755 without this explicit fix -- the exact
#      trap 852e06f had to close with an unconditional `chmod`).
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
# 'previous run still going' trains people to ignore it." That contract
# covers ONLY a genuinely busy lock (lockf's own EX_TEMPFAIL). Anything
# else that stops the wrapped command from ever starting -- a missing or
# non-executable `lockf` binary, a permissions error, any other exec
# failure -- is an infrastructure failure, not a skip, and must be
# reported the same way a failing gate is (F-1 below).
set -uo pipefail

#: sysexits(3) EX_TEMPFAIL -- what `lockf` exits with when the lock is
#: already held and `-t 0` gives up immediately, per lockf(1). This is
#: the ONLY exit code that means "someone else is already running";
#: every other lockf exit means lockf itself failed to do its job.
readonly EX_TEMPFAIL=75

#: Overridable so tests can point at a deliberately-missing binary to
#: exercise the F-1 "lockf could not execute at all" path without
#: needing to tamper with the real macOS `/usr/bin/lockf`. Production
#: callers never set this; it defaults to the real thing.
LOCKF_BIN="${LOCKF_BIN:-/usr/bin/lockf}"

# notify_failure SUBTITLE BODY LOG_FILE
#
# The one place this script talks to `osascript`. Text is always passed
# as an ARGUMENT to a fixed AppleScript handler (`on run argv`), never
# string-concatenated into the `-e` source -- a `"` in a value used to
# terminate the AppleScript string literal early (reproduced: a
# `qu"ote` checkout path both broke the notification AND, crafted
# further, executed injected AppleScript; M4 fix). `osascript`'s own
# failure (non-zero exit, e.g. an ungranted Notification Center/TCC
# permission under launchd) is never swallowed by `|| true` -- a failed
# delivery is itself a logged line, not silence, because the
# notification is the only human-facing signal this design relies on.
notify_failure() {
  local subtitle="$1" body="$2" log_file="$3"
  if ! osascript \
      -e 'on run argv' \
      -e 'display notification (item 2 of argv) with title "IDP Regression scheduled run FAILED" subtitle (item 1 of argv)' \
      -e 'end run' \
      -- "${subtitle}" "${body}" >/dev/null 2>&1; then
    echo "=== notification delivery FAILED (osascript nonzero) -- the failure marker is the durable record; no user notification was shown for this run ===" >>"${log_file}"
  fi
}

# classify_and_report STATUS_RAW LOG_FILE FAILURE_MARKER
#
# Interprets the child's captured exit status and reports pass/fail
# accordingly, then echoes (on its own stdout) the exit code this
# wrapper itself should use. Pulled out of `main` as its own function
# so it is directly testable with a crafted status value, independent
# of the lockf/subprocess plumbing that produces one in production.
#
# F-3 fix (resilience re-review, 2026-09-23, reproduced: an empty or
# non-numeric status file -- partial write, full disk, FS error --
# used to make `[ "${status}" -ne 0 ]` itself error, so the `if` was
# never entered and a genuinely failed child produced no marker and no
# notification; observed live: child exited 4, wrapper rc 255, nothing
# signalled). Fail-closed: an unparseable status is treated as a
# failure, never as silence.
classify_and_report() {
  local status_raw="$1" log_file="$2" failure_marker="$3"

  if ! [[ "${status_raw}" =~ ^[0-9]+$ ]]; then
    echo "=== idp-regression scheduled run: status file was corrupt/unreadable (raw: '${status_raw}') -- fail-closed, treating this as a FAILURE ===" >>"${log_file}"
    {
      echo "the child's status file was empty or non-numeric (raw: '${status_raw}') -- a partial write, full disk or filesystem error must not silently read as green (CLAUDE.md ## Rigor: silently-wrong GREEN builds are the worst failure this system can produce)."
    } >"${failure_marker}"
    notify_failure "status file corrupt/unreadable" "see ${log_file}" "${log_file}"
    echo 1
    return
  fi

  echo "=== idp-regression scheduled run: finished, exit=${status_raw} ===" >>"${log_file}"

  if [ "${status_raw}" -ne 0 ]; then
    {
      echo "run_eval exited ${status_raw} -- per CT-04 this means EITHER the regression gate FAILed OR the run ABORTed before a gate could be computed; see ${log_file} for which."
    } >"${failure_marker}"
    notify_failure "gate FAIL or run aborted (CT-04)" "exit ${status_raw} -- see ${log_file}" "${log_file}"
  fi

  echo "${status_raw}"
}

main() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  REPO_ROOT="$(pwd)"

  LOCK_FILE="${REPO_ROOT}/.idp-regression-scheduled-run.lock"
  LOG_DIR="${REPO_ROOT}/logs/scheduled-runs"

  # F-2 hardening: restrict new files by default, and force the
  # directory itself closed regardless of who created it or when --
  # see the header note above for why both are needed.
  umask 077
  mkdir -p "${LOG_DIR}"
  chmod 0700 "${LOG_DIR}"

  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  # DEBT-113: the timestamp alone has one-second resolution, so a manual run
  # and a launchd run starting in the same second shared a log, a status file
  # and a failure marker -- the lock-busy one could delete the holder's status
  # file, or report the holder's result as its own. The PID is unique among
  # processes alive at the same moment, which is exactly the collision case.
  RUN_KEY="${TS}-$$"
  LOG_FILE="${LOG_DIR}/run-${RUN_KEY}.log"
  FAILURE_MARKER="${LOG_DIR}/FAILED-${RUN_KEY}.marker"

  # L9 fix (resilience review, 2026-09-23, reproduced live with
  # `lockf … /bin/bash -c 'exit 75'`): the prior version read `lockf`'s own
  # exit status and the wrapped command's real exit status off the SAME
  # variable, so a child that legitimately exits 75 (unrelated to this
  # script's own sysexits use) was indistinguishable from "lock busy" -- a
  # fail-open: the wrapper printed SKIPPED and exited 0 even though
  # `run_eval_local.sh` actually ran and failed with a real 75.
  #
  # Fix: the child's real exit status is captured to a status file INSIDE
  # the locked region, on its own channel -- `lockf`'s own exit code is
  # used ONLY to detect "did the wrapped command run at all", never to
  # stand in for the command's own exit status.
  STATUS_FILE="${LOG_DIR}/.status-${RUN_KEY}"
  rm -f "${STATUS_FILE}"

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
  # `bash -c '"$0" ...; echo $? > "$1"'` runs INSIDE the lock: the real
  # child status lands in STATUS_FILE only if the wrapped command actually
  # started, so its presence is what tells "the lock was busy" apart from
  # "the child ran and (maybe) itself exited 75" -- not lockf's own exit
  # code, which stays EX_TEMPFAIL either way it's read.
  "${LOCKF_BIN}" -k -s -t 0 "${LOCK_FILE}" \
    /bin/bash -c '"$0" >>"$1" 2>&1; echo "$?" > "$2"' \
    "${REPO_ROOT}/scripts/run_eval_local.sh" "${LOG_FILE}" "${STATUS_FILE}"
  lockf_status=$?

  if [ ! -f "${STATUS_FILE}" ]; then
    # F-1 fix (resilience re-review, 2026-09-23, reproduced by pointing
    # `LOCKF_BIN` at a non-existent binary: `lockf_MISSING: No such file
    # or directory`, `rc=127`, and the PRE-FIX script reported this the
    # exact same way as a busy lock -- "SKIPPED ... exiting 0 by design"
    # -- so a broken `lockf` install would make the regression never run
    # on every tick, forever, with launchd recording success and no
    # marker and no notification. `EX_TEMPFAIL` was declared and never
    # compared against; that omission is what let the two cases collapse.
    #
    # Fix: the absence of a status file means "the wrapped command never
    # started", which is EITHER a genuinely busy lock (lockf's own exit
    # is exactly EX_TEMPFAIL) OR an infrastructure failure (any other
    # exit -- missing binary, exec permission, anything else). Only the
    # first is a skip; the second is reported exactly like a failing
    # gate.
    if [ "${lockf_status}" -eq "${EX_TEMPFAIL}" ]; then
      echo "=== SKIPPED: another scheduled run already holds ${LOCK_FILE} (lockf exit ${lockf_status}) -- exiting 0 by design, this is not a failure ===" >>"${LOG_FILE}"
      exit 0
    fi

    echo "=== FAILED: ${LOCKF_BIN} did not run the wrapped command at all (exit ${lockf_status}; a busy lock exits exactly ${EX_TEMPFAIL}) -- treating this as an infrastructure failure, not a skip ===" >>"${LOG_FILE}"
    {
      echo "${LOCKF_BIN} exited ${lockf_status} without ever starting ${REPO_ROOT}/scripts/run_eval_local.sh. A busy lock exits exactly ${EX_TEMPFAIL} (sysexits EX_TEMPFAIL); any other code means lockf itself could not do its job (missing binary, exec permission, or another infrastructure fault), which must never be silently folded into the busy-lock skip path."
    } >"${FAILURE_MARKER}"
    notify_failure "lockf could not execute" "lockf infrastructure failure (exit ${lockf_status}) -- see ${LOG_FILE}" "${LOG_FILE}"
    exit "${lockf_status}"
  fi

  status="$(cat "${STATUS_FILE}")"
  rm -f "${STATUS_FILE}"

  wrapper_status="$(classify_and_report "${status}" "${LOG_FILE}" "${FAILURE_MARKER}")"
  exit "${wrapper_status}"
}

# Only auto-run when executed directly -- sourcing this file (as the
# test harness for `classify_and_report` does) must not also trigger
# `main`'s lockf/subprocess dance.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
