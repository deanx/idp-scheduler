"""Input shape validation + typed ClassifierError family (T-01.1.4, TP-21, NFR N22).

A malformed golden is a loud Curator error, not a silent ``match``. A malformed
NormalizedOutput raises a typed ClassifierError too.

Malformed inputs are built as plain dicts and ``cast`` to the param type at
the call site — the whole point is to feed the validator something the static
type system would otherwise reject.
"""

from __future__ import annotations

from typing import cast

import pytest

from idp_regression.classifier import (
    ClassifierError,
    MalformedActualError,
    MalformedGoldenError,
    classify,
)
from idp_regression.classifier.types import Golden, NormalizedOutput

GOOD_GOLDEN: Golden = {
    "fields": {
        "total": {"value": "1250.00", "type": "number", "critical": True},
    }
}


def _good_actual() -> NormalizedOutput:
    return {"status": "SUCCEEDED", "fields": {"total": {"value": "1250.00"}}}


def test_non_mapping_golden_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(cast(Golden, ["not", "a", "mapping"]), _good_actual())


def test_golden_without_fields_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(cast(Golden, {"document_id": "x"}), _good_actual())


def test_golden_empty_fields_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(cast(Golden, {"fields": {}}), _good_actual())


def test_golden_field_missing_type_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(cast(Golden, {"fields": {"total": {"value": "1.00"}}}), _good_actual())


def test_golden_field_invalid_type_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(
            cast(Golden, {"fields": {"total": {"value": "1.00", "type": "currency"}}}),
            _good_actual(),
        )


def test_golden_table_missing_match_key_raises_malformed_golden() -> None:
    with pytest.raises(MalformedGoldenError):
        classify(
            cast(
                Golden,
                {
                    "fields": {"t": {"value": "1", "type": "text"}},
                    "tables": {"items": {"rows": []}},
                },
            ),
            _good_actual(),
        )


def test_non_mapping_actual_raises_malformed_actual() -> None:
    with pytest.raises(MalformedActualError):
        classify(GOOD_GOLDEN, cast(NormalizedOutput, "not a mapping"))


def test_actual_fields_non_mapping_raises_malformed_actual() -> None:
    with pytest.raises(MalformedActualError):
        classify(GOOD_GOLDEN, cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": []}))


def test_actual_field_cell_non_mapping_raises_malformed_actual() -> None:
    with pytest.raises(MalformedActualError):
        classify(
            GOOD_GOLDEN,
            cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": {"total": "1250.00"}}),
        )


def test_actual_field_cell_missing_value_raises_malformed_actual() -> None:
    with pytest.raises(MalformedActualError):
        classify(
            GOOD_GOLDEN,
            cast(
                NormalizedOutput,
                {"status": "SUCCEEDED", "fields": {"total": {"confidence": 0.9}}},
            ),
        )


def test_actual_tables_non_list_raises_malformed_actual() -> None:
    with pytest.raises(MalformedActualError):
        classify(
            GOOD_GOLDEN,
            cast(
                NormalizedOutput,
                {"status": "SUCCEEDED", "fields": {}, "tables": {"items": "notalist"}},
            ),
        )


def test_golden_table_non_mapping_row_raises_malformed_golden() -> None:
    # N22: a golden row that is a list-element (not a dict[str, str]) must raise
    # a typed MalformedGoldenError, not leak a raw AttributeError out of
    # _classify_table when it calls grow.get(key_col).
    with pytest.raises(MalformedGoldenError):
        classify(
            cast(
                Golden,
                {
                    "fields": {"t": {"value": "1", "type": "text"}},
                    "tables": {
                        "items": {
                            "match_key": "description",
                            "rows": [["Widget A", "10"]],  # list-element row, not a dict
                        }
                    },
                },
            ),
            _good_actual(),
        )


def test_actual_table_non_mapping_cell_raises_malformed_actual() -> None:
    # N22: an actual table cell that is a plain string (not a dict-with-'value')
    # must raise a typed MalformedActualError, not leak a raw AttributeError
    # when _classify_table calls acell.get("value").
    with pytest.raises(MalformedActualError):
        classify(
            GOOD_GOLDEN,
            cast(
                NormalizedOutput,
                {
                    "status": "SUCCEEDED",
                    "fields": {},
                    "tables": {
                        "items": [{"description": {"value": "Widget A"}, "qty": "10"}],
                    },
                },
            ),
        )


def test_classifier_error_is_base_of_malformed_variants() -> None:
    # N22: a typed ClassifierError family.
    assert issubclass(MalformedGoldenError, ClassifierError)
    assert issubclass(MalformedActualError, ClassifierError)


def test_malformed_golden_is_loud_not_silent_match() -> None:
    # The headline N22 guarantee: a broken golden (field missing `type`) must
    # raise, never return a verdict map of silent ``match`` entries.
    with pytest.raises(ClassifierError):
        classify(cast(Golden, {"fields": {"total": {"value": "1.00"}}}), _good_actual())


# --- TP-21 / N22: prompt-level malformation coverage ----------------------
# Field-level and table-level malformation are pinned above; these cover the
# prompt surface of the same N22 contract (typed ClassifierError, not a raw
# leak). A minimal valid golden/actual baseline with one prompt is mutated
# only on the prompt part so each test isolates the prompt malformation.


def _good_golden_with_prompt() -> Golden:
    return {
        "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        "prompts": {"q1": {"answer": "Yes", "critical": True}},
    }


def _good_actual_with_prompt() -> NormalizedOutput:
    return {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "1250.00"}},
        "prompts": {"q1": {"answer": "Yes"}},
    }


