"""Tests for line-item comparison (T-01.1.3) — BR8 match_key pairing.

Rows are paired by the golden table's ``match_key`` (NOT position). An
unmatched actual row -> ``new_line`` (never fails the gate, BR3). An
unmatched golden row -> ``missing`` (fails the gate if the block is
critical, BR2). Row reordering is not a diff.
"""

from __future__ import annotations

from typing import cast

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.types import (
    FieldValue,
    Golden,
    NormalizedOutput,
    TableVerdict,
    VerdictMap,
)

GOLDEN: Golden = {
    "fields": {
        "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
        "total": {"value": "1250.00", "type": "number", "critical": True},
    },
    "tables": {
        "line_items": {
            "match_key": "description",
            "critical": True,
            "rows": [
                {"description": "Widget A", "qty": "10", "unit_price": "50.00"},
                {"description": "Widget B", "qty": "2", "unit_price": "20.00"},
            ],
        }
    },
}


def _actual(
    fields: dict[str, FieldValue],
    tables: dict[str, list[dict[str, FieldValue]]] | None = None,
) -> NormalizedOutput:
    out: NormalizedOutput = {"status": "SUCCEEDED", "fields": fields}
    if tables is not None:
        out["tables"] = tables
    return out


def _row(description: str, qty: str, unit_price: str) -> dict[str, FieldValue]:
    return {
        "description": {"value": description},
        "qty": {"value": qty},
        "unit_price": {"value": unit_price},
    }


def _table(verdicts: VerdictMap, name: str) -> TableVerdict:
    return cast(TableVerdict, verdicts[name])


def test_line_items_matched_by_match_key_not_position() -> None:
    # BR8: actual rows in reverse order still match.
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {
            "line_items": [
                _row("Widget B", "2", "20.00"),
                _row("Widget A", "10", "50.00"),
            ]
        },
    )
    v = classify(GOLDEN, actual)
    table = _table(v, "line_items")
    assert table["verdict"] == "detail"
    rows = table["rows"]
    # every sub-verdict is a match
    assert all(r["verdict"] == "match" for r in rows)
    assert overall_gate(v) == "PASS"


def test_line_item_wrong_value_column_fails_when_critical() -> None:
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "10", "99.00"), _row("Widget B", "2", "20.00")]},
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    up = [r for r in rows if r["column"] == "unit_price" and r["match_key"] == "Widget A"]
    assert up[0]["verdict"] == "wrong_value"
    assert overall_gate(v) == "FAIL"


def test_new_line_on_unmatched_actual_row_never_fails_gate() -> None:
    # An actual row with no golden counterpart -> new_line (BR3, informational).
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {
            "line_items": [
                _row("Widget A", "10", "50.00"),
                _row("Widget B", "2", "20.00"),
                _row("Widget C", "7", "13.00"),
            ]
        },
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    assert any(r["verdict"] == "new_line" and r["match_key"] == "Widget C" for r in rows)
    assert overall_gate(v) == "PASS"


def test_missing_on_unmatched_golden_row_fails_when_critical() -> None:
    # A golden row with no actual counterpart -> missing; critical block -> FAIL.
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "10", "50.00")]},  # Widget B missing
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    missing_rows = [r for r in rows if r["verdict"] == "missing"]
    assert any(r["match_key"] == "Widget B" for r in missing_rows)
    assert overall_gate(v) == "FAIL"


def test_missing_golden_row_non_critical_table_passes() -> None:
    golden: Golden = {
        "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        "tables": {
            "line_items": {
                "match_key": "description",
                "critical": False,
                "rows": [{"description": "Widget A", "qty": "10"}],
            }
        },
    }
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {"line_items": []},
    )
    v = classify(golden, actual)
    assert overall_gate(v) == "PASS"


def test_missing_actual_column_in_matched_row_is_missing() -> None:
    # Matched row, but a column absent in actual -> missing for that column.
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {
            "line_items": [
                {"description": {"value": "Widget A"}, "qty": {"value": "10"}},  # no unit_price
                _row("Widget B", "2", "20.00"),
            ]
        },
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    up = [r for r in rows if r["column"] == "unit_price" and r["match_key"] == "Widget A"]
    assert up[0]["verdict"] == "missing"


def test_empty_actual_table_all_golden_rows_missing() -> None:
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {"line_items": []},
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    assert all(r["verdict"] == "missing" for r in rows)
    assert overall_gate(v) == "FAIL"


def test_table_verdict_carries_critical_and_type_none() -> None:
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "10", "50.00"), _row("Widget B", "2", "20.00")]},
    )
    v = classify(GOLDEN, actual)
    table = _table(v, "line_items")
    assert table["critical"] is True
    assert table["type"] is None