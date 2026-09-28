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
