"""T-01.2.4/.2.5 — normalize() merge semantics + untrusted-input contract
(NFR N21, ADR-0002).

``fixtures/raw_idp_response.json`` is the LEGACY ``pages[]`` envelope
(ADR-0002 A11) — never observed live, kept only as the optional secondary
shape. The primary wire contract is the top-level ``fields``/``tables``
shape pinned by the real capture in ``tests/fixtures/live/seed-001-clean.raw.json``
(see ``test_normalize_live_capture.py`` and CT-01)."""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from idp_regression.adapter.errors import MalformedIDPOutputError
from idp_regression.adapter.normalize import MAX_TABLE_ROWS, MAX_VALUE_BYTES, normalize
from tests.adapter._prompts import _as_documented_map

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
                "prompts": _as_documented_map([
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
                ])
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


def test_field_name_129_chars_is_rejected() -> None:
    # Dedicated regression pin (Atchim round 2) — kills a "remove {1,128}"
    # mutant on _SAFE_NAME_PATTERN: at 129 chars an unbounded charset would
    # still match, but the golden schema's `^[A-Za-z0-9_-]{1,128}$` caps it.
    name = "x" * 129
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {name: {"value": "v", "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_field_name"


def test_table_name_129_chars_is_rejected() -> None:
    name = "x" * 129
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {name: []}}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_field_name"


def test_unsafe_table_name_raises_typed_error() -> None:
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {"a:b": []}}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_field_name"


def test_unsafe_table_column_name_raises_typed_error() -> None:
    # /test Scenario B item 11: a coverage gap — _validate_name is called
    # on table column names too (normalize.py's _merge_tables), but no
    # test previously exercised it directly.
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"tables": {"line_items": [{"a:b": {"value": "x", "confidence": None}}]}}
        ],
    }
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
            {"prompts": _as_documented_map([
                {"prompt": "vendor?\n", "answer": {"value": "A", "confidence": None}}
            ])}
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_prompt_key"


def test_unsafe_prompt_key_lone_surrogate_raises() -> None:
    # /test Scenario B item 5: a lone surrogate passes the old
    # control-char-only charset check and would crash later downstream
    # (the platform's sha256 of the prompt key, UnicodeEncodeError) — the
    # typed error must not echo the value.
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": _as_documented_map([
                    {"prompt": "vendor?\udcff", "answer": {"value": "A", "confidence": None}}
                ])
            }
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_prompt_key"
    assert "\udcff" not in str(excinfo.value)


def test_unsafe_prompt_key_too_long_raises() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"prompts": _as_documented_map([
                {"prompt": "x" * 201, "answer": {"value": "A", "confidence": None}}
            ])}
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
                "prompts": _as_documented_map([
                    {
                        "prompt": "What is the vendor: name?",
                        "answer": {"value": "A", "confidence": None},
                    }
                ])
            }
        ],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert "What is the vendor: name?" in out["prompts"]


# ---- bounded sizes -----------------------------------------------------


def test_max_value_bytes_is_pinned_at_64kb() -> None:
    # Pinned literal (Atchim suggestion) — the cap itself is load-bearing
    # (ADR-0002), not just "whatever the constant happens to be".
    assert MAX_VALUE_BYTES == 65_536


def test_field_value_too_large_raises_typed_error() -> None:
    huge = "x" * (MAX_VALUE_BYTES + 1)
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": huge, "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "value_too_large"


def test_field_value_at_the_64kb_limit_is_accepted() -> None:
    at_limit = "x" * MAX_VALUE_BYTES
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": at_limit, "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == at_limit


def test_field_value_multibyte_utf8_is_measured_in_bytes_not_characters() -> None:
    # "€" is 3 UTF-8 bytes — a character-count check would under-measure
    # this and let a byte-oversized value through.
    char_count = MAX_VALUE_BYTES // 3 + 1  # 3 bytes/char -> exceeds the byte cap
    huge_multibyte = "€" * char_count
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": huge_multibyte, "confidence": None}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "value_too_large"