def test_golden_prompts_non_mapping_raises_malformed_golden() -> None:
    # N22: golden.prompts must be a mapping; a list is a Curator error.
    golden = _good_golden_with_prompt()
    golden["prompts"] = ["q1"]  # type: ignore[typeddict-item]
    with pytest.raises(MalformedGoldenError):
        classify(golden, _good_actual_with_prompt())


def test_golden_prompt_spec_non_mapping_raises_malformed_golden() -> None:
    # N22: a golden prompt spec must be a mapping, not a bare string.
    golden = _good_golden_with_prompt()
    golden["prompts"] = {"q1": "Yes"}  # type: ignore[dict-item]
    with pytest.raises(MalformedGoldenError):
        classify(golden, _good_actual_with_prompt())


def test_golden_prompt_missing_answer_raises_malformed_golden() -> None:
    # N22: a golden prompt without an `answer` key is malformed.
    golden = _good_golden_with_prompt()
    golden["prompts"] = {"q1": {"critical": True}}  # type: ignore[typeddict-item]
    with pytest.raises(MalformedGoldenError):
        classify(golden, _good_actual_with_prompt())


def test_golden_prompt_critical_not_bool_raises_malformed_golden() -> None:
    # N22: a golden prompt whose `critical` is not a bool is malformed.
    golden = _good_golden_with_prompt()
    golden["prompts"] = {"q1": {"answer": "Yes", "critical": "yes"}}  # type: ignore[typeddict-item]
    with pytest.raises(MalformedGoldenError):
        classify(golden, _good_actual_with_prompt())


def test_actual_prompts_non_mapping_raises_malformed_actual() -> None:
    # N22: actual.prompts must be a mapping (top-level dict check, gate.py:124).
    actual = _good_actual_with_prompt()
    actual["prompts"] = ["q1"]  # type: ignore[typeddict-item]
    with pytest.raises(MalformedActualError):
        classify(_good_golden_with_prompt(), actual)


def test_actual_prompt_cell_non_mapping_raises_malformed_actual() -> None:
    # N22: an actual prompt cell that is a plain string (not a dict-with-'answer')
    # must raise a typed MalformedActualError, not leak a raw AttributeError
    # when _classify_prompt calls acell.get("answer").
    actual = _good_actual_with_prompt()
    actual["prompts"] = {"q1": "plain string answer"}  # type: ignore[dict-item]
    with pytest.raises(MalformedActualError):
        classify(_good_golden_with_prompt(), actual)


# --- N28 (T-01.4.5, ADR-0005 Decision #8): orchestration reuses N22 -----


def test_validate_golden_structure_is_validate_golden() -> None:
    """`validate_golden_structure` (the orchestration-facing N28 alias)
    must `is` `_validate_golden` -- the SAME function object, not a copy
    -- so N28 can never silently drift from N22 as `_validate_golden`
    evolves. A copy-paste "reuse" would defeat the whole point of the
    alias and this test exists specifically to catch that mutation."""
    from idp_regression.classifier.gate import _validate_golden, validate_golden_structure

    assert validate_golden_structure is _validate_golden