"""The pure classifier and aggregate gate (ADR-0003).

``classify(golden, actual) -> dict[str, Verdict | TableVerdict]`` and
``overall_gate(verdicts) -> "PASS" | "FAIL"`` are both pure functions with no
I/O. The module never imports from ``adapter`` / ``platform`` /
``orchestration`` and never touches the network, disk, or logging.
"""

from __future__ import annotations

from typing import Literal, cast, get_args

from idp_regression.classifier.canonical import compare_value, match_key_form
from idp_regression.classifier.types import (
    FIELD_TYPES,
    FieldValue,
    Golden,
    GoldenTable,
    MalformedActualError,
    MalformedGoldenError,
    NormalizedOutput,
    PromptValue,
    RowVerdict,
    TableVerdict,
    Verdict,
    VerdictLiteral,
    VerdictMap,
)

# Derived from the declared type (not hand-enumerated, DEBT-40/43/47): a
# seventh verdict added to VerdictLiteral is automatically accepted here.
_VALID_VERDICTS: frozenset[str] = frozenset(get_args(VerdictLiteral))

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
        rows = block.get("rows", [])
        if not isinstance(rows, list):
            raise MalformedGoldenError(f"golden table {tname!r} rows must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise MalformedGoldenError(
                    f"golden table {tname!r} has a non-mapping row "
                    f"(expected dict[str, str], got {type(row).__name__})"
                )
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


#: N28 (T-01.4.5, ADR-0005 Decision #8): the orchestration pre-run
#: structural validator over the whole golden set MUST be the classifier's
#: own N22 golden validator -- never a second validator/dialect (ADR-0005
#: #8 rejected a second `jsonschema` pass because its strictness could
#: disagree with this one). This is an additive public alias, not a copy:
#: it `is` `_validate_golden` (see
#: ``tests/classifier/test_gate.py::test_validate_golden_structure_is_validate_golden``),
#: so N28 can never silently drift from N22 as this function evolves.
validate_golden_structure = _validate_golden


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
            for cname, cell in row.items():
                if not isinstance(cell, dict):
                    raise MalformedActualError(
                        f"actual table {tname!r} row has a non-mapping cell "
                        f"at column {cname!r} (expected dict-with-'value', "
                        f"got {type(cell).__name__})"
                    )
                if "value" not in cell:
                    raise MalformedActualError(
                        f"actual table {tname!r} row cell at column {cname!r} "
                        f"is missing 'value'"
                    )
    aprompts = actual.get("prompts", {})
    if not isinstance(aprompts, dict):
        raise MalformedActualError("actual.prompts must be a mapping")
    for pname, pcell in aprompts.items():
        if not isinstance(pcell, dict):
            raise MalformedActualError(
                f"actual prompt {pname!r} must be a mapping "
                f"(expected dict, got {type(pcell).__name__})"
            )


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


# DATA-MODEL-01 §1 carries no per-column type for table rows; columns are
# compared as text. A future golden schema may add per-column types.
_TABLE_COLUMN_TYPE = "text"


def _classify_table(
    gtable: GoldenTable,
    arows: list[dict[str, FieldValue]],
) -> TableVerdict:
    key_col = gtable["match_key"]
    critical = gtable.get("critical", False)

    # Index actual rows by normalized match_key (BR8: position-independent).
    a_index: dict[str, dict[str, FieldValue]] = {}
    for row in arows:
        mk_cell = row.get(key_col)
        mk_value = mk_cell.get("value") if mk_cell else None
        if _is_empty(mk_value):
            continue
        a_index[match_key_form(cast(str, mk_value))] = row

    rows: list[RowVerdict] = []
    for grow in gtable["rows"]:
        gmk = grow.get(key_col)
        gmk_str = gmk if isinstance(gmk, str) else None
        arow = a_index.pop(match_key_form(gmk_str), None) if gmk_str else None
        if arow is None:
            rows.append(
                RowVerdict(
                    match_key=gmk_str,
                    column=None,
                    verdict="missing",
                    expected=None,
                    actual=None,
                    confidence=None,
                )
            )
            continue
        for col, gval in grow.items():
            if col == key_col:
                continue
            acell = arow.get(col)
            if acell is None or _is_empty(acell.get("value")):
                rows.append(
                    RowVerdict(
                        match_key=gmk_str,
                        column=col,
                        verdict="missing",
                        expected=gval,
                        actual=None,
                        confidence=acell.get("confidence") if acell else None,
                    )
                )
                continue
            avalue = acell.get("value")
            verdict = compare_value(_TABLE_COLUMN_TYPE, gval or "", cast(str, avalue))
            rows.append(
                RowVerdict(
                    match_key=gmk_str,
                    column=col,
                    verdict=verdict,
                    expected=gval,
                    actual=cast(str, avalue),
                    confidence=acell.get("confidence"),
                )
            )

    # Leftover actual rows (no golden counterpart) -> new_line (BR3, informational).
    for row in a_index.values():
        mk_cell = row.get(key_col)
        mk_value = mk_cell.get("value") if mk_cell else None
        rows.append(
            RowVerdict(
                match_key=mk_value,
                column=None,
                verdict="new_line",
                expected=None,
                actual=None,
                confidence=mk_cell.get("confidence") if mk_cell else None,
            )
        )

    return TableVerdict(verdict="detail", critical=critical, type=None, rows=rows)


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

    # Line items: pair rows by match_key, per-column sub-verdicts (ADR-0003, BR8).
    gtables = golden.get("tables", {})
    atables: dict[str, list[dict[str, FieldValue]]] = actual.get("tables", {})
    for tname, gtable in gtables.items():
        verdicts[tname] = _classify_table(gtable, atables.get(tname, []))

    return verdicts


def overall_gate(verdicts: VerdictMap) -> Literal["PASS", "FAIL"]:
    """Aggregate a verdict map to ``"PASS"`` or ``"FAIL"`` (ADR-0003).

    ``FAIL`` iff a ``missing`` or ``wrong_value`` verdict is ``critical: True``
    (BR2). ``wrong_format``, ``new_field``, ``new_line``, and any non-critical
    difference do not fail the gate (BR3).

    Raises :class:`MalformedActualError` (FO-5) on a verdict string outside
    ``VerdictLiteral`` — at the top level or inside a table's row detail —
    rather than silently falling through to ``PASS``. This is public API
    taking a caller-supplied :class:`VerdictMap`, so an extension that adds a
    verdict without updating every comparison here must fail loud, not gate
    green.
    """
    for key, entry in verdicts.items():
        if entry["verdict"] == "detail":
            for row in entry["rows"]:
                if row["verdict"] not in _VALID_VERDICTS:
                    raise MalformedActualError(
                        f"table {key!r} row (column {row['column']!r}) has an "
                        "unrecognised verdict"
                    )
            # Table container: fail iff a critical block has a missing/wrong_value row.
            if entry["critical"] and any(
                r["verdict"] in ("missing", "wrong_value") for r in entry["rows"]
            ):
                return "FAIL"
        elif entry["verdict"] not in _VALID_VERDICTS:
            raise MalformedActualError(f"entry {key!r} has an unrecognised verdict")
        elif entry["verdict"] in ("missing", "wrong_value") and entry["critical"]:
            return "FAIL"
    return "PASS"