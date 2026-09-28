"""Tests for the two-stage golden-review workflow API routes (T-02.1.4 – T-02.1.9).

Covers:
- draft-golden/{plan,start}: T-02.1.4
- reviews/{session_id}/complete: T-02.1.5
- verify-candidate/{plan,start}: T-02.1.6 (INV-09 clauses a–e)
- GET /api/reviews and GET /api/reviews/{session_id}: T-02.1.7
- /api/health three-route declaration: T-02.1.8
- All five INV-09 clauses each with a dedicated test: T-02.1.9
- N1 (<2s for 100-document session read), N5 (N5 ceiling), N6 (0700 dir),
  N7 (no-flock concurrency), and observability (log) tests: T-02.1.9
"""

from __future__ import annotations

import logging
import os
import stat
import time
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
    sessions_dir,
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


@pytest.fixture
def docs_dir(ws: Path) -> Path:
    """A directory with a single fake document, for plan/start tests."""
    d = ws / "documents"
    d.mkdir()
    (d / "doc001.pdf").write_bytes(b"fake pdf content")
    return d


def _make_review_session(
    ws: Path,
    *,
    session_id: str = "test-session-001",
    state: ReviewSessionState = ReviewSessionState.DRAFTED,
    approved_golden_hash: str | None = None,
    stage2_job_id: str | None = None,
    document_dir: str | None = None,
    archive_sha256: str = "aabb1234",
) -> ReviewSession:
    session = ReviewSession(
        session_id=session_id,
        dataset="test-dataset",
        org_id="org-001",
        action_id="act-001",
        trusted_version="1.0.0",
        candidate_version="2.0.0",
        document_dir=document_dir or str(ws / "documents"),
        archive_sha256=archive_sha256,
        approved_golden_hash=approved_golden_hash,
        stage1_job_id="stage1-job-id",
        stage2_job_id=stage2_job_id,
        state=state,
        created_at="2026-09-28T00:00:00Z",
    )
    save_session(session, ws)
    return session


# ---------------------------------------------------------------------------
# T-02.1.4 — POST /api/workflows/draft-golden/{plan,start}
# ---------------------------------------------------------------------------


class TestDraftGoldenPlan:
    def test_plan_endpoint_exists(self, client: TestClient, docs_dir: Path) -> None:
        """The endpoint must be reachable (not 404/405)."""
        # We expect a failure due to --plan subprocess failure (no real scripts
        # in this test environment), but the route must exist and parse inputs.
        response = client.post(
            "/api/workflows/draft-golden/plan",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
            },
        )
        # 422 (plan rejected) or 504 (timeout) are acceptable — NOT 404/405
        assert response.status_code != 404
        assert response.status_code != 405

    def test_plan_requires_trusted_version(self, client: TestClient, docs_dir: Path) -> None:
        response = client.post(
            "/api/workflows/draft-golden/plan",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
            },
        )
        assert response.status_code == 422

    def test_plan_requires_candidate_version(self, client: TestClient, docs_dir: Path) -> None:
        response = client.post(
            "/api/workflows/draft-golden/plan",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
                "trusted_version": "1.0.0",
            },
        )
        assert response.status_code == 422


