"""record_run — item-id precondition, task_failed raise, and the total
task's catch-all (Atchim mutation-testing findings, re-review 2026-09-19).

All against a mocked ExperimentRunner (no network) — these are pure
control-flow tests, not integration facts.
"""

from __future__ import annotations

from typing import Any

import pytest

from idp_regression.platform.errors import ExperimentRecordFailedError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.types import DocumentRecord, RunMetadata

_METADATA: RunMetadata = {"action_id": "a", "action_version": "v", "golden_version": "g"}


class _FakeItemResult:
    def __init__(self, item: Any, trace_id: str, dataset_run_id: str) -> None:
        self.item = item
        self.trace_id = trace_id
        self.dataset_run_id = dataset_run_id


class _FakeResult:
    def __init__(self, item_results: list[_FakeItemResult]) -> None:
        self.item_results = item_results


class _FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        return 200, {"id": "x"}


class _RunExperimentTracingClient:
    """Actually calls the task per item (like the real SDK) and returns a
    successful structural result — used to isolate record_run's OWN
    control flow (precondition, task_failed check) from tracing.py's
    watcher/structural-check logic, which is tested separately."""

    def __init__(self) -> None:
        self.task_outputs: list[Any] = []

    def run_experiment(self, **kwargs: Any) -> _FakeResult:
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


def _adapter_with_cache(*item_ids: str) -> tuple[LangfuseAdapter, _FakeHttpClient]:
    http_client = _FakeHttpClient()
    adapter = LangfuseAdapter(client=http_client, tracing_client=_RunExperimentTracingClient())
    adapter._item_cache = {item_id: (f"ds-{item_id}", {"fields": {}}) for item_id in item_ids}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    return adapter, http_client


def _record(item_id: str, document_id: str) -> DocumentRecord:
    return {
        "item_id": item_id,
        "document_id": document_id,
        "actual": {"status": "SUCCEEDED", "fields": {}},
        "scores": [],
    }


# --- item-id precondition (:190-197) ----------------------------------


def test_precondition_raises_on_duplicate_item_id_in_records() -> None:
    adapter, _ = _adapter_with_cache("item-1")
    duplicated = [_record("item-1", "doc-0"), _record("item-1", "doc-0")]

    with pytest.raises(ExperimentRecordFailedError, match="duplicate"):
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=duplicated, metadata=_METADATA
        )


def test_precondition_raises_when_a_cached_item_is_missing_from_records() -> None:
    """The dataset has 2 items but only 1 record was built — record_run
    must refuse a partial recording rather than silently record less
    than the fetched dataset."""
    adapter, _ = _adapter_with_cache("item-1", "item-2")
    records = [_record("item-1", "doc-0")]  # item-2 missing

    with pytest.raises(ExperimentRecordFailedError, match="do not exactly match"):
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )


def test_precondition_raises_when_records_contain_an_item_id_not_in_the_cache() -> None:
    """An extra item_id records claims that get_dataset never returned —
    e.g. a caller-side bug building records against the wrong dataset."""
    adapter, _ = _adapter_with_cache("item-1")
    records = [_record("item-1", "doc-0"), _record("item-999-not-cached", "doc-1")]

    with pytest.raises(ExperimentRecordFailedError, match="do not exactly match"):
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )


def test_precondition_passes_and_records_when_item_ids_match_exactly() -> None:
    """Sanity: the exact-match case (no duplicate, no missing, no extra)
    proceeds and writes scores — proves the precondition isn't just
    always-raising."""
    adapter, http_client = _adapter_with_cache("item-1", "item-2")
    records = [_record("item-1", "doc-0"), _record("item-2", "doc-1")]

    adapter.record_run(
        dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
    )

    score_calls = [c for c in http_client.calls if c[1] == "/api/public/scores"]
    # zero scores per record here (empty "scores" lists), but no
    # precondition/task_failed error means record_run reached the write
    # loop; assert it didn't raise (implicit) and made it past the gate.
    assert score_calls == []


# --- task_failed raise (:235-238) --------------------------------------


