"""TP-01 — live orchestration end-to-end happy path (`run_eval` against a
REAL MuleSoft Anypoint IDP action and a REAL Langfuse golden dataset).

Skipped by default and in CI (see ``tests/conftest.py``); opt in with
``RUN_INTEGRATION_TESTS=1``. Coverage audit gap 8 (2026-09-21): TP-01 is
deferred on the S-01.6 blocker (no real, published IDP action id/version
exists yet), but that deferral was completely invisible in the test
suite -- there was no file under ``tests/orchestration/`` for it at all,
so a reader scanning collected tests would never learn TP-01 exists,
let alone why it isn't running. This placeholder makes the deferral
show up as a named, skipped test instead of an absence.

Even under ``RUN_INTEGRATION_TESTS=1`` this test still SKIPS until a
real IDP action id + published version is available (S-01.6 spike), and
until the full IDP + Langfuse credential set is present -- at that
point, replace the ``pytest.skip`` below with the real end-to-end run:
seed a small golden dataset on the live platform, call
``run_eval(action_id, version, run_name, dataset_name)``, and assert
exit code 0 with a real experiment + per-document scores written
(SPEC-01 TP-01: "every doc extracted, classified, scored; run holds
`field:<name>` per field + `gate` per doc + action/golden version").
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

#: S-01.6 (not yet run): a real, published MuleSoft Anypoint IDP action
#: id + version. ``IDP_TEST_ACTION_ID``/``IDP_TEST_ACTION_VERSION``
#: mirror the override names ``tests/adapter/test_integration_idp.py``
#: already uses for the same blocker.
_ACTION_ID_VARS = ("IDP_TEST_ACTION_ID", "IDP_ACTION_ID")
_ACTION_VERSION_VARS = ("IDP_TEST_ACTION_VERSION", "IDP_ACTION_VERSION")


def _first_set(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def test_live_happy_path_run_eval_end_to_end() -> None:
    """TP-01 (AC1 happy path): deferred until S-01.6 supplies a real,
    published IDP action id + version. Skips with a reason naming
    exactly what's missing, rather than being absent from the suite."""
    action_id = _first_set(_ACTION_ID_VARS)
    version = _first_set(_ACTION_VERSION_VARS)
    if not action_id or not version:
        pytest.skip(
            "TP-01 deferred (S-01.6 blocker): needs a real, published "
            "MuleSoft Anypoint IDP action id (IDP_TEST_ACTION_ID or "
            "IDP_ACTION_ID) and version (IDP_TEST_ACTION_VERSION or "
            "IDP_ACTION_VERSION) -- neither is configured. See "
            "docs/specs/SPEC-01-baseline-regression.md TP-01 and the "
            "S-01.6 spike."
        )
        return

    pytest.skip(
        "TP-01 live end-to-end run_eval is not yet implemented -- an "
        "action id/version was found, but the real seed-dataset + "
        "run_eval + assert-on-live-experiment steps still need to be "
        "written once S-01.6 confirms the action is usable."
    )
