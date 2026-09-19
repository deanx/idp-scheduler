"""Deterministic score identity + score-name derivation.

ADR-0005 Decision #5 (replaces the superseded `(run_name, document_id,
field_name)` idempotency key): ``score.id = uuid5(NAMESPACE, run_id|
document_id|score_name)``. ``NAMESPACE`` is a committed, pinned UUID
constant — never generated at runtime, never changed. ``run_id`` is a
unique id generated per invocation (not ``run_name``), so two runs under
the same ``run_name`` never collide (N26).

DEBT-13 / ADR-0002 amendment: prompt-derived score names never carry the
raw prompt string (it may be arbitrary Curator-authored text) — instead
``prompt:<16-hex sha256 of the UTF-8 prompt key>``, first 16 hex chars,
no normalization.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from idp_regression.classifier.types import Golden
from idp_regression.platform.types import ScoreInput

# Pinned committed constant (ADR-0005 #5). NEVER regenerate or change this
# value — doing so would silently change every score id already written.
NAMESPACE = uuid.UUID("6f1b8c2a-6e3d-4f1a-9c7e-2b8a5d4e1f60")


def score_id(*, run_id: str, document_id: str, score_name: str) -> str:
    """Deterministic score client-id, retry-safe within a run (upsert)."""
    key = f"{run_id}|{document_id}|{score_name}"
    return str(uuid.uuid5(NAMESPACE, key))


#: Sentinel document_id for the run-level trace (mark_run_status, DEBT-15).
#: Never a real document_id (those come from the golden set).
RUN_LEVEL_TRACE_SENTINEL = "run"


def trace_id(*, run_id: str, document_id: str) -> str:
    """Deterministic per-document OTel trace id (32 lowercase hex chars).

    Langfuse's ``POST /api/public/scores`` requires exactly one of
    ``traceId``/``sessionId``/``datasetRunId`` on every score (live-probed,
    2026-09-19) — a score with none of those is rejected 400, even though
    the DoD's original wording didn't call this out. A score may target a
    trace that was never ingested (live-confirmed), so this id is safe to
    use even before/without T-01.3.10a's OTLP trace export.
    """
    key = f"{run_id}|{document_id}"
    return uuid.uuid5(NAMESPACE, key).hex


def prompt_score_name(prompt_key: str) -> str:
    """``prompt:<16-hex sha256>`` — the raw prompt never appears in the name."""
    digest = hashlib.sha256(prompt_key.encode("utf-8")).hexdigest()
    return f"prompt:{digest[:16]}"


def field_score_name(field_name: str) -> str:
    """``field:<name>`` — the stable score-key contract (BR11, INV-03)."""
    return f"field:{field_name}"


def build_score_inputs(
    *,
    golden: Golden,
    verdicts: object,
    gate: Literal["PASS", "FAIL"],
    run_id: str,
    document_id: str,
) -> list[ScoreInput]:
    """CT-03: one ``field:<name>`` per golden field, one ``prompt:<16-hex>``
    per golden prompt, and exactly one ``gate`` per document (NFR N9 — no
    unbounded score write). Table-block verdicts are not individually
    scored under this contract (documented limitation, see DEBT.md).
    """
    verdict_map = verdicts if isinstance(verdicts, dict) else {}
    scores: list[ScoreInput] = []

    for field_name in golden.get("fields", {}):
        verdict = verdict_map.get(field_name)
        name = field_score_name(field_name)
        scores.append(
            {
                "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
                "name": name,
                "value": _verdict_value(verdict),
                "comment": _verdict_comment(verdict),
            }
        )

    for prompt_key in golden.get("prompts", {}):
        verdict = verdict_map.get(prompt_key)
        name = prompt_score_name(prompt_key)
        scores.append(
            {
                "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
                "name": name,
                "value": _verdict_value(verdict),
                "comment": _verdict_comment(verdict),
            }
        )

    scores.append(
        {
            "id": score_id(run_id=run_id, document_id=document_id, score_name="gate"),
            "name": "gate",
            "value": gate,
            "comment": None,
        }
    )
    return scores


def _verdict_value(verdict: object) -> str:
    if isinstance(verdict, dict) and "verdict" in verdict:
        value = verdict["verdict"]
        return str(value)
    return "missing"


def _verdict_comment(verdict: object) -> str | None:
    if not isinstance(verdict, dict):
        return None
    expected = verdict.get("expected")
    actual = verdict.get("actual")
    confidence = verdict.get("confidence")
    return f"expected={expected!r} actual={actual!r} confidence={confidence!r}"
