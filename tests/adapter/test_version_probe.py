"""ADR-0006 §A'.4 (classify_probe_response) and §A'.2 (walk_candidates) —
the pure, HTTP-free half of version-change detection."""

from __future__ import annotations

import json

from idp_regression.adapter.version_probe import (
    ProbeResult,
    classify_probe_response,
    format_semver,
    parse_semver,
    sweep_candidates,
    walk_candidates,
)

with open("tests/fixtures/live/version_probe_exists.raw.json") as _fh:
    LIVE_EXISTS_CAPTURE = json.load(_fh)
with open("tests/fixtures/live/version_probe_absent.raw.json") as _fh:
    LIVE_ABSENT_CAPTURE = json.load(_fh)
with open("tests/fixtures/live/version_probe_wrong_org.raw.json") as _fh:
    LIVE_WRONG_ORG_CAPTURE = json.load(_fh)
with open("tests/fixtures/live/version_probe_wrong_action.raw.json") as _fh:
    LIVE_WRONG_ACTION_CAPTURE = json.load(_fh)


# -- classify_probe_response: table-driven over ADR-0006 §A'.4 --------------


#: The (action_id, version) baked into the committed live captures
#: (see the docstring above `_ABSENT_DETAIL_PATTERN` in version_probe.py).
_LIVE_ACTION_ID = "078ca317-d3a2-4979-8386-7daf4453ea3e"
_LIVE_VERSION = "9.9.9"


def test_classifies_the_captured_live_exists_response_as_exists() -> None:
    # SR-1/CT-06: pinned against a captured LIVE response, not a
    # hand-authored fixture.
    result = classify_probe_response(
        LIVE_EXISTS_CAPTURE["status"],
        LIVE_EXISTS_CAPTURE["detail"],
        action_id=_LIVE_ACTION_ID,
        version=_LIVE_VERSION,
    )
    assert result is ProbeResult.EXISTS


def test_exists_branch_is_deliberately_unbound_to_the_calling_action_id_and_version() -> None:
    # G-2 (Wave-1 Lane B /test stamp finding): the previous version of this
    # test passed the SAME action_id/version the capture was taken against,
    # which reads as though EXISTS is constrained by them -- it is not. The
    # 400 "Invalid query parameter 'file'" body carries no echo to bind
    # against (unlike the 404 ABSENT branch's R3 fix below), so a 400 for a
    # completely different action/version still classifies as EXISTS. This
    # pins that as a DECISION (see the comment on the EXISTS branch in
    # version_probe.py), not an oversight. It is SAFE because IDP never sends
    # this 400 for a wrong org (403) or a wrong action (404), per the two
    # `test_a_live_wrong_*` pins below, recorded 2026-09-27.
    result = classify_probe_response(
        LIVE_EXISTS_CAPTURE["status"],
        LIVE_EXISTS_CAPTURE["detail"],
        action_id="totally-different-action-id",
        version="0.0.0",
    )
    assert result is ProbeResult.EXISTS


def test_a_live_wrong_org_response_is_never_exists() -> None:
    """G-2, closed against RECORDED responses (SR-1): a wrong org is a 403
    with no `detail`, so it can never be mistaken for the EXISTS 400."""
    result = classify_probe_response(
        LIVE_WRONG_ORG_CAPTURE["status"],
        LIVE_WRONG_ORG_CAPTURE.get("detail"),
        action_id=_LIVE_ACTION_ID,
        version=_LIVE_VERSION,
    )
    assert result is ProbeResult.UNKNOWN


def test_a_live_wrong_action_response_is_never_exists_nor_absent() -> None:
    """A wrong action is a 404 "Action Id: <id>" with no version in it. It
    must not read as EXISTS, and not as a confirmed ABSENT either: it says
    the ACTION is unknown, not that this version is missing."""
    result = classify_probe_response(
        LIVE_WRONG_ACTION_CAPTURE["status"],
        LIVE_WRONG_ACTION_CAPTURE["detail"],
        action_id="00000000-0000-4000-8000-000000000000",
        version=_LIVE_VERSION,
    )
    assert result is ProbeResult.UNKNOWN


def test_classifies_the_captured_live_absent_response_as_absent() -> None:
    result = classify_probe_response(
        LIVE_ABSENT_CAPTURE["status"],
        LIVE_ABSENT_CAPTURE["detail"],
        action_id=_LIVE_ACTION_ID,
        version=_LIVE_VERSION,
    )
    assert result is ProbeResult.ABSENT


def test_400_with_a_different_detail_is_unknown_not_exists() -> None:
    assert (
        classify_probe_response(
            400,
            "Malformed or invalid request body",
            action_id=_LIVE_ACTION_ID,
            version=_LIVE_VERSION,
        )
        is ProbeResult.UNKNOWN
    )


def test_404_with_a_different_detail_is_unknown_not_absent() -> None:
    # The A9 wrong-org case named explicitly in the ADR: a 404 whose
    # detail doesn't match the pinned shape must never read as ABSENT.
    assert (
        classify_probe_response(
            404, "Not Found", action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION
        )
        is ProbeResult.UNKNOWN
    )


def test_404_naming_a_different_action_id_is_unknown_not_absent() -> None:
    # R3 pin: the wrong-org/wrong-action case (A9) -- a 404 that matches
    # the pinned SHAPE but echoes an action id THIS PROBE did not send
    # must never read as ABSENT.
    result = classify_probe_response(
        LIVE_ABSENT_CAPTURE["status"],
        LIVE_ABSENT_CAPTURE["detail"],
        action_id="ffffffff-0000-0000-0000-000000000000",
        version=_LIVE_VERSION,
    )
    assert result is ProbeResult.UNKNOWN


