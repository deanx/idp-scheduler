"""Contract test CT-01 (T-01.2.6).

Pins the ``NormalizedOutput``/``FieldValue``/``PromptValue`` shape that
``normalize()`` emits from a raw IDP ``pages[]`` body, against a
**synthetic, scrubbed** fixture (S-01.6 hasn't captured a real one yet —
re-pin this test against the real fixture when S-01.6 runs). Also asserts
structural compatibility with what the classifier's ``classify()`` accepts
(the classifier keeps its own local TypedDict copies — ADR-0003 purity —
so this is the seam that would catch a silent drift between the two).
"""

from __future__ import annotations

import inspect
import json
import pathlib
from typing import get_type_hints

from idp_regression.adapter import types as adapter_types
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter
from idp_regression.adapter.normalize import normalize
from idp_regression.classifier import classify
from idp_regression.classifier import types as classifier_types

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "raw_idp_response.json").read_text()
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


def test_a_normalized_output_is_accepted_by_classify_without_a_shape_error() -> None:
    # The real end-to-end seam: an adapter-produced NormalizedOutput must be
    # directly usable as classify()'s `actual` argument (ADR-0002 §Mapping to
    # the golden). No MalformedActualError means the shapes line up.
    actual = normalize(FIXTURE, success_statuses={"SUCCEEDED"})
    golden: classifier_types.Golden = {
        "fields": {
            "invoice_number": {"value": "INV-1001", "type": "id", "critical": True},
            "invoice_date": {"value": "2024-03-15", "type": "date", "critical": False},
            "total": {"value": "1250.00", "type": "number", "critical": True},
        },
        "tables": {
            "line_items": {
                "match_key": "description",
                "critical": False,
                "rows": [{"description": "Widget A", "qty": "10", "unit_price": "50.00"}],
            }
        },
        "prompts": {"What is the vendor name?": {"answer": "Acme Corp", "critical": False}},
    }
    verdicts = classify(golden, actual)  # type: ignore[arg-type]
    assert verdicts["invoice_number"]["verdict"] == "match"
