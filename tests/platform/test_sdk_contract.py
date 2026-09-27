"""DEBT-17: the langfuse SDK surface `record_run` depends on, pinned against the
REAL installed package -- not a fake.

`record_run` (ADR-0005 #9) drives `Langfuse.run_experiment` with duck-typed
items and reads its result back. Every other test in this directory uses a
fake client, which agrees with our code by construction, so a version bump
that renamed a keyword, a result attribute, or the duck-typing check would
pass the whole suite and fail only in production, silently in the linking
case. These tests read the installed SDK and fail LOUDLY on any of that.
They are the first thing to re-run, with CT-05 and the three
`make_platform` SDK-privates tests, before any `langfuse` bump
(CLAUDE.md `## External services`).
"""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

langfuse = pytest.importorskip("langfuse")

from langfuse import Langfuse  # noqa: E402
from langfuse.experiment import ExperimentItemResult, ExperimentResult  # noqa: E402

from idp_regression.platform.tracing import ExperimentItem  # noqa: E402


def _params(func: object) -> set[str]:
    return set(inspect.signature(func).parameters)  # type: ignore[arg-type]


def test_run_experiment_still_takes_every_keyword_record_experiment_passes() -> None:
    """`tracing.record_experiment` calls it with exactly these keywords."""
    passed = {"name", "run_name", "data", "task", "max_concurrency", "metadata"}
    assert passed <= _params(Langfuse.run_experiment)


def test_the_result_still_exposes_what_record_experiment_reads() -> None:
    """`record_experiment` reads `result.item_results`, then each item
    result's `.trace_id`, `.dataset_run_id` and `.item.id`."""
    assert "item_results" in _params(ExperimentResult.__init__)
    assert {"item", "trace_id", "dataset_run_id"} <= _params(ExperimentItemResult.__init__)
    item = ExperimentItem(id="i", dataset_id="d", input={}, expected_output={}, metadata=None)
    result = ExperimentItemResult(
        item=item,  # type: ignore[arg-type]
        output=None,
        evaluations=[],
        trace_id="t",
        dataset_run_id="r",
    )
    assert (result.item.id, result.trace_id, result.dataset_run_id) == ("i", "t", "r")  # type: ignore[union-attr]


def test_the_sdk_still_links_items_to_a_dataset_run_by_duck_typing() -> None:
    """The SDK links an item to its dataset run only when the item has BOTH
    an `id` and a `dataset_id` (`hasattr` checks inside `run_experiment`).
    If that check changed, runs would stop appearing linked in the
    Experiments tab with nothing raising -- ADR-0005 #9's worst case."""
    source = inspect.getsource(Langfuse)
    assert 'hasattr(item, "dataset_id")' in source
    assert 'hasattr(item, "id")' in source
    names = {f.name for f in fields(ExperimentItem)}
    assert {"id", "dataset_id", "input", "expected_output", "metadata"} <= names
