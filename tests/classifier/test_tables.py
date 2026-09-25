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


# --- DEBT-09: duplicate `match_key` behavior (decided 2026-09-24, pinned) ---
#
# Decision: a duplicate normalized `match_key` in the *actual* is ORDINARY
# BUSINESS DATA -- one SKU legitimately appears on two lines (a split
# shipment). Every same-keyed actual row is kept as a candidate; a golden
# row consumes exactly one; leftovers are `new_line` (BR3, informational).
#
# The previous decision (last-write-wins: keep only the final same-keyed
# row, drop the rest silently) was reversed by live evidence. On
# `inv-003-table-heavy.pdf` -- a fixture built to carry SKU-500 twice --
# the collapse manufactured three `wrong_value`s (golden line 1 compared
# against line 2's values) plus one `missing` (golden line 2 found an
# emptied index), failing the `baseline-1.0.0` gate on a document the
# adapter had extracted perfectly. The old rationale held that raising
# would false-fail ordinary multi-line-same-SKU invoices; that was right,
# but collapsing false-fails them too, just less visibly. Keeping every row
# and pairing by content false-fails neither.
#
# Within a duplicate-key group the pairing is by content affinity (most
# matching non-key columns), NOT by position -- see
# test_duplicate_match_key_rows_pair_by_content_not_position -- so BR8's
# "row reordering is not a diff" stays true inside the group as well as
# across keys.
#
# ADR-0002 R-2's "tables" bullet named this collapse as load-bearing
# masking for the pages[]-vs-top-level row doubling. That justification was
# retired by ADR-0002's own Correction 2026-09-24 (b): live 1-page and
# 3-page captures carry no `pages` key, so the doubling seam is
# unreachable. Were it ever reachable, a doubled row now shows up as
# `new_line`, which never fails the gate.
_DUP_GOLDEN: Golden = {
    "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
    "tables": {
        "line_items": {
            "match_key": "description",
            "critical": True,
            "rows": [
                {"description": "Widget A", "qty": "10", "unit_price": "50.00"},
                {"description": "Widget A", "qty": "5", "unit_price": "20.00"},
            ],
        }
    },
}


def test_duplicate_actual_match_key_keeps_every_row_instead_of_collapsing() -> None:
    # One golden Widget A line, two actual Widget A rows. The golden row
    # pairs with the row that actually matches it; the surplus row is
    # reported as new_line rather than silently overwriting the other.
    # Under the old last-write-wins this asserted "999.00" and no new_line.
    actual = _actual(
        {"invoice_number": {"value": "INV-1"}, "total": {"value": "1250.00"}},
        {
            "line_items": [
                _row("Widget A", "10", "50.00"),  # the golden line
                _row("Widget A", "10", "999.00"),  # a second, surplus line
                _row("Widget B", "2", "20.00"),
            ]
        },
    )
    v = classify(GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    up = [r for r in rows if r["column"] == "unit_price" and r["match_key"] == "Widget A"]
    assert len(up) == 1
    assert up[0]["actual"] == "50.00"
    assert up[0]["verdict"] == "match"
    # The unconsumed duplicate is surfaced, not dropped.
    new_lines = [r for r in rows if r["verdict"] == "new_line"]
    assert len(new_lines) == 1
    assert new_lines[0]["match_key"] == "Widget A"
    assert overall_gate(v) == "PASS"  # new_line never fails the gate (BR3)


def test_duplicate_match_key_on_both_sides_pairs_each_line_independently() -> None:
    # REGRESSION PIN for the `baseline-1.0.0` false FAIL on
    # inv-003-table-heavy.pdf. Golden declares the same key twice (a split
    # shipment) and the actual carries both lines, correctly extracted.
    # Every column must match: no `missing`, no `wrong_value`, gate PASS.
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "10", "50.00"), _row("Widget A", "5", "20.00")]},
    )
    v = classify(_DUP_GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    assert {r["verdict"] for r in rows} == {"match"}
    assert overall_gate(v) == "PASS"


def test_duplicate_match_key_rows_pair_by_content_not_position() -> None:
    # The same two lines, arriving in the opposite order. BR8 says row
    # reordering is not a diff; that must hold inside a duplicate-key group
    # too. Pairing the group positionally would report four wrong_values
    # here and fail the gate on a correct extraction.
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "5", "20.00"), _row("Widget A", "10", "50.00")]},
    )
    v = classify(_DUP_GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    assert {r["verdict"] for r in rows} == {"match"}
    assert overall_gate(v) == "PASS"


def test_duplicate_match_key_still_fails_on_a_genuine_regression() -> None:
    # Fail-CLOSED guard on the pairing change: content-affinity pairing must
    # not become a search for the reading that makes the run look green. One
    # of the two same-keyed lines has a wrong unit_price; that must still be
    # exactly one wrong_value and still fail the critical block.
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {"line_items": [_row("Widget A", "10", "50.00"), _row("Widget A", "5", "777.00")]},
    )
    v = classify(_DUP_GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    wrong = [r for r in rows if r["verdict"] == "wrong_value"]
    assert len(wrong) == 1
    assert wrong[0]["column"] == "unit_price"
    assert wrong[0]["expected"] == "20.00"
    assert wrong[0]["actual"] == "777.00"
    assert not any(r["verdict"] == "missing" for r in rows)
    assert overall_gate(v) == "FAIL"


def test_triplicate_actual_match_key_consumes_one_per_golden_row() -> None:
    # Three same-keyed actual rows against two golden lines: two pair, the
    # third is a single new_line. Guards the candidate list against both
    # over-consumption (a spurious missing) and under-reporting.
    actual = _actual(
        {"total": {"value": "1250.00"}},
        {
            "line_items": [
                _row("Widget A", "10", "50.00"),
                _row("Widget A", "5", "20.00"),
                _row("Widget A", "99", "99.00"),
            ]
        },
    )
    v = classify(_DUP_GOLDEN, actual)
    rows = _table(v, "line_items")["rows"]
    assert [r["verdict"] for r in rows].count("new_line") == 1
    assert not any(r["verdict"] in ("missing", "wrong_value") for r in rows)
    assert overall_gate(v) == "PASS"


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