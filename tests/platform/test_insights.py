"""Reading the platform for dashboards.

Written against the facts `CLAUDE.md ## Langfuse facts that bite`
recorded live against 4.38.0 in `events_only` mode. Each test below
pins one of them, because each was learned the expensive way and each
has a failure mode that looks like success:

* the runs endpoint 404s -- a FACT about the deployment, not an error;
* `/v2/scores` is gone, v3 only;
* the dataset endpoint returns no items, and reading it instead of the
  paginated items endpoint yields a silently empty golden set that
  passes vacuously.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from idp_regression.platform.errors import TransportError
from idp_regression.platform.insights import MAX_LIMIT, LangfuseInsights, insights_from_env


class FakeHttp:
    def __init__(self, responses: dict[str, tuple[int, Any]]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.paths.append(path)
        for prefix, response in self.responses.items():
            if path.startswith(prefix):
                return response
        return 404, {"message": "not found"}


class TestCapabilities:
    def test_a_missing_runs_endpoint_is_a_capability_not_a_failure(self) -> None:
        """This deployment's 404 body says "not available in events_only
        mode". A dashboard that painted that red would teach people to
        ignore red."""
        http = FakeHttp({
            "/api/public/datasets/_probe_/runs": (
                404, {"message": "not available in events_only mode"}
            ),
            "/api/public/v3/scores": (200, {"data": []}),
        })
        caps = LangfuseInsights(http).capabilities()
        assert caps["reachable"] is True
        assert caps["scores_v3"] is True
        assert caps["runs_list_endpoint"] is False
        assert "events_only" in caps["runs_list_detail"]

    def test_an_unreachable_platform_is_reported_not_raised(self) -> None:
        class Dead:
            def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
                raise TransportError("connection refused")

        caps = LangfuseInsights(Dead()).capabilities()
        assert caps["reachable"] is False
        assert caps["scores_v3"] is False

    def test_it_states_that_it_is_not_a_gate_source(self) -> None:
        """INV-08: the gate is computed in process before any platform
        write. Nothing read here may ever be mistaken for it."""
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": []})})
        assert LangfuseInsights(http).capabilities()["is_gate_source"] is False


class TestScores:
    def test_it_reads_v3_and_never_v2(self) -> None:
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": [{"name": "gate"}]})})
        LangfuseInsights(http).scores()
        assert all("/v3/scores" in path for path in http.paths)
        assert not any("/v2/scores" in path for path in http.paths)

    def test_every_read_is_bounded_and_time_filtered(self) -> None:
        """An unbounded score query against a busy project is a slow
        request that times out the console and loads the platform's
        database for a chart nobody scrolled to."""
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": []})})
        LangfuseInsights(http).scores()
        assert "limit=" in http.paths[0]
        assert "fromTimestamp=" in http.paths[0]

    def test_an_absurd_limit_is_clamped(self) -> None:
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": []})})
        LangfuseInsights(http).scores(limit=10_000_000)
        assert f"limit={MAX_LIMIT}" in http.paths[0]

    def test_a_non_200_is_a_transport_error_not_an_empty_chart(self) -> None:
        """An empty chart reads as "no regressions"; a failed read must
        never render as that."""
        http = FakeHttp({"/api/public/v3/scores": (500, {"message": "boom"})})
        with pytest.raises(TransportError):
            LangfuseInsights(http).scores()


class TestTrend:
    def test_categorical_verdicts_are_bucketed_by_day(self) -> None:
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": [
            {"timestamp": "2026-09-24T10:00:00Z", "name": "field:total", "stringValue": "match"},
            {"timestamp": "2026-09-24T11:00:00Z", "name": "field:total", "stringValue": "match"},
            {"timestamp": "2026-09-25T09:00:00Z", "name": "gate", "stringValue": "FAIL"},
        ]})})
        trend = LangfuseInsights(http).score_trend()
        assert [point["bucket"] for point in trend["series"]] == ["2026-09-24", "2026-09-25"]
        assert trend["series"][0]["counts"] == {"match": 2}
        assert trend["score_names"] == {"field:total": 2, "gate": 1}

    def test_a_full_page_is_flagged_as_truncated(self) -> None:
        """Otherwise a chart implies it covers the whole window when it
        covers only the most recent page of it."""
        rows = [{"timestamp": "2026-09-25T00:00:00Z", "stringValue": "match"}] * 5
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": rows})})
        assert LangfuseInsights(http).score_trend(limit=5)["truncated"] is True

    def test_a_partial_page_is_not_flagged(self) -> None:
        http = FakeHttp({"/api/public/v3/scores": (200, {"data": [
            {"timestamp": "2026-09-25T00:00:00Z", "stringValue": "match"}
        ]})})
        assert LangfuseInsights(http).score_trend(limit=50)["truncated"] is False


class TestDatasetItems:
    def test_items_come_from_the_paginated_items_endpoint(self) -> None:
        """`GET /api/public/v2/datasets/{name}` does NOT return items.
        Reading that and finding none is how a golden set silently
        becomes empty and every run passes vacuously."""
        http = FakeHttp({"/api/public/dataset-items": (200, {
            "data": [{"id": "i1", "input": {"document_id": "inv-001.pdf"},
                      "metadata": {"trusted_action_version": "1.0.0"}}],
            "meta": {"totalItems": 1},
        })})
        result = LangfuseInsights(http).dataset_items("invoices-golden")
        assert "/api/public/dataset-items" in http.paths[0]
        assert "datasetName=invoices-golden" in http.paths[0]
        assert result["documents"][0]["document_id"] == "inv-001.pdf"
        assert result["total_items"] == 1

    def test_it_returns_identity_and_provenance_never_expected_values(self) -> None:
        """The console renders which documents a golden set covers and
        what pinned them. Pulling expected VALUES back out of the
        platform to display here is a disclosure this page does not
        need to make."""
        http = FakeHttp({"/api/public/dataset-items": (200, {"data": [{
            "id": "i1",
            "input": {"document_id": "inv-001.pdf"},
            "expectedOutput": {"fields": {"total": {"value": "1250.00"}}},
            "metadata": {"trusted_action_version": "1.0.0"},
        }]})})
        result = LangfuseInsights(http).dataset_items("d")
        assert set(result["documents"][0]) == {"id", "document_id", "metadata"}
        assert "1250.00" not in str(result)


class TestConfiguration:
    def test_an_unconfigured_platform_yields_none_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A console with no platform credentials is a perfectly good
        local console; every other page must keep working."""
        for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(key, raising=False)
        assert insights_from_env() is None

    def test_a_blank_credential_counts_as_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "   ")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        assert insights_from_env() is None

    def test_an_injected_client_is_used_as_given(self) -> None:
        http = FakeHttp({})
        assert isinstance(insights_from_env(http), LangfuseInsights)


def test_reads_are_documented_as_history_never_read_after_write() -> None:
    """Public reads lag on this deployment, and the case that breaks is
    read-after-write specifically. Nothing here is used to confirm a
    write landed -- pinned as a doc assertion so a future caller reaching
    for this module to verify a write has to delete the sentence first.
    """
    import inspect

    from idp_regression.platform import insights

    assert "read-after-write" in (inspect.getdoc(insights) or "")


def test_a_naive_since_is_still_sent_as_utc() -> None:
    """A timestamp filter that silently shifted by the host's offset
    would quietly change which window a chart covers."""
    http = FakeHttp({"/api/public/v3/scores": (200, {"data": []})})
    LangfuseInsights(http).scores(since=dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
    assert "2026-09-01T00%3A00%3A00Z" in http.paths[0]
