"""ADR-0006 §Decision A' Phase 1 — the check-versions tick (docs/qa/NFR-02.md
Pass D). check_once() is the pure(ish) facade under test here; state-file
I/O and the CLI wiring get their own thin tests further down."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter.version_probe import NEGATIVE_CONTROL_VERSION, ProbeResult
from idp_regression.orchestration.check_versions import (
    OUTCOME_ANCHOR_VANISHED,
    OUTCOME_CEILING_REACHED,
    OUTCOME_DETECTOR_DEGRADED,
    OUTCOME_DISCRIMINATOR_INVALID,
    OUTCOME_INDETERMINATE,
    OUTCOME_NEW_VERSION_DETECTED,
    OUTCOME_NO_NEW_VERSIONS,
    OUTCOME_REFUSED_UNINITIALISED,
    OUTCOME_REFUSED_UNPARSEABLE_VERSION_SCHEME,
    CheckVersionsRefused,
    StateFileLocked,
    TickState,
    _lock_path_for,
    _reject_state_file_inside_repo,
    check_once,
    load_state,
    main,
    open_state_file_locked,
    save_state_atomic,
)


class FakeProbe:
    """A scripted IDPVersionProbe test double: ``responses`` maps a
    version string to a ProbeResult; any version not in the map is
    ABSENT by default (a real vendor would 404 an un-probed version)."""

    def __init__(
        self,
        responses: dict[str, ProbeResult],
        rate_limit_after: int | None = None,
        default: ProbeResult = ProbeResult.ABSENT,
    ):
        self.responses = responses
        self.calls: list[str] = []
        self.rate_limit_after = rate_limit_after
        self.default = default
        self.last_status_code: int | None = None

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        self.calls.append(version)
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            self.last_status_code = 429
            return ProbeResult.UNKNOWN
        self.last_status_code = 200
        return self.responses.get(version, self.default)


def _base_kwargs(**overrides: object) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        org_id="org1",
        action_id="action1",
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
    )
    kwargs.update(overrides)
    return kwargs


def _controls_ok(anchor: str) -> dict[str, ProbeResult]:
    return {anchor: ProbeResult.EXISTS, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT}


# -- Fail-closed bootstrap (§A'.6 / D9) --------------------------------------


def test_no_anchor_at_all_refuses_to_run() -> None:
    with pytest.raises(CheckVersionsRefused) as exc:
        check_once(probe=FakeProbe({}), **_base_kwargs(state=TickState(), known_version=None))
    assert exc.value.outcome == OUTCOME_REFUSED_UNINITIALISED


@pytest.mark.parametrize("bad_anchor", ["v2-draft", "1.0", "", "1.0.0-beta"])
def test_an_unparseable_anchor_refuses_to_run(bad_anchor: str) -> None:
    with pytest.raises(CheckVersionsRefused) as exc:
        check_once(
            probe=FakeProbe({}),
            **_base_kwargs(state=TickState(known_versions=[bad_anchor]), known_version=None),
        )
    assert exc.value.outcome == OUTCOME_REFUSED_UNPARSEABLE_VERSION_SCHEME


# -- Control probes (§A'.7 / D8) ---------------------------------------------


def test_positive_control_failure_halts_as_anchor_vanished() -> None:
    probe = FakeProbe({"1.0.0": ProbeResult.ABSENT, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT})
    result = check_once(probe=probe, **_base_kwargs())
    assert result.outcome == OUTCOME_ANCHOR_VANISHED
    assert result.event["outcome"] == OUTCOME_ANCHOR_VANISHED


def test_negative_control_failure_halts_as_discriminator_invalid() -> None:
    probe = FakeProbe({"1.0.0": ProbeResult.EXISTS, NEGATIVE_CONTROL_VERSION: ProbeResult.EXISTS})
    result = check_once(probe=probe, **_base_kwargs())
    assert result.outcome == OUTCOME_DISCRIMINATOR_INVALID


def test_a_violated_control_never_reports_no_new_versions() -> None:
    probe = FakeProbe({"1.0.0": ProbeResult.ABSENT, NEGATIVE_CONTROL_VERSION: ProbeResult.ABSENT})
    result = check_once(probe=probe, **_base_kwargs())
    assert result.outcome != OUTCOME_NO_NEW_VERSIONS


def test_controls_pass_and_no_new_version_exists() -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    result = check_once(probe=probe, **_base_kwargs())
    assert result.outcome == OUTCOME_NO_NEW_VERSIONS
    assert result.new_state.known_versions == ["1.0.0"]


# -- The re-anchoring walk (§A'.2) -------------------------------------------


def test_walk_reaches_1_5_7_via_1_5_0_in_one_tick() -> None:
    """The exact scenario Addendum 3 warns a static grid misses: with
    minor-lookahead >= 5, the minor axis hits 1.5.0, re-anchors, and the
    patch axis from there reaches 1.5.7 — all within one tick."""
    responses = _controls_ok("1.0.0")
    responses["1.5.0"] = ProbeResult.EXISTS
    for p in range(1, 8):
        responses[f"1.5.{p}"] = ProbeResult.EXISTS if p <= 7 else ProbeResult.ABSENT
    probe = FakeProbe(responses)
    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=200, patch_lookahead=10, minor_lookahead=5, major_lookahead=0
        ),
    )
    assert result.outcome == OUTCOME_NEW_VERSION_DETECTED
    assert "1.5.7" in result.new_state.known_versions
    assert "1.5.0" in result.new_state.known_versions


def test_a_patch_chain_is_followed_within_one_tick() -> None:
    responses = _controls_ok("1.0.0")
    responses.update({"1.0.1": ProbeResult.EXISTS, "1.0.2": ProbeResult.EXISTS})
    probe = FakeProbe(responses)
    result = check_once(
        probe=probe,
        **_base_kwargs(max_probes_per_tick=50, patch_lookahead=3, minor_lookahead=0),
    )
    assert result.outcome == OUTCOME_NEW_VERSION_DETECTED
    assert set(result.new_state.known_versions) >= {"1.0.0", "1.0.1", "1.0.2"}
    assert len(result.run_eval_commands) == 2


# -- ceiling_reached is a DISTINCT outcome from no_new_versions (D7) ---------


def test_budget_exhausted_mid_hit_is_ceiling_reached_not_no_new_versions() -> None:
    responses = _controls_ok("1.0.0")
    responses["1.0.1"] = ProbeResult.EXISTS
    responses["1.0.2"] = ProbeResult.EXISTS
    probe = FakeProbe(responses)
    # 2 control probes + exactly 1 walk probe budget -- the walk finds
    # 1.0.1 (a hit) and re-anchors, but has no budget left to look further.
    result = check_once(
        probe=probe,
        **_base_kwargs(max_probes_per_tick=1, patch_lookahead=5, minor_lookahead=0),
    )
    assert result.outcome != OUTCOME_NO_NEW_VERSIONS
    # A hit WAS found this tick, so new_version_detected takes priority
    # over ceiling_reached in this design's stated outcome-priority order
    # (documented in check_versions.check_once) -- but ceiling_reached
    # must still be recorded on the event so a human can see truncation.
    assert result.event["ceiling_reached"] is True


def test_budget_exhausted_with_no_hits_at_all_is_ceiling_reached() -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    result = check_once(
        probe=probe,
        **_base_kwargs(max_probes_per_tick=1, patch_lookahead=5, minor_lookahead=0),
    )
    assert result.outcome == OUTCOME_CEILING_REACHED
    assert result.outcome != OUTCOME_NO_NEW_VERSIONS


def test_ceiling_reached_and_no_new_versions_never_share_a_value() -> None:
    assert OUTCOME_CEILING_REACHED != OUTCOME_NO_NEW_VERSIONS


# -- UNKNOWN never terminates a walk as ABSENT (D6) --------------------------


def test_pending_unknowns_is_capped_rather_than_growing_unbounded() -> None:
    """"Cheap one" (2026-09-23 re-review): `pending_unknowns` had no cap
    or ageing -- a persistently-ambiguous endpoint could park the walk
    behind an ever-growing re-probe list forever. This is a hard cap
    (`MAX_PENDING_UNKNOWNS`), not true LRU ageing (see the constant's
    docstring for why ageing is out of scope) -- it just proves growth is
    bounded and visible on the event."""
    from idp_regression.orchestration.check_versions import MAX_PENDING_UNKNOWNS

    many_pending = [f"9.{i}.0" for i in range(MAX_PENDING_UNKNOWNS + 50)]
    state = TickState(known_versions=["1.0.0"], pending_unknowns=many_pending)
    # Every pending candidate stays UNKNOWN (never resolved) so the tick's
    # `all_unknowns` set stays at/above the cap.
    probe = FakeProbe(_controls_ok("1.0.0"), default=ProbeResult.UNKNOWN)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            state=state,
            known_version=None,
            max_probes_per_tick=len(many_pending) + 10,
            patch_lookahead=0,
            minor_lookahead=0,
            major_lookahead=0,
        ),
    )

    assert len(result.new_state.pending_unknowns) <= MAX_PENDING_UNKNOWNS
    assert result.event["pending_unknowns_capped"] is True


def test_an_unknown_candidate_never_becomes_absent_and_is_recorded() -> None:
    class UnknownOnceProbe:
        last_status_code: int | None = None

        def __init__(self) -> None:
            self.calls: list[str] = []

        def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
            self.calls.append(version)
            if version == "1.0.0":
                return ProbeResult.EXISTS
            if version == NEGATIVE_CONTROL_VERSION:
                return ProbeResult.ABSENT
            if version == "1.0.1":
                return ProbeResult.UNKNOWN
            return ProbeResult.ABSENT

    probe = UnknownOnceProbe()
    result = check_once(
        probe=probe,
        **_base_kwargs(max_probes_per_tick=10, patch_lookahead=1, minor_lookahead=0),
    )
    assert "1.0.1" not in result.new_state.known_versions
    assert "1.0.1" in result.new_state.pending_unknowns
    assert result.outcome == OUTCOME_INDETERMINATE


def test_a_pending_unknown_is_re_probed_next_tick_before_the_walk_advances() -> None:
    state = TickState(known_versions=["1.0.0"], pending_unknowns=["1.0.1"])
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    result = check_once(
        probe=probe, **_base_kwargs(state=state, known_version=None, patch_lookahead=1)
    )
    assert probe.calls[0:2] == ["1.0.0", NEGATIVE_CONTROL_VERSION] or set(
        probe.calls[:2]
    ) == {"1.0.0", NEGATIVE_CONTROL_VERSION}
    assert "1.0.1" in probe.calls
    assert "1.0.1" in result.new_state.known_versions
    assert "1.0.1" not in result.new_state.pending_unknowns


def test_a_429_on_the_first_pending_unknown_abandons_the_rest_of_the_tick_rq3() -> None:
    """RQ-3 (2026-09-23 re-review): `_resolve_unknowns` never inspected
    `last_status_code` at all before this fix -- a 429 on the FIRST
    pending unknown re-probed the REST of the pending list, then fell
    through into the walk's full grid, directly violating ADR-0006
    §A'.4's unqualified "abandon the remaining probes for this tick"."""
    state = TickState(known_versions=["1.0.0"], pending_unknowns=["1.0.1", "1.0.2"])
    # 2 control probes succeed; every call from the 3rd onward is a
    # 429/UNKNOWN -- so the FIRST pending-unknown probe is already
    # rate-limited.
    probe = FakeProbe({**_controls_ok("1.0.0")}, rate_limit_after=2)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            state=state,
            known_version=None,
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
        ),
    )

    # Only the first pending unknown was actually probed -- the second
    # pending item, and the walk's entire grid, must be abandoned.
    assert probe.calls[2:] == ["1.0.1"]
    assert result.event["probed"] == ["1.0.1"]
    assert result.event["rate_limited"] is True
    assert "1.0.2" in result.new_state.pending_unknowns


# -- indeterminate counter -> detector_degraded (D13) ------------------------


def test_a_single_indeterminate_tick_does_not_degrade() -> None:
    probe = FakeProbe({**_controls_ok("1.0.0")}, rate_limit_after=2)
    result = check_once(
        probe=probe,
        **_base_kwargs(
            state=TickState(consecutive_indeterminate_ticks=0),
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            max_indeterminate_ticks=3,
        ),
    )
    assert result.outcome == OUTCOME_INDETERMINATE
    assert result.new_state.consecutive_indeterminate_ticks == 1


def test_exceeding_max_indeterminate_ticks_becomes_detector_degraded() -> None:
    probe = FakeProbe({**_controls_ok("1.0.0")}, rate_limit_after=2)
    result = check_once(
        probe=probe,
        **_base_kwargs(
            state=TickState(consecutive_indeterminate_ticks=3),
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            max_indeterminate_ticks=3,
        ),
    )
    assert result.outcome == OUTCOME_DETECTOR_DEGRADED


def test_a_clean_tick_resets_the_indeterminate_counter() -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    result = check_once(
        probe=probe,
        **_base_kwargs(state=TickState(consecutive_indeterminate_ticks=2)),
    )
    assert result.outcome == OUTCOME_NO_NEW_VERSIONS
    assert result.new_state.consecutive_indeterminate_ticks == 0


# -- the check_tick event carries the full probed grid (D10) -----------------


def test_check_tick_event_carries_the_full_probed_grid_on_a_no_op_tick() -> None:
    probe = FakeProbe(_controls_ok("1.0.0"))
    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=6, patch_lookahead=3, minor_lookahead=0, major_lookahead=0
        ),
    )
    assert result.event["probed"] == ["1.0.1", "1.0.2", "1.0.3"]
    assert result.event["hits"] == []
    assert result.event["unknowns"] == []
    assert "ceiling_reached" in result.event
    assert "days_since_anchor_changed" in result.event
    assert "ticks_since_last_detection" in result.event


# -- state-file I/O (tier 2, §A'.6 / D16 / M12-style schema guard) ----------


def test_state_round_trips_through_json(tmp_path: Path) -> None:
    state = TickState(known_versions=["1.0.0", "1.0.1"], tick_count=4)
    path = tmp_path / "state.json"
    save_state_atomic(path, state)
    with open(path) as fh:
        loaded = load_state(fh)
    assert loaded.known_versions == ["1.0.0", "1.0.1"]
    assert loaded.tick_count == 4


def test_an_unrecognised_schema_version_is_a_fail_closed_halt(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 999}))
    with open(path) as fh, pytest.raises(CheckVersionsRefused) as exc:
        load_state(fh)
    assert exc.value.outcome == OUTCOME_REFUSED_UNINITIALISED


def test_an_empty_state_file_is_treated_as_first_run(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("")
    with open(path) as fh:
        state = load_state(fh)
    assert state.known_versions == []


def test_deleting_the_state_file_costs_only_a_redundant_walk(tmp_path: Path) -> None:
    """D16: the cache is explicitly non-authoritative. Losing it and
    re-supplying --known-version must still detect correctly."""
    probe = FakeProbe({**_controls_ok("1.0.0"), "1.0.1": ProbeResult.EXISTS})
    result = check_once(probe=probe, **_base_kwargs(state=TickState(), known_version="1.0.0"))
    assert result.outcome == OUTCOME_NEW_VERSION_DETECTED
    assert "1.0.1" in result.new_state.known_versions


def test_state_file_inside_the_repo_is_rejected(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    inside = repo_root / "docs" / "state" / "check-versions-state.json"
    with pytest.raises(CheckVersionsRefused):
        _reject_state_file_inside_repo(inside)


def test_state_file_outside_the_repo_is_accepted(tmp_path: Path) -> None:
    _reject_state_file_inside_repo(tmp_path / "state.json")  # must not raise



def test_run_eval_command_is_literally_runnable() -> None:
    """The detection banner's "ready to paste" command must actually run.

    Phase 1's entire product is that line: on a hit the watcher prints a
    command a human pastes. It previously emitted ``idp-regression --org ...``,
    which is NOT an installed console script -- ``pyproject.toml`` declares no
    ``[project.scripts]`` and ``.venv/bin`` holds no ``idp-*`` entry point --
    so the one line the feature exists to produce would have given the reader
    "command not found".

    This pin RUNS the emitted module with ``--help`` rather than asserting on
    the text, so it fails if the entry point is renamed, moved or made
    non-runnable, not merely if the wording changes.
    """
    responses = _controls_ok("1.0.0")
    responses["1.0.1"] = ProbeResult.EXISTS
    result = check_once(probe=FakeProbe(responses), **_base_kwargs())

    assert result.outcome == OUTCOME_NEW_VERSION_DETECTED
    assert result.run_eval_commands, "a detected version must yield a command"

    tokens = result.run_eval_commands[0].split()
    assert tokens[1] == "-m", f"expected a `-m module` invocation, got: {tokens[:3]}"
    module = tokens[2]

    completed = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, (
        f"the pasted command's module is not runnable: {module}\n"
        f"stderr: {completed.stderr[:400]}"
    )
    for flag in ("--org", "--action", "--version", "--dataset", "--run"):
        assert flag in completed.stdout, f"{module} --help does not offer {flag}"


# -- The periodic sweep (§A'.2) -- driver-level tests (C1 / C2 / R1) ---------
#
# Before this pass, the sweep path had NO driver-level tests at all -- only
# `sweep_candidates` (the pure generator) was exercised, which is how C1 and
# R1 survived 53 new tests. Every test below drives `check_once` end to end
# with `sweep_every_n_ticks=1` so the sweep actually runs.


def test_sweep_unknowns_fold_into_the_ticks_unknowns_c1_regression_pin() -> None:
    """C1 REGRESSION PIN (reviewer, 2026-09-23, REQUEST CHANGES on 97f3d13 /
    49939b2). Before the fix, the sweep loop tested `result` only against
    `ProbeResult.EXISTS`, so UNKNOWN and ABSENT were indistinguishable --
    reproduced live with a fake probe (anchor EXISTS, negative control
    ABSENT, the walk's own candidates confirmed ABSENT, every SWEEP probe
    UNKNOWN): the tick reported `no_new_versions`, exit 0, with 41 of 50
    probes UNKNOWN and the sweep's own probes entirely absent from the
    event.

    A tick whose sweep returns only UNKNOWN must NEVER produce
    `no_new_versions`."""
    anchor = "1.0.0"
    responses = _controls_ok(anchor)
    # The walk's own three candidates are confirmed ABSENT -- the walk
    # alone, with no sweep, would legitimately report no_new_versions.
    for p in (1, 2, 3):
        responses[f"1.0.{p}"] = ProbeResult.ABSENT
    # Everything else (every sweep candidate) is UNKNOWN.
    probe = FakeProbe(responses, default=ProbeResult.UNKNOWN)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=50,
        ),
    )

    assert result.outcome != OUTCOME_NO_NEW_VERSIONS, (
        "an all-UNKNOWN sweep must never be read as no_new_versions (C1)"
    )
    assert result.outcome == OUTCOME_INDETERMINATE
    assert result.event["sweep_ran"] is True
    assert len(result.event["sweep_unknowns"]) > 0
    # every sweep UNKNOWN must be re-probed next tick, exactly like a walk
    # UNKNOWN already was before this fix
    assert set(result.event["sweep_unknowns"]) <= set(result.new_state.pending_unknowns)


def test_sweep_is_skipped_entirely_when_the_walk_rate_limited_c2() -> None:
    """C2 fix: `walk.rate_limited` was declared, set by `_run_walk`, and
    returned on `WalkOutcome` -- but read nowhere. ADR-0006 §A'.4 says a
    429 means UNKNOWN and *abandon the tick's remaining probes*; letting
    the sweep fire anyway issues up to `--max-probes-per-sweep` MORE
    probes at the endpoint that just rate-limited this tick."""
    # 2 control probes succeed, everything after is a 429/UNKNOWN.
    probe = FakeProbe({**_controls_ok("1.0.0")}, rate_limit_after=2)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=50,
        ),
    )

    assert result.event["rate_limited"] is True
    assert result.event["sweep_ran"] is False
    assert result.event["sweep_probed"] == []
    assert result.event["sweep_probe_count"] == 0


def test_a_429_mid_sweep_abandons_the_rest_of_the_sweep_rq3() -> None:
    """RQ-3 (2026-09-23 re-review): C2's fix only wired the 429 check
    into the walk -> sweep edge (a walk that rate-limited skips the
    sweep entirely). The sweep loop itself never inspected
    `last_status_code` at all -- a 429 on the FIRST sweep probe still let
    it fire up to `max_probes_per_sweep - 1` MORE probes at an endpoint
    that had just rate-limited this tick."""
    anchor = "1.0.0"
    responses = _controls_ok(anchor)
    for p in (1, 2, 3):
        responses[f"1.0.{p}"] = ProbeResult.ABSENT
    # 2 controls + the walk's own 3 probes (patch_lookahead=3) = 5 calls,
    # all confirmed ABSENT -- the walk itself never rate-limits. The
    # sweep's very FIRST real probe is call #6, already past
    # `rate_limit_after=5`.
    probe = FakeProbe(responses, rate_limit_after=5, default=ProbeResult.ABSENT)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=50,
        ),
    )

    assert result.event["sweep_ran"] is True
    # sweep_candidates("1.0.0", ...) yields "1.0.0" first (skipped,
    # already known, charges nothing) then "1.0.1" -- the first REAL
    # sweep probe, which is already rate-limited. Nothing past it (of
    # the remaining ~10 sweep candidates within budget) is probed.
    assert result.event["sweep_probed"] == ["1.0.1"]
    assert result.event["sweep_rate_limited"] is True
    assert result.event["rate_limited"] is True


def test_sweep_truncation_is_a_visible_signal_r1() -> None:
    """R1 fix: the sweep's truncation used to raise no ceiling signal at
    all -- a sweep covering 4 of 273 candidates was indistinguishable
    from one covering all of them. `sweep_truncated` must be set, and a
    truncated sweep must never let the tick land on `no_new_versions`."""
    anchor = "1.0.0"
    responses = _controls_ok(anchor)
    for p in (1, 2, 3):
        responses[f"1.0.{p}"] = ProbeResult.ABSENT
    probe = FakeProbe(responses, default=ProbeResult.ABSENT)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=2,  # deliberately tiny -- forces truncation
        ),
    )

    assert result.event["sweep_ran"] is True
    assert result.event["sweep_truncated"] is True
    assert result.outcome == OUTCOME_CEILING_REACHED
    assert result.outcome != OUTCOME_NO_NEW_VERSIONS


def test_sweep_budget_is_charged_only_for_probes_actually_issued_r1() -> None:
    """R1 fix (the second bug in the same paragraph): the budget counter
    used to increment BEFORE the `continue` that skips an already-known
    candidate -- so effective sweep coverage silently shrank as
    `known_versions` grew. The anchor itself ("1.0.0") is always a sweep
    candidate and always already `known`; it must not consume any of the
    2-probe budget below."""
    anchor = "1.0.0"
    responses = _controls_ok(anchor)
    for p in (1, 2, 3):
        responses[f"1.0.{p}"] = ProbeResult.ABSENT
    probe = FakeProbe(responses, default=ProbeResult.ABSENT)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=3,
            minor_lookahead=0,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=2,
        ),
    )

    # sweep_candidates("1.0.0", minor_ceiling=0, patch_ceiling=12, ...)
    # yields "1.0.0" first (skipped, already `known`, charges nothing),
    # then "1.0.1", "1.0.2" (2 real probes -- budget exhausted), then
    # truncates before "1.0.3".
    assert result.event["sweep_probed"] == ["1.0.1", "1.0.2"]
    assert result.event["sweep_probe_count"] == 2
    assert result.event["total_probe_count"] == result.event["probe_count"] + 2


def test_a_sweep_found_missed_version_is_still_a_new_version_detected() -> None:
    """The sweep's whole reason to exist: a version more than
    `--minor-lookahead` minors ahead, whose own `.0` was never published
    (so the walk's minor axis can't reach it), is still found."""
    anchor = "1.0.0"
    responses = _controls_ok(anchor)
    responses["1.5.0"] = ProbeResult.EXISTS
    probe = FakeProbe(responses, default=ProbeResult.ABSENT)

    result = check_once(
        probe=probe,
        **_base_kwargs(
            max_probes_per_tick=10,
            patch_lookahead=0,
            minor_lookahead=2,
            major_lookahead=0,
            sweep_every_n_ticks=1,
            max_probes_per_sweep=50,
        ),
    )
    # walk's own minor-axis lookahead (2 -> "1.1.0", "1.2.0") cannot reach
    # "1.5.0"; the sweep's ceiling (minor_lookahead * 4 = 8, i.e. up to
    # "1.8.0") does -- this is the sweep's whole reason to exist.

    assert result.outcome == OUTCOME_NEW_VERSION_DETECTED
    assert "1.5.0" in result.new_state.known_versions
    assert result.event["sweep_found_missed_version"] == ["1.5.0"]


# -- R2: the state-file lock (single-tick, shared by watch.main()) ----------


def test_open_state_file_locked_raises_when_already_locked(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    # RQ-1 (2026-09-23 re-review): the lock lives on the SIDECAR path
    # (`<state_file>.lock`), never on the data file itself -- locking
    # `path` directly here must NOT be what blocks a second
    # `open_state_file_locked(path)`; only a lock on `_lock_path_for(path)`
    # does.
    lock_path = _lock_path_for(path)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(StateFileLocked):
            open_state_file_locked(path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_open_state_file_locked_does_not_conflict_with_a_lock_on_the_data_file_itself(
    tmp_path: Path,
) -> None:
    """The inverse of the test above, and the exact property RQ-1 fixes:
    the data file's own inode is no longer what the lock guards, so a
    (pointless, but conceivable) external lock taken on `path` itself
    must NOT block `open_state_file_locked(path)`."""
    path = tmp_path / "state.json"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        lock_fd = open_state_file_locked(path)  # must not raise
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_main_reports_skipped_locked_and_never_touches_credentials_when_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IDP_CLIENT_ID", raising=False)
    monkeypatch.delenv("IDP_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("IDP_REGION", raising=False)
    monkeypatch.setattr("idp_regression.orchestration.check_versions.load_dotenv", lambda: None)
    state_path = tmp_path / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # RQ-1: lock the SIDECAR path -- that's what `open_state_file_locked`
    # actually locks now, not `state_path` itself.
    fd = os.open(str(_lock_path_for(state_path)), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        exit_code = main(
            [
                "--org", "org1",
                "--action", "action1",
                "--dataset", "ds1",
                "--state-file", str(state_path),
                "--max-probes-per-tick", "5",
                "--max-probes-per-sweep", "5",
                "--patch-lookahead", "3",
                "--minor-lookahead", "0",
                "--major-lookahead", "0",
                "--sweep-every-n-ticks", "0",
                "--max-indeterminate-ticks", "3",
                "--known-version", "1.0.0",
            ]
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # skipped_locked is returned BEFORE any credential is read -- proven by
    # not raising despite every IDP_* var being unset above.
    assert exit_code == 0


# -- R4: a missing credential is a controlled failure, not a raw traceback --


def test_main_missing_credential_env_var_is_a_controlled_failure_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IDP_CLIENT_ID", raising=False)
    monkeypatch.delenv("IDP_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("IDP_REGION", raising=False)
    # Isolate this test from whatever real `.env` the checkout has.
    monkeypatch.setattr("idp_regression.orchestration.check_versions.load_dotenv", lambda: None)
    state_path = tmp_path / "state.json"

    exit_code = main(
        [
            "--org", "org1",
            "--action", "action1",
            "--dataset", "ds1",
            "--state-file", str(state_path),
            "--max-probes-per-tick", "5",
            "--max-probes-per-sweep", "5",
            "--patch-lookahead", "3",
            "--minor-lookahead", "0",
            "--major-lookahead", "0",
            "--sweep-every-n-ticks", "0",
            "--max-indeterminate-ticks", "3",
            "--known-version", "1.0.0",
        ]
    )

    assert exit_code == 1  # controlled -- no raised KeyError escaped main()


def test_main_a_load_dotenv_failure_is_a_controlled_exit_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> None:
        raise OSError("simulated unreadable .env")

    monkeypatch.setattr("idp_regression.orchestration.check_versions.load_dotenv", _raise)
    state_path = tmp_path / "state.json"

    exit_code = main(
        [
            "--org", "org1",
            "--action", "action1",
            "--dataset", "ds1",
            "--state-file", str(state_path),
            "--max-probes-per-tick", "5",
            "--max-probes-per-sweep", "5",
            "--patch-lookahead", "3",
            "--minor-lookahead", "0",
            "--major-lookahead", "0",
            "--sweep-every-n-ticks", "0",
            "--max-indeterminate-ticks", "3",
        ]
    )

    assert exit_code == 1