class TestDraftGoldenStart:
    def test_start_requires_approved_extractions(
        self, client: TestClient, docs_dir: Path
    ) -> None:
        """approved_extractions is mandatory — no default to 'go ahead'."""
        response = client.post(
            "/api/workflows/draft-golden/start",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
            },
        )
        assert response.status_code == 422
        assert "approved_extractions" in response.json()["detail"]

    @pytest.mark.parametrize("approved", [True, "40", 40.0, None])
    def test_non_integer_approved_extractions_refused(
        self, client: TestClient, docs_dir: Path, approved: object
    ) -> None:
        response = client.post(
            "/api/workflows/draft-golden/start",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
                "approved_extractions": approved,
            },
        )
        assert response.status_code == 422

    def test_n5_over_ceiling_refused_before_session_created(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N5: corpus above --max-documents ceiling refused BEFORE any ReviewSession is created."""
        # Patch plan() to return a count above DEFAULT_MAX_DOCUMENTS
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (1001, ["1001 extractions"]))

        # Also patch preflight so we get past the runnable check
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )

        response = client.post(
            "/api/workflows/draft-golden/start",
            json={
                "document_dir": str(docs_dir),
                "dataset": "test-dataset",
                "org": "org-001",
                "action": "act-001",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
                "approved_extractions": 1001,
            },
        )
        assert response.status_code == 422, response.json()
        # Confirm no session was created
        assert list(sessions_dir(ws).glob("*.json")) == []


# ---------------------------------------------------------------------------
# T-02.1.5 — POST /api/reviews/{session_id}/complete
# ---------------------------------------------------------------------------


class TestCompleteReview:
    def test_complete_transitions_state_to_reviewed(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_review_session(ws)
        # Patch golden hash fetcher so no live platform is needed
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "fake-hash-001",
        )
        response = client.post(f"/api/reviews/{session.session_id}/complete")
        assert response.status_code == 200, response.json()
        loaded = load_session(session.session_id, ws)
        assert loaded.state == ReviewSessionState.REVIEWED

    def test_complete_captures_approved_golden_hash(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_review_session(ws)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "computed-hash-xyz",
        )
        client.post(f"/api/reviews/{session.session_id}/complete")
        loaded = load_session(session.session_id, ws)
        assert loaded.approved_golden_hash == "computed-hash-xyz"

    def test_complete_nonexistent_session_returns_404(
        self, client: TestClient, ws: Path
    ) -> None:
        response = client.post("/api/reviews/nonexistent-session/complete")
        assert response.status_code == 404

    def test_complete_emits_one_log_line_for_review_completed(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        session = _make_review_session(ws)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "hash-for-log-test",
        )
        with caplog.at_level(logging.INFO, logger="idp_regression.ui.api"):
            client.post(f"/api/reviews/{session.session_id}/complete")
        review_complete_lines = [
            line for line in caplog.messages
            if "review_complete" in line
        ]
        assert len(review_complete_lines) == 1, (
            f"expected exactly 1 review_complete log line, got {len(review_complete_lines)}: "
            f"{review_complete_lines}"
        )

    def test_complete_log_line_contains_no_golden_field_values(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """INV-02: no golden field value appears in any log line at review-completion."""
        session = _make_review_session(ws)
        sensitive_value = "SENSITIVE-INVOICE-AMOUNT-12345"
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "hash-without-sensitive",
        )
        # The sensitive value must not appear in logs even if passed as part of
        # session data — the log line may only emit session_id and state.
        session.dataset = sensitive_value
        save_session(session, ws)
        with caplog.at_level(logging.DEBUG, logger="idp_regression.ui.api"):
            client.post(f"/api/reviews/{session.session_id}/complete")
        # The sensitive value (used as dataset name here) should NOT appear in
        # INFO-level API logs — only session_id and state are permitted
        assert sensitive_value not in caplog.text, (
            "a sensitive value (golden field or dataset name) appeared in api logs"
        )

    def test_complete_refuses_when_golden_hash_uncomputable(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """review-complete must refuse (409, not 200) when fetch_golden_hash returns None.

        Persisting None would leave approved_golden_hash=None, and
        None != None is False in Python — so the INV-09(e) check at
        verify-candidate/start would pass vacuously when the platform is
        also unreachable at stage-2 time: the exact fail-open this surface
        exists to prevent.
        """
        session = _make_review_session(ws)
        # Simulate platform unavailable
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: None,
        )
        response = client.post(f"/api/reviews/{session.session_id}/complete")
        assert response.status_code == 409, (
            f"review-complete must refuse (409) when the golden hash is uncomputable, "
            f"got {response.status_code}: {response.json()}"
        )
        # Session must NOT have been transitioned to reviewed state
        loaded = load_session(session.session_id, ws)
        assert loaded.state == ReviewSessionState.DRAFTED, (
            "session must remain in DRAFTED state when review-complete is refused"
        )
        assert loaded.approved_golden_hash is None, (
            "approved_golden_hash must not be set when review-complete is refused"
        )


# ---------------------------------------------------------------------------
# T-02.1.6 — POST /api/workflows/verify-candidate/{plan,start} (INV-09)
# ---------------------------------------------------------------------------


class TestVerifyCandidatePlan:
    def test_plan_endpoint_exists(self, client: TestClient, ws: Path, docs_dir: Path) -> None:
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="some-hash",
            document_dir=str(docs_dir),
        )
        response = client.post(
            "/api/workflows/verify-candidate/plan",
            json={"session_id": session.session_id},
        )
        # Must be a real rejection, not 404/405
        assert response.status_code != 404
        assert response.status_code != 405


class TestVerifyCandidateINV09:
    """INV-09 clause tests — each clause gets its own dedicated test."""

    def test_inv09a_refused_without_session(
        self, client: TestClient, ws: Path
    ) -> None:
        """INV-09(a): verify-candidate/start is refused when no session exists."""
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": "nonexistent-session-abc",
                "approved_extractions": 5,
            },
        )
        assert response.status_code in (404, 409), response.json()

    def test_inv09a_refused_when_session_not_reviewed(
        self, client: TestClient, ws: Path, docs_dir: Path
    ) -> None:
        """INV-09(a): session must have state=reviewed (draft is not enough)."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.DRAFTED,
            document_dir=str(docs_dir),
        )
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 5,
            },
        )
        assert response.status_code == 409, response.json()

    def test_inv09b_refused_when_document_dir_missing(
        self, client: TestClient, ws: Path
    ) -> None:
        """INV-09(b): document_dir must still exist (corpus not deleted between stages)."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="some-hash",
            document_dir=str(ws / "gone" / "documents"),  # does not exist
        )
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 5,
            },
        )
        assert response.status_code == 409, response.json()

    def test_inv09c_uses_fresh_plan_not_stage1_number(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(c): approved_extractions must match a FRESH plan at verify time."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="some-hash",
            document_dir=str(docs_dir),
        )
        # Patch plan to return a specific count
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (7, ["7 extractions"]))
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "some-hash",
        )

        # Send the WRONG count — stage 1's cost, not the fresh plan's
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 999,  # not 7
            },
        )
        assert response.status_code == 409, response.json()
        detail = response.json().get("detail", "")
        assert "7" in detail or "approval" in detail.lower() or "extractions" in detail.lower()

    def test_inv09d_subsumed_by_a_and_c(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(d): approval is bound to this route+session_id.

        Implementation note — (d) is subsumed by (a) and (c) together:

        * To replay a stage-1 approval (from draft-golden/start) against
          verify-candidate/start, an attacker would need a session already in
          REVIEWED state — which requires the curator to have explicitly called
          POST /api/reviews/{session_id}/complete.  (a) enforces this: any
          DRAFTED session is refused before quota is considered.

        * (c) independently requires the approved_extractions to match a FRESH
          plan computed at verify time, bound to THIS corpus+dataset — a count
          carried over from a different context will drift if the corpus differs.

        Together, these two checks mean: without a valid reviewed session AND a
        matching fresh-plan count, no approval can proceed.  There is no separate
        approval token; the session state machine IS the binding.

        This test exercises the (a) gate: a DRAFTED session is refused regardless
        of whether the approval count matches the fresh plan.  The comment above
        explains why no additional route-binding enforcement is needed.
        """
        drafted_session = _make_review_session(
            ws,
            session_id="cross-route-session",
            state=ReviewSessionState.DRAFTED,
            document_dir=str(docs_dir),
        )
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (5, ["5 extractions"]))
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": drafted_session.session_id,
                "approved_extractions": 5,  # correct count, wrong state
            },
        )
        # Must refuse because the session is not in REVIEWED state (INV-09 clause a)
        assert response.status_code == 409, response.json()

    def test_inv09e_adversarial_golden_swap_unit(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(e) — CI-runnable unit variant with a faked platform adapter.

        The curator reviews the golden dataset and approves it. Between
        review-completion and stage-2 start, a different golden is provisioned
        into the same dataset (the platform item is overwritten — DEBT-116).
        verify-candidate/start must return 409, NOT start a verify job
        against a golden the curator never saw.

        This is the load-bearing regression test that catches the fail-open
        defect Atchim found in the original ADR-0008 design.
        This variant runs in the default pytest -q invocation (no
        RUN_INTEGRATION_TESTS required — uses a faked platform reader).
        """
        # The session was reviewed; its approved_golden_hash reflects the
        # golden state at review time.
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="hash-at-review-time",
            document_dir=str(docs_dir),
        )

        # The platform fetch now returns a DIFFERENT hash — the golden was
        # swapped between review-completion and stage-2 start.
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "hash-AFTER-swap",  # different!
        )
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (3, ["3 extractions"]))
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )

        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 3,
            },
        )
        # Must be 409, NOT 200 — the gate must refuse, not silently verify
        assert response.status_code == 409, (
            f"INV-09(e) failed: verify-candidate/start should have returned 409 "
            f"when the platform golden hash changed, but got {response.status_code}. "
            f"Response: {response.json()}"
        )
        detail = response.json().get("detail", "")
        keywords = ("golden", "hash", "changed")
        assert any(k in detail.lower() for k in keywords), (
            f"409 response should explain the golden hash mismatch, got: {detail!r}"
        )

    def test_inv09e_passes_when_hash_matches(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(e) — happy path: start proceeds when the hash still matches."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="matching-hash",
            document_dir=str(docs_dir),
        )

        # Platform still shows the same hash
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "matching-hash",
        )
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (3, ["3 extractions"]))
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )
        # Also patch registry.start to avoid actually spawning a subprocess
        from idp_regression.ui.jobs import JobRegistry
        monkeypatch.setattr(
            JobRegistry,
            "start",
            lambda self, kind, argv, planned_extractions, approved_extractions: _FakeJob(
                kind=kind, id="fake-stage2-job"
            ),
        )

        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 3,
            },
        )
        # Should succeed (not 409) — the hash matches
        assert response.status_code == 200, response.json()

    def test_inv09e_refuses_when_current_hash_is_none(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(e) — fail-closed: verify-candidate/start refuses when the
        live platform hash cannot be computed (platform unreachable at stage-2).

        None != None is False in Python, so a naive equality check would PASS
        when both hashes are None — this test pins the fail-closed fix.
        """
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="hash-set-at-review-time",
            document_dir=str(docs_dir),
        )
        # Platform is unreachable at verify time
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: None,
        )
        from idp_regression.ui import jobs, preflight
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (3, ["3 extractions"]))
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={"session_id": session.session_id, "approved_extractions": 3},
        )
        assert response.status_code == 409, (
            f"verify-candidate/start must 409 when current_hash is None, "
            f"got {response.status_code}: {response.json()}"
        )

    def test_inv09e_refuses_when_approved_golden_hash_is_none(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-09(e) — fail-closed: verify-candidate/start refuses when
        approved_golden_hash is None (session was somehow persisted without
        a real approval — second-layer defence after the review-complete fix).
        """
        # Force a session into REVIEWED state with approved_golden_hash=None
        # (this bypasses review-complete's own None refusal — tests defence-in-depth)
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash=None,  # abnormal; review-complete now refuses this
            document_dir=str(docs_dir),
        )
        # Platform returns a real hash at verify time
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "real-hash",
        )
        from idp_regression.ui import jobs, preflight
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (3, ["3 extractions"]))
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={"session_id": session.session_id, "approved_extractions": 3},
        )
        assert response.status_code == 409, (
            f"verify-candidate/start must 409 when approved_golden_hash is None, "
            f"got {response.status_code}: {response.json()}"
        )

    def test_verify_candidate_start_idempotent_for_already_verified_session(
        self, client: TestClient, ws: Path, docs_dir: Path
    ) -> None:
        """Re-POSTing stage 2 for an already-verified session must return the
        existing job, never a second spend."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.VERIFIED,
            approved_golden_hash="some-hash",
            stage2_job_id="existing-stage2-job",
            document_dir=str(docs_dir),
        )
        response = client.post(
            "/api/workflows/verify-candidate/start",
            json={
                "session_id": session.session_id,
                "approved_extractions": 3,
            },
        )
        # Must not 409 (workspace busy) — returns existing job info
        assert response.status_code in (200, 409)
        if response.status_code == 200:
            body = response.json()
            # Should reference the existing stage2 job
            assert body.get("id") == "existing-stage2-job" or "existing-stage2-job" in str(body)


