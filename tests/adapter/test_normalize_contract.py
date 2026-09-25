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
    # R-2 secondary finding (2026-09-22 REQUEST CHANGES round): the original
    # assertion only checked `invoice_number`, so a changed `total` or a
    # changed table-row value would still pass this test — content beyond
    # one field was never actually asserted. Widen it.
    assert verdicts["total"]["verdict"] == "match"
    line_items = verdicts["line_items"]
    assert line_items["verdict"] == "detail"
    # The golden only lists row A-100; the actual's second row (B-200) has
    # no golden counterpart and correctly surfaces as a "new_line" — that
    # is itself part of what this widened assertion pins (a changed value
    # on the matched row, or a golden update collapsing this to "match",
    # would both be caught here).
    matched_rows = [row for row in line_items["rows"] if row["match_key"] == "A-100"]
    assert {row["column"] for row in matched_rows} == {"description", "quantity", "unit_price"}
    assert all(row["verdict"] == "match" for row in matched_rows)
    unmatched_rows = [row for row in line_items["rows"] if row["match_key"] == "B-200"]
    assert len(unmatched_rows) == 1
    assert unmatched_rows[0]["verdict"] == "new_line"


# ---------------------------------------------------------------------------
# SR-1: the FAILED terminal shape, pinned against a live capture.
#
# Probed live 2026-09-25 (DEBT-22 leg 2 / ASM-01) by submitting a truncated
# PDF. IDP reached `status: "FAILED"` in 3.2s. IDP discards a result after 24
# hours, so this capture is the only durable copy of that response and these
# tests are the only thing that can falsify a claim about its shape.
# ---------------------------------------------------------------------------

FAILED_FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent.parent / "fixtures" / "live" / "failed-execution.raw.json"
    ).read_text()
)


def test_the_live_failed_body_carries_status_failed_and_empty_containers() -> None:
    """The shape a hard IDP failure actually has on the wire.

    Note what it is NOT: there is no error object, no message, no reason
    code -- `fields` and `tables` are present and EMPTY, and `status` is
    the only thing distinguishing this from a document where the
    extractor legitimately found nothing. That is precisely why `status`
    has to be load-bearing, and why a FAILED execution must never reach
    `normalize()` as if it were a result (see the next test).
    """
    assert FAILED_FIXTURE["status"] == "FAILED"
    assert FAILED_FIXTURE["fields"] == {}
    assert FAILED_FIXTURE["tables"] == {}
    assert "pages" not in FAILED_FIXTURE


def test_normalize_raises_on_the_live_failed_body_and_never_returns_empty() -> None:
    """REG-11's defect class, on the failure path.

    REG-11 was `normalize()` returning an empty-but-successful
    `NormalizedOutput` for a real body instead of raising. This body is
    the shape most likely to reproduce it -- well-formed, with every
    container present and empty -- so the pin is that it RAISES, never
    that it returns something falsy a caller might treat as "no fields
    extracted" and gate as a pass.
    """
    with pytest.raises(Exception) as excinfo:
        normalize(FAILED_FIXTURE, {"SUCCEEDED"})
    assert type(excinfo.value).__name__ == "MalformedIDPOutputError"
