"""The pure classifier and aggregate gate (ADR-0003).

``classify(golden, actual) -> dict[str, Verdict | TableVerdict]`` and
``overall_gate(verdicts) -> "PASS" | "FAIL"`` are both pure functions with no
I/O. The module never imports from ``adapter`` / ``platform`` /
``orchestration`` and never touches the network, disk, or logging.
"""

from __future__ import annotations

from typing import Literal, cast, get_args

from idp_regression.classifier.canonical import compare_value, is_empty, match_key_form
from idp_regression.classifier.scorers import pinned_file_scorer, regression_scorer
from idp_regression.classifier.scoring import (
    ClassifyFn,
    ScoreContext,
    Scorer,
    ScoreResult,
)
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
    RowVerdictLiteral,
    TableVerdict,
    Verdict,
    VerdictLiteral,
    VerdictMap,
)

# Derived from the declared type (not hand-enumerated, DEBT-40/43/47): a
# seventh verdict added to VerdictLiteral is automatically accepted here.
_VALID_VERDICTS: frozenset[str] = frozenset(get_args(VerdictLiteral))
#: The six a table ROW may carry -- `new_table` is not one of them.
_VALID_ROW_VERDICTS: frozenset[str] = frozenset(get_args(RowVerdictLiteral))


def _as_row_verdict(
    verdict: VerdictLiteral, *, tname: str | None, column: str | None
) -> RowVerdictLiteral:
    """Narrow a scorer's verdict to the six a table row may carry.

    A `Scorer` returns a `VerdictLiteral`, which since D1a includes
    `new_table` -- a statement about a table's EXISTENCE, meaningless for
    a single cell. A custom scorer returning it for a cell is a scorer
    bug, and the fail-closed posture this module already takes for an
    unrecognised verdict (FO-5 in `overall_gate`) applies for the same
    reason: a verdict the row vocabulary does not know must not be
    written into a row where nothing downstream would question it.

    mypy catches this statically for the shipped scorers; this guard is
    for the ones loaded from a spec at runtime.
    """
    if verdict not in _VALID_ROW_VERDICTS:
        location = f"table {tname!r} column {column!r}" if tname else f"column {column!r}"
        raise MalformedActualError(
            f"a scorer returned a verdict that is not valid for a table row at {location}"
        )
    return cast("RowVerdictLiteral", verdict)

# Prompt answers are free-form text (DATA-MODEL-01 §1 has no per-prompt type).
_PROMPT_TYPE = "text"


def _as_result(returned: VerdictLiteral | ScoreResult) -> ScoreResult:
    """Normalise either scorer return shape. Harness-internal: a scorer
    author never calls this, which is why it is not in `scoring.py`."""
    if isinstance(returned, ScoreResult):
        return returned
    return ScoreResult(verdict=returned)


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
        if not isinstance(spec.get("format_critical", False), bool):
            raise MalformedGoldenError(
                f"golden field {name!r} format_critical must be bool"
            )

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

    # Fail-open #6 (ADR-0003 residual note): classify() keys fields, tables,
    # and prompts into ONE verdict-map namespace, and a later loop's write
    # silently overwrites an earlier one's on a name collision -- a critical
    # field's wrong_value can vanish behind a same-named table's "detail"
    # verdict and the gate goes green. Fail closed here instead: a golden
    # whose field names, table names, and prompt keys are not pairwise
    # disjoint is malformed. Check all three pairings, not just the
    # field/table route the collision was first reproduced on.
    field_names = set(fields.keys())
    table_names = set(tables.keys())
    prompt_names = set(prompts.keys())
    for a_label, a_names, b_label, b_names in (
        ("field", field_names, "table", table_names),
        ("field", field_names, "prompt", prompt_names),
        ("table", table_names, "prompt", prompt_names),
    ):
        collisions = a_names & b_names
        if collisions:
            # INV-02: name the colliding key only -- never a value.
            key = sorted(collisions)[0]
            raise MalformedGoldenError(
                f"golden {a_label}/{b_label} name collision on {key!r}: "
                "field, table, and prompt keys share one verdict-map "
                "namespace and must be pairwise disjoint"
            )


