"""Golden JSON Schema provisioning — raw-REST upsert, never drop.

ADR-0005 #4: schema evolution is expand/contract, never drop. This step
never deletes a dataset schema and never sends an empty/null schema —
``POST /api/public/v2/datasets`` upserts by name (verified live,
``docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md``).
"""

from __future__ import annotations

import logging

from idp_regression.platform.errors import PlatformError, TransportError
from idp_regression.platform.schema import load_golden_schema
from idp_regression.platform.transport import HttpClient

logger = logging.getLogger(__name__)


def provision_golden_schema(client: HttpClient, *, dataset_name: str) -> None:
    """Upsert the committed golden schema onto ``dataset_name``.

    Raises ``PlatformError`` on failure. Never falls back to a second call
    with an empty/null schema on failure — a failed provisioning attempt
    leaves whatever schema (if any) already exists on the platform intact.
    """
    schema = load_golden_schema()
    if not schema:
        # Atchim suggestion: this was an `assert` (stripped under -O).
        # The committed schema file must never be empty/absent — a code
        # defect, not a caller error, but still a real raise, not a
        # silently-optimized-away guard.
        raise PlatformError("the committed golden schema must never be empty")
    try:
        status, body = client.request(
            "POST",
            "/api/public/v2/datasets",
            {"name": dataset_name, "expectedOutputSchema": schema},
        )
    except TransportError as exc:
        raise PlatformError(f"provision_golden_schema transport failure: {exc}") from exc
    if status >= 400:
        logger.error(
            "schema_provisioning_failed status=%s dataset=%s",
            status,
            dataset_name,
        )
        raise PlatformError(f"provision_golden_schema failed for {dataset_name!r}: HTTP {status}")
