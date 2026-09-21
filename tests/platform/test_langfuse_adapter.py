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
    PlatformConfigurationError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
    TransportError,
)
from idp_regression.platform.langfuse_adapter import LangfuseAdapter, make_platform
from idp_regression.platform.scoring import RUN_LEVEL_TRACE_SENTINEL, score_id, trace_id


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

def _derived_score(
    *, document_id: str, name: str, value: str, run_id: str = "run-1"
) -> dict[str, Any]:
    """A ScoreInput whose id is derived from the SAME run_id record_run is
    called with — record_run's A3 precondition (ADR-0005 #9) refuses any
    other id, so fixtures must use the real derivation, not a stub."""
    return {
        "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
        "name": name,
        "value": value,
        "comment": None,
    }


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
    """QA-01 re-audit F-2 / DoD (b): an isinstance-only assertion here
    would pass even if the factory ignored LANGFUSE_HOST entirely --
    assert the constructed UrllibHttpClient AND the SDK client actually
    carry the host and both keys, not just that SOME LangfuseAdapter
    came back."""
    import base64

    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "distinctive-pub-9f3a")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "distinctive-secret-2c71")
    # Isolation, not a dodge (Atchim R-1): this test's job is the FACTORY's
    # host/key wiring, not the LANGFUSE_HOST/LANGFUSE_BASE_URL split-brain
    # -- that has its own dedicated tests below. An ambient .env (sourced
    # for the live-integration gate) can leak LANGFUSE_BASE_URL in and
    # change what the SDK resolves `_base_url` to regardless of the
    # explicit `host=` arg; unset it here so this test only ever asserts
    # what make_platform() itself did with LANGFUSE_HOST.
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)

    adapter = make_platform()

    assert isinstance(adapter, LangfuseAdapter)

    # -- raw-REST UrllibHttpClient (datasets/schema/scores) --
    http_client = adapter._client  # noqa: SLF001
    assert http_client._host == "https://example.invalid"  # noqa: SLF001
    decoded = base64.b64decode(
        http_client._auth_header.removeprefix("Basic ")  # noqa: SLF001
    ).decode("ascii")
    assert decoded == "distinctive-pub-9f3a:distinctive-secret-2c71"

    # -- Langfuse SDK client (OTLP trace + dataset-run linkage) --
    sdk_client = adapter._tracing_client  # noqa: SLF001
    assert sdk_client._base_url == "https://example.invalid"  # type: ignore[attr-defined]  # noqa: SLF001
    sdk_headers = sdk_client.api._client_wrapper.get_headers()  # type: ignore[attr-defined]
    assert sdk_headers["X-Langfuse-Public-Key"] == "distinctive-pub-9f3a"
    sdk_decoded = base64.b64decode(
        sdk_headers["Authorization"].removeprefix("Basic ")
    ).decode("ascii")
    assert sdk_decoded == "distinctive-pub-9f3a:distinctive-secret-2c71"


def test_make_platform_raises_on_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "not-a-real-platform")

    with pytest.raises(ValueError):
        make_platform()


# --- Atchim R-1 PIN (2026-09-20, new REG -- coordinator records the row):
# LANGFUSE_HOST is the only variable CLAUDE.md's External services table
# documents, but the langfuse SDK's own base_url resolution prioritizes
# LANGFUSE_BASE_URL over the explicit `host=` constructor arg. An operator
# (or an ambient .env) setting both to DIFFERENT values would silently
# split traffic: the raw-REST client authenticates against LANGFUSE_HOST,
# the SDK client authenticates (same credentials) against
# LANGFUSE_BASE_URL -- a credential reaching a host the operator never
# named (same class as REG-07, arriving via env instead of a 3xx), and a
# run's traces/scores split across two platform instances with no marker
# (a silent variant of DEBT-28). Fail closed, before either client is
# constructed.


def test_make_platform_raises_when_langfuse_base_url_disagrees_with_langfuse_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://intended.invalid")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://elsewhere.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")

    with pytest.raises(PlatformConfigurationError) as excinfo:
        make_platform()

    message = str(excinfo.value)
    # INV-02: names the two variable NAMES only -- never their values (a
    # URL can embed a credential, e.g. https://user:pass@host).
    assert "LANGFUSE_HOST" in message
    assert "LANGFUSE_BASE_URL" in message
    assert "intended.invalid" not in message
    assert "elsewhere.invalid" not in message


