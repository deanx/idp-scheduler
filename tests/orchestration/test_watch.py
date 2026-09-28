"""ADR-0006 §A'.5 / §A'.10 -- the foreground watcher loop (user decision
2026-09-23: no launchd job, "just a command ... that keeps watching until
Ctrl-C"). `run_watch_loop()` is the pure(ish), injectable core under test
here; `main()` gets thin CLI-wiring tests further down.

Console output is the priority here (2026-09-23 correction): the sponsor
watches this scroll by on a real terminal, so several tests assert on the
exact printed lines, not just on outcome/state."""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter.version_probe import NEGATIVE_CONTROL_VERSION, ProbeResult
from idp_regression.orchestration import check_versions as check_versions_module
from idp_regression.orchestration.check_versions import (
    StateFileLockedError,
    TickState,
    _lock_path_for,
    open_state_file_locked,
)
from idp_regression.orchestration.watch import (
    _HALT_OUTCOMES,
    DEFAULT_INTERVAL_SECONDS,
    main,
    run_watch_loop,
)


class FakeProbe:
    """Same scripted double as tests/orchestration/test_check_versions.py
    -- any version not in `responses` is ABSENT by default (a real vendor
    404s an un-probed version); `default=ProbeResult.UNKNOWN` (added
    S-01.4 re-stamp #5 F-1) scripts a persistently-ambiguous endpoint
    instead, for the ceiling_reached/indeterminate repro."""

    def __init__(
        self, responses: dict[str, ProbeResult], default: ProbeResult = ProbeResult.ABSENT
    ) -> None:
        self.responses = responses
        self.default = default
        self.calls: list[str] = []
        self.last_status_code: int | None = 200

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        self.calls.append(version)
        return self.responses.get(version, self.default)


class RaisesOnceThenProbe:
    """A transient failure on its first call (a network blip), then
    delegates to a FakeProbe -- proves a tick failure doesn't kill the
    loop.

    F-3: carries `last_status_code` even though it never sets it to
    anything but `None` -- this is the Protocol-conformant double the
    F-3 finding names as already existing in the suite, now that
    `last_status_code` is part of `IDPVersionProbe` itself rather than
    reached only via `getattr(..., None)`."""

    def __init__(self, inner: FakeProbe) -> None:
        self.inner = inner
        self._raised = False
        self.last_status_code: int | None = None

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        if not self._raised:
            self._raised = True
            raise ConnectionError("simulated transient network failure")
        return self.inner.probe(org_id, action_id, version)


class RaisesForeverProbe:
    """Every call raises -- the F-1 reproduction: a watcher whose every
    tick fails must eventually escalate to a halt instead of running
    forever while its own summary line claims "no new versions found"."""

    def __init__(self) -> None:
        self.last_status_code: int | None = None

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        raise ConnectionError("simulated permanent transient failure")


def _controls_ok(anchor: str) -> dict[str, ProbeResult]:
    return {anchor: ProbeResult.EXISTS, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT}


def _base_kwargs(**overrides: object) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        org_id="org1",
        action_id="12345678-1234-1234-1234-123456789012",
        dataset_name="ds1",
        state=TickState(),
        known_version="1.0.0",
        max_probes_per_tick=20,
        patch_lookahead=3,
        minor_lookahead=3,
        major_lookahead=1,
        sweep_every_n_ticks=0,
        max_probes_per_sweep=0,
        max_indeterminate_ticks=3,
        interval_seconds=300,
    )
    kwargs.update(overrides)
    return kwargs


def _stop_after(n: int) -> Any:
    """A `sleep_fn` double that raises KeyboardInterrupt on its Nth call
    -- the test-side equivalent of the user hitting Ctrl-C, so the loop
    ends deterministically without a real wall-clock wait or a
    production-only test hook."""
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= n:
            raise KeyboardInterrupt

    _sleep.calls = calls  # type: ignore[attr-defined]
    return _sleep


# -- Console output: the priority (2026-09-23) -------------------------------


def test_startup_banner_and_quiet_tick_lines(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    sleep_fn = _stop_after(2)

    exit_code = run_watch_loop(probe=probe, sleep_fn=sleep_fn, **_base_kwargs())

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert exit_code == 0
    assert lines[0].startswith("watching org=org1 action=12345678")
    assert "dataset=ds1" in lines[0]
    assert "every 5m" in lines[0]
    assert "Ctrl-C to stop" in lines[0]

    tick_lines = [line for line in lines if "tick 1" in line or "tick 2" in line]
    assert len(tick_lines) == 2
    for line in tick_lines:
        # Smaller item (2026-09-24): the per-tick phrase and the closing
        # summary's phrase are deliberately DIFFERENT strings (neither is
        # a substring of the other) so a script can't confuse a quiet
        # tick line with the final summary.
        assert "no new version this tick" in line
        assert "no new versions found" not in line
        assert line.strip().endswith("ok")

    assert "Stopped after 2 tick(s)" in out
    assert "no new versions found" in out


def test_a_clean_ctrl_c_prints_no_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    exit_code = run_watch_loop(probe=probe, sleep_fn=_stop_after(1), **_base_kwargs())
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_detection_prints_an_unmissable_block_with_the_ready_to_paste_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    run_watch_loop(probe=probe, sleep_fn=_stop_after(1), **_base_kwargs())

    out = capsys.readouterr().out
    assert "NEW VERSION DETECTED: 1.0.1" in out
    assert "--version 1.0.1" in out
    assert "--action 12345678-1234-1234-1234-123456789012" in out
    assert "Stopped after 1 tick(s)" in out
    assert "detected 1 new version(s): 1.0.1" in out


def test_json_events_default_hidden_but_available_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="idp_regression")
    probe = FakeProbe(_controls_ok("1.0.0"))

    run_watch_loop(probe=probe, sleep_fn=_stop_after(1), **_base_kwargs())

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any('check_tick' in r.getMessage() for r in debug_records)
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert not any('check_tick' in r.getMessage() for r in info_records)