def test_field_value_multibyte_utf8_at_the_byte_limit_is_accepted() -> None:
    at_limit_multibyte = "€" * (MAX_VALUE_BYTES // 3)  # exactly at the 64KB byte cap
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": at_limit_multibyte, "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == at_limit_multibyte


def test_max_table_rows_is_pinned_at_10000() -> None:
    assert MAX_TABLE_ROWS == 10_000


def test_table_at_the_10000_row_limit_is_accepted() -> None:
    rows = [{"description": {"value": "x", "confidence": None}}] * MAX_TABLE_ROWS
    raw = {"status": "SUCCEEDED", "pages": [{"tables": {"line_items": rows}}]}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert len(out["tables"]["line_items"]) == MAX_TABLE_ROWS


def test_prompt_source_too_large_raises_typed_error() -> None:
    huge_source = "x" * (MAX_VALUE_BYTES + 1)
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": _as_documented_map([
                    {
                        "prompt": "vendor?",
                        "source": huge_source,
                        "answer": {"value": "A", "confidence": None},
                    }
                ])
            }
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "value_too_large"


def test_prompt_source_at_the_64kb_limit_is_accepted() -> None:
    at_limit_source = "x" * MAX_VALUE_BYTES
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": _as_documented_map([
                    {
                        "prompt": "vendor?",
                        "source": at_limit_source,
                        "answer": {"value": "A", "confidence": None},
                    }
                ])
            }
        ],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["prompts"]["vendor?"]["source"] == at_limit_source


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


# ---- confidence coercion: out-of-range now RAISES, never silently None
# (ADR-0002 A11 / REG-11 D2 — the old "-> None" behavior was itself a
# fail-open defect: a broken IDP action's confidence became `None` with no
# exception and no log line).


def test_huge_int_confidence_raises_typed_error_not_an_overflow_error() -> None:
    # int -> float conversion of a huge int raises OverflowError; this must
    # never escape normalize() as a raw exception, and (D2 fix) must not be
    # silently swallowed into None either.
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": 10**400}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


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
def test_invalid_confidence_raises_typed_error_not_clamped_or_silenced(bad_conf: float) -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": bad_conf}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


def test_non_numeric_confidence_raises_typed_error() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": "high"}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


def test_bool_confidence_raises_typed_error() -> None:
    # bool is a subclass of int in Python; must not silently pass as 0/1.
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": True}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


def test_missing_confidence_key_entirely_is_still_none_not_an_error() -> None:
    # Three-state contract preserved: ABSENT is "not provided", distinct
    # from a present-but-invalid value, which now raises.
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00"}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] is None


def test_explicit_null_confidence_is_none_not_an_error() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "100.00", "confidence": None}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] is None


# ---- confidenceScore: the real 0-100 scale (ADR-0002 A11) -------------


def test_confidence_score_key_is_converted_from_0_100_to_0_1_scale() -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0, "geometry": None}},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] == pytest.approx(0.99)


@pytest.mark.parametrize("boundary,expected", [(0.0, 0.0), (100.0, 1.0)])
def test_confidence_score_boundary_values_are_accepted(
    boundary: float, expected: float
) -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": boundary}},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] == pytest.approx(expected)


@pytest.mark.parametrize("bad_score", [100.1, -0.1, math.nan, "high", True])
def test_confidence_score_out_of_range_or_non_numeric_raises_typed_error(
    bad_score: object,
) -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": bad_score}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


def test_confidence_score_huge_int_raises_typed_error_not_overflow_error() -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": 10**400}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_confidence"


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


def test_status_outside_success_statuses_raises_typed_error() -> None:
    # ADR-0002:125 — normalize() itself enforces success_statuses, not just
    # the adapter's poll loop (Atchim R7).
    raw = {"status": "FAILED", "pages": []}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "status_not_success"


def test_status_within_success_statuses_is_accepted() -> None:
    raw = {"status": "DONE", "fields": {}}
    out = normalize(raw, success_statuses={"DONE", "SUCCEEDED"})
    assert out["status"] == "DONE"


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
    raw = {"status": "SUCCEEDED", "pages": [{"prompts": _as_documented_map(["not a mapping"])}]}
    with pytest.raises(MalformedIDPOutputError):
        normalize(raw, success_statuses={"SUCCEEDED"})


def test_value_only_cell_shape_raises_typed_error() -> None:
    # Defect (2026-09-22 architecture-adherence review): IDP's execution-
    # result endpoint returns value-only cells by default (a bare scalar,
    # not {"value": ..., "confidence": ...}) unless the poll GET carries
    # ?valueOnly=false. normalize() REQUIRES the full shape (a "value" key
    # on every cell) — this pins the consequence a live, un-parameterized
    # poll GET would hit today: a value-only cell is a malformed cell, not
    # silently coerced or confidence-dropped.
    raw = {"status": "SUCCEEDED", "pages": [{"fields": {"total": "1150.00"}}]}
    with pytest.raises(MalformedIDPOutputError):
        normalize(raw, success_statuses={"SUCCEEDED"})


