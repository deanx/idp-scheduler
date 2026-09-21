"""Tests for overall_gate() (T-01.1.2).

BR2 criticality governs the gate; BR3 new_field/new_line never fail it.

FO-5: overall_gate() is public API taking a caller-supplied VerdictMap. An
unrecognised verdict string (top-level or inside a table's row detail) must
raise, not silently fall through to PASS.
"""

from __future__ import annotations

from typing import cast, get_args

import pytest

from idp_regression.classifier import MalformedActualError, classify, overall_gate
from idp_regression.classifier.gate import _VALID_VERDICTS
from idp_regression.classifier.types import (
    FieldValue,
    Golden,
    NormalizedOutput,
    RowVerdict,
    TableVerdict,
    Verdict,
    VerdictLiteral,
    VerdictMap,
)

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


# --- FO-5: unrecognised verdict must raise, not fall through to PASS -------


def _verdict(verdict: str, *, critical: bool) -> Verdict:
    return cast(
        Verdict,
        {
            "verdict": verdict,
            "expected": None,
            "actual": None,
            "confidence": None,
            "critical": critical,
            "type": None,
        },
    )


def _row(verdict: str) -> RowVerdict:
    return cast(
        RowVerdict,
        {
            "match_key": "k1",
            "column": "amount",
            "verdict": verdict,
            "expected": None,
            "actual": None,
            "confidence": None,
        },
    )


def test_unknown_top_level_verdict_raises_instead_of_passing() -> None:
    # FO-5: a cast-in unknown verdict must not silently fall through to PASS.
    verdicts: VerdictMap = {"total": _verdict("unknown_verdict", critical=True)}
    with pytest.raises(MalformedActualError):
        overall_gate(verdicts)


def test_unknown_row_verdict_inside_detail_raises_instead_of_passing() -> None:
    # FO-5: same defect, but one layer down inside a table's detail rows.
    table: TableVerdict = cast(
        TableVerdict,
        {
            "verdict": "detail",
            "critical": True,
            "type": None,
            "rows": [_row("unknown_verdict")],
        },
    )
    verdicts: VerdictMap = {"line_items": table}
    with pytest.raises(MalformedActualError):
        overall_gate(verdicts)


@pytest.mark.parametrize("verdict", get_args(VerdictLiteral))
def test_all_legitimate_top_level_verdicts_still_gate_correctly(verdict: str) -> None:
    # BR2/BR3 semantics must stay untouched for every real verdict, critical.
    verdicts: VerdictMap = {"field": _verdict(verdict, critical=True)}
    expected = "FAIL" if verdict in ("missing", "wrong_value") else "PASS"
    assert overall_gate(verdicts) == expected


@pytest.mark.parametrize("verdict", get_args(VerdictLiteral))
def test_all_legitimate_top_level_verdicts_non_critical_never_fail(verdict: str) -> None:
    verdicts: VerdictMap = {"field": _verdict(verdict, critical=False)}
    assert overall_gate(verdicts) == "PASS"


def test_valid_verdicts_is_derived_from_the_literal_not_a_hand_written_tuple() -> None:
    # M13-analogue (REG-09 pattern): a production pin, not a filter-semantics
    # pin. Passes today regardless of whether _VALID_VERDICTS is derived or
    # hand-written -- it only fires the moment VerdictLiteral gains a member
    # production hasn't been updated for, naming the drift in a red test
    # instead of letting a real 7th verdict fall through to MalformedActualError
    # (or worse, PASS) at runtime.
    assert frozenset(get_args(VerdictLiteral)) == _VALID_VERDICTS


@pytest.mark.parametrize("verdict", get_args(VerdictLiteral))
def test_all_legitimate_row_verdicts_inside_critical_detail_gate_correctly(
    verdict: str,
) -> None:
    # BR2/BR3 inside a critical table block: same semantics per row.
    table: TableVerdict = cast(
        TableVerdict,
        {
            "verdict": "detail",
            "critical": True,
            "type": None,
            "rows": [_row(verdict)],
        },
    )
    verdicts: VerdictMap = {"line_items": table}
    expected = "FAIL" if verdict in ("missing", "wrong_value") else "PASS"
    assert overall_gate(verdicts) == expected