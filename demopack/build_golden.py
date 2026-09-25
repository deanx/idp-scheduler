#!/usr/bin/env python
"""Build ``testpack/golden_set.json`` from ``invoice_data.py`` (the same
ground truth ``generate_pdfs.py`` renders into PDF text), in the
DATA-MODEL-01 SS1 golden shape, and validate every entry against the
committed ``golden_schema_v1.json`` the same way
``scripts/provision_golden_dataset.py`` does (via the stdlib
``jsonschema`` package already used by the project).

Usage::

    .venv/bin/python testpack/build_golden.py

Writes ``testpack/golden_set.json`` and prints the validation result for
every entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jsonschema
from invoice_data import INVOICES, Invoice

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = (
    REPO_ROOT / "src" / "idp_regression" / "platform" / "schema" / "golden_schema_v1.json"
)
OUT_PATH = Path(__file__).resolve().parent / "golden_set.json"

# critical markers -- see testpack/README.md "Critical markers" section
# for the justification of each. Applied uniformly across every document
# that has the field (inv-005 lacks po_number/tax on the page itself).
FIELD_CRITICAL = {
    "vendor_name": True,
    "bill_to": False,
    "invoice_number": True,
    "invoice_date": True,
    "po_number": False,
    "currency": True,
    "subtotal": True,
    "tax": False,
    "total": True,
}

# DEBT-80 (decided 2026-09-24): fields whose FORMAT is part of the contract,
# so a `wrong_format` verdict fails the gate instead of staying informational.
# `invoice_date` qualifies because the extraction prompt is *required* to emit
# ISO-8601 -- dropping that instruction is the single most common real-world
# date-prompt regression, and it is precisely the one the 1.1.0 live run
# proved the gate was green for. Everything else keeps BR3's default.
FIELD_FORMAT_CRITICAL = {
    "invoice_date": True,
}

FIELD_TYPE = {
    "vendor_name": "text",
    "bill_to": "text",
    "invoice_number": "id",
    "invoice_date": "date",
    "po_number": "id",
    "currency": "text",
    "subtotal": "number",
    "tax": "number",
    "total": "number",
}


def _field(name: str, value: str) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "value": value,
        "type": FIELD_TYPE[name],
        "critical": FIELD_CRITICAL[name],
    }
    if FIELD_FORMAT_CRITICAL.get(name):
        spec["format_critical"] = True
    return spec


def build_entry(inv: Invoice) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "vendor_name": _field("vendor_name", inv.vendor_name),
        "bill_to": _field("bill_to", inv.bill_to),
        "invoice_number": _field("invoice_number", inv.invoice_number),
        "invoice_date": _field("invoice_date", inv.invoice_date),
        "currency": _field("currency", inv.currency),
        "subtotal": _field("subtotal", inv.subtotal),
        "total": _field("total", inv.total),
    }

    if inv.po_number is not None:
        fields["po_number"] = _field("po_number", inv.po_number)
    else:
        # inv-005: the document has no PO number printed. We still declare
        # the field (critical: false) with an inert placeholder value that
        # is NEVER compared -- _classify_field short-circuits to `missing`
        # the moment the actual side is absent/empty, before it ever looks
        # at the golden value. This is the deliberate `missing`-verdict
        # exercise the pack asks for; see README "Deliberate `missing`
        # exercise" for the full reasoning.
        fields["po_number"] = _field("po_number", "N/A")

    if inv.tax is not None:
        fields["tax"] = _field("tax", inv.tax)
    else:
        fields["tax"] = _field("tax", "0.00")

    rows = [
        {
            "sku": li.sku,
            "description": li.description,
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "amount": li.amount,
        }
        for li in inv.line_items
    ]

    return {
        "document_id": inv.document_id,
        "fields": fields,
        "tables": {
            "line_items": {
                "match_key": "sku",
                "critical": True,
                "rows": rows,
            }
        },
    }


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)

    golden_set: dict[str, Any] = {}
    all_ok = True
    for key, inv in INVOICES.items():
        entry = build_entry(inv)
        errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
        status = "VALID" if not errors else "INVALID"
        if errors:
            all_ok = False
        print(f"{key}: {status}")
        for err in errors:
            print(f"    {list(err.path)}: {err.message}")
        golden_set[key] = entry

    OUT_PATH.write_text(json.dumps(golden_set, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH} ({len(golden_set)} entries)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