# ---- envelope selection: pages[] vs top-level fields/tables (REG-11 D1) --


def test_top_level_fields_and_tables_are_parsed_without_a_pages_key() -> None:
    # The real wire shape: no 'pages' key at all.
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0}},
        "tables": {"line_items": [{"sku": {"value": "A-100", "confidenceScore": 99.0}}]},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "87.48"
    assert out["tables"]["line_items"][0]["sku"]["value"] == "A-100"


def test_top_level_fields_only_with_no_tables_key_is_parsed() -> None:
    raw = {"status": "SUCCEEDED", "fields": {"total": {"value": "87.48"}}}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "87.48"
    assert out["tables"] == {}


def test_top_level_tables_only_with_no_fields_key_is_parsed() -> None:
    raw = {"status": "SUCCEEDED", "tables": {"line_items": [{"sku": {"value": "A-100"}}]}}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"] == {}
    assert out["tables"]["line_items"][0]["sku"]["value"] == "A-100"


def test_a_response_with_a_pages_key_and_top_level_fields_merges_both_not_exclusive() -> None:
    # D1 terminal rule (2026-09-23): normalize() UNIONS every recognised
    # container present — it never picks one envelope to the exclusion of
    # another. A non-empty 'pages' list no longer suppresses a top-level
    # container beside it (that exclusivity was itself the REG-11 D1 defect
    # family, one level deeper each round — see the D1 terminal-rule note
    # in test_barren_or_junk_pages_never_discards_a_populated_top_level_container
    # below). Renamed from '..._ignores_any_top_level_fields_or_tables',
    # whose old assertion (decoy must NOT appear) is the exact behavior this
    # fix removes.
    raw = {
        "status": "SUCCEEDED",
        "fields": {"decoy": {"value": "should also appear"}},
        "pages": [{"fields": {"total": {"value": "87.48"}}}],
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["decoy"]["value"] == "should also appear"
    assert out["fields"]["total"]["value"] == "87.48"


# ---- R-1 (2026-09-23 REQUEST CHANGES round): the union's collision
# precedence was emergent and unpinned. `normalize.py` appends the
# top-level rollup AFTER `pages[]`'s entries and `_merge_fields` is
# last-wins, so the top level silently wins any shared field name — but
# nothing asserted that until now. Mutation-verified: swapping the two
# `logical_pages.extend`/`.append` blocks in `normalize()` (pages-after-
# top-level instead of pages-before) turns this test RED while the rest of
# the suite stays green — proof the old suite exercised the union but
# never a genuine collision.


def test_top_level_field_wins_over_a_colliding_pages_entry() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"fields": {"total": {"value": "from pages"}}}],
        "fields": {"total": {"value": "from top level"}},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "from top level"


# ---- R-2 (2026-09-23 REQUEST CHANGES round): three collision policies at
# the page/top-level seam the D1 union opened. `fields` above is last-wins
# (top level silently wins); `tables` concatenates without dedup (a
# genuinely shared row DOUBLES in NormalizedOutput — masked only at
# classifier/gate.py's match_key indexing, not here); `prompts` used to
# raise `duplicate_prompt` for ANY collision, including this one, which is
# inconsistent with how `fields` treats the identical situation. Decision:
# a prompt key colliding across the pages/top-level boundary now resolves
# last-wins (top level overrides), matching `fields`; a TRUE duplicate
# within one container (two entries in the same `pages[]` entry, or two in
# the top-level rollup's own `prompts` list) still raises — that is a
# genuine integrity defect, not a rollup echo.


def test_top_level_table_row_concatenates_without_dedup_across_the_seam() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"tables": {"line_items": [{"sku": {"value": "A-100"}}]}}],
        "tables": {"line_items": [{"sku": {"value": "A-100"}}]},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    # R-2 decision: normalize() does not dedup — it has no match_key to
    # dedup by (that is golden-schema knowledge). The row appears twice;
    # classifier/gate.py's match_key indexing is what collapses this in
    # practice, not normalize().
    assert len(out["tables"]["line_items"]) == 2


