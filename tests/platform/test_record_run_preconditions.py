"""record_run — item-id precondition, task_failed raise, and the total
task's catch-all (Atchim mutation-testing findings, re-review 2026-09-19).

All against a mocked ExperimentRunner (no network) — these are pure
control-flow tests, not integration facts.
"""

from __future__ import annotations

from typing import Any, cast

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
    always-raising.

    FU-01.3-D / DEBT-34 fix (2026-09-21): the fixture used to give every
    record an EMPTY "scores" list, so `assert score_calls == []` could
    never fail — it observed that nothing existed, not that nothing
    leaked, while the docstring claimed the opposite ("writes scores").
    Fix picks the FIRST of DEBT-34's two options (give the fixture real
    scores, a coverage gain) rather than re-wording the docstring."""
    adapter, http_client = _adapter_with_cache("item-1", "item-2")
    records: list[DocumentRecord] = [
        {
            "item_id": "item-1",
            "document_id": "doc-0",
            "scores": [
                {
                    "id": score_id(run_id="run-1", document_id="doc-0", score_name="gate"),
                    "name": "gate",
                    "value": "PASS",
                }
            ],
        },
        {
            "item_id": "item-2",
            "document_id": "doc-1",
            "scores": [
                {
                    "id": score_id(run_id="run-1", document_id="doc-1", score_name="gate"),
                    "name": "gate",
                    "value": "PASS",
                }
            ],
        },
    ]

    adapter.record_run(
        dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
    )

    score_calls = [c for c in http_client.calls if c[1] == "/api/public/scores"]
    assert len(score_calls) == 2


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
    # A record with a well-shaped ``scores`` list (passes the FU-01.3-B
    # shape precondition and the run_id derivation check) but the score
    # dict is missing "value" — the task's dict comprehension over
    # `score["value"]` will KeyError, triggering task_failed. (Before
    # FU-01.3-B this used a record missing "scores" entirely, but that
    # now raises earlier, from the shape precondition itself — REG-09.)
    # DEBT-39: this fixture passes only because `_require_record_shape`
    # does not check "value" today — the moment it does, this record
    # would be rejected at the PRECONDITION and never reach the task,
    # silently hollowing out what this test means to cover. A future
    # "value" check must re-fixture this test deliberately, not by
    # reflex-adding "value" here.
    malformed: Any = {
        "item_id": "item-1",
        "document_id": "doc-0",
        "scores": [
            {
                "id": score_id(run_id="run-1", document_id="doc-0", score_name="gate"),
                "name": "gate",
            }
        ],
    }

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
    # See the sibling test above (REG-09): a well-shaped scores list with
    # a score missing "value" still trips the task's own catch-all.
    # DEBT-39: same dependency as the sibling test above — this fixture
    # passes only because `_require_record_shape` does not check "value"
    # today; a future "value" check must re-fixture this deliberately.
    malformed: Any = {
        "item_id": "item-1",
        "document_id": "doc-0",
        "scores": [
            {
                "id": score_id(run_id="run-1", document_id="doc-0", score_name="gate"),
                "name": "gate",
            }
        ],
    }

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

    # Atchim S-2: this test survives the FU-01.3-B shape guard ONLY
    # because that guard reads `record.get("scores")`, and CPython's
    # `dict.get` does NOT route through an overridden `__getitem__` --
    # so the RuntimeError trap below is never sprung by
    # `_require_record_shape`, and this record instead reaches the task
    # (whose body DOES subscript `record["scores"]` directly, springing
    # it there, which is what this test means to exercise). If the guard
    # is ever rewritten to use `record["scores"]` instead of `.get(...)`,
    # this test's meaning silently changes -- it would then be pinning
    # the PRECONDITION's catch, not the task's, and would need a new
    # docstring (or a new fixture that doesn't rely on this
    # implementation detail).
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


# --- FU-01.3-B / QA-01 F-1 / REG-09: malformed caller input must raise
# ExperimentRecordFailedError (the typed contract, types.py:99-101), never
# an untyped KeyError/TypeError -- and must do so before any SDK call
# (run_experiment_calls stays 0 on every one of these paths). This also
# absorbs the `.get("scores")` tolerance debt (FU-01.3-A handoff, Open
# item 1): the shape check below replaces that tolerance entirely.

_SENTINEL_SCORE_ID = "SENTINEL-SCORE-ID-do-not-leak-9b1e4f"
_SENTINEL_SCORE_NAME = "SENTINEL-SCORE-NAME-do-not-leak-2d7c8a"
_SENTINEL_SCORE_VALUE = "SENTINEL-SCORE-VALUE-do-not-leak-5a3f19"


def _assert_zero_platform_writes(
    http_client: _FakeHttpClient, tracing_client: _RunExperimentTracingClient
) -> None:
    assert tracing_client.run_experiment_calls == 0
    assert http_client.calls == []


def _assert_no_score_sentinel_leaked(message: str) -> None:
    assert _SENTINEL_SCORE_ID not in message
    assert _SENTINEL_SCORE_NAME not in message
    assert _SENTINEL_SCORE_VALUE not in message


def test_score_missing_id_raises_typed_error_before_any_sdk_call() -> None:
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_score: Any = {"name": _SENTINEL_SCORE_NAME, "value": _SENTINEL_SCORE_VALUE}
    records: list[DocumentRecord] = [
        {"item_id": "item-1", "document_id": "doc-0", "scores": [bad_score]}
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "doc-0" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_score_missing_name_raises_typed_error_before_any_sdk_call() -> None:
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_score: Any = {"id": _SENTINEL_SCORE_ID, "value": _SENTINEL_SCORE_VALUE}
    records: list[DocumentRecord] = [
        {"item_id": "item-1", "document_id": "doc-0", "scores": [bad_score]}
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "doc-0" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_scores_none_raises_typed_error_before_any_sdk_call() -> None:
    """Delta-coverage audit note: no sentinel-bearing score can be added
    to THIS fixture -- ``scores: None`` is the malformed shape under
    test, and ``document_id`` is the one field INV-02 deliberately
    allows in the message. There is nothing else in a ``DocumentRecord``
    to leak here, so the sentinel-absence leg genuinely doesn't apply to
    this case (unlike the missing-``document_id`` sibling below, which
    DOES carry a scores list and gets the fix)."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    records: list[DocumentRecord] = [
        {"item_id": "item-1", "document_id": "doc-0", "scores": None}  # type: ignore[typeddict-item]
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "doc-0" in message
    _assert_zero_platform_writes(http_client, tracing_client)


