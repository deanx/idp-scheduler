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

Usage: ``python _tp45_subprocess_scenario.py {happy|failure}``
Prints one JSON line to stdout: ``{"error_type": str|null, "spans": [...]}``.
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
from idp_regression.platform.types import RunMetadata

PATH_SENTINEL = "/IDP_DOCUMENT_DIR/invoice-007.pdf"


class _RecordingHttpClient:
    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return 200, {"id": "x"}


def _build_isolated_adapter(exporter: InMemorySpanExporter) -> LangfuseAdapter:
    from langfuse import Langfuse

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sdk = Langfuse(
        host="http://localhost:1",
        public_key="pk-test",
        secret_key="sk-test",
        tracer_provider=provider,
    )
    adapter = LangfuseAdapter(client=_RecordingHttpClient(), tracing_client=sdk)  # type: ignore[arg-type]
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    return adapter


def main() -> None:
    scenario = sys.argv[1]
    exporter = InMemorySpanExporter()
    adapter = _build_isolated_adapter(exporter)
    metadata: RunMetadata = {"action_id": "a", "action_version": "v", "golden_version": "g"}

    if scenario == "happy":
        records: list[Any] = [
            {
                "item_id": "item-1",
                "document_id": PATH_SENTINEL,
                "actual": {"status": "SUCCEEDED", "fields": {"total": {"value": "100.00"}}},
                "scores": [],
            }
        ]
    elif scenario == "failure":
        records = [{"item_id": "item-1", "document_id": "doc-1", "scores": []}]  # no "actual"
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
    print(json.dumps({"error_type": error_type, "spans": spans}))


if __name__ == "__main__":
    main()
