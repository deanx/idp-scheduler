"""CT-03 — platform score contract (score-name shape, one gate per document,
DEBT-13 prompt-derived score family).

Producer: ``idp_regression.platform.scoring.build_score_inputs``. This is
what ``run_eval`` (S-01.4) will feed to ``PlatformAdapter.write_scores``.
"""

from __future__ import annotations

import re

import pytest

from idp_regression.classifier.types import Golden, VerdictMap
from idp_regression.platform.scoring import build_score_inputs

FIELD_SCORE_RE = re.compile(r"^field:.+$")
PROMPT_SCORE_RE = re.compile(r"^prompt:[0-9a-f]{16}$")


def _golden() -> Golden:
    return {
        "document_id": "invoice-007.pdf",
        "fields": {
            "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
            "total": {"value": "1250.00", "type": "number", "critical": True},
        },
        "prompts": {
            "What is the vendor name?": {"answer": "Acme Corp", "critical": False},
        },
    }


def _verdicts() -> VerdictMap:
    return {
        "invoice_number": {
            "verdict": "match",
            "expected": "INV-1",
            "actual": "INV-1",
            "confidence": 0.99,
            "critical": True,
            "type": "id",
        },
        "total": {
            "verdict": "wrong_value",
            "expected": "1250.00",
            "actual": "1150.00",
            "confidence": 0.8,
            "critical": True,
            "type": "number",
        },
        "What is the vendor name?": {
            "verdict": "match",
            "expected": "Acme Corp",
            "actual": "Acme Corp",
            "confidence": 0.9,
            "critical": False,
            "type": None,
        },
    }


def test_one_field_score_per_golden_field() -> None:
    scores = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="FAIL",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )
    field_scores = [s for s in scores if FIELD_SCORE_RE.match(s["name"])]
    names = {s["name"] for s in field_scores}
    assert names == {"field:invoice_number", "field:total"}


def test_one_prompt_score_per_golden_prompt_with_16_hex_shape() -> None:
    scores = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="FAIL",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )
    prompt_scores = [s for s in scores if PROMPT_SCORE_RE.match(s["name"])]
    assert len(prompt_scores) == 1
    # the raw prompt text never appears as (or inside) a score name
    for s in prompt_scores:
        assert "What is the vendor name?" not in s["name"]


def test_exactly_one_gate_score_per_document() -> None:
    scores = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )
    gate_scores = [s for s in scores if s["name"] == "gate"]
    assert len(gate_scores) == 1
    assert gate_scores[0]["value"] == "PASS"


def test_score_ids_are_deterministic_and_run_scoped() -> None:
    scores_a = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )
    scores_b = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="PASS",
        run_id="run-2",
        document_id="invoice-007.pdf",
    )
    ids_a = {s["id"] for s in scores_a}
    ids_b = {s["id"] for s in scores_b}
    assert ids_a.isdisjoint(ids_b)  # N26: distinct run_id -> distinct score ids


def test_score_count_formula_n9() -> None:
    """NFR N9: exactly one field:<name> per golden field + one prompt score
    per golden prompt + one gate — no unbounded score write."""
    golden = _golden()
    scores = build_score_inputs(
        golden=golden,
        verdicts=_verdicts(),
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )
    expected_count = len(golden["fields"]) + len(golden.get("prompts", {})) + 1
    assert len(scores) == expected_count


def test_a_missing_verdict_raises_instead_of_silently_writing_missing() -> None:
    """Atchim suggestion (scoring.py:121): a golden field with no matching
    verdict entry is a caller bug (classify() always produces a verdict
    for every golden ∪ actual key) — loud failure, not a silent
    "missing" value masking the real problem."""
    golden = _golden()
    verdicts = _verdicts()
    del verdicts["total"]  # simulate a caller that forgot to classify a field

    with pytest.raises(ValueError, match="total"):
        build_score_inputs(
            golden=golden,
            verdicts=verdicts,
            gate="FAIL",
            run_id="run-1",
            document_id="invoice-007.pdf",
        )


# --- DEBT-18 option B: no expected/actual/confidence value leaves the app --

_EXPECTED_SENTINEL = "SENTINEL-EXPECTED-4f8c1e"
_ACTUAL_SENTINEL = "SENTINEL-ACTUAL-9b2d7a"


def test_score_comment_is_always_none() -> None:
    """DEBT-18 option B: the minimal, value-free shape is no comment at
    all — CT-03 pins this so a future change can't silently reintroduce
    expected/actual/confidence into a score payload."""
    scores = build_score_inputs(
        golden=_golden(),
        verdicts=_verdicts(),
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )

    for score in scores:
        assert score.get("comment") is None


def test_no_sentinel_expected_or_actual_value_appears_in_any_score_input() -> None:
    """Plants distinctive sentinels as expected/actual in the verdicts
    and asserts neither ever reaches a ScoreInput (id/name/value/comment)."""
    golden = _golden()
    verdicts = _verdicts()
    verdicts["total"] = {
        "verdict": "wrong_value",
        "expected": _EXPECTED_SENTINEL,
        "actual": _ACTUAL_SENTINEL,
        "confidence": 0.42,
        "critical": True,
        "type": "number",
    }

    scores = build_score_inputs(
        golden=golden, verdicts=verdicts, gate="FAIL", run_id="run-1", document_id="doc-0"
    )

    for score in scores:
        for value in score.values():
            text = str(value)
            assert _EXPECTED_SENTINEL not in text
            assert _ACTUAL_SENTINEL not in text
            assert "0.42" not in text
