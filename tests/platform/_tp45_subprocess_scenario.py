"""Standalone script run in its OWN process by test_inv01_payload.py's
TP-45 test (Atchim round 3). Not collected by pytest.

Why a subprocess: OTel registers its ``TracerProvider`` process-globally
on first use, and (empirically confirmed 2026-09-19) the langfuse SDK's
``run_experiment`` task-execution path resolves its tracer through that
global registration rather than strictly through the ``tracer_provider=``
instance passed to a given ``Langfuse(...)`` client. In the FULL test
suite (many earlier tests already construct real ``Langfuse`` clients),
the global slot is already claimed, so a later client's own
``InMemorySpanExporter`` silently receives zero spans and the
process-wide log watcher can observe a DIFFERENT client's export
errors. A fresh subprocess has no such prior registration, so this
scenario is deterministic regardless of suite/test order.

DEBT-18 (user decision, option B): distinctive sentinels are planted as
the golden field value and as the classifier's expected/actual verdict
values, to prove empirically (not just by code inspection) that neither
reaches a span attribute or a score payload.

Usage: ``python _tp45_subprocess_scenario.py {happy|failure}``
Prints one JSON line to stdout:
``{"error_type": str|null, "spans": [...], "score_bodies": [...]}``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from idp_regression.platform.errors import ExperimentRecordFailedError, FlushFailedError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.scoring import build_score_inputs, score_id
from idp_regression.platform.types import RunMetadata

PATH_SENTINEL = "/IDP_DOCUMENT_DIR/invoice-007.pdf"
GOLDEN_SENTINEL = "SENTINEL-GOLDEN-VALUE-a91cf3"
EXPECTED_SENTINEL = "SENTINEL-EXPECTED-4f8c1e"
ACTUAL_SENTINEL = "SENTINEL-ACTUAL-9b2d7a"


class _ScoresExplodeOnSubscript(dict):  # type: ignore[type-arg]
    """DEBT-39 twin, found live (FO-2 fix round, 2026-09-21): this
    scenario used to give the task a well-shaped ``scores`` list except
    for a score missing ``"value"`` — that stopped exercising the task's
    real-KeyError path the moment ``_require_record_shape`` started
    checking ``"value"`` (FO-2's fix in ``langfuse_adapter.py``), because
    the record is now rejected at the PRECONDITION and never reaches the
    real SDK task at all (this test regressed from ``FlushFailedError``
    to ``ExperimentRecordFailedError`` the moment that guard landed).
    Same ``.get()``-vs-``__getitem__`` split already used in
    ``tests/platform/test_record_run_preconditions.py``'s own DEBT-39
    re-fixture: ``_require_record_shape`` reads via ``record.get(...)``
    (bypasses this override, sees a well-formed scores list, passes
    shape validation and the run_id derivation check), while the real
    task body subscripts ``record["scores"]`` directly (raises here) —
    so it's a genuine exception inside the REAL SDK task, in this
    isolated subprocess, exactly as this scenario's docstring promises."""

    def __getitem__(self, key: str) -> Any:
        if key == "scores":
            raise RuntimeError("simulated task-only failure")
        return super().__getitem__(key)


class _RecordingHttpClient:
    def __init__(self) -> None:
        self.bodies: list[Any] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.bodies.append(body)
        return 200, {"id": "x"}


def _build_isolated_adapter(
    exporter: InMemorySpanExporter, http_client: _RecordingHttpClient
) -> LangfuseAdapter:
    from langfuse import Langfuse

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sdk = Langfuse(
        host="http://localhost:1",
        public_key="pk-test",
        secret_key="sk-test",
        tracer_provider=provider,
    )
    adapter = LangfuseAdapter(client=http_client, tracing_client=sdk)  # type: ignore[arg-type]
    # DEBT-18 (Atchim suggestion): _item_cache only holds dataset_id now
    # (never the golden) — the GOLDEN_SENTINEL is planted directly in the
    # build_score_inputs(golden=...) call below instead.
    adapter._item_cache = {"item-1": "ds-1"}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    return adapter


def main() -> None:
    scenario = sys.argv[1]
    exporter = InMemorySpanExporter()
    http_client = _RecordingHttpClient()
    adapter = _build_isolated_adapter(exporter, http_client)
    metadata: RunMetadata = {"action_id": "a", "action_version": "v", "golden_version": "g"}

    if scenario == "happy":
        scores = build_score_inputs(
            golden={
                "fields": {
                    "total": {"value": GOLDEN_SENTINEL, "type": "number", "critical": True}
                }
            },
            verdicts={
                "total": {
                    "verdict": "wrong_value",
                    "expected": EXPECTED_SENTINEL,
                    "actual": ACTUAL_SENTINEL,
                    "confidence": 0.42,
                    "critical": True,
                    "type": "number",
                }
            },
            gate="FAIL",
            run_id=f"run-{scenario}",
            document_id=PATH_SENTINEL,
        )
        records: list[Any] = [
            {"item_id": "item-1", "document_id": PATH_SENTINEL, "scores": scores}
        ]
    elif scenario == "failure":
        # A well-shaped "scores" list (passes the FU-01.3-B shape
        # precondition / REG-09 and the run_id derivation check) but the
        # record itself explodes on ``record["scores"]`` subscript access
        # -- a genuine RuntimeError inside the REAL task, in this
        # isolated subprocess. See `_ScoresExplodeOnSubscript`'s
        # docstring above (DEBT-39 twin, found live): a score missing
        # "value" no longer reaches here at all -- it's rejected at the
        # precondition now that FO-2 made "value" a checked field.
        records = [
            _ScoresExplodeOnSubscript(
                item_id="item-1",
                document_id="doc-1",
                scores=[
                    {
                        "id": score_id(
                            run_id=f"run-{scenario}", document_id="doc-1", score_name="gate"
                        ),
                        "name": "gate",
                        "value": "PASS",
                    }
                ],
            )
        ]
    else:
        raise SystemExit(f"unknown scenario: {scenario!r}")

    error_type: str | None = None
    try:
        adapter.record_run(
            dataset_name="ds",
            run_name=f"test-s013-run-{scenario}",
            run_id=f"run-{scenario}",
            records=records,
            metadata=metadata,
        )
    except (FlushFailedError, ExperimentRecordFailedError) as exc:
        error_type = type(exc).__name__

    spans = [dict(span.attributes or {}) for span in exporter.get_finished_spans()]
    print(
        json.dumps(
            {"error_type": error_type, "spans": spans, "score_bodies": http_client.bodies}
        )
    )


if __name__ == "__main__":
    main()
