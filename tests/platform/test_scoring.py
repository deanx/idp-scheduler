"""Deterministic score id + score-name derivation (ADR-0005 Decision #5, DEBT-13)."""

from __future__ import annotations

import re
import uuid

from idp_regression.platform.scoring import NAMESPACE, prompt_score_name, score_id, trace_id


def test_namespace_is_a_pinned_constant_uuid() -> None:
    # Regression pin: NAMESPACE must never change (ADR-0005 #5) — this literal
    # is the committed value. If this test needs editing, that's the bug.
    assert uuid.UUID("6f1b8c2a-6e3d-4f1a-9c7e-2b8a5d4e1f60") == NAMESPACE


def test_score_id_is_deterministic_for_the_same_inputs() -> None:
    a = score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="field:total")
    b = score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="field:total")
    assert a == b


def test_score_id_differs_across_runs_no_cross_run_overwrite() -> None:
    # N26: two runs over the same golden set must not collide.
    a = score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="gate")
    b = score_id(run_id="run-2", document_id="invoice-007.pdf", score_name="gate")
    assert a != b


def test_score_id_differs_across_documents_and_score_names() -> None:
    base = score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="gate")
    other_doc = score_id(run_id="run-1", document_id="invoice-008.pdf", score_name="gate")
    other_name = score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="field:total")
    assert base != other_doc
    assert base != other_name


def test_score_id_is_uuid5_over_namespace_and_pipe_joined_key() -> None:
    expected = str(uuid.uuid5(NAMESPACE, "run-1|invoice-007.pdf|gate"))
    assert score_id(run_id="run-1", document_id="invoice-007.pdf", score_name="gate") == expected


def test_prompt_score_name_is_16_hex_sha256_of_utf8_key() -> None:
    import hashlib

    key = "What is the vendor name?"
    expected = f"prompt:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"
    assert prompt_score_name(key) == expected


def test_prompt_score_name_matches_the_pinned_shape() -> None:
    assert re.fullmatch(r"prompt:[0-9a-f]{16}", prompt_score_name("Qual é o fornecedor?"))


def test_prompt_score_name_non_ascii_key_pinned_to_a_precomputed_digest_literal() -> None:
    """Gap 6 (TP-39): pin the exact encoding for a non-ASCII key against a
    precomputed sha256[:16] literal of the UTF-8 bytes — not a pattern
    match, and not computed inline (a hashlib-based "expected" in the
    test could silently agree with an encoding regression the same way
    the implementation would)."""
    key = "Qual é o fornecedor?"
    # sha256("Qual é o fornecedor?".encode("utf-8")).hexdigest()[:16]
    # precomputed independently of scoring.py, 2026-09-19.
    assert prompt_score_name(key) == "prompt:90fa1c7d3ca326e4"


def test_prompt_score_name_never_echoes_the_raw_prompt() -> None:
    key = "SENSITIVE PROMPT TEXT SHOULD NOT LEAK"
    assert key not in prompt_score_name(key)


def test_trace_id_is_32_hex_chars_valid_otel_trace_id() -> None:
    tid = trace_id(run_id="run-1", document_id="invoice-007.pdf")
    assert re.fullmatch(r"[0-9a-f]{32}", tid)
    assert tid != "0" * 32  # OTel forbids the all-zero trace id


def test_trace_id_is_deterministic_for_the_same_inputs() -> None:
    a = trace_id(run_id="run-1", document_id="invoice-007.pdf")
    b = trace_id(run_id="run-1", document_id="invoice-007.pdf")
    assert a == b


def test_trace_id_differs_across_runs_and_documents() -> None:
    base = trace_id(run_id="run-1", document_id="invoice-007.pdf")
    other_run = trace_id(run_id="run-2", document_id="invoice-007.pdf")
    other_doc = trace_id(run_id="run-1", document_id="invoice-008.pdf")
    assert base != other_run
    assert base != other_doc


def test_trace_id_is_uuid5_hex_over_namespace_and_pipe_joined_key() -> None:
    expected = uuid.uuid5(NAMESPACE, "run-1|invoice-007.pdf").hex
    assert trace_id(run_id="run-1", document_id="invoice-007.pdf") == expected


def test_trace_id_run_level_sentinel_for_run_status() -> None:
    # mark_run_status targets a run-level trace id — document_id="run" is
    # the pinned sentinel (DEBT-15) so it never collides with a real
    # document_id (document ids come from the golden set, never "run").
    run_level = trace_id(run_id="run-1", document_id="run")
    per_doc = trace_id(run_id="run-1", document_id="invoice-007.pdf")
    assert run_level != per_doc
