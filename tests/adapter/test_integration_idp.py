"""T-01.2.9 — integration tests against the live MuleSoft Anypoint IDP.

Skipped by default and in CI (see tests/conftest.py); opt in with
``RUN_INTEGRATION_TESTS=1``. Requires ``IDP_CLIENT_ID`` / ``IDP_CLIENT_SECRET``
/ ``IDP_REGION`` / ``IDP_ORG_ID`` in the environment (gitignored ``.env``
locally, per Mestre handoff).

(a) The live OAuth token fetch + cache reuse is safe and runnable now (no
    document is submitted) — it always runs when creds are present.
(b) A live submit/poll/normalize needs a real, published IDP action id and
    version. ``IDP_ACTION_ID`` is currently EMPTY and no published version
    is known (per the coordinator's probe) — this test SKIPS with a clear
    reason unless ``IDP_ACTION_ID``/``IDP_ACTION_VERSION`` (or the
    test-only ``IDP_TEST_ACTION_ID``/``IDP_TEST_ACTION_VERSION`` override)
    are set. Do NOT submit any document to the live IDP without them.
"""

from __future__ import annotations

import os

import pytest

from idp_regression.adapter.idp_client import make_idp_adapter

pytestmark = pytest.mark.integration


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set — integration test needs live IDP credentials")
    return value


def test_live_oauth_token_fetch_and_cache_reuse() -> None:
    _require_env("IDP_CLIENT_ID")
    _require_env("IDP_CLIENT_SECRET")
    _require_env("IDP_REGION")
    org_id = _require_env("IDP_ORG_ID")

    # ADR-0004 A9 (2026-09-22): org_id is a caller-supplied PARAMETER now,
    # not an env var make_idp_adapter() reads itself -- IDP_ORG_ID here is
    # only this test's own test-harness-convenience source for the value.
    adapter = make_idp_adapter(org_id)
    token_cache = adapter._token_cache  # noqa: SLF001 - white-box, this IS the test

    first = token_cache.get()
    assert isinstance(first, str) and first

    # Cache reuse within the run — no second live call, same token object.
    second = token_cache.get()
    assert second == first


def test_live_submit_poll_and_normalize_a_real_document() -> None:
    _require_env("IDP_CLIENT_ID")
    _require_env("IDP_CLIENT_SECRET")
    _require_env("IDP_REGION")
    org_id = _require_env("IDP_ORG_ID")

    action_id = os.environ.get("IDP_TEST_ACTION_ID") or os.environ.get("IDP_ACTION_ID")
    version = os.environ.get("IDP_TEST_ACTION_VERSION") or os.environ.get("IDP_ACTION_VERSION")
    document_path = os.environ.get("IDP_TEST_DOCUMENT_PATH")
    if not action_id or not version or not document_path:
        pytest.skip(
            "IDP_ACTION_ID/IDP_ACTION_VERSION (or IDP_TEST_ACTION_ID/"
            "IDP_TEST_ACTION_VERSION) and IDP_TEST_DOCUMENT_PATH are not "
            "all set — no published IDP action/version is known yet "
            "(S-01.6 pins this); skipping the live submit/poll test"
        )

    adapter = make_idp_adapter(org_id)
    out = adapter.extract(document_path, action_id, version)
    assert out["status"]
    assert isinstance(out["fields"], dict)
