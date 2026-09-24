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


# --- TP-42 (gap 4): a truthiness-only check here could never fail — every
# provisioning call posts the same fixed, always-populated committed
# schema, so that guard would be dead under the current code path.
# FU-01.3-D / DEBT-35: the truthy-only assertion (`assert
# body["expectedOutputSchema"]`) was superseded by the strictly stronger
# equality check at :35 (`== load_golden_schema()`) three lines above it
# — it could never fail while :35 passes, so it was deleted rather than
# folded (nothing it proved wasn't already proven). The cases below
# replace/extend it with cases that can actually go red.


def test_provision_raises_and_makes_no_call_when_the_committed_schema_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The never-drop guard (schema_provisioning.py) must fire BEFORE any
    REST call — an empty/null committed schema must never reach the wire
    as an attempt to null out an existing platform schema."""
    import idp_regression.platform.schema_provisioning as schema_provisioning_module

    monkeypatch.setattr(schema_provisioning_module, "load_golden_schema", lambda: {})
    client = RecordingHttpClient()

    with pytest.raises(PlatformError):
        provision_golden_schema(client, dataset_name="spike-01-patterns")

    assert client.calls == []  # never sent — not even a null-schema attempt


def test_provision_never_issues_a_delete_call() -> None:
    client = RecordingHttpClient()

    provision_golden_schema(client, dataset_name="spike-01-patterns")

    assert all(method != "DELETE" for method, _, _ in client.calls)


def test_provision_expand_and_contract_style_calls_both_upsert_never_delete() -> None:
    """Two provisioning calls against the same dataset name (standing in
    for an expand, then a later contract, schema-version bump per
    ADR-0005 #4) — both must be plain upserts (POST, non-empty schema),
    and neither may ever be preceded or followed by a DELETE."""
    client = RecordingHttpClient()

    provision_golden_schema(client, dataset_name="spike-01-patterns")  # "expand" call
    provision_golden_schema(client, dataset_name="spike-01-patterns")  # "contract" call

    assert len(client.calls) == 2
    for method, path, body in client.calls:
        assert method == "POST"
        assert path == "/api/public/v2/datasets"
        # DEBT-42(1): truthy-only assertion superseded by the strictly
        # stronger equality check (identical fix as DEBT-35's `:38`
        # twin, three lines above there — this was the sibling that
        # survived because DEBT-35 was carded by line number).
        assert body["expectedOutputSchema"] == load_golden_schema()
    assert all(method != "DELETE" for method, _, _ in client.calls)


def test_provision_raises_typed_error_on_failure_without_retrying_a_drop() -> None:
    client = RecordingHttpClient(status=400, response={"message": "rejected"})

    with pytest.raises(PlatformError):
        provision_golden_schema(client, dataset_name="spike-01-patterns")

    # exactly one call was made — a failed provisioning attempt never
    # falls back to a second call with an empty/null schema.
    assert len(client.calls) == 1


def test_provision_error_log_survives_a_newline_in_dataset_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N5 sibling (/test Scenario B follow-up): schema_provisioning_failed
    logs dataset_name -- caller-controlled. Same fix (sanitize_for_log)."""
    import logging

    caplog.set_level(logging.ERROR)
    malicious_name = 'ds\ninjected fake log line status=200 dataset="ok"'
    client = RecordingHttpClient(status=400, response={"message": "rejected"})

    with pytest.raises(PlatformError):
        provision_golden_schema(client, dataset_name=malicious_name)

    for record in caplog.records:
        rendered = record.getMessage()
        assert "\n" not in rendered, f"raw newline reached a rendered log line: {rendered!r}"
        assert 'dataset="ok"' not in rendered, f"unescaped quote forged a field: {rendered!r}"
