"""Committed golden JSON Schema — loader (T-01.3.0, ADR-0005 Decisions #1-4).

The schema file is versioned and committed under ``schema/``. It is never
generated at runtime: this module only loads and canonically re-serializes
it. Evolution is expand/contract (ADR-0005 #4) — a new version gets its own
file (``golden_schema_v2.json``) and ``SCHEMA_VERSION``/``SCHEMA_PATH`` move
forward together; the old file is kept for the drift-hash history.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

SCHEMA_VERSION = "v1"
SCHEMA_FILENAME = "golden_schema_v1.json"


@lru_cache(maxsize=1)
def load_golden_schema() -> dict[str, Any]:
    """Load the committed golden JSON Schema as a Python dict."""
    raw = resources.files("idp_regression.platform.schema").joinpath(SCHEMA_FILENAME).read_text(
        encoding="utf-8"
    )
    schema: dict[str, Any] = json.loads(raw)
    return schema


def golden_schema_json() -> str:
    """The canonical minified JSON encoding of the committed schema.

    This is what gets sent over the wire to Langfuse (``expectedOutputSchema``)
    and hashed for the S-01.4 run-start schema-drift check (ADR-0005 #8) —
    sorted keys, no whitespace.
    """
    return json.dumps(load_golden_schema(), separators=(",", ":"), sort_keys=True)