def test_scores_not_a_list_raises_typed_error_before_any_sdk_call() -> None:
    """Atchim's addition at ruling time: `"scores"` can be any wrong
    shape, not just `None` -- a dict is a concrete case a `.get(...,
    [])` tolerance would NOT catch either (a dict is truthy and iterable,
    so it would silently iterate its keys as if they were score dicts)."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    records: list[DocumentRecord] = [
        {  # type: ignore[typeddict-item]
            "item_id": "item-1",
            "document_id": "doc-0",
            "scores": {"id": _SENTINEL_SCORE_ID},
        }
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "doc-0" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_record_missing_document_id_raises_typed_error_before_any_sdk_call() -> None:
    """Found by Atchim at ruling time, not in the original F-1 finding.
    There is no document_id to name in this case -- the message must say
    so without inventing one (INV-02). Delta-coverage audit follow-up:
    the record carries a sentinel-bearing score (unreachable by the code
    path -- document_id is checked first -- but present in the fixture)
    so the sentinel-absence assertion actually proves something, rather
    than trivially passing because nothing sensitive was ever in the
    fixture to begin with."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_record: Any = {
        "item_id": "item-1",
        "scores": [
            {
                "id": _SENTINEL_SCORE_ID,
                "name": _SENTINEL_SCORE_NAME,
                "value": _SENTINEL_SCORE_VALUE,
            }
        ],
    }
    records: list[DocumentRecord] = [bad_record]

    with pytest.raises(ExperimentRecordFailedError, match="document_id") as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    _assert_no_score_sentinel_leaked(str(excinfo.value))
    _assert_zero_platform_writes(http_client, tracing_client)


def test_record_missing_the_scores_key_entirely_raises_typed_error_before_any_sdk_call() -> None:
    """Atchim R-2 / M2: distinct from `"scores": None` above -- the KEY
    itself absent. `record.get("scores")` must default to something that
    is NOT a list (None), never `record.get("scores", [])` -- a `[]`
    default would let a record with no "scores" key at all sail past this
    guard, reach `run_experiment` (breaking the `run_experiment_calls ==
    0` property DoD (a) requires), and stay typed only by accident via
    the task's own catch-all."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_record: Any = {"item_id": "item-1", "document_id": "doc-0"}  # no "scores" key at all
    records: list[DocumentRecord] = [bad_record]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    assert "doc-0" in str(excinfo.value)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_a_non_dict_score_raises_typed_error_before_any_sdk_call() -> None:
    """Atchim R-2 / M1: a non-dict entry in `scores` (e.g. an int) must
    also raise -- the guard's `isinstance(score, dict)` leg has nothing
    pinning it today. Deliberately a non-str (5, not "5"): a str score
    would pass a naive `"id" not in score` check via substring semantics
    instead of exercising the isinstance leg."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    records: list[DocumentRecord] = [
        {"item_id": "item-1", "document_id": "doc-0", "scores": [5]}  # type: ignore[typeddict-item]
    ]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    assert "doc-0" in str(excinfo.value)
    _assert_zero_platform_writes(http_client, tracing_client)


