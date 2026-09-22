"""``normalize()`` — the single seam between IDP's volatile raw execution
body and the stable internal ``NormalizedOutput`` contract (ADR-0002
Option B). The real wire shape (confirmed live, ADR-0002 A11) carries
``fields``/``tables`` at the TOP LEVEL of the body; the historical
``pages[]`` envelope is kept as an optional, still-supported shape (never
observed live, but not removed — a genuine multi-page document is an
unverified unknown) — a response with neither raises.

Pure function, no I/O: ``normalize(raw, success_statuses) -> NormalizedOutput``.
Treats the raw body as untrusted input (NFR N21) — every malformed shape
raises the typed ``MalformedIDPOutputError`` (with a stable ``reason`` tag)
rather than letting a ``KeyError``/``AttributeError`` escape, and the
message never echoes the offending value (INV-02: golden/actual content is
never logged/echoed in plain text).
"""

from __future__ import annotations

import math
import re

from idp_regression.adapter.errors import MalformedIDPOutputError
from idp_regression.adapter.types import FieldValue, NormalizedOutput, PromptValue

#: Field/table-name trust boundary charset (ADR-0002 §Field-name
#: sanitization) — matches the golden schema's `^[A-Za-z0-9_-]{1,128}$`
#: (\A/\Z, not ^/$: `$` matches before a trailing newline in Python re,
#: which would silently accept a name like "total\n" — Atchim R1).
_SAFE_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")

#: Prompt-key rule (ADR-0002 amendment 2026-09-19, ADR-0005 F10): verbatim,
#: 1-200 chars, no control characters — matches the golden schema's
#: ``propertyNames`` pattern (CT-05). Also excludes lone surrogates
#: (\uD800-\uDFFF): a raw JSON body can't legally contain one, but Python's
#: json.loads(surrogatepass-style malformed input) can still hand us a str
#: with one, and it would otherwise pass this charset check only to crash
#: later downstream (the platform's sha256 of the prompt key,
#: UnicodeEncodeError — /test Scenario B item 5).
_SAFE_PROMPT_PATTERN = re.compile(r"\A[^\u0000-\u001F\u007F\uD800-\uDFFF]{1,200}\Z")

#: A field/table-cell value beyond this many UTF-8 bytes is rejected (ADR-0002).
MAX_VALUE_BYTES = 64 * 1024

#: A table beyond this many total rows (summed across pages) is rejected (ADR-0002).
MAX_TABLE_ROWS = 10_000


def normalize(raw: object, success_statuses: set[str]) -> NormalizedOutput:
    if not isinstance(raw, dict):
        raise MalformedIDPOutputError("invalid_body", "raw IDP response must be a mapping")

    status = raw.get("status")
    if not isinstance(status, str) or not status:
        raise MalformedIDPOutputError(
            "invalid_status", "raw IDP response is missing a non-empty 'status' string"
        )
    if status not in success_statuses:
        # ADR-0002:125 — normalize() only ever emits a NormalizedOutput for
        # a status in the caller's success set; the adapter's poll loop
        # already filters this, but normalize() is the single seam and
        # must not trust a caller to have done so (Atchim R7).
        raise MalformedIDPOutputError(
            "status_not_success", "raw IDP response 'status' is not in success_statuses"
        )

    # ADR-0002 A11 (REG-11 fix). The real MuleSoft Anypoint IDP execution
    # body carries `fields`/`tables` at the TOP LEVEL and has no `pages`
    # key at all — confirmed 2026-09-22 by the first live extraction ever
    # run (seed-001-clean.pdf). `pages[]` was the shape every hand-authored
    # fixture used, never observed live; it is kept as an OPTIONAL, still-
    # supported envelope (a genuine multi-page document may yet use it —
    # unverified) rather than removed outright. Whichever shape wins, a
    # response with NEITHER present must raise, never silently return an
    # empty success (the fail-open regression this amendment exists to
    # close) — `raw.get("pages", [])` used to default a missing key to an
    # empty list and walk zero pages without raising.
    has_pages = "pages" in raw
    has_top_level_container = "fields" in raw or "tables" in raw

    logical_pages: list[object]
    if has_pages:
        pages = raw["pages"]
        if not isinstance(pages, list):
            raise MalformedIDPOutputError(
                "invalid_pages", "raw IDP response 'pages' must be a list"
            )
        logical_pages = pages
    elif has_top_level_container:
        # Treat the top-level body itself as the single logical page: it
        # already carries `fields`/`tables`/(optionally) `prompts` at the
        # keys `_merge_*` reads, and any other top-level key (`documentName`,
        # `id`, `status`) is simply ignored by those readers.
        logical_pages = [raw]
    else:
        raise MalformedIDPOutputError(
            "missing_envelope",
            "raw IDP response has neither a 'pages' list nor a top-level "
            "'fields'/'tables' container",
        )

    fields: dict[str, FieldValue] = {}
    tables: dict[str, list[dict[str, FieldValue]]] = {}
    prompts: dict[str, PromptValue] = {}
    seen_prompt_keys: set[str] = set()

    for page in logical_pages:
        if not isinstance(page, dict):
            raise MalformedIDPOutputError("invalid_page", "each page must be a mapping")
        _merge_fields(page.get("fields", {}), fields)
        _merge_tables(page.get("tables", {}), tables)
        _merge_prompts(page.get("prompts", []), prompts, seen_prompt_keys)

    return NormalizedOutput(status=status, fields=fields, tables=tables, prompts=prompts)