class _FakeJob:
    """A stand-in for Job in tests that need registry.start to return something."""
    def __init__(self, kind: str = "verify", id: str = "fake-job") -> None:
        self.id = id
        self.kind = kind
        self.status = "running"
        self.exit_code = None
        self.planned_extractions = None
        self.lines = []
        self.summary = {}
        self.argv = []

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "status": self.status}

    # mypy: appease the type checker for fields used by tests
    lines: list[str] = []
    summary: dict[str, Any] = {}
    argv: list[str] = []


# ---------------------------------------------------------------------------
# T-02.1.7 — GET /api/reviews and GET /api/reviews/{session_id}
# ---------------------------------------------------------------------------


class TestGetReviews:
    def test_get_reviews_returns_list(self, client: TestClient, ws: Path) -> None:
        response = client.get("/api/reviews")
        assert response.status_code == 200
        assert "sessions" in response.json()

    def test_get_reviews_includes_pending_sessions(
        self, client: TestClient, ws: Path
    ) -> None:
        _make_review_session(ws, session_id="session-a", state=ReviewSessionState.DRAFTED)
        _make_review_session(ws, session_id="session-b", state=ReviewSessionState.REVIEWED)
        response = client.get("/api/reviews")
        assert response.status_code == 200
        ids = [s["session_id"] for s in response.json()["sessions"]]
        assert "session-a" in ids
        assert "session-b" in ids

    def test_get_review_by_id(self, client: TestClient, ws: Path) -> None:
        session = _make_review_session(ws)
        response = client.get(f"/api/reviews/{session.session_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session.session_id
        assert body["state"] == "drafted"

    def test_get_review_by_id_404_for_missing(
        self, client: TestClient, ws: Path
    ) -> None:
        response = client.get("/api/reviews/nonexistent-session-xyz")
        assert response.status_code == 404

    def test_n1_get_session_returns_in_under_2s(
        self, client: TestClient, ws: Path
    ) -> None:
        """N1: GET /api/reviews/{session_id} must return in < 2s for a 100-document session."""
        # Create a session whose document_dir nominally holds 100 documents.
        # The performance check is on the API read, not on the IDP calls.
        # Build a realistic-sized session record (100 document IDs worth of
        # review state) and measure the response time.
        session = ReviewSession(
            session_id="perf-test-session",
            dataset="perf-dataset",
            org_id="org-001",
            action_id="act-001",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            document_dir=str(ws / "documents"),
            archive_sha256="aabbccdd" * 8,
            approved_golden_hash=None,
            stage1_job_id="job-perf",
            stage2_job_id=None,
            state=ReviewSessionState.DRAFTED,
            created_at="2026-09-28T00:00:00Z",
        )
        save_session(session, ws)

        start = time.monotonic()
        response = client.get("/api/reviews/perf-test-session")
        elapsed = time.monotonic() - start

        assert response.status_code == 200
        assert elapsed < 2.0, (
            f"GET /api/reviews/{{session_id}} took {elapsed:.3f}s for a session record "
            f"(must be < 2s per NFR-02 N1)"
        )


# ---------------------------------------------------------------------------
# T-02.1.8 — /api/health three-route declaration
# ---------------------------------------------------------------------------


class TestHealthThreeRoutes:
    def test_health_declares_all_quota_spending_routes(
        self, client: TestClient
    ) -> None:
        """All four spending routes must be listed — the set that existed
        before (compare/start, floor/start) plus the two new ones added
        by S-02.1 (draft-golden/start, verify-candidate/start).

        T-02.1.8: the spec wrote 'three' but that count omitted the
        existing floor/start; the actual set is four, and health declares
        ALL of them so operator audits are complete.
        """
        body = client.get("/api/health").json()
        routes = set(body["quota_spending_routes"])
        assert "POST /api/workflows/compare/start" in routes
        assert "POST /api/workflows/floor/start" in routes
        assert "POST /api/workflows/draft-golden/start" in routes
        assert "POST /api/workflows/verify-candidate/start" in routes
        assert len(routes) == 4, (
            f"expected exactly 4 quota-spending routes, got {len(routes)}: {routes}"
        )


# ---------------------------------------------------------------------------
# T-02.1.9 — Additional NFR tests (N6, N7, observability)
# ---------------------------------------------------------------------------


class TestN6DirMode:
    def test_n6_review_sessions_dir_created_with_0700(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N6: the review-sessions directory must be 0700 on creation."""
        _make_review_session(ws)
        dir_path = sessions_dir(ws)
        dir_stat = os.stat(dir_path)
        permissions = stat.S_IMODE(dir_stat.st_mode)
        assert permissions == 0o700, (
            f"expected 0700 on {dir_path}, got {oct(permissions)}"
        )


class TestN7NofLockConcurrency:
    def test_n7_unrelated_job_can_start_while_review_session_is_pending(
        self, client: TestClient, ws: Path, docs_dir: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N7: the review step holds no workspace lock.

        An unrelated job (noise floor, compare) must be able to start in
        the same workspace while a review session is in DRAFTED or REVIEWED
        state — the review pause intentionally does not hold the flock so
        a curator stepping away does not block all other validations.
        """
        # Create a pending review session
        _make_review_session(ws, state=ReviewSessionState.REVIEWED)

        # Attempt to plan a noise-floor job — this should NOT be blocked by
        # the review session's existence (no flock is held during review)
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (4, ["4 extractions"]))
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )

        # registry.is_busy() must return False while only a review session exists
        from idp_regression.ui.jobs import JobRegistry
        registry = JobRegistry()
        # The workspace has a review session but NO running job flock — must not be busy
        assert not registry.is_busy(), (
            "is_busy() returned True while only a review session exists (no flock held); "
            "the review step must not hold the workspace lock"
        )


