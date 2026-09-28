"""Tests for overall_gate() (T-01.1.2).

BR2 criticality governs the gate; BR3 new_field/new_line never fail it.

FO-5: overall_gate() is public API taking a caller-supplied VerdictMap. An
unrecognised verdict string (top-level or inside a table's row detail) must
raise, not silently fall through to PASS.
"""

from __future__ import annotations

from typing import cast, get_args

import pytest

from idp_regression.classifier import (
    MalformedActualError,
    MalformedGoldenError,
    classify,
    overall_gate,
)
from idp_regression.classifier.gate import _VALID_VERDICTS
from idp_regression.classifier.types import (
    FieldValue,
    Golden,
    NormalizedOutput,
    RowVerdict,
    RowVerdictLiteral,
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


# The ROW verdicts (6): `new_table` is a table-level verdict and a row
# carrying it is refused (Wave C S-01.1 re-stamp F-2), so it is not a
# "legitimate row verdict" -- this parametrisation used to include it and
# pinned the defect as correct.
@pytest.mark.parametrize("verdict", get_args(RowVerdictLiteral))
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

# --- DEBT-80: `format_critical`, the per-field opt-in on wrong_format ------
#
# BR3's default is unchanged and deliberate: a value that is semantically
# right but formatted differently is not a regression, and `wrong_format`
# stays informational. `format_critical: true` is the declared exception,
# for a field whose FORMAT is itself the contract.
#
# Why it exists, recorded because the default looked obviously right until a
# live run proved otherwise: on 2026-09-24 the TEST-PLAN Phase 7 regression
# run (action 1.1.0, the `invoice_date` prompt stripped of its ISO-8601
# instruction) changed exactly one cell across 45 --
# `inv-002-format-variance.pdf`'s `invoice_date`, `2026-01-22` ->
# `22/01/2026` -- and the tool returned `exit_code=0`. `_format_date` parses
# both to the same calendar date, so the verdict was `wrong_format`, and BR3
# does not fail the gate on it even when the field is `critical`. A real
# prompt regression, on the exact field the fixture was built to regress,
# produced a GREEN build. That is the fail-open class CLAUDE.md's ## Rigor
# section names as the worst this system can produce.
_FMT_GOLDEN: Golden = {
    "fields": {
        "invoice_date": {
            "value": "2026-01-22",
            "type": "date",
            "critical": True,
            "format_critical": True,
        },
    },
}


def test_wrong_format_fails_the_gate_when_the_field_is_format_critical() -> None:
    # The live 1.1.0 regression, reproduced as a unit test.
    v = classify(_FMT_GOLDEN, _actual({"invoice_date": {"value": "22/01/2026"}}))
    assert v["invoice_date"]["verdict"] == "wrong_format"
    assert v["invoice_date"]["format_critical"] is True
    assert overall_gate(v) == "FAIL"


def test_wrong_format_still_passes_when_format_critical_is_absent() -> None:
    # BR3's default is untouched: the SAME values, on a field that does not
    # opt in, still PASS. Guards against the fix being applied globally.
    golden: Golden = {
        "fields": {"invoice_date": {"value": "2026-01-22", "type": "date", "critical": True}}
    }
    v = classify(golden, _actual({"invoice_date": {"value": "22/01/2026"}}))
    assert v["invoice_date"]["verdict"] == "wrong_format"
    assert v["invoice_date"]["format_critical"] is False
    assert overall_gate(v) == "PASS"


def test_format_critical_does_not_fail_the_gate_on_a_match() -> None:
    # The opt-in must gate on the VERDICT, not merely on the flag being set.
    v = classify(_FMT_GOLDEN, _actual({"invoice_date": {"value": "2026-01-22"}}))
    assert v["invoice_date"]["verdict"] == "match"
    assert overall_gate(v) == "PASS"


def test_format_critical_is_independent_of_critical() -> None:
    # A format-critical field that is NOT `critical` still fails on
    # wrong_format: the two flags gate different verdicts and neither
    # implies the other.
    golden: Golden = {
        "fields": {
            "invoice_date": {
                "value": "2026-01-22",
                "type": "date",
                "critical": False,
                "format_critical": True,
            }
        }
    }
    v = classify(golden, _actual({"invoice_date": {"value": "22/01/2026"}}))
    assert overall_gate(v) == "FAIL"


def test_overall_gate_tolerates_a_verdict_map_without_format_critical() -> None:
    # overall_gate is public API over a caller-supplied VerdictMap. A map
    # built before this key existed (or by an external caller) must keep the
    # old behaviour, not raise KeyError -- hence `.get`, not `[...]`.
    legacy: VerdictMap = {
        "invoice_date": cast(
            Verdict,
            {
                "verdict": "wrong_format",
                "expected": "2026-01-22",
                "actual": "22/01/2026",
                "confidence": 0.99,
                "critical": True,
                "type": "date",
            },
        )
    }
    assert overall_gate(legacy) == "PASS"


def test_non_bool_format_critical_is_a_malformed_golden() -> None:
    golden: Golden = {
        "fields": {
            "invoice_date": {
                "value": "2026-01-22",
                "type": "date",
                "format_critical": cast(bool, "yes"),
            }
        }
    }
    with pytest.raises(MalformedGoldenError, match="format_critical must be bool"):
        classify(golden, _actual({"invoice_date": {"value": "2026-01-22"}}))


def test_a_new_table_row_inside_a_detail_block_is_refused() -> None:
    """Wave C S-01.1 re-stamp F-2: `new_table` is a table-level verdict; a
    ROW carrying it is malformed. The row check used the 7-verdict set, so
    a caller-supplied `new_table` row in a CRITICAL block returned PASS."""
    from idp_regression.classifier.gate import overall_gate
    from idp_regression.classifier.types import MalformedActualError

    verdicts = {"line_items": {"verdict": "detail", "critical": True, "rows": [
        {"match_key": "A", "column": "amount", "verdict": "new_table"},
    ]}}
    with pytest.raises(MalformedActualError):
        overall_gate(verdicts)  # type: ignore[arg-type]


def _table_golden(types: object) -> Golden:
    return cast(Golden, {
        "fields": {"total": {"value": "1", "type": "number", "critical": False}},
        "tables": {"line_items": {"match_key": "sku", "critical": True, "types": types,
                                  "rows": [{"sku": "A", "amount": "1250.00"}]}},
    })


def test_an_unknown_declared_column_type_is_refused() -> None:
    """Gate F-5: "integer" used to degrade silently to text."""
    from idp_regression.classifier.types import MalformedGoldenError

    actual = cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": {}, "tables": {}})
    with pytest.raises(MalformedGoldenError, match="types"):
        classify(_table_golden({"amount": "integer"}), actual)


def test_row_pairing_uses_the_declared_column_type() -> None:
    """Gate F-8 (M12): two actual rows share the key. Only a type-aware
    affinity sees that `1,250.00` IS `1250.00` as a number and pairs it."""
    actual = cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": {}, "tables": {
        "line_items": [
            {"sku": {"value": "A"}, "amount": {"value": "999"}},
            {"sku": {"value": "A"}, "amount": {"value": "1,250.00"}},
        ]}})
    verdicts = classify(_table_golden({"amount": "number"}), actual)
    paired = [r for r in verdicts["line_items"]["rows"] if r["column"] == "amount"]  # type: ignore[typeddict-item]
    assert paired and paired[0]["verdict"] in ("match", "wrong_format")


def test_a_new_table_entry_never_gates() -> None:
    """Gate F-6 (M08): a table the golden does not have is informational."""
    golden = cast(Golden, {"fields": {"total": {"value": "1", "type": "number",
                                                "critical": True}}})
    actual = cast(NormalizedOutput, {"status": "SUCCEEDED",
                                     "fields": {"total": {"value": "1"}},
                                     "tables": {"freight": [{"a": {"value": "x"}}]}})
    verdicts = classify(golden, actual)
    assert verdicts["freight"]["verdict"] == "new_table"
    assert verdicts["freight"]["critical"] is False
