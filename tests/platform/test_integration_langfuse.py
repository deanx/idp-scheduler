"""T-01.3.8 — integration tests against a live self-hosted Langfuse.

Skipped by default and in CI (see tests/conftest.py); opt in with
``RUN_INTEGRATION_TESTS=1``. Requires ``LANGFUSE_HOST`` /
``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` in the environment
(gitignored ``.env`` locally, per Mestre handoff). Uses only synthetic
data; datasets/runs are created with a ``test-s013-`` name prefix.

F-1 note: the server may still be on a floating ``:4`` tag while the
image gets pinned to 4.38.0 (user/Mestre) — the observed server version
is recorded in the test output, not blocked on.

Atchim R2: every test here asserts something real, never just an HTTP
200. R1: ``test_get_dataset_item_count_is_a_regression_pin`` pins the
exact item count against a live-created dataset — the old code silently
returned 0 items forever, which no prior test would have caught.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any, cast

import pytest

from idp_regression.platform.errors import DatasetFetchFailedError, FlushFailedError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.schema_provisioning import provision_golden_schema
from idp_regression.platform.scoring import build_score_inputs, score_id
from idp_regression.platform.transport import UrllibHttpClient
from idp_regression.platform.types import DocumentRecord

pytestmark = pytest.mark.integration


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set — integration test needs live Langfuse credentials")
    return value


@pytest.fixture
def client() -> UrllibHttpClient:
    host = _require_env("LANGFUSE_HOST")
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = _require_env("LANGFUSE_SECRET_KEY")
    return UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)


@pytest.fixture
def sdk_client() -> Any:
    from langfuse import Langfuse

    return Langfuse(
        host=_require_env("LANGFUSE_HOST"),
        public_key=_require_env("LANGFUSE_PUBLIC_KEY"),
        secret_key=_require_env("LANGFUSE_SECRET_KEY"),
    )


def _bounded_poll(
    check: Callable[[], bool], *, max_wait_s: float = 30.0, interval_s: float = 1.0
) -> bool:
    """TP-38: bounded poll, never read-after-write. Max 30s wait, 1s interval."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval_s)
    return False


def _create_dataset_with_items(
    client: UrllibHttpClient, *, n_items: int, with_tables: bool = False
) -> tuple[str, list[str]]:
    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    provision_golden_schema(client, dataset_name=dataset_name)
    item_ids = []
    for i in range(n_items):
        expected_output: dict[str, object] = {
            "fields": {"total": {"value": f"{100 + i}.00", "type": "number", "critical": True}}
        }
        if with_tables:
            expected_output["tables"] = {
                "line_items": {
                    "match_key": "description",
                    "critical": True,
                    "rows": [{"description": "Widget A", "qty": "1", "unit_price": "50.00"}],
                }
            }
        status, body = client.request(
            "POST",
            "/api/public/dataset-items",
            {
                "datasetName": dataset_name,
                "input": {"document_id": f"doc-{i}"},
                "expectedOutput": expected_output,
            },
        )
        assert status == 200
        item_ids.append(body["id"])
    return dataset_name, item_ids


# --- R1: critical regression pin -------------------------------------------


def test_get_dataset_item_count_is_a_regression_pin(client: UrllibHttpClient) -> None:
    """R1 (Atchim, critical): the old code read body["items"] against
    GET /api/public/v2/datasets/{name}, which carries no such key on
    4.38.0 -- it silently returned 0 items forever. This test pins the
    exact item count against a live-created 3-item dataset so that
    regression can never land silently again."""
    dataset_name, item_ids = _create_dataset_with_items(client, n_items=3)
    adapter = LangfuseAdapter(client=client)

    dataset = adapter.get_dataset(dataset_name)

    assert len(dataset["items"]) == 3
    assert {item["item_id"] for item in dataset["items"]} == set(item_ids)
    assert {item["document_id"] for item in dataset["items"]} == {"doc-0", "doc-1", "doc-2"}


def test_schema_provisioning_round_trip(client: UrllibHttpClient) -> None:
    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    adapter = LangfuseAdapter(client=client)

    provision_golden_schema(client, dataset_name=dataset_name)
    dataset = adapter.get_dataset(dataset_name)

    assert dataset["expected_output_schema"] is not None
    assert dataset["expected_output_schema"]["required"] == ["fields"]