# --- FU-01.3-D DoD (g) / QA-01 re-audit F-1: REG-09 WIDENED. Three sibling
# shapes on the SAME seam still escaped record_run untyped, because
# langfuse_adapter.py:409 used to subscript record["item_id"] in a
# comprehension seventeen lines BEFORE the _require_record_shape loop at
# :426, and the guard checked neither "item_id" nor isinstance(record,
# dict). The structural fix (Atchim's ruling, carried verbatim in
# substance -- "enumerating keys by hand is not the fix, it IS the
# defect"): (1) validate EVERY record at the very TOP of record_run,
# before any subscript of any record -- the :409-before-:426 seam is now
# physically impossible; (2) isinstance(record, dict) is the guard's
# FIRST statement; (3) required keys are read from
# DocumentRecord.__required_keys__ (verified ['document_id', 'item_id',
# 'scores']), not hand-enumerated, so a fourth escape is structurally
# unavailable. The seven anchor tests above are left untouched.


@pytest.mark.parametrize("missing_key", sorted(DocumentRecord.__required_keys__))
def test_record_missing_any_required_key_raises_typed_error_before_any_sdk_call(
    missing_key: str,
) -> None:
    """Part (3) of the structural fix: parametrized over
    DocumentRecord.__required_keys__, NOT a hand-written list -- a new
    required field on the TypedDict auto-generates its own case here.
    Two of these three keys (document_id, scores) already had a
    hand-written anchor test above; item_id did not -- proof that
    hand-enumeration is exactly the gap this parametrization closes."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    full_record: dict[str, Any] = {
        "item_id": "item-1",
        "document_id": "doc-0",
        "scores": [
            {
                "id": _SENTINEL_SCORE_ID,
                "name": _SENTINEL_SCORE_NAME,
                "value": _SENTINEL_SCORE_VALUE,
            }
        ],
    }
    del full_record[missing_key]
    records: list[DocumentRecord] = [cast(DocumentRecord, full_record)]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    if missing_key != "document_id":
        # document_id itself can't be named in the message when IT is
        # the missing key (INV-02: no document_id to name -- the message
        # says so without inventing one, same stance as the pre-existing
        # anchor test for this case).
        assert "doc-0" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_item_id_not_a_string_raises_typed_error_before_any_sdk_call() -> None:
    """QA-01 re-audit F-1 (REG-09 widened): a well-shaped record whose
    `item_id` is the wrong type (e.g. a list) used to reach
    `record["item_id"]` in the comprehension that built
    `record_item_ids`, then `set(record_item_ids)` raised an untyped
    `TypeError: unhashable type`. Moving the shape guard to record_run's
    very top -- before that comprehension ever runs -- makes this seam
    physically impossible."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_record: Any = {
        "item_id": ["not", "a", "string"],
        "document_id": "doc-0",
        "scores": [
            {
                "id": _SENTINEL_SCORE_ID,
                "name": _SENTINEL_SCORE_NAME,
                "value": _SENTINEL_SCORE_VALUE,
            }
        ],
    }
    records: list[DocumentRecord] = [bad_record]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "doc-0" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_record_not_a_dict_raises_typed_error_before_any_sdk_call() -> None:
    """QA-01 re-audit F-1 (REG-09 widened): a record that isn't even a
    dict (e.g. a bare string) used to reach `record["item_id"]` and raise
    an untyped `TypeError: string indices must be integers`.
    `isinstance(record, dict)` is now the guard's FIRST statement --
    `"x" not in record` on a str silently does substring semantics, the
    same trap FU-01.3-B already patched one level down for non-dict
    scores (REG-09's seventh anchor case) and not one level up. No
    document_id can be named here (there is no dict to read one from,
    same stance as the missing-document_id anchor case above)."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_record: Any = "item_id"  # a plain str -- would substring-match its own key name
    records: list[DocumentRecord] = [bad_record]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    _assert_no_score_sentinel_leaked(str(excinfo.value))
    _assert_zero_platform_writes(http_client, tracing_client)