def _validate_name(name: object, kind: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME_PATTERN.match(name):
        raise MalformedIDPOutputError(
            "unsafe_field_name", f"a {kind} name failed the safe-charset check"
        )
    return name


def _coerce_cell(raw_cell: object, kind: str) -> FieldValue:
    if not isinstance(raw_cell, dict):
        raise MalformedIDPOutputError("invalid_cell", f"a {kind} cell must be a mapping")
    if "value" not in raw_cell:
        raise MalformedIDPOutputError("invalid_cell", f"a {kind} cell is missing 'value'")
    value = raw_cell["value"]
    if value is not None and not isinstance(value, str):
        raise MalformedIDPOutputError(
            "invalid_cell_value", f"a {kind} cell value must be a string or null"
        )
    if value is not None:
        try:
            value_byte_length = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            # Never echo the offending value (a lone surrogate, etc.) — the
            # message is static and the chain is broken with `from None`
            # (Atchim R2: str(exc) and repr(exc.__cause__) must hold no value).
            raise MalformedIDPOutputError(
                "invalid_cell_value", f"a {kind} cell value contains an unencodable character"
            ) from None
        if value_byte_length > MAX_VALUE_BYTES:
            raise MalformedIDPOutputError(
                "value_too_large", f"a {kind} cell value exceeds {MAX_VALUE_BYTES} bytes"
            )
    confidence = _coerce_confidence(raw_cell, kind)
    return FieldValue(value=value, confidence=confidence)


#: ADR-0002 A11 (REG-11 D2 fix). The real live response scores confidence
#: on a 0-100 scale under the key `confidenceScore` (observed 95.0-99.0);
#: every hand-authored fixture used a 0-1 scale under the key `confidence`.
#: Both keys are accepted — `confidenceScore` is converted to the
#: documented 0-1 internal scale (DATA-MODEL-01 §2) by dividing by 100, the
#: legacy `confidence` key is carried through unconverted. Neither branch
#: silently swallows an out-of-range value into `None` any more: the old
#: `_coerce_confidence` comment claimed "never clamped — a broken IDP
#: action must be visible, not hidden", but returning `None` on an
#: out-of-range value IS hiding it (that is exactly how every real
#: confidenceScore became `None` with no exception and no log line before
#: this fix). A present-but-invalid confidence now raises; an ABSENT
#: confidence key still means "not provided" -> `None` (the three-state
#: missing/empty/null contract this module preserves everywhere else).
_CONFIDENCE_SCALES: tuple[tuple[str, float, float, float], ...] = (
    # (key, divisor, min valid raw value, max valid raw value)
    ("confidenceScore", 100.0, 0.0, 100.0),
    ("confidence", 1.0, 0.0, 1.0),
)


def _coerce_confidence(raw_cell: dict[str, object], kind: str) -> float | None:
    for key, divisor, lo, hi in _CONFIDENCE_SCALES:
        if key not in raw_cell:
            continue
        raw = raw_cell[key]
        if raw is None:
            return None  # explicit null -> "not provided", not an error
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise MalformedIDPOutputError(
                "invalid_confidence", f"a {kind} confidence value is not numeric"
            )
        try:
            fval = float(raw)
        except OverflowError:
            # A huge int (e.g. 10**400) can't convert to float — this is an
            # out-of-range value like any other now, and must be visible,
            # not swallowed into None (Atchim R2's rationale, corrected).
            raise MalformedIDPOutputError(
                "invalid_confidence", f"a {kind} confidence value overflowed"
            ) from None
        if math.isnan(fval) or fval < lo or fval > hi:
            raise MalformedIDPOutputError(
                "invalid_confidence",
                f"a {kind} confidence value is outside the expected [{lo}, {hi}] range",
            )
        return fval / divisor
    return None  # key absent entirely -> "not provided"


def _merge_fields(raw_fields: object, into: dict[str, FieldValue]) -> None:
    if not isinstance(raw_fields, dict):
        raise MalformedIDPOutputError("invalid_page", "page 'fields' must be a mapping")
    for raw_name, raw_cell in raw_fields.items():
        name = _validate_name(raw_name, "field")
        into[name] = _coerce_cell(raw_cell, "field")  # last-wins across pages


def _merge_tables(raw_tables: object, into: dict[str, list[dict[str, FieldValue]]]) -> None:
    if not isinstance(raw_tables, dict):
        raise MalformedIDPOutputError("invalid_page", "page 'tables' must be a mapping")
    for raw_name, raw_rows in raw_tables.items():
        name = _validate_name(raw_name, "table")
        if not isinstance(raw_rows, list):
            raise MalformedIDPOutputError("invalid_page", f"table {name!r} rows must be a list")
        rows: list[dict[str, FieldValue]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                raise MalformedIDPOutputError(
                    "invalid_page", f"table {name!r} has a non-mapping row"
                )
            row: dict[str, FieldValue] = {}
            for raw_col, raw_cell in raw_row.items():
                col = _validate_name(raw_col, "table column")
                row[col] = _coerce_cell(raw_cell, "table cell")
            rows.append(row)
        existing = into.setdefault(name, [])
        if len(existing) + len(rows) > MAX_TABLE_ROWS:
            raise MalformedIDPOutputError(
                "table_too_large", f"table {name!r} exceeds {MAX_TABLE_ROWS} rows"
            )
        existing.extend(rows)


def _merge_prompts(
    raw_prompts: object,
    into: dict[str, PromptValue],
    seen_keys: set[str],
) -> None:
    if not isinstance(raw_prompts, list):
        raise MalformedIDPOutputError("invalid_page", "page 'prompts' must be a list")
    for raw_entry in raw_prompts:
        if not isinstance(raw_entry, dict):
            raise MalformedIDPOutputError("invalid_page", "each prompt entry must be a mapping")
        prompt_key = raw_entry.get("prompt")
        if not isinstance(prompt_key, str) or not _SAFE_PROMPT_PATTERN.match(prompt_key):
            raise MalformedIDPOutputError(
                "unsafe_prompt_key", "a prompt key failed the verbatim safe-charset check"
            )
        if prompt_key in seen_keys:
            raise MalformedIDPOutputError(
                "duplicate_prompt", "two prompt entries share the same prompt key"
            )
        seen_keys.add(prompt_key)

        answer_cell = _coerce_cell(raw_entry.get("answer"), "prompt answer")
        source = raw_entry.get("source")
        if source is not None and not isinstance(source, str):
            raise MalformedIDPOutputError(
                "invalid_page", "prompt 'source' must be a string or null"
            )
        if source is not None:
            try:
                source_byte_length = len(source.encode("utf-8"))
            except UnicodeEncodeError:
                raise MalformedIDPOutputError(
                    "invalid_cell_value", "a prompt source contains an unencodable character"
                ) from None
            if source_byte_length > MAX_VALUE_BYTES:
                # Unbounded prompt source (Atchim suggestion) — same cap as
                # a field/cell value, same reason tag.
                raise MalformedIDPOutputError(
                    "value_too_large", f"a prompt source exceeds {MAX_VALUE_BYTES} bytes"
                )

        into[prompt_key] = PromptValue(
            answer=answer_cell["value"], confidence=answer_cell.get("confidence"), source=source
        )
