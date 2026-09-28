"""Persistence for ReviewSession records (CT-06 / T-02.1.3).

A ReviewSession ties a stage-1 draft/pin job to a stage-2 verify job
across the human review pause that may span a console restart. It is
console-local state, not a platform entity: it lives at

    <workspace>/.idp-regression-jobs/review-sessions/<session_id>.json

mirroring the existing job-history persistence convention in jobs.py.

Fail-closed contract
--------------------
- Missing file                → `ReviewSessionNotFoundError`
- Any parse / schema failure  → `ReviewSessionCorruptError`
- Never a best-effort resume from a partial or corrupt record.

Atomic writes
-------------
Written to a sibling temp file and renamed into place (`os.replace`) so a
reader always sees a complete record or the previous one, never a torn
write. The same pattern `jobs.py` uses for its history files.

File modes
----------
- Session file: 0600 (run-identity data, sensitive-adjacent)
- Session directory: 0700

These mirror the job-history directory's own modes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from idp_regression.ui.jobs import HISTORY_DIR_NAME

logger = logging.getLogger(__name__)

#: The subdirectory under the job-history directory that holds sessions.
SESSIONS_SUBDIR: str = "review-sessions"


class ReviewSessionState(str, Enum):  # noqa: UP042 — StrEnum is 3.11+, str+Enum works on 3.13
    """The lifecycle of a two-stage golden-review session."""

    DRAFTED = "drafted"    # stage 1 complete; waiting for human review
    REVIEWED = "reviewed"  # curator approved; stage 2 ready to be priced
    VERIFYING = "verifying"  # stage 2 job is running
    VERIFIED = "verified"  # stage 2 job completed (pass or fail — gate decides)
    STALE = "stale"        # INV-09 refused stage 2 (golden changed under review)


@dataclass
class ReviewSession:
    """CT-06: the record that ties stage-1 to stage-2 across the review pause."""

    session_id: str
    dataset: str
    org_id: str
    action_id: str
    trusted_version: str
    candidate_version: str
    document_dir: str
    archive_sha256: str
    approved_golden_hash: str | None  # None until review-completion
    stage1_job_id: str
    stage2_job_id: str | None         # None until stage 2 starts
    state: ReviewSessionState
    created_at: str                    # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "dataset": self.dataset,
            "org_id": self.org_id,
            "action_id": self.action_id,
            "trusted_version": self.trusted_version,
            "candidate_version": self.candidate_version,
            "document_dir": self.document_dir,
            "archive_sha256": self.archive_sha256,
            "approved_golden_hash": self.approved_golden_hash,
            "stage1_job_id": self.stage1_job_id,
            "stage2_job_id": self.stage2_job_id,
            "state": self.state.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewSession:
        """Fail-closed: any missing/wrong-typed field raises `ReviewSessionCorruptError`."""
        required_str_fields = (
            "session_id", "dataset", "org_id", "action_id",
            "trusted_version", "candidate_version", "document_dir",
            "archive_sha256", "stage1_job_id", "created_at",
        )
        for field in required_str_fields:
            val = data.get(field)
            if not isinstance(val, str) or not val:
                raise ReviewSessionCorruptError(
                    f"session record is missing or has invalid field {field!r}"
                )
        try:
            state = ReviewSessionState(data["state"])
        except (KeyError, ValueError) as exc:
            raise ReviewSessionCorruptError(
                f"session record has invalid state: {data.get('state')!r}"
            ) from exc

        return cls(
            session_id=data["session_id"],
            dataset=data["dataset"],
            org_id=data["org_id"],
            action_id=data["action_id"],
            trusted_version=data["trusted_version"],
            candidate_version=data["candidate_version"],
            document_dir=data["document_dir"],
            archive_sha256=data["archive_sha256"],
            approved_golden_hash=data.get("approved_golden_hash"),
            stage1_job_id=data["stage1_job_id"],
            stage2_job_id=data.get("stage2_job_id"),
            state=state,
            created_at=data["created_at"],
        )


class ReviewSessionNotFoundError(Exception):
    """The session file does not exist."""


class ReviewSessionCorruptError(Exception):
    """The session file exists but cannot be safely read."""


def sessions_dir(workspace: Path) -> Path:
    """The directory that holds all review-session files for this workspace."""
    return workspace / HISTORY_DIR_NAME / SESSIONS_SUBDIR


def _session_path(session_id: str, workspace: Path) -> Path:
    return sessions_dir(workspace) / f"{session_id}.json"


def save_session(session: ReviewSession, workspace: Path) -> None:
    """Atomically write a review session record.

    Written owner-only (0600) via temp+rename so a reader always sees
    a complete record; the directory is created owner-only (0700) if it
    does not yet exist.
    """
    path = _session_path(session.session_id, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)

    record = json.dumps(session.to_dict(), indent=2)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = pathlib.Path(temporary_name)
    os.fchmod(descriptor, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(record)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise

    logger.info(
        "review_session_saved session_id=%s state=%s",
        session.session_id,
        session.state.value,
    )


def load_session(session_id: str, workspace: Path) -> ReviewSession:
    """Load a session. Fail-closed: raises on any read or parse failure."""
    path = _session_path(session_id, workspace)
    if not path.exists():
        raise ReviewSessionNotFoundError(
            f"no review session {session_id!r} in {sessions_dir(workspace)}"
        )
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewSessionCorruptError(
            f"review session {session_id!r} could not be read: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict):
        raise ReviewSessionCorruptError(
            f"review session {session_id!r} has unexpected JSON type: {type(data).__name__}"
        )
    return ReviewSession.from_dict(data)


def list_sessions(workspace: Path) -> list[ReviewSession]:
    """All review sessions on disk, newest first. Skips corrupt files with a warning."""
    directory = sessions_dir(workspace)
    if not directory.is_dir():
        return []
    sessions = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            session = ReviewSession.from_dict(data)
            sessions.append(session)
        except (OSError, json.JSONDecodeError, ReviewSessionCorruptError) as exc:
            logger.warning("review_session_skipped path=%s reason=%s", path, type(exc).__name__)
    return sessions
