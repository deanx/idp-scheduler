"""Spike port + edge-case matrix (T-01.1.5).

Ports the 11 spike tests from ``idp-regression-spike/classifier/test_compare.py``
into the new ADR-0003 contract (keyed ``tables`` map, ``FieldValue`` cells) and
adds the canonical-form / format-vs-value / criticality / new_line / missing
edge-case matrix required by the S-01.1 DoD.

Deviation from the spike: ``test_date_format_equivalent`` in the spike asserted
``match`` for ``"March 15, 2024"`` vs ``"2024-03-15"``; AC4 / EX-A1-4 / ADR-0003
format-vs-value bullet require ``wrong_format``. The ported test asserts
``wrong_format`` (the AC is authoritative over the spike).

Covers: TP-02, TP-03, TP-04, TP-06, TP-07 (EX-A1-1), TP-08 (EX-A1-2),
TP-10 (EX-A1-4), TP-11 (EX-A1-5).
"""

from __future__ import annotations

from typing import cast

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.canonical import compare_value
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
        "invoice_date": {"value": "2024-03-15", "type": "date", "critical": True},
        "total": {"value": "1250.00", "type": "number", "critical": True},
    },
    "tables": {
        "line_items": {
            "match_key": "description",
            "critical": True,
            "rows": [{"description": "Widget A", "qty": "10", "unit_price": "50.00"}],
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


def _table(verdicts: VerdictMap, name: str) -> TableVerdict:
    return cast(TableVerdict, verdicts[name])


def _row(description: str, qty: str, unit_price: str) -> dict[str, FieldValue]:
    return {
        "description": {"value": description},
        "qty": {"value": qty},
        "unit_price": {"value": unit_price},
    }


# ---------------------------------------------------------------------------
# Ported spike tests (adapted to the keyed-tables / FieldValue contract)
# ---------------------------------------------------------------------------


def test_spike_match() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
            },
            {"line_items": [_row("Widget A", "10", "50.00")]},
        ),
    )
    assert v["invoice_number"]["verdict"] == "match"
    assert overall_gate(v) == "PASS"


def test_spike_date_format_is_wrong_format_not_match() -> None:
    # Spike asserted "match"; AC4/EX-A1-4 require wrong_format. Ported corrected.
    v = classify(GOLDEN, _actual({"invoice_date": {"value": "March 15, 2024"}}))
    assert v["invoice_date"]["verdict"] == "wrong_format"


def test_spike_wrong_critical_value_fails() -> None:
    v = classify(GOLDEN, _actual({"total": {"value": "1150.00"}}))
    assert v["total"]["verdict"] == "wrong_value"
    assert overall_gate(v) == "FAIL"


def test_spike_new_field_is_informational() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
                "discount": {"value": "5.00"},
            },
            {"line_items": [_row("Widget A", "10", "50.00")]},
        ),
    )
    assert v["discount"]["verdict"] == "new_field"
    assert overall_gate(v) == "PASS"


def test_spike_wrong_format_id_punctuation() -> None:
    # same content, different punctuation -> wrong_format, not wrong_value
    assert compare_value("id", "INV 001", "INV-001") == "wrong_format"


def test_spike_wrong_format_does_not_fail_gate_when_noncritical() -> None:
    g: Golden = {"fields": {"po": {"value": "PO 12 34", "type": "id", "critical": False}}}
    v = classify(g, _actual({"po": {"value": "PO-1234"}}))
    assert v["po"]["verdict"] == "wrong_format"
    assert overall_gate(v) == "PASS"


def test_spike_missing_when_absent() -> None:
    v = classify(GOLDEN, _actual({"invoice_number": {"value": "INV-1"}}))  # date+total absent
    assert v["invoice_date"]["verdict"] == "missing"
    assert v["total"]["verdict"] == "missing"
    assert overall_gate(v) == "FAIL"


def test_spike_missing_when_empty_string() -> None:
    v = classify(GOLDEN, _actual({"total": {"value": ""}}))
    assert v["total"]["verdict"] == "missing"


def test_spike_line_item_wrong_value_fails() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
            },
            {"line_items": [_row("Widget A", "10", "99.00")]},
        ),
    )
    rows = _table(v, "line_items")["rows"]
    up = [r for r in rows if r["column"] == "unit_price"]
    assert up[0]["verdict"] == "wrong_value"
    assert overall_gate(v) == "FAIL"


def test_spike_extra_line_is_new_line_and_passes() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
            },
            {
                "line_items": [
                    _row("Widget A", "10", "50.00"),
                    _row("Widget B", "2", "20.00"),
                ]
            },
        ),
    )
    assert any(r["verdict"] == "new_line" for r in _table(v, "line_items")["rows"])
    assert overall_gate(v) == "PASS"


def test_spike_currency_formatting_is_match() -> None:
    assert compare_value("number", "1250.00", "$1,250.00") == "match"


# ---------------------------------------------------------------------------
# Edge-case matrix: canonical forms across number/date/id/text
# ---------------------------------------------------------------------------


