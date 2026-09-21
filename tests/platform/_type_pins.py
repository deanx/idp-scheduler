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

from typing import NotRequired, TypedDict, get_type_hints


def _str_fields(td: type) -> list[str]:
    """Extract the `str`-annotated field names of a TypedDict, sorted --
    the SINGLE derivation every real value-pin parametrize (record_run's
    `DocumentRecord`, get_dataset's `DatasetItem`) and `_StrFieldProbe`'s
    dedicated pin (below) call through. Atchim R-4 (fresh DEBT-44
    instance, FU-01.3-G fix round): extracting this into a named,
    independently-testable function is what makes the "type-driven, not
    a hand list in disguise" claim PINNABLE -- `DocumentRecord` alone has
    only two `str` fields today, so a hand-written `["item_id",
    "document_id"]` and a genuine `get_type_hints` derivation are
    indistinguishable by any test that only ever looks at
    `DocumentRecord`. `_str_fields` gives the claim a second,
    structurally different subject (`_StrFieldProbe`) to be tested
    against."""
    return sorted(name for name, hint in get_type_hints(td).items() if hint is str)


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
