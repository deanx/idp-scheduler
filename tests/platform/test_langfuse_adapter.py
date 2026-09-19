"""LangfuseAdapter — get_dataset / record_run / mark_run_status.

All unit tests run against a mocked HttpClient (no network, no Langfuse
credentials required) — the Protocol seam defined in
``idp_regression.platform.transport.HttpClient``.

R1 (Atchim, critical): the live ``GET /api/public/v2/datasets/{name}``
response on Langfuse 4.38.0 carries NO ``items`` key — dataset items come
from the separate, paginated ``GET /api/public/dataset-items?datasetName=``
(``{"data": [...], "meta": {"page","limit","totalItems","totalPages"}}``).
The mocks below match that real shape (live-probed 2026-09-19), not an
invented ``items`` key.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
)
from idp_regression.platform.langfuse_adapter import LangfuseAdapter, make_platform
from idp_regression.platform.scoring import RUN_LEVEL_TRACE_SENTINEL, trace_id


class FakeHttpClient:
    """Records calls and returns pre-scripted (status, body) responses."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        key = (method, path)
        if key in self._responses:
            return self._responses[key]
        # allow a path-prefix match for parameterized paths (query strings)
        for (m, p), resp in self._responses.items():
            if m == method and path.startswith(p):
                return resp
        raise AssertionError(f"unexpected call: {method} {path}")


