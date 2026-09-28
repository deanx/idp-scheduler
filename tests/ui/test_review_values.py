"""Tests for T-02.3.7: GET /api/reviews/{session_id}/values.

Covers:
- Batch fetch of all platform items in one round-trip (not O(N²))
- Correct mapping: expectedOutput → ReviewFieldValue per field, provenance tag
- 503 when the platform is not configured / unreachable (fetch_platform_items returns None)
- 404 for an unknown session_id
- INV-02: no field name or value appears in any log line emitted by this route
- Zero IDP quota spent (no subprocess invoked)
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="the `ui` extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from idp_regression.ui import api as api_module  # noqa: E402
from idp_regression.ui import workspace  # noqa: E402
from idp_regression.ui.api import create_app  # noqa: E402
from idp_regression.ui.review_sessions import (  # noqa: E402
    ReviewSession,
    ReviewSessionState,
    save_session,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        yield tmp_path
    finally:
        workspace.set_workspace(previous)


@pytest.fixture
def client(ws: Path) -> Iterator[TestClient]:
    yield TestClient(create_app(scorer_dir=ws / "scorers"))


def _make_session(
    ws: Path,
    *,
    session_id: str = "sess-values-001",
    state: ReviewSessionState = ReviewSessionState.DRAFTED,
    edited_document_ids: dict[str, list[str]] | None = None,
) -> ReviewSession:
    session = ReviewSession(
        session_id=session_id,
        dataset="test-dataset",
        org_id="org-001",
        action_id="act-001",
        trusted_version="1.0.0",
        candidate_version="2.0.0",
        document_dir=str(ws / "documents"),
        archive_sha256="aabb1234",
        approved_golden_hash=None,
        stage1_job_id="stage1-job-id",
        stage2_job_id=None,
        state=state,
        created_at="2026-09-28T00:00:00Z",
        edited_document_ids=edited_document_ids or {},
    )
    save_session(session, ws)
    return session


def _fake_items(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the shape fetch_platform_items returns: [{document_id, expected_output}]."""
    return [
        {
            "document_id": d["document_id"],
            "expected_output": d.get("expected_output"),
        }
        for d in docs
    ]


# ---------------------------------------------------------------------------
# T-02.3.7 — GET /api/reviews/{session_id}/values
# ---------------------------------------------------------------------------


