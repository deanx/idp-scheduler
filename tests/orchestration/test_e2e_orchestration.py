"""T-01.4.9: end-to-end `run_eval` orchestration test, against a mock IDP
adapter and a mock platform -- no live services.

Covers the happy path (extract -> classify -> gate -> one post-loop
`record_run`, INV-04 run metadata), the INV-06 abort path (a mid-run
failure stops the loop and writes the `aborted` marker exactly once),
INV-08 (every gate is computed before any platform write), and NFR N3
(the abort path returns promptly -- no wall-clock sleep is on it).

The fakes are typed against the real `PlatformAdapter` (`platform/types.py`)
and `IDPAdapter` (`adapter/types.py`) Protocols via explicit `:
PlatformAdapter` / `: IDPAdapter` annotations -- a signature drift on
either side is a mypy failure here, not a silently passing test (per
this task's "keep the fakes honest" instruction; S-2 gate finding
2026-09-21: `_E2EIDPAdapter` previously had no such binding at all).
"""

from __future__ import annotations

import logging
import os
import time

import pytest

from idp_regression.adapter.errors import IDPExecutionFailedError
from idp_regression.adapter.types import IDPAdapter, NormalizedOutput
from idp_regression.orchestration import facade
from idp_regression.orchestration.facade import run_eval
from idp_regression.platform.schema import load_golden_schema
from idp_regression.platform.types import (
    Dataset,
    DocumentRecord,
    PlatformAdapter,
    RunMetadata,
    RunStatus,
)

DOCUMENT_DIR = "/documents"
ACTION_ID = "12345678-1234-1234-1234-123456789012"
VERSION = "1.0"
RUN_NAME = "nightly"
DATASET_NAME = "idp-regression-golden"
ORG_ID = "org-123"


def _set_all_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "distinctive-pub-9f3a")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "distinctive-secret-NOT-A-REAL-KEY-2c71")
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.setenv("IDP_CLIENT_ID", "distinctive-client-id-7b1e")
    monkeypatch.setenv("IDP_CLIENT_SECRET", "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c")
    monkeypatch.setenv("IDP_REGION", "us-east")
    monkeypatch.setenv("IDP_ORG_ID", "org-123")
    monkeypatch.setenv("GOLDEN_DATASET_NAME", "idp-regression-golden")
    monkeypatch.setenv("IDP_DOCUMENT_DIR", DOCUMENT_DIR)


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    _set_all_credential_env(monkeypatch)


def _dataset(document_ids: list[str]) -> Dataset:
    return {
        "items": [
            {
                "item_id": f"item-{doc_id}",
                "document_id": doc_id,
                "golden": {
                    "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}}
                },
            }
            for doc_id in document_ids
        ],
        "expected_output_schema": load_golden_schema(),
    }


def _passing_actual() -> NormalizedOutput:
    """Matches `_dataset`'s single golden field exactly -- classifies PASS."""
    return {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "1250.00", "confidence": 0.99}},
    }