def _v2_dataset_response(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """The real (R1-confirmed) shape of GET /api/public/v2/datasets/{name} —
    no ``items`` key."""
    return {
        "id": "cmu7ye9y6002umn07sjy4jw9q",
        "projectId": "cmu5v7o3h0006l106py27q6c7",
        "name": "spike-01",
        "description": None,
        "metadata": None,
        "inputSchema": None,
        "expectedOutputSchema": schema,
        "createdAt": "2026-09-19T05:35:16.447Z",
        "updatedAt": "2026-09-19T05:35:16.447Z",
    }


def _dataset_items_page(
    items: list[dict[str, Any]], *, page: int, total_pages: int
) -> dict[str, Any]:
    meta = {"page": page, "limit": 100, "totalItems": len(items), "totalPages": total_pages}
    return {"data": items, "meta": meta}


def test_get_dataset_returns_items_and_expected_output_schema() -> None:
    schema = {"type": "object", "required": ["fields"], "properties": {}}
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(schema)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (
                200,
                _dataset_items_page(
                    [
                        {
                            "id": "item-1",
                            "input": {"document_id": "invoice-007.pdf"},
                            "expectedOutput": {
                                "fields": {"total": {"value": "1250.00", "type": "number"}}
                            },
                        }
                    ],
                    page=1,
                    total_pages=1,
                ),
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert dataset["expected_output_schema"] == schema
    assert dataset["items"] == [
        {
            "item_id": "item-1",
            "document_id": "invoice-007.pdf",
            "golden": {"fields": {"total": {"value": "1250.00", "type": "number"}}},
        }
    ]


def test_get_dataset_returns_none_schema_when_dataset_has_no_schema() -> None:
    """Gap 3: an absent expectedOutputSchema must return None, not raise
    and not be silently dropped from the Dataset shape (the orchestrator
    turns None into schema_drift, ADR-0005 Decision #8)."""
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (
                200,
                _dataset_items_page([], page=1, total_pages=1),
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert dataset["expected_output_schema"] is None


def test_get_dataset_paginates_through_all_pages() -> None:
    def item(i: int) -> dict[str, Any]:
        return {
            "id": f"item-{i}",
            "input": {"document_id": f"doc-{i}"},
            "expectedOutput": {"fields": {}},
        }

    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01&page=1"): (
                200,
                _dataset_items_page([item(1)], page=1, total_pages=2),
            ),
            ("GET", "/api/public/dataset-items?datasetName=spike-01&page=2"): (
                200,
                _dataset_items_page([item(2)], page=2, total_pages=2),
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert [i["item_id"] for i in dataset["items"]] == ["item-1", "item-2"]


def test_get_dataset_url_encodes_the_dataset_name() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike%2001"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike%2001"): (
                200,
                _dataset_items_page([], page=1, total_pages=1),
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    adapter.get_dataset("spike 01")  # a raw space must be encoded, never sent verbatim

    called_paths = [path for _, path, _ in client.calls]
    assert any("spike 01" not in p for p in called_paths)
    assert all("spike 01" not in p for p in called_paths)


def test_get_dataset_404_raises_typed_dataset_fetch_failed_error() -> None:
    client = FakeHttpClient(
        {("GET", "/api/public/v2/datasets/missing"): (404, {"message": "not found"})}
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("missing")


def test_get_dataset_5xx_raises_typed_dataset_fetch_failed_error() -> None:
    client = FakeHttpClient({("GET", "/api/public/v2/datasets/spike-01"): (503, "down")})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("spike-01")


def test_get_dataset_items_fetch_failure_raises_typed_error() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (500, "boom"),
        }
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("spike-01")


def test_get_dataset_malformed_item_raises_typed_error_not_key_error() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (
                200,
                _dataset_items_page([{"id": "item-1", "input": {}}], page=1, total_pages=1),
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("spike-01")


def test_get_dataset_error_message_never_echoes_the_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (
                400,
                {"message": "golden-shaped-value-should-not-leak: total=1250.00"},
            )
        }
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError) as excinfo:
        adapter.get_dataset("spike-01")

    assert "1250.00" not in str(excinfo.value)
    assert "1250.00" not in caplog.text


def test_mark_run_status_writes_a_run_status_score() -> None:
    client = FakeHttpClient({("POST", "/api/public/scores"): (200, {"id": "x"})})
    adapter = LangfuseAdapter(client=client)

    adapter.mark_run_status(
        "run-1",
        "aborted",
        action_id="action-1",
        action_version="v1",
        golden_version="deadbeef",
    )

    method, path, body = client.calls[-1]
    assert method == "POST"
    assert path == "/api/public/scores"
    assert body["name"] == "run_status"
    assert body["value"] == "aborted"
    assert body["dataType"] == "CATEGORICAL"
    assert body["traceId"] == trace_id(run_id="run-1", document_id=RUN_LEVEL_TRACE_SENTINEL)


def test_mark_run_status_failure_raises_typed_error() -> None:
    client = FakeHttpClient({("POST", "/api/public/scores"): (500, "boom")})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(RunStatusWriteFailedError):
        adapter.mark_run_status(
            "run-1",
            "complete",
            action_id="a",
            action_version="v",
            golden_version="g",
        )


def test_make_platform_dispatches_on_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")

    adapter = make_platform()

    assert isinstance(adapter, LangfuseAdapter)


def test_make_platform_raises_on_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "not-a-real-platform")

    with pytest.raises(ValueError):
        make_platform()


# --- /test gap-fill: ScoreWriteFailedError (langfuse_adapter.py:178) -------


class _FakeExperimentItemResult:
    def __init__(self, item: Any, trace_id_: str, dataset_run_id: str) -> None:
        self.item = item
        self.trace_id = trace_id_
        self.dataset_run_id = dataset_run_id


class _FakeExperimentResult:
    def __init__(self, item_results: list[_FakeExperimentItemResult]) -> None:
        self.item_results = item_results


class _CompletingTracingClient:
    """A fake ExperimentRunner that always completes successfully (no
    network, no OTLP) — lets record_run reach its score-write loop."""

    def run_experiment(self, **kwargs: Any) -> _FakeExperimentResult:
        results = [
            _FakeExperimentItemResult(
                item=item, trace_id_=f"trace-{item.id}", dataset_run_id="run-x"
            )
            for item in kwargs["data"]
        ]
        for item in kwargs["data"]:
            kwargs["task"](item=item)
        return _FakeExperimentResult(results)

    def flush(self) -> None:
        return None


def test_record_run_score_write_5xx_raises_score_write_failed_error() -> None:
    """Gap 1: ScoreWriteFailedError was never exercised. A 5xx on the
    score POST, inside an otherwise-completing record_run (dataset
    fetch + experiment linkage both succeed), must surface as the typed
    error from langfuse_adapter.py's _write_scores (~:178)."""
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/ds"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=ds"): (
                200,
                _dataset_items_page(
                    [
                        {
                            "id": "item-1",
                            "input": {"document_id": "doc-0"},
                            "expectedOutput": {"fields": {}},
                        }
                    ],
                    page=1,
                    total_pages=1,
                ),
            ),
            ("POST", "/api/public/scores"): (500, {"message": "boom"}),
        }
    )
    adapter = LangfuseAdapter(client=client, tracing_client=_CompletingTracingClient())
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": dataset["items"][0]["item_id"],
            "document_id": dataset["items"][0]["document_id"],
            "scores": [{"id": "s1", "name": "gate", "value": "PASS", "comment": None}],
        }
    ]

    with pytest.raises(ScoreWriteFailedError):
        adapter.record_run(
            dataset_name="ds",
            run_name="run-1",
            run_id="run-1",
            records=records,  # type: ignore[arg-type]
            metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
        )


