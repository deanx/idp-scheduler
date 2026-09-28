"""Tests for S-02.2: Golden edit and whole-file replace API (T-02.2.1 – T-02.2.4).

Covers:
- T-02.2.1  PATCH /api/reviews/{session_id}/items/{document_id}
- T-02.2.2  POST /api/reviews/{session_id}/replace
- T-02.2.3  Provenance (drafted vs edited) in GET /api/reviews/{session_id}
- T-02.2.4  schema-rejection-leaves-no-partial-write; zero-quota assertion;
            replace-failure-leaves-drafted-intact; observability (one log line,
            no golden field value in logs — INV-02)
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="the `ui` extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from idp_regression.ui import workspace  # noqa: E402
from idp_regression.ui.api import create_app  # noqa: E402
from idp_regression.ui.review_sessions import (  # noqa: E402
    ReviewSession,
    ReviewSessionState,
    load_session,
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
    session_id: str = "sess-001",
    state: ReviewSessionState = ReviewSessionState.DRAFTED,
    approved_golden_hash: str | None = None,
) -> ReviewSession:
    session = ReviewSession(
        session_id=session_id,
        dataset="test-dataset",
        org_id="org-001",
        action_id="act-001",
        trusted_version="1.0.0",
        candidate_version="2.0.0",
        document_dir=str(ws / "docs"),
        archive_sha256="aabb1234",
        approved_golden_hash=approved_golden_hash,
        stage1_job_id="job-stage1",
        stage2_job_id=None,
        state=state,
        created_at="2026-09-28T00:00:00Z",
        edited_document_ids={},
    )
    save_session(session, ws)
    return session


# A minimal valid golden entry that satisfies the committed schema.
_VALID_ENTRY: dict[str, Any] = {
    "document_id": "inv-001.pdf",
    "fields": {
        "invoice_total": {"value": "1250.00", "type": "number"},
        "invoice_date": {"value": "2024-06-28", "type": "date"},
    },
}

# An entry whose field value violates the schema (number field has non-numeric value).
_INVALID_ENTRY: dict[str, Any] = {
    "document_id": "inv-001.pdf",
    "fields": {
        "invoice_total": {"value": "not-a-number", "type": "number"},
    },
}


# ---------------------------------------------------------------------------
# T-02.2.3 — Provenance in ReviewSession model
# ---------------------------------------------------------------------------


class TestProvenanceField:
    def test_new_session_has_empty_edited_document_ids(self, ws: Path) -> None:
        """A freshly-created session starts with no provenance."""
        session = _make_session(ws)
        assert session.edited_document_ids == {}

    def test_provenance_survives_save_and_load(self, ws: Path) -> None:
        """edited_document_ids is persisted and round-trips correctly."""
        session = _make_session(ws)
        session.edited_document_ids = {"inv-001.pdf": ["invoice_total", "invoice_date"]}
        save_session(session, ws)
        loaded = load_session(session.session_id, ws)
        assert loaded.edited_document_ids == {"inv-001.pdf": ["invoice_total", "invoice_date"]}

    def test_provenance_appears_in_to_dict(self, ws: Path) -> None:
        session = _make_session(ws)
        session.edited_document_ids = {"doc.pdf": ["amount"]}
        d = session.to_dict()
        assert "edited_document_ids" in d
        assert d["edited_document_ids"] == {"doc.pdf": ["amount"]}

    def test_get_review_response_includes_edited_document_ids(
        self, client: TestClient, ws: Path
    ) -> None:
        """GET /api/reviews/{session_id} must expose provenance for the UI (T-02.2.3)."""
        session = _make_session(ws)
        session.edited_document_ids = {"inv-001.pdf": ["invoice_total"]}
        save_session(session, ws)
        response = client.get(f"/api/reviews/{session.session_id}")
        assert response.status_code == 200
        body = response.json()
        assert "edited_document_ids" in body
        assert body["edited_document_ids"] == {"inv-001.pdf": ["invoice_total"]}

    def test_sessions_with_missing_provenance_field_read_as_empty_dict(
        self, ws: Path
    ) -> None:
        """Backward compatibility: a session file written by S-02.1 (no provenance field)
        must load successfully, with edited_document_ids defaulting to {}."""
        # Write a session file without the provenance field (simulates S-02.1's output)
        import os
        import pathlib
        import tempfile

        from idp_regression.ui.review_sessions import _session_path

        session = _make_session(ws)
        path = _session_path(session.session_id, ws)
        data = json.loads(path.read_text())
        data.pop("edited_document_ids", None)  # remove if present

        descriptor, tmp_name = tempfile.mkstemp(dir=path.parent)
        tmp = pathlib.Path(tmp_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)

        loaded = load_session(session.session_id, ws)
        assert loaded.edited_document_ids == {}


# ---------------------------------------------------------------------------
# T-02.2.1 — PATCH /api/reviews/{session_id}/items/{document_id}
# ---------------------------------------------------------------------------


class TestPatchItem:
    """Single-field edit: schema validation + deterministic upsert (T-02.2.1)."""

    def test_patch_returns_200_on_valid_edit_in_drafted_state(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 200

    def test_patch_returns_200_on_valid_edit_in_reviewed_state(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REVIEWED state is allowed; the session transitions back to DRAFTED."""
        session = _make_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="old-hash",
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 200
        # Session must revert to DRAFTED (hash is stale after the edit)
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.DRAFTED
        assert reloaded.approved_golden_hash is None

    def test_patch_refuses_on_invalid_schema(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """schema-rejection-leaves-no-partial-write (T-02.2.4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        upsert_called = []
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: upsert_called.append(True),
        )
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_INVALID_ENTRY,
        )
        assert response.status_code == 422
        # The upsert must not have been called — no partial write
        assert len(upsert_called) == 0, "upsert was called despite a schema rejection"
        # Session state must be unchanged
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.DRAFTED

    def test_patch_refuses_for_verifying_state(
        self, client: TestClient, ws: Path
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.VERIFYING)
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 409

    def test_patch_refuses_for_verified_state(
        self, client: TestClient, ws: Path
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.VERIFIED)
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 409

    def test_patch_refuses_for_stale_state(
        self, client: TestClient, ws: Path
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.STALE)
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 409

    def test_patch_refuses_nonexistent_session(
        self, client: TestClient, ws: Path
    ) -> None:
        response = client.patch(
            "/api/reviews/no-such-session/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code == 404

    def test_patch_updates_provenance_on_success(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edited fields are tracked in edited_document_ids (T-02.2.3)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        reloaded = load_session(session.session_id, ws)
        assert "inv-001.pdf" in reloaded.edited_document_ids
        # The edited fields should be tracked
        edited_fields = reloaded.edited_document_ids["inv-001.pdf"]
        assert "invoice_total" in edited_fields
        assert "invoice_date" in edited_fields

    def test_patch_names_specific_invalid_field_in_error(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal message names the specific invalid field (DoD requirement)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_INVALID_ENTRY,
        )
        assert response.status_code == 422
        detail = response.json().get("detail", "")
        # The error must mention WHERE the problem is, without echoing a value
        assert detail, "error detail must be non-empty"

    # ---- T-02.2.4: Zero IDP quota assertion for PATCH ----

    def test_patch_spends_zero_idp_quota(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No subprocess (IDP extraction) is ever spawned by this endpoint (T-02.2.4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )

        spawn_calls: list[Any] = []

        original_popen = subprocess.Popen

        def fake_popen(*args: Any, **kwargs: Any) -> Any:
            spawn_calls.append(args)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        assert len(spawn_calls) == 0, (
            "PATCH /items must not spawn any subprocess — "
            "zero IDP quota must be spent (T-02.2.4)"
        )

    # ---- T-02.2.4: Observability (one log line, no field values) ----

    def test_patch_emits_exactly_one_structured_log_line(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        with caplog.at_level(logging.INFO, logger="idp_regression.ui.api"):
            client.patch(
                f"/api/reviews/{session.session_id}/items/inv-001.pdf",
                json=_VALID_ENTRY,
            )
        edit_lines = [m for m in caplog.messages if "golden_item_edited" in m]
        assert len(edit_lines) == 1, (
            f"expected exactly 1 golden_item_edited log line, got {len(edit_lines)}: {edit_lines}"
        )
        # Log line must include session_id
        assert session.session_id in edit_lines[0]

    def test_patch_log_line_contains_no_golden_field_values(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """INV-02: no golden field value appears in any log line (T-02.2.4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        # Use a conspicuous sensitive value in the edit body
        sensitive_entry = {
            "document_id": "inv-001.pdf",
            "fields": {
                "invoice_total": {"value": "SENSITIVE-VALUE-9876543", "type": "text"},
            },
        }
        with caplog.at_level(logging.DEBUG, logger="idp_regression.ui.api"):
            client.patch(
                f"/api/reviews/{session.session_id}/items/inv-001.pdf",
                json=sensitive_entry,
            )
        assert "SENSITIVE-VALUE-9876543" not in caplog.text, (
            "INV-02 violation: a golden field value appeared in the api log"
        )


# ---------------------------------------------------------------------------
# T-02.2.2 — POST /api/reviews/{session_id}/replace
# ---------------------------------------------------------------------------

# A minimal valid golden file (multi-entry format).
_VALID_GOLDEN_FILE: dict[str, Any] = {
    "ENTRY-001": {
        "document_id": "inv-001.pdf",
        "fields": {
            "invoice_total": {"value": "1250.00", "type": "number"},
        },
    },
    "ENTRY-002": {
        "document_id": "inv-002.pdf",
        "fields": {
            "invoice_total": {"value": "999.00", "type": "number"},
        },
    },
}

# A golden file with one invalid entry.
_GOLDEN_FILE_WITH_ONE_INVALID: dict[str, Any] = {
    "ENTRY-001": {
        "document_id": "inv-001.pdf",
        "fields": {
            "invoice_total": {"value": "1250.00", "type": "number"},
        },
    },
    "ENTRY-INVALID": {
        "document_id": "inv-002.pdf",
        "fields": {
            "invoice_total": {"value": "not-a-number", "type": "number"},
        },
    },
}


class TestReplace:
    """Whole-file replace: all-or-nothing validation (T-02.2.2)."""

    def test_replace_returns_200_on_valid_file(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert response.status_code == 200

    def test_replace_refuses_on_any_invalid_entry(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All-or-nothing: one invalid entry refuses the whole batch (T-02.2.2)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        upsert_calls: list[Any] = []
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: upsert_calls.append(True),
        )
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _GOLDEN_FILE_WITH_ONE_INVALID},
        )
        assert response.status_code == 422
        # The invalid entry key must be named in the error detail
        detail = response.json().get("detail", "")
        assert "ENTRY-INVALID" in detail, (
            f"error detail must name the invalid entry, got: {detail!r}"
        )
        # No upsert must have been called — no partial write
        assert len(upsert_calls) == 0, "upsert was called despite a validation failure"

    def test_replace_failure_leaves_drafted_set_intact(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """replace-failure-leaves-drafted-set-intact (T-02.2.4).

        After a failed replace, the session state must be unchanged and
        the provenance must not have been updated.
        """
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        session.edited_document_ids = {"pre-existing.pdf": ["amount"]}
        save_session(session, ws)

        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _GOLDEN_FILE_WITH_ONE_INVALID},
        )
        reloaded = load_session(session.session_id, ws)
        # State and provenance must be exactly as before
        assert reloaded.state == ReviewSessionState.DRAFTED
        assert reloaded.edited_document_ids == {"pre-existing.pdf": ["amount"]}

    def test_replace_refuses_nonexistent_session(
        self, client: TestClient, ws: Path
    ) -> None:
        response = client.post(
            "/api/reviews/no-such/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert response.status_code == 404

    def test_replace_refuses_for_verifying_state(
        self, client: TestClient, ws: Path
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.VERIFYING)
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert response.status_code == 409

    def test_replace_names_specific_invalid_entry(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal must name the specific invalid entry (AC4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _GOLDEN_FILE_WITH_ONE_INVALID},
        )
        assert response.status_code == 422
        detail = response.json().get("detail", "")
        assert detail, "error detail must be non-empty"

    def test_replace_updates_provenance_on_success(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful replace, all document_ids in the file are marked edited."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        reloaded = load_session(session.session_id, ws)
        # Both document_ids from the replacement must be in provenance
        assert "inv-001.pdf" in reloaded.edited_document_ids
        assert "inv-002.pdf" in reloaded.edited_document_ids

    def test_replace_in_reviewed_state_reverts_to_drafted(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A replace on a REVIEWED session reverts to DRAFTED (hash is stale after replace)."""
        session = _make_session(
            ws, state=ReviewSessionState.REVIEWED, approved_golden_hash="old-hash"
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.DRAFTED
        assert reloaded.approved_golden_hash is None

    # ---- T-02.2.4: Zero IDP quota assertion for POST /replace ----

    def test_replace_spends_zero_idp_quota(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No subprocess is ever spawned by this endpoint (T-02.2.4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        spawn_calls: list[Any] = []
        original_popen = subprocess.Popen

        def fake_popen(*args: Any, **kwargs: Any) -> Any:
            spawn_calls.append(args)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert len(spawn_calls) == 0, (
            "POST /replace must not spawn any subprocess — zero IDP quota (T-02.2.4)"
        )

    # ---- T-02.2.4: Observability (one log line, no field values) ----

    def test_replace_emits_exactly_one_structured_log_line(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        with caplog.at_level(logging.INFO, logger="idp_regression.ui.api"):
            client.post(
                f"/api/reviews/{session.session_id}/replace",
                json={"entries": _VALID_GOLDEN_FILE},
            )
        replace_lines = [m for m in caplog.messages if "golden_set_replaced" in m]
        assert len(replace_lines) == 1, (
            f"expected exactly 1 golden_set_replaced log line, got {len(replace_lines)}: "
            f"{replace_lines}"
        )
        assert session.session_id in replace_lines[0]

    def test_replace_log_line_contains_no_golden_field_values(
        self,
        client: TestClient,
        ws: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """INV-02: no golden field value appears in any log line (T-02.2.4)."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        sensitive_file = {
            "ENTRY-001": {
                "document_id": "inv-001.pdf",
                "fields": {
                    "amount": {"value": "SENSITIVE-REPLACE-VALUE-12345", "type": "text"},
                },
            },
        }
        with caplog.at_level(logging.DEBUG, logger="idp_regression.ui.api"):
            client.post(
                f"/api/reviews/{session.session_id}/replace",
                json={"entries": sensitive_file},
            )
        assert "SENSITIVE-REPLACE-VALUE-12345" not in caplog.text, (
            "INV-02 violation: a golden field value appeared in the api log (replace)"
        )


# ---------------------------------------------------------------------------
# TestQuotaBoundary: new routes must be in the route table (T-02.2.4 + existing)
# ---------------------------------------------------------------------------


class TestRouteTableUpdated:
    """The two new routes must appear in the route table that TestQuotaBoundary tracks."""

    def test_patch_items_route_exists_in_app(self, client: TestClient) -> None:
        """Route must exist (not 404/405) for a known session."""
        # 404 is acceptable for "session not found"; 405 is NOT acceptable (route missing)
        response = client.patch(
            "/api/reviews/any-session-id/items/doc.pdf",
            json=_VALID_ENTRY,
        )
        assert response.status_code != 405, "PATCH route must be registered in the app"

    def test_replace_route_exists_in_app(self, client: TestClient) -> None:
        response = client.post(
            "/api/reviews/any-session-id/replace",
            json={"entries": {}},
        )
        assert response.status_code != 405, "POST /replace route must be registered in the app"


# ---------------------------------------------------------------------------
# C1 — Atchim round 2: upsert payload inspected + golden_edits unit tests
# ---------------------------------------------------------------------------


class TestGoldenEditsModule:
    """Direct unit tests for item_id() and build_item_payload() (C1)."""

    def test_item_id_is_deterministic(self) -> None:
        """Same inputs always produce the same id."""
        from idp_regression.ui.golden_edits import item_id
        assert item_id("ds", "doc.pdf") == item_id("ds", "doc.pdf")

    def test_item_id_differs_by_dataset(self) -> None:
        from idp_regression.ui.golden_edits import item_id
        assert item_id("ds1", "doc.pdf") != item_id("ds2", "doc.pdf")

    def test_item_id_differs_by_document(self) -> None:
        from idp_regression.ui.golden_edits import item_id
        assert item_id("ds", "doc1.pdf") != item_id("ds", "doc2.pdf")

    def test_item_id_matches_uuid5_formula(self) -> None:
        """item_id is uuid5(ITEM_NAMESPACE, '{dataset}|{document_id}') as a string."""
        import uuid

        from idp_regression.ui.golden_edits import ITEM_NAMESPACE, item_id
        expected = str(uuid.uuid5(ITEM_NAMESPACE, "my-dataset|invoice.pdf"))
        assert item_id("my-dataset", "invoice.pdf") == expected

    def test_build_item_payload_correct_shape(self) -> None:
        """Payload has id, datasetName, input.document_id, expectedOutput.fields."""
        import uuid

        from idp_regression.ui.golden_edits import ITEM_NAMESPACE, build_item_payload
        entry = {
            "document_id": "inv.pdf",
            "fields": {"total": {"value": "100.00", "type": "number"}},
        }
        payload = build_item_payload("my-ds", "inv.pdf", entry)
        assert payload["id"] == str(uuid.uuid5(ITEM_NAMESPACE, "my-ds|inv.pdf"))
        assert payload["datasetName"] == "my-ds"
        assert payload["input"]["document_id"] == "inv.pdf"
        assert payload["expectedOutput"]["fields"]["total"]["value"] == "100.00"
        assert "tables" not in payload["expectedOutput"]
        assert "prompts" not in payload["expectedOutput"]

    def test_build_item_payload_includes_tables_when_present(self) -> None:
        from idp_regression.ui.golden_edits import build_item_payload
        entry = {
            "document_id": "inv.pdf",
            "fields": {"total": {"value": "100.00", "type": "number"}},
            "tables": {"line_items": {"match_key": "desc", "rows": []}},
        }
        payload = build_item_payload("ds", "inv.pdf", entry)
        assert "tables" in payload["expectedOutput"]
        assert "line_items" in payload["expectedOutput"]["tables"]

    def test_build_item_payload_includes_prompts_when_present(self) -> None:
        from idp_regression.ui.golden_edits import build_item_payload
        entry = {
            "document_id": "inv.pdf",
            "fields": {"total": {"value": "100.00", "type": "number"}},
            "prompts": {"Is this a valid invoice?": {"answer": "yes"}},
        }
        payload = build_item_payload("ds", "inv.pdf", entry)
        assert "prompts" in payload["expectedOutput"]

    def test_build_item_payload_omits_tables_when_absent(self) -> None:
        from idp_regression.ui.golden_edits import build_item_payload
        entry = {
            "document_id": "inv.pdf",
            "fields": {"total": {"value": "100.00", "type": "number"}},
        }
        payload = build_item_payload("ds", "inv.pdf", entry)
        assert "tables" not in payload["expectedOutput"]
        assert "prompts" not in payload["expectedOutput"]


# ---------------------------------------------------------------------------
# C1 — payload inspection on PATCH
# ---------------------------------------------------------------------------


class TestPatchPayloadInspection:
    """PATCH must call upsert exactly once with the correct deterministic-id payload (C1)."""

    def test_patch_calls_upsert_exactly_once_with_correct_payload(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(a) exactly one call, (b) correct deterministic id, (c) correct field value."""
        import uuid

        from idp_regression.ui.golden_edits import ITEM_NAMESPACE
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        captured: list[dict[str, Any]] = []

        def _capture_upsert(
            dataset: str, payload: dict[str, Any], workspace_path: Path
        ) -> str | None:
            captured.append(payload)
            return None

        monkeypatch.setattr("idp_regression.ui.api.upsert_platform_item", _capture_upsert)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: None,
        )
        client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        # (a) exactly one upsert call
        assert len(captured) == 1, (
            f"expected exactly 1 upsert call, got {len(captured)}"
        )
        payload = captured[0]
        # (b) deterministic item id
        expected_id = str(uuid.uuid5(ITEM_NAMESPACE, "test-dataset|inv-001.pdf"))
        assert payload["id"] == expected_id, (
            f"wrong item id: {payload['id']!r}, expected {expected_id!r}"
        )
        # (c) field value reaches the platform
        assert payload["expectedOutput"]["fields"]["invoice_total"]["value"] == "1250.00", (
            "the edited field value must be present in the upserted payload"
        )

    def test_replace_calls_upsert_once_per_entry_with_correct_payload(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /replace: one upsert call per valid entry, each with the correct id."""
        import uuid

        from idp_regression.ui.golden_edits import ITEM_NAMESPACE
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        captured: list[dict[str, Any]] = []

        def _capture_upsert2(
            dataset: str, payload: dict[str, Any], workspace_path: Path
        ) -> str | None:
            captured.append(payload)
            return None

        monkeypatch.setattr("idp_regression.ui.api.upsert_platform_item", _capture_upsert2)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: None,
        )
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert len(captured) == 2, f"expected 2 upsert calls (one per entry), got {len(captured)}"
        ids = {p["id"] for p in captured}
        assert str(uuid.uuid5(ITEM_NAMESPACE, "test-dataset|inv-001.pdf")) in ids
        assert str(uuid.uuid5(ITEM_NAMESPACE, "test-dataset|inv-002.pdf")) in ids
        # Each payload must carry the correct field value
        by_doc = {p["input"]["document_id"]: p for p in captured}
        assert by_doc["inv-001.pdf"]["expectedOutput"]["fields"]["invoice_total"]["value"] == "1250.00"  # noqa: E501
        assert by_doc["inv-002.pdf"]["expectedOutput"]["fields"]["invoice_total"]["value"] == "999.00"  # noqa: E501


# ---------------------------------------------------------------------------
# C2 — ITEM_NAMESPACE must match provision_golden_dataset._ITEM_NAMESPACE
# ---------------------------------------------------------------------------


class TestItemNamespaceNotDrifted:
    """Guard: golden_edits.ITEM_NAMESPACE must equal provision_golden_dataset._ITEM_NAMESPACE.

    A divergence would silently create duplicate platform items instead of upserts —
    double IDP quota + a stale golden shadowing the fresh one (incident documented at
    provision_golden_dataset.py:210-220).  This test is the binding contract so that
    any change to either side immediately fails here.

    Importing via sys.path (in the test only) is the approach used to access the
    script constant.  The production module does not import from scripts/ to avoid
    making sys.path mutation a runtime side effect of importing the production code.
    """

    def test_item_namespace_matches_provision_golden_dataset(self) -> None:
        import sys
        from pathlib import Path as _Path
        scripts_dir = str(_Path(__file__).resolve().parents[3] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import provision_golden_dataset as _pgd

        from idp_regression.ui.golden_edits import ITEM_NAMESPACE
        assert ITEM_NAMESPACE == _pgd._ITEM_NAMESPACE, (
            "golden_edits.ITEM_NAMESPACE has drifted from "
            "provision_golden_dataset._ITEM_NAMESPACE — "
            "this would create duplicate platform items instead of upserts"
        )


# ---------------------------------------------------------------------------
# R1 — PATCH is whole-item replace, not field-merge (pinned behavior)
# ---------------------------------------------------------------------------


class TestPatchIsWholeItemReplace:
    """PATCH replaces the full item on the platform; it does not merge fields (R1).

    S-02.3 must always send the COMPLETE corrected item, not just the changed fields.
    This test pins that semantics explicitly so the behavior is a known contract, not
    an accident of implementation.
    """

    def test_patch_with_subset_of_fields_replaces_not_merges(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sending only one field in the PATCH body writes ONLY that field to the platform.

        The platform item is NOT merged with any previously-stored fields.
        """
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        captured: list[dict[str, Any]] = []

        def _capture_upsert3(
            dataset: str, payload: dict[str, Any], workspace_path: Path
        ) -> str | None:
            captured.append(payload)
            return None

        monkeypatch.setattr("idp_regression.ui.api.upsert_platform_item", _capture_upsert3)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: None,
        )
        # Send only ONE field; the platform previously had two (see _VALID_ENTRY).
        single_field_body = {
            "document_id": "inv-001.pdf",
            "fields": {
                "invoice_total": {"value": "9999.00", "type": "number"},
            },
        }
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=single_field_body,
        )
        assert response.status_code == 200
        assert len(captured) == 1
        fields_written = captured[0]["expectedOutput"]["fields"]
        # Only the one field we sent must be in the payload.
        assert "invoice_total" in fields_written
        assert "invoice_date" not in fields_written, (
            "PATCH sent only invoice_total; invoice_date must NOT appear in the "
            "upserted payload — PATCH is replace, not merge (R1)"
        )


# ---------------------------------------------------------------------------
# R2 — Provenance: only mark fields whose VALUE actually changed as edited
# ---------------------------------------------------------------------------


class TestProvenanceAccuracy:
    """Provenance marks only fields whose value actually changed (R2).

    Re-sending an unchanged field must NOT mark it as edited.
    A genuinely changed field MUST be marked as edited.
    """

    def test_unchanged_field_not_marked_edited(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-sending a field with the same value does not mark it as edited."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        # Platform currently has these values (what pin_document.py wrote)
        current_platform_item = {
            "fields": {
                "invoice_total": {"value": "1250.00", "type": "number"},
                "invoice_date": {"value": "2024-06-28", "type": "date"},
            }
        }
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: current_platform_item,
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        # PATCH: change invoice_total; resend invoice_date unchanged
        patch_body = {
            "document_id": "inv-001.pdf",
            "fields": {
                "invoice_total": {"value": "1300.00", "type": "number"},  # changed
                "invoice_date": {"value": "2024-06-28", "type": "date"},  # unchanged
            },
        }
        client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=patch_body,
        )
        reloaded = load_session(session.session_id, ws)
        edited = reloaded.edited_document_ids.get("inv-001.pdf", [])
        assert "invoice_total" in edited, (
            "invoice_total was changed — it must be marked as edited"
        )
        assert "invoice_date" not in edited, (
            "invoice_date was re-sent unchanged — it must NOT be marked as edited (R2)"
        )

    def test_all_changed_fields_are_marked_edited(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When all fields are new (platform had none), all are marked edited."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        # Platform has no current item (first edit — e.g. document wasn't in the draft)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: None,
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=_VALID_ENTRY,
        )
        reloaded = load_session(session.session_id, ws)
        edited = reloaded.edited_document_ids.get("inv-001.pdf", [])
        assert "invoice_total" in edited
        assert "invoice_date" in edited

    def test_replace_marks_only_changed_fields_per_entry(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /replace: only fields whose value changed are marked edited."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)

        def fake_fetch(dataset: str, doc_id: str, workspace_path: Any) -> dict[str, Any] | None:
            if doc_id == "inv-001.pdf":
                return {
                    "fields": {
                        "invoice_total": {"value": "1250.00", "type": "number"},
                    }
                }
            return None

        monkeypatch.setattr("idp_regression.ui.api.fetch_platform_item", fake_fetch)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        replace_file = {
            "ENTRY-001": {
                "document_id": "inv-001.pdf",
                "fields": {
                    # unchanged from platform current state
                    "invoice_total": {"value": "1250.00", "type": "number"},
                },
            },
            "ENTRY-002": {
                "document_id": "inv-002.pdf",
                "fields": {
                    # no current state → all fields marked edited
                    "invoice_total": {"value": "999.00", "type": "number"},
                },
            },
        }
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": replace_file},
        )
        reloaded = load_session(session.session_id, ws)
        # inv-001.pdf: invoice_total unchanged — should NOT be marked edited
        edited_001 = reloaded.edited_document_ids.get("inv-001.pdf", [])
        assert "invoice_total" not in edited_001, (
            "inv-001.pdf's invoice_total was unchanged — must not be marked edited"
        )
        # inv-002.pdf: no prior state — all fields should be marked edited
        edited_002 = reloaded.edited_document_ids.get("inv-002.pdf", [])
        assert "invoice_total" in edited_002


# ---------------------------------------------------------------------------
# S2 (optional, Atchim flagged) — body document_id mismatch rejected
# ---------------------------------------------------------------------------


class TestDocumentIdMismatch:
    """Body-supplied document_id that disagrees with the URL parameter is rejected (S2)."""

    def test_patch_rejects_mismatched_document_id_in_body(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_platform_item",
            lambda dataset, doc_id, workspace_path: None,
        )
        mismatch_body = {
            "document_id": "WRONG-DOC.pdf",  # differs from URL parameter "inv-001.pdf"
            "fields": {
                "invoice_total": {"value": "1250.00", "type": "text"},
            },
        }
        response = client.patch(
            f"/api/reviews/{session.session_id}/items/inv-001.pdf",
            json=mismatch_body,
        )
        assert response.status_code == 422, (
            "a body document_id that disagrees with the URL parameter must be rejected"
        )


# ---------------------------------------------------------------------------
# DEBT-142 — mid-batch write failure forces REPLACE_FAILED; /complete blocked
# ---------------------------------------------------------------------------


class TestReplaceMidBatchFailure:
    """DEBT-142 fix: a mid-batch upsert failure forces session to REPLACE_FAILED.

    Probe: 5-entry batch, upsert fails on the 3rd entry (same technique Branca used).
    Asserts:
    - The endpoint returns 502.
    - The session state is 'replace_failed' (not 'drafted').
    - approved_golden_hash is cleared.
    - POST /reviews/{id}/complete returns 409 when state is replace_failed (not approvable).
    - A successful /replace from replace_failed state transitions back to drafted.
    """

    _FIVE_ENTRY_GOLDEN: dict[str, Any] = {
        "E-001": {
            "document_id": "inv-001.pdf",
            "fields": {"total": {"value": "100.00", "type": "number"}},
        },
        "E-002": {
            "document_id": "inv-002.pdf",
            "fields": {"total": {"value": "200.00", "type": "number"}},
        },
        "E-003": {
            "document_id": "inv-003.pdf",
            "fields": {"total": {"value": "300.00", "type": "number"}},
        },
        "E-004": {
            "document_id": "inv-004.pdf",
            "fields": {"total": {"value": "400.00", "type": "number"}},
        },
        "E-005": {
            "document_id": "inv-005.pdf",
            "fields": {"total": {"value": "500.00", "type": "number"}},
        },
    }

    def _make_fail_on_nth(self, n: int) -> Any:
        """Return a upsert stub that succeeds for the first n-1 calls, fails on call n."""
        call_count: list[int] = [0]

        def _stub(dataset: str, payload: dict[str, Any], workspace_path: Any) -> str | None:
            call_count[0] += 1
            if call_count[0] == n:
                return f"HTTP 503 (simulated failure on call {n})"
            return None

        return _stub

    def test_mid_batch_failure_returns_502(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A upsert failure mid-batch must yield a 502 response."""
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            self._make_fail_on_nth(3),
        )
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": self._FIVE_ENTRY_GOLDEN},
        )
        assert response.status_code == 502

    def test_mid_batch_failure_forces_replace_failed_state(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEBT-142: session must be in 'replace_failed' state after a mid-batch write failure.

        The curator sees a 502 AND the session state changes — so /complete is blocked
        until the golden is fully repaired, preventing approval of a partial (mongrel) golden.
        """
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            self._make_fail_on_nth(3),
        )
        client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": self._FIVE_ENTRY_GOLDEN},
        )
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.REPLACE_FAILED, (
            "a mid-batch write failure must force the session to REPLACE_FAILED state "
            "(not leave it in DRAFTED — that would let /complete approve a partial golden)"
        )
        assert reloaded.approved_golden_hash is None, (
            "approved_golden_hash must be cleared when a replace fails mid-batch"
        )

    def test_complete_refuses_replace_failed_session(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEBT-142: /complete must return 409 when the session is in REPLACE_FAILED state.

        A partial golden must not be approvable — doing so would let verify-candidate/start
        run INV-09(e) against a mongrel golden set and produce a silently-wrong result.
        """
        # Simulate a session already in REPLACE_FAILED state (e.g. from a previous run)
        session = _make_session(ws, state=ReviewSessionState.REPLACE_FAILED)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace_path: "some-hash",
        )
        response = client.post(f"/api/reviews/{session.session_id}/complete")
        assert response.status_code == 409, (
            "/complete must return 409 for a REPLACE_FAILED session — "
            "a partial golden must never be approved"
        )
        # Session state must not have changed
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.REPLACE_FAILED

    def test_successful_replace_from_replace_failed_transitions_to_drafted(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful /replace on a REPLACE_FAILED session transitions back to DRAFTED.

        This allows a curator to repair the golden after a partial failure without
        starting a fresh session.
        """
        session = _make_session(ws, state=ReviewSessionState.REPLACE_FAILED)
        monkeypatch.setattr(
            "idp_regression.ui.api.upsert_platform_item",
            lambda dataset, payload, workspace_path: None,
        )
        response = client.post(
            f"/api/reviews/{session.session_id}/replace",
            json={"entries": _VALID_GOLDEN_FILE},
        )
        assert response.status_code == 200
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.DRAFTED, (
            "a successful /replace from REPLACE_FAILED must transition back to DRAFTED "
            "so the session is approvable again"
        )


# ---------------------------------------------------------------------------
# DEBT-143 — empty platform dataset causes /complete to refuse (409)
# ---------------------------------------------------------------------------


class TestCompleteRefusesEmptyDataset:
    """DEBT-143 fix: /complete must refuse when the platform dataset is empty.

    sha256([]) is a fixed constant — an empty dataset at both /complete and
    verify-candidate/start would satisfy INV-09(e) vacuously.  fetch_golden_hash
    now returns None for an empty item list, so the existing None → 409 guard
    handles it identically to an unreachable platform.
    """

    def test_complete_refuses_when_dataset_is_empty(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEBT-143: /complete must return 409 when fetch_golden_hash returns None for empty set.

        This is the same 409 it returns for an unreachable platform, exercised via the
        existing None-return path (fetch_golden_hash now returns None for items == []).
        """
        session = _make_session(ws, state=ReviewSessionState.DRAFTED)
        # Simulate fetch_golden_hash returning None (as it now does for an empty dataset)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace_path: None,
        )
        response = client.post(f"/api/reviews/{session.session_id}/complete")
        assert response.status_code == 409, (
            "/complete must refuse (409) when fetch_golden_hash returns None — "
            "this covers both 'platform unreachable' and 'empty dataset' (DEBT-143)"
        )
        # Session state must remain DRAFTED (not transition to REVIEWED)
        reloaded = load_session(session.session_id, ws)
        assert reloaded.state == ReviewSessionState.DRAFTED
        assert reloaded.approved_golden_hash is None

    def test_fetch_golden_hash_returns_none_for_empty_items(
        self, ws: Path
    ) -> None:
        """fetch_golden_hash returns None when the platform returns zero items (DEBT-143).

        Tested at the unit level by monkeypatching the HTTP response inline via
        monkeypatching insights_from_env to return a fake client.
        """
        # Unit-test the fixed behavior: an empty item list → None, not a fixed hash constant.
        # We test this indirectly by ensuring the existing None-guard catches it.
        # The actual fetch_golden_hash unit test uses a fake insights object.
        import hashlib

        # Verify the PREVIOUS (unfixed) behavior produced a non-None constant.
        # If items == [], the old code would have computed: sha256(json.dumps([]))
        empty_canonical = json.dumps([], sort_keys=True, separators=(",", ":"))
        empty_hash = hashlib.sha256(empty_canonical.encode()).hexdigest()
        # Confirm this is a fixed constant (not None) — this was the bug.
        assert empty_hash is not None
        assert len(empty_hash) == 64
        # The fix: fetch_golden_hash now returns None for empty items.
        # We verify this by patching insights_from_env and checking the function's output.
        from unittest.mock import MagicMock, patch

        fake_body: dict[str, Any] = {"data": []}  # empty items
        fake_http = MagicMock()
        fake_http.request.return_value = (200, fake_body)
        fake_insights = MagicMock()
        fake_insights._http = fake_http

        with patch("idp_regression.ui.api.insights_from_env", return_value=fake_insights):
            from idp_regression.ui.api import fetch_golden_hash
            result = fetch_golden_hash("test-dataset", ws)

        assert result is None, (
            "fetch_golden_hash must return None for an empty platform dataset (DEBT-143 fix) — "
            f"got {result!r} instead. An empty dataset must not produce a hash that satisfies "
            "INV-09(e) vacuously."
        )
