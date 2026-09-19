"""Typed contracts for the IDP adapter (ADR-0002).

These TypedDicts are structurally identical to the classifier's local
copies in ``idp_regression.classifier.types`` (``NormalizedOutput`` /
``FieldValue`` / ``PromptValue``) — CT-01 (``tests/adapter/test_normalize_contract.py``)
asserts that identity. The adapter defines its own copies rather than
importing the classifier's so neither package depends on the other
(the classifier must stay import-free of ``adapter``/``platform``/
``orchestration`` — ADR-0003).
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class FieldValue(TypedDict):
    """An actual extracted field (or table cell) value (DATA-MODEL-01 §2)."""

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


class IDPAdapter(Protocol):
    """The adapter's single public seam (ADR-0002)."""

    def extract(self, document_path: str, action_id: str, version: str) -> NormalizedOutput: ...
