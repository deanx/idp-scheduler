"""INV-01 — document files never enter the evaluation platform.

Every payload the platform module sends over the wire (dataset fetch is
read-only; score writes, the run_status marker, and record_run's
task-output span are the write paths) must carry only ``document_id`` +
golden-derived content — never file bytes or a file-path blob.

TP-45 (Atchim R3): the span-attribute allowlist from ADR-0005 #9 —
``input={"document_id"}``, ``expected_output`` (the stored golden),
``output`` (``DocumentRecord.actual`` only), ``RunMetadata`` strings, item
``metadata=None`` — asserted with a fake tracing client that records
exactly what ``record_run`` hands to ``run_experiment``.
"""

from __future__ import annotations

import re
from typing import Any

from idp_regression.classifier.types import NormalizedOutput
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.scoring import build_score_inputs
from idp_regression.platform.tracing import ExperimentItem

_FILE_PATH_LIKE = re.compile(r"(/[\w.\-]+){2,}|\.pdf\b|\.png\b|\.jpg\b")


class RecordingHttpClient:
    def __init__(self) -> None:
        self.bodies: list[Any] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.bodies.append(body)
        return 200, {"id": "x", "data": [], "meta": {"totalPages": 1}}


class _FakeItemResult:
    def __init__(self, item: ExperimentItem, trace_id: str, dataset_run_id: str) -> None:
        self.item = item
        self.trace_id = trace_id
        self.dataset_run_id = dataset_run_id


class _FakeResult:
    def __init__(self, item_results: list[_FakeItemResult]) -> None:
        self.item_results = item_results


class RecordingTracingClient:
    """Captures exactly what record_run hands to run_experiment (TP-45)."""

    def __init__(self) -> None:
        self.run_experiment_calls: list[dict[str, Any]] = []
        self.task_outputs: list[Any] = []

    def run_experiment(self, **kwargs: Any) -> _FakeResult:
        self.run_experiment_calls.append(kwargs)
        results = []
        for item in kwargs["data"]:
            output = kwargs["task"](item=item)
            self.task_outputs.append(output)
            results.append(
                _FakeItemResult(item=item, trace_id=f"trace-{item.id}", dataset_run_id="run-x")
            )
        return _FakeResult(results)

    def flush(self) -> None:
        return None


def test_write_scores_payload_never_carries_a_file_path_or_bytes() -> None:
    client = RecordingHttpClient()
    adapter = LangfuseAdapter(client=client, tracing_client=RecordingTracingClient())
    adapter._item_cache = {
        "item-1": ("ds-1", {"fields": {"total": {"value": "1250.00", "type": "number"}}})
    }  # noqa: SLF001
    scores = build_score_inputs(
        golden={
            "document_id": "invoice-007.pdf",
            "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        },
        verdicts={
            "total": {
                "verdict": "match",
                "expected": "1250.00",
                "actual": "1250.00",
                "confidence": 0.9,
                "critical": True,
                "type": "number",
            }
        },
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[
            {
                "item_id": "item-1",
                "document_id": "invoice-007.pdf",
                "actual": {"status": "SUCCEEDED", "fields": {}},
                "scores": scores,
            }
        ],
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )

    score_bodies = [b for b in client.bodies if isinstance(b, dict) and b.get("name")]
    for body in score_bodies:
        assert isinstance(body, dict)
        for value in body.values():
            if isinstance(value, str):
                assert not _FILE_PATH_LIKE.search(value), f"file-path-like content in {value!r}"
        assert "file" not in body
        assert "path" not in body
        assert "bytes" not in body


def test_mark_run_status_payload_never_carries_a_file_path_or_bytes() -> None:
    client = RecordingHttpClient()
    adapter = LangfuseAdapter(client=client)

    adapter.mark_run_status(
        "run-1",
        "complete",
        action_id="action-1",
        action_version="v1",
        golden_version="deadbeef",
    )

    for body in client.bodies:
        assert "file" not in body
        assert "path" not in body
        assert "bytes" not in body


# --- TP-45: span-attribute allowlist (ADR-0005 #9) -------------------------

_ALLOWED_TASK_INPUT_KEYS = {"document_id"}


def test_experiment_item_input_contains_only_document_id() -> None:
    """Span-attribute allowlist: input = {"document_id"}, nothing else."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[
            {
                "item_id": "item-1",
                "document_id": "/etc/secret/invoice-007.pdf",
                "actual": {"status": "SUCCEEDED", "fields": {}},
                "scores": [],
            }
        ],
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )

    item = tracing_client.run_experiment_calls[0]["data"][0]
    assert set(item.input.keys()) == _ALLOWED_TASK_INPUT_KEYS
    assert item.input["document_id"] == "/etc/secret/invoice-007.pdf"  # id only, not file bytes
    assert item.metadata is None


def test_experiment_task_output_is_only_the_normalized_actual_never_exception_text() -> None:
    """output = DocumentRecord.actual only; on any task failure the
    output is a fixed constant, never str(exception) (ADR-0005 #9
    defense in depth)."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001
    actual: NormalizedOutput = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "1250.00", "confidence": 0.9}},
    }

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[{"item_id": "item-1", "document_id": "doc-1", "actual": actual, "scores": []}],
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )

    assert tracing_client.task_outputs == [{"actual": actual}]


def test_run_metadata_forwarded_as_experiment_metadata() -> None:
    """RunMetadata's three strings become run_experiment's metadata kwarg
    (the SDK attaches it to the dataset run, per its own docs)."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[
            {
                "item_id": "item-1",
                "document_id": "doc-1",
                "actual": {"status": "SUCCEEDED", "fields": {}},
                "scores": [],
            }
        ],
        metadata={"action_id": "action-1", "action_version": "v1", "golden_version": "deadbeef"},
    )

    call = tracing_client.run_experiment_calls[0]
    assert call["metadata"] == {
        "action_id": "action-1",
        "action_version": "v1",
        "golden_version": "deadbeef",
    }
