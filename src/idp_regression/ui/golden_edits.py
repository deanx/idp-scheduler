"""Golden dataset edit utilities for the console review surface (T-02.2.1 / T-02.2.2).

Both the single-field PATCH and the whole-file POST /replace endpoints use these
helpers. They spend ZERO IDP quota — they POST to the evaluation platform's
dataset-items API, never to the MuleSoft IDP extraction endpoint.

Validation reuses the same underlying functions that provision_golden_dataset.py
calls: `jsonschema.Draft7Validator(load_golden_schema()).validate(entry)` and
`validate_golden_structure(entry)`. This is "calling into the script's validation"
without duplicating the logic — the same primitives, not a copy.

The deterministic item id uses the SAME namespace UUID as provision_golden_dataset.py's
`_ITEM_NAMESPACE`. They MUST stay in sync: `uuid5(dataset|document_id)` is the
idempotency key on the platform. A different namespace here would create duplicate
items instead of upserts. If provision_golden_dataset.py ever changes its namespace,
this constant must change with it — document that coupling in the PR description.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import jsonschema

from idp_regression.classifier.gate import validate_golden_structure
from idp_regression.classifier.types import Golden
from idp_regression.platform.schema import load_golden_schema

#: Pinned item-namespace — same value as provision_golden_dataset.py's `_ITEM_NAMESPACE`.
#: See module docstring for the coupling contract.
ITEM_NAMESPACE = uuid.UUID("9d3c7e14-5a62-4b8f-9e07-3c1d6f2a8b45")


def item_id(dataset_name: str, document_id: str) -> str:
    """Deterministic platform dataset-item id — same formula as provision_golden_dataset.py."""
    return str(uuid.uuid5(ITEM_NAMESPACE, f"{dataset_name}|{document_id}"))


def validate_entry(entry: dict[str, Any]) -> str | None:
    """Validate a single golden entry against the committed schema.

    Returns None when valid, else a short reason that names WHERE the problem
    is (e.g. `schema: $.fields.invoice_total is invalid (pattern)`) without
    ever echoing the offending value — INV-02.

    Contract: same as provision_golden_dataset.py's `_validation_error`.
    """
    try:
        jsonschema.Draft7Validator(load_golden_schema()).validate(entry)
        validate_golden_structure(cast(Golden, entry))
    except jsonschema.ValidationError as exc:
        return f"schema: {exc.json_path} is invalid ({exc.validator})"
    except Exception as exc:  # noqa: BLE001 — INV-02: type name only, never str(exc) value
        return f"structure: {type(exc).__name__}"
    return None


def build_item_payload(
    dataset_name: str,
    document_id: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Build the platform dataset-item upsert payload.

    Same shape as provision_golden_dataset.py's `_build_item_payload`.
    The deterministic `id` makes re-calling this an UPSERT rather than
    an append.
    """
    expected_output: dict[str, Any] = {"fields": entry["fields"]}
    if entry.get("tables"):
        expected_output["tables"] = entry["tables"]
    if entry.get("prompts"):
        expected_output["prompts"] = entry["prompts"]
    return {
        "id": item_id(dataset_name, document_id),
        "datasetName": dataset_name,
        "input": {"document_id": document_id},
        "expectedOutput": expected_output,
    }
