"""The pure classifier and aggregate gate (ADR-0003).

``classify(golden, actual) -> dict[str, Verdict | TableVerdict]`` and
``overall_gate(verdicts) -> "PASS" | "FAIL"`` are both pure functions with no
I/O. The module never imports from ``adapter`` / ``platform`` /
``orchestration`` and never touches the network, disk, or logging.
"""

from __future__ import annotations

from typing import Literal, cast

from idp_regression.classifier.canonical import compare_value
from idp_regression.classifier.types import (
    FIELD_TYPES,
    FieldValue,
    Golden,
    MalformedActualError,
    MalformedGoldenError,
    NormalizedOutput,
    PromptValue,
    Verdict,
    VerdictMap,
)

# Prompt answers are free-form text (DATA-MODEL-01 §1 has no per-prompt type).
_PROMPT_TYPE = "text"


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _validate_golden(golden: Golden) -> None:
    if not isinstance(golden, dict):
        raise MalformedGoldenError("golden must be a mapping")
    fields = golden.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise MalformedGoldenError("golden.fields must be a non-empty mapping")
    for name, spec in fields.items():
        if not isinstance(name, str) or not name:
            raise MalformedGoldenError("golden field names must be non-empty strings")
        if not isinstance(spec, dict):
            raise MalformedGoldenError(f"golden field {name!r} must be a mapping")
        ftype = spec.get("type")
        if ftype not in FIELD_TYPES:
            raise MalformedGoldenError(
                f"golden field {name!r} has invalid or missing type {ftype!r}"
            )
        if "value" not in spec:
            raise MalformedGoldenError(f"golden field {name!r} is missing 'value'")
        if not isinstance(spec.get("critical", False), bool):
            raise MalformedGoldenError(f"golden field {name!r} critical must be bool")

    tables = golden.get("tables", {})
    if not isinstance(tables, dict):
        raise MalformedGoldenError("golden.tables must be a mapping")
    for tname, block in tables.items():
        if not isinstance(block, dict):
            raise MalformedGoldenError(f"golden table {tname!r} must be a mapping")
        if not isinstance(block.get("match_key"), str) or not block["match_key"]:
            raise MalformedGoldenError(f"golden table {tname!r} needs a non-empty match_key")
        if not isinstance(block.get("rows", []), list):
            raise MalformedGoldenError(f"golden table {tname!r} rows must be a list")
        if not isinstance(block.get("critical", False), bool):
            raise MalformedGoldenError(f"golden table {tname!r} critical must be bool")

    prompts = golden.get("prompts", {})
    if not isinstance(prompts, dict):
        raise MalformedGoldenError("golden.prompts must be a mapping")
    for pname, pspec in prompts.items():
        if not isinstance(pspec, dict):
            raise MalformedGoldenError(f"golden prompt {pname!r} must be a mapping")
        if "answer" not in pspec:
            raise MalformedGoldenError(f"golden prompt {pname!r} is missing 'answer'")
        if not isinstance(pspec.get("critical", False), bool):
            raise MalformedGoldenError(f"golden prompt {pname!r} critical must be bool")


def _validate_actual(actual: NormalizedOutput) -> None:
    if not isinstance(actual, dict):
        raise MalformedActualError("actual must be a mapping")
    afields = actual.get("fields", {})
    if not isinstance(afields, dict):
        raise MalformedActualError("actual.fields must be a mapping")
    for name, cell in afields.items():
        if not isinstance(cell, dict):
            raise MalformedActualError(f"actual field {name!r} must be a mapping")
        if "value" not in cell:
            raise MalformedActualError(f"actual field {name!r} is missing 'value'")
    atables = actual.get("tables", {})
    if not isinstance(atables, dict):
        raise MalformedActualError("actual.tables must be a mapping")
    for tname, rows in atables.items():
        if not isinstance(rows, list):
            raise MalformedActualError(f"actual table {tname!r} must be a list of rows")
        for row in rows:
            if not isinstance(row, dict):
                raise MalformedActualError(f"actual table {tname!r} has a non-mapping row")
    aprompts = actual.get("prompts", {})
    if not isinstance(aprompts, dict):
        raise MalformedActualError("actual.prompts must be a mapping")