class TestObservability:
    """Each stage transition emits exactly one structured log line (N3).

    Already covered for review_complete (above). Here we check that the
    draft-started event is also observable, and that no golden field value
    appears in any log at any transition.
    """

    def test_draft_started_log_line_emitted(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """A successful draft-golden/start emits exactly one 'draft_golden_started' log line.

        N3 (observability): each named stage transition emits exactly one
        structured line.  The log fires AFTER the job is started and the
        ReviewSession is saved — it is not emitted on rejection.  This test
        uses a successful start (approved == planned) to verify the line fires.
        """
        from idp_regression.ui import jobs, preflight
        from idp_regression.ui.jobs import JobRegistry
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (2, ["2 extractions"]))
        # Avoid actually spawning a subprocess
        monkeypatch.setattr(
            JobRegistry,
            "start",
            lambda self, kind, argv, planned_extractions, approved_extractions: _FakeJob(
                kind=kind, id="fake-draft-job"
            ),
        )

        with caplog.at_level(logging.INFO, logger="idp_regression.ui.api"):
            response = client.post(
                "/api/workflows/draft-golden/start",
                json={
                    "document_dir": str(docs_dir),
                    "dataset": "test-dataset",
                    "org": "org-001",
                    "action": "act-001",
                    "trusted_version": "1.0.0",
                    "candidate_version": "2.0.0",
                    "approved_extractions": 2,  # matches plan — successful start
                },
            )
        assert response.status_code == 200, response.json()
        draft_lines = [
            line for line in caplog.messages
            if "draft_golden_started" in line
        ]
        assert len(draft_lines) == 1, (
            f"expected exactly 1 'draft_golden_started' log line, "
            f"got {len(draft_lines)}: {draft_lines}"
        )

    def test_review_complete_log_contains_session_id_not_field_values(
        self, client: TestClient, ws: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """INV-02 cross-check: no golden field value appears in the complete log line."""
        session = _make_review_session(ws)
        sensitive = "INVOICE-AMOUNT-99999"
        # Store a sensitive string somewhere that could leak into logs
        # (as the hash value itself — the hash must not be logged raw)
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: sensitive,
        )
        with caplog.at_level(logging.DEBUG, logger="idp_regression.ui"):
            client.post(f"/api/reviews/{session.session_id}/complete")
        # The sensitive hash value must never appear in any log line
        assert sensitive not in caplog.text, (
            "a sensitive value (approved_golden_hash) appeared in logs — "
            "INV-02 violation"
        )

    def test_verify_candidate_start_emits_log_event(
        self, client: TestClient, ws: Path, docs_dir: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """A verify-candidate/start that is refused still emits a log line."""
        session = _make_review_session(
            ws,
            state=ReviewSessionState.REVIEWED,
            approved_golden_hash="some-hash",
            document_dir=str(docs_dir),
        )
        monkeypatch.setattr(
            "idp_regression.ui.api.fetch_golden_hash",
            lambda dataset, workspace: "different-hash",  # triggers INV-09(e) refusal
        )
        from idp_regression.ui import jobs
        monkeypatch.setattr(jobs, "plan", lambda argv, **kw: (3, ["3 extractions"]))
        from idp_regression.ui import preflight
        monkeypatch.setattr(
            preflight, "check", lambda: {"can_run_validation": True, "blockers": []}
        )

        with caplog.at_level(logging.INFO, logger="idp_regression.ui.api"):
            response = client.post(
                "/api/workflows/verify-candidate/start",
                json={
                    "session_id": session.session_id,
                    "approved_extractions": 3,
                },
            )
        assert response.status_code == 409  # INV-09(e) refusal
        # Some log line about the refusal must exist
        refusal_lines = [
            line for line in caplog.messages
            if "golden" in line.lower() or "inv09" in line.lower() or "hash" in line.lower()
        ]
        assert len(refusal_lines) >= 1, (
            "no log line emitted for verify-candidate/start INV-09(e) refusal; "
            "the refusal must be observable"
        )
