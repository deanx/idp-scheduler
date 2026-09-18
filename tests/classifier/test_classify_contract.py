"""Contract test CT-02 (T-01.1.6).

Pins the ``Verdict`` TypedDict, the six verdict literals, and the verdict-key
union (golden fields ∪ actual fields ∪ table names ∪ prompt keys). Drift on
either the producer (classify) or a consumer (gate / platform score write)
breaks this test even when the other is mocked.
"""

from __future__ import annotations

import inspect
from typing import cast, get_args, get_type_hints

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.types import (
    FIELD_TYPES,
    RowVerdict,
    TableVerdict,
    Verdict,
    VerdictLiteral,
)

SIX_VERDICTS = {"match", "missing", "wrong_value", "wrong_format", "new_field", "new_line"}
FOUR_TYPES = {"number", "date", "id", "text"}


def test_verdict_literal_is_exactly_the_six_glossary_verdicts() -> None:
    assert set(get_args(VerdictLiteral)) == SIX_VERDICTS


def test_verdict_typeddict_has_the_contract_keys() -> None:
    # ADR-0003 / DATA-MODEL-01 §3 / CT-02 shape.
    hints = get_type_hints(Verdict)
    assert set(hints) == {"verdict", "expected", "actual", "confidence", "critical", "type"}


def test_verdict_value_field_is_the_six_literal_union() -> None:
    hints = get_type_hints(Verdict)
    assert set(get_args(hints["verdict"])) == SIX_VERDICTS


def test_row_verdict_uses_the_same_six_literal_union() -> None:
    hints = get_type_hints(RowVerdict)
    assert set(get_args(hints["verdict"])) == SIX_VERDICTS


def test_row_verdict_has_the_contract_keys() -> None:
    # Pin the row sub-verdict contract (DATA-MODEL-01 §3). NB: the key is
    # ``column`` here, while DATA-MODEL-01 §3 still says ``field`` — that drift
    # is logged as doc-debt for Soneca; do NOT edit DATA-MODEL-01 from this test.
    hints = get_type_hints(RowVerdict)
    assert set(hints) == {
        "match_key",
        "column",
        "verdict",
        "expected",
        "actual",
        "confidence",
    }


def test_table_verdict_is_a_detail_container_with_rows() -> None:
    hints = get_type_hints(TableVerdict)
    assert set(hints) == {"verdict", "critical", "type", "rows"}
    assert set(get_args(hints["verdict"])) == {"detail"}
    assert hints["type"] is type(None)


def test_field_types_are_exactly_four() -> None:
    # The four field types drive per-type canonical comparison (ADR-0003).
    assert FIELD_TYPES == FOUR_TYPES


def _golden() -> dict[str, object]:
    return {
        "fields": {
            "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
            "total": {"value": "1250.00", "type": "number", "critical": True},
        },
        "tables": {
            "line_items": {
                "match_key": "description",
                "critical": True,
                "rows": [{"description": "Widget A", "qty": "10"}],
            }
        },
        "prompts": {"vendor_name": {"answer": "Acme Corp", "critical": False}},
    }


def _actual() -> dict[str, object]:
    return {
        "status": "SUCCEEDED",
        "fields": {
            "invoice_number": {"value": "INV-1"},
            "total": {"value": "1250.00"},
            "discount": {"value": "5.00"},  # actual-only -> new_field
        },
        "tables": {
            "line_items": [
                {"description": {"value": "Widget A"}, "qty": {"value": "10"}},
                {"description": {"value": "Widget Z"}, "qty": {"value": "9"}},  # new_line
            ]
        },
        "prompts": {"vendor_name": {"answer": "Acme Corp"}},
    }


def test_verdict_keys_are_golden_fields_union_actual_fields_union_tables_union_prompts() -> None:
    v = classify(_golden(), _actual())  # type: ignore[arg-type]
    expected_keys = {"invoice_number", "total", "discount", "line_items", "vendor_name"}
    assert set(v) == expected_keys


def test_every_leaf_verdict_is_one_of_the_six_literals() -> None:
    v = classify(_golden(), _actual())  # type: ignore[arg-type]
    for _key, entry in v.items():
        if entry["verdict"] == "detail":
            # table container: every row sub-verdict is one of the six
            assert "rows" in entry
            for r in entry["rows"]:
                assert r["verdict"] in SIX_VERDICTS
        else:
            assert entry["verdict"] in SIX_VERDICTS


def test_critical_is_echoed_from_golden_and_false_for_new() -> None:
    v = classify(_golden(), _actual())  # type: ignore[arg-type]
    assert v["invoice_number"]["critical"] is True  # echoed from golden
    assert v["discount"]["critical"] is False  # new_field -> False
    # new_line row -> critical False by definition (BR3)
    table = cast(TableVerdict, v["line_items"])
    new_lines = [r for r in table["rows"] if r["verdict"] == "new_line"]
    assert new_lines  # Widget Z
    # table container carries the golden block critical
    assert table["critical"] is True


def test_overall_gate_is_pure_function_of_verdicts() -> None:
    # ADR-0003 API contract: re-running overall_gate on a stored map is stable.
    v = classify(_golden(), _actual())  # type: ignore[arg-type]
    assert overall_gate(v) == overall_gate(dict(v))


def test_classify_signature_is_pure_contract() -> None:
    # The public contract: classify(golden, actual) -> dict[str, Verdict | TableVerdict]
    sig = inspect.signature(classify)
    assert list(sig.parameters) == ["golden", "actual"]
    assert sig.return_annotation is not inspect.Signature.empty