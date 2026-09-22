"""Contract test CT-01 (T-01.2.6).

Pins the ``NormalizedOutput``/``FieldValue``/``PromptValue`` shape that
``normalize()`` emits, against the REAL captured IDP response
(``tests/fixtures/live/seed-001-clean.raw.json``, commit ``0c82a02``) —
re-pointed here 2026-09-22 (REG-11 fix, ADR-0002 A11) from the synthetic
``pages[]`` fixture the code was originally hand-authored against, which is
exactly why 886 tests passed against a contract the live API does not
implement. Also asserts structural compatibility with what the
classifier's ``classify()`` accepts (the classifier keeps its own local
TypedDict copies — ADR-0003 purity — so this is the seam that would catch a
silent drift between the two).
"""

from __future__ import annotations

import inspect
import json
import pathlib
from typing import get_type_hints

import pytest

from idp_regression.adapter import types as adapter_types
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter
from idp_regression.adapter.normalize import normalize
from idp_regression.classifier import classify
from idp_regression.classifier import types as classifier_types

FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent.parent / "fixtures" / "live" / "seed-001-clean.raw.json"
    ).read_text()
)


def test_normalized_output_typeddict_has_the_contract_keys() -> None:
    hints = get_type_hints(adapter_types.NormalizedOutput)
    assert set(hints) == {"status", "fields", "tables", "prompts"}


def test_field_value_typeddict_has_the_contract_keys() -> None:
    hints = get_type_hints(adapter_types.FieldValue)
    assert set(hints) == {"value", "confidence"}


def test_prompt_value_typeddict_has_the_contract_keys() -> None:
    hints = get_type_hints(adapter_types.PromptValue)
    assert set(hints) == {"answer", "confidence", "source"}


def test_confidence_is_always_present_as_a_key_possibly_none() -> None:
    # API contract (ADR-0002): consumers may assume the key exists on every FieldValue.
    out = normalize(FIXTURE, success_statuses={"SUCCEEDED"})
    for cell in out["fields"].values():
        assert "confidence" in cell
    for row in out["tables"]["line_items"]:
        for cell in row.values():
            assert "confidence" in cell


def test_tables_shape_is_dict_of_list_of_dict_of_field_value() -> None:
    out = normalize(FIXTURE, success_statuses={"SUCCEEDED"})
    assert isinstance(out["tables"], dict)
    for rows in out["tables"].values():
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict)
            for cell in row.values():
                assert set(cell) == {"value", "confidence"}


def test_extract_signature_matches_the_adr0002_contract() -> None:
    sig = inspect.signature(MuleSoftIDPAdapter.extract)
    assert list(sig.parameters) == ["self", "document_path", "action_id", "version"]


def test_normalize_signature_is_the_pure_function_contract() -> None:
    sig = inspect.signature(normalize)
    assert list(sig.parameters) == ["raw", "success_statuses"]


# ---- structural compatibility with the classifier's own local types ----


def _adapter_hints(name: str) -> set[str]:
    return set(get_type_hints(getattr(adapter_types, name)))


def _classifier_hints(name: str) -> set[str]:
    return set(get_type_hints(getattr(classifier_types, name)))


def test_normalized_output_keys_match_the_classifiers_local_copy() -> None:
    assert _adapter_hints("NormalizedOutput") == _classifier_hints("NormalizedOutput")


def test_field_value_keys_are_a_subset_the_classifier_accepts() -> None:
    # The classifier's FieldValue.confidence is NotRequired; the adapter's is
    # always-present. Always-present is structurally assignable wherever
    # NotRequired is accepted, so this is a one-way compatibility check.
    assert _adapter_hints("FieldValue") == _classifier_hints("FieldValue")


def test_prompt_value_keys_are_a_subset_the_classifier_accepts() -> None:
    assert _adapter_hints("PromptValue") == _classifier_hints("PromptValue")


@pytest.mark.parametrize(
    "type_name", ["NormalizedOutput", "FieldValue", "PromptValue"]
)
def test_typeddict_required_and_optional_keys_match_the_classifiers_exactly(
    type_name: str,
) -> None:
    # R6 (Atchim): matching key SETS isn't enough — a key that's Required
    # in one TypedDict and NotRequired in the other is a real mypy
    # incompatibility (this is exactly what the `# type: ignore[arg-type]`
    # below used to hide). __required_keys__/__optional_keys__ must match
    # key-for-key.
    adapter_cls = getattr(adapter_types, type_name)
    classifier_cls = getattr(classifier_types, type_name)
    assert adapter_cls.__required_keys__ == classifier_cls.__required_keys__
    assert adapter_cls.__optional_keys__ == classifier_cls.__optional_keys__


def test_a_normalized_output_is_accepted_by_classify_without_a_shape_error() -> None:
    # The real end-to-end seam: an adapter-produced NormalizedOutput must be
    # directly usable as classify()'s `actual` argument (ADR-0002 §Mapping to
    # the golden). No MalformedActualError means the shapes line up. Note:
    # no `# type: ignore` here — the adapter's NormalizedOutput now mirrors
    # the classifier's Required/NotRequired keys exactly (R6), so mypy
    # accepts this call on its own structural terms.
    actual = normalize(FIXTURE, success_statuses={"SUCCEEDED"})
    golden: classifier_types.Golden = {
        "fields": {
            "invoice_number": {"value": "INV-1001", "type": "id", "critical": True},
            "invoice_date": {"value": "2024-06-28", "type": "date", "critical": False},
            "total": {"value": "87.48", "type": "number", "critical": True},
        },
        "tables": {
            "line_items": {
                "match_key": "sku",
                "critical": False,
                "rows": [
                    {
                        "sku": "A-100",
                        "description": "Printer paper, A4, 500 sheets",
                        "quantity": "10",
                        "unit_price": "6.50",
                    }
                ],
            }
        },
        # This action returns no prompts (still-unverified unknown for one
        # that does — ADR-0002 A11), so the golden's `prompts` block is
        # empty rather than expecting an answer that will never arrive.
        "prompts": {},
    }
    verdicts = classify(golden, actual)
    assert verdicts["invoice_number"]["verdict"] == "match"
