"""LangfuseAdapter — the concrete PlatformAdapter (ADR-0001, ADR-0005).

All Langfuse-specific knowledge (endpoints, auth, wire shapes) is confined
to this module (and ``transport.py``/``schema_provisioning.py``) — NFR N24
swappability, enforced by ``tests/platform/test_module_boundary.py``.

Design note (flagged for Atchim): the ``run_status`` metadata marker
(ADR-0004 #14) is implemented here as a well-known score
(``name="run_status"``) rather than a dataset-run/trace attribute, because
the OTLP/v4-SDK trace-linkage mechanism (T-01.3.10a) is not yet built (see
the handoff blocker). This keeps `mark_run_status` usable without OTLP but
is a provisional wire shape, not empirically confirmed against a live
"run" object — it should be revisited once T-01.3.10a lands and may need
to move onto the trace/dataset-run linkage instead.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, cast

from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
)
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

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def get_dataset(self, name: str) -> Dataset:
        status, body = self._client.request("GET", f"/api/public/v2/datasets/{name}")
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

        raw_items = body.get("items", [])
        items: list[DatasetItem] = [
            {
                "document_id": raw_item["input"]["document_id"],
                "golden": raw_item["expectedOutput"],
            }
            for raw_item in raw_items
        ]
        schema = body.get("expectedOutputSchema")
        return {"items": items, "expected_output_schema": schema}

    def write_scores(self, run_id: str, document_id: str, scores: list[ScoreInput]) -> None:
        for score in scores:
            status, body = self._client.request(
                "POST",
                "/api/public/scores",
                {
                    "id": score["id"],
                    "name": score["name"],
                    "value": score["value"],
                    "comment": score.get("comment"),
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
        """No buffered trace exporter exists yet (T-01.3.10a not landed) —
        this is a genuine no-op, not a best-effort swallow of a real
        failure. Once OTLP export lands, this must surface an export
        failure as ``FlushFailedError`` (never best-effort, TP-43)."""
        return None

    def mark_run_status(
        self,
        run_id: str,
        status: Literal["aborted", "complete"],
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
    ) -> None:
        from idp_regression.platform.scoring import score_id

        comment = (
            f"action_id={action_id} action_version={action_version} "
            f"golden_version={golden_version}"
        )
        run_status_id = score_id(
            run_id=run_id, document_id="run", score_name=_RUN_STATUS_SCORE_NAME
        )
        resp_status, body = self._client.request(
            "POST",
            "/api/public/scores",
            {
                "id": run_status_id,
                "name": _RUN_STATUS_SCORE_NAME,
                "value": status,
                "comment": comment,
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
    already called ``load_dotenv()`` (INV-05 — this module never does)."""
    platform = os.environ.get("PLATFORM", "langfuse")
    if platform != "langfuse":
        raise ValueError(f"unsupported PLATFORM: {platform!r}")
    client = UrllibHttpClient(
        host=os.environ["LANGFUSE_HOST"],
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    )
    return cast(PlatformAdapter, LangfuseAdapter(client=client))