def test_json_events_flag_promotes_the_structured_event_to_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="idp_regression")
    probe = FakeProbe(_controls_ok("1.0.0"))

    run_watch_loop(probe=probe, sleep_fn=_stop_after(1), json_events=True, **_base_kwargs())

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any('check_tick' in r.getMessage() for r in info_records)


# -- Resilience: a failing tick must not kill the loop -----------------------


def test_a_transient_probe_exception_does_not_kill_the_loop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = RaisesOnceThenProbe(FakeProbe(_controls_ok("1.0.0")))
    sleep_fn = _stop_after(2)

    exit_code = run_watch_loop(probe=probe, sleep_fn=sleep_fn, **_base_kwargs())

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "FAILED (transient)" in out
    assert "will retry next tick" in out
    # the loop kept going after the transient failure and completed a real tick
    assert "no new version this tick" in out
    assert len(sleep_fn.calls) == 2


def test_a_probe_that_fails_every_tick_eventually_halts_instead_of_running_forever(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F-1 (Critical): before this fix, an `except Exception` with no
    ceiling on CONSECUTIVE failures meant a watcher whose every tick
    raised (revoked credentials, a wrong IDP_REGION, a dead endpoint)
    ran forever and reported "no new versions found" -- the ceiling
    below is the escalation half `test_a_transient_probe_exception_does_
    not_kill_the_loop` never covered: a failing tick must not kill the
    loop, but it also must not be allowed to hide behind it forever."""
    probe = RaisesForeverProbe()
    sleep_fn = _stop_after(100)  # a safety net far past where the ceiling must fire

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=sleep_fn,
        max_consecutive_tick_failures=3,
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "WATCHER STOPPED" in out
    assert "TICK FAILURES" in out
    # halts on the 4th consecutive failure -- 3 sleeps happened first,
    # mirroring max_indeterminate_ticks's own ">" (not ">=") semantics.
    assert len(sleep_fn.calls) == 3
    # F-1's other half: the summary must never misreport "no answer" as
    # "no new versions found".
    assert "no new versions found" not in out
    assert "never got an answer" in out
    assert "4 tick(s)" in out


def test_f4_ctrl_c_after_only_failed_ticks_below_the_ceiling_is_not_no_new_versions_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-01.4 re-stamp #4 F-4: `if healthy_ticks > 0` is the boundary
    between "no new versions found" and "no answer" in the `interrupted`
    arm -- and nothing exercised the `healthy_ticks == 0` leg WITHOUT
    also tripping the consecutive-failure ceiling (the sibling test above
    covers the ceiling-tripped case, a different `end_reason` branch
    entirely). Every tick fails, the ceiling is set high enough never to
    fire, and Ctrl-C lands first."""
    probe = RaisesForeverProbe()
    sleep_fn = _stop_after(5)

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=sleep_fn,
        max_consecutive_tick_failures=100,  # never trips -- isolate healthy_ticks == 0
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no new versions found" not in out
    assert "no answer -- 5 tick(s) never got an answer" in out


def test_f1_ceiling_reached_ticks_are_no_verdict_too_not_just_indeterminate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-01.4 re-stamp #5 F-1 (fail-open #7): re-stamp #4 counted only
    `OUTCOME_INDETERMINATE` as 'no verdict', but `OUTCOME_CEILING_REACHED`
    (a tick whose own probing budget ran out before it could resolve) is
    JUST as much a non-answer and was silently folded into 'healthy' --
    the same fail-open, one outcome over. Reproduced through the REAL
    `check_once`, not a monkeypatched double, with a real Protocol probe
    that answers UNKNOWN to every non-control candidate -- the exact
    live repro (small `--max-probes-per-tick`, generous lookahead).

    S-01.4 re-stamp #6 F-3: EVERY one of the 40 ticks is no-verdict here
    (0 exceptions, 0 verdicts) -- re-stamp #5's own fix still branched on
    `healthy_ticks > 0` ("a tick completed"), so this exact run still
    printed the unqualified-looking `no new versions found (40 of 40...)`
    headline. The correct output when NO tick ever resolved a verdict is
    the same honest `no answer` phrasing the exception-only case already
    used -- there is nothing to call "found" here."""
    probe = FakeProbe(_controls_ok("1.0.0"), default=ProbeResult.UNKNOWN)
    sleep_fn = _stop_after(40)

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=sleep_fn,
        max_consecutive_tick_failures=100,
        **_base_kwargs(
            max_probes_per_tick=3,
            patch_lookahead=5,
            minor_lookahead=5,
            major_lookahead=2,
            max_indeterminate_ticks=1000,  # isolate the SUMMARY, not the escalation
        ),
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no new versions found" not in out
    assert "no answer -- 40 tick(s) never got an answer" in out


def test_f3_a_partial_verdict_run_still_reports_no_new_versions_found_qualified(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-01.4 re-stamp #6 F-3, the other half: when at least ONE tick DID
    resolve a verdict, the headline stays 'no new versions found', with
    the no-verdict ticks named in the parenthetical -- unchanged from
    re-stamp #5's intent, now correctly gated on `verdict_ticks`, not
    `healthy_ticks`."""
    from idp_regression.orchestration import watch as watch_module
    from idp_regression.orchestration.check_versions import TickResult

    state = TickState()
    healthy_event: dict[str, Any] = {
        "event": "check_tick", "outcome": "no_new_versions", "org_id": "org1",
        "action_id": "x", "dataset_name": "ds1", "anchor": "1.0.0",
        "tick_count": 0, "probed": [],
    }
    indeterminate_event: dict[str, Any] = {
        "event": "check_tick", "outcome": "indeterminate", "org_id": "org1",
        "action_id": "x", "dataset_name": "ds1", "anchor": "1.0.0",
        "tick_count": 0, "probed": [],
    }
    calls = {"n": 0}

    def _fake_check_once(**kwargs: object) -> TickResult:
        n = calls["n"]
        calls["n"] += 1
        if n == 0:
            return TickResult("no_new_versions", state, dict(healthy_event), [])
        return TickResult("indeterminate", state, dict(indeterminate_event), [])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(watch_module, "check_once", _fake_check_once)
    try:
        exit_code = run_watch_loop(
            probe=FakeProbe({}),
            sleep_fn=_stop_after(10),
            max_consecutive_tick_failures=100,
            **_base_kwargs(max_indeterminate_ticks=1000),
        )
    finally:
        monkeypatch.undo()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no new versions found (9 of 10 tick(s) got no answer)" in out


def test_f5_a_majority_failure_rate_is_never_silently_hidden_by_one_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-5 (Critical, reproduced): before this fix, `failed_ticks` was
    computed and then DISCARDED whenever `healthy_ticks > 0` -- a probe
    failing 3 of every 4 ticks (the field-common case: a flaky
    credential, a 5xx-ing endpoint -- `RaisesForeverProbe` above cannot
    reproduce this, since a forever-failing probe makes "consecutive"
    and "cumulative" failures the same number) made the
    `max_consecutive_tick_failures` ceiling (consecutive-only)
    permanently unreachable, AND a single early success made the closing
    summary claim "no new versions found" for the rest of the run no
    matter how many later ticks failed. The invariant: the closing
    summary must never claim "no new versions found" UNQUALIFIED when
    ANY tick failed to get an answer -- keyed on `failed_ticks` directly,
    never on `healthy_ticks == 0`.

    `check_once` itself is monkeypatched (rather than driven through a
    scripted `IDPVersionProbe`) so the fail/succeed pattern is pinned at
    TICK granularity, exactly matching the brief's own repro (30 of 40
    ticks failed) without depending on how many `probe()` calls one
    healthy tick happens to issue internally."""
    from idp_regression.orchestration import watch as watch_module
    from idp_regression.orchestration.check_versions import TickResult

    state = TickState()
    healthy_event: dict[str, Any] = {
        "event": "check_tick",
        "outcome": "no_new_versions",
        "org_id": "org1",
        "action_id": "x",
        "dataset_name": "ds1",
        "anchor": "1.0.0",
        "tick_count": 0,
        "probed": [],
    }
    calls = {"n": 0}

    def _fake_check_once(**kwargs: object) -> TickResult:
        n = calls["n"]
        calls["n"] += 1
        # 30 of 40 ticks fail -- 3 of every 4, the brief's exact ratio.
        if n % 4 != 0:
            raise ConnectionError("simulated flaky failure")
        return TickResult("no_new_versions", state, dict(healthy_event), [])

    monkeypatch.setattr(watch_module, "check_once", _fake_check_once)
    sleep_fn = _stop_after(40)

    exit_code = run_watch_loop(
        probe=FakeProbe({}),
        sleep_fn=sleep_fn,
        max_consecutive_tick_failures=100,  # isolate the SUMMARY invariant, not the ceiling
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Stopped after 40 tick(s)" in out
    # the exact bug: this phrase, UNQUALIFIED, must never appear when any
    # tick failed.
    assert "no new versions found." not in out
    assert "no new versions found (" in out
    assert "30 of 40 tick(s) got no answer)" in out


def test_f1_indeterminate_ticks_interleaved_with_healthy_ones_are_not_folded_into_no_new_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-01.4 re-stamp #4 F-1 (fail-open #6): `check_once`'s own
    `consecutive_indeterminate_ticks` -> `max_indeterminate_ticks`
    escalation only fires on a CONSECUTIVE run -- a resolved tick resets
    it to zero. So an endpoint that is ambiguous 3 of every 4 ticks (the
    same 30-of-40 ratio as the F-5 sibling above, on the NO-VERDICT axis
    instead of the exception axis) never reaches the ceiling, and every
    one of those ticks completes WITHOUT an exception -- `healthy_ticks`
    counts it, and the old summary logic folded it straight into
    unqualified "no new versions found". The invariant: the closing
    summary must account for every tick that got no verdict, whether
    that came from an exception OR a completed-but-indeterminate tick."""
    from idp_regression.orchestration import watch as watch_module
    from idp_regression.orchestration.check_versions import TickResult

    state = TickState()
    healthy_event: dict[str, Any] = {
        "event": "check_tick", "outcome": "no_new_versions", "org_id": "org1",
        "action_id": "x", "dataset_name": "ds1", "anchor": "1.0.0",
        "tick_count": 0, "probed": [],
    }
    indeterminate_event: dict[str, Any] = {
        "event": "check_tick", "outcome": "indeterminate", "org_id": "org1",
        "action_id": "x", "dataset_name": "ds1", "anchor": "1.0.0",
        "tick_count": 0, "probed": [],
    }
    calls = {"n": 0}

    def _fake_check_once(**kwargs: object) -> TickResult:
        n = calls["n"]
        calls["n"] += 1
        # 30 of 40 ticks come back ambiguous -- no exception, no
        # escalation (never 4 consecutive), but no verdict either.
        if n % 4 != 0:
            return TickResult("indeterminate", state, dict(indeterminate_event), [])
        return TickResult("no_new_versions", state, dict(healthy_event), [])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(watch_module, "check_once", _fake_check_once)
    sleep_fn = _stop_after(40)

    try:
        exit_code = run_watch_loop(
            probe=FakeProbe({}),
            sleep_fn=sleep_fn,
            max_consecutive_tick_failures=100,
            **_base_kwargs(),
        )
    finally:
        monkeypatch.undo()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Stopped after 40 tick(s)" in out
    assert "no new versions found." not in out
    assert "no new versions found (" in out
    assert "30 of 40 tick(s) got no answer)" in out


