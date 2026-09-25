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
from typing import Literal, cast, get_args

from idp_regression.classifier.types import Golden, TableVerdict, Verdict, VerdictMap
from idp_regression.platform.types import ScoreInput

#: The per-document CI verdict published to the platform (FO-4). Named so
#: the valid set below can be derived from it, never hand-written.
GateLiteral = Literal["PASS", "FAIL"]

# Derived from the declared type (not hand-enumerated, DEBT-40/43/47 /
# mirrors classifier/gate.py's _VALID_VERDICTS, FO-5): a third gate value
# added to GateLiteral is automatically accepted here.
_VALID_GATES: frozenset[str] = frozenset(get_args(GateLiteral))

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


def _comment(verdict: Verdict | TableVerdict, include_values: bool) -> str | None:
    """The score's comment: what the platform shows beside the verdict.

    ``None`` unless the run is recording values (DEBT-18 option B's
    behaviour, reproduced exactly by ``--platform-values verdicts-only``).
    When it is, the comment is the one line a human reading a red score in
    the UI actually wants -- what was expected and what came back -- and
    nothing else; the full map is on the trace span.
    """
    if not include_values or verdict.get("verdict") == "detail":
        return None
    expected = verdict.get("expected")
    actual = verdict.get("actual")
    if verdict["verdict"] == "match":
        return f"= {expected!r}"
    return f"expected {expected!r} · actual {actual!r}"


def _gate_comment(verdicts: VerdictMap, gate: GateLiteral) -> str | None:
    """Which fields failed the gate. Field NAMES only -- never values --
    so this is safe to emit whatever `--platform-values` says."""
    if gate == "PASS":
        return None
    failing: list[str] = []
    for name, entry in verdicts.items():
        verdict = entry["verdict"]
        critical = entry["critical"]
        if verdict == "detail":
            rows = cast("TableVerdict", entry)["rows"]
            fails = critical and any(
                row["verdict"] in ("missing", "wrong_value") for row in rows
            )
        elif verdict == "wrong_format":
            fails = bool(cast("Verdict", entry).get("format_critical"))
        else:
            fails = critical and verdict in ("missing", "wrong_value")
        if fails:
            failing.append(name)
    return f"FAIL on: {', '.join(sorted(failing))}" if failing else None


def build_score_inputs(
    *,
    golden: Golden,
    verdicts: VerdictMap,
    gate: GateLiteral,
    run_id: str,
    document_id: str,
    include_values: bool = False,
) -> list[ScoreInput]:
    """CT-03: one ``field:<name>`` per golden field, one ``prompt:<16-hex>``
    per golden prompt, and exactly one ``gate`` per document (NFR N9 — no
    unbounded score write). Table-block verdicts are not individually
    scored under this contract (documented limitation, see DEBT.md).

    Raises ``ValueError`` if a golden field/prompt has no matching verdict
    entry — ``classify()`` always produces one for every golden ∪ actual
    key (CT-02), so a miss here is a caller bug, not a "missing" value to
    paper over (Atchim suggestion, scoring.py:121).

    Raises ``ValueError`` if ``gate`` is not a recognised value (FO-4):
    the type annotation is not enforced at runtime by Python, and this
    value is the per-document CI verdict published to the platform — it
    must never reach a score unchecked (mirrors FO-5 in
    ``classifier/gate.py``'s ``overall_gate``).
    """
    if gate not in _VALID_GATES:
        # INV-02: name the parameter, never interpolate the offending value
        # (it could carry extracted content — e.g. a caller passing golden
        # content through the wrong parameter by mistake).
        raise ValueError("gate must be one of the recognised gate values")

    scores: list[ScoreInput] = []

    for field_name in golden.get("fields", {}):
        name = field_score_name(field_name)
        verdict = _require_verdict(verdicts, field_name)
        scores.append(
            {
                "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
                "name": name,
                "value": _verdict_value(verdict),
                # DEBT-18 (user decision, option B): no expected/actual/
                # confidence value leaves the app — the golden lives only
                # in its Langfuse dataset item.
                "comment": _comment(verdict, include_values),
            }
        )

    for prompt_key in golden.get("prompts", {}):
        name = prompt_score_name(prompt_key)
        verdict = _require_verdict(verdicts, prompt_key, report_key=name)
        scores.append(
            {
                "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
                "name": name,
                "value": _verdict_value(verdict),
                "comment": _comment(verdict, include_values),
            }
        )

    scores.append(
        {
            "id": score_id(run_id=run_id, document_id=document_id, score_name="gate"),
            "name": "gate",
            "value": gate,
            # Names the fields that caused a FAIL -- names only, so this
            # line stays useful even under `verdicts-only`, where it is
            # the one thing that keeps a red gate self-explanatory.
            "comment": _gate_comment(verdicts, gate),
        }
    )
    return scores


def _require_verdict(
    verdicts: VerdictMap, key: str, *, report_key: str | None = None
) -> Verdict | TableVerdict:
    """``report_key`` is what the error message names instead of ``key`` —
    field names (``[A-Za-z0-9_-]``) are safe to report verbatim, but a
    prompt key is Curator-authored golden content (INV-02, REG-05) and
    must never appear in an exception message. Callers over prompts pass
    ``report_key=prompt_score_name(key)`` (the hash); field callers omit
    it and get the field name, which is fine."""
    if key not in verdicts:
        name = report_key if report_key is not None else key
        raise ValueError(f"no verdict entry for golden key {name!r} — classify() must cover it")
    return verdicts[key]


def _verdict_value(verdict: Verdict | TableVerdict) -> str:
    return str(verdict["verdict"])


