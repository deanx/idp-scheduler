"""Pure classifier and aggregate gate (Epic B, ADR-0003).

This package is deliberately pure: it must not import from ``adapter``,
``platform``, or ``orchestration``, and must not perform any network, disk,
or logging operations. The classifier is the CI gate.
"""

from __future__ import annotations

from idp_regression.classifier.gate import classify, overall_gate
from idp_regression.classifier.types import (
    ClassifierError,
    FieldValue,
    Golden,
    MalformedActualError,
    MalformedGoldenError,
    NormalizedOutput,
    RowVerdict,
    TableVerdict,
    Verdict,
    VerdictLiteral,
    VerdictMap,
)

__all__ = [
    "ClassifierError",
    "FieldValue",
    "Golden",
    "MalformedActualError",
    "MalformedGoldenError",
    "NormalizedOutput",
    "RowVerdict",
    "TableVerdict",
    "Verdict",
    "VerdictLiteral",
    "VerdictMap",
    "classify",
    "overall_gate",
]