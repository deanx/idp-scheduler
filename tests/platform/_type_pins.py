"""Shared test helper: type-driven `str`-field extraction for TypedDicts.

Moved here from ``test_record_run_preconditions.py`` (FU-01.3-I, following
the ``_tp45_subprocess_scenario.py`` convention of a leading-underscore
standalone module in this directory for cross-file test infrastructure) so
the same derivation backs BOTH trust-boundary value pins -- ``record_run``'s
`DocumentRecord` guard and ``get_dataset``'s `DatasetItem` guard -- instead
of a second hand-copied instance, which is the defect class this move
exists to stop.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict, get_args, get_origin, get_type_hints


def _required_fields(td: type) -> list[str]:
    """DEBT-49 -- sound PRESENCE derivation, robust to `NotRequired` under
    postponed annotations. Mirrors production's `_required_field_names`
    (`langfuse_adapter.py`), kept as a separate copy for the same reason
    `_str_fields` is: production code must not import from `tests/`.

    `TypedDict.__required_keys__` MIS-DERIVES every `NotRequired` field as
    required when `from __future__ import annotations` is in effect
    (verified live, this repo, Python 3.13.5: `ScoreInput.__required_keys__`
    includes `'comment'` even though it is declared
    `NotRequired[str | None]`) -- the annotation is a string at class-
    creation time, so `NotRequired` is never detected by that mechanism.
    NEVER use `__required_keys__` for a presence check while that import is
    active in this codebase.

    The fix: resolve each field via `get_type_hints(td, include_extras=True)`
    (which keeps the `NotRequired[...]` wrapper visible, unlike the
    extras-stripped form `_str_fields` uses for its VALUE-type check) and
    exclude any field whose resolved hint's origin is `typing.NotRequired`.
    This is presence-only -- it says nothing about a field's value type,
    which is `_str_fields`'s separate job for the `str`-typed subset."""
    hints = get_type_hints(td, include_extras=True)
    return sorted(name for name, hint in hints.items() if get_origin(hint) is not NotRequired)


def _str_fields(td: type) -> list[str]:
    """Extract the REQUIRED `str`-annotated field names of a TypedDict,
    sorted -- the SINGLE derivation every real value-pin parametrize
    (record_run's `DocumentRecord`, get_dataset's `DatasetItem`) and
    `_StrFieldProbe`'s dedicated pin (below) call through. Atchim R-4
    (fresh DEBT-44 instance, FU-01.3-G fix round): extracting this into a
    named, independently-testable function is what makes the
    "type-driven, not a hand list in disguise" claim PINNABLE --
    `DocumentRecord` alone has only two `str` fields today, so a
    hand-written `["item_id", "document_id"]` and a genuine
    `get_type_hints` derivation are indistinguishable by any test that
    only ever looks at `DocumentRecord`. `_str_fields` gives the claim a
    second, structurally different subject (`_StrFieldProbe`) to be
    tested against.

    DEBT-53 (prose) fix: mirrors production's `_str_annotated_field_names`
    (`langfuse_adapter.py`) -- resolves with `include_extras=True` and
    EXCLUDES any `NotRequired`-wrapped field, rather than the old
    no-`include_extras` call that silently folded a hypothetical
    `NotRequired[str]` field into this REQUIRED set. See
    `_optional_str_fields` below for the `NotRequired[str]` companion."""
    hints = get_type_hints(td, include_extras=True)
    result = []
    for name, hint in hints.items():
        if get_origin(hint) is NotRequired:
            continue
        if hint is str:
            result.append(name)
    return sorted(result)


def _optional_str_fields(td: type) -> list[str]:
    """DEBT-53 (prose) companion to `_str_fields` above: the
    `NotRequired[str]` fields of `td` -- `str`-typed WHEN PRESENT, never
    required to be present. Test-side twin of production's
    `_optional_str_annotated_field_names`."""
    hints = get_type_hints(td, include_extras=True)
    result = []
    for name, hint in hints.items():
        if get_origin(hint) is not NotRequired:
            continue
        (inner,) = get_args(hint)
        if inner is str:
            result.append(name)
    return sorted(result)


class _StrFieldProbe(TypedDict):
    """A dedicated probe TypedDict for `_str_fields` -- deliberately NOT
    shaped like `DocumentRecord`/`DatasetItem` (three `str` fields, not
    two, plus a `NotRequired[str]`, an `int`, and a `list[str]`) so the
    filter is exercised on a shape a hand-written literal couldn't
    coincidentally match. MUST stay MODULE-level: with `from __future__
    import annotations`, `get_type_hints` resolves forward-referenced
    annotations against the DEFINING MODULE's globals -- a function-local
    TypedDict has no such globals entry and `NotRequired` raises
    `NameError` at resolution time."""

    field_a: str
    field_b: str
    field_c: str
    optional_field: NotRequired[str]
    count_field: int
    list_field: list[str]
