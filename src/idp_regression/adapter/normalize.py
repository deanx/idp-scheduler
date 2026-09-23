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

    # ADR-0002 A11 (REG-11 fix, third-occurrence D1 terminal rule 2026-09-23)
    # + 2026-09-22 REQUEST CHANGES round (R-1) + D2 (2026-09-23).
    # The real MuleSoft Anypoint IDP execution body carries `fields`/
    # `tables` at the TOP LEVEL and has no `pages` key at all — confirmed
    # 2026-09-22 by the first live extraction ever run (seed-001-clean.pdf).
    # `pages[]` was the shape every hand-authored fixture used, never
    # observed live; it is kept as an OPTIONAL, still-supported envelope (a
    # genuine multi-page document may yet use it — unverified) rather than
    # removed outright.
    #
    # D1 terminal rule: normalize() UNIONS every recognised container that
    # is PRESENT — it never picks one envelope to the exclusion of another.
    # Two prior rounds tried to fix "barren beats real" by testing content
    # SUFFICIENCY one level deeper each time (`pages` absent -> `[]`
    # default; then `pages: []` outranking a populated top level; then
    # `pages: [{}]` outranking it) — each round moved the emptiness test in
    # without making it terminal, because the underlying model was still
    # "one envelope wins". A union has no such model: every recognised
    # container present contributes whatever it actually holds (zero
    # entries for an empty one, real entries for a populated one) to the
    # SAME merge loop that already combines multiple `pages[]` entries.
    # There is no "one level deeper" left, because presence is tested
    # exactly once, at the top, and nothing about how empty or nested a
    # container's content is changes whether it is included — only whether
    # it contributes anything once included.
    #   - a non-empty `pages` list contributes its entries;
    #   - a present top-level `fields`/`tables`/`prompts` container
    #     contributes the raw body itself as one more logical page (any
    #     other top-level key — `documentName`, `id`, `status` — is simply
    #     ignored by `_merge_*`, which only reads the keys it recognises);
    #   - neither is present -> raises `missing_envelope`, never a silent
    #     empty success.
    # A non-list `pages` value always raises `invalid_pages` outright and
    # never falls through, even when a top-level container is present —
    # that would hide a genuinely corrupt response shape.
    #
    # D2: `prompts` is a recognised top-level envelope container too, on
    # equal footing with `fields`/`tables` — an action can plausibly return
    # only prompt answers (`NormalizedOutput.prompts` is first-class) and
    # must not be rejected as `missing_envelope` for lacking `fields`/
    # `tables` it was never going to have. ⚠️ This makes the envelope check
    # consistent with the declared type; it is NOT a wire-contract
    # verification of the `prompts` shape (SR-1) — that shape stays
    # unverified against a live IDP response (DEBT-69).
    #
    # Empty-extraction decision (R-1, recorded in ADR-0002 A11, preserved
    # unchanged by the union rule): a recognised container that is PRESENT
    # but genuinely EMPTY still counts as present and contributes zero
    # entries — "IDP looked and found nothing" is a legitimate result, not
    # a malformed one. Only the ABSENCE of every recognisable container
    # raises `missing_envelope`.
    pages_key_present = "pages" in raw
    raw_pages = raw.get("pages")
    if pages_key_present and not isinstance(raw_pages, list):
        raise MalformedIDPOutputError("invalid_pages", "raw IDP response 'pages' must be a list")
    has_top_level_container = "fields" in raw or "tables" in raw or "prompts" in raw

    logical_pages: list[object] = []
    if pages_key_present and raw_pages:
        logical_pages.extend(raw_pages)
    if has_top_level_container:
        logical_pages.append(raw)

    if not logical_pages:
        raise MalformedIDPOutputError(
            "missing_envelope",
            "raw IDP response has no non-empty 'pages' list and no "
            "top-level 'fields'/'tables'/'prompts' container",
        )

    fields: dict[str, FieldValue] = {}
    tables: dict[str, list[dict[str, FieldValue]]] = {}
    prompts: dict[str, PromptValue] = {}
    seen_prompt_keys: set[str] = set()

    # 2026-09-23 REQUEST CHANGES round (R-1/R-2). `raw` — the top-level
    # rollup — is appended to `logical_pages` LAST (see above), and is the
    # single `page is raw` entry in this loop. That ordering, not any
    # special-casing inside the loop body, is what makes the top level win
    # on every collision below:
    #   - fields (`_merge_fields`, last-wins): processed last -> the
    #     top-level value for a shared field name silently overwrites
    #     whatever a `pages[]` entry set (R-1 PIN, see
    #     `test_top_level_field_wins_over_a_colliding_pages_entry`).
    #   - tables (`_merge_tables`, `existing.extend(rows)`): concatenated,
    #     not deduplicated, across this same seam — a shared row that
    #     genuinely appears in both a `pages[]` entry and the top-level
    #     rollup is DOUBLED in `NormalizedOutput.tables`, not merged. R-2
    #     decision: do not dedup here — `normalize()` has no `match_key`
    #     (that is golden-schema knowledge, supplied only at classify time)
    #     to dedup rows by, so any dedup attempt here would be a guess, not
    #     a rule. `classifier/gate.py` indexes actual rows by normalized
    #     `match_key` (`compare_rows`, ~line 213-226) and so pairs/collapses
    #     the duplicate transparently — the gate's masking IS load-bearing
    #     for this seam, and is only now written down. `NormalizedOutput`
    #     itself, and anything reading it directly (ADR-0007's local run
    #     artifact, Epic E's future remediation UI), still sees every row
    #     twice.
    #   - prompts: see `_merge_prompts`'s `allow_override` parameter below.
    for page in logical_pages:
        if not isinstance(page, dict):
            raise MalformedIDPOutputError("invalid_page", "each page must be a mapping")
        is_top_level_rollup = has_top_level_container and page is raw
        _merge_fields(page.get("fields", {}), fields)
        _merge_tables(page.get("tables", {}), tables)
        _merge_prompts(
            page.get("prompts", []), prompts, seen_prompt_keys, allow_override=is_top_level_rollup
        )

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
    # M-2 (2026-09-22 REQUEST CHANGES round). Two confidence-scale keys on
    # one cell is a structural conflict, not a preference — the module's
    # contract everywhere else is fail-closed on contradiction, and
    # silently letting `confidenceScore` win meant an out-of-range
    # `confidence` sitting next to it was never even validated.
    present_keys = [key for key, *_ in _CONFIDENCE_SCALES if key in raw_cell]
    if len(present_keys) > 1:
        raise MalformedIDPOutputError(
            "conflicting_confidence_keys",
            f"a {kind} cell has more than one confidence-scale key present",
        )

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
        # M-1 (2026-09-22 REQUEST CHANGES round). A `confidenceScore` value
        # strictly between 0 and 1 is scale-ambiguous, not a legitimate low
        # score: every legitimate 0-1-scale value sits inside the [0, 100]
        # range check above and would otherwise be silently divided by 100
        # into a value two orders of magnitude wrong. 0.0 and 1.0 are the
        # endpoints of the ambiguous OPEN interval, not inside it, and stay
        # legitimate (0%/1% confidence on the real 0-100 scale). The
        # `confidence` key's own native scale is already 0-1, so it is not
        # ambiguous and is exempt.
        if key == "confidenceScore" and 0.0 < fval < 1.0:
            raise MalformedIDPOutputError(
                "confidence_scale_ambiguous",
                f"a {kind} confidenceScore value is between 0 and 1, ambiguous "
                "between its declared 0-100 scale and a possible 0-1 scale",
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
    allow_override: bool = False,
) -> None:
    # R-2 (2026-09-23 REQUEST CHANGES round): `duplicate_prompt` was a
    # page-vs-page integrity rule ("two entries in this action's real
    # `pages[]` output claim the same prompt key" — always a defect)
    # reapplied wholesale to the page-vs-top-level seam the D1 union rule
    # opened, where the SAME key legitimately appearing in a `pages[]`
    # entry and in the top-level rollup is not two independent answers —
    # it is the same answer reported at two levels of the one wire shape
    # (the rollup echoing what the pages already say), exactly as `fields`
    # already treats it (last-wins, no raise). Decision: keep
    # `duplicate_prompt` FATAL for a true within-container duplicate (two
    # entries in the SAME `prompts` list, tracked by `seen_in_this_list`
    # below — that is still always a defect, page-vs-page or within the
    # rollup itself) but let the caller mark ONE container's pass as the
    # rollup (`allow_override=True`, only ever the top-level page, see
    # `normalize()`) so a key already seen in an earlier `pages[]` entry is
    # overwritten, last-wins, instead of raising. A `pages[]`-vs-`pages[]`
    # collision (`allow_override=False` on both) still raises exactly as
    # before — this only de-fangs the specific page/top-level seam R-2 is
    # about, nothing else.
    if not isinstance(raw_prompts, list):
        raise MalformedIDPOutputError("invalid_page", "page 'prompts' must be a list")
    seen_in_this_list: set[str] = set()
    for raw_entry in raw_prompts:
        if not isinstance(raw_entry, dict):
            raise MalformedIDPOutputError("invalid_page", "each prompt entry must be a mapping")
        prompt_key = raw_entry.get("prompt")
        if not isinstance(prompt_key, str) or not _SAFE_PROMPT_PATTERN.match(prompt_key):
            raise MalformedIDPOutputError(
                "unsafe_prompt_key", "a prompt key failed the verbatim safe-charset check"
            )
        if prompt_key in seen_in_this_list:
            # A duplicate WITHIN one container (same page, or the rollup
            # itself) is always a genuine defect, regardless of
            # `allow_override`.
            raise MalformedIDPOutputError(
                "duplicate_prompt", "two prompt entries share the same prompt key"
            )
        seen_in_this_list.add(prompt_key)
        if prompt_key in seen_keys and not allow_override:
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
