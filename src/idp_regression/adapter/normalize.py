"""``normalize()`` — the single seam between IDP's volatile ``pages[]``
shape and the stable internal ``NormalizedOutput`` contract (ADR-0002
Option B).

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
#: ``propertyNames`` pattern (CT-05).
_SAFE_PROMPT_PATTERN = re.compile(r"\A[^\u0000-\u001F\u007F]{1,200}\Z")

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

    pages = raw.get("pages", [])
    if not isinstance(pages, list):
        raise MalformedIDPOutputError("invalid_pages", "raw IDP response 'pages' must be a list")

    fields: dict[str, FieldValue] = {}
    tables: dict[str, list[dict[str, FieldValue]]] = {}
    prompts: dict[str, PromptValue] = {}
    seen_prompt_keys: set[str] = set()

    for page in pages:
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
    confidence = _coerce_confidence(raw_cell.get("confidence"))
    return FieldValue(value=value, confidence=confidence)


def _coerce_confidence(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float)):
        return None
    try:
        fval = float(raw)
    except OverflowError:
        # A huge int (e.g. 10**400) can't convert to float — out of any
        # sane range, so this is "out of range" -> None (Atchim R2),
        # consistent with the NaN/out-of-range handling below.
        return None
    if math.isnan(fval) or fval < 0.0 or fval > 1.0:
        return None  # never clamped — a broken IDP action must be visible, not hidden
    return fval


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

        into[prompt_key] = PromptValue(
            answer=answer_cell["value"], confidence=answer_cell["confidence"], source=source
        )
