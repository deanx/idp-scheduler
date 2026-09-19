"""LangfuseAdapter — the concrete PlatformAdapter (ADR-0001, ADR-0005).

All Langfuse-specific knowledge (endpoints, auth, wire shapes) is confined
to this module (and ``transport.py``/``schema_provisioning.py``/
``tracing.py``) — NFR N24 swappability, enforced by
``tests/platform/test_module_boundary.py``.

Design note (flagged for Atchim): the ``run_status`` metadata marker
(ADR-0004 #14) is implemented here as a well-known score
(``name="run_status"``) rather than a dataset-run/trace attribute
(DEBT-15) — this remains true even after T-01.3.10a landed, because the
only Experiments-tab-visible linkage mechanism found (``run_experiment``,
see ``tracing.py``) is a bulk, per-dataset-item task runner with no
natural single "run-level" trace to attach a marker to.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any, Literal, cast

from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
    TracingNotConfiguredError,
    TransportError,
)
from idp_regression.platform.tracing import TracingClient, flush_or_raise, run_dataset_experiment
from idp_regression.platform.transport import HttpClient, UrllibHttpClient
from idp_regression.platform.types import Dataset, DatasetItem, PlatformAdapter, ScoreInput

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

    def __init__(self, client: HttpClient, tracing_client: TracingClient | None = None) -> None:
        self._client = client
        self._tracing_client = tracing_client
        #: item_id -> (dataset_id, golden) from the most recent get_dataset
        #: call — record_run() reads this instead of re-fetching (INV-04,
        #: ADR-0005 #9 "no second fetch").
        self._item_cache: dict[str, tuple[str, dict[str, Any]]] = {}

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
                self._item_cache[item_id] = (dataset_id, golden)

            meta = body.get("meta", {})
            total_pages = meta.get("totalPages", 1) if isinstance(meta, dict) else 1
            page += 1
        return items

    def write_scores(self, run_id: str, document_id: str, scores: list[ScoreInput]) -> None:
        from idp_regression.platform.scoring import trace_id

        score_trace_id = trace_id(run_id=run_id, document_id=document_id)
        for score in scores:
            status, body = self._client.request(
                "POST",
                "/api/public/scores",
                {
                    "id": score["id"],
                    "name": score["name"],
                    "value": score["value"],
                    "comment": score.get("comment"),
                    "traceId": score_trace_id,
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

    def flush(self) -> None:
        """Flush the OTLP trace exporter (T-01.3.10a). No-op if no
        ``tracing_client`` was configured (e.g. unit tests that only
        exercise the REST score/dataset paths). Never best-effort once a
        tracing client IS configured — raises ``FlushFailedError`` on a
        real export failure (TP-43); see ``tracing.flush_or_raise``.
        """
        if self._tracing_client is None:
            return None
        flush_or_raise(self._tracing_client)

    def run_dataset_experiment(
        self, *, run_name: str, dataset_items: list[Any], task: Any
    ) -> Any:
        """T-01.3.10a: run ``task`` once per dataset item under a single
        named experiment, visible in the dataset's Experiments tab
        (``tracing.run_dataset_experiment`` — the only linkage mechanism
        empirically confirmed to work, see that module's docstring).
        """
        if self._tracing_client is None:
            raise TracingNotConfiguredError(
                "run_dataset_experiment requires a tracing_client (OTLP/v4 SDK) — none configured"
            )
        return run_dataset_experiment(
            self._tracing_client, run_name=run_name, dataset_items=dataset_items, task=task
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
        LangfuseAdapter(client=client, tracing_client=cast(TracingClient, sdk_client)),
    )
