"""Performance test (T-01.1.7, NFR N2).

``classify()`` + ``overall_gate()`` for a single document with <= 50 fields
and <= 500 line-item rows must complete in < 100 ms p95 on M1. Uses
pytest-benchmark; the p95 is computed from the benchmark's sorted per-round
timings.
"""

from __future__ import annotations

import math

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.types import (
    FieldType,
    FieldValue,
    Golden,
    GoldenField,
    NormalizedOutput,
)

P95_BUDGET_SECONDS = 0.100  # NFR N2: < 100 ms p95
N_FIELDS = 50
N_ROWS = 500
_FTYPES: tuple[FieldType, ...] = ("number", "date", "id", "text")


def _build_golden() -> Golden:
    fields: dict[str, GoldenField] = {}
    for i in range(N_FIELDS):
        ftype = _FTYPES[i % 4]
        fields[f"f{i}"] = {"value": f"v{i}", "type": ftype, "critical": i % 5 == 0}
    rows = [
        {"description": f"item_{i}", "qty": str(i % 100), "unit_price": f"{i % 50}.00"}
        for i in range(N_ROWS)
    ]
    return {
        "fields": fields,
        "tables": {"line_items": {"match_key": "description", "critical": True, "rows": rows}},
    }


def _build_actual() -> NormalizedOutput:
    afields: dict[str, FieldValue] = {}
    for i in range(N_FIELDS):
        afields[f"f{i}"] = {"value": f"v{i}"}
    # Half the rows match golden, half are new (exercise match + new_line paths).
    rows: list[dict[str, FieldValue]] = []
    for i in range(N_ROWS):
        rows.append(
            {
                "description": {"value": f"item_{i}"},
                "qty": {"value": str(i % 100)},
                "unit_price": {"value": f"{i % 50}.00"},
            }
        )
    return {"status": "SUCCEEDED", "fields": afields, "tables": {"line_items": rows}}


def _percentile_95(sorted_data: list[float]) -> float:
    if not sorted_data:
        return math.inf
    idx = max(0, math.ceil(0.95 * len(sorted_data)) - 1)
    return sorted_data[idx]


def test_classify_and_gate_under_100ms_p95(benchmark) -> None:  # type: ignore[no-untyped-def]
    golden = _build_golden()
    actual = _build_actual()

    def run() -> str:
        return overall_gate(classify(golden, actual))

    result = benchmark.pedantic(run, rounds=200, iterations=1)
    assert result == "PASS"

    raw = list(benchmark.stats.stats.sorted_data)  # seconds, ascending
    p95 = _percentile_95(raw)
    assert p95 < P95_BUDGET_SECONDS, f"p95 {p95 * 1000:.2f} ms exceeds 100 ms budget"