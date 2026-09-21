"""record_run — item-id precondition, task_failed raise, and the total
task's catch-all (Atchim mutation-testing findings, re-review 2026-09-19).

All against a mocked ExperimentRunner (no network) — these are pure
control-flow tests, not integration facts.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict, cast, get_type_hints

import pytest

from idp_regression.platform.errors import ExperimentRecordFailedError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.scoring import field_score_name, prompt_score_name, score_id
from idp_regression.platform.types import DocumentRecord, RunMetadata, ScoreInput

_METADATA: RunMetadata = {"action_id": "a", "action_version": "v", "golden_version": "g"}


def _str_fields(td: type) -> list[str]:
    """Extract the `str`-annotated field names of a TypedDict, sorted --
    the SINGLE derivation both the real `document_id`/`item_id`
    parametrize below and `_StrFieldProbe`'s dedicated pin (further down)
    call through. Atchim R-4 (fresh DEBT-44 instance, FU-01.3-G fix
    round): extracting this into a named, independently-testable function
    is what makes the "type-driven, not a hand list in disguise" claim
    PINNABLE -- `DocumentRecord` alone has only two `str` fields today,
    so a hand-written `["item_id", "document_id"]` and a genuine
    `get_type_hints` derivation are indistinguishable by any test that
    only ever looks at `DocumentRecord`. `_str_fields` gives the claim a
    second, structurally different subject (`_StrFieldProbe`) to be
    tested against."""
    return sorted(name for name, hint in get_type_hints(td).items() if hint is str)


class _StrFieldProbe(TypedDict):
    """A dedicated probe TypedDict for `_str_fields` -- deliberately NOT
    shaped like `DocumentRecord` (three `str` fields, not two, plus a
    `NotRequired[str]`, an `int`, and a `list[str]`) so the filter is
    exercised on a shape a hand-written literal couldn't coincidentally
    match. ⚠️ MUST stay MODULE-level: with `from __future__ import
    annotations`, `get_type_hints` resolves forward-referenced
    annotations against the DEFINING MODULE's globals -- a function-local
    TypedDict has no such globals entry and `NotRequired` raises
    `NameError` at resolution time."""

    field_a: str
    field_b: str
    field_c: str
    optional_field: NotRequired[str]
    count_field: int
    list_field: list[str]


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
#: Atchim R-2 (FU-01.3-G fix round, DEBT-44 fresh instance): a distinctive
#: marker carried INSIDE a wrong-type value, so a test can assert INV-02
#: is actually ENFORCED (message stays a constant string) rather than
#: merely reasoned about in a comment. A non-string on purpose -- this is
#: what's assigned to the `str`-annotated field under test.
_SENTINEL_DOCUMENT_ID_MARKER = "SENTINEL-DOCUMENT-ID-do-not-leak-7c3a91"
_SENTINEL_DOCUMENT_ID: Any = [_SENTINEL_DOCUMENT_ID_MARKER]


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
    same stance as the missing-document_id anchor case above).

    Atchim re-review R-1: the fixture must contain ALL THREE required
    key names as substrings (not just one, as the original "item_id"
    fixture did). With `isinstance(record, dict)` deleted, `key not in
    record` does substring semantics -- a fixture containing only
    "item_id" happens to make `"document_id" not in record` True, so
    the `__required_keys__` loop raises a TYPED
    ExperimentRecordFailedError BY ACCIDENT, via the exact trap this
    guard exists to prevent, and the mutant is never observed. Worse:
    `DocumentRecord.__required_keys__` is a frozenset, so which key the
    loop happens to check first (and therefore whether it "accidentally"
    raises) varies with `PYTHONHASHSEED` -- DEBT-34's defect class
    (an assertion that observes nothing) reproduced inside the very
    card whose job includes closing DEBT-34. With all three key names
    present as substrings, `key not in record` is False for every key
    under every hash seed, the loop always falls through, and the
    guard's `isinstance` check is the ONLY thing standing between this
    fixture and an untyped exception -- deterministically, every seed.
    Also asserts the SPECIFIC message, not just that SOME typed error
    was raised."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    # Deliberately contains all three required key names as substrings
    # -- see the docstring above for why a partial match (e.g. just
    # "item_id") does not pin the guard.
    bad_record: Any = "item_id document_id scores"
    records: list[DocumentRecord] = [bad_record]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    assert "not a dict" in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)


def test_shape_validation_covers_every_record_not_just_the_first() -> None:
    """Atchim re-review R-2: `for record in records:` -> `for record in
    records[:1]:` survived the entire 171-test platform suite, because
    every malformed-record test in this file used a SINGLE-element
    `records` list -- the "very top" half of DoD (g) part (1) was
    pinned, the "EVERY record" half was not. A well-formed record at
    index 0, malformed at index 1: the run must still fail closed."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    well_formed: DocumentRecord = {
        "item_id": "item-1",
        "document_id": "doc-0",
        "scores": [],
    }
    malformed: Any = {
        "item_id": ["not", "a", "string"],
        "document_id": "doc-1",
        "scores": [],
    }
    records: list[DocumentRecord] = [well_formed, malformed]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    assert "doc-1" in str(excinfo.value)
    _assert_zero_platform_writes(http_client, tracing_client)