@pytest.mark.parametrize("base_url_env", [None, "https://example.invalid"])
def test_make_platform_resolves_both_clients_to_the_same_host_when_unset_or_equal(
    monkeypatch: pytest.MonkeyPatch, base_url_env: str | None
) -> None:
    """Sanity: the R-1 fail-closed check isn't always-raising -- when
    LANGFUSE_BASE_URL is unset, or set but equal to LANGFUSE_HOST, both
    the raw-REST client and the SDK client resolve to the SAME host."""
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    if base_url_env is None:
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("LANGFUSE_BASE_URL", base_url_env)

    adapter = make_platform()

    http_client = adapter._client  # noqa: SLF001
    sdk_client = adapter._tracing_client  # noqa: SLF001
    assert http_client._host == "https://example.invalid"  # noqa: SLF001
    assert sdk_client._base_url == "https://example.invalid"  # type: ignore[attr-defined]  # noqa: SLF001


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
    adapter = LangfuseAdapter(
        client=client, tracing_client=_CompletingTracingClient(), sleep=lambda _seconds: None
    )
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": dataset["items"][0]["item_id"],
            "document_id": dataset["items"][0]["document_id"],
            "scores": [
                _derived_score(
                    document_id=dataset["items"][0]["document_id"],
                    name="gate",
                    value="PASS",
                )
            ],
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
            "scores": [
                _derived_score(
                    document_id=dataset["items"][0]["document_id"],
                    name="gate",
                    value="PASS",
                )
            ],
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
    """/test Scenario B, NFR N5 log injection (GREEN, fixed): a dataset
    name containing "\\n" or '"' must not be able to forge what looks
    like a second, independent log line to a naive log tailer/alerting
    pipeline -- the rendered log message must stay on one line. Fixed
    by wrapping caller-controlled strings in
    ``transport.sanitize_for_log`` (``repr()``) before they reach a
    ``%s`` log substitution. Was RED prior to that fix (confirmed via
    caplog, see the commit that added this test)."""
    caplog.set_level(logging.ERROR)
    malicious_name = 'evil\ninjected fake log line status=200 dataset="ok"'
    client = FakeHttpClient({("GET", "/api/public/v2/datasets/"): (404, {})})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset(malicious_name)

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        # Atchim (N5 follow-up): repr() does not escape '"', so a forged
        # field can still be smuggled past a logfmt/key=value parser --
        # the unescaped quoted substring must never appear verbatim.
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"


def test_write_scores_error_log_survives_a_newline_in_document_id_and_score_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N5 sibling: score_write_failed logs document_id and score_name --
    both caller-controlled (document_id from the golden dataset;
    score_name is derived from a field/prompt name). Same fix
    (sanitize_for_log), same class of bug."""
    caplog.set_level(logging.ERROR)
    malicious_document_id = 'doc\ninjected fake log line status=200 dataset="ok"'
    malicious_score_name = "field:evil\ninjected"
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/ds"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=ds"): (
                200,
                _dataset_items_page(
                    [
                        {
                            "id": "item-1",
                            "input": {"document_id": malicious_document_id},
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
    adapter = LangfuseAdapter(
        client=client, tracing_client=_CompletingTracingClient(), sleep=lambda _seconds: None
    )
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": dataset["items"][0]["item_id"],
            "document_id": dataset["items"][0]["document_id"],
            "scores": [
                _derived_score(
                    document_id=dataset["items"][0]["document_id"],
                    name=malicious_score_name,
                    value="FAIL",
                )
            ],
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

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"


def test_mark_run_status_error_log_survives_a_newline_in_run_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N5 sibling: run_status_write_failed logs run_id -- caller-controlled
    (S-01.4 generates it). Same fix (sanitize_for_log)."""
    caplog.set_level(logging.ERROR)
    malicious_run_id = 'run\ninjected fake log line status=200 dataset="ok"'
    client = FakeHttpClient({("POST", "/api/public/scores"): (500, {"message": "boom"})})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(RunStatusWriteFailedError):
        adapter.mark_run_status(
            malicious_run_id,
            "aborted",
            action_id="a",
            action_version="v",
            golden_version="g",
        )

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"


# --- REG-03 / F-2: a bounded retry on score writes (ADR-0005 #9: scores
# are idempotent via the deterministic score_id, so a 5xx/transport
# failure is safe to retry within the transport budget) -----------------