def test_top_level_prompt_wins_over_a_colliding_pages_entry_instead_of_raising() -> None:
    # R-2 PIN. Before the fix, this raised `duplicate_prompt` — the same
    # page/top-level echo that `fields` already tolerates silently was
    # treated as a fatal integrity violation for `prompts` alone.
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {
                "prompts": _as_documented_map([
                    {"prompt": "vendor?", "answer": {"value": "from pages"}},
                ])
            }
        ],
        "prompts": _as_documented_map([
            {"prompt": "vendor?", "answer": {"value": "from top level"}},
        ]),
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["prompts"]["vendor?"]["answer"] == "from top level"


def test_duplicate_prompt_within_the_top_level_rollup_itself_still_raises() -> None:
    # The R-2 relaxation is scoped to the pages/top-level SEAM only — two
    # entries sharing a key inside the SAME container (here, the top-level
    # rollup's own 'prompts' list) is still a genuine defect and must
    # still raise, exactly as two entries in the same pages[] entry does
    # (test_duplicate_prompt_string_raises_typed_error, unchanged above).
    raw = {
        "status": "SUCCEEDED",
        "prompts": _as_documented_map([
            {"prompt": "vendor?", "answer": {"value": "A"}},
            {"prompt": "vendor?", "answer": {"value": "B"}},
        ]),
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "duplicate_prompt"


def test_response_with_neither_pages_nor_top_level_container_raises_typed_error() -> None:
    # THE REGRESSION PIN (REG-11 D1). The pre-fix code did
    # `raw.get("pages", [])`, defaulting a missing key to an empty list and
    # walking zero pages WITHOUT RAISING — returning a confident, empty
    # "success" for what should be a rejected, unrecognisable envelope.
    # This is the single most serious defect found in this project: a
    # silently-wrong GREEN build (CLAUDE.md ## Rigor).
    raw = {"status": "SUCCEEDED", "documentName": "seed-001-clean.pdf", "id": "exec-1"}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "missing_envelope"


def test_response_with_neither_container_does_not_return_an_empty_success() -> None:
    # Same pin, stated the other way round so it can never be "fixed" by
    # loosening the raise back into a silent empty NormalizedOutput.
    raw = {"status": "SUCCEEDED"}
    try:
        normalize(raw, success_statuses={"SUCCEEDED"})
    except MalformedIDPOutputError:
        pass
    else:
        pytest.fail(
            "normalize() must never return an empty NormalizedOutput for an "
            "unrecognisable envelope (REG-11) — it must raise"
        )


# ---- R-1 (2026-09-22 REQUEST CHANGES round): precedence must be
# CONTENT-based, not PRESENCE-based. The original A11 fix keyed precedence
# on `"pages" in raw`, so an empty `pages: []` outranked a populated
# top-level `fields`/`tables` container and silently discarded it — the
# same fail-open defect class REG-11 exists to close, reopened by its own
# fix. Reproduced live: re-wrapping a genuine 9-field/2-table extraction
# with a bare `"pages": []` alongside it made all its content vanish
# without raising.


def test_empty_pages_list_does_not_outrank_a_populated_top_level_container() -> None:
    # THE R-1 REGRESSION PIN. Before the fix, `"pages" in raw` was True
    # (even though the list is empty) so the top-level 'fields'/'tables'
    # container next to it was silently discarded and normalize() returned
    # an empty success — nine real fields thrown away with no raise.
    raw = {
        "status": "SUCCEEDED",
        "pages": [],
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0}},
        "tables": {"line_items": [{"sku": {"value": "A-100", "confidenceScore": 99.0}}]},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "87.48"
    assert out["tables"]["line_items"][0]["sku"]["value"] == "A-100"


def test_empty_pages_list_alone_with_no_top_level_container_still_raises() -> None:
    # The other reproduced NO-RAISE case from the R-1 finding: an empty
    # 'pages' list with nothing else recognisable is the same
    # "unrecognisable envelope" as 'pages' being absent entirely — it must
    # raise, not return an empty success.
    raw = {"status": "SUCCEEDED", "pages": []}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "missing_envelope"