def test_record_run_never_posts_to_the_v4_trace_ingestion_endpoint() -> None:
    """Gap 8 (TP-38, runtime): a full, successfully-completing record_run
    (dataset fetch + score writes, over the REST HttpClient seam) never
    issues a call to /api/public/ingestion -- scores go via
    /api/public/scores; trace ingestion is the SDK's own OTLP exporter,
    confined to make_platform(), never this module's raw-REST path."""
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/ds"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=ds"): (
                200,
                _dataset_items_page(
                    [
                        {
                            "id": "item-1",
                            "input": {"document_id": "doc-0"},
                            "expectedOutput": {"fields": {}},
                        }
                    ],
                    page=1,
                    total_pages=1,
                ),
            ),
            ("POST", "/api/public/scores"): (200, {"id": "x"}),
        }
    )
    adapter = LangfuseAdapter(client=client, tracing_client=_CompletingTracingClient())
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": dataset["items"][0]["item_id"],
            "document_id": dataset["items"][0]["document_id"],
            "scores": [{"id": "s1", "name": "gate", "value": "PASS", "comment": None}],
        }
    ]

    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=records,  # type: ignore[arg-type]
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )

    assert all("/api/public/ingestion" not in path for _, path, _ in client.calls)


def test_get_dataset_error_log_survives_a_newline_in_the_dataset_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SCENARIO-B (/test, gap 9, NFR N5 log injection — S-01.3 AC): a
    dataset name containing "\\n" or '"' must not be able to forge what
    looks like a second, independent log line to a naive log
    tailer/alerting pipeline -- the rendered log message must stay on
    one line (escaped or JSON-quoted), not contain a raw embedded
    newline. RED as written: langfuse_adapter.py's %-style logging
    (`logger.error("dataset_fetch_failed status=%s dataset=%s
    detail=%s", status, name, ...)`) does not escape `name`, so an
    embedded "\\n" reaches the rendered message verbatim (confirmed via
    caplog). Not fixed here per /test rules (tests only, no
    implementation changes) — this is an NFR N5 gap in S-01.3's own
    logging helper (`_body_snippet_for_error`'s callers), to be routed
    as a card, not a pre-existing unrelated bug."""
    caplog.set_level(logging.ERROR)
    malicious_name = 'evil\ninjected fake log line status=200 dataset="ok"'
    client = FakeHttpClient({("GET", "/api/public/v2/datasets/"): (404, {})})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset(malicious_name)

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"


def test_platform_adapter_protocol_has_no_get_golden_version() -> None:
    """Gap 9 (INV-04 single-fetch): golden_version is the caller's
    hash_dataset(dataset) over the SAME fetch it iterates (TOCTOU guard)
    -- there must be no get_golden_version method on the PlatformAdapter
    Protocol tempting a second, separately-timed fetch."""
    from idp_regression.platform.types import PlatformAdapter

    assert not hasattr(PlatformAdapter, "get_golden_version")
    assert not hasattr(LangfuseAdapter, "get_golden_version")
