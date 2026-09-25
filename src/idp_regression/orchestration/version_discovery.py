"""Which versions of an Action exist? — bounded probing, zero quota.

**IDP has no version-enumeration endpoint.** ADR-0006 §Decision A′ settled
that and built what is possible instead: an *existence probe* that
discriminates "this version exists" from "it does not" by POSTing a
deliberately EMPTY multipart body, which routing accepts and payload
validation then rejects. No document is submitted and **no extraction
quota is spent** -- each probe is one HTTP round trip.

So a version picker cannot be a list fetched from IDP. It is a **sweep
of a candidate grid around an anchor**, and it reports three things
rather than two, because that is the truth available:

* `exists`   -- the probe got the 400 that means routing succeeded;
* `absent`   -- a 404 whose body names this action and version;
* `unknown`  -- anything else, including a 429. **Never rendered as
  "absent"**: a version the probe could not classify may well exist, and
  a picker that quietly dropped it would hide the very version the user
  published a minute ago.

The grid is `version_probe.sweep_candidates`' own, so the candidate
*shape* stays defined in one place and stays unit-testable apart from
any HTTP concern.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from idp_regression.adapter.version_probe import (
    IDPVersionProbe,
    ProbeResult,
    parse_semver,
    sweep_candidates,
)

logger = logging.getLogger(__name__)

#: A deliberately small default grid. Every candidate is an HTTP round
#: trip against a customer's org, so a picker that swept hundreds would
#: be a rate-limit incident dressed up as a dropdown.
DEFAULT_MINOR_CEILING = 4
DEFAULT_PATCH_CEILING = 4
DEFAULT_MAJOR_CEILING = 1
DEFAULT_MAX_PROBES = 40


class DiscoveredVersion(NamedTuple):
    version: str
    status: str  # "exists" | "unknown"


class DiscoveryOutcome(NamedTuple):
    anchor: str
    versions: list[DiscoveredVersion]
    probes_used: int
    #: True when the probe budget ran out before the grid did -- the list
    #: is then a PARTIAL answer and the UI says so. A truncated list
    #: presented as complete is how a user concludes their new version
    #: was never published.
    truncated: bool
    #: True when a 429 abandoned the sweep (ADR-0006 §A′.4 is unqualified
    #: about this). Same honesty requirement as `truncated`.
    rate_limited: bool


class AnchorNotSemverError(ValueError):
    """The anchor is not strict `M.m.p`, so no candidate grid can be
    generated from it (ADR-0006 §A′.8 R1: a detector that cannot
    enumerate its candidate space says so rather than guessing)."""


def discover_versions(
    probe: IDPVersionProbe,
    org_id: str,
    action_id: str,
    anchor: str,
    *,
    minor_ceiling: int = DEFAULT_MINOR_CEILING,
    patch_ceiling: int = DEFAULT_PATCH_CEILING,
    major_ceiling: int = DEFAULT_MAJOR_CEILING,
    max_probes: int = DEFAULT_MAX_PROBES,
) -> DiscoveryOutcome:
    """Sweep the grid around `anchor` and report what exists.

    The anchor itself is probed first and always: it is the version the
    caller believes in, and a picker that offered a trusted version which
    does not actually exist would send the user into a batch that fails
    on document one, after paying for it.
    """
    if parse_semver(anchor) is None:
        raise AnchorNotSemverError(
            f"{anchor!r} is not strict semver (M.m.p); no candidate grid can be built from it"
        )

    found: list[DiscoveredVersion] = []
    seen: set[str] = set()
    probes_used = 0
    rate_limited = False
    truncated = False

    candidates = [anchor] + [
        candidate
        for candidate in sweep_candidates(
            anchor,
            sweep_minor_ceiling=minor_ceiling,
            sweep_major_ceiling=major_ceiling,
            sweep_patch_ceiling=patch_ceiling,
        )
        if candidate != anchor
    ]

    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if probes_used >= max_probes:
            truncated = True
            break
        result = probe.probe(org_id, action_id, candidate)
        probes_used += 1
        if getattr(probe, "last_status_code", None) == 429:
            # §A′.4: abandon the rest, and SAY so. Continuing would both
            # worsen the rate limit and produce a list whose absences
            # mean nothing.
            rate_limited = True
            break
        if result is ProbeResult.EXISTS:
            found.append(DiscoveredVersion(candidate, "exists"))
        elif result is ProbeResult.UNKNOWN:
            found.append(DiscoveredVersion(candidate, "unknown"))

    logger.info(
        "version_discovery anchor=%s probes=%d found=%d truncated=%s rate_limited=%s",
        anchor,
        probes_used,
        len(found),
        truncated,
        rate_limited,
    )
    return DiscoveryOutcome(
        anchor=anchor,
        versions=sorted(found, key=lambda v: parse_semver(v.version) or (0, 0, 0)),
        probes_used=probes_used,
        truncated=truncated,
        rate_limited=rate_limited,
    )
