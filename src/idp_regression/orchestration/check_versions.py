"""``idp-regression check-versions`` — ADR-0006 §Decision A' Phase 1
(detect-and-notify). See the ADR for the full design; this module
implements the tick.

Deliberately NOT named ``watch-once`` (reserved for Phase 2 unattended
run-triggering). Spends **zero** extraction quota. **``--auto-run`` does
not exist** — this command only detects and notifies.

Scope note (reported, not silently absorbed): ADR-0006 §A'.6 revives
``list_certified_versions`` on ``PlatformAdapter`` (tier 1). That widening
touches ``platform/types.py`` and the concrete evaluation-platform adapter
module (NFR N24-confined, named there not here) — files with an existing,
heavily mutation-tested suite this task did not size in.
Phase 1 here anchors from **tier 2 only** (the local, non-authoritative
cache file) plus the ADR-sanctioned ``--known-version`` bootstrap — both
are named, explicit paths in §A'.6 ("Two bootstraps are acceptable ...
run one ``run_eval`` ... **or** pass ``--known-version <v>`` once"). Wiring
tier 1 in is a follow-up, not a silent omission — see the handoff report.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.adapter.version_probe import (
    NEGATIVE_CONTROL_VERSION,
    IDPVersionProbe,
    MuleSoftVersionProbe,
    ProbeResult,
    parse_semver,
    sweep_candidates,
    walk_candidates,
)
from idp_regression.orchestration.cli import _is_valid_action_id, configure_logging
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.log_sanitize import frame_location

#: NOT `logging.getLogger(__name__)` -- the identical bug fixed in
#: `cli.py` 2026-09-23 (`318c9ff`): under `python -m
#: idp_regression.orchestration.check_versions`, `runpy` imports this
#: module as `__main__`, so `__name__` at module level would be
#: `"__main__"`, orphaning this logger from the `idp_regression` package
#: hierarchy `configure_logging()` attaches its handler to -- every line
#: this module logs (including every `check_tick` event) would fall
#: through to `logging.lastResort`, i.e. nowhere, only when run the
#: documented way. Live-reproduced 2026-09-23: output was
#: `INFO:__main__:...` instead of the configured
#: `2026-09-23 10:58:00 INFO idp_regression...: ...` format. Pinned by
#: `tests/orchestration/test_logging_config.py`
#: `test_check_versions_logger_name_is_stable_under_python_dash_m_invocation`.
logger = logging.getLogger("idp_regression.orchestration.check_versions")

#: Bumped whenever the state-file schema changes shape. An unrecognised
#: version is a fail-closed halt, never a silent reset (a silent reset
#: would re-walk from nothing and could re-notify every known version).
SCHEMA_VERSION = 1

#: RQ (2026-09-23 re-review, "cheap ones"): `pending_unknowns` had no cap
#: or ageing -- a persistently-ambiguous endpoint could park the walk
#: behind re-probing an ever-growing list forever, emitting
#: `ceiling_reached` every tick without ever making progress. This is a
#: hard cap, not true LRU ageing (the state's `to_json()` always
#: `sorted()`s the list for a stable diff, which already discards
#: insertion order -- true ageing would need a separate
#: candidate->first-seen-tick map, a schema change out of scope for this
#: pass). Capping at a sorted-alphabetical prefix is an arbitrary but
#: deterministic and bounded policy: it stops unbounded growth and is
#: visible in the event (`pending_unknowns_capped`), which is what this
#: pass commits to.
MAX_PENDING_UNKNOWNS = 500

#: The full outcome vocabulary (ADR-0006 §A'.9).
OUTCOME_NO_NEW_VERSIONS = "no_new_versions"
OUTCOME_NEW_VERSION_DETECTED = "new_version_detected"
OUTCOME_CEILING_REACHED = "ceiling_reached"
OUTCOME_INDETERMINATE = "indeterminate"
OUTCOME_DETECTOR_DEGRADED = "detector_degraded"
OUTCOME_DISCRIMINATOR_INVALID = "discriminator_invalid"
OUTCOME_ANCHOR_VANISHED = "anchor_vanished"
OUTCOME_REFUSED_UNINITIALISED = "refused_uninitialised"
OUTCOME_REFUSED_UNPARSEABLE_VERSION_SCHEME = "refused_unparseable_version_scheme"
OUTCOME_SKIPPED_LOCKED = "skipped_locked"

#: Exit code 0 outcomes (a scheduler must NOT treat these as "look at me").
_ZERO_EXIT_OUTCOMES = frozenset(
    {OUTCOME_NO_NEW_VERSIONS, OUTCOME_SKIPPED_LOCKED, OUTCOME_INDETERMINATE}
)


class CheckVersionsRefused(Exception):
    """A fail-closed refusal decided before (or instead of) probing —
    caught once at the CLI boundary to set the right outcome/exit code,
    never a raw escape."""

    def __init__(self, outcome: str, message: str) -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass
class TickState:
    """The tier-2, explicitly non-authoritative local cache (§A'.6).
    Losing this file costs one redundant walk and at most one duplicate
    notification — never a missed detection or a wrong exit code (D16)."""

    schema_version: int = SCHEMA_VERSION
    known_versions: list[str] = field(default_factory=list)
    pending_unknowns: list[str] = field(default_factory=list)
    tick_count: int = 0
    ticks_since_last_detection: int = 0
    anchor_changed_at_epoch: float | None = None
    consecutive_indeterminate_ticks: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "known_versions": sorted(self.known_versions),
            "pending_unknowns": sorted(self.pending_unknowns),
            "tick_count": self.tick_count,
            "ticks_since_last_detection": self.ticks_since_last_detection,
            "anchor_changed_at_epoch": self.anchor_changed_at_epoch,
            "consecutive_indeterminate_ticks": self.consecutive_indeterminate_ticks,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> TickState:
        schema_version = data.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise CheckVersionsRefused(
                OUTCOME_REFUSED_UNINITIALISED,
                f"state file schema_version {schema_version!r} is not the "
                f"recognised {SCHEMA_VERSION!r} — refusing rather than "
                "silently resetting (a silent reset re-walks from nothing "
                "and can re-notify every known version)",
            )
        return TickState(
            schema_version=schema_version,
            known_versions=list(data.get("known_versions", [])),
            pending_unknowns=list(data.get("pending_unknowns", [])),
            tick_count=int(data.get("tick_count", 0)),
            ticks_since_last_detection=int(data.get("ticks_since_last_detection", 0)),
            anchor_changed_at_epoch=data.get("anchor_changed_at_epoch"),
            consecutive_indeterminate_ticks=int(data.get("consecutive_indeterminate_ticks", 0)),
        )


@dataclass
class WalkOutcome:
    final_anchor: str
    probed: list[str]
    hits: list[str]
    unknowns: list[str]
    ceiling_reached: bool
    rate_limited: bool
    probes_used: int


def _probe_with_rate_limit_check(
    probe: IDPVersionProbe, org_id: str, action_id: str, candidate: str
) -> tuple[ProbeResult, bool]:
    """Probes one candidate and reports whether THIS response was itself
    a 429. ADR-0006 §A'.4 is unqualified: "429 -> UNKNOWN, abandon the
    remaining probes for this tick and back off" — a 429 always
    classifies to `ProbeResult.UNKNOWN` (`classify_probe_response`), but
    `ProbeResult` alone cannot distinguish "429, abandon everything
    downstream" from any other UNKNOWN. Shared by all three probe loops
    (`_resolve_unknowns`, the walk's own grid, and the sweep) so the rule
    is enforced once, not three times with three chances to miss an edge
    — RQ-3 (2026-09-23 re-review): C2's fix only wired this into the
    walk -> sweep edge; `_resolve_unknowns` and the sweep loop itself
    still kept probing past a 429 before this fix."""
    result = probe.probe(org_id, action_id, candidate)
    # F-3 (2026-09-23, `/test` gate): `last_status_code` is now part of
    # `IDPVersionProbe` itself, not merely `getattr(..., None)`-reached --
    # a Protocol-conformant probe lacking it is a mypy error at the call
    # site, not a silent "never rate-limited" default.
    rate_limited = probe.last_status_code == 429
    return result, rate_limited


def _resolve_unknowns(
    probe: IDPVersionProbe,
    org_id: str,
    action_id: str,
    pending: list[str],
    *,
    probes_remaining: int,
) -> tuple[list[str], list[str], list[str], int, bool]:
    """Re-probe every pending UNKNOWN before the walk advances past it
    (§A'.4) — an ambiguous answer is a *pending* answer, not a resolved
    one. Returns (probed, hits, still_unknown, probes_used, rate_limited).
    A 429 abandons the REST of this pass's pending candidates (they are
    appended to `still_unknown` unprobed, exactly like a budget-exhausted
    candidate) and is reported back so the caller can skip the walk's own
    grid entirely this tick (§A'.4's "abandon the remaining probes for
    this tick" — RQ-3)."""
    probed: list[str] = []
    hits: list[str] = []
    still_unknown: list[str] = []
    probes_used = 0
    rate_limited = False
    for candidate in pending:
        if rate_limited or probes_used >= probes_remaining:
            still_unknown.append(candidate)
            continue
        probes_used += 1
        probed.append(candidate)
        result, hit_rate_limited = _probe_with_rate_limit_check(
            probe, org_id, action_id, candidate
        )
        if result is ProbeResult.EXISTS:
            hits.append(candidate)
        elif result is ProbeResult.UNKNOWN:
            still_unknown.append(candidate)
        # ABSENT: resolved, dropped — it is confirmed not to exist.
        if hit_rate_limited:
            rate_limited = True
    return probed, hits, still_unknown, probes_used, rate_limited


def _run_walk(
    probe: IDPVersionProbe,
    org_id: str,
    action_id: str,
    anchor: str,
    pending_unknowns: list[str],
    *,
    max_probes: int,
    patch_lookahead: int,
    minor_lookahead: int,
    major_lookahead: int,
) -> WalkOutcome:
    """One tick's bounded, re-anchoring walk (§A'.2)."""
    probed: list[str] = []
    hits: list[str] = []
    unknowns: list[str] = []
    probes_used = 0
    current_anchor = anchor
    ceiling_reached = False
    rate_limited = False

    (
        resolved_probed,
        resolved_hits,
        still_unknown,
        used,
        resolve_rate_limited,
    ) = _resolve_unknowns(probe, org_id, action_id, pending_unknowns, probes_remaining=max_probes)
    probed.extend(resolved_probed)
    hits.extend(resolved_hits)
    unknowns.extend(still_unknown)
    probes_used += used
    rate_limited = resolve_rate_limited
    for candidate in resolved_hits:
        parsed_candidate = parse_semver(candidate)
        parsed_anchor = parse_semver(current_anchor)
        if parsed_candidate is not None and (
            parsed_anchor is None or parsed_candidate > parsed_anchor
        ):
            current_anchor = candidate

    # RQ-3 (2026-09-23 re-review): a 429 while resolving pending unknowns
    # must abandon the REST of this tick's probes, including the walk's
    # own grid -- not just the remaining pending items. Before this fix
    # only the grid-loop -> sweep edge honoured "abandon the rest of the
    # tick"; a 429 on the FIRST pending unknown still let the walk run
    # its full grid.
    while not rate_limited:
        if probes_used >= max_probes:
            # Budget was already exhausted by the PREVIOUS pass's hit
            # (a re-anchor consumed the last probe) -- this pass, which
            # would have continued the chain, never gets to run at all.
            # That is truncation "while still hitting" (§A'.3 mechanism
            # 2): the outcome must be ceiling_reached, never
            # no_new_versions.
            ceiling_reached = True
            break
        candidates = list(
            walk_candidates(
                current_anchor,
                patch_lookahead=patch_lookahead,
                minor_lookahead=minor_lookahead,
                major_lookahead=major_lookahead,
            )
        )
        re_anchored = False
        exhausted_mid_candidates = False
        for candidate in candidates:
            if probes_used >= max_probes:
                exhausted_mid_candidates = True
                break
            probes_used += 1
            probed.append(candidate)
            result, hit_rate_limited = _probe_with_rate_limit_check(
                probe, org_id, action_id, candidate
            )
            # Known-open item, closed 2026-09-24 (Wave-1 Lane D): this
            # used to check `hit_rate_limited` ONLY inside the UNKNOWN
            # branch, while `_resolve_unknowns` and the sweep loop below
            # both check it unconditionally after every probe --
            # contradicting `_probe_with_rate_limit_check`'s own "enforced
            # once, not three times" docstring. In production this branch
            # never diverges (`classify_probe_response` always maps 429 ->
            # UNKNOWN, so an EXISTS/ABSENT result and a 429 status code
            # cannot co-occur through `MuleSoftVersionProbe`), but
            # `IDPVersionProbe` is a Protocol -- nothing at the type level
            # ties `last_status_code` to the returned `ProbeResult`, so a
            # differently-behaved probe (a future implementation, or a
            # test double) could decouple them, and this loop would then
            # silently drop the rate-limit signal exactly where the other
            # two loops catch it. Checked unconditionally now, matching
            # both siblings.
            if result is ProbeResult.EXISTS:
                hits.append(candidate)
                current_anchor = candidate
                re_anchored = True
            elif result is ProbeResult.UNKNOWN:
                unknowns.append(candidate)
            if hit_rate_limited:
                rate_limited = True
            if rate_limited:
                break
            if re_anchored:
                break  # re-anchor: restart the walk from the new anchor
        if rate_limited:
            break
        if exhausted_mid_candidates:
            # Truncated partway through this pass's own lookahead grid —
            # "unknown beyond here", the same distinct outcome.
            ceiling_reached = True
            break
        if not re_anchored:
            # This pass completed its FULL lookahead grid within budget
            # and found no further hit -- a genuine, not-truncated,
            # natural end to the walk.
            break

    return WalkOutcome(
        final_anchor=current_anchor,
        probed=probed,
        hits=hits,
        unknowns=unknowns,
        ceiling_reached=ceiling_reached,
        rate_limited=rate_limited,
        probes_used=probes_used,
    )


@dataclass
class TickResult:
    outcome: str
    new_state: TickState
    event: dict[str, Any]
    run_eval_commands: list[str]


def check_once(
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
    clock: Any = time.time,
) -> TickResult:
    """The pure(ish) tick facade — everything except state-file I/O and
    the OS lock, so it is unit-testable with a fake ``IDPVersionProbe``
    and no filesystem. Mirrors ``run_eval``'s Facade role (ADR-0006
    §A'.11 design-patterns table)."""
    tick_count = state.tick_count + 1
    known = set(state.known_versions)
    if known_version is not None:
        known.add(known_version)
    if not known:
        raise CheckVersionsRefused(
            OUTCOME_REFUSED_UNINITIALISED,
            "no anchor available for this (org, action, dataset) — bootstrap "
            "with one `run_eval` invocation (records action_version via "
            "INV-04) or pass --known-version once",
        )
    anchor = max(known, key=lambda v: parse_semver(v) or (-1, -1, -1))
    if parse_semver(anchor) is None:
        raise CheckVersionsRefused(
            OUTCOME_REFUSED_UNPARSEABLE_VERSION_SCHEME,
            f"anchor {anchor!r} does not match strict semver ^\\d+\\.\\d+\\.\\d+$ "
            "— refusing rather than walking a grid that cannot contain the answer",
        )

    # -- Two control probes (§A'.7) — the runtime canary for the
    # discriminator itself. Both run before the walk, every tick.
    positive_control = probe.probe(org_id, action_id, anchor)
    if positive_control is not ProbeResult.EXISTS:
        event = _base_event(org_id, action_id, dataset_name, anchor, tick_count)
        event["outcome"] = OUTCOME_ANCHOR_VANISHED
        event["positive_control"] = str(positive_control)
        event["negative_control"] = None
        return TickResult(OUTCOME_ANCHOR_VANISHED, state, event, [])

    negative_control = probe.probe(org_id, action_id, NEGATIVE_CONTROL_VERSION)
    if negative_control is not ProbeResult.ABSENT:
        event = _base_event(org_id, action_id, dataset_name, anchor, tick_count)
        event["outcome"] = OUTCOME_DISCRIMINATOR_INVALID
        event["positive_control"] = str(positive_control)
        event["negative_control"] = str(negative_control)
        return TickResult(OUTCOME_DISCRIMINATOR_INVALID, state, event, [])

    walk = _run_walk(
        probe,
        org_id,
        action_id,
        anchor,
        state.pending_unknowns,
        max_probes=max_probes_per_tick,
        patch_lookahead=patch_lookahead,
        minor_lookahead=minor_lookahead,
        major_lookahead=major_lookahead,
    )

    # -- Periodic wide sweep (§A'.2) -- the systematic recovery from the
    # walk's named blind spot. C1 fix (2026-09-23, reviewer PIN): every
    # UNKNOWN this loop sees MUST fold into the tick's unknowns, exactly
    # like the walk's own UNKNOWNs -- a sweep that came back ambiguous is
    # not evidence of "no new version", it is evidence of nothing, and
    # reading it as the former is the exact fail-open D6 exists to
    # prevent (`classify_probe_response`'s UNKNOWN default branch is
    # pointless if a caller two frames up silently drops the value).
    # C2 fix: a 429 during the walk means "abandon the rest of this
    # tick's probes" (§A'.4) -- the sweep is more of this tick's probes,
    # so it must not run when the walk already rate-limited.
    # R1 fix: the sweep's own candidates/probes/truncation are now
    # tracked separately (`sweep_probed`, `sweep_unknowns`,
    # `sweep_truncated`) so a partial sweep is never indistinguishable
    # from a complete one, and the budget is charged only for probes
    # actually issued -- charging it for a `continue`d already-known
    # candidate silently shrank effective coverage as `known_versions`
    # grew.
    # RQ-3 (2026-09-23 re-review): a 429 mid-sweep must abandon the rest
    # of the SWEEP's own remaining probes too, via the same
    # `_probe_with_rate_limit_check` helper the walk and
    # `_resolve_unknowns` now use -- before this fix the sweep loop never
    # inspected `last_status_code` at all and would keep issuing up to
    # `max_probes_per_sweep - 1` more probes at an endpoint that had just
    # rate-limited this tick.
    sweep_missed: list[str] = []
    sweep_probed: list[str] = []
    sweep_unknowns: list[str] = []
    sweep_truncated = False
    sweep_rate_limited = False
    ran_sweep = (
        sweep_every_n_ticks > 0
        and tick_count % sweep_every_n_ticks == 0
        and not walk.rate_limited
    )
    if ran_sweep:
        sweep_probes_used = 0
        for candidate in sweep_candidates(
            walk.final_anchor,
            sweep_minor_ceiling=minor_lookahead * 4,
            sweep_major_ceiling=major_lookahead * 4,
            sweep_patch_ceiling=patch_lookahead * 4,
        ):
            if candidate in walk.hits or candidate in known:
                continue
            if sweep_probes_used >= max_probes_per_sweep:
                sweep_truncated = True
                break
            sweep_probes_used += 1
            sweep_probed.append(candidate)
            result, hit_rate_limited = _probe_with_rate_limit_check(
                probe, org_id, action_id, candidate
            )
            if result is ProbeResult.EXISTS:
                sweep_missed.append(candidate)
            elif result is ProbeResult.UNKNOWN:
                sweep_unknowns.append(candidate)
            # ABSENT: resolved, dropped -- confirmed not to exist.
            if hit_rate_limited:
                sweep_rate_limited = True
                break

    # Tick-level rate_limited signal (RQ-3): true if EITHER the walk (and
    # its unknowns-resolution pass) or the sweep hit a 429 this tick --
    # the single value the event records, so a human reading the tick
    # log sees one honest answer regardless of which of the three loops
    # tripped it.
    tick_rate_limited = walk.rate_limited or sweep_rate_limited

    all_unknowns = sorted(set(walk.unknowns) | set(sweep_unknowns))
    pending_unknowns_capped = len(all_unknowns) > MAX_PENDING_UNKNOWNS
    if pending_unknowns_capped:
        logger.warning(
            "check_tick: pending_unknowns exceeds cap (%d > %d) -- capping "
            "to the first %d alphabetically; the rest are re-probed via "
            "the walk/sweep as they naturally recur",
            len(all_unknowns),
            MAX_PENDING_UNKNOWNS,
            MAX_PENDING_UNKNOWNS,
        )
        all_unknowns = all_unknowns[:MAX_PENDING_UNKNOWNS]

    new_known = known | set(walk.hits) | set(sweep_missed)
    new_versions = sorted(set(walk.hits) | set(sweep_missed))

    anchor_changed_at_epoch = state.anchor_changed_at_epoch
    ticks_since_last_detection = state.ticks_since_last_detection + 1
    if new_versions:
        anchor_changed_at_epoch = clock()
        ticks_since_last_detection = 0

    if new_versions:
        outcome = OUTCOME_NEW_VERSION_DETECTED
    elif walk.ceiling_reached or sweep_truncated:
        outcome = OUTCOME_CEILING_REACHED
    elif all_unknowns:
        outcome = OUTCOME_INDETERMINATE
    else:
        outcome = OUTCOME_NO_NEW_VERSIONS

    consecutive_indeterminate_ticks = state.consecutive_indeterminate_ticks
    if outcome == OUTCOME_INDETERMINATE:
        consecutive_indeterminate_ticks += 1
        if consecutive_indeterminate_ticks > max_indeterminate_ticks:
            outcome = OUTCOME_DETECTOR_DEGRADED
    else:
        consecutive_indeterminate_ticks = 0

    days_since_anchor_changed = (
        (clock() - anchor_changed_at_epoch) / 86_400.0
        if anchor_changed_at_epoch is not None
        else None
    )

    new_state = TickState(
        schema_version=SCHEMA_VERSION,
        known_versions=sorted(new_known),
        pending_unknowns=all_unknowns,
        tick_count=tick_count,
        ticks_since_last_detection=ticks_since_last_detection,
        anchor_changed_at_epoch=anchor_changed_at_epoch,
        consecutive_indeterminate_ticks=consecutive_indeterminate_ticks,
    )

    event = _base_event(org_id, action_id, dataset_name, walk.final_anchor, tick_count)
    event.update(
        {
            "outcome": outcome,
            "probed": walk.probed,
            "hits": walk.hits,
            "absent_count": len(walk.probed) - len(walk.hits) - len(walk.unknowns),
            "unknowns": walk.unknowns,
            "ceiling_reached": walk.ceiling_reached,
            "rate_limited": tick_rate_limited,
            "sweep_rate_limited": sweep_rate_limited,
            "pending_unknowns_capped": pending_unknowns_capped,
            "probe_count": walk.probes_used,
            "total_probe_count": walk.probes_used + len(sweep_probed),
            "ticks_since_last_detection": ticks_since_last_detection,
            "days_since_anchor_changed": days_since_anchor_changed,
            "positive_control": str(positive_control),
            "negative_control": str(negative_control),
            "sweep_ran": ran_sweep,
            "sweep_probed": sweep_probed,
            "sweep_probe_count": len(sweep_probed),
            "sweep_unknowns": sweep_unknowns,
            "sweep_truncated": sweep_truncated,
            "sweep_found_missed_version": sweep_missed or None,
        }
    )

    # The command must be literally paste-able. `idp-regression` is NOT an
    # installed console script -- `pyproject.toml` declares no
    # `[project.scripts]`, and `.venv/bin/` contains no `idp-*` entry point --
    # so emitting it would hand the reader a "command not found". The module
    # invocation below is the one `CLAUDE.md ## Commands` documents and the
    # one both live runs on 2026-09-23 actually used. Pinned by
    # `test_run_eval_command_is_literally_runnable`; if a console script is
    # ever added, change this string and that test together.
    run_eval_commands = [
        f".venv/bin/python -m idp_regression.orchestration.cli "
        f"--org {org_id} --action {action_id} --version {version} "
        f"--dataset {dataset_name} --run auto-detected-{version}"
        for version in new_versions
    ]

    return TickResult(outcome, new_state, event, run_eval_commands)


def _base_event(
    org_id: str, action_id: str, dataset_name: str, anchor: str, tick_count: int
) -> dict[str, Any]:
    return {
        "event": "check_tick",
        "org_id": org_id,
        "action_id": action_id,
        "dataset_name": dataset_name,
        "anchor": anchor,
        "tick_count": tick_count,
    }


# -- State-file I/O (tier 2, §A'.6) ------------------------------------------


class StateFileLocked(Exception):
    """Another process already holds the state file's advisory lock
    (R2, 2026-09-23) — the caller must treat this as `skipped_locked`,
    never retry-block, never proceed unlocked."""

    def __init__(self, state_file: str) -> None:
        super().__init__(f"state file {state_file} is locked by another instance")


def _lock_path_for(state_file: Path) -> Path:
    """The sidecar lock path for `state_file` -- `<state_file>.lock`,
    never the data file itself and never passed to `os.replace()`
    (RQ-1, 2026-09-23 re-review)."""
    return state_file.with_name(state_file.name + ".lock")


def open_state_file_locked(state_file: Path) -> int:
    """Opens (creating if absent) and takes a non-blocking exclusive
    `flock` on a SIDECAR path (`<state_file>.lock`) -- deliberately NOT
    `state_file` itself. Raises `StateFileLocked` (fd already closed) if
    another process holds it.

    RQ-1 (2026-09-23 re-review, PIN): `flock` locks the INODE a path
    resolves to at open() time. `save_state_atomic()` does
    `os.replace(tmp, path)`, which retargets `path` to a BRAND-NEW inode
    -- the fd this function's caller is holding stays locked on the OLD,
    now-unreferenced inode. A lock taken directly on `state_file` is
    therefore silently orphaned by the very first `save_state_atomic()`
    call: `watch.py`'s `run_watch_loop` saves every tick, so from tick 2
    onward the watcher would run completely unlocked for the rest of a
    potentially days-long loop, while both the code comment and the
    operator-facing "refusing to start" message kept asserting a
    guarantee that no longer held. The sidecar path is never the target
    of an `os.replace()`, so the SAME fd (and the SAME inode) stays
    locked for as long as it is held open, regardless of how many times
    the data file underneath it is atomically replaced.

    Shared by `check_versions.main()` (one tick, lock held for that one
    tick) and `watch.main()` (lock held for the WHOLE foreground loop's
    lifetime, R2 — the watcher used to only `open()` the file and never
    lock it at all, so two watchers, or a watcher plus a scheduled
    `check-versions`, could interleave writes and lose `known_versions`,
    which with `--auto-run` is a re-detection and a duplicate real-quota
    run). The caller owns the fd and must eventually
    `fcntl.flock(fd, fcntl.LOCK_UN)` then `os.close(fd)`. A process
    killed while holding the lock releases it for free -- `flock` is
    kernel-released on process death regardless of which path it was
    taken on, so a stale lock from a killed watcher never wedges the
    next run."""
    lock_path = _lock_path_for(state_file)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise StateFileLocked(str(state_file)) from None
    return fd


def load_state_from_file(state_file: Path) -> TickState:
    """Opens (creating if absent) and reads `state_file`'s current
    content, independent of the lock. RQ-1 split locking (the sidecar,
    `open_state_file_locked`) from reading the data file itself -- the
    lock no longer needs to be held on the same fd/inode the data is
    read from, since `save_state_atomic()` replaces that inode on every
    save regardless. Locking remains entirely the caller's
    responsibility; this only reads."""
    fd = os.open(str(state_file), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as fh:
            return load_state(fh)
    finally:
        os.close(fd)


def _reject_state_file_inside_repo(state_file: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    resolved = state_file.resolve()
    if resolved == repo_root or repo_root in resolved.parents:
        raise CheckVersionsRefused(
            OUTCOME_REFUSED_UNINITIALISED,
            f"--state-file must be OUTSIDE the repository working tree "
            f"(got {resolved}, inside {repo_root}) — a git-tracked store is "
            "not one whose correctness quota spend and gate provenance "
            "should depend on",
        )


def load_state(fh: Any) -> TickState:
    raw = fh.read()
    if not raw.strip():
        return TickState()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckVersionsRefused(
            OUTCOME_REFUSED_UNINITIALISED, "state file is not valid JSON"
        ) from exc
    if not isinstance(data, dict):
        raise CheckVersionsRefused(
            OUTCOME_REFUSED_UNINITIALISED, "state file does not contain a JSON object"
        )
    return TickState.from_json(data)


def save_state_atomic(path: Path, state: TickState) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state.to_json(), fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


#: Shared by both boundless-loop entry points on top of `check_once`
#: (this module's own `main()` and `watch.py`'s `_run()`) -- deliberately
#: NOT duplicated in `watch.py`, imported from here instead, so the two
#: never drift. **The invariant (defect repro, 2026-09-24): no
#: run-identity parameter may reach the network blank or whitespace-
#: only.** `cli.py`'s `run_eval` entry point already enforces exactly
#: this shape for `--org`/`--action`/`--dataset` (ADR-0004 A8/A9: reject
#: after `.strip()`, fail closed, before any network call) -- these two
#: entry points never got it, so a shell that lost its env vars (the
#: live repro) sent an empty-string org/action/dataset straight into the
#: probe URL, which then came back UNKNOWN/ABSENT and was reported as
#: "ANCHOR CHECK UNREACHABLE" -- a diagnosis about the network for what
#: was actually "you passed nothing". `--action` additionally gets
#: `cli.py`'s UUID check (imported, not reinvented) -- watch/check-
#: versions address the exact same MuleSoft action id `run_eval` does,
#: and both print a ready-to-paste `run_eval --action <id> ...` command
#: on detection, which would otherwise embed an already-invalid id that
#: only surfaces as a failure once a human pastes it. INV-02: the
#: message names only the field, never the value.
def _validate_run_identity(org_id: str, action_id: str, dataset_name: str) -> str | None:
    """Returns `None` if `org_id`/`action_id`/`dataset_name` are all
    usable, else a one-line message naming the first offending field."""
    if not (org_id or "").strip():
        return "--org must not be blank"
    if not (action_id or "").strip():
        return "--action must not be blank"
    if not _is_valid_action_id(action_id.strip()):
        return "--action is not a valid UUID"
    if not (dataset_name or "").strip():
        return "--dataset must not be blank"
    return None


def _emit(event: dict[str, Any]) -> None:
    # Structured, one JSON object per line (ADR-0006 §A'.9). Every value
    # already sanitized at construction (org/action ids are CLI-supplied,
    # not golden/actual/credential content — INV-02 has nothing to strip
    # here beyond normal control-character hygiene).
    logger.info(sanitize_for_log(json.dumps(event, default=str)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="idp-regression check-versions")
    parser.add_argument("--org", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--max-probes-per-tick", required=True, type=int)
    parser.add_argument("--max-probes-per-sweep", required=True, type=int)
    parser.add_argument("--patch-lookahead", required=True, type=int)
    parser.add_argument("--minor-lookahead", required=True, type=int)
    parser.add_argument("--major-lookahead", required=True, type=int)
    parser.add_argument("--sweep-every-n-ticks", required=True, type=int)
    parser.add_argument("--max-indeterminate-ticks", required=True, type=int)
    parser.add_argument(
        "--known-version",
        required=False,
        default=None,
        help="one-time anchor seed (ADR-0006 §A'.6 bootstrap) — not persisted "
        "as a flag anyone should keep passing; it is folded into the state "
        "file on the first successful tick.",
    )
    args = parser.parse_args(argv)

    # Item 1 fix (2026-09-23, this module's own instance of the bug fixed
    # in cli.py this morning): `logging.basicConfig(level=logging.INFO)`
    # attaches a handler to the ROOT logger, which this module's own
    # `"__main__"`-orphaned logger (now fixed above) never reached anyway
    # under `-m`. `configure_logging()` is the one shared, idempotent
    # setup routine -- same handler, same format, same stderr target as
    # `cli.py` and `watch.py`.
    configure_logging()

    # R4 fix (2026-09-23, reviewer REQUEST CHANGES): this entry point
    # never called `load_dotenv()` at all, unlike `cli.py`/`facade.py`
    # (INV-05) -- so the documented `python -m ...` invocation only
    # worked if the operator had pre-sourced `.env` by hand.
    try:
        load_dotenv()
    except Exception as exc:  # noqa: BLE001 - never let a .env load raise raw
        logger.error(
            "check-versions: unexpected error loading .env: %s", type(exc).__name__
        )
        return 1

    # Defect 1 (2026-09-24, user repro): --org/--action/--dataset blank
    # (or --action not a UUID) must be rejected here, BEFORE the state
    # file is even opened -- see `_validate_run_identity`'s docstring for
    # the full invariant.
    identity_error = _validate_run_identity(args.org, args.action, args.dataset)
    if identity_error is not None:
        logger.error("check-versions: %s", identity_error)
        return 1

    try:
        _reject_state_file_inside_repo(args.state_file)

        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = open_state_file_locked(args.state_file)
        except StateFileLocked:
            _emit(
                {
                    "event": "check_tick",
                    "outcome": OUTCOME_SKIPPED_LOCKED,
                    "org_id": args.org,
                    "action_id": args.action,
                }
            )
            return 0
        try:
            # RQ-1 fix (2026-09-23 re-review): `fd` above is now the
            # SIDECAR lock's fd, not the data file's -- read the data
            # file itself through `load_state_from_file`, independent of
            # the lock.
            state = load_state_from_file(args.state_file)

            # R4 fix: a missing/renamed credential env var used to escape
            # as a raw `KeyError` traceback instead of a fail-closed
            # outcome + exit 1 (watch.py already got this right).
            try:
                client_id = os.environ["IDP_CLIENT_ID"].strip()
                client_secret = os.environ["IDP_CLIENT_SECRET"].strip()
                region = os.environ["IDP_REGION"].strip()
            except KeyError as exc:
                logger.error("check-versions: missing required environment variable %s", exc)
                return 1
            idp_probe = MuleSoftVersionProbe(client_id, client_secret, region)

            result = check_once(
                probe=idp_probe,
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
            )
            _emit(result.event)
            for command in result.run_eval_commands:
                logger.info("version_detected run_eval_command=%s", sanitize_for_log(command))
            save_state_atomic(args.state_file, result.new_state)
            return 0 if result.outcome in _ZERO_EXIT_OUTCOMES else 1
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except CheckVersionsRefused as exc:
        _emit({"event": "check_tick", "outcome": exc.outcome, "message": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - F-2/INV-02: never a raw traceback/path here
        # ⚠️ Fixed 2026-09-23 (`/test` gate, F-2): this try only ever
        # caught `CheckVersionsRefused` -- an `OSError` from `mkdir()`
        # (or from `open_state_file_locked` raising anything other than
        # `StateFileLocked`) escaped `main()`, the outermost caller, as a
        # RAW TRACEBACK carrying the state-file path. Identical shape and
        # identical fix to `cli.py`'s own catch-all: only the type name
        # and frame location are logged, never `str(exc)`.
        logger.error(
            "check-versions: unexpected error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