# --- FU-01.3-G / QA-01 re-audit #3 F-1 / REG-09 (reopened): VALUE pins
# become type-driven, exactly as DoD (3) of FU-01.3-D made PRESENCE pins
# type-driven. `_require_record_shape` checked `item_id`'s value (:112)
# but had NO equivalent check for `document_id`, though `types.py:65`
# declares `document_id: str` -- a non-string `document_id` (None, 7,
# ["doc"], {"k": "v"}) sailed past the guard, `score_id()` is an f-string
# so it stringifies anything and the A3 derivation check passed, and the
# score LANDED (1 HTTP call, 1 SDK call) -- `record_run` returned SUCCESS
# on malformed input. Every prior REG-09 shape was Minor because it was
# fail-closed; this is the first that WRITES.
#
# DEBT-42 S-2 prohibition still applies here: this parametrization only
# DELETES a valid value's type (never synthesises a new valid value), so
# the case is generated, never a guessed-valid replacement -- fail-loud
# is still the feature.


def test_str_fields_extracts_only_str_annotated_fields_from_a_probe_type() -> None:
    """Atchim R-4 (fresh DEBT-44 instance): pins `_str_fields` ITSELF
    against `_StrFieldProbe` -- a shape with THREE str fields (not two,
    matching `DocumentRecord`'s own count by coincidence), a
    `NotRequired[str]` (resolves to `str` via `get_type_hints`, so it
    belongs in the result), an `int`, and a `list[str]` (both must be
    excluded). Before this test, replacing `_str_fields`'s body with the
    literal `["item_id", "document_id"]` passed the entire 496-test
    suite (mutation (c) of the FU-01.3-G fix round, reported honestly) --
    with only two str fields on `DocumentRecord`, "reads the type" and "a
    hand list that happens to match today's shape" were indistinguishable
    by any test that only ever exercised `DocumentRecord`. This probe
    breaks that coincidence."""
    assert _str_fields(_StrFieldProbe) == [
        "field_a",
        "field_b",
        "field_c",
        "optional_field",
    ]


def test_str_fields_of_document_record_is_document_id_and_item_id() -> None:
    """The production-shape counterpart to the probe pin above -- a
    probe-only pin cannot catch a mutation that mis-scopes `_str_fields`
    BY NAME (e.g. `and name != "document_id"`, Atchim's own M4), since
    `_StrFieldProbe`'s field names never collide with `DocumentRecord`'s.
    `test_record_field_wrong_type_raises_typed_error_before_any_sdk_call`
    below would silently drop a parametrize leg under that mutation (495
    passed, no signal) -- this direct equality assertion is what makes
    that silent drop visible."""
    assert _str_fields(DocumentRecord) == ["document_id", "item_id"]


