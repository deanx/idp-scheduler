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
from idp_regression.platform.scoring import field_score_name, prompt_score_name, score_id
from idp_regression.platform.types import DocumentRecord, RunMetadata, ScoreInput

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
        self.run_experiment_calls = 0

    def run_experiment(self, **kwargs: Any) -> _FakeResult:
        self.run_experiment_calls += 1
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
    adapter._item_cache = {item_id: f"ds-{item_id}" for item_id in item_ids}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    return adapter, http_client


def _record(item_id: str, document_id: str) -> DocumentRecord:
    return {
        "item_id": item_id,
        "document_id": document_id,
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
    adapter._item_cache = {"item-1": "ds-1"}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    # A record missing the required "scores" key — the task's dict
    # comprehension over `record["scores"]` will KeyError, triggering
    # task_failed.
    malformed: Any = {"item_id": "item-1", "document_id": "doc-0"}

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
    adapter._item_cache = {"item-1": "ds-1"}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    malformed: Any = {"item_id": "item-1", "document_id": "doc-0"}  # no "scores"

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
    different failure mode (a poisoned "scores" access raising a
    RuntimeError deeper in a hypothetical future task body) by making
    the records lookup itself explode with a different exception type."""
    tracing_client = _RunExperimentTracingClient()
    adapter = LangfuseAdapter(client=_FakeHttpClient(), tracing_client=tracing_client)
    adapter._item_cache = {"item-1": "ds-1"}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001

    class _ExplodesOnDictAccess(dict):  # type: ignore[type-arg]
        def __getitem__(self, key: str) -> Any:
            if key == "scores":
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


# --- FU-01.3-A / ADR-0005 #9 amendment A3: run_id is verified, not
# decorative. Every scores[*].id MUST be uuid5-derived from the run_id
# passed in this same call, checked before any SDK call (N26 becomes a
# boundary-checked precondition instead of orchestrator discipline).


def _adapter_with_tracing(
    *item_ids: str,
) -> tuple[LangfuseAdapter, _FakeHttpClient, _RunExperimentTracingClient]:
    http_client = _FakeHttpClient()
    tracing_client = _RunExperimentTracingClient()
    adapter = LangfuseAdapter(client=http_client, tracing_client=tracing_client)
    adapter._item_cache = {item_id: f"ds-{item_id}" for item_id in item_ids}  # noqa: SLF001
    adapter._cached_dataset_name = "ds"  # noqa: SLF001
    return adapter, http_client, tracing_client


def _score(*, run_id: str, document_id: str, name: str, value: str) -> ScoreInput:
    return {
        "id": score_id(run_id=run_id, document_id=document_id, score_name=name),
        "name": name,
        "value": value,
    }


def test_score_id_from_a_different_run_id_is_refused_before_any_sdk_call() -> None:
    """The defect N26 rests on: a DocumentRecord whose score ids were
    derived from ANOTHER invocation's run_id would previously record as
    correct, overwriting that other run's scores (the ids are the upsert
    key). The check must fire before the experiment is created, so a
    rejected run leaves nothing behind on the platform."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    records: list[DocumentRecord] = [
        {
            "item_id": "item-1",
            "document_id": "doc-0",
            # derived from a DIFFERENT run
            "scores": [_score(run_id="run-OTHER", document_id="doc-0", name="gate", value="PASS")],
        }
    ]

    with pytest.raises(ExperimentRecordFailedError, match="run_id"):
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    assert tracing_client.run_experiment_calls == 0
    assert tracing_client.task_outputs == []
    assert http_client.calls == []


def test_a_single_mismatched_score_among_correct_ones_is_still_refused() -> None:
    """Whole-run refusal, not a per-score skip: one wrong id among many
    correct ones must abort the entire record (a partial record is worse
    than none — same stance as the task_failed path)."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1", "item-2")
    records: list[DocumentRecord] = [
        {
            "item_id": "item-1",
            "document_id": "doc-0",
            "scores": [_score(run_id="run-1", document_id="doc-0", name="gate", value="PASS")],
        },
        {
            "item_id": "item-2",
            "document_id": "doc-1",
            "scores": [
                _score(run_id="run-1", document_id="doc-1", name="field:total", value="match"),
                # right run_id, but derived for the WRONG document
                _score(run_id="run-1", document_id="doc-0", name="gate", value="PASS"),
            ],
        },
    ]

    with pytest.raises(ExperimentRecordFailedError, match="run_id"):
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    assert tracing_client.run_experiment_calls == 0
    assert http_client.calls == []


@pytest.mark.parametrize("run_id", ["run-1", "run-2"])
def test_correctly_derived_ids_for_all_three_score_families_pass(run_id: str) -> None:
    """Proves the precondition isn't always-raising, over the full score
    vocabulary build_score_inputs emits (CT-03): field:<name>,
    prompt:<16-hex> and the single per-document gate.

    Parametrized over two run_ids (Atchim FU-01.3-A review, suggestion 1):
    with one fixed run_id, a mutant that ignores the run_id parameter and
    hard-codes "run-1" — precisely the defect A3 exists to close — survives
    every test in this file, and dies only incidentally in an unrelated
    INV-01 test that happens to use a different run id. Varying it pins
    "the parameter is actually consumed" here, where it belongs.
    """
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    prompt_name = prompt_score_name("who signed the invoice?")
    records: list[DocumentRecord] = [
        {
            "item_id": "item-1",
            "document_id": "doc-0",
            "scores": [
                _score(
                    run_id=run_id,
                    document_id="doc-0",
                    name=field_score_name("total"),
                    value="match",
                ),
                _score(run_id=run_id, document_id="doc-0", name=prompt_name, value="match"),
                _score(run_id=run_id, document_id="doc-0", name="gate", value="PASS"),
            ],
        }
    ]

    adapter.record_run(
        dataset_name="ds", run_name="r", run_id=run_id, records=records, metadata=_METADATA
    )

    assert tracing_client.run_experiment_calls == 1
    score_calls = [c for c in http_client.calls if c[1] == "/api/public/scores"]
    assert len(score_calls) == 3


def test_the_run_id_mismatch_error_names_only_document_id_and_score_name() -> None:
    """INV-02 (same class as QA-01 S-01.3 F-5 / REG-05): the raise must
    carry document_id + score_name ONLY — both value-free by construction
    (``field:<name>`` is charset-restricted, ``prompt:<16-hex>`` is a
    digest). No golden value, and no dump of the offending id pair."""
    sentinel_golden_value = "SENTINEL-GOLDEN-VALUE-do-not-leak-3e9f2b"
    sentinel_prompt_key = "SENTINEL-PROMPT-KEY-do-not-leak-7f3c1a"
    hashed_name = prompt_score_name(sentinel_prompt_key)

    adapter, _, _ = _adapter_with_tracing("item-1")
    bad_score = _score(
        run_id="run-OTHER", document_id="doc-0", name=hashed_name, value=sentinel_golden_value
    )
    records: list[DocumentRecord] = [
        {"item_id": "item-1", "document_id": "doc-0", "scores": [bad_score]}
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert sentinel_golden_value not in message
    assert sentinel_prompt_key not in message
    # neither the offending id nor the id it should have been
    assert bad_score["id"] not in message
    assert score_id(run_id="run-1", document_id="doc-0", score_name=hashed_name) not in message
    # but it must still be actionable
    assert "doc-0" in message
    assert hashed_name in message