#: N28 (T-01.4.5, ADR-0005 Decision #8): the orchestration pre-run
#: structural validator over the whole golden set MUST be the classifier's
#: own N22 golden validator -- never a second validator/dialect (ADR-0005
#: #8 rejected a second `jsonschema` pass because its strictness could
#: disagree with this one). This is an additive public alias, not a copy:
#: it `is` `_validate_golden` (see
#: ``tests/classifier/test_validation.py::test_validate_golden_structure_is_validate_golden``),
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
    format_critical: bool = False,
    scorer: Scorer = regression_scorer,
    kind: Literal["field", "prompt", "table_column"] = "field",
    source: str | None = None,
) -> Verdict:
    """Build one leaf `Verdict`. The verdict WORD is the `scorer`'s call
    and nothing else here second-guesses it; this function only assembles
    the context the scorer reads and the `Verdict` the gate reads.

    `actual` on the returned Verdict is normalised to `None` when nothing
    was read, whatever the scorer decided the verdict is -- the verdict
    map is also what `show_run`/the run artifact display, and `""` and
    `None` should not both appear there for "nothing".
    """
    avalue = acell.get("value") if acell is not None else None
    confidence = acell.get("confidence") if acell is not None else None
    result = _as_result(
        scorer(
            ScoreContext(
                name=name,
                kind=kind,
                field_type=ftype,
                expected=gvalue,
                actual=avalue,
                confidence=confidence,
                critical=critical,
                format_critical=format_critical,
                source=source,
            )
        )
    )
    return Verdict(
        verdict=result.verdict,
        expected=gvalue,
        actual=None if is_empty(avalue) else cast(str, avalue),
        confidence=confidence,
        # OR, never replace: a scorer may make a field mandatory, never
        # make a mandatory field optional (see `scorers.ScoreResult`).
        critical=critical or result.critical,
        format_critical=format_critical or result.format_critical,
        type=ftype,
    )


def _classify_prompt(
    name: str,
    ganswer: str | None,
    critical: bool,
    acell: PromptValue | None,
    scorer: Scorer = regression_scorer,
) -> Verdict:
    field_value: FieldValue | None = None
    if acell is not None:
        field_value = FieldValue(
            value=acell.get("answer"),
            confidence=acell.get("confidence"),
        )
    return _classify_field(
        name,
        ganswer,
        _PROMPT_TYPE,
        critical,
        field_value,
        scorer=scorer,
        kind="prompt",
        # IDP's own `source` for a prompt answer: carried to the scorer
        # (a scorer may want it), never to the Verdict, which stays the
        # CT-02 shape.
        source=acell.get("source") if acell is not None else None,
    )


# DATA-MODEL-01 §1 carries no per-column type for table rows; columns are
# compared as text. A future golden schema may add per-column types.
_TABLE_COLUMN_TYPE = "text"


def _row_affinity(
    grow: dict[str, str],
    arow: dict[str, FieldValue],
    key_col: str,
) -> int:
    """Count the non-key columns of ``grow`` that ``arow`` matches exactly.

    Used only to choose between several actual rows sharing one normalized
    ``match_key`` (DEBT-09). Higher is a better pairing.
    """
    score = 0
    for col, gval in grow.items():
        if col == key_col:
            continue
        acell = arow.get(col)
        if acell is None or is_empty(acell.get("value")):
            continue
        avalue = cast(str, acell.get("value"))
        if compare_value(_TABLE_COLUMN_TYPE, gval or "", avalue) == "match":
            score += 1
    return score


def _take_candidate(
    a_index: dict[str, list[dict[str, FieldValue]]],
    grow: dict[str, str],
    key_col: str,
    gmk_str: str | None,
) -> dict[str, FieldValue] | None:
    """Consume one actual row for ``grow``'s match_key, or return ``None``.

    With a single candidate (the overwhelmingly common case, and the only
    one the N2 perf benchmark exercises) this is a plain pop -- no column
    comparison is done. With several -- a match_key repeated across lines,
    e.g. a split shipment (DEBT-09) -- the candidate matching the most of
    this golden row's non-key columns wins, earliest-in-document-order
    breaking a tie. Choosing by content rather than by position keeps BR8's
    "row reordering is not a diff" true *within* a duplicate-key group too;
    pairing positionally there would report two `wrong_value`s for two rows
    that merely arrived swapped.
    """
    if gmk_str is None:
        return None
    candidates = a_index.get(match_key_form(gmk_str))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates.pop()
    best = max(
        range(len(candidates)),
        key=lambda i: (_row_affinity(grow, candidates[i], key_col), -i),
    )
    return candidates.pop(best)


