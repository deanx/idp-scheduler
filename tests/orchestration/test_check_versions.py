"""ADR-0006 §Decision A' Phase 1 — the check-versions tick (docs/qa/NFR-02.md
Pass D). check_once() is the pure(ish) facade under test here; state-file
I/O and the CLI wiring get their own thin tests further down."""

from __future__ import annotations

import json
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
    TickState,
    _reject_state_file_inside_repo,
    check_once,
    load_state,
    save_state_atomic,
)


class FakeProbe:
    """A scripted IDPVersionProbe test double: ``responses`` maps a
    version string to a ProbeResult; any version not in the map is
    ABSENT by default (a real vendor would 404 an un-probed version)."""

    def __init__(self, responses: dict[str, ProbeResult], rate_limit_after: int | None = None):
        self.responses = responses
        self.calls: list[str] = []
        self.rate_limit_after = rate_limit_after
        self.last_status_code: int | None = None

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        self.calls.append(version)
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            self.last_status_code = 429
            return ProbeResult.UNKNOWN
        self.last_status_code = 200
        return self.responses.get(version, ProbeResult.ABSENT)


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


def test_an_unknown_candidate_never_becomes_absent_and_is_recorded() -> None:
    class UnknownOnceProbe:
        last_status_code = None

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