def test_f7_a_refusal_before_any_tick_completes_is_not_misreported_as_no_new_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F-7 (Major, reproduced): `elif healthy_ticks == 0 and failed_ticks
    > 0` was wrong a second way -- a `CheckVersionsRefusedError` halt (an
    unparseable anchor) increments NEITHER counter, so a watcher that
    halts before a single tick completes fell through to the plain "no
    new versions found" `else` branch. Reproduced with an anchor that
    fails strict semver (`_run_walk` never runs; `check_once` raises
    `CheckVersionsRefusedError` before the first probe)."""
    probe = FakeProbe({})
    state = TickState(known_versions=["not-a-semver"])

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(5),
        **{**_base_kwargs(), "state": state, "known_version": None},
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no new versions found" not in out
    assert "no answer" in out
    assert "Stopped after 1 tick(s)" in out


def test_f6_interleaved_failing_and_succeeding_ticks_never_hit_the_consecutive_ceiling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-6 mutant-killer: `test_a_probe_that_fails_every_tick_eventually_
    halts...` uses a FOREVER-failing probe, where "consecutive" and
    "cumulative" failure counts are numerically identical -- deleting the
    `consecutive_tick_failures = 0` reset (the mutant named in the
    brief) survives that test untouched. This test alternates a failing
    tick with a healthy one: consecutive failures never exceed 1, but
    CUMULATIVE failures grow past the ceiling well before the loop is
    asked to stop. If the reset is ever deleted, this test goes RED (the
    watcher wrongly halts partway through instead of running all 10
    requested ticks)."""
    from idp_regression.orchestration import watch as watch_module

    state = TickState()
    healthy_event: dict[str, Any] = {
        "event": "check_tick",
        "outcome": "no_new_versions",
        "org_id": "org1",
        "action_id": "x",
        "dataset_name": "ds1",
        "anchor": "1.0.0",
        "tick_count": 0,
        "probed": [],
    }
    calls = {"n": 0}

    def _fake_check_once(**kwargs: object) -> Any:
        from idp_regression.orchestration.check_versions import TickResult

        n = calls["n"]
        calls["n"] += 1
        if n % 2 == 0:  # every OTHER tick fails -- never two in a row
            raise ConnectionError("simulated transient failure")
        return TickResult("no_new_versions", state, dict(healthy_event), [])

    monkeypatch.setattr(watch_module, "check_once", _fake_check_once)

    exit_code = run_watch_loop(
        probe=FakeProbe({}),
        sleep_fn=_stop_after(10),
        max_consecutive_tick_failures=3,
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 0  # stopped by Ctrl-C (the sleep double), never a halt
    assert "TICK FAILURES" not in out


def test_auto_run_that_raises_does_not_crash_the_whole_watcher(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smaller item: `save_state_atomic` persists a detected version into
    `known_versions` BEFORE `--auto-run` ever fires, so a `run_eval_fn`
    that RAISES (not merely returns a gate-FAIL exit code) used to escape
    uncaught -- crashing the whole watch loop for one failed run, with
    the detection already unretriable. Must be loud (printed + logged)
    but not fatal to the loop."""
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})

    def _raising_run_eval(*args: object, **kwargs: object) -> int:
        raise RuntimeError("simulated run_eval crash")

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(1),
        auto_run=True,
        max_runs_per_tick=5,
        run_eval_fn=_raising_run_eval,
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 0  # the loop itself is healthy -- Ctrl-C'd normally
    assert "FAILED TO RUN" in out
    assert "no verdict" in out


def test_anchor_vanished_halts_the_loop_loudly_and_returns_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = FakeProbe({"1.0.0": ProbeResult.ABSENT, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT})
    sleep_fn = _stop_after(5)

    exit_code = run_watch_loop(probe=probe, sleep_fn=sleep_fn, **_base_kwargs())

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "WATCHER STOPPED" in out
    assert "ANCHOR VANISHED" in out
    assert len(sleep_fn.calls) == 0  # halted before ever sleeping


def test_an_unreachable_positive_control_is_not_misreported_as_anchor_vanished(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """"Cheap one" (2026-09-23 re-review): a positive control that comes
    back UNKNOWN (a 429, a 5xx, a malformed response) halts as
    `anchor_vanished` exactly like a genuine ABSENT -- the exit code and
    the halt are correct either way, since neither is safe to keep
    walking on -- but the human-facing diagnosis is a different claim.
    ABSENT means the anchor version itself is gone; UNKNOWN means the API
    could not be reached cleanly at all. This must not print "ANCHOR
    VANISHED" for the UNKNOWN case."""
    probe = FakeProbe({"1.0.0": ProbeResult.UNKNOWN, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT})
    sleep_fn = _stop_after(5)

    exit_code = run_watch_loop(probe=probe, sleep_fn=sleep_fn, **_base_kwargs())

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "WATCHER STOPPED" in out
    assert "UNREACHABLE" in out
    assert "ANCHOR VANISHED" not in out
    assert len(sleep_fn.calls) == 0  # halted before ever sleeping


# -- Defect 2 (2026-09-24, user repro): the closing summary must never -------
# -- claim "no new versions found" on a halting outcome ----------------------


@pytest.mark.parametrize("halting_outcome", sorted(_HALT_OUTCOMES))
def test_every_halting_outcome_never_lets_the_summary_claim_no_new_versions_found(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    halting_outcome: str,
) -> None:
    """Defect 2 (user repro, 2026-09-24, fourth round on this function):
    a completed tick reporting ANY `_HALT_OUTCOMES` member (not just
    `anchor_vanished`, the one the user's repro happened to hit) used to
    still increment `healthy_ticks` before the halt check ran -- the old
    summary logic keyed on `healthy_ticks > 0`, so it printed "no new
    versions found" on every one of these halts. The invariant: the
    closing summary must never claim "no new versions found" unless the
    loop ended normally without halting. Iterates over every member of
    `_HALT_OUTCOMES` rather than pinning the single outcome from the
    repro (per the brief's explicit instruction), so a fourth halting
    outcome added later is covered for free."""
    from idp_regression.orchestration import watch as watch_module
    from idp_regression.orchestration.check_versions import TickResult

    state = TickState()
    halt_event: dict[str, Any] = {
        "event": "check_tick",
        "outcome": halting_outcome,
        "org_id": "org1",
        "action_id": "x",
        "dataset_name": "ds1",
        "anchor": "1.0.0",
        "tick_count": 0,
        "probed": [],
        "positive_control": str(ProbeResult.ABSENT),
    }

    def _fake_check_once(**kwargs: object) -> TickResult:
        return TickResult(halting_outcome, state, dict(halt_event), [])

    monkeypatch.setattr(watch_module, "check_once", _fake_check_once)

    exit_code = run_watch_loop(
        probe=FakeProbe({}),
        sleep_fn=_stop_after(5),
        **_base_kwargs(),
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Stopped after 1 tick(s)" in out
    assert "no new versions found" not in out
    assert "watcher HALTED" in out


# -- Defect 1 (2026-09-24, user repro): run-identity flags must never --------
# -- reach the network blank or invalid ---------------------------------------

_BLANK_OR_INVALID_IDENTITY_CASES = [
    ("", "12345678-1234-1234-1234-123456789012", "ds1", "--org must not be blank"),
    ("   ", "12345678-1234-1234-1234-123456789012", "ds1", "--org must not be blank"),
    ("org1", "", "ds1", "--action must not be blank"),
    ("org1", "   ", "ds1", "--action must not be blank"),
    ("org1", "not-a-uuid", "ds1", "--action is not a valid UUID"),
    ("org1", "12345678-1234-1234-1234-123456789012", "", "--dataset must not be blank"),
    ("org1", "12345678-1234-1234-1234-123456789012", "   ", "--dataset must not be blank"),
]


@pytest.mark.parametrize(
    "org,action,dataset,expected_message", _BLANK_OR_INVALID_IDENTITY_CASES
)
def test_watch_main_rejects_a_blank_or_invalid_run_identity_flag_pre_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    org: str,
    action: str,
    dataset: str,
    expected_message: str,
) -> None:
    """Defect 1 (user repro, 2026-09-24): a shell that had lost its env
    vars made `--org "" --action "" --dataset ""` expand to blank
    strings -- argparse's own `required=True` only checks the flag is
    PRESENT, not non-empty, so the blanks sailed straight into the probe
    URL. The probe then came back an ambiguous UNKNOWN, which the watcher
    reported as "ANCHOR CHECK UNREACHABLE" -- a diagnosis about the
    NETWORK for what was actually "you passed nothing". Must be rejected
    here, exit 1, before any probe call -- mirrors `cli.py`'s ADR-0004 A8
    shape for `run_eval`'s identical `--org`/`--action`/`--dataset`
    flags (a MISSING flag is unchanged: that is still argparse's own
    exit 2, not this guard)."""
    exit_code = main(
        [
            "--org",
            org,
            "--action",
            action,
            "--dataset",
            dataset,
            "--state-file",
            str(tmp_path / "state.json"),
        ]
    )
    assert exit_code == 1
    assert expected_message in capsys.readouterr().err


# -- --auto-run: opt-in, bounded, spends quota --------------------------------


def test_auto_run_off_by_default_never_calls_run_eval() -> None:
    """S-01.4 re-stamp #4 F-2: a spy that RECORDS rather than raises --
    `_run_auto_run` wraps `run_eval_fn` in a broad `except Exception`
    (by design, so a failed run doesn't crash the watcher), which
    swallowed the old `_fail_if_called` sentinel's AssertionError
    silently. Mutating `if auto_run:` to `if True:` survived the whole
    suite because of exactly that: the assertion never had anywhere to
    land. Observing the call count directly is what tells them apart."""
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    calls: list[tuple[object, ...]] = []

    def _spy_run_eval(*args: object, **kwargs: object) -> int:
        calls.append(args)
        return 0

    run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(1),
        run_eval_fn=_spy_run_eval,
        # max_runs_per_tick=1 so a wrongly-True auto_run WOULD reach the
        # spy -- left at its own default (0), the "$max_runs_per_tick
        # reached" skip would hide the mutant regardless of auto_run.
        max_runs_per_tick=1,
        **_base_kwargs(),
    )

    assert calls == []


def test_auto_run_is_bounded_by_max_runs_per_tick(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe(
        {
            **_controls_ok("1.0.0"),
            "1.0.1": ProbeResult.EXISTS,
            "1.1.0": ProbeResult.EXISTS,
            "2.0.0": ProbeResult.EXISTS,
        }
    )
    calls: list[tuple[object, ...]] = []

    def _spy_run_eval(*args: object, **kwargs: object) -> int:
        calls.append(args)
        return 0

    run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(1),
        auto_run=True,
        max_runs_per_tick=2,
        run_eval_fn=_spy_run_eval,
        **_base_kwargs(
            patch_lookahead=3, minor_lookahead=3, major_lookahead=2, max_probes_per_tick=40
        ),
    )

    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "spends real IDP quota" in out
    assert "left for a future manual run" in out


def test_a_gate_fail_from_auto_run_is_never_retried(capsys: pytest.CaptureFixture[str]) -> None:
    """A FAIL is a successful detection-and-run (ADR-0006 A'.5) -- the
    version is already folded into state.known_versions by check_once
    regardless of the run's outcome, so a second tick must not see it as
    `new_version_detected` again and must not call run_eval for it twice.

    F-4 fix: the name's load-bearing half -- the gate FAIL itself -- is
    now actually asserted (mutating `_failing_run_eval` to `return 0`
    must turn this test RED), and the loop's own exit code is pinned as
    NOT reflecting a --auto-run gate outcome (see the ADR-0006 A'.5
    implementation note this test cites for the "why")."""
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    calls: list[tuple[object, ...]] = []

    def _failing_run_eval(*args: object, **kwargs: object) -> int:
        calls.append(args)
        return 1  # gate FAIL

    exit_code = run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(2),
        auto_run=True,
        max_runs_per_tick=5,
        run_eval_fn=_failing_run_eval,
        **_base_kwargs(),
    )

    assert len(calls) == 1
    out = capsys.readouterr().out
    # the load-bearing half: the gate FAIL is surfaced on the console the
    # sponsor is watching, since there is no alerting sink yet (ADR-0006
    # A'.5).
    assert "gate FAIL or abort" in out
    # the decision (recorded in ADR-0006 A'.5): a --auto-run gate outcome
    # does NOT propagate into run_watch_loop's own exit code -- the loop
    # keeps running (this is a foreground watcher, not a one-shot gate),
    # so its exit code reflects the WATCHER's own health (halted vs.
    # Ctrl-C'd), never a single run's verdict.
    assert exit_code == 0