class TestGetReviewValues:
    def test_returns_field_values_from_platform(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: endpoint returns one field-row per field, sourced from the platform."""
        _make_session(ws)
        fake_items = _fake_items([
            {
                "document_id": "doc-001",
                "expected_output": {
                    "fields": {
                        "invoice_total": {"value": "1250.00", "type": "number", "critical": True},
                        "invoice_date": {"value": "2024-06-28", "type": "date", "critical": False},
                    },
                },
            },
        ])
        monkeypatch.setattr(
            api_module, "fetch_platform_items", lambda dataset, workspace_path: fake_items
        )

        resp = client.get("/api/reviews/sess-values-001/values")

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "sess-values-001"
        assert body["platform_configured"] is True
        assert len(body["documents"]) == 1
        doc = body["documents"][0]
        assert doc["document_id"] == "doc-001"
        fields_by_name = {f["name"]: f for f in doc["fields"]}
        assert "invoice_total" in fields_by_name
        assert fields_by_name["invoice_total"]["value"] == "1250.00"
        assert fields_by_name["invoice_total"]["type"] == "number"
        assert fields_by_name["invoice_total"]["critical"] is True
        assert "invoice_date" in fields_by_name

    def test_provenance_drafted_when_not_edited(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fields not in edited_document_ids are tagged 'drafted'."""
        _make_session(ws, edited_document_ids={})
        monkeypatch.setattr(
            api_module,
            "fetch_platform_items",
            lambda dataset, workspace_path: _fake_items([
                {
                    "document_id": "doc-001",
                    "expected_output": {
                        "fields": {"total": {"value": "500", "type": "number", "critical": True}},
                    },
                },
            ]),
        )

        resp = client.get("/api/reviews/sess-values-001/values")
        doc = resp.json()["documents"][0]
        assert doc["fields"][0]["provenance"] == "drafted"

    def test_provenance_edited_when_field_in_edited_document_ids(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fields in edited_document_ids are tagged 'edited'."""
        _make_session(ws, edited_document_ids={"doc-001": ["total"]})
        monkeypatch.setattr(
            api_module,
            "fetch_platform_items",
            lambda dataset, workspace_path: _fake_items([
                {
                    "document_id": "doc-001",
                    "expected_output": {
                        "fields": {
                            "total": {"value": "999", "type": "number", "critical": True},
                            "vat": {"value": "0.20", "type": "number", "critical": False},
                        },
                    },
                },
            ]),
        )

        resp = client.get("/api/reviews/sess-values-001/values")
        doc = resp.json()["documents"][0]
        fields_by_name = {f["name"]: f for f in doc["fields"]}
        assert fields_by_name["total"]["provenance"] == "edited"
        assert fields_by_name["vat"]["provenance"] == "drafted"

    def test_503_when_platform_unreachable(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """503 when fetch_platform_items returns None (platform unconfigured/unreachable)."""
        _make_session(ws)
        monkeypatch.setattr(
            api_module, "fetch_platform_items", lambda dataset, workspace_path: None
        )

        resp = client.get("/api/reviews/sess-values-001/values")
        assert resp.status_code == 503

    def test_404_for_unknown_session(self, client: TestClient, ws: Path) -> None:
        """Returns 404 for a session_id that has no file on disk."""
        resp = client.get("/api/reviews/does-not-exist/values")
        assert resp.status_code == 404

    def test_one_platform_call_not_per_document(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch_platform_items is called exactly ONCE regardless of document count.

        This is the O(1) vs O(N) guarantee — not a per-document fan-out.
        """
        _make_session(ws)
        call_count = 0

        def counting_fetch(dataset: str, workspace_path: Path) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            # Return three documents to make the N>1 case meaningful.
            return _fake_items([
                {
                    "document_id": f"doc-{i:03d}",
                    "expected_output": {
                        "fields": {
                            "total": {"value": str(i * 100), "type": "number", "critical": True},
                        },
                    },
                }
                for i in range(3)
            ])

        monkeypatch.setattr(api_module, "fetch_platform_items", counting_fetch)

        resp = client.get("/api/reviews/sess-values-001/values")
        assert resp.status_code == 200
        assert call_count == 1, f"Expected exactly 1 platform call, got {call_count}"
        assert len(resp.json()["documents"]) == 3

    def test_inv02_no_field_value_in_logs(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """INV-02: no field name or field value appears in any log line from this route.

        The endpoint returns values to the loopback client (that is its purpose),
        but it must never log them — same discipline as every other route that
        touches golden data.
        """
        _make_session(ws)
        sentinel_value = "SENTINEL_FIELD_VALUE_1234"
        sentinel_field = "sentinel_field_name"

        monkeypatch.setattr(
            api_module,
            "fetch_platform_items",
            lambda dataset, workspace_path: _fake_items([
                {
                    "document_id": "doc-001",
                    "expected_output": {
                        "fields": {
                            sentinel_field: {
                                "value": sentinel_value,
                                "type": "text",
                                "critical": False,
                            },
                        },
                    },
                },
            ]),
        )

        with caplog.at_level(logging.DEBUG, logger="idp_regression.ui.api"):
            resp = client.get("/api/reviews/sess-values-001/values")

        assert resp.status_code == 200
        # The response body MUST contain the values (that is the endpoint's purpose).
        assert sentinel_value in resp.text
        # But no log line must contain either the field name or the field value.
        for record in caplog.records:
            assert sentinel_value not in record.getMessage(), (
                f"INV-02 violation: field value {sentinel_value!r} found in log: "
                f"{record.getMessage()!r}"
            )
            assert sentinel_field not in record.getMessage(), (
                f"INV-02 violation: field name {sentinel_field!r} found in log: "
                f"{record.getMessage()!r}"
            )

    def test_zero_quota_no_subprocess(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /values spends zero IDP quota — no subprocess is invoked."""
        _make_session(ws)
        monkeypatch.setattr(
            api_module,
            "fetch_platform_items",
            lambda dataset, workspace_path: _fake_items([
                {
                    "document_id": "doc-001",
                    "expected_output": {
                        "fields": {"total": {"value": "100", "type": "number", "critical": True}},
                    },
                },
            ]),
        )

        popen_calls: list[Any] = []
        original_popen = subprocess.Popen

        class TrackingPopen(original_popen):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                popen_calls.append(args)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", TrackingPopen)

        resp = client.get("/api/reviews/sess-values-001/values")
        assert resp.status_code == 200
        assert popen_calls == [], (
            "GET /values must spend zero quota — no subprocess should be started"
        )

    def test_multiple_documents_all_returned(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All documents from the platform batch are present in the response."""
        _make_session(ws)
        n_docs = 10
        monkeypatch.setattr(
            api_module,
            "fetch_platform_items",
            lambda dataset, workspace_path: _fake_items([
                {
                    "document_id": f"doc-{i:03d}",
                    "expected_output": {
                        "fields": {
                            "total": {"value": str(i * 10), "type": "number", "critical": True},
                        },
                    },
                }
                for i in range(n_docs)
            ]),
        )

        resp = client.get("/api/reviews/sess-values-001/values")
        assert resp.status_code == 200
        assert len(resp.json()["documents"]) == n_docs