class _E2EIDPAdapter:
    """`.extract()` double -- the real adapter's call signature
    (`document_path, action_id, version`). Raises `error_for_document_id`
    when it reaches the matching document; every call before or after is
    recorded in `calls` and (unless erroring) returns a passing actual,
    so a test can assert exactly which documents were reached.

    S-2 gate finding: unlike `_E2EPlatform` below (bound to
    `PlatformAdapter` at each construction site), this fake previously
    had NO Protocol binding anywhere, so a real `IDPAdapter.extract`
    signature change would drift silently past this suite. Bound to
    `IDPAdapter` (`adapter/types.py`) at each construction site below.
    """

    def __init__(
        self,
        *,
        events: list[str],
        error_for_document_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self._error_for_document_id = error_for_document_id
        self._error = error
        self._events = events
        self.calls: list[str] = []

    def extract(self, document_path: str, action_id: str, version: str) -> NormalizedOutput:
        document_id = os.path.relpath(document_path, DOCUMENT_DIR)
        self.calls.append(document_id)
        self._events.append(f"extract:{document_id}")
        if document_id == self._error_for_document_id and self._error is not None:
            raise self._error
        return _passing_actual()


class _E2EPlatform:
    """A `PlatformAdapter`-conforming double. Every method signature
    mirrors `platform/types.py::PlatformAdapter` exactly (including the
    keyword-only markers) -- the `: PlatformAdapter` annotation at each
    construction site below is what makes a drift here a mypy failure."""

    def __init__(self, dataset: Dataset, *, events: list[str]) -> None:
        self._dataset = dataset
        self._events = events
        self.record_run_calls: list[dict[str, object]] = []
        self.mark_run_status_calls: list[dict[str, str]] = []

    def get_dataset(self, name: str) -> Dataset:
        return self._dataset

    def record_run(
        self,
        *,
        dataset_name: str,
        run_name: str,
        run_id: str,
        records: list[DocumentRecord],
        metadata: RunMetadata,
    ) -> None:
        self._events.append("record_run")
        self.record_run_calls.append(
            {
                "dataset_name": dataset_name,
                "run_name": run_name,
                "run_id": run_id,
                "records": records,
                "metadata": metadata,
            }
        )

    def mark_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
        golden_dataset_name: str,
    ) -> None:
        self._events.append(f"mark_run_status:{status}")
        self.mark_run_status_calls.append(
            {
                "run_id": run_id,
                "status": status,
                "action_id": action_id,
                "action_version": action_version,
                "golden_version": golden_version,
                "golden_dataset_name": golden_dataset_name,
            }
        )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document_ids: list[str],
    idp_adapter: _E2EIDPAdapter,
) -> _E2EPlatform:
    platform: PlatformAdapter = _E2EPlatform(_dataset(document_ids), events=idp_adapter._events)
    monkeypatch.setattr(facade, "make_platform", lambda: platform)
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: idp_adapter)
    assert isinstance(platform, _E2EPlatform)  # narrows back for call-log assertions
    return platform


# --- Happy path: 3 documents, one post-loop record_run -----------------