@pytest.mark.parametrize("field_name", _str_fields(DocumentRecord))
def test_record_field_wrong_type_raises_typed_error_before_any_sdk_call(field_name: str) -> None:
    """THE KILLING TEST for REG-09's reopened row (QA-01 re-audit #3 F-1).
    Parametrized over the `str`-annotated fields of
    `typing.get_type_hints(DocumentRecord)` -- NOT a hand-written
    `item_id`/`document_id` pair -- so a future scalar field on
    DocumentRecord auto-generates its own wrong-type-value case here, the
    value-side twin of `test_record_missing_any_required_key_raises_...`
    above (which does the same for PRESENCE over `__required_keys__`).
    Before the fix this parametrize has exactly one RED leg
    (`document_id`): no raise, `run_experiment_calls == 1`,
    `http_client.calls` non-empty -- the fail-open write QA-01 found.
    `item_id` was already covered by the pre-existing hand-written anchor
    `test_item_id_not_a_string_raises_typed_error_before_any_sdk_call`
    above; this test does not replace that anchor, it generalises it.

    The score id MUST be the real ``score_id(run_id, document_id,
    score_name)`` derivation (not a sentinel) -- a sentinel id would trip
    the unrelated A3 run_id-derivation precondition first (which DOES
    interpolate `score_name` into its message, unlike INV-02's shape
    guard), catching the malformed field by coincidence instead of
    proving the actual defect: that nothing on the shape-validation path
    ever looked at this field's type before writing.

    Atchim R-2 (fresh DEBT-44 instance, M7): the `document_id` leg's bad
    value MUST itself carry a distinctive, assertable marker
    (`_SENTINEL_DOCUMENT_ID`), not a plain `["not", "a", "string"]" --
    reasoning that the guard's message is a constant string is not the
    same as PINNING it. Without a marker, a mutant that changes the
    message to interpolate `{record['document_id']!r}` survives every
    assertion here. With the marker, that mutant is caught the same way
    `_assert_no_score_sentinel_leaked` already catches an interpolated
    score value elsewhere in this file."""
    adapter, http_client, tracing_client = _adapter_with_tracing("item-1")
    bad_value: Any = (
        _SENTINEL_DOCUMENT_ID if field_name == "document_id" else ["not", "a", "string"]
    )
    document_id_for_score_id = bad_value if field_name == "document_id" else "doc-0"
    full_record: dict[str, Any] = {
        "item_id": "item-1",
        "document_id": "doc-0",
        "scores": [
            {
                "id": score_id(
                    run_id="run-1",
                    document_id=document_id_for_score_id,
                    score_name=_SENTINEL_SCORE_NAME,
                ),
                "name": _SENTINEL_SCORE_NAME,
                "value": _SENTINEL_SCORE_VALUE,
            }
        ],
    }
    full_record[field_name] = bad_value  # wrong TYPE, never a synthesised value
    records: list[DocumentRecord] = [cast(DocumentRecord, full_record)]

    with pytest.raises(ExperimentRecordFailedError) as excinfo:
        adapter.record_run(
            dataset_name="ds", run_name="r", run_id="run-1", records=records, metadata=_METADATA
        )

    message = str(excinfo.value)
    if field_name != "document_id":
        # document_id itself can't be named in the message when IT is the
        # field under test (INV-02: never interpolate the offending
        # value, and here document_id IS the bad value) -- same stance as
        # the missing-document_id anchor case above.
        assert "doc-0" in message
    else:
        # F-G2 (third DEBT-44 Atchim instance, /test TDD gate): INV-02
        # has two clauses here and only the "never the value" half was
        # pinned -- the "name the field" half was reasoned about in a
        # comment and enforced nowhere. Mirrors the get_dataset twin's
        # `assert "document_id" in message` below.
        assert "document_id" in message
        # R-2 / M7: the marker carried by the bad document_id value must
        # never leak into the message -- pinning INV-02 for this guard,
        # not just reasoning about it in a comment.
        assert _SENTINEL_DOCUMENT_ID_MARKER not in message
    _assert_no_score_sentinel_leaked(message)
    _assert_zero_platform_writes(http_client, tracing_client)
