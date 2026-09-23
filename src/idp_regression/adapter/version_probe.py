"""Version-existence probe (ADR-0006 §Decision A' — bounded existence
probing).

``POST …/actions/{action}/versions/{v}/executions`` with a deliberately
EMPTY multipart body discriminates version existence without ever
submitting a document: 400 ``Invalid query parameter 'file'`` means the
version exists (routing succeeded, payload validation rejected); 404
``…version {v} not found`` means it does not. Zero extraction quota is
spent. This is an *existence check, not an enumeration* — see the ADR for
the full design (the anchored semver walk, the two control probes, the
observability contract).

Reuses ``adapter.transport``'s shared no-redirect opener (via
``transport.post_empty_multipart``) and the same ``TokenCache`` shape
``idp_client.MuleSoftIDPAdapter`` uses — never a second HTTP transport
(ADR-0006 §A'.1, D14): a second opener would be a second place a 3xx
could re-send the Bearer token (REG-07).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import Protocol

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPTransportError
from idp_regression.adapter.oauth import fetch_access_token
from idp_regression.adapter.token_cache import TokenCache

logger = logging.getLogger(__name__)

#: The version grammar that reaches a URL path (ADR-0002's rule, reused
#: as defence-in-depth at the URL-build site here — ADR-0006 §A'.1: the
#: version id is locally GENERATED from a grammar we own, never received
#: from an external response, so this is a generator-bug guard, not an
#: untrusted-input boundary).
VERSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

#: The anchor and every walked candidate must be strict semver
#: (ADR-0006 §A'.8 R1) — a non-conforming anchor causes a refusal to run,
#: never a best-effort grid over a scheme that cannot contain the answer.
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

#: A version guaranteed never to exist — the negative control (§A'.7).
NEGATIVE_CONTROL_VERSION = "999.999.999"


class ProbeResult(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"
    UNKNOWN = "unknown"  # load-bearing — see classify_probe_response.


# Pinned against a captured LIVE response (SR-1 / proposed CT-06) — never
# a fixture authored from this parser. Captures:
#   tests/fixtures/live/version_probe_exists.raw.json
#   tests/fixtures/live/version_probe_absent.raw.json
# Captured 2026-09-23 against the real org/action in .env
# (org ef1232be-0e85-43e7-a7b2-927d32eb6d38, action
# 078ca317-d3a2-4979-8386-7daf4453ea3e), zero extraction quota spent.
_EXISTS_DETAIL = "Invalid query parameter 'file'"
_ABSENT_DETAIL_PATTERN = re.compile(r"^Document Action Id: .+ and version .+ not found$")


def classify_probe_response(status_code: int, detail: object) -> ProbeResult:
    """Pure. Discriminates on the response DETAIL, never the status code
    alone (ADR-0006 §A'.4) — a 400/404 with any other detail, a 401/403,
    a 429, a 5xx, or anything malformed all fall through to the default
    branch, ``UNKNOWN``. **This default branch is the whole safety
    argument (D6): an ambiguous response read as ``ABSENT`` is the
    fail-open this design exists to prevent.**"""
    if status_code == 400 and detail == _EXISTS_DETAIL:
        return ProbeResult.EXISTS
    if status_code == 404 and isinstance(detail, str) and _ABSENT_DETAIL_PATTERN.match(detail):
        return ProbeResult.ABSENT
    return ProbeResult.UNKNOWN


class IDPVersionProbe(Protocol):
    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult: ...


def _executions_base_url(region: str, org_id: str, action_id: str, version: str) -> str:
    return (
        f"https://idp-rt.{region}.anypoint.mulesoft.com/api/v1"
        f"/organizations/{org_id}/actions/{action_id}/versions/{version}/executions"
    )


class MuleSoftVersionProbe:
    """``IDPVersionProbe`` Protocol implementation. Same host, same path
    family, same OAuth credential as ``MuleSoftIDPAdapter.extract()`` —
    there is no separate "management plane" (ADR-0006 §A'.1)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        region: str,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._region = region
        self._timeout_seconds = timeout_seconds
        self._token_cache = TokenCache(
            fetch=lambda: fetch_access_token(client_id, client_secret, timeout_seconds),
            refresh_margin_seconds=60.0,
            clock=clock,
        )
        #: Set after every ``probe()`` call — not part of the Protocol
        #: contract, read only by the tick driver to special-case a 429
        #: (which must abandon the rest of the tick's probes, §A'.4) since
        #: ``ProbeResult`` alone cannot distinguish "429" from any other
        #: UNKNOWN-causing response.
        self.last_status_code: int | None = None

    def probe(self, org_id: str, action_id: str, version: str) -> ProbeResult:
        if not VERSION_ID_PATTERN.match(version):
            # Defence-in-depth only (ADR-0002's regex) — a generator bug
            # producing an off-grammar candidate must never reach a URL.
            self.last_status_code = None
            return ProbeResult.UNKNOWN
        token = self._token_cache.get()
        url = _executions_base_url(self._region, org_id, action_id, version)
        try:
            status, body = transport.post_empty_multipart(
                url,
                timeout_seconds=self._timeout_seconds,
                headers={"Authorization": f"Bearer {token}"},
            )
        except IDPTransportError:
            self.last_status_code = None
            return ProbeResult.UNKNOWN
        self.last_status_code = status
        if status in (401, 403):
            logger.error(
                "probe_auth_failure version=%s status=%s",
                transport.sanitize_for_log(version),
                status,
            )
        detail = body.get("detail") if isinstance(body, dict) else None
        return classify_probe_response(status, detail)


# -- Candidate generation (ADR-0006 §A'.2) -----------------------------------


def parse_semver(version: str) -> tuple[int, int, int] | None:
    """``None`` for anything that is not strict ``M.m.p`` semver (§A'.8
    R1) — a detector that cannot enumerate its candidate space must say
    so, never guess."""
    if not SEMVER_PATTERN.match(version):
        return None
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def format_semver(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def walk_candidates(
    anchor: str,
    *,
    patch_lookahead: int,
    minor_lookahead: int,
    major_lookahead: int,
) -> Iterator[str]:
    """One pass of the per-tick walk from ``anchor``: patch axis, then
    minor axis, then major axis (§A'.2). Does **not** re-anchor itself —
    the caller re-anchors and calls this again on every HIT, which is
    what turns a static grid into a walk. Kept a pure generator so the
    candidate *shape* is unit-testable independent of any HTTP concern."""
    parsed = parse_semver(anchor)
    if parsed is None:
        return
    major, minor, patch = parsed
    for offset in range(1, patch_lookahead + 1):
        yield format_semver(major, minor, patch + offset)
    for offset in range(1, minor_lookahead + 1):
        yield format_semver(major, minor + offset, 0)
    for offset in range(1, major_lookahead + 1):
        yield format_semver(major + offset, 0, 0)


def sweep_candidates(
    anchor: str,
    *,
    sweep_minor_ceiling: int,
    sweep_major_ceiling: int,
    sweep_patch_ceiling: int,
) -> Iterator[str]:
    """The periodic wide sweep (§A'.2) — a much wider, separately-budgeted
    grid: every minor ``0..sweep_minor_ceiling`` (and every patch
    ``0..sweep_patch_ceiling`` at each), plus every major
    ``anchor_major+1..+sweep_major_ceiling``. This is the systematic
    recovery from the walk's named blind spot (a publish more than
    ``--minor-lookahead`` minors ahead whose own ``.0`` was never
    published)."""
    parsed = parse_semver(anchor)
    if parsed is None:
        return
    major, _minor, _patch = parsed
    for m in range(0, sweep_minor_ceiling + 1):
        for p in range(0, sweep_patch_ceiling + 1):
            yield format_semver(major, m, p)
    for dmajor in range(1, sweep_major_ceiling + 1):
        for m in range(0, sweep_minor_ceiling + 1):
            yield format_semver(major + dmajor, m, 0)
