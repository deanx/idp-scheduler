"""Unit tests for the field-level classifier (T-01.1.1).

Covers TP-02 (all-match), TP-03 (critical missing), TP-04 (format-only date),
TP-06 (new_field informational), TP-08 (EX-A1-2 critical total empty),
TP-10 (EX-A1-4 date wrong_format), TP-11 (EX-A1-5 new_field discount).
"""

from __future__ import annotations

from idp_regression.classifier import classify
from idp_regression.classifier.types import FieldValue, Golden, NormalizedOutput

GOLDEN: Golden = {
    "document_id": "invoice-007.pdf",
    "fields": {
        "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
        "invoice_date": {"value": "2024-03-15", "type": "date", "critical": True},
        "total": {"value": "1250.00", "type": "number", "critical": True},
    },
}


def _actual(fields: dict[str, FieldValue]) -> NormalizedOutput:
    return {"status": "SUCCEEDED", "fields": fields}


def test_tp02_all_match_every_field_match() -> None:
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1", "confidence": 0.99},
            "invoice_date": {"value": "2024-03-15", "confidence": 0.90},
            "total": {"value": "1250.00", "confidence": 0.80},
        }
    )
    verdicts = classify(GOLDEN, actual)
    assert verdicts["invoice_number"]["verdict"] == "match"
    assert verdicts["invoice_date"]["verdict"] == "match"
    assert verdicts["total"]["verdict"] == "match"
    # confidence is carried from actual; critical echoed from golden; type echoed.
    assert verdicts["total"]["confidence"] == 0.80
    assert verdicts["total"]["critical"] is True
    assert verdicts["total"]["type"] == "number"


def test_tp03_critical_missing_field_is_missing() -> None:
    # IDP returns empty for a critical field -> missing (TP-03 / EX-A1-2).
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": ""},
        }
    )
    verdicts = classify(GOLDEN, actual)
    assert verdicts["total"]["verdict"] == "missing"
    assert verdicts["total"]["actual"] is None
    assert verdicts["total"]["expected"] == "1250.00"
    assert verdicts["total"]["critical"] is True


def test_tp08_critical_total_empty_is_missing() -> None:
    # EX-A1-2: one invoice total empty, critical: true -> missing.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": ""},
        }
    )
    verdicts = classify(GOLDEN, actual)
    assert verdicts["total"]["verdict"] == "missing"


def test_tp04_tp10_date_format_only_is_wrong_format() -> None:
    # AC4 / EX-A1-4: "March 15, 2024" vs golden "2024-03-15" (type date)
    # -> wrong_format, NOT wrong_value and NOT match.
    actual = _actual({"invoice_date": {"value": "March 15, 2024"}})
    verdicts = classify(GOLDEN, actual)
    assert verdicts["invoice_date"]["verdict"] == "wrong_format"


def test_date_genuinely_different_is_wrong_value() -> None:
    actual = _actual({"invoice_date": {"value": "March 16, 2024"}})
    verdicts = classify(GOLDEN, actual)
    assert verdicts["invoice_date"]["verdict"] == "wrong_value"


def test_tp06_tp11_new_field_is_informational() -> None:
    # AC6 / EX-A1-5: actual has `discount` absent from golden -> new_field.
    actual = _actual(
        {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2024-03-15"},
            "total": {"value": "1250.00"},
            "discount": {"value": "5.00", "confidence": 0.70},
        }
    )
    verdicts = classify(GOLDEN, actual)
    assert verdicts["discount"]["verdict"] == "new_field"
    assert verdicts["discount"]["expected"] is None
    assert verdicts["discount"]["actual"] == "5.00"
    assert verdicts["discount"]["critical"] is False
    assert verdicts["discount"]["type"] is None
    assert verdicts["discount"]["confidence"] == 0.70


def test_number_currency_formatting_is_match() -> None:
    # ADR-0003: 1250.00 == $1,250.00 (number canonical = numeric).
    actual = _actual({"total": {"value": "$1,250.00"}})
    verdicts = classify(GOLDEN, actual)
    assert verdicts["total"]["verdict"] == "match"


def test_id_punctuation_difference_is_wrong_format() -> None:
    # ADR-0003: "INV 001" vs "INV-001" (id) -> wrong_format (same content,
    # different punctuation).
    golden: Golden = {"fields": {"po": {"value": "INV 001", "type": "id", "critical": False}}}
    actual = _actual({"po": {"value": "INV-001"}})
    verdicts = classify(golden, actual)
    assert verdicts["po"]["verdict"] == "wrong_format"


def test_wrong_value_when_content_genuinely_differs() -> None:
    actual = _actual({"total": {"value": "1150.00"}})
    verdicts = classify(GOLDEN, actual)
    assert verdicts["total"]["verdict"] == "wrong_value"


def test_missing_when_field_absent_from_actual() -> None:
    actual = _actual({"invoice_number": {"value": "INV-1"}})
    verdicts = classify(GOLDEN, actual)
    assert verdicts["invoice_date"]["verdict"] == "missing"
    assert verdicts["total"]["verdict"] == "missing"