def _classify_table(
    gtable: GoldenTable,
    arows: list[dict[str, FieldValue]],
    scorer: Scorer = regression_scorer,
) -> TableVerdict:
    key_col = gtable["match_key"]
    critical = gtable.get("critical", False)
    escalated = False

    # Index actual rows by normalized match_key (BR8: position-independent).
    # DEBT-09 (decided 2026-09-24): a duplicate normalized match_key is
    # ORDINARY INVOICE DATA -- one SKU can legitimately appear on two lines
    # (a split shipment). Every same-keyed actual row is therefore KEPT, as
    # a candidate list in document order, and a golden row consumes exactly
    # one candidate. The previous behaviour (`a_index[key] = row`, last-
    # write-wins) silently dropped all but the last, which manufactured
    # both a spurious `wrong_value` (golden row 1 paired against the
    # surviving row's values) and a spurious `missing` (golden row 2 found
    # an empty index) on a document the adapter had extracted correctly --
    # observed live on `inv-003-table-heavy.pdf`, run `baseline-1.0.0`.
    #
    # This no longer masks the adapter's pages[]-vs-top-level table-row
    # doubling (ADR-0002 R-2, "tables" bullet). That is deliberate and
    # safe: ADR-0002's Correction 2026-09-24 (b) established from two live
    # captures (1-page and 3-page) that the response carries no `pages`
    # key at all, so the seam that produced the doubling is unreachable --
    # and were it ever reachable, a doubled row now surfaces as `new_line`,
    # which is informational and never fails the gate (BR3), rather than
    # vanishing silently.
    a_index: dict[str, list[dict[str, FieldValue]]] = {}
    for row in arows:
        mk_cell = row.get(key_col)
        mk_value = mk_cell.get("value") if mk_cell else None
        if is_empty(mk_value):
            continue
        a_index.setdefault(match_key_form(cast(str, mk_value)), []).append(row)

    rows: list[RowVerdict] = []
    for grow in gtable["rows"]:
        gmk = grow.get(key_col)
        gmk_str = gmk if isinstance(gmk, str) else None
        arow = _take_candidate(a_index, grow, key_col, gmk_str)
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
            avalue = acell.get("value") if acell is not None else None
            # The same scorer the fields use, so a classifier's rule holds
            # for a line-item cell as well as a top-level field -- one
            # comparison policy per run, not two.
            cell_result = _as_result(
                scorer(
                    ScoreContext(
                        name=col,
                        kind="table_column",
                        field_type=_TABLE_COLUMN_TYPE,
                        expected=gval,
                        actual=avalue,
                        confidence=acell.get("confidence") if acell else None,
                        critical=critical,
                        match_key=gmk_str,
                    )
                )
            )
            verdict = _as_row_verdict(cell_result.verdict, tname=None, column=col)
            # A `RowVerdict` carries no `critical` of its own -- the gate
            # reads the BLOCK's. So a scorer escalating one cell escalates
            # the block it is in, which is the only place that decision
            # can be expressed.
            escalated = escalated or cell_result.critical
            rows.append(
                RowVerdict(
                    match_key=gmk_str,
                    column=col,
                    verdict=verdict,
                    expected=gval,
                    actual=None if is_empty(avalue) else cast(str, avalue),
                    confidence=acell.get("confidence") if acell else None,
                )
            )

    # Leftover actual rows (no golden counterpart) -> new_line (BR3, informational).
    # Includes an unconsumed same-keyed duplicate (DEBT-09): the golden
    # declares one line for that key, the actual carried two.
    for candidates in a_index.values():
        for row in candidates:
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

    return TableVerdict(
        verdict="detail", critical=critical or escalated, type=None, rows=rows
    )


def classify(golden: Golden, actual: NormalizedOutput) -> VerdictMap:
    """Classify each field/prompt/table of ``actual`` against ``golden``.

    Returns a verdict map keyed by the union of golden field names, actual
    field names, table names, and prompt keys (ADR-0003 API contract). Pure:
    no I/O. Raises a typed :class:`ClassifierError` on malformed input (NFR N22).

    The `regression` classifier (``registry.py``): the default, and the
    one the watched-Action path has always used. Unchanged.
    """
    return _classify(golden, actual, scorer=regression_scorer)


def classify_pinned_file(golden: Golden, actual: NormalizedOutput) -> VerdictMap:
    """The `pinned-file` classifier: identical to :func:`classify` except
    that an empty expected value matched by an empty actual is a
    ``match`` rather than ``missing`` (see ``_classify_field``).

    Same two-argument contract, same six verdicts, same score-name
    vocabulary (INV-03) -- it differs in one rule, which is why it shares
    the comparison engine rather than forking it. A golden pinned from a
    trusted Action version can therefore mark EVERY field critical,
    including the ones that version read as empty: agreement on "nothing
    there" passes, and invented content still fails.
    """
    return _classify(golden, actual, scorer=pinned_file_scorer)


