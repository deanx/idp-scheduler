"""Single-fetch content-hash golden versioning (ADR-0001, INV-04, T-01.3.2).

``golden_version`` is computed by the orchestrator as
``hash_dataset(dataset)`` over the *same* ``dataset`` object it iterates
(TOCTOU guard) — there is deliberately no ``get_golden_version`` on the
``PlatformAdapter`` interface. The encoding is canonical JSON (sorted keys,
no whitespace) so the hash is stable across runs and independent of dict
key-insertion order, but sensitive to document order and content.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def hash_dataset(dataset: list[dict[str, Any]]) -> str:
    """SHA-256 hex digest over the canonical JSON encoding of the dataset.

    ``dataset`` is a list of dataset items, each item expected to carry at
    least ``document_id`` and ``golden`` — but this function hashes whatever
    it is given verbatim (canonical encoding), so it stays a pure content
    hash with no opinion on the item shape.
    """
    canonical = json.dumps(dataset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
