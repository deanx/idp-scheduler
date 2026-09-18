"""Typed contracts for the pure classifier (ADR-0003, DATA-MODEL-01).

The classifier is pure: it must not import anything from ``adapter``,
``platform``, or ``orchestration``, and must not touch the network, disk,
or logging. These TypedDicts are defined locally (structurally compatible
with the adapter's ``NormalizedOutput``) so the classifier stays
self-contained and side-effect-free.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# The six verdicts that are the stable contract (glossary, ADR-0003, CT-02).
VerdictLiteral = Literal[
    "match",
    "missing",
    "wrong_value",
    "wrong_format",
    "new_field",
    "new_line",
]

# The four field types that drive per-type canonical comparison (ADR-0003).
FieldType = Literal["number", "date", "id", "text"]

#: The set of valid field types, used for input validation.
FIELD_TYPES: frozenset[str] = frozenset({"number", "date", "id", "text"})


class GoldenField(TypedDict):
    """A golden (expected) field specification."""

    value: str | None
    type: FieldType
    critical: bool


class GoldenTable(TypedDict):
    """A golden (expected) line-item block."""

    match_key: str
    critical: bool
    rows: list[dict[str, str]]


class GoldenPrompt(TypedDict):
    """A golden (expected) prompt answer."""

    answer: str | None
    critical: bool


class Golden(TypedDict):
    """The curated reference shape (DATA-MODEL-01 §1)."""

    document_id: str
    fields: dict[str, GoldenField]
    tables: dict[str, GoldenTable]
    prompts: dict[str, GoldenPrompt]


class FieldValue(TypedDict):
    """An actual extracted field value (DATA-MODEL-01 §2)."""

    value: str | None
    confidence: float | None


class PromptValue(TypedDict):
    """An actual extracted prompt answer (DATA-MODEL-01 §2)."""

    answer: str | None
    confidence: float | None
    source: str | None


class NormalizedOutput(TypedDict):
    """The shape ``normalize()`` emits (ADR-0002 / DATA-MODEL-01 §2)."""

    status: str
    fields: dict[str, FieldValue]
    tables: dict[str, list[dict[str, FieldValue]]]
    prompts: dict[str, PromptValue]


class Verdict(TypedDict):
    """Per-field verdict (ADR-0003, CT-02)."""

    verdict: VerdictLiteral
    expected: str | None
    actual: str | None
    confidence: float | None
    critical: bool
    type: str | None


class RowVerdict(TypedDict):
    """A per-column sub-verdict inside a table block's ``rows[]`` detail."""

    match_key: str | None
    column: str | None
    verdict: VerdictLiteral
    expected: str | None
    actual: str | None
    confidence: float | None


class TableVerdict(TypedDict):
    """A table-block container in the verdicts map (DATA-MODEL-01 §3, CT-02).

    The six verdict literals apply to leaf verdicts (fields, prompt keys, and
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