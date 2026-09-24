"""INV-01 — document files never enter the evaluation platform.

Every payload the platform module sends over the wire (dataset fetch is
read-only; score writes, the run_status marker, and record_run's
task-output span are the write paths) must carry only ``document_id`` +
golden-derived content — never file bytes or a file-path blob.

TP-45 / DEBT-18 (user decision, option B — no expected/actual/confidence
value ever leaves the app): the span-attribute allowlist from ADR-0005
#9, tightened — ``input={"document_id"}``, ``expected_output={}`` (the
golden is NEVER copied into a span; it lives only in its Langfuse
dataset item), ``output`` = the verdict map only (score name -> score
value, e.g. ``"match"``/``"PASS"`` — never a raw extracted/expected
value or a confidence number), ``RunMetadata`` strings, item
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

from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.scoring import build_score_inputs, score_id
from idp_regression.platform.tracing import ExperimentItem
from idp_regression.platform.types import ScoreInput

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
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"
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
                "scores": scores,
            }
        ],
        metadata={
            "action_id": "a",
            "action_version": "v",
            "golden_version": "g",
            "golden_dataset_name": "d",
        },
    )

    score_bodies = [b for b in client.bodies if isinstance(b, dict) and b.get("name")]
    assert len(score_bodies) > 0  # a body-content check over an empty list proves nothing
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
        golden_dataset_name="dataset-1",
    )

    assert len(client.bodies) > 0
    for body in client.bodies:
        # gap 10: assert on VALUES too, not just key absence — a key
        # named "file"/"path"/"bytes" is not the only way to leak one.
        for value in body.values():
            if isinstance(value, str):
                assert not _FILE_PATH_LIKE.search(value), f"file-path-like content: {value!r}"
        assert "file" not in body
        assert "path" not in body
        assert "bytes" not in body

    # gap 10: RunMetadata (action_id/action_version/golden_version) must
    # actually be present in the posted body — ADR-0004 #14's run_status
    # marker is meant to carry it, and INV-01/INV-04 both depend on it
    # being posted, not silently dropped.
    posted_text = json.dumps(client.bodies)
    assert "action-1" in posted_text
    assert "v1" in posted_text
    assert "deadbeef" in posted_text


def test_record_run_completes_and_posts_no_sentinel_value_in_any_score_body() -> None:
    """Atchim (round 4, Required): the subprocess-based TP-45 test's
    happy scenario always raises FlushFailedError before _write_scores
    runs (unreachable host), so its score_bodies list is structurally
    always empty — a content-check loop there proves nothing (Atchim
    proved this by mutating _write_scores to post
    `repr(self._item_cache)` as every score's comment; every test still
    passed). This test uses the FAKE tracing client instead, so
    record_run actually COMPLETES and reaches _write_scores — a
    distinctive golden sentinel is planted (via build_score_inputs'
    `golden=`/`verdicts=` arguments) and must be absent from every
    posted score body.

    Note: Atchim's exact mutation (`repr(self._item_cache)`) no longer
    leaks the sentinel, because the companion fix in this same change
    (Atchim's suggestion) made `_item_cache` hold only `dataset_id`, not
    the golden — there is nothing left in it to leak. Verified this
    test still catches a leaking comment in general by temporarily
    hardcoding a sentinel-bearing literal into `_write_scores`'s
    `comment` field: the assertion fired correctly (`golden_sentinel not
    in json.dumps(body)` failed as expected), then reverted; not
    committed.
    """
    golden_sentinel = "SENTINEL-GOLDEN-VALUE-7f3a"
    client = RecordingHttpClient()
    adapter = LangfuseAdapter(client=client, tracing_client=RecordingTracingClient())
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"
    scores = build_score_inputs(
        golden={
            "fields": {"total": {"value": golden_sentinel, "type": "number", "critical": True}}
        },
        verdicts={
            "total": {
                "verdict": "match",
                "expected": golden_sentinel,
                "actual": golden_sentinel,
                "confidence": 0.9,
                "critical": True,
                "type": "number",
            }
        },
        gate="PASS",
        run_id="run-1",
        document_id="doc-1",
    )

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[{"item_id": "item-1", "document_id": "doc-1", "scores": scores}],
        metadata={
            "action_id": "a",
            "action_version": "v",
            "golden_version": "g",
            "golden_dataset_name": "d",
        },
    )

    score_bodies = [b for b in client.bodies if isinstance(b, dict) and b.get("name")]
    assert len(score_bodies) > 0
    for body in score_bodies:
        assert golden_sentinel not in json.dumps(body)


# --- TP-45: span-attribute allowlist (ADR-0005 #9) -------------------------

_ALLOWED_TASK_INPUT_KEYS = {"document_id"}


def test_experiment_item_input_contains_only_document_id() -> None:
    """Span-attribute allowlist: input = {"document_id"}, nothing else."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[
            {
                "item_id": "item-1",
                "document_id": "/etc/secret/invoice-007.pdf",
                "scores": [],
            }
        ],
        metadata={
            "action_id": "a",
            "action_version": "v",
            "golden_version": "g",
            "golden_dataset_name": "d",
        },
    )

    item = tracing_client.run_experiment_calls[0]["data"][0]
    assert set(item.input.keys()) == _ALLOWED_TASK_INPUT_KEYS
    assert item.input["document_id"] == "/etc/secret/invoice-007.pdf"  # id only, not file bytes
    assert item.metadata is None


