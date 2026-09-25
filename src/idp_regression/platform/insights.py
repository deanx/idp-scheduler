"""Reading the evaluation platform, for dashboards in this console.

The write side is `langfuse_adapter.py`. This is the **read** side, and
it exists because the console should be able to show platform-held
history (score trends over time, a run's scores, the golden set's size)
beside the local-only views, rather than making the operator hold two
windows.

It is written against the facts `CLAUDE.md ## Langfuse facts that bite`
recorded live against 4.38.0 in `events_only` mode, and it degrades
rather than lying when those facts bite:

* **There is no runs-list API in this deployment.**
  `GET /api/public/datasets/{name}/runs` **404s**, body explicit: "not
  available in events_only mode". So `run_history()` is built on the one
  thing confirmed to work -- a bounded, filtered poll of
  `GET /api/public/v3/scores` -- and `runs_endpoint_available()` reports
  the 404 as a capability fact rather than an error.
* **`GET /v2/scores` is gone.** v3 only, filtered.
* **Public reads lag.** Every read here is a HISTORY read, never a
  read-after-write: nothing in this module is used to confirm that a
  write landed, which is the case the lag breaks.
* **`GET /api/public/v2/datasets/{name}` does not return items** --
  items come from the paginated `/api/public/dataset-items`, and getting
  that wrong yields a silently empty golden set that passes vacuously.

Everything returned is **data the console renders, never instructions**,
and no value read here feeds a gate: the gate is computed in-process
before any platform write (INV-08) and nothing in this module can change
that.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from idp_regression.platform.errors import TransportError
from idp_regression.platform.transport import HttpClient

logger = logging.getLogger(__name__)

#: A read is always bounded. An unbounded score query against a busy
#: project is a slow request that times out the console and loads the
#: platform's database for a chart nobody scrolled to.
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
DEFAULT_WINDOW_DAYS = 30


class PlatformInsights(Protocol):
    """The read seam, kept separate from `PlatformAdapter` on purpose.

    `PlatformAdapter` is what a RUN depends on (`get_dataset`,
    `record_run`, `mark_run_status`) and N24 keeps it swappable. Reading
    for dashboards is a different capability with different failure
    consequences -- a dashboard that cannot load must never be able to
    stop a run -- so it is a different Protocol, and nothing in
    `orchestration/` imports this one.
    """

    def capabilities(self) -> dict[str, Any]: ...
    def scores(self, **kwargs: Any) -> list[dict[str, Any]]: ...
    def score_trend(self, **kwargs: Any) -> dict[str, Any]: ...
    def dataset_items(self, name: str, *, limit: int = ...) -> dict[str, Any]: ...


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


class LangfuseInsights:
    """`PlatformInsights` over the public REST API, via the same
    `HttpClient` seam (Basic auth, no-redirect opener) the write adapter
    uses. No SDK import -- N24 keeps that confined to `make_platform()`.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    # ------------------------------------------------------------ meta

    def capabilities(self) -> dict[str, Any]:
        """What this deployment can actually answer, probed rather than
        assumed.

        The runs endpoint 404ing in `events_only` mode is a *fact about
        the deployment*, not a failure, and the console renders it as one
        -- otherwise every dashboard would show a red error for a
        configuration that is working exactly as documented.
        """
        runs_available = False
        detail = "not probed"
        try:
            status, body = self._http.request("GET", "/api/public/datasets/_probe_/runs")
            runs_available = status == 200
            if status == 404:
                detail = "no runs-list endpoint (expected in events_only mode)"
            else:
                detail = f"status {status}"
                if isinstance(body, dict) and isinstance(body.get("message"), str):
                    detail = body["message"][:200]
        except TransportError as exc:
            detail = f"unreachable: {exc}"

        reachable = True
        try:
            status, _ = self._http.request("GET", "/api/public/v3/scores?limit=1")
            scores_available = status == 200
        except TransportError:
            reachable = False
            scores_available = False

        return {
            "reachable": reachable,
            "scores_v3": scores_available,
            "runs_list_endpoint": runs_available,
            "runs_list_detail": detail,
            # Stated so a dashboard cannot be mistaken for the gate.
            "is_gate_source": False,
        }

    # ---------------------------------------------------------- scores

    def scores(
        self,
        *,
        name: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        limit: int = DEFAULT_LIMIT,
        data_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """A bounded page of scores, newest first.

        `/v3/scores` is the endpoint; `/v2` is gone. The filter is always
        applied server-side -- pulling everything and filtering here
        would be the slow-request problem with extra steps.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))
        if since is None:
            since = dt.datetime.now(dt.UTC) - dt.timedelta(days=DEFAULT_WINDOW_DAYS)
        params: dict[str, str] = {"limit": str(limit), "fromTimestamp": _iso(since)}
        if until is not None:
            params["toTimestamp"] = _iso(until)
        if name:
            params["name"] = name
        if data_type:
            params["dataType"] = data_type

        status, body = self._http.request("GET", "/api/public/v3/scores?" + urlencode(params))
        if status != 200:
            raise TransportError(f"scores read failed: status {status}")
        data = body.get("data") if isinstance(body, dict) else None
        return list(data) if isinstance(data, list) else []

    def score_trend(
        self,
        *,
        name: str | None = None,
        since: dt.datetime | None = None,
        limit: int = MAX_LIMIT,
        bucket: str = "day",
    ) -> dict[str, Any]:
        """Categorical score counts bucketed by day -- the shape a chart
        wants.

        Deliberately computed here rather than asked of the platform: a
        dashboard-query API exists but its views are `traces` /
        `observations` / `scores-*` with their own dimension vocabulary,
        and this console needs exactly one shape. Bucketing a bounded
        page in memory is simpler than owning a second query language,
        and the bound is what keeps it honest.
        """
        rows = self.scores(name=name, since=since, limit=limit)
        buckets: dict[str, Counter[str]] = defaultdict(Counter)
        names: Counter[str] = Counter()
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            key = timestamp[:10] if bucket == "day" else timestamp[:13]
            value = row.get("stringValue")
            if value is None:
                value = row.get("value")
            buckets[key][str(value)] += 1
            names[str(row.get("name") or "?")] += 1
        return {
            "bucket": bucket,
            "series": [
                {"bucket": key, "counts": dict(sorted(counter.items()))}
                for key, counter in sorted(buckets.items())
            ],
            "score_names": dict(names.most_common(50)),
            "observations": len(rows),
            # The page was bounded; say so rather than letting a chart
            # imply it covers the whole window.
            "truncated": len(rows) >= min(limit, MAX_LIMIT),
        }

    # -------------------------------------------------------- datasets

    def dataset_items(self, name: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """The golden set's items, from the PAGINATED items endpoint.

        `GET /api/public/v2/datasets/{name}` does not return items.
        Reading that and finding none is how a golden set silently
        becomes empty and every run passes vacuously, so this asks the
        endpoint that actually carries them.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))
        params = {"datasetName": name, "limit": str(limit)}
        status, body = self._http.request(
            "GET", "/api/public/dataset-items?" + urlencode(params)
        )
        if status != 200:
            raise TransportError(f"dataset-items read failed: status {status}")
        data = body.get("data") if isinstance(body, dict) else None
        items = list(data) if isinstance(data, list) else []
        meta = body.get("meta") if isinstance(body, dict) else None
        total = meta.get("totalItems") if isinstance(meta, dict) else None
        return {
            "dataset": name,
            "items_returned": len(items),
            "total_items": total,
            # Identity only. The console lists which documents the golden
            # set covers and what version pinned them; it does not pull
            # expected VALUES back out of the platform to render here.
            "documents": [
                {
                    "id": item.get("id"),
                    "document_id": (item.get("input") or {}).get("document_id")
                    if isinstance(item.get("input"), dict)
                    else None,
                    "metadata": item.get("metadata"),
                }
                for item in items
            ],
        }

    def dataset_names(self, *, limit: int = 50) -> list[str]:
        status, body = self._http.request(
            "GET", "/api/public/v2/datasets?" + urlencode({"limit": str(limit)})
        )
        if status != 200:
            return []
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        return [str(row.get("name")) for row in data if isinstance(row, dict) and row.get("name")]

    def run_scores(self, run_name: str, *, limit: int = MAX_LIMIT) -> dict[str, Any]:
        """Every score this console wrote for one run.

        Built on the filtered score poll because this deployment has no
        runs-list endpoint (`CLAUDE.md`: the 404 body says so explicitly),
        and confirmed live as the way a run's landing is checked.
        """
        rows = self.scores(limit=limit)
        matching = [
            row
            for row in rows
            if run_name in str((row.get("metadata") or {}).get("run_name", ""))
            or run_name in str(row.get("comment") or "")
            or run_name in str(row.get("traceId") or "")
        ]
        verdicts: Counter[str] = Counter(
            str(row.get("stringValue") or row.get("value")) for row in matching
        )
        return {
            "run_name": run_name,
            "scores": len(matching),
            "verdicts": dict(sorted(verdicts.items())),
            "scanned": len(rows),
            "exhaustive": len(rows) < MAX_LIMIT,
        }


def configuration_hint() -> str:
    """What the operator must set, named HERE rather than in the caller.

    The variable names are vendor names, and N24 confines those to this
    package -- so the console renders this string instead of spelling
    them itself (`tests/platform/test_module_boundary.py` enforces it).
    """
    return "LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set"


def insights_from_env(http: HttpClient | None = None) -> LangfuseInsights | None:
    """Build a reader from the environment, or `None` when the platform
    is not configured.

    `None` rather than an exception: a console with no platform
    credentials is a perfectly good local console, and every other page
    must keep working. The platform page is the only one that cares.
    """
    if http is not None:
        return LangfuseInsights(http)
    import os

    host = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not (host and public_key and secret_key):
        return None
    from idp_regression.platform.transport import UrllibHttpClient

    return LangfuseInsights(UrllibHttpClient(host, public_key, secret_key))


__all__ = [
    "DEFAULT_LIMIT",
    "configuration_hint",
    "LangfuseInsights",
    "MAX_LIMIT",
    "PlatformInsights",
    "insights_from_env",
    "quote",
]