def test_pages_list_containing_only_an_empty_page_still_wins_over_nothing_else() -> None:
    # Content-based precedence means a NON-EMPTY 'pages' list (even one
    # whose sole page carries no fields) still legitimately wins and is
    # NOT the missing-envelope case — distinguishing "an empty pages LIST"
    # (falls through) from "a pages list containing an empty PAGE" (a
    # genuine, if content-free, page).
    raw = {"status": "SUCCEEDED", "pages": [{}]}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"] == {}


# ---- D1 terminal rule (2026-09-23): "barren" is a PRESENCE question, not a
# CONTENT-sufficiency question. `normalize()` no longer chooses one envelope
# over another at all — it UNIONS every recognised container that is
# present (each `pages[]` entry, plus the top-level body itself when it
# carries `fields`/`tables`/`prompts`). An empty container simply
# contributes zero entries to the merge; a non-empty one contributes its
# real entries; nothing is ever silently discarded in favor of the other.
# This is the third occurrence of the REG-11 D1 defect class (`pages`
# absent -> `[]` default; then `pages: []` outranking a populated top
# level; then `pages: [{}]` outranking it) — each prior fix moved the
# emptiness test one level of nesting in without making it terminal. A
# UNION rule has no "level" left to dig into: presence is checked exactly
# once, at the top, exactly as it already is for entries WITHIN `pages[]`;
# there is no deeper barren shape a fourth variant could hide behind.
@pytest.mark.parametrize(
    "pages_value",
    [
        [],
        [{}],
        [{}, {}],
        [{"fields": {"zzz": {"value": "junk", "confidence": None}}}],
    ],
    ids=[
        "empty_pages_list",
        "single_empty_page",
        "two_empty_pages",
        "junk_field_beside_populated_top_level",
    ],
)
def test_barren_or_junk_pages_never_discards_a_populated_top_level_container(
    pages_value: object,
) -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": pages_value,
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0}},
        "tables": {"line_items": [{"sku": {"value": "A-100", "confidenceScore": 99.0}}]},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["value"] == "87.48"
    assert out["tables"]["line_items"][0]["sku"]["value"] == "A-100"


# ---- D2 (2026-09-23): `prompts` is a recognised top-level envelope
# container too, alongside `fields`/`tables`. Before this fix a
# prompts-only response (no `fields`, no `tables`) raised
# `missing_envelope` outright, though `NormalizedOutput` carries `prompts`
# as a first-class member and an IDP action can plausibly return only
# prompt answers. ⚠️ This test makes the envelope check CONSISTENT WITH THE
# DECLARED TYPE — it is explicitly NOT a wire-contract verification (SR-1,
# docs/state/REGRESSIONS.md): the `prompts` shape itself remains
# unverified against a live IDP response (DEBT-69).
def test_top_level_prompts_only_is_a_recognised_envelope_not_missing() -> None:
    raw = {
        "status": "SUCCEEDED",
        "prompts": _as_documented_map([
            {"prompt": "invoice_number", "answer": {"value": "INV-1", "confidence": None}}
        ]),
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"] == {}
    assert out["tables"] == {}
    assert out["prompts"]["invoice_number"]["answer"] == "INV-1"


def test_non_list_pages_raises_even_with_a_populated_top_level_container_present() -> None:
    # A malformed 'pages' value must raise outright, never silently fall
    # through to the top-level container — falling through here would hide
    # a genuinely corrupt response shape.
    raw = {
        "status": "SUCCEEDED",
        "pages": "not-a-list",
        "fields": {"total": {"value": "87.48"}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_pages"


def test_well_formed_envelope_with_genuinely_empty_containers_is_not_an_error() -> None:
    # The explicit empty-extraction decision (R-1): a top-level container
    # that is PRESENT but empty means "IDP looked and found nothing" — a
    # legitimate result, not a rejected envelope. Only the ABSENCE of every
    # recognisable container (missing_envelope) raises.
    raw = {"status": "SUCCEEDED", "fields": {}, "tables": {}}
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out == {"status": "SUCCEEDED", "fields": {}, "tables": {}, "prompts": {}}


# ---- M-1 (2026-09-22 REQUEST CHANGES round): a 0-1 confidenceScore is a
# scale-ambiguous value, not a legitimate low score — rejected fail-closed
# rather than silently divided by 100 into a wrong two-orders-of-magnitude
# value.


@pytest.mark.parametrize("ambiguous_score", [0.99, 0.5, 0.01])
def test_confidence_score_strictly_between_0_and_1_is_scale_ambiguous_and_raises(
    ambiguous_score: float,
) -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": ambiguous_score}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "confidence_scale_ambiguous"


@pytest.mark.parametrize("boundary_score,expected", [(0.0, 0.0), (1.0, 0.01)])
def test_confidence_score_at_0_or_1_is_a_legitimate_boundary_not_ambiguous(
    boundary_score: float, expected: float
) -> None:
    # 0.0 and 1.0 are the endpoints of the ambiguous OPEN interval, not
    # inside it — 0.0 is "0% confidence" and 1.0 is "1% confidence" on the
    # documented 0-100 confidenceScore scale, both legitimate.
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": boundary_score}},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["fields"]["total"]["confidence"] == pytest.approx(expected)


# ---- M-2 (2026-09-22 REQUEST CHANGES round): both confidence keys present
# on one cell is a structural conflict — two scales on one cell is an
# unrecognised envelope, not a preference — and must raise rather than
# silently letting `confidenceScore` win and skip validating `confidence`.


def test_both_confidence_keys_present_raises_even_when_confidencescore_is_valid() -> None:
    # THE M-2 REGRESSION PIN. Before the fix, `confidenceScore` silently
    # won and the out-of-range `confidence: 5.0` — which alone would raise
    # `invalid_confidence` — was never even looked at.
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0, "confidence": 5.0}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "conflicting_confidence_keys"


def test_both_confidence_keys_present_raises_even_when_both_are_individually_valid() -> None:
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "87.48", "confidenceScore": 99.0, "confidence": 0.9}},
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "conflicting_confidence_keys"


