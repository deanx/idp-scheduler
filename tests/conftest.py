"""Shared pytest config — integration tests are opt-in only.

``@pytest.mark.integration`` tests require live external services
(Langfuse, IDP) and are skipped by default and in CI unless
``RUN_INTEGRATION_TESTS=1`` is set (the credentials themselves are never
read here — the tests that need them read their own env vars and skip if
absent).
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION_TESTS") == "1":
        return
    skip_integration = pytest.mark.skip(
        reason="integration test — set RUN_INTEGRATION_TESTS=1 to run against live services"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