class _QueuedScorePostClient:
    """A FakeHttpClient variant where the ``POST /api/public/scores``
    response is popped from a queue on each call (one entry per attempt)
    -- everything else behaves like FakeHttpClient's static map."""

    def __init__(
        self,
        *,
        static_responses: dict[tuple[str, str], tuple[int, Any]],
        score_post_queue: list[tuple[int, Any] | Exception],
    ) -> None:
        self._static_responses = static_responses
        self._score_post_queue = list(score_post_queue)
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        if method == "POST" and path == "/api/public/scores":
            next_response = self._score_post_queue.pop(0)
            if isinstance(next_response, Exception):
                raise next_response
            return next_response
        key = (method, path)
        if key in self._static_responses:
            return self._static_responses[key]
        for (m, p), resp in self._static_responses.items():
            if m == method and path.startswith(p):
                return resp
        raise AssertionError(f"unexpected call: {method} {path}")


def _dataset_fetch_responses(document_id: str = "doc-0") -> dict[tuple[str, str], tuple[int, Any]]:
    return {
        ("GET", "/api/public/v2/datasets/ds"): (200, _v2_dataset_response(None)),
        ("GET", "/api/public/dataset-items?datasetName=ds"): (
            200,
            _dataset_items_page(
                [
                    {
                        "id": "item-1",
                        "input": {"document_id": document_id},
                        "expectedOutput": {"fields": {}},
                    }
                ],
                page=1,
                total_pages=1,
            ),
        ),
    }


def _record_run_via(adapter: LangfuseAdapter) -> None:
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": dataset["items"][0]["item_id"],
            "document_id": dataset["items"][0]["document_id"],
            "scores": [
                _derived_score(
                    document_id=dataset["items"][0]["document_id"],
                    name="gate",
                    value="PASS",
                )
            ],
        }
    ]
    adapter.record_run(
        dataset_name="ds",
        run_name="run-1",
        run_id="run-1",
        records=records,  # type: ignore[arg-type]
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )


def test_write_scores_transport_error_retry_actually_sleeps_before_retrying() -> None:
    """Atchim (mutation survivor): a transport-error retry must call the
    injected sleep with a positive delay before the next attempt -- not
    just eventually succeed regardless of whether sleep was invoked."""
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[TransportError("connection reset"), (200, {"id": "x"})],
    )
    sleep_calls: list[float] = []
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=sleep_calls.append,
    )

    _record_run_via(adapter)

    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


def test_write_scores_backoff_delay_is_jitter_times_ceiling_not_the_full_backoff() -> None:
    """Atchim (mutation survivor): the jitter must actually scale the
    delay -- a fixed random_func()==0.25 must produce 0.25 * ceiling, not
    the unscaled ceiling (which would kill a `jitter * ceiling` ->
    `ceiling` mutation)."""
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(503, "down"), (200, {"id": "x"})],
    )
    sleep_calls: list[float] = []
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=sleep_calls.append,
        random_func=lambda: 0.25,
        score_write_backoff_base_seconds=1.0,
        score_write_backoff_cap_seconds=8.0,
    )

    _record_run_via(adapter)

    # attempt 1: ceiling = min(8.0, 1.0 * 2**0) = 1.0 -> delay = 0.25 * 1.0
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.25)


def test_write_scores_retries_a_5xx_then_succeeds_with_the_same_score_id() -> None:
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(503, "down"), (200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
    )

    _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 2
    assert (
        score_post_calls[0][2]["id"]
        == score_post_calls[1][2]["id"]
        == score_id(run_id="run-1", document_id="doc-0", score_name="gate")
    )


def test_write_scores_retries_a_transport_error_then_succeeds() -> None:
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[TransportError("connection reset"), (200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
    )

    _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 2


def test_write_scores_persistent_5xx_raises_score_write_failed_error_after_budget() -> None:
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(503, "down"), (503, "down"), (503, "down"), (503, "down")],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        score_write_max_attempts=3,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ScoreWriteFailedError):
        _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 3


def test_write_scores_persistent_transport_error_raises_after_exactly_max_attempts() -> None:
    """Coverage audit gap: the 5xx-budget test above never exercises a
    persistent TransportError (every attempt fails at the transport
    layer, never reaching an HTTP status) -- must also raise
    ScoreWriteFailedError after exactly max_attempts POSTs."""
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[
            TransportError("connection reset"),
            TransportError("connection reset"),
            TransportError("connection reset"),
        ],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        score_write_max_attempts=3,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ScoreWriteFailedError):
        _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 3


