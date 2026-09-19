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

import json
import os
import re
import subprocess
import sys
from pathlib import Path
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
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
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
    adapter._cached_dataset_name = "ds"  # noqa: SLF001

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
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
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
    adapter._cached_dataset_name = "ds"  # noqa: SLF001

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


# --- TP-45 (Atchim R3, round 3): a SUBPROCESS running the REAL langfuse
# SDK + a REAL OTel InMemorySpanExporter — no fake client, and no
# reliance on process-shared OTel global state. OTel registers its
# TracerProvider process-globally on first use, and (confirmed
# empirically 2026-09-19) the langfuse SDK's run_experiment
# task-execution path resolves its tracer through that global
# registration rather than strictly through the `tracer_provider=`
# instance passed to a given client — so even an explicit, private
# TracerProvider is NOT sufficient once earlier tests in the SAME
# process have already claimed the global slot (this test flipped
# between getting 0 spans / a different client's export errors
# depending on suite/test order before this fix). A fresh subprocess
# has no such prior registration, so the scenario script in
# `_tp45_subprocess_scenario.py` is deterministic regardless of order.

_PATH_SENTINEL = "/IDP_DOCUMENT_DIR/invoice-007.pdf"

# The only span-attribute keys the SDK's run_experiment is known to set
# (live/local-probed 2026-09-19) — none of them may carry raw exception
# text or a file-path blob beyond the document_id value itself.
_KNOWN_SPAN_ATTRIBUTE_KEYS = {
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.observation.type",
    "langfuse.observation.metadata.experiment_name",
    "langfuse.observation.metadata.experiment_run_name",
    "langfuse.observation.metadata.dataset_id",
    "langfuse.observation.metadata.dataset_item_id",
    # RunMetadata (action_id/action_version/golden_version), forwarded as
    # run_experiment's metadata= kwarg — the only "extra" allowlisted
    # content per ADR-0005 #9 ("trace metadata = the three RunMetadata
    # strings"); the SDK attaches it under both prefixes.
    "langfuse.observation.metadata.action_id",
    "langfuse.observation.metadata.action_version",
    "langfuse.observation.metadata.golden_version",
    "langfuse.experiment.metadata.action_id",
    "langfuse.experiment.metadata.action_version",
    "langfuse.experiment.metadata.golden_version",
    "langfuse.environment",
    "langfuse.experiment.id",
    "langfuse.experiment.name",
    "langfuse.experiment.dataset.id",
    "langfuse.experiment.item.id",
    "langfuse.experiment.item.expected_output",
    "langfuse.experiment.item.root_observation_id",
    "langfuse.internal.is_app_root",
}


def _run_tp45_subprocess_scenario(scenario: str) -> dict[str, Any]:
    script = Path(__file__).parent / "_tp45_subprocess_scenario.py"
    # Strip LANGFUSE_* env vars: with them set, the SDK's OTLP exporter
    # target gets redirected to the real host regardless of the explicit
    # `host=` constructor arg (confirmed empirically) — stripping them
    # makes the subprocess deterministically hit the unreachable
    # "http://localhost:1" host instead.
    env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_")}
    result = subprocess.run(
        [sys.executable, str(script), scenario],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess scenario {scenario!r} failed: "
        f"stdout={result.stdout!r} stderr={result.stderr[-2000:]!r}"
    )
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)  # type: ignore[no-any-return]


def test_real_sdk_span_allowlist_and_path_sentinel_via_isolated_subprocess() -> None:
    """R3: the full ADR-0005 #9 allowlist, asserted over EVERY attribute of
    a REAL span from the REAL langfuse SDK + a REAL InMemorySpanExporter,
    run in an isolated subprocess (see the module note above). A
    path-sentinel is planted as ``document_id`` (the one place a
    path-shaped string is allowed to appear, INV-01) and must never
    appear anywhere else on any span."""
    result = _run_tp45_subprocess_scenario("happy")

    assert result["error_type"] == "FlushFailedError"
    spans = result["spans"]
    assert len(spans) > 0
    for attributes in spans:
        assert set(attributes.keys()) <= _KNOWN_SPAN_ATTRIBUTE_KEYS, (
            f"unexpected span attribute key(s): "
            f"{set(attributes.keys()) - _KNOWN_SPAN_ATTRIBUTE_KEYS}"
        )
        for key, value in attributes.items():
            text = str(value)
            if key == "langfuse.observation.input":
                assert _PATH_SENTINEL in text  # the one allowed place
                assert json.loads(text) == {"document_id": _PATH_SENTINEL}
            else:
                assert _PATH_SENTINEL not in text, f"sentinel leaked into {key!r}: {text!r}"
            assert "Traceback" not in text


def test_real_sdk_span_output_is_the_task_failed_constant_via_isolated_subprocess() -> None:
    """R3: exercises the REAL failure branch (a malformed record missing
    the required "actual" key raises a genuine KeyError inside the total
    task, in an isolated subprocess) — the span's output must be the
    fixed constant, never str(KeyError(...))."""
    result = _run_tp45_subprocess_scenario("failure")

    assert result["error_type"] == "FlushFailedError"
    spans = result["spans"]
    assert len(spans) > 0
    for attributes in spans:
        raw_output = attributes.get("langfuse.observation.output")
        assert raw_output is not None
        output = str(raw_output)
        assert json.loads(output) == {"record_error": "task_failed"}
        assert "KeyError" not in output
        assert "actual" not in output  # the missing-key name never leaks either