def test_404_naming_a_different_version_is_unknown_not_absent() -> None:
    # R3 pin, the version half of the same echo check.
    result = classify_probe_response(
        LIVE_ABSENT_CAPTURE["status"],
        LIVE_ABSENT_CAPTURE["detail"],
        action_id=_LIVE_ACTION_ID,
        version="1.2.3",
    )
    assert result is ProbeResult.UNKNOWN


def test_401_is_unknown() -> None:
    assert (
        classify_probe_response(401, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )


def test_403_is_unknown() -> None:
    assert (
        classify_probe_response(403, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )


def test_429_is_unknown() -> None:
    assert (
        classify_probe_response(429, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )


def test_5xx_is_unknown() -> None:
    assert (
        classify_probe_response(500, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )
    assert (
        classify_probe_response(503, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )


def test_non_string_detail_is_unknown_never_raises() -> None:
    assert (
        classify_probe_response(400, None, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )
    assert (
        classify_probe_response(404, 12345, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION)
        is ProbeResult.UNKNOWN
    )
    assert (
        classify_probe_response(
            404, {"nested": "object"}, action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION
        )
        is ProbeResult.UNKNOWN
    )


def test_the_default_branch_is_unknown_mutation_pin() -> None:
    """Named per D6: mutation-verified that flipping the default branch to
    ABSENT turns this test RED. A completely unrecognised status/detail
    combination must never resolve to ABSENT."""
    assert (
        classify_probe_response(
            999, "anything at all", action_id=_LIVE_ACTION_ID, version=_LIVE_VERSION
        )
        is ProbeResult.UNKNOWN
    )


# -- walk_candidates: the anchored semver walk shape (§A'.2) -----------------


def test_walk_candidates_patch_axis_first() -> None:
    candidates = list(
        walk_candidates("1.0.0", patch_lookahead=3, minor_lookahead=0, major_lookahead=0)
    )
    assert candidates == ["1.0.1", "1.0.2", "1.0.3"]


def test_walk_candidates_minor_axis_lands_on_dot_zero() -> None:
    candidates = list(
        walk_candidates("1.0.0", patch_lookahead=0, minor_lookahead=3, major_lookahead=0)
    )
    assert candidates == ["1.1.0", "1.2.0", "1.3.0"]


def test_walk_candidates_major_axis_resets_minor_and_patch() -> None:
    candidates = list(
        walk_candidates("1.5.7", patch_lookahead=0, minor_lookahead=0, major_lookahead=2)
    )
    assert candidates == ["2.0.0", "3.0.0"]


def test_walk_candidates_all_three_axes_in_order() -> None:
    candidates = list(
        walk_candidates("1.0.0", patch_lookahead=2, minor_lookahead=2, major_lookahead=1)
    )
    assert candidates == ["1.0.1", "1.0.2", "1.1.0", "1.2.0", "2.0.0"]


def test_walk_candidates_on_a_non_semver_anchor_yields_nothing() -> None:
    assert list(
        walk_candidates("v2-draft", patch_lookahead=3, minor_lookahead=3, major_lookahead=3)
    ) == []


def test_walk_candidates_zero_lookaheads_yields_nothing() -> None:
    assert (
        list(walk_candidates("1.0.0", patch_lookahead=0, minor_lookahead=0, major_lookahead=0))
        == []
    )


# -- sweep_candidates ---------------------------------------------------------


def test_sweep_candidates_covers_a_minor_the_walk_would_miss() -> None:
    # 1.0.0 -> 1.5.7 with no 1.1.0..1.4.0 published is the walk's named
    # blind spot; the wide sweep must still be ABLE to generate 1.5.0 as
    # a candidate (whether a live probe then confirms it is a separate,
    # HTTP-touching concern tested in test_check_versions.py).
    candidates = list(
        sweep_candidates(
            "1.0.0", sweep_minor_ceiling=5, sweep_major_ceiling=0, sweep_patch_ceiling=0
        )
    )
    assert "1.5.0" in candidates


def test_sweep_candidates_on_a_non_semver_anchor_yields_nothing() -> None:
    assert (
        list(
            sweep_candidates(
                "v2-draft", sweep_minor_ceiling=5, sweep_major_ceiling=5, sweep_patch_ceiling=5
            )
        )
        == []
    )


# -- parse_semver / format_semver --------------------------------------------


def test_parse_semver_strict() -> None:
    assert parse_semver("1.0.0") == (1, 0, 0)
    assert parse_semver("10.20.30") == (10, 20, 30)


def test_parse_semver_rejects_non_conforming_schemes() -> None:
    # "2025.09.1" is deliberately NOT in this list: it IS three
    # dot-separated integers, so it matches the ADR's strict
    # ^\d+\.\d+\.\d+$ grammar even though it reads as a date-based
    # scheme -- R1's stated limit (a parseable anchor does not prove IDP
    # constrains the format to conventional semver).
    for bad in ["v2-draft", "1.0", "", "1.0.0-beta", "1.0.0.1"]:
        assert parse_semver(bad) is None, bad


def test_format_semver_roundtrip() -> None:
    assert format_semver(1, 2, 3) == "1.2.3"
    assert parse_semver(format_semver(9, 9, 9)) == (9, 9, 9)