# --- FU-01.3-B / DEBT-20: record-phase monotonic deadline. Provisional
# -- the mechanism lands here; the real deadline VALUE stays owed to
# S-01.6 timings + N8's 50-doc arithmetic (Soneca ruling). Default is
# None (disabled) -- a low guess would ship a new way to abort a run
# that would otherwise have succeeded.


def _sequenced_clock(*times: float) -> Any:
    """A fake monotonic clock: returns each value in order, then holds at
    the last one for any further call (mirrors a real clock continuing
    to run past a deadline without needing an unbounded fixture)."""
    values = list(times)

    def _clock() -> float:
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    return _clock


def test_record_deadline_defaults_to_disabled_and_never_consults_the_clock() -> None:
    """Ship with a generous default or None=disabled (DEBT-20's own
    warning) -- assert the shipped default really is disabled, not a
    picked number, by proving the clock is never even called."""

    def _clock_must_not_be_called() -> float:
        raise AssertionError("clock must not be consulted when the deadline is disabled")

    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
        clock=_clock_must_not_be_called,
    )

    _record_run_via(adapter)  # must not raise, must not touch the clock

    assert adapter._record_deadline_seconds is None  # noqa: SLF001


def test_record_deadline_exceeded_before_any_score_write_raises_without_any_post() -> None:
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
        record_deadline_seconds=5.0,
        # deadline = clock() + 5.0 = 105.0 at record_run start; the
        # per-score check below then observes 200.0, already past it.
        clock=_sequenced_clock(100.0, 200.0),
    )

    with pytest.raises(ScoreWriteFailedError, match="deadline"):
        _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert score_post_calls == []