def test_happy_path_extracts_classifies_gates_and_records_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    _base_env(monkeypatch, tmp_path)
    document_ids = ["doc-1", "doc-2", "doc-3"]
    events: list[str] = []
    idp_adapter = _E2EIDPAdapter(events=events)
    _idp_adapter_conforms: IDPAdapter = idp_adapter  # S-2: Protocol binding check
    platform = _install(monkeypatch, document_ids=document_ids, idp_adapter=idp_adapter)

    exit_code = run_eval(ACTION_ID, VERSION, RUN_NAME, DATASET_NAME, ORG_ID)

    assert exit_code == 0
    assert idp_adapter.calls == document_ids

    # Scores written exactly once (a single record_run call).
    assert len(platform.record_run_calls) == 1
    call = platform.record_run_calls[0]

    # One DocumentRecord per item.
    records = call["records"]
    assert isinstance(records, list)
    assert [r["document_id"] for r in records] == document_ids
    assert all(set(r.keys()) == {"item_id", "document_id", "scores"} for r in records)

    # INV-04 (widened to four fields, A6/DEBT-48): action_id/
    # action_version/golden_version/golden_dataset_name in run metadata.
    metadata = call["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["action_id"] == ACTION_ID
    assert metadata["action_version"] == VERSION
    assert isinstance(metadata["golden_version"], str) and metadata["golden_version"]
    assert metadata["golden_dataset_name"] == DATASET_NAME

    # The run completes -- best-effort "complete" marker, not "aborted".
    assert [c["status"] for c in platform.mark_run_status_calls] == ["complete"]


# --- INV-08: no platform write before every gate is computed -----------


def test_no_platform_write_happens_before_every_gate_is_computed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    _base_env(monkeypatch, tmp_path)
    document_ids = ["doc-1", "doc-2", "doc-3"]
    events: list[str] = []
    idp_adapter = _E2EIDPAdapter(events=events)
    _idp_adapter_conforms: IDPAdapter = idp_adapter  # S-2: Protocol binding check
    _install(monkeypatch, document_ids=document_ids, idp_adapter=idp_adapter)

    exit_code = run_eval(ACTION_ID, VERSION, RUN_NAME, DATASET_NAME, ORG_ID)

    assert exit_code == 0
    # Every extract:<doc> event precedes the single record_run event --
    # no per-document platform write is interleaved into the loop.
    record_run_index = events.index("record_run")
    extract_indices = [i for i, e in enumerate(events) if e.startswith("extract:")]
    assert extract_indices == sorted(extract_indices)
    assert all(i < record_run_index for i in extract_indices)
    assert len(extract_indices) == len(document_ids)


# --- INV-06: a mid-run failure stops the run ----------------------------


def test_abort_at_document_2_of_3_stops_the_run_and_marks_aborted_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _base_env(monkeypatch, tmp_path)
    document_ids = ["doc-1", "doc-2", "doc-3"]
    events: list[str] = []
    idp_adapter = _E2EIDPAdapter(
        events=events,
        error_for_document_id="doc-2",
        error=IDPExecutionFailedError("execution failed", status="FAILED"),
    )
    _idp_adapter_conforms: IDPAdapter = idp_adapter  # S-2: Protocol binding check
    platform = _install(monkeypatch, document_ids=document_ids, idp_adapter=idp_adapter)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(ACTION_ID, VERSION, RUN_NAME, DATASET_NAME, ORG_ID)

    assert exit_code != 0
    # doc-1 and doc-2 were reached; doc-3 (after the failure) was not.
    assert idp_adapter.calls == ["doc-1", "doc-2"]

    # record_run is never reached on an in-loop abort.
    assert platform.record_run_calls == []

    # The aborted marker is written exactly once, never the complete one.
    assert [c["status"] for c in platform.mark_run_status_calls] == ["aborted"]
    assert platform.mark_run_status_calls[0]["action_id"] == ACTION_ID
    assert platform.mark_run_status_calls[0]["action_version"] == VERSION

    assert "hard_failure" in caplog.text


# --- NFR N3: the abort path returns promptly, no wall-clock sleep -------


def test_abort_path_returns_promptly(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Uses a real monotonic clock (`time.perf_counter`) around the call
    -- not a wall-clock `sleep` anywhere in the path under test -- to pin
    that the abort path never blocks. All collaborators here are pure
    in-memory fakes with no I/O, so a generous bound is enough to catch
    a regression that introduces an accidental blocking wait (e.g. a
    retry loop or a real `time.sleep`) on the abort path."""
    _base_env(monkeypatch, tmp_path)
    document_ids = ["doc-1", "doc-2", "doc-3"]
    events: list[str] = []
    idp_adapter = _E2EIDPAdapter(
        events=events,
        error_for_document_id="doc-1",
        error=IDPExecutionFailedError("execution failed", status="FAILED"),
    )
    _idp_adapter_conforms: IDPAdapter = idp_adapter  # S-2: Protocol binding check
    _install(monkeypatch, document_ids=document_ids, idp_adapter=idp_adapter)

    start = time.perf_counter()
    exit_code = run_eval(ACTION_ID, VERSION, RUN_NAME, DATASET_NAME, ORG_ID)
    elapsed = time.perf_counter() - start

    assert exit_code != 0
    assert elapsed < 1.0


def test_all_gates_pass_success_path_returns_promptly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Coverage audit gap 7 (2026-09-21): NFR N3 was only bounded on the
    abort path (`test_abort_path_returns_promptly` above) -- the success
    path (extract -> classify -> gate -> the single post-loop
    `record_run` -> the `complete` marker) had no latency bound at all.
    Same technique as the abort-path test: a real monotonic clock
    (`time.perf_counter`) around the whole `run_eval` call, never a
    wall-clock `time.sleep` anywhere in the path under test -- every
    collaborator here is a pure in-memory fake with no I/O, so the same
    generous bound catches an accidental blocking wait on the happy
    path too."""
    _base_env(monkeypatch, tmp_path)
    document_ids = ["doc-1", "doc-2", "doc-3"]
    events: list[str] = []
    idp_adapter = _E2EIDPAdapter(events=events)
    _idp_adapter_conforms: IDPAdapter = idp_adapter  # S-2: Protocol binding check
    _install(monkeypatch, document_ids=document_ids, idp_adapter=idp_adapter)

    start = time.perf_counter()
    exit_code = run_eval(ACTION_ID, VERSION, RUN_NAME, DATASET_NAME, ORG_ID)
    elapsed = time.perf_counter() - start

    assert exit_code == 0
    assert elapsed < 1.0