def test_experiment_item_expected_output_is_never_the_golden() -> None:
    """DEBT-18 option B: expected_output is ALWAYS {} — the golden lives
    only in its Langfuse dataset item, spans reference the item by id,
    never a copy of the golden's field values. Atchim suggestion:
    _item_cache no longer even HOLDS the golden (only dataset_id) —
    there's nothing left to leak from that seam."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[{"item_id": "item-1", "document_id": "doc-1", "scores": []}],
        metadata={
            "action_id": "a",
            "action_version": "v",
            "golden_version": "g",
            "golden_dataset_name": "d",
        },
    )

    item = tracing_client.run_experiment_calls[0]["data"][0]
    assert item.expected_output == {}


def test_experiment_task_output_is_the_verdict_map_never_a_raw_value() -> None:
    """DEBT-18 option B: output = {score_name: score_value} derived from
    record["scores"] — score values are verdict literals ("match",
    "wrong_value", "PASS"/"FAIL"), never a raw extracted/expected value
    or a confidence number. On any task failure the output is the fixed
    constant, never str(exception) (ADR-0005 #9 defense in depth)."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"
    scores: list[ScoreInput] = [
        {
            "id": score_id(run_id="run-1", document_id="doc-1", score_name="field:total"),
            "name": "field:total",
            "value": "wrong_value",
            "comment": None,
        },
        {
            "id": score_id(run_id="run-1", document_id="doc-1", score_name="gate"),
            "name": "gate",
            "value": "FAIL",
            "comment": None,
        },
    ]

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[{"item_id": "item-1", "document_id": "doc-1", "scores": scores}],
        metadata={
            "action_id": "a",
            "action_version": "v",
            "golden_version": "g",
            "golden_dataset_name": "d",
        },
    )

    assert tracing_client.task_outputs == [{"field:total": "wrong_value", "gate": "FAIL"}]