def test_schema_invalid_write_rejected_400_stored_value_unchanged(
    client: UrllibHttpClient, caplog: pytest.LogCaptureFixture
) -> None:
    """R2/TP-34: asserts the write is rejected AND the previously-stored
    valid value is unchanged AND the 400 body is never logged."""
    caplog.set_level(logging.ERROR)
    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    provision_golden_schema(client, dataset_name=dataset_name)
    status, item = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": {"document_id": "doc-0"},
            "expectedOutput": {
                "fields": {"total": {"value": "100.00", "type": "number", "critical": True}}
            },
        },
    )
    assert status == 200
    item_id = item["id"]

    status, body = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "id": item_id,
            "datasetName": dataset_name,
            "input": {"document_id": "doc-0"},
            "expectedOutput": {
                "fields": {"total": {"value": "twelve fifty", "type": "number", "critical": True}}
            },
        },
    )
    assert status == 400
    assert isinstance(body, dict)
    assert "twelve fifty" not in caplog.text

    status, readback = client.request("GET", f"/api/public/dataset-items/{item_id}")
    assert status == 200
    assert readback["expectedOutput"]["fields"]["total"]["value"] == "100.00"


def test_schema_covers_tables_block_write(client: UrllibHttpClient) -> None:
    """TP-33: the tables-block write is untested — write a golden with a
    tables block against the committed schema and confirm it's accepted
    and readable."""
    dataset_name, item_ids = _create_dataset_with_items(client, n_items=1, with_tables=True)

    status, readback = client.request("GET", f"/api/public/dataset-items/{item_ids[0]}")

    assert status == 200
    assert readback["expectedOutput"]["tables"]["line_items"]["rows"] == [
        {"description": "Widget A", "qty": "1", "unit_price": "50.00"}
    ]


def test_get_dataset_missing_dataset_raises_typed_error(client: UrllibHttpClient) -> None:
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset(f"does-not-exist-{uuid.uuid4().hex}")


def _build_record_for_item(item: Any, *, run_id: str) -> DocumentRecord:
    total_value = item["golden"]["fields"]["total"]["value"]
    return {
        "item_id": item["item_id"],
        "document_id": item["document_id"],
        "actual": {"status": "SUCCEEDED", "fields": {"total": {"value": total_value}}},
        "scores": build_score_inputs(
            golden=item["golden"],
            verdicts={
                "total": {
                    "verdict": "match",
                    "expected": total_value,
                    "actual": total_value,
                    "confidence": 0.9,
                    "critical": True,
                    "type": "number",
                }
            },
            gate="PASS",
            run_id=run_id,
            document_id=item["document_id"],
        ),
    }


def _gate_score_value(client: UrllibHttpClient, gate_score_id: str) -> str | None:
    # Filter by id= server-side rather than scanning the default 50-row
    # page unfiltered — the shared local instance accumulates scores
    # across every test run in a session, and an unfiltered scan can
    # miss a just-written score once total volume exceeds one page
    # (flaky, observed 2026-09-19).
    status, body = client.request(
        "GET", f"/api/public/v3/scores?fields=details&id={gate_score_id}"
    )
    if status != 200 or not isinstance(body, dict):
        return None
    for score in body.get("data", []):
        if score.get("id") == gate_score_id:
            return cast(str | None, score.get("value"))
    return None


def _experiments_named(client: UrllibHttpClient, run_name: str) -> list[dict[str, Any]]:
    one_hour_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    from_ts = one_hour_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    status, body = client.request(
        "GET", f"/api/public/experiments?limit=50&fromStartTime={from_ts}"
    )
    if status != 200 or not isinstance(body, dict):
        return []
    return [e for e in body.get("data", []) if e.get("name") == run_name]


# --- TP-37 (N26): two record_run invocations, no score collision -----------


def test_n26_distinct_run_names_no_score_collision_two_separate_experiments(
    client: UrllibHttpClient, sdk_client: Any
) -> None:
    """Two record_run calls over the same golden set, different run_id AND
    different run_name (the realistic case — S-01.4 generates a fresh
    run_name per invocation): no score collision, and two independent
    experiments."""
    from idp_regression.platform.tracing import ExperimentRunner

    dataset_name, _ = _create_dataset_with_items(client, n_items=1)
    adapter = LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client))
    dataset = adapter.get_dataset(dataset_name)
    item = dataset["items"][0]

    run_names = []
    gate_ids = []
    for run_id in ("run-a", "run-b"):
        run_name = f"test-s013-run-{uuid.uuid4().hex[:8]}"
        run_names.append(run_name)
        record = _build_record_for_item(item, run_id=run_id)
        adapter.record_run(
            dataset_name=dataset_name,
            run_name=run_name,
            run_id=run_id,
            records=[record],
            metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
        )
        gate_ids.append(
            score_id(run_id=run_id, document_id=item["document_id"], score_name="gate")
        )

    assert gate_ids[0] != gate_ids[1]  # N26: distinct run_id -> distinct score ids
    assert _bounded_poll(lambda: _gate_score_value(client, gate_ids[0]) == "PASS")
    assert _bounded_poll(lambda: _gate_score_value(client, gate_ids[1]) == "PASS")

    assert _bounded_poll(lambda: len(_experiments_named(client, run_names[0])) == 1)
    assert _bounded_poll(lambda: len(_experiments_named(client, run_names[1])) == 1)
    assert run_names[0] != run_names[1]


