"""Typed contracts for the pure classifier (ADR-0003, DATA-MODEL-01).

The classifier is pure: it must not import anything from ``adapter``,
``platform``, or ``orchestration``, and must not touch the network, disk,
or logging. These TypedDicts are defined locally (structurally compatible
with the adapter's ``NormalizedOutput``) so the classifier stays
self-contained and side-effect-free.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# The seven verdicts that are the stable contract (glossary, ADR-0003, CT-02;
# `new_table` added by ADR-0003 A2).
#: The six LEAF verdicts: what a single expected/actual comparison can say.
#: A table ROW sub-verdict is always one of these -- a row is a comparison,
#: so it can be `new_line` (a row the golden does not have) but never
#: `new_table`, which is a statement about a table's existence rather than
#: about any comparison inside it. Kept as its own alias so that stays true
#: by construction rather than by convention (INV-03).
RowVerdictLiteral = Literal[
    "match",
    "missing",
    "wrong_value",
    "wrong_format",
    "new_field",
    "new_line",
]

VerdictLiteral = Literal[
    "match",
    "missing",
    "wrong_value",
    "wrong_format",
    "new_field",
    "new_line",
    # D1a (user decision 2026-09-25), closing DEBT-05: a table present in
    # `actual.tables` but absent from the golden. Before this it had no
    # entry in the verdict map AT ALL -- `classify` iterated the golden's
    # tables only -- so an extractor that began emitting a whole table was
    # invisible to the artifact, the platform scores and the console.
    #
    # Informational, like `new_field` and `new_line`: BR3 says an ADDITION
    # is not a regression, and a new table is that same event one level up.
    # Representation was the defect DEBT-05 named, not gating.
    "new_table",
]

# The four field types that drive per-type canonical comparison (ADR-0003).
FieldType = Literal["number", "date", "id", "text"]

#: The set of valid field types, used for input validation.
FIELD_TYPES: frozenset[str] = frozenset({"number", "date", "id", "text"})


class GoldenField(TypedDict):
    """A golden (expected) field specification."""

    value: str | None
    type: FieldType
    critical: NotRequired[bool]
    #: Opt-in: when true, a ``wrong_format`` verdict on this field fails the
    #: gate exactly as ``wrong_value`` does (DEBT-80, decided 2026-09-24).
    #: Defaults to false, so BR3's "format is informational" stays the rule
    #: and this is the declared exception -- for a field whose *format* is
    #: part of the contract, e.g. a date the extraction prompt is required
    #: to emit as ISO-8601 because a downstream parser is strict.
    format_critical: NotRequired[bool]


class GoldenTable(TypedDict):
    """A golden (expected) line-item block."""

    match_key: str
    rows: list[dict[str, str]]
    critical: NotRequired[bool]


class GoldenPrompt(TypedDict):
    """A golden (expected) prompt answer."""

    answer: str | None
    critical: NotRequired[bool]


class Golden(TypedDict):
    """The curated reference shape (DATA-MODEL-01 §1)."""

    document_id: NotRequired[str]
    fields: dict[str, GoldenField]
    tables: NotRequired[dict[str, GoldenTable]]
    prompts: NotRequired[dict[str, GoldenPrompt]]


class FieldValue(TypedDict):
    """An actual extracted field value (DATA-MODEL-01 §2)."""

    value: str | None
    confidence: NotRequired[float | None]


class PromptValue(TypedDict):
    """An actual extracted prompt answer (DATA-MODEL-01 §2)."""

    answer: str | None
    confidence: NotRequired[float | None]
    source: NotRequired[str | None]


class NormalizedOutput(TypedDict):
    """The shape ``normalize()`` emits (ADR-0002 / DATA-MODEL-01 §2)."""

    status: str
    fields: dict[str, FieldValue]
    tables: NotRequired[dict[str, list[dict[str, FieldValue]]]]
    prompts: NotRequired[dict[str, PromptValue]]


class Verdict(TypedDict):
    """Per-field verdict (ADR-0003, CT-02)."""

    verdict: VerdictLiteral
    expected: str | None
    actual: str | None
    confidence: float | None
    critical: bool
    #: Mirrors the golden field's ``format_critical`` opt-in so
    #: ``overall_gate`` -- which sees only this map, never the golden --
    #: can honour it (DEBT-80). Always false for a ``new_field``, for a
    #: prompt, and for a table row: the opt-in is fields-only for now.
    format_critical: bool
    type: str | None


class RowVerdict(TypedDict):
    """A per-column sub-verdict inside a table block's ``rows[]`` detail."""

    match_key: str | None
    column: str | None
    verdict: RowVerdictLiteral
    expected: str | None
    actual: str | None
    confidence: float | None


class TableVerdict(TypedDict):
    """A table-block container in the verdicts map (DATA-MODEL-01 §3, CT-02).

    The verdict literals apply to leaf verdicts (fields, prompt keys, and
    each row-column sub-verdict). A table entry is a *container* over its
    ``rows[]``; its own ``verdict`` is the literal ``"detail"``.
    """

    verdict: Literal["detail"]
    critical: bool
    type: None
    rows: list[RowVerdict]


# The verdict map: one entry per (golden field ∪ actual field ∪ table ∪ prompt).
VerdictMap = dict[str, Verdict | TableVerdict]


class ClassifierError(Exception):
    """Base for typed classifier input-shape errors (NFR N22)."""


class MalformedGoldenError(ClassifierError):
    """The golden shape is invalid — a loud Curator error, not a silent match."""


class MalformedActualError(ClassifierError):
    """The NormalizedOutput shape is invalid."""