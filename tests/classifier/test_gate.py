"""Tests for overall_gate() (T-01.1.2).

BR2 criticality governs the gate; BR3 new_field/new_line never fail it.
"""

from __future__ import annotations

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.types import FieldValue, Golden, NormalizedOutput

GOLDEN: Golden = {
    "fields": {
        "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
        "invoice_date": {"value": "2024-03-15", "type": "date", "critical": True},
        "total": {"value": "1250.00", "type": "number", "critical": True},
    },
}


def _actual(fields: dict[str, FieldValue]) -> NormalizedOutput:
    return {"status": "SUCCEEDED", "fields": fields}


def test_all_match_gate_passes() -> None:
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": "1250.00"},
        }
    )
    assert overall_gate(classify(GOLDEN, actual)) == "PASS"


def test_critical_missing_fails_gate() -> None:
    # BR2: missing on a critical field -> FAIL.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": ""},
        }
    )
    assert overall_gate(classify(GOLDEN, actual)) == "FAIL"


def test_critical_wrong_value_fails_gate() -> None:
    # BR2: wrong_value on a critical field -> FAIL.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": "1150.00"},
        }
    )
    assert overall_gate(classify(GOLDEN, actual)) == "FAIL"


def test_wrong_format_on_critical_does_not_fail_gate() -> None:
    # AC4: wrong_format never fails the gate, even on a critical field.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "March 15, 2024"},  # wrong_format
            "total": {"value": "1250.00"},
        }
    )
    assert overall_gate(classify(GOLDEN, actual)) == "PASS"


def test_new_field_never_fails_gate() -> None:
    # BR3: new_field is informational; gate stays PASS if no critical miss.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": "1250.00"},
            "discount": {"value": "5.00"},
        }
    )
    v = classify(GOLDEN, actual)
    assert v["discount"]["verdict"] == "new_field"
    assert overall_gate(v) == "PASS"


def test_non_critical_missing_passes_gate() -> None:
    # BR2: a missing on a NON-critical field does not fail the gate.
    golden: Golden = {
        "fields": {
            "note": {"value": "hello", "type": "text", "critical": False},
            "total": {"value": "1250.00", "type": "number", "critical": True},
        }
    }
    actual = _actual({"total": {"value": "1250.00"}})  # note missing, non-critical
    assert overall_gate(classify(golden, actual)) == "PASS"


def test_non_critical_wrong_value_passes_gate() -> None:
    golden: Golden = {
        "fields": {
            "note": {"value": "hello", "type": "text", "critical": False},
        }
    }
    actual = _actual({"note": {"value": "goodbye"}})
    v = classify(golden, actual)
    assert v["note"]["verdict"] == "wrong_value"
    assert overall_gate(v) == "PASS"


def test_gate_is_pure_function_of_verdicts() -> None:
    # ADR-0003 API contract: overall_gate is a pure function of the verdict map.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": "1250.00"},
        }
    )
    v = classify(GOLDEN, actual)
    assert overall_gate(v) == overall_gate(dict(v))