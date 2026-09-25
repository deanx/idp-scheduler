"""Pure classifier and aggregate gate (Epic B, ADR-0003).

This package is deliberately pure: it must not import from ``adapter``,
``platform``, or ``orchestration``, and must not perform any network, disk,
or logging operations. The classifier is the CI gate.
"""

from __future__ import annotations

from idp_regression.classifier.gate import (
    classify,
    classify_pinned_file,
    make_classifier,
    overall_gate,
)
from idp_regression.classifier.registry import (
    CLASSIFIERS,
    DEFAULT_CLASSIFIER,
    Classifier,
    UnknownClassifierError,
    resolve,
)
from idp_regression.classifier.scoring import (
    ScoreContext,
    Scorer,
    ScoreResult,
    compare_value,
    is_empty,
)
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
    "CLASSIFIERS",
    "DEFAULT_CLASSIFIER",
    "Classifier",
    "ClassifierError",
    "FieldValue",
    "Golden",
    "MalformedActualError",
    "MalformedGoldenError",
    "NormalizedOutput",
    "RowVerdict",
    "ScoreContext",
    "ScoreResult",
    "Scorer",
    "TableVerdict",
    "Verdict",
    "VerdictLiteral",
    "UnknownClassifierError",
    "VerdictMap",
    "classify",
    "classify_pinned_file",
    "compare_value",
    "is_empty",
    "make_classifier",
    "overall_gate",
    "resolve",
]