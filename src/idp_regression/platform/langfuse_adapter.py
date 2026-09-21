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
import random
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, Literal, cast

from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    ExperimentRecordFailedError,
    PlatformConfigurationError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
    TracingNotConfiguredError,
    TransportError,
)
from idp_regression.platform.scoring import RUN_LEVEL_TRACE_SENTINEL, score_id, trace_id
from idp_regression.platform.tracing import ExperimentItem, ExperimentRunner, record_experiment
from idp_regression.platform.transport import HttpClient, UrllibHttpClient, sanitize_for_log
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

#: Hard cap on dataset-items pages fetched per get_dataset() call (REG-04,
#: F-3) -- generous for any real golden set, but stops an untrusted/bogus
#: ``meta.totalPages`` from looping unbounded.
_MAX_DATASET_PAGES = 500

#: Bounded score-write retry defaults (REG-03, F-2; ADR-0005 #9: the
#: deterministic score_id makes a retry an upsert, so a 5xx/transport
#: failure is safe to retry). Backoff shape matches ADR-0004 #3's
#: transient-transport-retry spec: exponential, full jitter, base 1s,
#: cap 8s, default 3 attempts.
_DEFAULT_SCORE_WRITE_MAX_ATTEMPTS = 3
_DEFAULT_SCORE_WRITE_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_SCORE_WRITE_BACKOFF_CAP_SECONDS = 8.0