# -- CLI wiring ----------------------------------------------------------------


def test_main_rejects_auto_run_without_max_runs_per_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(tmp_path / "state.json"),
            "--auto-run",
        ]
    )
    assert exit_code == 1
    assert "--auto-run requires --max-runs-per-tick" in capsys.readouterr().err


def test_main_rejects_max_runs_per_tick_without_auto_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(tmp_path / "state.json"),
            "--max-runs-per-tick",
            "3",
        ]
    )
    assert exit_code == 1
    assert "--max-runs-per-tick has no effect without --auto-run" in capsys.readouterr().err


def test_main_rejects_a_state_file_inside_the_repo(capsys: pytest.CaptureFixture[str]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    inside = repo_root / "docs" / "state" / "watch-state.json"

    exit_code = main(
        [
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(inside),
        ]
    )
    assert exit_code == 1
    assert "must be OUTSIDE the repository" in capsys.readouterr().err


def test_default_interval_is_five_minutes() -> None:
    assert DEFAULT_INTERVAL_SECONDS == 300


def test_startup_banner_shows_the_highest_version_numerically_not_lexicographically(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Suggestion fix (2026-09-23): plain `max()` over version strings is
    lexicographic -- "1.9.0" would beat "1.10.0". This is display-only,
    but it is the one line a non-engineer sponsor is actually watching."""
    probe = FakeProbe(_controls_ok("1.10.0"))
    state = TickState(known_versions=["1.2.0", "1.10.0", "1.9.0"])

    run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(1),
        **{**_base_kwargs(), "state": state, "known_version": None},
    )

    banner = capsys.readouterr().out.splitlines()[0]
    assert "anchor=1.10.0" in banner


# -- RQ-1/RQ-2 (2026-09-23 re-review, PIN): the lock must survive a save ----


def test_lock_survives_a_state_file_save_mid_loop(tmp_path: Path) -> None:
    """RQ-2: the two existing R2 tests pass for the wrong reason -- one
    locks BEFORE `main()` runs (before any save), the other only asserts
    release after a halt. Neither exercises the claimed property ("held
    for the entire foreground loop's lifetime").

    `flock` locks the INODE a path resolves to at open() time.
    `save_state_atomic()`'s `os.replace(tmp, path)` retargets `path` to a
    BRAND-NEW inode -- a lock taken directly on the data file's own
    inode is silently orphaned by the very first save. `run_watch_loop`
    saves every tick, so from tick 2 onward a watcher holding such a lock
    would run the rest of a potentially days-long loop completely
    unlocked (RQ-1).

    This test acquires the lock exactly as `watch.main()` does, runs
    >=1 tick (so `save_state_atomic` actually fires), and THEN attempts a
    second lock on the same path while the first is still held -- proving
    the lock still blocks a second instance after a save, not just before
    one. Before the RQ-1 fix this test is RED (the second lock wrongly
    succeeds, because `save_state_atomic`'s `os.replace()` already moved
    the locked inode out from under the first fd)."""
    state_path = tmp_path / "state.json"
    probe = FakeProbe(_controls_ok("1.0.0"))

    fd = open_state_file_locked(state_path)
    try:
        exit_code = run_watch_loop(
            probe=probe,
            sleep_fn=_stop_after(1),
            state_file=state_path,
            **_base_kwargs(),
        )
        assert exit_code == 0
        assert state_path.exists(), "save_state_atomic must have fired at least once"

        with pytest.raises(StateFileLockedError):
            open_state_file_locked(state_path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# -- R2: the state-file lock is held for the WHOLE loop's lifetime ----------


def test_watch_main_refuses_to_start_when_the_state_file_is_already_locked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_path = tmp_path / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # RQ-1: lock the SIDECAR path -- what `open_state_file_locked` (and
    # therefore `watch.main()`) actually locks now, not `state_path`
    # itself.
    fd = os.open(str(_lock_path_for(state_path)), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        exit_code = main(
            [
                "--org", "org1",
                "--action", "12345678-1234-1234-1234-123456789012",
                "--dataset", "ds1",
                "--state-file", str(state_path),
            ]
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "locked by another running" in err


def test_watch_main_releases_the_lock_after_a_halt_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: the lock is now held for the loop's whole lifetime, so it is
    just as important that it is actually RELEASED once the loop ends
    -- otherwise every watch invocation after the first would falsely
    report `skipped_locked` forever."""
    monkeypatch.setenv("IDP_CLIENT_ID", "id")
    monkeypatch.setenv("IDP_CLIENT_SECRET", "secret")
    monkeypatch.setenv("IDP_REGION", "us")
    monkeypatch.setattr("idp_regression.orchestration.watch.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "idp_regression.orchestration.watch.MuleSoftVersionProbe",
        lambda *a, **k: FakeProbe(
            {"1.0.0": ProbeResult.ABSENT, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT}
        ),
    )
    state_path = tmp_path / "state.json"

    exit_code = main(
        [
            "--org", "org1",
            "--action", "12345678-1234-1234-1234-123456789012",
            "--dataset", "ds1",
            "--state-file", str(state_path),
            "--known-version", "1.0.0",
        ]
    )

    assert exit_code == 1  # anchor_vanished halt, before any sleep

    # A fresh lock attempt must succeed -- proves main()'s `finally`
    # actually released it rather than leaking the fd. RQ-1: the lock
    # lives on the SIDECAR path, so THAT is what must be provably
    # unlocked, not `state_path` itself (which was never what
    # `open_state_file_locked` locks after the RQ-1 fix).
    fd = os.open(str(_lock_path_for(state_path)), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# -- R4: `.env` loading, consistent with cli.py / facade.py -----------------


def test_watch_main_a_load_dotenv_failure_is_a_controlled_exit_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S-01.4 re-stamp #4 F-5 (M18): a bare `exit_code == 1` is not
    mutation-sensitive here -- `_run()` reaches its OWN exit-1 path
    further down (a missing credential, an unreachable state file) with
    `load_dotenv()` deleted entirely, since nothing downstream depends on
    it having run in this fixture. The specific log message pins that
    `load_dotenv()` was actually called and actually raised."""
    def _raise() -> None:
        raise OSError("simulated unreadable .env")

    monkeypatch.setattr("idp_regression.orchestration.watch.load_dotenv", _raise)
    state_path = tmp_path / "state.json"

    with caplog.at_level(logging.ERROR):
        exit_code = main(
            [
                "--org", "org1",
                "--action", "12345678-1234-1234-1234-123456789012",
                "--dataset", "ds1",
                "--state-file", str(state_path),
            ]
        )

    assert exit_code == 1
    assert "watch: unexpected error loading .env: OSError" in caplog.text


# -- F-2: an OSError from state-file prep must never escape as a raw ---------
# -- traceback carrying a filesystem path (INV-02) ---------------------------


def test_watch_main_the_live_repro_an_isadirectoryerror_from_load_state_is_a_controlled_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-2 (Critical, STILL NOT CLOSED after the round that added the two
    site-specific `except Exception` clauses above `load_state_from_file`'s
    call site): the THIRD site. `load_state_from_file` does a bare
    `os.open(...)` (`check_versions.py`); the try wrapping its call in
    `main()` (now `_run()`) only ever caught `CheckVersionsRefusedError`. This
    is the exact live repro (2026-09-24): a `--state-file` path that is
    actually a directory makes that `os.open()` raise `IsADirectoryError`,
    which used to escape `main()` raw, carrying both the state-file path
    AND the absolute deployment path of the checkout in the traceback."""
    state_path = tmp_path / "state.json"
    state_path.mkdir()  # a directory sits where a file is expected

    exit_code = main(
        [
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(state_path),
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(state_path) not in err
    # the absolute deployment path of the checkout must not leak either
    assert str(Path(__file__).resolve().parents[2]) not in err


@pytest.mark.parametrize(
    "target",
    [
        # F-2's real invariant: "no exception from any state-file I/O --
        # or from anywhere else in main() -- escapes raw", not "these two
        # calls are guarded". Neither of these two sites was individually
        # wrapped before this fix -- `load_state_from_file`'s try only
        # ever caught `CheckVersionsRefusedError` (the exact third site the
        # coordinator's repro names), and the `MuleSoftVersionProbe(...)`
        # construction sits between two `try` blocks with no guard of its
        # own at all. The old tests only proved the two sites the PRIOR
        # fix round happened to touch (`Path.mkdir`,
        # `open_state_file_locked`) -- exactly the "closes the case
        # reported, not the invariant violated" pattern named in the
        # brief. A third or fourth site added tomorrow needs no new test
        # here, because the invariant is now enforced structurally
        # (`main()`'s one outer catch-all), not enumerated.
        "idp_regression.orchestration.watch.load_state_from_file",
        "idp_regression.orchestration.watch.MuleSoftVersionProbe",
    ],
)
def test_watch_main_no_exception_from_any_site_escapes_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
) -> None:
    sentinel = "should-never-leak-a-raw-traceback"
    monkeypatch.setenv("IDP_CLIENT_ID", "id")
    monkeypatch.setenv("IDP_CLIENT_SECRET", "secret")
    monkeypatch.setenv("IDP_REGION", "us")
    monkeypatch.setattr("idp_regression.orchestration.watch.load_dotenv", lambda: None)

    def _boom(*args: object, **kwargs: object) -> Any:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(target, _boom)

    exit_code = main(
        [
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(tmp_path / "state.json"),
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    # only the exception's TYPE name is ever surfaced, never str(exc)
    assert sentinel not in err


def test_check_versions_main_an_oserror_preparing_the_state_file_is_a_controlled_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-2, the identical shape at `check_versions.py:765-768` -- the
    outer `try` there only ever caught `CheckVersionsRefusedError`, so an
    `OSError` from `mkdir` (or from anything else under it, e.g.
    `open_state_file_locked` raising a bare `OSError`) escaped
    `check_versions.main()` as a raw traceback too. Named, not
    line-numbered (DEBT-42): this test lives in test_watch.py per this
    task's declared test-file scope, exercising `check_versions.main`
    directly via the module import at the top of this file."""
    sentinel = "/some/should-never-leak/cv-path"

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError(f"[Errno 13] Permission denied: '{sentinel}'")

    monkeypatch.setattr(Path, "mkdir", _boom)
    monkeypatch.setattr(check_versions_module, "load_dotenv", lambda: None)

    exit_code = check_versions_module.main(
        [
            "--org", "org1",
            "--action", "12345678-1234-1234-1234-123456789012",
            "--dataset", "ds1",
            "--state-file", str(tmp_path / "sub" / "state.json"),
            "--max-probes-per-tick", "20",
            "--max-probes-per-sweep", "40",
            "--patch-lookahead", "3",
            "--minor-lookahead", "3",
            "--major-lookahead", "1",
            "--sweep-every-n-ticks", "10",
            "--max-indeterminate-ticks", "3",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert sentinel not in captured.err
    assert sentinel not in captured.out


# -- Suggestion: --max-runs-per-tick must be >= 1 ----------------------------


@pytest.mark.parametrize("bad_value", ["0", "-1"])
def test_main_rejects_a_non_positive_max_runs_per_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_value: str
) -> None:
    exit_code = main(
        [
            "--org", "org1",
            "--action", "12345678-1234-1234-1234-123456789012",
            "--dataset", "ds1",
            "--state-file", str(tmp_path / "state.json"),
            "--auto-run",
            "--max-runs-per-tick", bad_value,
        ]
    )
    assert exit_code == 1
    assert "--max-runs-per-tick must be >= 1" in capsys.readouterr().err


def test_watch_logger_name_is_stable_under_python_dash_m_invocation(tmp_path: Path) -> None:
    """Mirrors tests/orchestration/test_logging_config.py's cli.py pin --
    the identical `__main__`-orphaning bug, fixed the same way, in this
    new module. Triggered via --auto-run's own pre-network validation
    error so the subprocess never needs IDP credentials."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "idp_regression.orchestration.watch",
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(tmp_path / "state.json"),
            "--auto-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "idp_regression.orchestration.watch" in result.stderr
    assert "--auto-run requires --max-runs-per-tick" in result.stderr
