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


def test_classifier_error_is_base_of_malformed_variants() -> None:
    # N22: a typed ClassifierError family.
    assert issubclass(MalformedGoldenError, ClassifierError)
    assert issubclass(MalformedActualError, ClassifierError)


def test_malformed_golden_is_loud_not_silent_match() -> None:
    # The headline N22 guarantee: a broken golden (field missing `type`) must
    # raise, never return a verdict map of silent ``match`` entries.
    with pytest.raises(ClassifierError):
        classify(cast(Golden, {"fields": {"total": {"value": "1.00"}}}), _good_actual())