"""T-01.2.4/.2.5 — normalize() merge semantics + untrusted-input contract
(NFR N21, ADR-0002)."""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from idp_regression.adapter.errors import MalformedIDPOutputError
from idp_regression.adapter.normalize import MAX_TABLE_ROWS, MAX_VALUE_BYTES, normalize

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "raw_idp_response.json").read_text()
)


# ---- happy path / CT-01 shape ---------------------------------------------


def test_normalize_walks_pages_fields_tables_prompts() -> None:
    out = normalize(FIXTURE, success_statuses={"SUCCEEDED"})
    assert out["status"] == "SUCCEEDED"
    assert out["fields"]["invoice_number"] == {"value": "INV-1001", "confidence": 0.98}
    assert out["tables"]["line_items"][0]["description"] == {
        "value": "Widget A",
        "confidence": 0.95,
    }
    assert out["prompts"]["What is the vendor name?"]["answer"] == "Acme Corp"
    assert out["prompts"]["What is the vendor name?"]["source"] == "page-1-ocr"


def test_normalize_defaults_missing_blocks_to_empty() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{}]}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out == {"status": "SUCCEEDED", "fields": {}, "tables": {}, "prompts": {}}


# ---- merge semantics --------------------------------------------------


def test_fields_last_wins_across_pages() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"fields": {"total": {"value": "100.00", "confidence": 0.5}}},
            {"fields": {"total": {"value": "200.00", "confidence": 0.9}}},
        ],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "200.00"


def test_tables_concatenate_rows_across_pages() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"tables": {"line_items": [{"description": {"value": "A", "confidence": 0.9}}]}},
            {"tables": {"line_items": [{"description": {"value": "B", "confidence": 0.9}}]}},
        ],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert [r["description"]["value"] for r in out["tables"]["line_items"]] == ["A", "B"]


def test_duplicate_prompt_string_raises_typed_error() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": [
                    {
                        "prompt": "vendor?",
                        "source": "p1",
                        "answer": {"value": "A", "confidence": 0.9},
                    },
                    {
                        "prompt": "vendor?",
                        "source": "p2",
                        "answer": {"value": "B", "confidence": 0.9},
                    },
                ]
            }
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "duplicate_prompt"


# ---- field/table name sanitization (trust boundary) ------------------


@pytest.mark.parametrize(
    "bad_name", ["total\ngate", "a:b", "", "has space", "total\n", "x" * 129]
)
def test_unsafe_field_name_raises_typed_error(bad_name: str) -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {bad_name: {"value": "x", "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_field_name"


def test_field_name_at_the_128_char_cap_is_accepted() -> None:
    name = "x" * 128
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {name: {"value": "v", "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert name in out["fields"]


def test_unsafe_table_name_raises_typed_error() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {"a:b": []}}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_field_name"


def test_safe_field_name_charset_accepted() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"Line-Item_1": {"value": "x", "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert "Line-Item_1" in out["fields"]


# ---- prompt-key rule (verbatim, 1-200 chars, no control chars) -------


def test_unsafe_prompt_key_with_control_char_raises() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"prompts": [{"prompt": "vendor?\n", "answer": {"value": "A", "confidence": None}}]}
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_prompt_key"


def test_unsafe_prompt_key_too_long_raises() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"prompts": [{"prompt": "x" * 201, "answer": {"value": "A", "confidence": None}}]}
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_prompt_key"


def test_prompt_key_allows_punctuation_and_charset_outside_field_names() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": [
                    {
                        "prompt": "What is the vendor: name?",
                        "answer": {"value": "A", "confidence": None},
                    }
                ]
            }
        ],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert "What is the vendor: name?" in out["prompts"]


# ---- bounded sizes -----------------------------------------------------


def test_field_value_too_large_raises_typed_error() -> None:
    huge = "x" * (MAX_VALUE_BYTES + 1)
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": huge, "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "value_too_large"


def test_table_too_large_raises_typed_error() -> None:
    rows = [{"description": {"value": "x", "confidence": None}}] * (MAX_TABLE_ROWS + 1)
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {"line_items": rows}}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "table_too_large"


def test_table_too_large_counts_rows_across_pages() -> None:
    half = [{"description": {"value": "x", "confidence": None}}] * (MAX_TABLE_ROWS // 2 + 1)
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"tables": {"line_items": half}}, {"tables": {"line_items": half}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "table_too_large"


# ---- confidence coercion: NaN/out-of-range -> None, never clamped -----


def test_huge_int_confidence_does_not_raise_overflow_error_becomes_none() -> None:
    # int -> float conversion of a huge int raises OverflowError; this must
    # never escape normalize() and is out-of-range -> None (Atchim R2).
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": 10**400}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] is None


def test_lone_surrogate_value_raises_typed_error_not_a_raw_unicode_error() -> None:
    # str.encode("utf-8") on a lone surrogate raises UnicodeEncodeError; this
    # must never escape normalize() as a raw exception, and the typed error
    # must not carry the value in its message or its exception chain.
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "\udcff", "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert "\udcff" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert "\udcff" not in repr(excinfo.value.__cause__)


@pytest.mark.parametrize("bad_conf", [math.nan, 1.5, -0.1])
def test_invalid_confidence_becomes_none_not_clamped(bad_conf: float) -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": bad_conf}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] is None


def test_valid_confidence_is_preserved() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": 0.0}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] == 0.0


# ---- three-state missing/empty/null preserved --------------------------


def test_absent_field_is_absent_not_synthesized() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"fields": {}}]}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert "total" not in out["fields"]


def test_null_value_is_preserved_as_none() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": None, "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] is None


def test_empty_string_value_is_preserved_distinct_from_null() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "", "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == ""


# ---- malformed body: no KeyError/AttributeError escapes ----------------


@pytest.mark.parametrize("bad_raw", [None, "a string", 42, [], {"pages": "not a list"}])
def test_malformed_top_level_body_raises_typed_error_not_kerror(bad_raw: object) -> None:
    with pytest.raises(MalformedIDPOutputError):
        normalize(bad_raw, success_statuses={"SUCCEEDED"})


def test_missing_status_raises_typed_error() -> None:
    with pytest.raises(MalformedIDPOutputError):
        normalize({"pages": []}, success_statuses={"SUCCEEDED"})


def test_non_dict_page_raises_typed_error() -> None:
    with pytest.raises(MalformedIDPOutputError):
        normalize({"status": "SUCCEEDED", "pages": ["not a dict"]}, success_statuses={"SUCCEEDED"})


def test_non_dict_field_cell_raises_typed_error() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"fields": {"total": "not a mapping"}}]}
    with pytest.raises(MalformedIDPOutputError):
        normalize(raw, success_statuses={"SUCCEEDED"})


def test_non_dict_table_row_raises_typed_error() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {"line_items": ["not a mapping"]}}]}
    with pytest.raises(MalformedIDPOutputError):
        normalize(raw, success_statuses={"SUCCEEDED"})


def test_non_dict_prompt_entry_raises_typed_error() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"prompts": ["not a mapping"]}]}
    with pytest.raises(MalformedIDPOutputError):
        normalize(raw, success_statuses={"SUCCEEDED"})
