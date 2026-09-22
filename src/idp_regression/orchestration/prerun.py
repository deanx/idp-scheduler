"""The pre-run chain (T-01.4.11 schema-drift, T-01.4.2 empty-set,
T-01.4.5 N28 structural validation, T-01.4.6 golden-version + run-id).

Pinned order (`docs/specs/S-01.4-KICKOFF.md`, ADR-0005 Decision #8),
run over the single `get_dataset()` fetch and before any IDP call::

    get_dataset()
       -> check_schema_drift   (T-01.4.11, canonical-JSON sha256)
            -> check_empty_set       (T-01.4.2)
                 -> validate_golden_set   (T-01.4.5, N28, malformed_golden)
                      -> hash_dataset(dataset["items"]) + generate_run_id()  (T-01.4.6)
                           -> first IDP call

Each guard raises `RunAborted` (`errors.py`) and writes NO platform
marker: ADR-0004 #14's `run_status=aborted` marker only makes sense once
a run exists, and the kickoff is explicit that "a pre-run abort writes
no run_status marker, because no run exists yet." `facade.py` is the
only caller and the only place these guards' exceptions are converted
into a process exit code.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.classifier.gate import validate_golden_structure
from idp_regression.classifier.types import MalformedGoldenError
from idp_regression.orchestration.errors import RunAborted
from idp_regression.platform.schema import load_golden_schema
from idp_regression.platform.types import Dataset

logger = logging.getLogger(__name__)

_UNKNOWN_DOCUMENT_ID = "<unknown>"


def _canonical_hash(obj: dict[str, Any]) -> str:
    """sha256 hex digest of the canonical JSON encoding (sorted keys, no
    whitespace) of a PARSED object -- never raw bytes. The evaluation
    platform stores the schema as JSONB and may reorder keys on read, so
    a raw-bytes hash would drift on key order alone, independent of
    content (ADR-0005 Decision #8; N24 -- this module names no vendor)."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_schema_drift(dataset: Dataset) -> None:
    """ADR-0005 Decision #8: compare the dataset's `expectedOutputSchema`
    against the committed schema file (`platform/schema/golden_schema_v1.json`),
    both canonicalised then hashed. Mismatch OR an absent schema aborts
    `schema_drift` -- before any IDP call, and before the empty-set check
    (TP-40: an empty dataset whose schema ALSO drifted reports
    `schema_drift`, not `empty_set`, because this guard runs first).
    Both hashes (or the literal `absent`) are logged for observability;
    neither is a golden value, so this is INV-02-safe by construction."""
    committed_hash = _canonical_hash(load_golden_schema())
    actual_schema = dataset.get("expected_output_schema")
    if actual_schema is None:
        logger.error("schema_drift committed=%s actual=absent", committed_hash)
        raise RunAborted("schema_drift", "golden dataset schema is absent")

    actual_hash = _canonical_hash(actual_schema)
    if actual_hash != committed_hash:
        logger.error(
            "schema_drift committed=%s actual=%s",
            committed_hash,
            actual_hash,
        )
        raise RunAborted(
            "schema_drift", "golden dataset schema does not match the committed schema"
        )


def check_empty_set(dataset: Dataset) -> None:
    """A4: `dataset["items"] == []` aborts `empty_set` -- distinct from
    `dataset_fetch_failed` (a network/404/auth failure on `get_dataset`
    itself never reaches this function; `facade.py` maps that directly).
    Runs AFTER `check_schema_drift` (TP-40 ordering)."""
    if not dataset["items"]:
        raise RunAborted("empty_set", "golden dataset has no items")


def _first_structural_issue_path(golden: object) -> str:
    """Best-effort JSON-path hint for the `malformed_golden` LOG LINE
    only -- it never decides pass/fail (that stays wholly owned by
    `validate_golden_structure`, the reused N22 alias, called by
    `validate_golden_set` below). Walks only KEY NAMES and TYPE-NAME
    membership, never a golden `value`, so it cannot leak golden content
    (INV-02, TP-46) even in the worst case where its own read of the
    shape has drifted from `_validate_golden`'s (a risk explicitly
    accepted here rather than re-deriving the pass/fail decision itself
    -- see the module docstring on N28 reuse)."""
    if not isinstance(golden, dict):
        return "$"
    fields = golden.get("fields")
    if not isinstance(fields, dict) or not fields:
        return "$.fields"
    for name, spec in fields.items():
        if not isinstance(name, str) or not name:
            return "$.fields[<invalid-name>]"
        if not isinstance(spec, dict):
            return f"$.fields.{name}"
        if spec.get("type") not in {"number", "date", "id", "text"}:
            return f"$.fields.{name}.type"
        if "value" not in spec:
            return f"$.fields.{name}.value"
        if not isinstance(spec.get("critical", False), bool):
            return f"$.fields.{name}.critical"

    tables = golden.get("tables", {})
    if isinstance(tables, dict):
        for tname, block in tables.items():
            if (
                not isinstance(block, dict)
                or not isinstance(block.get("match_key"), str)
                or not block.get("match_key")
            ):
                return f"$.tables.{tname}.match_key"
            rows = block.get("rows", [])
            if not isinstance(rows, list):
                return f"$.tables.{tname}.rows"
            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    return f"$.tables.{tname}.rows[{idx}]"
            if not isinstance(block.get("critical", False), bool):
                return f"$.tables.{tname}.critical"

    prompts = golden.get("prompts", {})
    if isinstance(prompts, dict):
        for pname, pspec in prompts.items():
            if not isinstance(pspec, dict) or "answer" not in pspec:
                return f"$.prompts.{pname}.answer"
            if not isinstance(pspec.get("critical", False), bool):
                return f"$.prompts.{pname}.critical"

    return "$"


def validate_golden_set(dataset: Dataset) -> None:
    """N28 (pre-run structural validation, TP-46): validate every golden
    item BEFORE any IDP call, reusing the classifier's own N22 validator
    (`validate_golden_structure`, an additive `is`-identical alias of
    `classifier.gate._validate_golden` -- never a second `jsonschema`
    dialect, ADR-0005 Decision #8). The first malformed item aborts
    `malformed_golden`; the log line carries `document_id` + a
    best-effort JSON path and NEVER the golden value (INV-02)."""
    for item in dataset["items"]:
        try:
            validate_golden_structure(item.get("golden"))
        except MalformedGoldenError:
            document_id = item.get("document_id", _UNKNOWN_DOCUMENT_ID)
            path = _first_structural_issue_path(item.get("golden"))
            logger.error(
                "malformed_golden document_id=%s path=%s",
                sanitize_for_log(str(document_id)),
                sanitize_for_log(path),
            )
            raise RunAborted(
                "malformed_golden", "a golden item failed N28 structural validation"
            ) from None