def _require_record_shape(record: DocumentRecord) -> None:
    """FU-01.3-B / QA-01 F-1 / REG-09 (widened by FU-01.3-D): validate a
    record's SHAPE before ANY subscript of it, so malformed caller input
    raises the typed ``ExperimentRecordFailedError`` (the Protocol
    docstring's promised contract, ``types.py:99-101``) instead of an
    untyped ``KeyError``/``TypeError``. Called from a loop at the very
    TOP of ``record_run``, before ``record_item_ids`` is even built --
    the seam where ``record["item_id"]`` used to be subscripted
    seventeen lines before this guard ran is now physically impossible,
    not merely patched at that call site. Runs before any SDK call, so
    ``run_experiment_calls == 0`` holds on every path here. Absorbs the
    ``.get("scores")`` tolerance debt from FU-01.3-A -- this replaces it.

    FU-01.3-D / QA-01 re-audit F-1 structural fix (Atchim's ruling,
    carried verbatim in substance -- "enumerating keys by hand is not
    the fix, it IS the defect"):
      1. ``isinstance(record, dict)`` is the FIRST statement --
         ``"x" not in record`` on a ``str`` silently does substring
         semantics, the same trap already patched one level down for
         non-dict scores (REG-09's seventh anchor case).
      2. Required keys are read from ``DocumentRecord.__required_keys__``
         (verified ``{'document_id', 'item_id', 'scores'}``), not
         hand-enumerated, so a new required field on the TypedDict
         auto-generates its own guard here -- three consecutive
         hand-written attempts enumerated a subset of that list (0 for 3).
      3. ``item_id``'s VALUE (not just its presence) is checked -- a
         non-string ``item_id`` would otherwise sail past the key check
         and later blow up ``set(record_item_ids)`` with an untyped
         ``TypeError: unhashable type``.

    INV-02: the message names ``document_id`` ONLY -- never a score id,
    name, or value. Cases with no document_id to name (record is not a
    dict at all, or ``document_id`` itself is the missing key) say so
    without inventing one.
    """
    if not isinstance(record, dict):
        raise ExperimentRecordFailedError("record_run: a record is not a dict (malformed input)")
    for key in sorted(DocumentRecord.__required_keys__):
        if key not in record:
            if key == "document_id":
                raise ExperimentRecordFailedError("record_run: a record is missing 'document_id'")
            raise ExperimentRecordFailedError(
                f"record_run: record for document_id={record.get('document_id')!r} "
                f"is missing {key!r}"
            )
    if not isinstance(record["document_id"], str):
        # FU-01.3-G / QA-01 re-audit #3 F-1 / REG-09 (reopened): the
        # first FAIL-OPEN member of this family. INV-02: name the FIELD
        # only, never interpolate the value -- document_id itself IS the
        # offending value here, unlike the item_id check below (which
        # can safely name a document_id already known to be a str).
        raise ExperimentRecordFailedError(
            "record_run: a record has a non-string 'document_id'"
        )
    document_id = record["document_id"]
    if not isinstance(record["item_id"], str):
        raise ExperimentRecordFailedError(
            f"record_run: record for document_id={document_id!r} has a non-string 'item_id'"
        )
    scores = record.get("scores")
    if not isinstance(scores, list):
        # Covers both `"scores": None` (TypeError today) and any other
        # non-list shape (e.g. a dict -- truthy and iterable, so a bare
        # `.get(..., [])` tolerance would NOT have caught it either).
        raise ExperimentRecordFailedError(
            f"record_run: record for document_id={document_id!r} has a non-list "
            "'scores' value (expected a list of score dicts)"
        )
    for score in scores:
        if not isinstance(score, dict) or "id" not in score:
            raise ExperimentRecordFailedError(
                f"record_run: a score for document_id={document_id!r} is missing 'id'"
            )
        if "name" not in score:
            raise ExperimentRecordFailedError(
                f"record_run: a score for document_id={document_id!r} is missing 'name'"
            )


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

    def __init__(
        self,
        client: HttpClient,
        tracing_client: ExperimentRunner | None = None,
        *,
        score_write_max_attempts: int = _DEFAULT_SCORE_WRITE_MAX_ATTEMPTS,
        score_write_backoff_base_seconds: float = _DEFAULT_SCORE_WRITE_BACKOFF_BASE_SECONDS,
        score_write_backoff_cap_seconds: float = _DEFAULT_SCORE_WRITE_BACKOFF_CAP_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        random_func: Callable[[], float] = random.random,
        record_deadline_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._tracing_client = tracing_client
        self._score_write_max_attempts = score_write_max_attempts
        self._score_write_backoff_base_seconds = score_write_backoff_base_seconds
        self._score_write_backoff_cap_seconds = score_write_backoff_cap_seconds
        self._sleep = sleep
        self._random = random_func
        #: DEBT-20: a whole-record-phase wall-clock cap (INV-07,
        #: monotonic). None = disabled -- the shipped default. This is
        #: the ADAPTER's own deadline (Soneca ruling: the orchestrator
        #: never sees the per-score loop under ADR-0005 #9, so it has
        #: nothing to time). PROVISIONAL: the real value needs S-01.6
        #: timings + N8's 50-doc arithmetic, neither of which exist yet
        #: -- a low guess would convert a slow-but-alive platform from
        #: *stretching* a run into *aborting* one that would otherwise
        #: have succeeded, on a CI gate whose whole job is to be trusted.
        self._record_deadline_seconds = record_deadline_seconds
        self._clock = clock
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
                sanitize_for_log(name),
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
            if page > _MAX_DATASET_PAGES:
                raise DatasetFetchFailedError(
                    f"dataset-items pagination exceeded the page cap ({_MAX_DATASET_PAGES})"
                )
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
                    sanitize_for_log(name),
                    _body_snippet_for_error(body),
                )
                raise DatasetFetchFailedError(f"dataset-items fetch failed with HTTP {status}")
            if not isinstance(body, dict):
                raise DatasetFetchFailedError("dataset-items returned an unexpected body shape")

            data = body.get("data")
            if not isinstance(data, list):
                raise DatasetFetchFailedError(
                    "dataset-items response 'data' is not a list (untrusted shape)"
                )
            meta = body.get("meta")
            if not isinstance(meta, dict):
                raise DatasetFetchFailedError("dataset-items response is missing 'meta'")
            raw_total_pages = meta.get("totalPages")
            if (
                not isinstance(raw_total_pages, int)
                or isinstance(raw_total_pages, bool)
                or raw_total_pages < 0
            ):
                # 0 is a legitimate shape (an empty dataset, live-confirmed
                # on Langfuse 4.38.0) -- only a negative or non-int value
                # is untrusted/malformed.
                raise DatasetFetchFailedError(
                    "dataset-items response 'meta.totalPages' is not a non-negative integer"
                )

            for raw_item in data:
                try:
                    item_id = raw_item["id"]
                    document_id = raw_item["input"]["document_id"]
                    golden = raw_item["expectedOutput"]
                except (KeyError, TypeError) as exc:
                    raise DatasetFetchFailedError(
                        f"malformed dataset item (missing {exc})"
                    ) from exc
                if not isinstance(document_id, str):
                    # FU-01.3-G / QA-01 re-audit #3 F-1 / REG-09
                    # (reopened): the OTHER trust boundary -- the golden
                    # schema (CT-05) guards `expectedOutput`, not
                    # `input`, so a malformed platform item otherwise
                    # flows in here and back out untyped (REG-04
                    # family). INV-02: name the field only, never the
                    # offending value.
                    raise DatasetFetchFailedError(
                        "malformed dataset item: 'document_id' is not a string"
                    )
                items.append(
                    {"item_id": item_id, "document_id": document_id, "golden": golden}
                )
                self._item_cache[item_id] = dataset_id

            total_pages = raw_total_pages
            page += 1
        return items

    def _check_record_deadline(
        self, *, deadline: float | None, document_id: str, score_name: str
    ) -> None:
        """DEBT-20: the whole-record-phase deadline, checked between
        scores AND between retry attempts (INV-07 monotonic). A no-op
        when disabled (``deadline is None``)."""
        if deadline is not None and self._clock() >= deadline:
            raise ScoreWriteFailedError(
                "write_scores: record-phase deadline exceeded before writing "
                f"score {score_name!r} for document_id={document_id!r}"
            )

    def _write_scores(
        self,
        *,
        trace_id: str,
        document_id: str,
        scores: list[ScoreInput],
        deadline: float | None = None,
    ) -> None:
        """Adapter-private (ADR-0005 #9 — no longer on the Protocol). Called
        by ``record_run`` once a real, ingested ``trace_id`` is known for
        the document (never the deterministic pre-#9 ``trace_id()``, which
        ``mark_run_status`` still uses for its own sentinel trace).

        ``deadline`` (DEBT-20) is an absolute ``self._clock()``-scale
        value, shared across every record in the same ``record_run`` call
        -- computed once by the caller, not reset per record."""
        for score in scores:
            self._check_record_deadline(
                deadline=deadline, document_id=document_id, score_name=score["name"]
            )
            self._write_score_with_retry(
                trace_id=trace_id, document_id=document_id, score=score, deadline=deadline
            )

    def _write_score_with_retry(
        self,
        *,
        trace_id: str,
        document_id: str,
        score: ScoreInput,
        deadline: float | None = None,
    ) -> None:
        """REG-03/F-2: a bounded retry (ADR-0004 #3 backoff shape). The
        score_id is deterministic (ADR-0005 #5), so every retried attempt
        re-sends the exact same payload -- an upsert, never a duplicate.
        Retries only 5xx and TransportError (transient); a 4xx is a
        caller/contract bug and is never retried."""
        payload = {
            "id": score["id"],
            "name": score["name"],
            "value": score["value"],
            "comment": score.get("comment"),
            "traceId": trace_id,
            "dataType": "CATEGORICAL",
        }
        last_status: int | None = None
        last_body: Any = None
        for attempt in range(1, self._score_write_max_attempts + 1):
            self._check_record_deadline(
                deadline=deadline, document_id=document_id, score_name=score["name"]
            )
            try:
                status, body = self._client.request("POST", "/api/public/scores", payload)
            except TransportError as exc:
                if attempt >= self._score_write_max_attempts:
                    logger.error(
                        "score_write_failed status=transport document_id=%s score_name=%s "
                        "attempts=%s detail=%s",
                        sanitize_for_log(document_id),
                        sanitize_for_log(score["name"]),
                        attempt,
                        sanitize_for_log(str(exc)),
                    )
                    raise ScoreWriteFailedError(
                        f"write_scores transport failure for {score['name']!r} "
                        f"after {attempt} attempts"
                    ) from exc
                self._sleep(self._backoff_delay_seconds(attempt))
                continue

            if status < 400:
                return
            if status < 500:
                # 4xx is a caller/contract bug (e.g. malformed payload) --
                # never retried, exactly one attempt.
                logger.error(
                    "score_write_failed status=%s document_id=%s score_name=%s detail=%s",
                    status,
                    sanitize_for_log(document_id),
                    sanitize_for_log(score["name"]),
                    _body_snippet_for_error(body),
                )
                raise ScoreWriteFailedError(
                    f"write_scores failed for {score['name']!r} with HTTP {status}"
                )

            last_status, last_body = status, body
            if attempt >= self._score_write_max_attempts:
                break
            self._sleep(self._backoff_delay_seconds(attempt))

        logger.error(
            "score_write_failed status=%s document_id=%s score_name=%s attempts=%s detail=%s",
            last_status,
            sanitize_for_log(document_id),
            sanitize_for_log(score["name"]),
            self._score_write_max_attempts,
            _body_snippet_for_error(last_body),
        )
        raise ScoreWriteFailedError(
            f"write_scores failed for {score['name']!r} with HTTP {last_status} "
            f"after {self._score_write_max_attempts} attempts"
        )

    def _backoff_delay_seconds(self, attempt: int) -> float:
        """Exponential full jitter (ADR-0004 #3): uniform(0, min(cap, base * 2**(attempt-1)))."""
        ceiling: float = min(
            self._score_write_backoff_cap_seconds,
            self._score_write_backoff_base_seconds * (2 ** (attempt - 1)),
        )
        jitter: float = self._random()
        return jitter * ceiling

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

        # FU-01.3-D / QA-01 re-audit F-1 (REG-09 widened): validate EVERY
        # record's shape here, at the very TOP of record_run, before ANY
        # subscript of ANY record below -- the seam where
        # record["item_id"] used to be subscripted seventeen lines before
        # this guard ran is now physically impossible, not merely patched
        # at that call site.
        for record in records:
            _require_record_shape(record)

        record_item_ids = [record["item_id"] for record in records]
        if len(record_item_ids) != len(set(record_item_ids)):
            raise ExperimentRecordFailedError("record_run: duplicate item_id in records")
        if set(record_item_ids) != set(self._item_cache.keys()):
            raise ExperimentRecordFailedError(
                "record_run: records' item_ids do not exactly match the fetched dataset items "
                "(call get_dataset(dataset_name) first, in this same run)"
            )

        # ADR-0005 #9 amendment A3 (Soneca, 2026-09-20): run_id is verified,
        # not decorative. N26's cross-invocation no-overwrite guarantee rests
        # on every scores[*].id having been derived from THIS run_id — score
        # ids are the upsert key, so ids belonging to another invocation would
        # overwrite that run's scores and still record as correct. A pure
        # local loop over data already in hand: no extra call, no network.
        # INV-02: the raise names document_id + score_name only (both
        # value-free by construction) and never the offending id pair.
        # (Shape already validated above -- this loop only derives ids.)
        for record in records:
            document_id = record["document_id"]
            for score in record.get("scores", []):
                score_name = score["name"]
                if score["id"] != score_id(
                    run_id=run_id, document_id=document_id, score_name=score_name
                ):
                    raise ExperimentRecordFailedError(
                        "record_run: a score id was not derived from the run_id passed in "
                        f"this call (document_id={document_id!r}, "
                        f"score_name={score_name!r}) — every score id must be "
                        "score_id(run_id, document_id, score_name) for this same run (N26)"
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

        # DEBT-20: one deadline for the WHOLE record phase (every record,
        # every score, every retry attempt below) -- computed once here,
        # not reset per record. Disabled (None) unless a caller opted in.
        deadline = (
            None
            if self._record_deadline_seconds is None
            else self._clock() + self._record_deadline_seconds
        )
        for record in records:
            trace_id = trace_ids[record["item_id"]]
            self._write_scores(
                trace_id=trace_id,
                document_id=record["document_id"],
                scores=record["scores"],
                deadline=deadline,
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
                sanitize_for_log(run_id),
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

    # Atchim PIN 2026-09-20 (new REG): the langfuse SDK's own base_url
    # resolution prioritizes LANGFUSE_BASE_URL over the explicit `host=`
    # constructor arg below -- if it's set and disagrees with
    # LANGFUSE_HOST (the only variable CLAUDE.md's External services
    # table documents), the SDK client would silently authenticate
    # against a host the operator never named via LANGFUSE_HOST, split
    # traffic across two platform instances with no marker (DEBT-28-like),
    # and leak credentials to an undeclared host (REG-07's class, arriving
    # via env instead of a 3xx). Fail closed, before either client is
    # constructed. Names only in the message -- never the values, since a
    # URL can embed a credential (INV-02).
    base_url_override = os.environ.get("LANGFUSE_BASE_URL")
    if base_url_override is not None and base_url_override != host:
        raise PlatformConfigurationError(
            "make_platform: LANGFUSE_BASE_URL is set and disagrees with LANGFUSE_HOST -- "
            "refusing to construct a platform client that would silently split traffic "
            "and credentials across two hosts. Unset LANGFUSE_BASE_URL or make it match "
            "LANGFUSE_HOST."
        )

    client = UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)

    from langfuse import Langfuse  # local import: confine the SDK to this factory

    # base_url=host, belt-and-braces alongside the fail-closed check above:
    # passing it explicitly makes the SDK resolve to `host` regardless of
    # LANGFUSE_BASE_URL's env precedence, for the (already-refused-if-
    # disagreeing) case where it's unset or equal.
    sdk_client = Langfuse(host=host, base_url=host, public_key=public_key, secret_key=secret_key)
    return cast(
        PlatformAdapter,
        LangfuseAdapter(client=client, tracing_client=cast(ExperimentRunner, sdk_client)),
    )