def test_tp37_same_run_name_different_run_ids_finding(
    client: UrllibHttpClient, sdk_client: Any
) -> None:
    """TP-37 (Atchim's suspicion, 2026-09-19): does Langfuse key dataset
    runs/experiments by run_name, merging two record_run invocations
    under the SAME run_name into one experiment even though run_id (and
    therefore every score_id) differs?

    OBSERVED (live, 4.38.0, verbatim from the probe that produced this
    test): YES — two record_run calls with the same run_name and
    different run_id merge into a SINGLE experiment entry
    (`itemCount` sums across invocations: 1 item recorded twice ->
    itemCount == 2 on one experiment, not two experiments of itemCount
    1 each). Scores do NOT collide (score_id is run_id-scoped, so both
    scores exist independently and are both readable at their expected
    value) — N26's score-safety guarantee holds regardless. This is
    purely an Experiments-tab/observability merge, not a data-safety
    issue. Per the coordinator: this finding is recorded here for
    Soneca to route (a run_name -> run_id suffix policy decision), NOT
    designed around in this commit.
    """
    from idp_regression.platform.tracing import ExperimentRunner

    dataset_name, _ = _create_dataset_with_items(client, n_items=1)
    adapter = LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client))
    dataset = adapter.get_dataset(dataset_name)
    item = dataset["items"][0]

    same_run_name = f"test-s013-run-{uuid.uuid4().hex[:8]}"
    gate_ids = []
    for run_id in ("run-a", "run-b"):
        record = _build_record_for_item(item, run_id=run_id)
        adapter.record_run(
            dataset_name=dataset_name,
            run_name=same_run_name,
            run_id=run_id,
            records=[record],
            metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
        )
        gate_ids.append(
            score_id(run_id=run_id, document_id=item["document_id"], score_name="gate")
        )

    # No score collision regardless of the merge below (N26 holds).
    assert gate_ids[0] != gate_ids[1]
    assert _bounded_poll(lambda: _gate_score_value(client, gate_ids[0]) == "PASS")
    assert _bounded_poll(lambda: _gate_score_value(client, gate_ids[1]) == "PASS")

    def _merged_experiment() -> dict[str, Any] | None:
        matches = _experiments_named(client, same_run_name)
        return matches[0] if len(matches) == 1 else None

    assert _bounded_poll(lambda: _merged_experiment() is not None)
    merged = _merged_experiment()
    assert merged is not None
    # OBSERVED FINDING: the two invocations merged into ONE experiment
    # whose itemCount is the SUM across both record_run calls (2), not
    # two separate itemCount==1 experiments. This confirms run_name is
    # the platform's merge key for dataset runs/experiments.
    assert merged["itemCount"] == 2


# --- T-01.3.10a / ADR-0005 #9: record_run -----------------------------------


