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

# Pinned committed constant (ADR-0005 #5). NEVER regenerate or change this
# value — doing so would silently change every score id already written.
NAMESPACE = uuid.UUID("6f1b8c2a-6e3d-4f1a-9c7e-2b8a5d4e1f60")


def score_id(*, run_id: str, document_id: str, score_name: str) -> str:
    """Deterministic score client-id, retry-safe within a run (upsert)."""
    key = f"{run_id}|{document_id}|{score_name}"
    return str(uuid.uuid5(NAMESPACE, key))


def prompt_score_name(prompt_key: str) -> str:
    """``prompt:<16-hex sha256>`` — the raw prompt never appears in the name."""
    digest = hashlib.sha256(prompt_key.encode("utf-8")).hexdigest()
    return f"prompt:{digest[:16]}"


def field_score_name(field_name: str) -> str:
    """``field:<name>`` — the stable score-key contract (BR11, INV-03)."""
    return f"field:{field_name}"
