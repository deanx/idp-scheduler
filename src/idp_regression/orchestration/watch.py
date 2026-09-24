"""``idp-regression watch`` -- a foreground watcher loop wrapping
``check_versions.check_once()`` in-process (user decision 2026-09-23:
*"I don't need a job in the SO. Just a command to start the watcher...
one foreground command they run in a terminal that keeps watching until
Ctrl-C."*).

This is Phase 1's trigger shape, unchanged: run one tick, sleep, repeat,
until interrupted or a halt condition fires (ADR-0006 SS A'.10 -- SS A'.5
for `--auto-run`). No daemon, no launchd job: a plain foreground process.

**Two audiences, two channels (2026-09-23 priority correction).** This
console is going to be watched live by a non-engineer sponsor, so the
default output is a short, aligned, human-readable line per tick --
printed directly, not routed through the logging framework's formatter.
The structured ``check_tick`` JSON event (the coverage record SS A'.3
depends on) is NOT dropped -- it is still emitted every tick, at DEBUG
level on the ``idp_regression`` package logger (so
``IDP_REGRESSION_LOG_LEVEL=DEBUG`` recovers it), and ``--json-events``
promotes it to INFO so it interleaves with the human lines on request.
The human line is never the JSON's little sibling formatted differently;
it says what happened in plain words ("probed 9 candidate versions, none
exist"), not what the code did ("walk_candidates yielded 9").

**``--auto-run``, opt-in and OFF by default.** ADR-0006 SS A'.5 names four
preconditions for unattended run-triggering that are not yet met (no
per-day document ledger, no claim/resolve idempotency protocol, `/signoff`
B-1/B-2 open, no live alerting sink). This flag ships anyway, as a
deliberate, named POC override (see the dated implementation note added
under SS A'.5) -- explicit opt-in, requires `--max-runs-per-tick` (no
default), and every invocation logs loudly that it spends real IDP
extraction quota, unlike the watcher itself.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.adapter.version_probe import (
    IDPVersionProbe,
    MuleSoftVersionProbe,
    ProbeResult,
    parse_semver,
)
from idp_regression.orchestration.check_versions import (
    OUTCOME_ANCHOR_VANISHED,
    OUTCOME_CEILING_REACHED,
    OUTCOME_DETECTOR_DEGRADED,
    OUTCOME_DISCRIMINATOR_INVALID,
    OUTCOME_INDETERMINATE,
    OUTCOME_NEW_VERSION_DETECTED,
    OUTCOME_NO_NEW_VERSIONS,
    OUTCOME_SKIPPED_LOCKED,
    CheckVersionsRefused,
    StateFileLocked,
    TickResult,
    TickState,
    _reject_state_file_inside_repo,
    check_once,
    load_state_from_file,
    open_state_file_locked,
    save_state_atomic,
)
from idp_regression.orchestration.cli import configure_logging
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.facade import DEFAULT_MAX_DOCUMENTS_PER_RUN, run_eval
from idp_regression.orchestration.log_sanitize import frame_location

#: NOT `logging.getLogger(__name__)` -- the identical bug fixed in
#: `cli.py` 2026-09-23 (`318c9ff`): under `python -m
#: idp_regression.orchestration.watch`, `runpy` imports this module as
#: `__main__`, which would orphan its logger from the `idp_regression`
#: package hierarchy `configure_logging()` attaches its handler to. Pinned
#: by `tests/orchestration/test_logging_config.py`
#: `test_watch_logger_name_is_stable_under_python_dash_m_invocation`.
logger = logging.getLogger("idp_regression.orchestration.watch")

#: 5 minutes. Each tick is ~11 HTTP round trips against MuleSoft and costs
#: zero extraction quota (SS A'.10), so the ceiling here is not quota --
#: it is (a) not hammering a production endpoint whose 400-vs-404
#: discrimination is undocumented behaviour the vendor never promised
#: (SS A'.8 R2), and (b) a POC cadence a human is actually watching. Five
#: minutes is prompt enough to demo live and conservative enough not to
#: look like probing abuse in an access log.
DEFAULT_INTERVAL_SECONDS = 300

_HALT_OUTCOMES = frozenset(
    {OUTCOME_ANCHOR_VANISHED, OUTCOME_DISCRIMINATOR_INVALID, OUTCOME_DETECTOR_DEGRADED}
)

#: "Cheap one" (2026-09-23 re-review): a positive-control response of
#: `ProbeResult.UNKNOWN` (a 429, a 5xx, a malformed response -- see
#: `classify_probe_response`) halts as `anchor_vanished` exactly like a
#: genuine `ProbeResult.ABSENT` (the exit code and the halt itself are
#: correct either way -- neither is safe to keep walking on), but the
#: human-facing diagnosis is not the same claim: ABSENT means the anchor
#: version itself is gone; UNKNOWN means the probe couldn't get a clean
#: answer at all, i.e. the API was unreachable/ambiguous, not that
#: anything vanished. Distinguished here, read from `event["positive_control"]`.
_ANCHOR_VANISHED_PHRASE = "ANCHOR VANISHED -- stopping"
_ANCHOR_UNREACHABLE_PHRASE = "ANCHOR CHECK UNREACHABLE (ambiguous response) -- stopping"

#: Smaller item (2026-09-24 `/test` re-gate): the per-tick phrase and the
#: closing summary's healthy-quiet phrase ("no new versions found", built
#: below) used to share the substring "no new versions", so a script
#: grepping the obvious phrase would match a single quiet TICK line just
#: as readily as the final summary -- the "found" suffix was the only
#: (undocumented) discriminator. Deliberately singular/differently worded
#: here so the two are unambiguous without relying on that suffix alone.
_OUTCOME_PHRASES = {
    OUTCOME_NO_NEW_VERSIONS: "no new version this tick",
    OUTCOME_NEW_VERSION_DETECTED: "NEW VERSION DETECTED",
    OUTCOME_CEILING_REACHED: "probe budget reached (continuing next tick)",
    OUTCOME_INDETERMINATE: "ambiguous result (retrying)",
    OUTCOME_DETECTOR_DEGRADED: "DETECTOR DEGRADED -- stopping",
    OUTCOME_DISCRIMINATOR_INVALID: "DISCRIMINATOR BROKEN -- stopping",
    OUTCOME_ANCHOR_VANISHED: _ANCHOR_VANISHED_PHRASE,
    OUTCOME_SKIPPED_LOCKED: "skipped (already running)",
}

_RUN_VERSION_PATTERN = re.compile(r"--version (\S+)")

#: F-1 (2026-09-23, `/test` gate, Critical, reproduced): before this
#: ceiling existed, `except Exception` had no bound on CONSECUTIVE tick
#: failures -- a watcher whose every tick raised (revoked credentials, a
#: wrong `IDP_REGION`, a dead endpoint) ran forever, logging "continuing"
#: every time, and its own terminal summary then read the absence of any
#: detection as `"no new versions found"` -- a silently-wrong claim to
#: the human watching. Mirrors `check_once`'s own
#: `consecutive_indeterminate_ticks` -> `max_indeterminate_ticks` ->
#: `detector_degraded` escalation (same ">" not ">=" semantics: the
#: ceiling fires on the (max+1)th consecutive failure, a healthy tick
#: resets the counter to zero) -- that escalates an AMBIGUOUS answer;
#: this escalates NO answer at all, which the old code never did.
DEFAULT_MAX_CONSECUTIVE_TICK_FAILURES = 3

_TICK_FAILURES_HALT_PHRASE = "TICK FAILURES EXCEEDED CEILING -- stopping"


def _anchor_vanished_phrase(event: dict[str, Any]) -> str:
    if event.get("positive_control") == str(ProbeResult.UNKNOWN):
        return _ANCHOR_UNREACHABLE_PHRASE
    return _ANCHOR_VANISHED_PHRASE


def _short(value: str, *, head: int = 8) -> str:
    """Truncates an identifier (an action id UUID) for the human line --
    the structured event still carries it in full."""
    return value if len(value) <= head else f"{value[:head]}…"


def _format_interval(interval_seconds: int) -> str:
    if interval_seconds % 3600 == 0 and interval_seconds >= 3600:
        return f"{interval_seconds // 3600}h"
    if interval_seconds % 60 == 0 and interval_seconds >= 60:
        return f"{interval_seconds // 60}m"
    return f"{interval_seconds}s"


def _now_str(clock: Callable[[], float]) -> str:
    return time.strftime("%H:%M:%S", time.localtime(clock()))


def _startup_banner(
    *,
    org_id: str,
    action_id: str,
    dataset_name: str,
    anchor: str | None,
    interval_seconds: int,
) -> str:
    anchor_str = anchor if anchor else "no anchor yet"
    return (
        f"watching org={org_id} action={_short(action_id)} dataset={dataset_name} "
        f"anchor={anchor_str} · every {_format_interval(interval_seconds)} "
        "· Ctrl-C to stop"
    )


def _human_tick_line(iteration: int, event: dict[str, Any], *, clock: Callable[[], float]) -> str:
    outcome = str(event.get("outcome"))
    probed = len(event.get("probed") or [])
    phrase = (
        _anchor_vanished_phrase(event)
        if outcome == OUTCOME_ANCHOR_VANISHED
        else _OUTCOME_PHRASES.get(outcome, outcome)
    )
    controls_failed = outcome in (OUTCOME_ANCHOR_VANISHED, OUTCOME_DISCRIMINATOR_INVALID)
    controls = "FAILED" if controls_failed else "ok"
    return (
        f"{_now_str(clock)}  tick {iteration:<3} probed {probed:<3} "
        f"{phrase:<40} {controls}"
    )


def _print_detection_block(result: TickResult) -> None:
    print()
    print("=" * 70)
    for command in result.run_eval_commands:
        match = _RUN_VERSION_PATTERN.search(command)
        version = match.group(1) if match else "?"
        print(f"  NEW VERSION DETECTED: {version}")
    print()
    print("  Ready to paste -- run this to regress it:")
    print()
    for command in result.run_eval_commands:
        print(f"      {command}")
    print()
    print("=" * 70)
    print()


def _print_halt_block(phrase: str, message: str) -> None:
    print()
    print("=" * 70)
    print(f"  WATCHER STOPPED: {phrase}")
    print(f"  {message}")
    print("  A human must look. See the log for full detail.")
    print("=" * 70)
    print()


def _emit_structured_event(event: dict[str, Any], *, json_events: bool) -> None:
    line = sanitize_for_log(json.dumps(event, default=str))
    if json_events:
        logger.info(line)
    else:
        logger.debug(line)


def _run_auto_run(
    result: TickResult,
    *,
    action_id: str,
    dataset_name: str,
    org_id: str,
    max_documents_per_run: int,
    max_runs_per_tick: int,
    run_eval_fn: Callable[[str, str, str, str, str, int], int],
) -> None:
    """Invokes `run_eval` in-process for up to `max_runs_per_tick` of the
    versions this tick detected. A version not reached this tick because
    of the cap is NOT lost -- `check_once` already folded every hit into
    `new_state.known_versions` regardless of whether it was auto-run, so
    it will never be reported as `new_version_detected` again (ADR-0006
    SS A'.5: a gate FAIL is a successful detection-and-run, never a retry
    candidate -- this falls out of that state update for free)."""
    runs_this_tick = 0
    skipped = 0
    for command in result.run_eval_commands:
        if runs_this_tick >= max_runs_per_tick:
            skipped += 1
            continue
        match = _RUN_VERSION_PATTERN.search(command)
        if not match:
            continue
        version = match.group(1)
        runs_this_tick += 1
        run_name = f"auto-watch-{version}"
        logger.warning(
            "auto_run: spending real IDP extraction quota -- version=%s run=%s",
            version,
            run_name,
        )
        print(f"  >>> --auto-run: launching run_eval for {version} (spends real IDP quota)")
        # Smaller item (2026-09-24 `/test` re-gate): `save_state_atomic`
        # already persisted this version into `known_versions` BEFORE
        # `_run_auto_run` was ever called (it runs once per tick, right
        # after the tick's own success), so a `run_eval_fn` that RAISES
        # (as opposed to returning a gate FAIL exit code) previously
        # escaped uncaught -- crashing the entire watch loop for what
        # should be one failed run, while the detection itself could
        # never be retried (it is already "known"). Distinct from an
        # ordinary gate FAIL, which is loud on the console by design
        # (see `verdict` below) -- this is the same loudness for the
        # "didn't even get a verdict" case, without taking the whole
        # watcher down with it.
        try:
            exit_code = run_eval_fn(
                action_id, version, run_name, dataset_name, org_id, max_documents_per_run
            )
        except Exception as exc:  # noqa: BLE001 - a failed run must not crash the watcher
            logger.error(
                "auto_run: run_eval RAISED for version=%s run=%s type=%s at %s -- "
                "no verdict recorded, will NOT be retried (already known)",
                version,
                run_name,
                type(exc).__name__,
                frame_location(exc),
            )
            print(
                f"  >>> --auto-run: {version} FAILED TO RUN (see log) -- "
                "no verdict, will not be retried"
            )
            continue
        verdict = "PASS" if exit_code == 0 else "gate FAIL or abort"
        print(f"  >>> --auto-run: {version} finished -- {verdict} (exit={exit_code})")
        logger.warning(
            "auto_run: run_eval finished version=%s exit_code=%d verdict=%s",
            version,
            exit_code,
            verdict,
        )
    if skipped:
        logger.warning(
            "auto_run: --max-runs-per-tick=%d reached; %d detected version(s) not "
            "auto-run this tick (already recorded as known -- will not retry)",
            max_runs_per_tick,
            skipped,
        )
        print(
            f"  >>> --auto-run: --max-runs-per-tick reached; {skipped} version(s) "
            "left for a future manual run"
        )


def run_watch_loop(
    *,
    probe: IDPVersionProbe,
    org_id: str,
    action_id: str,
    dataset_name: str,
    state: TickState,
    known_version: str | None,
    max_probes_per_tick: int,
    patch_lookahead: int,
    minor_lookahead: int,
    major_lookahead: int,
    sweep_every_n_ticks: int,
    max_probes_per_sweep: int,
    max_indeterminate_ticks: int,
    interval_seconds: int,
    max_consecutive_tick_failures: int = DEFAULT_MAX_CONSECUTIVE_TICK_FAILURES,
    state_file: Path | None = None,
    auto_run: bool = False,
    max_runs_per_tick: int = 0,
    max_documents_per_run: int = DEFAULT_MAX_DOCUMENTS_PER_RUN,
    json_events: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    run_eval_fn: Callable[[str, str, str, str, str, int], int] = run_eval,
) -> int:
    """The watcher's core loop -- tick, sleep, repeat, until
    `KeyboardInterrupt` or a halt outcome (`_HALT_OUTCOMES`). `sleep_fn`
    and `clock` are injected so the loop is unit-testable without a real
    wall-clock wait (mirrors the adapter's poll tests) -- a test's
    `sleep_fn` can raise `KeyboardInterrupt` after N calls to end the loop
    deterministically instead of running forever.

    A tick that raises an ordinary exception (a transient network error,
    a 5xx the probe didn't classify) does NOT kill the loop -- it is
    logged and the loop continues at the next interval, unchanged state.
    `CheckVersionsRefused` (an unparseable anchor, an uninitialised
    bootstrap) is different: it is a structurally broken setup that
    waiting out an interval cannot fix, so it halts like the named
    outcomes.

    F-1: a tick failure surviving does NOT mean it survives forever --
    `max_consecutive_tick_failures` consecutive failures (a successful
    tick resets the count to zero) halts the loop exactly like a named
    `_HALT_OUTCOMES` outcome, exit code 1. Without this, a permanently
    broken credential or endpoint looks identical, on the console and in
    the exit code, to a perfectly healthy quiet watcher."""
    print(
        _startup_banner(
            org_id=org_id,
            action_id=action_id,
            dataset_name=dataset_name,
            anchor=(
                # Suggestion fix (2026-09-23): plain `max()` over the
                # version strings is LEXICOGRAPHIC, so "1.9.0" would
                # display over "1.10.0" -- wrong even though this is
                # display-only, because it is the one line the sponsor
                # watching live actually reads. `parse_semver` as the
                # sort key makes it numeric, matching how the walk/sweep
                # themselves compare versions.
                max(state.known_versions, key=lambda v: parse_semver(v) or (-1, -1, -1))
                if state.known_versions
                else known_version
            ),
            interval_seconds=interval_seconds,
        )
    )

    iteration = 0
    detected_versions: list[str] = []
    healthy_ticks = 0
    failed_ticks = 0
    consecutive_tick_failures = 0
    started_at = clock()
    exit_code = 0
    try:
        while True:
            iteration += 1
            try:
                result = check_once(
                    probe=probe,
                    org_id=org_id,
                    action_id=action_id,
                    dataset_name=dataset_name,
                    state=state,
                    known_version=known_version,
                    max_probes_per_tick=max_probes_per_tick,
                    patch_lookahead=patch_lookahead,
                    minor_lookahead=minor_lookahead,
                    major_lookahead=major_lookahead,
                    sweep_every_n_ticks=sweep_every_n_ticks,
                    max_probes_per_sweep=max_probes_per_sweep,
                    max_indeterminate_ticks=max_indeterminate_ticks,
                    clock=clock,
                )
            except CheckVersionsRefused as exc:
                logger.error(
                    "watch: refused outcome=%s message=%s",
                    exc.outcome,
                    sanitize_for_log(str(exc)),
                )
                _print_halt_block(_OUTCOME_PHRASES.get(exc.outcome, exc.outcome), str(exc))
                exit_code = 1
                break
            except Exception as exc:  # noqa: BLE001 - a tick failing must not kill the loop
                failed_ticks += 1
                consecutive_tick_failures += 1
                logger.error(
                    "tick %d  FAILED (transient) type=%s at %s -- continuing "
                    "(%d consecutive failure(s), ceiling %d)",
                    iteration,
                    type(exc).__name__,
                    frame_location(exc),
                    consecutive_tick_failures,
                    max_consecutive_tick_failures,
                )
                print(
                    f"{_now_str(clock)}  tick {iteration:<3} "
                    "FAILED (transient) -- will retry next tick"
                )
                # F-1: escalate NO answer at all, not just an ambiguous one --
                # mirrors check_once's own consecutive_indeterminate_ticks ->
                # max_indeterminate_ticks escalation, ">" not ">=" so the
                # ceiling fires on the (max+1)th consecutive failure.
                if consecutive_tick_failures > max_consecutive_tick_failures:
                    logger.error(
                        "watch: halting -- %d consecutive tick failures exceeds "
                        "ceiling %d",
                        consecutive_tick_failures,
                        max_consecutive_tick_failures,
                    )
                    _print_halt_block(
                        _TICK_FAILURES_HALT_PHRASE,
                        f"{consecutive_tick_failures} consecutive tick(s) got no "
                        f"answer (ceiling {max_consecutive_tick_failures}) -- see "
                        f"tick {iteration}'s log line above",
                    )
                    exit_code = 1
                    break
                sleep_fn(interval_seconds)
                continue

            consecutive_tick_failures = 0
            healthy_ticks += 1
            state = result.new_state
            print(_human_tick_line(iteration, result.event, clock=clock))
            _emit_structured_event(result.event, json_events=json_events)

            if state_file is not None:
                save_state_atomic(state_file, state)

            if result.outcome == OUTCOME_NEW_VERSION_DETECTED:
                for command in result.run_eval_commands:
                    match = _RUN_VERSION_PATTERN.search(command)
                    if match:
                        detected_versions.append(match.group(1))
                _print_detection_block(result)
                for command in result.run_eval_commands:
                    logger.info("version_detected run_eval_command=%s", sanitize_for_log(command))
                if auto_run:
                    _run_auto_run(
                        result,
                        action_id=action_id,
                        dataset_name=dataset_name,
                        org_id=org_id,
                        max_documents_per_run=max_documents_per_run,
                        max_runs_per_tick=max_runs_per_tick,
                        run_eval_fn=run_eval_fn,
                    )

            if result.outcome in _HALT_OUTCOMES:
                logger.error("watch: halting -- outcome=%s", result.outcome)
                halt_phrase = (
                    _anchor_vanished_phrase(result.event)
                    if result.outcome == OUTCOME_ANCHOR_VANISHED
                    else _OUTCOME_PHRASES.get(result.outcome, result.outcome)
                )
                _print_halt_block(halt_phrase, f"see tick {iteration}'s log line above")
                exit_code = 1
                break

            sleep_fn(interval_seconds)
    except KeyboardInterrupt:
        pass

    elapsed = clock() - started_at
    elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"

    # F-5/F-7 (2026-09-24 `/test` re-gate, both Critical/Major, both
    # reproduced): the invariant this block enforces is "the closing
    # summary must never claim 'no new versions found' when the watcher
    # did not get a clean answer from every tick it attempted" -- keyed
    # on `failed_ticks` directly, never discarded once a single tick
    # happens to succeed (F-5: a probe failing 3 of 4 ticks made the old
    # `healthy_ticks == 0` guard permanently unreachable and a single
    # early success silently absolved every later failure). `elif
    # healthy_ticks == 0 and failed_ticks > 0` (F-7) was ALSO wrong the
    # other way: a `CheckVersionsRefused` halt before any tick completes
    # increments neither counter, so that conjunct excluded exactly the
    # case it needed to cover -- the `and failed_ticks > 0` half is
    # simply gone below.
    if detected_versions:
        found = f"detected {len(detected_versions)} new version(s): {', '.join(detected_versions)}"
    elif healthy_ticks > 0:
        found = "no new versions found"
    else:
        # No tick this run ever produced a real answer -- either every
        # attempted tick failed transiently, or the watcher halted
        # (`CheckVersionsRefused`) before a single tick could even be
        # attempted (F-7's exact case: iteration=1, healthy_ticks=0,
        # failed_ticks=0). Neither is evidence of "no new versions".
        found = (
            f"no answer -- {failed_ticks} tick(s) never got an answer"
            if failed_ticks > 0
            else f"no answer -- watcher halted before any tick completed "
            f"({iteration} tick(s) attempted)"
        )

    # F-5: a mixed run (some ticks healthy, some failed) must say so even
    # when a healthy tick (or an outright detection) also happened --
    # `failed_ticks` alone decides whether this note is appended, not
    # whether `healthy_ticks` is zero. This is the ONLY place `found` can
    # still contain the bare phrase "no new versions found" while
    # `failed_ticks > 0` is true, and it is never silent about it.
    if healthy_ticks > 0 and failed_ticks > 0:
        found += f" ({failed_ticks} of {iteration} tick(s) got no answer)"

    print(f"\nStopped after {iteration} tick(s) ({elapsed_str}). {found}.")
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="idp-regression watch")
    parser.add_argument("--org", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=(
            f"seconds to sleep between ticks (default {DEFAULT_INTERVAL_SECONDS}, "
            "5 minutes). Each tick is ~11 HTTP round trips against MuleSoft "
            "and spends ZERO extraction quota; the default trades promptness "
            "against not hammering a documented-but-not-designed-for-polling "
            "endpoint."
        ),
    )
    parser.add_argument("--max-probes-per-tick", type=int, default=20)
    parser.add_argument("--max-probes-per-sweep", type=int, default=40)
    parser.add_argument("--patch-lookahead", type=int, default=3)
    parser.add_argument("--minor-lookahead", type=int, default=3)
    parser.add_argument("--major-lookahead", type=int, default=1)
    parser.add_argument("--sweep-every-n-ticks", type=int, default=10)
    parser.add_argument("--max-indeterminate-ticks", type=int, default=3)
    parser.add_argument(
        "--max-consecutive-tick-failures",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_TICK_FAILURES,
        help=(
            "F-1: consecutive ticks that raise (not merely come back "
            "ambiguous) before the watcher halts loudly instead of running "
            f"forever silently reporting no detections (default "
            f"{DEFAULT_MAX_CONSECUTIVE_TICK_FAILURES}). A successful tick "
            "resets the count."
        ),
    )
    parser.add_argument(
        "--known-version",
        default=None,
        help="one-time anchor seed (ADR-0006 SS A'.6 bootstrap) -- only needed once.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        default=False,
        help=(
            "also print the full structured check_tick JSON event each tick "
            "(normally only the short human line is printed; the JSON event "
            "is still emitted at DEBUG on the idp_regression logger either way)."
        ),
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        default=False,
        help=(
            "OPT-IN, OFF BY DEFAULT. Automatically invokes run_eval in-process "
            "for every newly detected version, IN ADDITION to printing the "
            "ready-to-paste command. UNLIKE THE WATCH ITSELF, THIS SPENDS REAL "
            "IDP EXTRACTION QUOTA. ADR-0006 A'.5 names four preconditions "
            "(per-day document ledger, claim/resolve idempotency, /signoff "
            "B-1/B-2 closed, a live alerting sink) that are not yet fully met "
            "-- this flag ships early as a deliberate, named POC override "
            "(see the dated implementation note under ADR-0006 A'.5). "
            "Requires --max-runs-per-tick."
        ),
    )
    parser.add_argument(
        "--max-runs-per-tick",
        type=int,
        default=None,
        help=(
            "REQUIRED with --auto-run: caps how many run_eval invocations one "
            "tick may fire, so a tick that detects several versions at once "
            "cannot trigger an unbounded number of regressions."
        ),
    )
    parser.add_argument(
        "--max-documents-per-run",
        type=int,
        default=DEFAULT_MAX_DOCUMENTS_PER_RUN,
        help="passed through to run_eval on every --auto-run invocation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Thin outer shell. F-2 (2026-09-24 `/test` re-gate, Critical, still
    NOT closed after the round that added the two site-specific catches
    below): the invariant is *no exception from any state-file I/O --
    or from anywhere else in this function -- escapes `main()` raw*, not
    "these particular calls are individually guarded". `check_versions
    .main()` already gets this right by putting its one catch-all on the
    OUTER try wrapping everything; this mirrors that shape instead of
    enumerating sites, so a site added tomorrow (a fourth `os.open`, a
    new credential lookup, anything) is covered for free. The inner,
    per-site `except`s in `_run(...)` below are NOT removed -- they give
    a friendlier, more specific message for the failures already known
    (`StateFileLocked`, `CheckVersionsRefused`, a missing env var) -- this
    is the backstop for everything else."""
    configure_logging()
    try:
        return _run(argv)
    except Exception as exc:  # noqa: BLE001 - F-2: the invariant, not a site list
        logger.error(
            "watch: unexpected error: %s at %s", type(exc).__name__, frame_location(exc)
        )
        print("error: unexpected error -- see log", file=sys.stderr)
        return 1


def _run(argv: list[str] | None) -> int:
    # R4 fix (2026-09-23, reviewer REQUEST CHANGES): this entry point
    # never called `load_dotenv()` at all -- `cli.py`/`facade.py` do
    # (INV-05) -- so the documented `python -m ...` invocation only
    # worked if the operator had pre-sourced `.env` by hand.
    try:
        load_dotenv()
    except Exception as exc:  # noqa: BLE001 - never let a .env load raise raw
        logger.error("watch: unexpected error loading .env: %s", type(exc).__name__)
        return 1

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.auto_run and args.max_runs_per_tick is None:
        logger.error(
            "watch: --auto-run requires --max-runs-per-tick (so one tick "
            "cannot fire an unbounded number of regressions)"
        )
        print("error: --auto-run requires --max-runs-per-tick", file=sys.stderr)
        return 1
    if not args.auto_run and args.max_runs_per_tick is not None:
        logger.error("watch: --max-runs-per-tick has no effect without --auto-run")
        print("error: --max-runs-per-tick has no effect without --auto-run", file=sys.stderr)
        return 1
    # Suggestion fix (2026-09-23): 0 or negative used to be accepted
    # while the banner below announces "--auto-run ENABLED" -- a lie to
    # the operator, since a tick would then auto-run nothing at all.
    if args.auto_run and args.max_runs_per_tick is not None and args.max_runs_per_tick < 1:
        logger.error(
            "watch: --max-runs-per-tick must be >= 1 (got %d)", args.max_runs_per_tick
        )
        print(
            f"error: --max-runs-per-tick must be >= 1 (got {args.max_runs_per_tick})",
            file=sys.stderr,
        )
        return 1

    try:
        _reject_state_file_inside_repo(args.state_file)
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
    except CheckVersionsRefused as exc:
        logger.error("watch: %s", sanitize_for_log(str(exc)))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - F-2/INV-02: never a raw traceback/path here
        # ⚠️ Fixed 2026-09-23 (`/test` gate, F-2): this try used to catch
        # ONLY `CheckVersionsRefused` -- an `OSError` from `mkdir()` (e.g.
        # a read-only parent) escaped `main()`, the outermost caller, as
        # a RAW TRACEBACK carrying the state-file path. Same shape as
        # every other catch-all in this codebase: only the type name and
        # frame location are logged, never `str(exc)`.
        logger.error(
            "watch: unexpected error preparing state file: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        print("error: unexpected error preparing state file -- see log", file=sys.stderr)
        return 1

    # R2 fix (2026-09-23, reviewer REQUEST CHANGES): this used to just
    # `open()` the state file -- no lock at all -- then
    # `save_state_atomic` every tick for the process's whole life. Two
    # watchers, or a watcher plus a scheduled `check-versions`, could
    # interleave and lose `known_versions`; with `--auto-run` a lost
    # entry is a re-detection AND a duplicate real-quota run. The lock
    # is now taken once, here, and held for the ENTIRE foreground loop's
    # lifetime (the same `flock` semantics `check_versions.main()` uses
    # for its one tick) -- released only when the loop actually ends.
    # RQ-1 fix (2026-09-23 re-review, PIN): the lock above is taken on a
    # SIDECAR path (`<state_file>.lock`), not `state_file` itself --
    # `save_state_atomic`'s `os.replace()` retargets `state_file` to a
    # brand-new inode on every save, which would silently orphan a lock
    # held on the data file's own (now unreferenced) inode from tick 2
    # onward, running the rest of a potentially days-long loop
    # completely unlocked. See `open_state_file_locked`'s docstring.
    try:
        fd = open_state_file_locked(args.state_file)
    except StateFileLocked:
        logger.error(
            "watch: %s is locked by another running instance -- refusing to start",
            args.state_file,
        )
        print(
            f"error: {args.state_file} is locked by another running `watch` "
            "(or `check-versions`) instance -- refusing to start",
            file=sys.stderr,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - F-2/INV-02: never a raw traceback/path here
        # F-2: `open_state_file_locked` raising anything OTHER than
        # `StateFileLocked` (a bare `OSError` from the sidecar's own
        # `os.open`) was not caught here either.
        logger.error(
            "watch: unexpected error locking state file: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        print("error: unexpected error locking state file -- see log", file=sys.stderr)
        return 1

    try:
        try:
            # RQ-1 fix (2026-09-23 re-review): `fd` above is now the
            # SIDECAR lock's fd (`open_state_file_locked` locks
            # `<state_file>.lock`, never the data file itself, since
            # `save_state_atomic`'s `os.replace()` would silently orphan
            # a lock taken on the data file's own inode after the first
            # tick's save). Read the data file itself through
            # `load_state_from_file`, independent of the lock.
            state = load_state_from_file(args.state_file)
        except CheckVersionsRefused as exc:
            logger.error("watch: %s", sanitize_for_log(str(exc)))
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.auto_run:
            logger.warning(
                "watch: --auto-run is ENABLED -- unlike the watcher itself, this "
                "SPENDS REAL IDP EXTRACTION QUOTA (max %d run(s)/tick).",
                args.max_runs_per_tick,
            )
            print(
                f"*** --auto-run ENABLED: up to {args.max_runs_per_tick} run_eval "
                "invocation(s) per tick -- spends real IDP extraction quota. ***"
            )

        # R4 fix: a missing/renamed credential env var is already
        # guarded here (this is the site the review called "gets this
        # right") -- kept as-is, now just inside the lock's scope.
        try:
            client_id = os.environ["IDP_CLIENT_ID"].strip()
            client_secret = os.environ["IDP_CLIENT_SECRET"].strip()
            region = os.environ["IDP_REGION"].strip()
        except KeyError as exc:
            logger.error("watch: missing required environment variable %s", exc)
            print(f"error: missing required environment variable {exc}", file=sys.stderr)
            return 1

        probe = MuleSoftVersionProbe(client_id, client_secret, region)

        try:
            return run_watch_loop(
                probe=probe,
                org_id=args.org,
                action_id=args.action,
                dataset_name=args.dataset,
                state=state,
                known_version=args.known_version,
                max_probes_per_tick=args.max_probes_per_tick,
                patch_lookahead=args.patch_lookahead,
                minor_lookahead=args.minor_lookahead,
                major_lookahead=args.major_lookahead,
                sweep_every_n_ticks=args.sweep_every_n_ticks,
                max_probes_per_sweep=args.max_probes_per_sweep,
                max_indeterminate_ticks=args.max_indeterminate_ticks,
                max_consecutive_tick_failures=args.max_consecutive_tick_failures,
                interval_seconds=args.interval_seconds,
                state_file=args.state_file,
                auto_run=args.auto_run,
                max_runs_per_tick=args.max_runs_per_tick or 0,
                max_documents_per_run=args.max_documents_per_run,
                json_events=args.json_events,
            )
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - defense in depth
            if isinstance(exc, KeyboardInterrupt):
                print("\nWatcher stopped (Ctrl-C).")
                return 0
            logger.error(
                "watch: unexpected error: %s at %s", type(exc).__name__, frame_location(exc)
            )
            print("error: unexpected error -- see log", file=sys.stderr)
            return 1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == "__main__":  # pragma: no cover - thin process entry
    sys.exit(main())