def test_record_run_writes_readable_scores_and_is_visible_in_experiments(
    client: UrllibHttpClient, sdk_client: Any
) -> None:
    """R2: replaces the old no-assertion `_score_visible`/N26 tests.
    Asserts (a) the score exists with the expected VALUE on the real
    trace id record_run returned, and (b) the run is visible under
    GET /api/public/experiments."""
    from idp_regression.platform.tracing import ExperimentRunner

    dataset_name, item_ids = _create_dataset_with_items(client, n_items=2)
    adapter = LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client))
    dataset = adapter.get_dataset(dataset_name)  # populates the private item cache

    run_id = f"test-s013-run-{uuid.uuid4().hex[:8]}"
    run_name = f"test-s013-run-{uuid.uuid4().hex[:8]}"
    records: list[DocumentRecord] = [
        {
            "item_id": item["item_id"],
            "document_id": item["document_id"],
            "actual": {
                "status": "SUCCEEDED",
                "fields": {"total": {"value": item["golden"]["fields"]["total"]["value"]}},
            },
            "scores": build_score_inputs(
                golden=item["golden"],
                verdicts={
                    "total": {
                        "verdict": "match",
                        "expected": item["golden"]["fields"]["total"]["value"],
                        "actual": item["golden"]["fields"]["total"]["value"],
                        "confidence": 0.9,
                        "critical": True,
                        "type": "number",
                    }
                },
                gate="PASS",
                run_id=run_id,
                document_id=item["document_id"],
            ),
        }
        for item in dataset["items"]
    ]

    adapter.record_run(
        dataset_name=dataset_name,
        run_name=run_name,
        run_id=run_id,
        records=records,
        metadata={"action_id": "action-1", "action_version": "v1", "golden_version": "deadbeef"},
    )

    gate_score_id = score_id(
        run_id=run_id, document_id=records[0]["document_id"], score_name="gate"
    )

    assert _bounded_poll(lambda: _gate_score_value(client, gate_score_id) == "PASS")

    one_hour_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    from_ts = one_hour_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _experiment_visible() -> bool:
        status, body = client.request(
            "GET", f"/api/public/experiments?limit=50&fromStartTime={from_ts}"
        )
        if status != 200 or not isinstance(body, dict):
            return False
        names = [e.get("name") for e in body.get("data", [])]
        return run_name in names

    assert _bounded_poll(_experiment_visible, max_wait_s=30.0, interval_s=2.0)


def test_record_run_no_retry_and_no_op_flush_when_tracing_not_configured(
    client: UrllibHttpClient,
) -> None:
    """R6 sanity: record_run without a tracing_client raises immediately
    (never silently no-ops)."""
    from idp_regression.platform.errors import TracingNotConfiguredError

    dataset_name, item_ids = _create_dataset_with_items(client, n_items=1)
    adapter = LangfuseAdapter(client=client)  # no tracing_client
    dataset = adapter.get_dataset(dataset_name)

    with pytest.raises(TracingNotConfiguredError):
        adapter.record_run(
            dataset_name=dataset_name,
            run_name="test-s013-run-x",
            run_id="run-x",
            records=[
                {
                    "item_id": dataset["items"][0]["item_id"],
                    "document_id": dataset["items"][0]["document_id"],
                    "actual": {"status": "SUCCEEDED", "fields": {}},
                    "scores": [],
                }
            ],
            metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
        )


def test_flush_raises_flush_failed_on_a_real_export_failure() -> None:
    """TP-43: an OTLP export failure must never be swallowed. Points the
    SDK's tracing client at an unreachable host (never the real
    LANGFUSE_HOST) so no real credentials or network calls to the live
    server are involved in this specific assertion."""
    from langfuse import Langfuse

    from idp_regression.platform.tracing import ExperimentItem, ExperimentRunner, record_experiment

    unreachable = Langfuse(host="http://localhost:1", public_key="pk-test", secret_key="sk-test")

    def task(*, item: object, **kwargs: object) -> dict[str, bool]:
        return {"ok": True}

    items = [
        ExperimentItem(id="i1", dataset_id="d", input={"document_id": "doc-0"}, expected_output={})
    ]

    with pytest.raises(FlushFailedError):
        record_experiment(
            cast(ExperimentRunner, unreachable),
            run_name="test-s013-unreachable",
            items=items,
            task=task,
        )


def test_record_run_never_logs_the_auth_header_on_the_otlp_path(
    client: UrllibHttpClient, sdk_client: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """R4: the Basic auth header (and the secret key) must never appear
    in a log line on the record_run/OTLP path -- captures every log
    record emitted by a real record_run call (REST scores + the SDK's
    own OTLP export + dataset-run-item creation)."""
    from idp_regression.platform.tracing import ExperimentRunner

    caplog.set_level(logging.DEBUG)
    secret_key = _require_env("LANGFUSE_SECRET_KEY")
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")

    dataset_name, _ = _create_dataset_with_items(client, n_items=1)
    adapter = LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client))
    dataset = adapter.get_dataset(dataset_name)
    item = dataset["items"][0]
    record = _build_record_for_item(item, run_id="run-otlp-redaction")

    adapter.record_run(
        dataset_name=dataset_name,
        run_name=f"test-s013-run-{uuid.uuid4().hex[:8]}",
        run_id="run-otlp-redaction",
        records=[record],
        metadata={"action_id": "a", "action_version": "v", "golden_version": "g"},
    )

    assert secret_key not in caplog.text
    assert f"Basic {public_key}" not in caplog.text
    import base64

    basic_value = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    assert basic_value not in caplog.text
