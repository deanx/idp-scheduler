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
from idp_regression.orchestration.check_versions import (
    StateFileLocked,
    TickState,
    _lock_path_for,
    open_state_file_locked,
)
from idp_regression.orchestration.watch import DEFAULT_INTERVAL_SECONDS, main, run_watch_loop


class FakeProbe:
    """Same scripted double as tests/orchestration/test_check_versions.py
    -- any version not in `responses` is ABSENT (a real vendor 404s an
    un-probed version)."""

    def __init__(self, responses: dict[str, ProbeResult]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.last_status_code: int | None = 200

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        self.calls.append(version)
        return self.responses.get(version, ProbeResult.ABSENT)


class RaisesOnceThenProbe:
    """A transient failure on its first call (a network blip), then
    delegates to a FakeProbe -- proves a tick failure doesn't kill the
    loop."""

    def __init__(self, inner: FakeProbe) -> None:
        self.inner = inner
        self._raised = False

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        if not self._raised:
            self._raised = True
            raise ConnectionError("simulated transient network failure")
        return self.inner.probe(org_id, action_id, version)


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
        assert "no new versions" in line
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
    assert "no new versions" in out
    assert len(sleep_fn.calls) == 2


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


# -- --auto-run: opt-in, bounded, spends quota --------------------------------


def test_auto_run_off_by_default_never_calls_run_eval() -> None:
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})

    def _fail_if_called(*args: object, **kwargs: object) -> int:
        raise AssertionError("run_eval must not be called -- auto_run is False")

    run_watch_loop(
        probe=probe, sleep_fn=_stop_after(1), run_eval_fn=_fail_if_called, **_base_kwargs()
    )


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


def test_a_gate_fail_from_auto_run_is_never_retried() -> None:
    """A FAIL is a successful detection-and-run (ADR-0006 A'.5) -- the
    version is already folded into state.known_versions by check_once
    regardless of the run's outcome, so a second tick must not see it as
    `new_version_detected` again and must not call run_eval for it twice."""
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    calls: list[tuple[object, ...]] = []

    def _failing_run_eval(*args: object, **kwargs: object) -> int:
        calls.append(args)
        return 1  # gate FAIL

    run_watch_loop(
        probe=probe,
        sleep_fn=_stop_after(2),
        auto_run=True,
        max_runs_per_tick=5,
        run_eval_fn=_failing_run_eval,
        **_base_kwargs(),
    )

    assert len(calls) == 1


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

        with pytest.raises(StateFileLocked):
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> None:
        raise OSError("simulated unreadable .env")

    monkeypatch.setattr("idp_regression.orchestration.watch.load_dotenv", _raise)
    state_path = tmp_path / "state.json"

    exit_code = main(
        [
            "--org", "org1",
            "--action", "12345678-1234-1234-1234-123456789012",
            "--dataset", "ds1",
            "--state-file", str(state_path),
        ]
    )

    assert exit_code == 1


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
