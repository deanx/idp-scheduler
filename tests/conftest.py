"""Shared pytest config — integration tests are opt-in only.

``@pytest.mark.integration`` tests require live external services
(Langfuse, IDP) and are skipped by default and in CI unless
``RUN_INTEGRATION_TESTS=1`` is set (the credentials themselves are never
read here — the tests that need them read their own env vars and skip if
absent).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

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


@pytest.fixture(autouse=True)
def _restore_the_package_logger() -> Iterator[None]:
    """Suite-wide isolation of the `idp_regression` logger.

    Any test that runs a real `main()` calls `cli.configure_logging()`, which
    binds a StreamHandler to whatever `sys.stderr` is -- under pytest, that
    test's capture stream, closed when the test ends. The handler used to
    survive the test, so a LATER test's background thread (a console job
    logging `job_started`) wrote through it and printed "Logging error ...
    I/O operation on closed file" -- intermittently, depending on test order
    and thread timing. `test_logging_config.py` already isolated itself this
    way; this applies the same snapshot/restore to every test."""
    logger = logging.getLogger("idp_regression")
    handlers, level = list(logger.handlers), logger.level
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