def make_classifier(scorer: Scorer) -> ClassifyFn:
    """Turn a scorer into a classifier -- a `(golden, actual) ->
    VerdictMap` function with the pinned CT-02 signature.

    This is the whole cost of adding a classifier: write a scorer (see
    `scorers.py`), wrap it here, add one row to `registry.CLASSIFIERS`.
    The fan-out over fields/prompts/table rows, the validation and the
    gate are shared and are not rewritten per classifier.
    """

    def classify_with(golden: Golden, actual: NormalizedOutput) -> VerdictMap:
        return _classify(golden, actual, scorer=scorer)

    return classify_with


def _classify(golden: Golden, actual: NormalizedOutput, *, scorer: Scorer) -> VerdictMap:
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
            spec.get("format_critical", False),
            scorer,
        )

    for name, cell in afields.items():
        if name not in gfields:
            verdicts[name] = Verdict(
                verdict="new_field",
                expected=None,
                actual=cell.get("value"),
                confidence=cell.get("confidence"),
                critical=False,
                format_critical=False,
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
            scorer,
        )
    for key, pcell in aprompts.items():
        if key not in gprompts:
            verdicts[key] = Verdict(
                verdict="new_field",
                expected=None,
                actual=pcell.get("answer"),
                confidence=pcell.get("confidence"),
                critical=False,
                format_critical=False,
                type=None,
            )

    # Line items: pair rows by match_key, per-column sub-verdicts (ADR-0003, BR8).
    gtables = golden.get("tables", {})
    atables: dict[str, list[dict[str, FieldValue]]] = actual.get("tables", {})
    for tname, gtable in gtables.items():
        verdicts[tname] = _classify_table(gtable, atables.get(tname, []), scorer)

    # DEBT-05 / D1a: a table the golden does not have. Emitted AFTER the
    # loop above and guarded on `not in gtables`, so a table present in
    # both keeps its real row-by-row `detail` comparison -- the obvious
    # implementation (iterate actual tables and assign) would overwrite it.
    #
    # Shaped like `new_field`: a leaf entry, not a `detail` container.
    # There is no golden to pair rows against, so per-row sub-verdicts
    # would be a list of `new_line` restating the same fact once per row.
    # `actual` carries the row COUNT rather than any cell value -- INV-02
    # keeps extracted values out of anything that is not the run artifact,
    # and the count is what tells an operator whether this is a stray row
    # or a whole table that appeared.
    for tname, arows in atables.items():
        if tname not in gtables:
            verdicts[tname] = Verdict(
                verdict="new_table",
                expected=None,
                actual=f"{len(arows)} row(s)" if isinstance(arows, list) else None,
                confidence=None,
                critical=False,
                format_critical=False,
                type=None,
            )

    return verdicts


def overall_gate(verdicts: VerdictMap) -> Literal["PASS", "FAIL"]:
    """Aggregate a verdict map to ``"PASS"`` or ``"FAIL"`` (ADR-0003).

    ``FAIL`` iff a ``missing`` or ``wrong_value`` verdict is ``critical: True``
    (BR2), **or** a ``wrong_format`` verdict is ``format_critical: True``
    (DEBT-80). ``new_field``, ``new_line``, ``new_table``, and any non-critical
    difference do not fail the gate (BR3) -- all three are ADDITIONS, and an
    addition is not a regression.

    ``wrong_format`` is informational by default and that remains BR3's rule:
    a value that is semantically right but formatted differently is not a
    regression. ``format_critical`` is the per-field opt-out, for a field
    whose *format* is itself the contract. It was added after a live run
    (2026-09-24) showed the tool returning exit 0 for a real prompt
    regression -- an ``invoice_date`` prompt that lost its ISO-8601
    instruction and began emitting ``22/01/2026``, the same calendar date in
    a different format, on a ``critical`` field.

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
        elif entry["verdict"] == "wrong_format" and entry.get("format_critical", False):
            # DEBT-80: the declared exception to BR3. `.get` with a default,
            # not `[...]`, because overall_gate is public API over a
            # caller-supplied VerdictMap -- a map built before this key
            # existed must keep the old behaviour, not raise a KeyError.
            return "FAIL"
    return "PASS"