# ---- DEBT-69(a): MuleSoft's documented `prompts` shape (2026-09-27) -------
#
# The pin is MuleSoft's own documentation example, NOT a live capture (see
# fixtures/mulesoft_docs_prompts_example.README.md). The org emits no prompts.

DOCS_PROMPTS_EXAMPLE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "mulesoft_docs_prompts_example.json").read_text()
)


def test_the_documented_prompts_example_parses() -> None:
    out = normalize(DOCS_PROMPTS_EXAMPLE, success_statuses={"SUCCEEDED"})
    prompt = out["prompts"]["what is the company main business"]
    assert prompt["answer"] is None
    assert prompt["source"] == "document"


def test_the_documented_map_is_also_accepted_at_the_top_level() -> None:
    """Real responses put `fields`/`tables` at the top level with no
    `pages` (1d78c32); a prompts map arriving the same way must parse."""
    raw = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "1.00", "confidenceScore": 99.0}},
        "prompts": {"vendor": {"prompt": "Who is the vendor?", "source": "document",
                               "answer": {"value": "Acme", "confidenceScore": 88.0}}},
    }
    out = normalize(raw, success_statuses={"SUCCEEDED"})
    assert out["prompts"]["Who is the vendor?"]["answer"] == "Acme"
    assert out["fields"]["total"]["value"] == "1.00"


def test_the_undocumented_list_form_is_rejected() -> None:
    """The shape the parser used to expect. No MuleSoft documentation
    describes it; accepting it is how the mismatch went unnoticed."""
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"prompts": [{"prompt": "vendor?", "answer": {"value": "A"}}]}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_page"
    assert "documented shape" in str(excinfo.value)


@pytest.mark.parametrize("empty", [[], {}])
def test_an_empty_prompts_container_is_no_prompts(empty: object) -> None:
    raw = {"status": "SUCCEEDED", "fields": {"total": {"value": "1.00"}}, "prompts": empty}
    assert normalize(raw, success_statuses={"SUCCEEDED"})["prompts"] == {}