def test_matrix_number_canonical() -> None:
    assert compare_value("number", "1250.00", "1250") == "match"
    assert compare_value("number", "1,250.00", "1250.00") == "match"
    assert compare_value("number", "1250.00", "1250.50") == "wrong_value"


def test_matrix_date_canonical() -> None:
    assert compare_value("date", "2024-03-15", "2024-03-15") == "match"
    assert compare_value("date", "2024-03-15", "03/15/2024") == "wrong_format"
    assert compare_value("date", "2024-03-15", "2024-03-16") == "wrong_value"


def test_matrix_id_canonical() -> None:
    assert compare_value("id", "INV-1", "INV-1") == "match"
    assert compare_value("id", "INV 001", "INV-001") == "wrong_format"
    assert compare_value("id", "INV-1", "INV-2") == "wrong_value"


def test_matrix_text_canonical() -> None:
    assert compare_value("text", "Acme Corp", "Acme Corp") == "match"
    # whitespace-only difference -> wrong_format
    assert compare_value("text", "Acme Corp", "Acme  Corp") == "wrong_format"
    # genuinely different -> wrong_value
    assert compare_value("text", "Acme Corp", "Beta LLC") == "wrong_value"


# ---------------------------------------------------------------------------
# Criticality gate edge cases
# ---------------------------------------------------------------------------


def test_matrix_non_critical_difference_passes() -> None:
    g: Golden = {
        "fields": {"note": {"value": "hello", "type": "text", "critical": False}}
    }
    v = classify(g, _actual({"note": {"value": "goodbye"}}))
    assert v["note"]["verdict"] == "wrong_value"
    assert overall_gate(v) == "PASS"


def test_matrix_critical_missing_fails() -> None:
    v = classify(GOLDEN, _actual({"invoice_number": {"value": "INV-1"}}))
    assert overall_gate(v) == "FAIL"


# ---------------------------------------------------------------------------
# Line-item edge cases: new_line on unmatched actual, missing on unmatched golden
# ---------------------------------------------------------------------------


def test_matrix_new_line_unmatched_actual() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
            },
            {
                "line_items": [
                    _row("Widget A", "10", "50.00"),
                    _row("Widget Z", "9", "9.00"),
                ]
            },
        ),
    )
    rows = _table(v, "line_items")["rows"]
    assert any(r["verdict"] == "new_line" and r["match_key"] == "Widget Z" for r in rows)
    assert overall_gate(v) == "PASS"


def test_matrix_missing_unmatched_golden() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
            },
            {"line_items": []},
        ),
    )
    rows = _table(v, "line_items")["rows"]
    assert rows and all(r["verdict"] == "missing" for r in rows)
    assert overall_gate(v) == "FAIL"


# ---------------------------------------------------------------------------
# EX-A1-1 (TP-07): 5 invoices, all fields correct -> 5 gates PASS, all match
# ---------------------------------------------------------------------------


def test_ex_a1_1_five_invoices_all_match() -> None:
    for i in range(5):
        golden: Golden = {
            "fields": {
                "invoice_number": {
                    "value": f"INV-{i}",
                    "type": "id",
                    "critical": True,
                },
                "invoice_date": {"value": "2024-03-15", "type": "date", "critical": True},
                "total": {"value": f"{1250.00 + i:.2f}", "type": "number", "critical": True},
            }
        }
        actual = _actual(
            {
                "invoice_number": {"value": f"INV-{i}"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": f"{1250.00 + i:.2f}"},
            }
        )
        v = classify(golden, actual)
        assert all(entry["verdict"] == "match" for entry in v.values()), f"doc {i}"
        assert overall_gate(v) == "PASS", f"doc {i}"


# ---------------------------------------------------------------------------
# EX-A1-5 (TP-11): new_field discount, gate unaffected, stays PASS
# ---------------------------------------------------------------------------


def test_ex_a1_5_new_field_discount_gate_unaffected() -> None:
    v = classify(
        GOLDEN,
        _actual(
            {
                "invoice_number": {"value": "INV-1"},
                "invoice_date": {"value": "2024-03-15"},
                "total": {"value": "1250.00"},
                "discount": {"value": "5.00"},
            },
            {"line_items": [_row("Widget A", "10", "50.00")]},
        ),
    )
    assert v["discount"]["verdict"] == "new_field"
    assert overall_gate(v) == "PASS"


# ---------------------------------------------------------------------------
# Prompts compare like text fields (ADR-0003)
# ---------------------------------------------------------------------------


def test_prompts_compare_like_text_fields() -> None:
    golden: Golden = {
        "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        "prompts": {"vendor_name": {"answer": "Acme Corp", "critical": False}},
    }
    actual = _actual(
        {"total": {"value": "1250.00"}},
    )
    actual["prompts"] = {"vendor_name": {"answer": "Acme Corp", "confidence": 0.88}}
    v = classify(golden, actual)
    assert v["vendor_name"]["verdict"] == "match"
    assert v["vendor_name"]["type"] == "text"
    assert overall_gate(v) == "PASS"