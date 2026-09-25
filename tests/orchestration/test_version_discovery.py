"""Discovering which Action versions exist.

The property under test is honesty about ignorance. IDP has no
enumeration endpoint, so this is a bounded sweep of existence probes,
and three of its four failure modes produce a list that is INCOMPLETE:
the probe budget ran out, a 429 abandoned the sweep, or a candidate came
back `unknown`. A picker that rendered any of those as "this version
does not exist" would tell a user their freshly-published version was
never published.
"""

from __future__ import annotations

import pytest

from idp_regression.adapter.version_probe import ProbeResult
from idp_regression.orchestration.version_discovery import (
    AnchorNotSemverError,
    discover_versions,
)


class FakeProbe:
    def __init__(self, existing: set[str], *, unknown: set[str] | None = None,
                 rate_limit_after: int | None = None) -> None:
        self.existing = existing
        self.unknown = unknown or set()
        self.rate_limit_after = rate_limit_after
        self.calls: list[str] = []
        self.last_status_code: int | None = 400

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        self.calls.append(version)
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            self.last_status_code = 429
            return ProbeResult.UNKNOWN
        if version in self.unknown:
            self.last_status_code = 500
            return ProbeResult.UNKNOWN
        if version in self.existing:
            self.last_status_code = 400
            return ProbeResult.EXISTS
        self.last_status_code = 404
        return ProbeResult.ABSENT


def test_the_anchor_is_probed_first_and_always() -> None:
    """A picker that offered a trusted version which does not exist would
    send a batch into a failure on document one, after paying for it."""
    probe = FakeProbe({"1.2.3"})
    outcome = discover_versions(probe, "org", "action", "1.2.3")
    assert probe.calls[0] == "1.2.3"
    assert [v.version for v in outcome.versions] == ["1.2.3"]


def test_existing_versions_are_found_and_ordered_by_semver() -> None:
    probe = FakeProbe({"1.0.0", "1.0.2", "1.2.0", "2.0.0"})
    outcome = discover_versions(probe, "org", "action", "1.0.0")
    assert [v.version for v in outcome.versions] == ["1.0.0", "1.0.2", "1.2.0", "2.0.0"]
    assert all(v.status == "exists" for v in outcome.versions)


def test_an_unknown_is_offered_not_hidden() -> None:
    """`unknown` means the probe could not classify it -- which is not
    the same as absent, and a version the user just published is exactly
    the kind of thing a transient 500 hides."""
    probe = FakeProbe({"1.0.0"}, unknown={"1.0.1"})
    outcome = discover_versions(probe, "org", "action", "1.0.0")
    statuses = {v.version: v.status for v in outcome.versions}
    assert statuses == {"1.0.0": "exists", "1.0.1": "unknown"}


def test_absent_versions_are_simply_not_listed() -> None:
    probe = FakeProbe({"1.0.0"})
    outcome = discover_versions(probe, "org", "action", "1.0.0")
    assert [v.version for v in outcome.versions] == ["1.0.0"]


def test_a_429_abandons_the_sweep_and_says_so() -> None:
    """ADR-0006 §A'.4 is unqualified about abandoning on 429. Continuing
    would worsen the limit AND produce a list whose absences mean
    nothing, so the flag is what makes the partial list readable."""
    probe = FakeProbe({"1.0.0"}, rate_limit_after=3)
    outcome = discover_versions(probe, "org", "action", "1.0.0")
    assert outcome.rate_limited is True
    assert len(probe.calls) == 4


def test_the_probe_budget_truncates_rather_than_sweeping_forever() -> None:
    probe = FakeProbe(set())
    outcome = discover_versions(probe, "org", "action", "1.0.0", max_probes=5)
    assert outcome.probes_used == 5
    assert outcome.truncated is True


def test_a_complete_sweep_is_not_reported_as_truncated() -> None:
    probe = FakeProbe({"1.0.0"})
    outcome = discover_versions(probe, "org", "action", "1.0.0", max_probes=1000)
    assert outcome.truncated is False
    assert outcome.rate_limited is False


@pytest.mark.parametrize("anchor", ["1.0", "v1.0.0", "latest", "", "1.0.0-rc1"])
def test_a_non_semver_anchor_is_refused_rather_than_guessed(anchor: str) -> None:
    """§A'.8 R1: a detector that cannot enumerate its candidate space
    says so. A best-effort grid over a scheme that cannot contain the
    answer is worse than a refusal."""
    with pytest.raises(AnchorNotSemverError):
        discover_versions(FakeProbe(set()), "org", "action", anchor)


def test_no_candidate_is_probed_twice() -> None:
    probe = FakeProbe({"1.0.0"})
    discover_versions(probe, "org", "action", "1.0.0")
    assert len(probe.calls) == len(set(probe.calls))