def test_an_unsafe_prompt_name_is_rejected() -> None:
    raw = {
        "status": "SUCCEEDED",
        "pages": [{"prompts": {"bad\nname": {"prompt": "ok?", "answer": {"value": "A"}}}}],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "unsafe_prompt_key"


# ---- DEBT-69(a) /test gate findings F-1..F-5 (2026-09-27) ------------------


def test_a_question_shared_by_two_pages_is_a_duplicate() -> None:
    """F-1 (M15/M17): the pages-vs-pages rule, unchanged by the map shape.
    The same question answered on two pages is two claims for one key."""
    raw = {
        "status": "SUCCEEDED",
        "pages": [
            {"prompts": {"a": {"prompt": "vendor?", "answer": {"value": "A"}}}},
            {"prompts": {"b": {"prompt": "vendor?", "answer": {"value": "B"}}}},
        ],
    }
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "duplicate_prompt"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"value": "Acme", "confidenceScore": 88.0}, 0.88),  # the live 0-100 key (REG-11 D2)
        ({"value": "Acme", "confidence": 0.7}, 0.7),
        ({"value": "Acme"}, None),
    ],
)
def test_a_prompt_answers_confidence_is_read_like_a_fields(
    answer: dict[str, object], expected: float | None
) -> None:
    """F-2 (M09/M10): the REG-11 D2 class, on the one branch its pins did not
    cover. A `confidenceScore` of 88 must arrive as 0.88, not be dropped."""
    raw = {"status": "SUCCEEDED",
           "prompts": {"v": {"prompt": "Who is the vendor?", "answer": answer}}}
    got = normalize(raw, success_statuses={"SUCCEEDED"})["prompts"]["Who is the vendor?"]
    if expected is None:
        assert got.get("confidence") is None
    else:
        assert got.get("confidence") == pytest.approx(expected)


@pytest.mark.parametrize(
    "prompts",
    [
        {"SECRET-NAME\n": {"prompt": "ok?", "answer": {"value": "A"}}},
        {"n": {"prompt": "SECRET-QUESTION\n", "answer": {"value": "A"}}},
        {"n": "SECRET-ENTRY"},
        {
            "a": {"prompt": "SECRET-DUP", "answer": {"value": "A"}},
            "b": {"prompt": "SECRET-DUP", "answer": {"value": "B"}},
        },
    ],
)
def test_no_prompt_name_question_or_entry_is_echoed_in_the_error(prompts: object) -> None:
    """F-3 (M21-M24): INV-02 on every new raise."""
    raw = {"status": "SUCCEEDED", "pages": [{"prompts": prompts}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert "SECRET" not in str(excinfo.value)
    assert "SECRET" not in repr(excinfo.value.__cause__)


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("company main business?", True),  # outside the field-name charset, allowed here
        ("x" * 200, True),
        ("x" * 201, False),
        ("", False),
    ],
)
def test_the_prompt_name_follows_the_prompt_key_rule(name: str, ok: bool) -> None:
    """F-4 (M06/M28/M29): the name's charset and 1..200 bounds."""
    raw = {"status": "SUCCEEDED",
           "pages": [{"prompts": {name: {"prompt": "q?", "answer": {"value": "A"}}}}]}
    if ok:
        assert normalize(raw, success_statuses={"SUCCEEDED"})["prompts"]["q?"]["answer"] == "A"
    else:
        with pytest.raises(MalformedIDPOutputError) as excinfo:
            normalize(raw, success_statuses={"SUCCEEDED"})
        assert excinfo.value.reason == "unsafe_prompt_key"


def test_a_non_string_prompt_source_is_rejected() -> None:
    """F-5 (M11)."""
    raw = {"status": "SUCCEEDED",
           "pages": [{"prompts": {"n": {"prompt": "q?", "source": 7, "answer": {"value": "A"}}}}]}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize(raw, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_page"


# S-01.2 re-stamp F-1 (R2): the value-type obligation, derived from the
# declared type rather than hand-listed. `FieldValue.value` is `str | None`.
_NON_STRING_VALUES = [1, 1.5, True, ["x"], {"v": "x"}]


def test_the_declared_cell_value_type_is_str_or_none() -> None:
    from typing import get_type_hints

    from idp_regression.classifier.types import FieldValue

    assert get_type_hints(FieldValue)["value"] == str | None


@pytest.mark.parametrize("value", _NON_STRING_VALUES)
@pytest.mark.parametrize("where", ["field", "table_cell", "prompt_answer"])
def test_a_non_string_cell_value_is_refused(where: str, value: object) -> None:
    if where == "field":
        page: dict[str, object] = {"fields": {"total": {"value": value, "confidence": None}}}
    elif where == "table_cell":
        page = {"tables": {"items": [{"sku": {"value": value, "confidence": None}}]}}
    else:
        page = {"prompts": _as_documented_map(
            [{"prompt": "What?", "source": "document", "answer": {"value": value}}])}
    with pytest.raises(MalformedIDPOutputError) as excinfo:
        normalize({"status": "SUCCEEDED", "pages": [page]}, success_statuses={"SUCCEEDED"})
    assert excinfo.value.reason == "invalid_cell_value"
