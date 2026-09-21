"""Fail-closed credential presence check for the orchestrator entry point
(T-01.4.1, DEBT-30, NFR N6).

The platform factory (`idp_regression.platform.make_platform`) reads its
required env vars by direct subscript on `os.environ`, so an operator who
forgets one gets a raw `KeyError` traceback rather than a clear message —
and a var that is SET but EMPTY raises no error at all
(`os.environ["X"]` succeeds when `X=""`), so N6's "exits non-zero with a
clear message" was silently defeated by an empty credential (Atchim
review C-1, 2026-09-21 — reproduced live: one required credential var
set to the empty string constructed a real client with no error).

NFR N6 requires `run_eval` to exit non-zero with a clear message BEFORE
ANY NETWORK CALL. This module therefore validates PRESENCE and
NON-EMPTINESS directly against `os.environ` and never constructs a
client at all — deliberately credential-validation-only (Atchim review
R-3): constructing a real client here just to discard it leaks a
background export thread per call, and puts a non-zero client
construction on a path HARDEN-01's split-brain condition ("zero client
constructions") will assert against. The actual client is constructed
later, by whichever code in the per-document run loop (T-01.4.2 onward)
actually needs and holds it. There is therefore no `make_platform()` call
here to wrap a `KeyError` around (Atchim review R-2 is moot by
elimination, not by narrowing).

`make_idp_adapter()` (`idp_regression.adapter.idp_client`) already raises
a clear `RuntimeError(f"missing required env var {name}")` on its own
missing-var path and correctly rejects an empty string too
(`if not value:`) — no wrapping needed there; `run_eval` still catches it
so a missing/empty IDP credential produces a controlled exit rather than
an unhandled traceback (see `facade.py`).

The required var names come from `idp_regression.platform.REQUIRED_ENV_VARS`
(NOT hand-listed here) — both to avoid yet another hand-written key list
(this repo has been bitten by that pattern before — DEBT-40/43/47/49/53)
and, structurally, because `platform/` is the only package allowed to
name the vendor at all (NFR N24; Atchim review R-1).
"""

from __future__ import annotations

import os

from idp_regression.platform import REQUIRED_ENV_VARS


class MissingCredentialError(Exception):
    """A required platform credential env var is missing or empty
    (DEBT-30, NFR N6).

    Names only the missing variable — never a value (INV-02)."""

    def __init__(self, variable_name: str) -> None:
        super().__init__(f"missing required env var {variable_name}")
        self.variable_name = variable_name


def validate_platform_credentials() -> None:
    """Fail closed if any required platform credential env var is
    missing, empty, OR whitespace-only, before any client is constructed
    and before any network call (NFR N6, DEBT-30). Raises
    `MissingCredentialError` naming the first missing/empty/whitespace-
    only variable found (fixed iteration order over `REQUIRED_ENV_VARS`).

    DEBT-44 gate finding 1 (2026-09-21, reproduced live): `if not
    os.environ.get(name)` alone accepts a whitespace-only value (e.g. a
    trailing-space `.env` line, or a CI secret resolving to a blank
    line) -- Python truthiness treats `"   "` as truthy. `.strip()`
    before the truthiness check closes that without over-rejecting: a
    literal `"0"` credential is still non-empty after stripping and is
    correctly accepted."""
    for name in REQUIRED_ENV_VARS:
        if not (os.environ.get(name) or "").strip():
            raise MissingCredentialError(name)
