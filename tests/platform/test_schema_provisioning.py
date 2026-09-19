"""Golden schema provisioning — upsert, never drop (ADR-0005 #4, TP-42)."""

from __future__ import annotations

from typing import Any

import pytest

from idp_regression.platform.errors import PlatformError
from idp_regression.platform.schema import load_golden_schema
from idp_regression.platform.schema_provisioning import provision_golden_schema


class RecordingHttpClient:
    def __init__(self, status: int = 200, response: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._status = status
        self._response = response if response is not None else {"name": "spike-01"}

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        return self._status, self._response


def test_provision_upserts_via_post_datasets() -> None:
    client = RecordingHttpClient()

    provision_golden_schema(client, dataset_name="spike-01-patterns")

    assert len(client.calls) == 1
    method, path, body = client.calls[0]
    assert method == "POST"
    assert path == "/api/public/v2/datasets"
    assert body["name"] == "spike-01-patterns"
    assert body["expectedOutputSchema"] == load_golden_schema()


def test_provision_never_sends_an_empty_or_null_schema() -> None:
    client = RecordingHttpClient()

    provision_golden_schema(client, dataset_name="spike-01-patterns")

    _, _, body = client.calls[0]
    assert body["expectedOutputSchema"]  # truthy: non-empty, non-null


def test_provision_raises_typed_error_on_failure_without_retrying_a_drop() -> None:
    client = RecordingHttpClient(status=400, response={"message": "rejected"})

    with pytest.raises(PlatformError):
        provision_golden_schema(client, dataset_name="spike-01-patterns")

    # exactly one call was made — a failed provisioning attempt never
    # falls back to a second call with an empty/null schema.
    assert len(client.calls) == 1