def test_record_deadline_exceeded_between_scores_stops_further_writes() -> None:
    """Direct call (precise clock-call control): 3 scores in one
    document, deadline exceeded right before the 3rd -- the first two
    are written, the third is refused before any POST for it."""
    http_client = _QueuedScorePostClient(
        static_responses={},
        score_post_queue=[(200, {"id": "x"}), (200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=http_client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
        clock=_sequenced_clock(0.0, 0.0, 50.0, 50.0, 150.0),
    )
    scores = [
        _derived_score(document_id="doc-0", name=f"field:score-{i}", value="match")
        for i in range(3)
    ]

    with pytest.raises(ScoreWriteFailedError, match="deadline"):
        adapter._write_scores(  # noqa: SLF001
            trace_id="trace-1", document_id="doc-0", scores=scores, deadline=100.0
        )

    score_post_calls = [c for c in http_client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 2


def test_record_deadline_checked_between_retry_attempts() -> None:
    """A transient TransportError on attempt 1 backs off, but the
    deadline check at the top of attempt 2 fires first -- attempt 2's
    POST must never happen."""
    http_client = _QueuedScorePostClient(
        static_responses={},
        score_post_queue=[TransportError("connection reset")],
    )
    adapter = LangfuseAdapter(
        client=http_client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
        # call 1: per-score check (0.0 < 10.0, passes); call 2: attempt-1
        # check (0.0 < 10.0, passes) -> TransportError -> sleep -> call 3:
        # attempt-2 check (20.0 >= 10.0, deadline exceeded).
        clock=_sequenced_clock(0.0, 0.0, 20.0),
    )
    score = _derived_score(document_id="doc-0", name="gate", value="PASS")

    with pytest.raises(ScoreWriteFailedError, match="deadline"):
        adapter._write_scores(  # noqa: SLF001
            trace_id="trace-1", document_id="doc-0", scores=[score], deadline=10.0
        )

    assert len(http_client.calls) == 1


def test_record_deadline_persists_across_records_not_reset_per_record() -> None:
    """Integration-level proof through record_run: the deadline is
    computed ONCE for the whole record phase, not per-record -- a
    deadline exceeded after record 1's score must also stop record 2's,
    even though record 2 is a fresh _write_scores call."""
    client = _QueuedScorePostClient(
        static_responses={
            ("GET", "/api/public/v2/datasets/ds"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=ds"): (
                200,
                _dataset_items_page(
                    [
                        {
                            "id": "item-1",
                            "input": {"document_id": "doc-0"},
                            "expectedOutput": {"fields": {}},
                        },
                        {
                            "id": "item-2",
                            "input": {"document_id": "doc-1"},
                            "expectedOutput": {"fields": {}},
                        },
                    ],
                    page=1,
                    total_pages=1,
                ),
            ),
        },
        score_post_queue=[(200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
        record_deadline_seconds=5.0,
        # deadline = 0.0 + 5.0 = 5.0. record-1's score check sees 0.0
        # (passes), its attempt check sees 0.0 (passes) -> succeeds.
        # record-2's score check then sees 10.0 -- exceeded.
        clock=_sequenced_clock(0.0, 0.0, 0.0, 10.0),
    )
    dataset = adapter.get_dataset("ds")
    records = [
        {
            "item_id": item["item_id"],
            "document_id": item["document_id"],
            "scores": [_derived_score(document_id=item["document_id"], name="gate", value="PASS")],
        }
        for item in dataset["items"]
    ]

    with pytest.raises(ScoreWriteFailedError, match="deadline"):
        adapter.record_run(
            dataset_name="ds",
            run_name="run-1",
            run_id="run-1",
            records=records,  # type: ignore[arg-type]
            metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
        )

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 1


def test_write_scores_4xx_makes_exactly_one_attempt_never_retried() -> None:
    client = _QueuedScorePostClient(
        static_responses=_dataset_fetch_responses(),
        score_post_queue=[(422, {"message": "invalid"}), (200, {"id": "x"})],
    )
    adapter = LangfuseAdapter(
        client=client,
        tracing_client=_CompletingTracingClient(),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ScoreWriteFailedError):
        _record_run_via(adapter)

    score_post_calls = [c for c in client.calls if c[1] == "/api/public/scores"]
    assert len(score_post_calls) == 1


# --- REG-04 / F-3: get_dataset must not trust the pagination body shape --


def test_get_dataset_empty_dataset_totalPages_zero_returns_no_items_one_request() -> None:
    """Atchim (Required): totalPages=0 is the live-confirmed shape for an
    empty dataset (not malformed) -- pins that get_dataset returns
    items == [] with exactly one dataset-items request and no exception,
    so S-01.4's empty_set abort can't be misrouted to
    dataset_fetch_failed. Also kills the `raw_total_pages < 0` ->
    `< 1` mutation (which would wrongly reject totalPages=0)."""
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (
                200,
                {"data": [], "meta": {"totalPages": 0}},
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert dataset["items"] == []
    dataset_items_calls = [c for c in client.calls if c[1].startswith("/api/public/dataset-items")]
    assert len(dataset_items_calls) == 1


@pytest.mark.parametrize(
    "malformed_page",
    [
        pytest.param({"data": None, "meta": {"totalPages": 1}}, id="data-null"),
        pytest.param({"data": "not-a-list", "meta": {"totalPages": 1}}, id="data-string"),
        pytest.param(
            {"data": [], "meta": {"totalPages": "2"}}, id="totalPages-string"
        ),
        pytest.param(
            {"data": [], "meta": {"totalPages": -1}}, id="totalPages-negative"
        ),
        pytest.param({"data": []}, id="meta-missing"),
    ],
)
def test_get_dataset_malformed_pagination_shape_raises_typed_error(
    malformed_page: dict[str, Any],
) -> None:
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (200, malformed_page),
        }
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("spike-01")


def test_get_dataset_pagination_stops_at_the_page_cap_instead_of_looping_forever() -> None:
    """A huge, bogus ``totalPages`` with empty pages must not loop
    unbounded (the probe in QA-01 stopped it at 50 requests) -- a hard
    page cap raises ``DatasetFetchFailedError`` instead."""
    from idp_regression.platform.langfuse_adapter import _MAX_DATASET_PAGES

    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (200, _v2_dataset_response(None)),
            ("GET", "/api/public/dataset-items?datasetName=spike-01"): (
                200,
                {"data": [], "meta": {"totalPages": _MAX_DATASET_PAGES * 10}},
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset("spike-01")

    request_calls = [c for c in client.calls if c[1].startswith("/api/public/dataset-items")]
    # the cap check runs before each request, so pages 1.._MAX_DATASET_PAGES
    # are actually requested and page _MAX_DATASET_PAGES+1 raises before a
    # request is made -- an off-by-one on the cap comparison must fail this.
    assert len(request_calls) == _MAX_DATASET_PAGES


def test_platform_adapter_protocol_has_no_get_golden_version() -> None:
    """Gap 9 (INV-04 single-fetch): golden_version is the caller's
    hash_dataset(dataset) over the SAME fetch it iterates (TOCTOU guard)
    -- there must be no get_golden_version method on the PlatformAdapter
    Protocol tempting a second, separately-timed fetch."""
    from idp_regression.platform.types import PlatformAdapter

    assert not hasattr(PlatformAdapter, "get_golden_version")
    assert not hasattr(LangfuseAdapter, "get_golden_version")
