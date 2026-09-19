"""LangfuseAdapter — the concrete PlatformAdapter (ADR-0001, ADR-0005 #9).

All Langfuse-specific knowledge (endpoints, auth, wire shapes) is confined
to this module (and ``transport.py``/``schema_provisioning.py``/
``tracing.py``) — NFR N24 swappability, enforced by
``tests/platform/test_module_boundary.py``.

Design note (flagged for Atchim): the ``run_status`` metadata marker
(ADR-0004 #14) is implemented here as a well-known score
(``name="run_status"``) rather than a dataset-run/trace attribute
(DEBT-15) — ``record_run``'s linkage is per-dataset-item, so there is no
single "run-level" trace to attach a marker to, and an aborted run may
have written no records at all.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any, Literal, cast

from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    ExperimentRecordFailedError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
    TracingNotConfiguredError,
    TransportError,
)
from idp_regression.platform.tracing import ExperimentItem, ExperimentRunner, record_experiment
from idp_regression.platform.transport import HttpClient, UrllibHttpClient
from idp_regression.platform.types import (
    Dataset,
    DatasetItem,
    DocumentRecord,
    PlatformAdapter,
    RunMetadata,
    ScoreInput,
)

logger = logging.getLogger(__name__)

_RUN_STATUS_SCORE_NAME = "run_status"


def _body_snippet_for_error(body: Any) -> str:
    """A logging-safe error summary — deliberately NEVER the raw response
    body. A 400 validation body can echo back golden-shaped values (NFR
    N5, INV-02), so only the body's *shape* is recorded, never its content.
    """
    if body is None:
        return "<empty body>"
    return f"<{type(body).__name__} body, {len(str(body))} chars, redacted>"


class LangfuseAdapter:
    """PlatformAdapter implementation over the Langfuse public REST API."""

    def __init__(self, client: HttpClient, tracing_client: ExperimentRunner | None = None) -> None:
        self._client = client
        self._tracing_client = tracing_client
        #: item_id -> dataset_id from the most recent get_dataset call —
        #: record_run() reads this instead of re-fetching (INV-04,
        #: ADR-0005 #9 "no second fetch"). DEBT-18 (Atchim suggestion):
        #: this used to also cache the golden per item, but nothing reads
        #: it any more (expected_output is always {} — see record_run) —
        #: keeping it would be dead sensitive data sitting in memory for
        #: no reason, so only dataset_id is kept.
        self._item_cache: dict[str, str] = {}
        #: the dataset name that produced ``_item_cache`` — record_run
        #: checks its own ``dataset_name`` argument against this so a
        #: caller passing the wrong dataset (while reusing stale item ids
        #: from a previous, correctly-fetched dataset) is caught rather
        #: than silently recording against the wrong dataset.
        self._cached_dataset_name: str | None = None

    def get_dataset(self, name: str) -> Dataset:
        encoded_name = urllib.parse.quote(name, safe="")
        try:
            status, body = self._client.request(
                "GET", f"/api/public/v2/datasets/{encoded_name}"
            )
        except TransportError as exc:
            raise DatasetFetchFailedError(f"get_dataset transport failure: {exc}") from exc
        if status >= 400:
            logger.error(
                "dataset_fetch_failed status=%s dataset=%s detail=%s",
                status,
                name,
                _body_snippet_for_error(body),
            )
            raise DatasetFetchFailedError(f"get_dataset failed with HTTP {status}")
        if not isinstance(body, dict):
            raise DatasetFetchFailedError("get_dataset returned an unexpected body shape")
        dataset_id = body.get("id")
        schema = body.get("expectedOutputSchema")

        items = self._fetch_all_dataset_items(name, encoded_name, dataset_id)
        self._cached_dataset_name = name
        return {"items": items, "expected_output_schema": schema}

    def _fetch_all_dataset_items(
        self, name: str, encoded_name: str, dataset_id: Any
    ) -> list[DatasetItem]:
        """R1 (Atchim, critical): items come from the separate, paginated
        ``GET /api/public/dataset-items?datasetName=`` endpoint — the
        ``GET /api/public/v2/datasets/{name}`` response carries NO ``items``
        key on Langfuse 4.38.0 (live-probed 2026-09-19)."""
        items: list[DatasetItem] = []
        self._item_cache = {}
        page = 1
        total_pages = 1
        while page <= total_pages:
            path = f"/api/public/dataset-items?datasetName={encoded_name}&page={page}"
            try:
                status, body = self._client.request("GET", path)
            except TransportError as exc:
                raise DatasetFetchFailedError(
                    f"dataset-items fetch transport failure: {exc}"
                ) from exc
            if status >= 400:
                logger.error(
                    "dataset_fetch_failed status=%s dataset=%s detail=%s",
                    status,
                    name,
                    _body_snippet_for_error(body),
                )
                raise DatasetFetchFailedError(f"dataset-items fetch failed with HTTP {status}")
            if not isinstance(body, dict):
                raise DatasetFetchFailedError("dataset-items returned an unexpected body shape")

            for raw_item in body.get("data", []):
                try:
                    item_id = raw_item["id"]
                    document_id = raw_item["input"]["document_id"]
                    golden = raw_item["expectedOutput"]
                except (KeyError, TypeError) as exc:
                    raise DatasetFetchFailedError(
                        f"malformed dataset item (missing {exc})"
                    ) from exc
                items.append(
                    {"item_id": item_id, "document_id": document_id, "golden": golden}
                )
                self._item_cache[item_id] = dataset_id

            meta = body.get("meta", {})
            total_pages = meta.get("totalPages", 1) if isinstance(meta, dict) else 1
            page += 1
        return items

    def _write_scores(self, *, trace_id: str, document_id: str, scores: list[ScoreInput]) -> None:
        """Adapter-private (ADR-0005 #9 — no longer on the Protocol). Called
        by ``record_run`` once a real, ingested ``trace_id`` is known for
        the document (never the deterministic pre-#9 ``trace_id()``, which
        ``mark_run_status`` still uses for its own sentinel trace)."""
        for score in scores:
            status, body = self._client.request(
                "POST",
                "/api/public/scores",
                {
                    "id": score["id"],
                    "name": score["name"],
                    "value": score["value"],
                    "comment": score.get("comment"),
                    "traceId": trace_id,
                    "dataType": "CATEGORICAL",
                },
            )
            if status >= 400:
                logger.error(
                    "score_write_failed status=%s document_id=%s score_name=%s detail=%s",
                    status,
                    document_id,
                    score["name"],
                    _body_snippet_for_error(body),
                )
                raise ScoreWriteFailedError(
                    f"write_scores failed for {score['name']!r} with HTTP {status}"
                )

    def record_run(
        self,
        *,
        dataset_name: str,
        run_name: str,
        run_id: str,
        records: list[DocumentRecord],
        metadata: RunMetadata,
    ) -> None:
        """ADR-0005 Decision #9: record a complete run once, after every
        gate is already known. No retry — see the ADR for why (a failed
        OTLP batch has already exhausted the exporter's own retries; a
        failed run_experiment is not safely re-runnable per run_name).
        """
        if self._tracing_client is None:
            raise TracingNotConfiguredError(
                "record_run requires a tracing_client (OTLP/v4 SDK) — none configured"
            )
        if dataset_name != self._cached_dataset_name:
            raise ExperimentRecordFailedError(
                f"record_run: dataset_name {dataset_name!r} does not match the dataset "
                f"{self._cached_dataset_name!r} last fetched by get_dataset() — call "
                "get_dataset(dataset_name) first, in this same run"
            )

        record_item_ids = [record["item_id"] for record in records]
        if len(record_item_ids) != len(set(record_item_ids)):
            raise ExperimentRecordFailedError("record_run: duplicate item_id in records")
        if set(record_item_ids) != set(self._item_cache.keys()):
            raise ExperimentRecordFailedError(
                "record_run: records' item_ids do not exactly match the fetched dataset items "
                "(call get_dataset(dataset_name) first, in this same run)"
            )

        records_by_item_id = {record["item_id"]: record for record in records}
        task_failed = False

        def task(*, item: ExperimentItem, **kwargs: Any) -> dict[str, str]:
            # A total function that cannot raise (ADR-0005 #9 defense in
            # depth): str(exception) must never reach a span attribute.
            # DEBT-18 (user decision, option B): the output is the verdict
            # map only (score name -> score value, e.g. "match"/"PASS") --
            # never an extracted/expected value or a confidence number.
            # The golden lives only in its Langfuse dataset item.
            nonlocal task_failed
            try:
                record = records_by_item_id[item.id]
                return {score["name"]: score["value"] for score in record["scores"]}
            except Exception:  # noqa: BLE001 - intentional total catch, no exception text kept
                task_failed = True
                return {"record_error": "task_failed"}

        experiment_items = [
            ExperimentItem(
                id=item_id,
                dataset_id=self._item_cache[item_id],
                input={"document_id": records_by_item_id[item_id]["document_id"]},
                # DEBT-18 option B: never copy the golden into a span --
                # spans reference the item by id; the golden lives only in
                # its Langfuse dataset item.
                expected_output={},
            )
            for item_id in record_item_ids
        ]

        trace_ids = record_experiment(
            self._tracing_client,
            run_name=run_name,
            items=experiment_items,
            task=task,
            metadata={
                "action_id": metadata["action_id"],
                "action_version": metadata["action_version"],
                "golden_version": metadata["golden_version"],
            },
        )

        if task_failed:
            raise ExperimentRecordFailedError(
                "record_run: the total task caught an unexpected exception for at least one item"
            )

        for record in records:
            trace_id = trace_ids[record["item_id"]]
            self._write_scores(
                trace_id=trace_id, document_id=record["document_id"], scores=record["scores"]
            )

    def mark_run_status(
        self,
        run_id: str,
        status: Literal["aborted", "complete"],
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
    ) -> None:
        from idp_regression.platform.scoring import (
            RUN_LEVEL_TRACE_SENTINEL,
            score_id,
            trace_id,
        )

        comment = (
            f"action_id={action_id} action_version={action_version} "
            f"golden_version={golden_version}"
        )
        run_status_id = score_id(
            run_id=run_id,
            document_id=RUN_LEVEL_TRACE_SENTINEL,
            score_name=_RUN_STATUS_SCORE_NAME,
        )
        run_status_trace_id = trace_id(run_id=run_id, document_id=RUN_LEVEL_TRACE_SENTINEL)
        resp_status, body = self._client.request(
            "POST",
            "/api/public/scores",
            {
                "id": run_status_id,
                "name": _RUN_STATUS_SCORE_NAME,
                "value": status,
                "comment": comment,
                "traceId": run_status_trace_id,
                "dataType": "CATEGORICAL",
            },
        )
        if resp_status >= 400:
            logger.error(
                "run_status_write_failed status=%s run_id=%s detail=%s",
                resp_status,
                run_id,
                _body_snippet_for_error(body),
            )
            raise RunStatusWriteFailedError(f"mark_run_status failed with HTTP {resp_status}")


def make_platform() -> PlatformAdapter:
    """Factory reading ``PLATFORM`` from env (ADR-0001). Assumes the caller
    already called ``load_dotenv()`` (INV-05 — this module never does).

    Constructs both the raw-REST ``HttpClient`` (datasets/schema/scores)
    and the ``langfuse`` SDK's OTel-based client (T-01.3.10a trace +
    dataset-run linkage) — the only Langfuse SDK import in this codebase,
    confined here per NFR N24.
    """
    platform = os.environ.get("PLATFORM", "langfuse")
    if platform != "langfuse":
        raise ValueError(f"unsupported PLATFORM: {platform!r}")
    host = os.environ["LANGFUSE_HOST"]
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ["LANGFUSE_SECRET_KEY"]
    client = UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)

    from langfuse import Langfuse  # local import: confine the SDK to this factory

    sdk_client = Langfuse(host=host, public_key=public_key, secret_key=secret_key)
    return cast(
        PlatformAdapter,
        LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client)),
    )