def _classify_field(
    name: str,
    gvalue: str | None,
    ftype: str,
    critical: bool,
    acell: FieldValue | None,
) -> Verdict:
    if acell is None:
        return Verdict(
            verdict="missing",
            expected=gvalue,
            actual=None,
            confidence=None,
            critical=critical,
            type=ftype,
        )
    avalue = acell.get("value")
    if _is_empty(avalue):
        return Verdict(
            verdict="missing",
            expected=gvalue,
            actual=None,
            confidence=acell.get("confidence"),
            critical=critical,
            type=ftype,
        )
    verdict = compare_value(ftype, gvalue or "", cast(str, avalue))
    return Verdict(
        verdict=verdict,
        expected=gvalue,
        actual=cast(str, avalue),
        confidence=acell.get("confidence"),
        critical=critical,
        type=ftype,
    )


def _classify_prompt(
    name: str,
    ganswer: str | None,
    critical: bool,
    acell: PromptValue | None,
) -> Verdict:
    field_value: FieldValue | None = None
    if acell is not None:
        field_value = FieldValue(
            value=acell.get("answer"),
            confidence=acell.get("confidence"),
        )
    return _classify_field(name, ganswer, _PROMPT_TYPE, critical, field_value)


def classify(golden: Golden, actual: NormalizedOutput) -> VerdictMap:
    """Classify each field/prompt/table of ``actual`` against ``golden``.

    Returns a verdict map keyed by the union of golden field names, actual
    field names, table names, and prompt keys (ADR-0003 API contract). Pure:
    no I/O. Raises a typed :class:`ClassifierError` on malformed input (NFR N22).
    """
    _validate_golden(golden)
    _validate_actual(actual)

    gfields = golden["fields"]
    afields: dict[str, FieldValue] = actual.get("fields", {})
    verdicts: VerdictMap = {}

    for name, spec in gfields.items():
        verdicts[name] = _classify_field(
            name,
            spec.get("value"),
            spec["type"],
            spec.get("critical", False),
            afields.get(name),
        )

    for name, cell in afields.items():
        if name not in gfields:
            verdicts[name] = Verdict(
                verdict="new_field",
                expected=None,
                actual=cell.get("value"),
                confidence=cell.get("confidence"),
                critical=False,
                type=None,
            )

    # Prompts compare like text fields (ADR-0003).
    gprompts = golden.get("prompts", {})
    aprompts: dict[str, PromptValue] = actual.get("prompts", {})
    for key, pspec in gprompts.items():
        verdicts[key] = _classify_prompt(
            key,
            pspec.get("answer"),
            pspec.get("critical", False),
            aprompts.get(key),
        )
    for key, pcell in aprompts.items():
        if key not in gprompts:
            verdicts[key] = Verdict(
                verdict="new_field",
                expected=None,
                actual=pcell.get("answer"),
                confidence=pcell.get("confidence"),
                critical=False,
                type=None,
            )

    return verdicts


def overall_gate(verdicts: VerdictMap) -> Literal["PASS", "FAIL"]:
    """Aggregate a verdict map to ``"PASS"`` or ``"FAIL"`` (ADR-0003).

    ``FAIL`` iff a ``missing`` or ``wrong_value`` verdict is ``critical: True``
    (BR2). ``wrong_format``, ``new_field``, ``new_line``, and any non-critical
    difference do not fail the gate (BR3).
    """
    for entry in verdicts.values():
        if entry["verdict"] == "detail":
            # Table container: fail iff a critical block has a missing/wrong_value row.
            if entry["critical"] and any(
                r["verdict"] in ("missing", "wrong_value") for r in entry["rows"]
            ):
                return "FAIL"
        elif entry["verdict"] in ("missing", "wrong_value") and entry["critical"]:
            return "FAIL"
    return "PASS"