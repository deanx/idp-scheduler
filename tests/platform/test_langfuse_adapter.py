"""LangfuseAdapter — get_dataset / write_scores / flush / mark_run_status.

All unit tests run against a mocked HttpClient (no network, no Langfuse
credentials required) — the Protocol seam defined in
``idp_regression.platform.transport.HttpClient``.
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
from idp_regression.platform.types import ScoreInput


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
        # allow a path-prefix match for parameterized paths (dataset name)
        for (m, p), resp in self._responses.items():
            if m == method and path.startswith(p):
                return resp
        raise AssertionError(f"unexpected call: {method} {path}")


def test_get_dataset_returns_items_and_expected_output_schema() -> None:
    schema = {"type": "object", "required": ["fields"], "properties": {}}
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (
                200,
                {
                    "name": "spike-01",
                    "expectedOutputSchema": schema,
                    "items": [
                        {
                            "id": "item-1",
                            "input": {"document_id": "invoice-007.pdf"},
                            "expectedOutput": {
                                "fields": {"total": {"value": "1250.00", "type": "number"}}
                            },
                        }
                    ],
                },
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert dataset["expected_output_schema"] == schema
    assert dataset["items"] == [
        {
            "document_id": "invoice-007.pdf",
            "golden": {"fields": {"total": {"value": "1250.00", "type": "number"}}},
        }
    ]


def test_get_dataset_returns_none_schema_when_absent() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/api/public/v2/datasets/spike-01"): (
                200,
                {"name": "spike-01", "items": []},
            ),
        }
    )
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset("spike-01")

    assert dataset["expected_output_schema"] is None
    assert dataset["items"] == []


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


def test_write_scores_posts_deterministic_ids() -> None:
    client = FakeHttpClient({("POST", "/api/public/scores"): (200, {"id": "x"})})
    adapter = LangfuseAdapter(client=client)
    scores: list[ScoreInput] = [
        {"id": "score-id-1", "name": "field:total", "value": "match", "comment": None},
        {"id": "score-id-2", "name": "gate", "value": "PASS"},
    ]

    adapter.write_scores(run_id="run-1", document_id="invoice-007.pdf", scores=scores)

    posted = [call[2] for call in client.calls if call[0] == "POST"]
    assert {"id": "score-id-1", "name": "field:total", "value": "match", "comment": None} in [
        {**p} for p in posted
    ]
    assert len(posted) == 2


def test_write_scores_failure_raises_typed_error() -> None:
    client = FakeHttpClient({("POST", "/api/public/scores"): (500, "boom")})
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(ScoreWriteFailedError):
        adapter.write_scores(
            run_id="run-1",
            document_id="invoice-007.pdf",
            scores=[{"id": "s1", "name": "gate", "value": "FAIL"}],
        )


def test_write_scores_payload_carries_no_file_content_beyond_document_id() -> None:
    """INV-01 smoke: the score payload never carries a file path/bytes blob."""
    client = FakeHttpClient({("POST", "/api/public/scores"): (200, {"id": "x"})})
    adapter = LangfuseAdapter(client=client)

    adapter.write_scores(
        run_id="run-1",
        document_id="/etc/secret/invoice-007.pdf",
        scores=[{"id": "s1", "name": "field:total", "value": "match"}],
    )

    for _, _, body in client.calls:
        assert "file" not in body if isinstance(body, dict) else True


def test_flush_is_a_no_op_success_until_otlp_lands() -> None:
    # T-01.3.10a (OTLP trace export) is a separate, not-yet-landed slice —
    # flush() here has no trace exporter to flush, so it succeeds trivially.
    adapter = LangfuseAdapter(client=FakeHttpClient({}))
    adapter.flush()  # must not raise


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
