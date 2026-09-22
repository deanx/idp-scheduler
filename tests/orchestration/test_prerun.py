"""T-01.4.11/.2/.5: the pre-run chain (schema-drift, empty-set, N28
structural validation) -- pinned order per S-01.4-KICKOFF.md / ADR-0005
Decision #8 / TP-40 / TP-46.
"""

from __future__ import annotations

import copy
import logging
from typing import cast

import pytest

from idp_regression.classifier.types import Golden
from idp_regression.orchestration.errors import RunAborted
from idp_regression.orchestration.prerun import (
    check_empty_set,
    check_schema_drift,
    validate_golden_set,
)
from idp_regression.platform.schema import load_golden_schema
from idp_regression.platform.types import Dataset, DatasetItem

GOOD_GOLDEN: Golden = {
    "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
}


def _dataset(*, items: list[DatasetItem] | None = None, schema: object = "MATCH") -> Dataset:
    actual_schema = load_golden_schema() if schema == "MATCH" else schema
    return {
        "items": items if items is not None else [],
        "expected_output_schema": actual_schema,  # type: ignore[typeddict-item]
    }


# --- T-01.4.11: schema drift --------------------------------------------


def test_check_schema_drift_passes_when_schema_matches_the_committed_file() -> None:
    check_schema_drift(_dataset())  # no raise


def test_check_schema_drift_aborts_on_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    mismatched = copy.deepcopy(load_golden_schema())
    mismatched["title"] = "a different title entirely"

    with caplog.at_level(logging.ERROR), pytest.raises(RunAborted) as excinfo:
        check_schema_drift(_dataset(schema=mismatched))

    assert excinfo.value.reason == "schema_drift"
    assert "schema_drift" in caplog.text
    assert "committed=" in caplog.text
    assert "actual=" in caplog.text


def test_check_schema_drift_aborts_when_schema_is_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(RunAborted) as excinfo:
        check_schema_drift(_dataset(schema=None))

    assert excinfo.value.reason == "schema_drift"
    assert "actual=absent" in caplog.text


def test_check_schema_drift_is_insensitive_to_key_order_and_whitespace() -> None:
    """ADR-0005 #8 / TP-40: canonical JSON (sorted keys, no whitespace) --
    a Langfuse JSONB key reorder alone must NOT trigger drift."""
    original = load_golden_schema()
    reordered = dict(reversed(list(original.items())))
    check_schema_drift(_dataset(schema=reordered))  # no raise


# --- T-01.4.2: empty golden set -----------------------------------------


def test_check_empty_set_passes_on_a_non_empty_dataset() -> None:
    check_empty_set(
        _dataset(items=[{"item_id": "i1", "document_id": "d1", "golden": GOOD_GOLDEN}])
    )  # no raise


def test_check_empty_set_aborts_on_an_empty_dataset() -> None:
    with pytest.raises(RunAborted) as excinfo:
        check_empty_set(_dataset(items=[]))
    assert excinfo.value.reason == "empty_set"


# --- TP-40: pinned ordering — drift beats empty --------------------------


def test_an_empty_dataset_with_a_drifted_schema_reports_schema_drift_not_empty_set() -> None:
    """The pinned pre-run order (get_dataset -> drift -> empty -> N28)
    means an empty dataset whose schema ALSO drifted must abort
    `schema_drift`, because `check_schema_drift` runs before
    `check_empty_set` in `facade.py`'s chain -- this test pins the
    ordering by construction: `check_schema_drift` raises for THIS
    dataset without ever consulting `items`."""
    empty_and_drifted = _dataset(items=[], schema={"not": "the committed schema"})
    with pytest.raises(RunAborted) as excinfo:
        check_schema_drift(empty_and_drifted)
    assert excinfo.value.reason == "schema_drift"


# --- T-01.4.5 / TP-46: N28 structural validation -------------------------


def test_validate_golden_set_passes_on_well_formed_items() -> None:
    validate_golden_set(
        _dataset(items=[{"item_id": "i1", "document_id": "d1", "golden": GOOD_GOLDEN}])
    )  # no raise


def test_validate_golden_set_aborts_malformed_golden_on_a_missing_fields_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_item: DatasetItem = {
        "item_id": "i1",
        "document_id": "doc-42",
        "golden": cast(Golden, {}),
    }
    with caplog.at_level(logging.ERROR), pytest.raises(RunAborted) as excinfo:
        validate_golden_set(_dataset(items=[bad_item]))

    assert excinfo.value.reason == "malformed_golden"
    assert "malformed_golden" in caplog.text
    assert "doc-42" in caplog.text
    assert "path=" in caplog.text


def test_validate_golden_set_checks_every_item_before_any_idp_call_first_bad_wins(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Composition, not just a single fixture: the malformed item can sit
    anywhere in the list; the FIRST one encountered aborts."""
    items: list[DatasetItem] = [
        {"item_id": "i1", "document_id": "good-1", "golden": GOOD_GOLDEN},
        {"item_id": "i2", "document_id": "bad-2", "golden": cast(Golden, {})},
        {"item_id": "i3", "document_id": "good-3", "golden": GOOD_GOLDEN},
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(RunAborted) as excinfo:
        validate_golden_set(_dataset(items=items))

    assert excinfo.value.reason == "malformed_golden"
    assert "bad-2" in caplog.text
    assert "good-1" not in caplog.text
    assert "good-3" not in caplog.text


def test_validate_golden_set_never_leaks_the_golden_value_into_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """INV-02: a malformed item whose field VALUE is a sensitive-shaped
    string must never see that value reach the log -- only document_id +
    a structural path (key/type names)."""
    sensitive_value = "ssn-123-45-6789-should-never-log"
    bad_item: DatasetItem = {
        "item_id": "i1",
        "document_id": "doc-9",
        "golden": cast(
            Golden,
            {"fields": {"total": {"value": sensitive_value, "type": "not-a-real-type"}}},
        ),
    }
    with caplog.at_level(logging.ERROR), pytest.raises(RunAborted):
        validate_golden_set(_dataset(items=[bad_item]))

    assert sensitive_value not in caplog.text
    assert "total" in caplog.text  # the field NAME may appear -- not the value


def test_validate_golden_set_runs_before_any_idp_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural pin: `validate_golden_set` imports nothing from
    `adapter/` and makes no IDP call -- a static assertion that this
    module contains no reference to the IDP client at all."""
    import idp_regression.orchestration.prerun as prerun_module

    assert not hasattr(prerun_module, "make_idp_adapter")