def test_run_metadata_forwarded_as_experiment_metadata() -> None:
    """RunMetadata's three strings become run_experiment's metadata kwarg
    (the SDK attaches it to the dataset run, per its own docs)."""
    client = RecordingHttpClient()
    tracing_client = RecordingTracingClient()
    adapter = LangfuseAdapter(client=client, tracing_client=tracing_client)
    adapter._item_cache = {"item-1": "ds-1"}
    adapter._cached_dataset_name = "ds"

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=[
            {
                "item_id": "item-1",
                "document_id": "doc-1",
                "scores": [],
            }
        ],
        metadata={
            "action_id": "action-1",
            "action_version": "v1",
            "golden_version": "deadbeef",
            "golden_dataset_name": "dataset-1",
        },
    )

    call = tracing_client.run_experiment_calls[0]
    assert call["metadata"] == {
        "action_id": "action-1",
        "action_version": "v1",
        "golden_version": "deadbeef",
        "golden_dataset_name": "dataset-1",
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
_GOLDEN_SENTINEL = "SENTINEL-GOLDEN-VALUE-a91cf3"
_EXPECTED_SENTINEL = "SENTINEL-EXPECTED-4f8c1e"
_ACTUAL_SENTINEL = "SENTINEL-ACTUAL-9b2d7a"

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
    # RunMetadata (action_id/action_version/golden_version, widened to
    # four fields by ADR-0004 amendment T-01.4.12 A6 / DEBT-48 to also
    # include golden_dataset_name), forwarded as run_experiment's
    # metadata= kwarg — the only "extra" allowlisted content per
    # ADR-0005 #9 ("trace metadata = the RunMetadata strings"); the SDK
    # attaches it under both prefixes.
    "langfuse.observation.metadata.action_id",
    "langfuse.observation.metadata.action_version",
    "langfuse.observation.metadata.golden_version",
    "langfuse.observation.metadata.golden_dataset_name",
    "langfuse.experiment.metadata.action_id",
    "langfuse.experiment.metadata.action_version",
    "langfuse.experiment.metadata.golden_version",
    "langfuse.experiment.metadata.golden_dataset_name",
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


def test_real_sdk_span_allowlist_and_no_golden_or_actual_sentinel_via_isolated_subprocess() -> (
    None
):
    """R3 / DEBT-18 option B: the full ADR-0005 #9 allowlist, asserted
    over EVERY attribute of a REAL span from the REAL langfuse SDK + a
    REAL InMemorySpanExporter, run in an isolated subprocess (see the
    module note above). A path-sentinel is planted as ``document_id``
    (the one place a path-shaped string is allowed to appear, INV-01);
    distinctive golden/expected/actual/confidence sentinels are planted
    too (`_tp45_subprocess_scenario.py`'s "happy" scenario) — none of
    the golden/expected/actual/confidence sentinels may appear ANYWHERE,
    including inside ``langfuse.observation.input``, and NOT in any
    score payload the adapter posted either."""
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
            assert _GOLDEN_SENTINEL not in text, f"golden sentinel leaked into {key!r}: {text!r}"
            assert _EXPECTED_SENTINEL not in text, f"expected sentinel leaked into {key!r}"
            assert _ACTUAL_SENTINEL not in text, f"actual sentinel leaked into {key!r}"
            assert "0.42" not in text  # the confidence sentinel
            assert "Traceback" not in text

    # Atchim (round 4): this scenario's host is unreachable by design (no
    # live creds needed), so record_experiment ALWAYS raises
    # FlushFailedError before record_run's score-write loop ever runs —
    # score_bodies is therefore always []. A content-check loop over an
    # empty list asserts nothing (Atchim's mutation proof: posting
    # `repr(self._item_cache)` as every score's comment still passed
    # every test here). The real, meaningful score-payload-leak check is
    # `test_record_run_completes_and_posts_no_sentinel_value_in_any_score_body`
    # below, which uses a fake tracing client that actually completes
    # record_run and reaches _write_scores.
    assert result["score_bodies"] == []


def test_real_sdk_span_output_is_the_task_failed_constant_via_isolated_subprocess() -> None:
    """R3: exercises the REAL failure branch (a malformed record missing
    the required "scores" key raises a genuine KeyError inside the total
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
        assert "scores" not in output  # the missing-key name never leaks either
