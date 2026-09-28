"""Tests for ReviewSession persistence (T-02.1.3 / CT-06).

Covers:
- Round-trip write and read
- Fail-closed reads (missing / truncated / invalid JSON → raise, not resume)
- Atomic write (temp+rename)
- File mode 0600 / directory mode 0700
- State enum values
- approved_golden_hash starts as None, can be set
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from idp_regression.ui.review_sessions import (
    ReviewSession,
    ReviewSessionCorruptError,
    ReviewSessionNotFoundError,
    ReviewSessionState,
    load_session,
    save_session,
    sessions_dir,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from idp_regression.ui import workspace as ws
    monkeypatch.chdir(tmp_path)
    ws.set_workspace(tmp_path)
    return tmp_path


def make_session(workspace: Path) -> ReviewSession:
    return ReviewSession(
        session_id="abc123",
        dataset="test-dataset",
        org_id="org-001",
        action_id="act-001",
        trusted_version="1.0.0",
        candidate_version="2.0.0",
        document_dir="/tmp/docs",
        archive_sha256="aabbcc",
        approved_golden_hash=None,
        stage1_job_id="job-stage1",
        stage2_job_id=None,
        state=ReviewSessionState.DRAFTED,
        created_at="2026-09-28T00:00:00Z",
    )


class TestReviewSessionRoundTrip:
    def test_write_and_read_returns_identical_session(self, workspace: Path) -> None:
        session = make_session(workspace)
        save_session(session, workspace)
        loaded = load_session("abc123", workspace)
        assert loaded.session_id == session.session_id
        assert loaded.dataset == session.dataset
        assert loaded.org_id == session.org_id
        assert loaded.action_id == session.action_id
        assert loaded.trusted_version == session.trusted_version
        assert loaded.candidate_version == session.candidate_version
        assert loaded.document_dir == session.document_dir
        assert loaded.archive_sha256 == session.archive_sha256
        assert loaded.approved_golden_hash is None
        assert loaded.stage1_job_id == session.stage1_job_id
        assert loaded.stage2_job_id is None
        assert loaded.state == ReviewSessionState.DRAFTED
        assert loaded.created_at == session.created_at

    def test_approved_golden_hash_persists(self, workspace: Path) -> None:
        session = make_session(workspace)
        session.approved_golden_hash = "sha256abcdef"
        save_session(session, workspace)
        loaded = load_session("abc123", workspace)
        assert loaded.approved_golden_hash == "sha256abcdef"

    def test_state_reviewed_persists(self, workspace: Path) -> None:
        session = make_session(workspace)
        session.state = ReviewSessionState.REVIEWED
        save_session(session, workspace)
        loaded = load_session("abc123", workspace)
        assert loaded.state == ReviewSessionState.REVIEWED

    def test_stage2_job_id_persists(self, workspace: Path) -> None:
        session = make_session(workspace)
        session.stage2_job_id = "job-stage2"
        save_session(session, workspace)
        loaded = load_session("abc123", workspace)
        assert loaded.stage2_job_id == "job-stage2"


class TestReviewSessionFailClosed:
    def test_missing_file_raises_not_found(self, workspace: Path) -> None:
        with pytest.raises(ReviewSessionNotFoundError):
            load_session("nonexistent-id", workspace)

    def test_empty_file_raises_corrupt(self, workspace: Path) -> None:
        path = sessions_dir(workspace) / "empty-id.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        with pytest.raises(ReviewSessionCorruptError):
            load_session("empty-id", workspace)

    def test_truncated_json_raises_corrupt(self, workspace: Path) -> None:
        path = sessions_dir(workspace) / "trunc-id.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"session_id": "trunc-id", "dataset"', encoding="utf-8")
        with pytest.raises(ReviewSessionCorruptError):
            load_session("trunc-id", workspace)

    def test_wrong_type_raises_corrupt(self, workspace: Path) -> None:
        path = sessions_dir(workspace) / "bad-id.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ReviewSessionCorruptError):
            load_session("bad-id", workspace)

    def test_missing_required_field_raises_corrupt(self, workspace: Path) -> None:
        path = sessions_dir(workspace) / "partial-id.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # missing most required fields
        path.write_text(json.dumps({"session_id": "partial-id"}), encoding="utf-8")
        with pytest.raises(ReviewSessionCorruptError):
            load_session("partial-id", workspace)


class TestReviewSessionFileModes:
    def test_session_file_is_owner_only_0600(self, workspace: Path) -> None:
        session = make_session(workspace)
        save_session(session, workspace)
        path = sessions_dir(workspace) / "abc123.json"
        file_stat = os.stat(path)
        # Mask to lower 12 bits (permissions)
        permissions = stat.S_IMODE(file_stat.st_mode)
        assert permissions == 0o600, f"expected 0600, got {oct(permissions)}"

    def test_sessions_dir_is_owner_only_0700(self, workspace: Path) -> None:
        session = make_session(workspace)
        save_session(session, workspace)
        dir_path = sessions_dir(workspace)
        dir_stat = os.stat(dir_path)
        permissions = stat.S_IMODE(dir_stat.st_mode)
        assert permissions == 0o700, f"expected 0700, got {oct(permissions)}"


class TestReviewSessionAtomicWrite:
    def test_no_temp_file_left_after_successful_write(self, workspace: Path) -> None:
        session = make_session(workspace)
        save_session(session, workspace)
        tmp_files = list(sessions_dir(workspace).glob("*.tmp"))
        assert tmp_files == [], f"temp files left behind: {tmp_files}"

    def test_write_does_not_corrupt_existing_on_success(self, workspace: Path) -> None:
        """A successful write replaces the old record completely."""
        session = make_session(workspace)
        save_session(session, workspace)
        session.state = ReviewSessionState.REVIEWED
        session.approved_golden_hash = "newhash"
        save_session(session, workspace)
        loaded = load_session("abc123", workspace)
        assert loaded.state == ReviewSessionState.REVIEWED
        assert loaded.approved_golden_hash == "newhash"


class TestReviewSessionState:
    def test_all_valid_state_literals(self) -> None:
        valid = {"drafted", "reviewed", "verifying", "verified", "stale"}
        for s in valid:
            state = ReviewSessionState(s)
            assert state.value == s

    def test_invalid_state_raises(self) -> None:
        with pytest.raises(ValueError):
            ReviewSessionState("nonexistent")


class TestSessionsDir:
    def test_sessions_dir_is_under_history_dir(self, workspace: Path) -> None:
        from idp_regression.ui.jobs import HISTORY_DIR_NAME
        d = sessions_dir(workspace)
        assert d.parent.name == HISTORY_DIR_NAME
        assert d.name == "review-sessions"
