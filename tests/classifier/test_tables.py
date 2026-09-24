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


# --- DEBT-09: duplicate `match_key` behavior (decided, pinned) ------------
#
# Decision: a duplicate normalized `match_key` in the *actual* is a
# LEGITIMATE COLLAPSE, not a reportable defect. The classifier cannot tell
# "two invoice lines that genuinely share the same SKU" (ordinary business
# data under DATA-MODEL-01's `match_key: "sku"` golden) apart from "the
# adapter's pages[]-vs-top-level union seam doubled a row" (ADR-0002 R-2) --
# both present as two actual rows with the same match_key and no other
# signal. Raising `MalformedActualError` would false-fail ordinary
# multi-line-same-SKU invoices, which is worse than the alternative for the
# common case; there is no principled `duplicate_match_key` verdict to add
# without a `match_key`-uniqueness contract that DATA-MODEL-01 does not make
# today (adding one would repeat DEBT-05's "not a code-only fix" lesson).
# So last-write-wins stays, explicit and pinned here rather than accidental,
# and named from the classifier side in ADR-0002's R-2 bullet so the
# coupling to the adapter's documented-load-bearing masking is discoverable
# from both ends of the seam.
def test_duplicate_actual_match_key_collapses_last_write_wins_not_raise() -> None:
    # Two actual rows both normalize to "Widget A". No MalformedActualError,
    # no synthetic new_line for the dropped duplicate, and the surviving
    # row is the LAST one in list order (dict last-write-wins on a_index).
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {
            "line_items": [
                _row("Widget A", "10", "50.00"),  # first duplicate: correct price
                _row("Widget A", "10", "999.00"),  # second duplicate: wins (last)
                _row("Widget B", "2", "20.00"),
            ]
        },
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    up = [r for r in rows if r["column"] == "unit_price" and r["match_key"] == "Widget A"]
    # Exactly one unit_price sub-verdict for Widget A -- the first duplicate
    # was silently dropped, not surfaced as a second row or a new_line.
    assert len(up) == 1
    assert up[0]["actual"] == "999.00"
    assert up[0]["verdict"] == "wrong_value"
    assert not any(r["verdict"] == "new_line" for r in rows)


def test_duplicate_golden_match_key_second_row_is_missing() -> None:
    # Two golden rows share a match_key; only one actual row can pair with
    # it (BR8 pairing pops the actual index), so the second golden row
    # reports "missing" even though a same-keyed actual row existed.
    golden: Golden = {
        "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        "tables": {
            "line_items": {
                "match_key": "description",
                "critical": True,
                "rows": [
                    {"description": "Widget A", "qty": "10"},
                    {"description": "Widget A", "qty": "5"},  # duplicate golden match_key
                ],
            }
        },
    }
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {"line_items": [{"description": {"value": "Widget A"}, "qty": {"value": "10"}}]},
    )
    v = classify(golden, actual)
    rows = _table(v, "line_items")["rows"]
    verdicts_for_widget_a = [r["verdict"] for r in rows if r["match_key"] == "Widget A"]
    assert verdicts_for_widget_a.count("missing") == 1
    assert overall_gate(v) == "FAIL"  # critical block, the second row's missing qty fails it