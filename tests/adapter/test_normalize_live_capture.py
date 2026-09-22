"""REG-11 positive pin — ``normalize()`` over the FIRST REAL captured IDP
response (``tests/fixtures/live/seed-001-clean.raw.json``, commit
``0c82a02``), proving the live path works end to end after the ADR-0002 A11
fix. This is the test the regression register names as required pin (1).

Companion negative pins for D1 (the fail-open envelope defect) and D2 (the
fail-open confidence-scale defect) live in ``test_normalize.py`` beside the
rest of the untrusted-input suite; this file is deliberately narrow — one
fixture, one job: does the real shape actually come out right.
"""

from __future__ import annotations

import json
import pathlib

from idp_regression.adapter.normalize import normalize

LIVE_FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent.parent / "fixtures" / "live" / "seed-001-clean.raw.json"
    ).read_text()
)

EXPECTED_FIELD_NAMES = {
    "bill_to",
    "currency",
    "invoice_date",
    "invoice_number",
    "po_number",
    "subtotal",
    "tax",
    "total",
    "vendor_name",
}


def test_live_capture_fixture_shape_is_the_real_top_level_wire_contract() -> None:
    # R-2 (2026-09-22 REQUEST CHANGES round). The pinned VALUES above don't
    # by themselves pin the ENVELOPE SHAPE: re-wrapping this exact fixture
    # into the legacy `pages[]` envelope leaves every value-pinning test in
    # this file green, because `normalize()` still walks a single logical
    # page either way — so a later "tidy up the fixture" pass could quietly
    # reshape it back to `pages[]` and delete the only real-shape coverage
    # in the project without a single test noticing. This is the wire
    # contract (ADR-0002 A11): assert the shape directly, not just what
    # comes out of it.
    assert "pages" not in LIVE_FIXTURE, (
        "the live capture must stay in the real top-level 'fields'/'tables' "
        "shape — reshaping it into 'pages[]' would delete the only "
        "real-wire-shape coverage in the project while every value-pinning "
        "test in this file stays green"
    )
    assert {"fields", "tables"} <= set(LIVE_FIXTURE)


def test_normalize_over_the_live_capture_does_not_raise() -> None:
    normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})


def test_normalize_over_the_live_capture_returns_all_nine_fields() -> None:
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    assert set(out["fields"]) == EXPECTED_FIELD_NAMES


def test_normalize_over_the_live_capture_preserves_field_values() -> None:
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    assert out["fields"]["invoice_number"]["value"] == "INV-1001"
    assert out["fields"]["total"]["value"] == "87.48"
    assert out["fields"]["vendor_name"]["value"] == "Acme Office Supplies"


def test_normalize_over_the_live_capture_converts_confidence_score_to_0_1_scale() -> None:
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    assert out["fields"]["invoice_number"]["confidence"] == 0.99
    assert out["fields"]["currency"]["confidence"] == 0.95


def test_normalize_over_the_live_capture_returns_both_line_items_rows() -> None:
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    rows = out["tables"]["line_items"]
    assert len(rows) == 2
    assert rows[0]["sku"]["value"] == "A-100"
    assert rows[1]["sku"]["value"] == "B-200"


def test_normalize_over_the_live_capture_preserves_row_cell_confidences() -> None:
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    rows = out["tables"]["line_items"]
    for row in rows:
        for cell in row.values():
            assert cell["confidence"] == 0.99


def test_normalize_over_the_live_capture_has_no_prompts() -> None:
    # This action returns none — a documented, still-unverified unknown
    # (ADR-0002 A11), not something this test should assume never happens.
    out = normalize(LIVE_FIXTURE, success_statuses={"SUCCEEDED"})
    assert out["prompts"] == {}
