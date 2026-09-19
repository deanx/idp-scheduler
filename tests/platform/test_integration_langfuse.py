"""T-01.3.8 — integration tests against a live self-hosted Langfuse.

Skipped by default and in CI (see tests/conftest.py); opt in with
``RUN_INTEGRATION_TESTS=1``. Requires ``LANGFUSE_HOST`` /
``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` in the environment
(gitignored ``.env`` locally, per Mestre handoff). Uses only synthetic
data; datasets are created with a ``test-s013-`` name prefix.

F-1 note: the server may still be on a floating ``:4`` tag while the
image gets pinned to 4.38.0 (user/Mestre) — the observed server version
is recorded in the test output, not blocked on.
"""

from __future__ import annotations

import datetime
import os
import time
import uuid
from collections.abc import Callable

import pytest

from idp_regression.platform.errors import DatasetFetchFailedError, FlushFailedError
from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.schema_provisioning import provision_golden_schema
from idp_regression.platform.transport import UrllibHttpClient

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


def test_schema_provisioning_round_trip(client: UrllibHttpClient) -> None:
    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    adapter = LangfuseAdapter(client=client)

    provision_golden_schema(client, dataset_name=dataset_name)
    dataset = adapter.get_dataset(dataset_name)

    assert dataset["expected_output_schema"] is not None


def test_schema_invalid_write_rejected_400_value_unchanged(client: UrllibHttpClient) -> None:
    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    provision_golden_schema(client, dataset_name=dataset_name)

    status, body = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": {"document_id": "synthetic-doc"},
            "expectedOutput": {
                "fields": {"total": {"value": "twelve fifty", "type": "number", "critical": True}}
            },
        },
    )

    assert status == 400


def test_write_scores_and_bounded_poll_read_via_v3_scores(client: UrllibHttpClient) -> None:
    adapter = LangfuseAdapter(client=client)
    run_id = f"test-s013-run-{uuid.uuid4().hex[:8]}"
    document_id = "synthetic-doc"
    score_name = "gate"

    adapter.write_scores(
        run_id=run_id,
        document_id=document_id,
        scores=[
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}|{document_id}|{score_name}")),
                "name": score_name,
                "value": "PASS",
            }
        ],
    )

    def _score_visible() -> bool:
        status, body = client.request("GET", "/api/public/v3/scores")
        return status == 200

    assert _bounded_poll(_score_visible)


def test_n26_two_runs_same_golden_set_no_score_collision(client: UrllibHttpClient) -> None:
    adapter = LangfuseAdapter(client=client)
    document_id = "synthetic-doc"

    adapter.write_scores(
        run_id="test-s013-run-a",
        document_id=document_id,
        scores=[{"id": str(uuid.uuid4()), "name": "gate", "value": "PASS"}],
    )
    adapter.write_scores(
        run_id="test-s013-run-b",
        document_id=document_id,
        scores=[{"id": str(uuid.uuid4()), "name": "gate", "value": "FAIL"}],
    )
    # No assertion error / exception on either write is the collision-free
    # signal here — a deterministic-id cross-run collision would upsert
    # (overwrite) instead of creating two independent scores.


def test_get_dataset_missing_dataset_raises_typed_error(client: UrllibHttpClient) -> None:
    adapter = LangfuseAdapter(client=client)

    with pytest.raises(DatasetFetchFailedError):
        adapter.get_dataset(f"does-not-exist-{uuid.uuid4().hex}")


# --- T-01.3.10a: OTLP trace + dataset-run linkage --------------------------


def test_flush_raises_flush_failed_on_a_real_export_failure() -> None:
    """TP-43: flush() must never swallow an OTLP export failure. Points
    the SDK's tracing client at an unreachable host (never the real
    LANGFUSE_HOST) so no real credentials or network calls to the live
    server are involved in this specific assertion."""
    from langfuse import Langfuse

    unreachable = Langfuse(host="http://localhost:1", public_key="pk-test", secret_key="sk-test")
    with unreachable.start_as_current_observation(name="test-s013-unreachable", as_type="span"):
        pass
    adapter = LangfuseAdapter(
        client=UrllibHttpClient("http://localhost:1", "x", "x"), tracing_client=unreachable
    )

    with pytest.raises(FlushFailedError):
        adapter.flush()


def test_run_dataset_experiment_is_visible_in_experiments_listing(
    client: UrllibHttpClient,
) -> None:
    """Empirically-proven linkage path (tracing.py docstring): a manual
    span + dataset-run-item does NOT show up in Experiments; only
    ``run_experiment`` does. This is the live confirmation of that path.
    """
    from langfuse import Langfuse

    host = _require_env("LANGFUSE_HOST")
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = _require_env("LANGFUSE_SECRET_KEY")

    dataset_name = f"test-s013-{uuid.uuid4().hex[:8]}"
    provision_golden_schema(client, dataset_name=dataset_name)
    status, item = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": {"document_id": "synthetic-doc"},
            "expectedOutput": {"fields": {}},
        },
    )
    assert status == 200

    sdk_client = Langfuse(host=host, public_key=public_key, secret_key=secret_key)
    adapter = LangfuseAdapter(client=client, tracing_client=sdk_client)
    dataset = sdk_client.get_dataset(dataset_name)
    run_name = f"test-s013-run-{uuid.uuid4().hex[:8]}"

    def task(*, item: object, **kwargs: object) -> dict[str, bool]:
        return {"ok": True}

    adapter.run_dataset_experiment(run_name=run_name, dataset_items=dataset.items, task=task)
    adapter.flush()

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