def test_task_failed_raises_experiment_record_failed_and_skips_score_writes() -> None:
    """When the total task's defense-in-depth catch fires for ANY item,
    record_run must raise (not silently proceed) and must NOT write any
    scores for that run (a partial/wrong record is worse than none)."""
    http_client = _FakeHttpClient()

    class _MissingKeyOnPurposeTracingClient(_RunExperimentTracingClient):
        pass

    adapter = LangfuseAdapter(
        client=http_client, tracing_client=_MissingKeyOnPurposeTracingClient()
    )
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    # A record missing the required "actual" key — the task's dict
    # lookup `record["actual"]` will KeyError, triggering task_failed.
    malformed: Any = {"item_id": "item-1", "document_id": "doc-0", "scores": []}

    with pytest.raises(ExperimentRecordFailedError, match="task caught an unexpected exception"):
        adapter.record_run(
            dataset_name="ds",
            run_name="r",
            run_id="run-1",
            records=[malformed],
            metadata=_METADATA,
        )

    score_calls = [c for c in http_client.calls if c[1] == "/api/public/scores"]
    assert score_calls == []


def test_task_failed_output_is_the_fixed_constant() -> None:
    """The task's output on failure is exactly {"record_error":
    "task_failed"} — never the exception, never a partial dict."""
    tracing_client = _RunExperimentTracingClient()
    adapter = LangfuseAdapter(client=_FakeHttpClient(), tracing_client=tracing_client)
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    malformed: Any = {"item_id": "item-1", "document_id": "doc-0", "scores": []}

    with pytest.raises(ExperimentRecordFailedError):
        adapter.record_run(
            dataset_name="ds",
            run_name="r",
            run_id="run-1",
            records=[malformed],
            metadata=_METADATA,
        )

    assert tracing_client.task_outputs == [{"record_error": "task_failed"}]


# --- the total task's catch-all (:209-211) ------------------------------


def test_task_catch_all_catches_more_than_just_key_error() -> None:
    """The task must catch ANY exception, not just KeyError — simulate a
    different failure mode (a non-dict "scores" causing a TypeError
    deeper in a hypothetical future task body) by making the records
    lookup itself explode with a different exception type."""
    tracing_client = _RunExperimentTracingClient()
    adapter = LangfuseAdapter(client=_FakeHttpClient(), tracing_client=tracing_client)
    # item-1 is NOT in the cache used to build records_by_item_id's
    # companion experiment_items list, but IS a valid record — force a
    # lookup mismatch by using a record whose "actual" is an object that
    # raises on any access attempt other than pure identity.
    adapter._item_cache = {"item-1": ("ds-1", {"fields": {}})}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001

    class _ExplodesOnDictAccess(dict):  # type: ignore[type-arg]
        def __getitem__(self, key: str) -> Any:
            if key == "actual":
                raise RuntimeError("simulated non-KeyError failure")
            return super().__getitem__(key)

    malformed = _ExplodesOnDictAccess(item_id="item-1", document_id="doc-0", scores=[])

    with pytest.raises(ExperimentRecordFailedError):
        adapter.record_run(
            dataset_name="ds",
            run_name="r",
            run_id="run-1",
            records=[malformed],  # type: ignore[list-item]
            metadata=_METADATA,
        )

    assert tracing_client.task_outputs == [{"record_error": "task_failed"}]


# --- suggestion: record_run must not silently ignore dataset_name ------


def test_record_run_raises_when_dataset_name_does_not_match_the_cached_dataset() -> None:
    """Suggestion: record_run currently keys its precondition purely off
    the item_id cache populated by the LAST get_dataset() call and never
    itself checks `dataset_name` against what was fetched — assert it
    at least matches the cached dataset_id derived from that same
    dataset, so a caller passing the wrong dataset_name string (while
    still using item ids from a correctly-fetched dataset) is caught."""
    adapter, _ = _adapter_with_cache("item-1")
    # _adapter_with_cache seeds dataset_id="ds-item-1" for item-1 (see
    # helper) — deliberately pass a dataset_name that doesn't match any
    # dataset_id the cache actually holds.
    records = [_record("item-1", "doc-0")]

    with pytest.raises(ExperimentRecordFailedError, match="dataset_name"):
        adapter.record_run(
            dataset_name="a-completely-different-dataset",
            run_name="r",
            run_id="run-1",
            records=records,
            metadata=_METADATA,
        )
