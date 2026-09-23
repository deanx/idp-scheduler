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

logger = logging.getLogger(__name__)

#: Bumped whenever the state-file schema changes shape. An unrecognised
#: version is a fail-closed halt, never a silent reset (a silent reset
#: would re-walk from nothing and could re-notify every known version).
SCHEMA_VERSION = 1

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


def _resolve_unknowns(
    probe: IDPVersionProbe,
    org_id: str,
    action_id: str,
    pending: list[str],
    *,
    probes_remaining: int,
) -> tuple[list[str], list[str], list[str], int]:
    """Re-probe every pending UNKNOWN before the walk advances past it
    (§A'.4) — an ambiguous answer is a *pending* answer, not a resolved
    one. Returns (probed, hits, still_unknown, probes_used)."""
    probed: list[str] = []
    hits: list[str] = []
    still_unknown: list[str] = []
    probes_used = 0
    for candidate in pending:
        if probes_used >= probes_remaining:
            still_unknown.append(candidate)
            continue
        probes_used += 1
        probed.append(candidate)
        result = probe.probe(org_id, action_id, candidate)
        if result is ProbeResult.EXISTS:
            hits.append(candidate)
        elif result is ProbeResult.UNKNOWN:
            still_unknown.append(candidate)
        # ABSENT: resolved, dropped — it is confirmed not to exist.
    return probed, hits, still_unknown, probes_used


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
    ) = _resolve_unknowns(probe, org_id, action_id, pending_unknowns, probes_remaining=max_probes)
    probed.extend(resolved_probed)
    hits.extend(resolved_hits)
    unknowns.extend(still_unknown)
    probes_used += used
    for candidate in resolved_hits:
        parsed_candidate = parse_semver(candidate)
        parsed_anchor = parse_semver(current_anchor)
        if parsed_candidate is not None and (
            parsed_anchor is None or parsed_candidate > parsed_anchor
        ):
            current_anchor = candidate

    while True:
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
            result = probe.probe(org_id, action_id, candidate)
            if result is ProbeResult.EXISTS:
                hits.append(candidate)
                current_anchor = candidate
                re_anchored = True
                break  # re-anchor: restart the walk from the new anchor
            if result is ProbeResult.UNKNOWN:
                unknowns.append(candidate)
                if getattr(probe, "last_status_code", None) == 429:
                    rate_limited = True
                    break
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

    sweep_missed: list[str] = []
    ran_sweep = sweep_every_n_ticks > 0 and tick_count % sweep_every_n_ticks == 0
    if ran_sweep:
        sweep_probed = 0
        for candidate in sweep_candidates(
            walk.final_anchor,
            sweep_minor_ceiling=minor_lookahead * 4,
            sweep_major_ceiling=major_lookahead * 4,
            sweep_patch_ceiling=patch_lookahead * 4,
        ):
            if sweep_probed >= max_probes_per_sweep:
                break
            sweep_probed += 1
            if candidate in walk.hits or candidate in known:
                continue
            result = probe.probe(org_id, action_id, candidate)
            if result is ProbeResult.EXISTS:
                sweep_missed.append(candidate)

    new_known = known | set(walk.hits) | set(sweep_missed)
    new_versions = sorted(set(walk.hits) | set(sweep_missed))

    anchor_changed_at_epoch = state.anchor_changed_at_epoch
    ticks_since_last_detection = state.ticks_since_last_detection + 1
    if new_versions:
        anchor_changed_at_epoch = clock()
        ticks_since_last_detection = 0

    if new_versions:
        outcome = OUTCOME_NEW_VERSION_DETECTED
    elif walk.ceiling_reached:
        outcome = OUTCOME_CEILING_REACHED
    elif walk.unknowns:
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
        pending_unknowns=sorted(walk.unknowns),
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
            "probe_count": walk.probes_used,
            "ticks_since_last_detection": ticks_since_last_detection,
            "days_since_anchor_changed": days_since_anchor_changed,
            "positive_control": str(positive_control),
            "negative_control": str(negative_control),
            "sweep_ran": ran_sweep,
            "sweep_found_missed_version": sweep_missed or None,
        }
    )

    run_eval_commands = [
        f"idp-regression --org {org_id} --action {action_id} --version {version} "
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

    logging.basicConfig(level=logging.INFO)

    try:
        _reject_state_file_inside_repo(args.state_file)

        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(args.state_file), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _emit(
                    {
                        "event": "check_tick",
                        "outcome": OUTCOME_SKIPPED_LOCKED,
                        "org_id": args.org,
                        "action_id": args.action,
                    }
                )
                return 0
            with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as fh:
                state = load_state(fh)

            client_id = os.environ["IDP_CLIENT_ID"].strip()
            client_secret = os.environ["IDP_CLIENT_SECRET"].strip()
            region = os.environ["IDP_REGION"].strip()
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


if __name__ == "__main__":
    sys.exit(main())
