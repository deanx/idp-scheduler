"""Regressions for the QA findings on the console's spend path.

Zangado's audit of 2026-09-25 blocked the four gap-closing tickets on one
Major defect and raised three Minor ones. Each is pinned here, named for
the finding, so re-introducing any of them fails the suite rather than
the invoice.

F-1 (Major, blocking) -- the preflight guard was fail-OPEN and unenforced.
F-2 -- the concurrency-safe attribution path was never tested.
F-3 -- a resolved artifact was trusted without checking it was this job's.
F-4 -- `os.chdir` on a request thread.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pytest

fastapi = pytest.importorskip("fastapi", reason="the `ui` extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from idp_regression.orchestration.run_artifact import artifact_envelope  # noqa: E402
from idp_regression.ui import jobs, preflight, workspace  # noqa: E402
from idp_regression.ui.api import create_app  # noqa: E402

ALL_CREDENTIALS = preflight.IDP_VARIABLES + preflight.PLATFORM_VARIABLES

SPEND_REQUEST: dict[str, Any] = {
    "dataset": "d",
    "org": "org-1",
    "action": "action-1",
    "trusted_version": "1.0.0",
    "candidate_version": "2.0.0",
    "approved_extractions": 6,
}


@pytest.fixture
def unrunnable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A console on a machine with NO credentials and no `.env` anywhere
    -- the machine the preflight exists to stop."""
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path / "no-repo")
    for name in ALL_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    documents = tmp_path / "docs"
    documents.mkdir()
    (documents / "a.pdf").write_bytes(b"%PDF-1.4")
    try:
        yield TestClient(create_app(scorer_dir=tmp_path / "scorers"))
    finally:
        workspace.set_workspace(previous)


class TestF1PreflightIsEnforcedServerSide:
    """**The server is the authority, not a disabled button.**

    A greyed-out button stops a browser that successfully fetched its
    status. It stops nothing else: a stale tab, a failed fetch, or any
    other caller on this loopback port. And the failure it must prevent
    is not a rejected request -- it is `pin_document` extracting every
    document against live IDP and only THEN failing because the platform
    credentials were never set.
    """

    def test_starting_a_comparison_is_refused_when_the_machine_cannot_run_one(
        self, unrunnable: TestClient, tmp_path: Path
    ) -> None:
        response = unrunnable.post(
            "/api/workflows/compare/start",
            json={**SPEND_REQUEST, "document_dir": str(tmp_path / "docs")},
        )
        assert response.status_code == 503
        assert "cannot run a validation" in response.json()["detail"]

    def test_starting_a_noise_floor_is_refused_too(
        self, unrunnable: TestClient, tmp_path: Path
    ) -> None:
        """The floor spends quota on the same terms, so it is guarded on
        the same terms."""
        response = unrunnable.post(
            "/api/workflows/floor/start",
            json={
                "document_dir": str(tmp_path / "docs"),
                "org": "org-1",
                "action": "action-1",
                "version": "1.0.0",
                "approved_extractions": 2,
            },
        )
        assert response.status_code == 503

    def test_the_refusal_names_the_expensive_failure_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IDP configured + platform absent is the costly combination:
        the run extracts everything, then fails. The refusal has to say
        so or it reads as a nuisance."""
        previous = workspace.workspace_root()
        workspace.set_workspace(tmp_path)
        monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path / "no-repo")
        try:
            for name in preflight.IDP_VARIABLES:
                monkeypatch.setenv(name, "configured")
            for name in preflight.PLATFORM_VARIABLES:
                monkeypatch.delenv(name, raising=False)
            documents = tmp_path / "docs"
            documents.mkdir()
            client = TestClient(create_app(scorer_dir=tmp_path / "scorers"))
            response = client.post(
                "/api/workflows/compare/start",
                json={**SPEND_REQUEST, "document_dir": str(documents)},
            )
            assert response.status_code == 503
            assert "SPEND" in response.json()["detail"]
        finally:
            workspace.set_workspace(previous)

    def test_a_malformed_body_is_still_rejected_before_the_preflight_runs(
        self, unrunnable: TestClient, tmp_path: Path
    ) -> None:
        """Ordering: the missing approval is the caller's error and is
        reported as such (422), rather than being masked by the machine's
        state (503). Both refuse; only one is the caller's to fix."""
        response = unrunnable.post(
            "/api/workflows/compare/start",
            json={
                **{k: v for k, v in SPEND_REQUEST.items() if k != "approved_extractions"},
                "document_dir": str(tmp_path / "docs"),
            },
        )
        assert response.status_code == 422


class TestF2ExperimentPrefixAttribution:
    """The path whose docstring claims immunity to concurrent runs.

    Every earlier test exercised only the time-based FALLBACK, so the
    primary mechanism was unverified -- and had the regex or the log's
    quoting drifted, the suite would have stayed green while every run
    silently degraded to the unsafe path.
    """

    @pytest.fixture
    def artifacts(self, tmp_path: Path) -> Iterator[Path]:
        previous = workspace.workspace_root()
        workspace.set_workspace(tmp_path)
        directory = workspace.artifact_dir()
        directory.mkdir(parents=True)
        yield directory
        workspace.set_workspace(previous)

    @staticmethod
    def write(directory: Path, run_id: str) -> None:
        (directory / f"{run_id}.json").write_text(
            json.dumps(artifact_envelope(
                run_id, {"a.pdf": {"total": {"verdict": "match", "critical": True}}},
                status="complete",
            )),
            encoding="utf-8",
        )

    def test_the_prefix_wins_over_a_newer_artifact_from_a_concurrent_run(
        self, artifacts: Path
    ) -> None:
        ours = "abcd1234" + "0" * 24
        decoy = "ffff9999" + "1" * 24
        self.write(artifacts, ours)
        time.sleep(0.01)
        self.write(artifacts, decoy)  # newer: the fallback would pick this

        assert jobs.find_run_artifact(
            [f'run_eval: pre-run checks passed run="v" experiment="v-{ours[:8]}" items=1'],
            since=0.0,
        ) == ours

    def test_it_matches_the_QUOTED_form_the_orchestrator_actually_logs(
        self, artifacts: Path
    ) -> None:
        """`facade` routes the experiment name through `sanitize_for_log`,
        which `json.dumps`es it -- so the line carries quotes. A pattern
        written against the unquoted form would match nothing and fail
        over to the fallback without a word."""
        run_id = "0a1b2c3d" + "e" * 24
        self.write(artifacts, run_id)
        quoted = f'experiment="ui-validate-{run_id[:8]}"'
        assert jobs.find_run_artifact([quoted], since=0.0) == run_id

    def test_the_unquoted_form_still_matches(self, artifacts: Path) -> None:
        run_id = "11112222" + "3" * 24
        self.write(artifacts, run_id)
        found = jobs.find_run_artifact([f"experiment=ui-validate-{run_id[:8]}"], since=0.0)
        assert found == run_id

    def test_without_a_prefix_it_falls_back_to_the_newest_since_the_job_started(
        self, artifacts: Path
    ) -> None:
        old = "aaaa0000" + "0" * 24
        self.write(artifacts, old)
        time.sleep(0.01)
        new = "bbbb1111" + "1" * 24
        self.write(artifacts, new)
        assert jobs.find_run_artifact(["no marker here"], since=0.0) == new

    def test_the_fallback_is_bounded_by_the_job_start_time(self, artifacts: Path) -> None:
        self.write(artifacts, "cccc2222" + "2" * 24)
        assert jobs.find_run_artifact(["no marker"], since=time.time() + 3600) is None


class TestF3ArtifactMustAgreeWithTheExitCode:
    """A resolved artifact is only this job's if it agrees with it."""

    @pytest.fixture
    def artifacts(self, tmp_path: Path) -> Iterator[Path]:
        previous = workspace.workspace_root()
        workspace.set_workspace(tmp_path)
        directory = workspace.artifact_dir()
        directory.mkdir(parents=True)
        yield directory
        workspace.set_workspace(previous)

    @staticmethod
    def write(
        directory: Path,
        run_id: str,
        *,
        failing: bool,
        status: Literal["complete", "aborted"] = "complete",
    ) -> None:
        verdict = "missing" if failing else "match"
        (directory / f"{run_id}.json").write_text(
            json.dumps(
                artifact_envelope(
                    run_id,
                    {
                        "a.pdf": {
                            "total": {
                                "verdict": verdict,
                                "critical": True,
                                "expected": "1.00",
                                "actual": None if failing else "1.00",
                            }
                        }
                    },
                    status=status,
                    abort_reason="auth_failure" if status == "aborted" else None,
                )
            ),
            encoding="utf-8",
        )

    def test_a_passing_artifact_under_a_failing_job_is_not_claimed(
        self, artifacts: Path
    ) -> None:
        """The real scenario: the PIN half failed, so `run_eval` never
        wrote an artifact -- and the time-bounded fallback picked up a
        neighbour's. Reporting "0 changed documents" for a job that
        failed would read as "still valid"."""
        run_id = "dddd3333" + "3" * 24
        self.write(artifacts, run_id, failing=False)
        summary = jobs.summarize([f"experiment=x-{run_id[:8]}"], 1, since=0.0)
        assert summary["run_id"] is None
        assert summary["changed_documents"] is None
        assert "disagrees with this job" in summary["artifact_discrepancy"]

    def test_an_aborted_artifact_is_never_counted_as_still_valid_documents(
        self, artifacts: Path
    ) -> None:
        """DEBT-91: an aborted run leaves a PARTIAL artifact with no failing
        document in it. Its gate is INCOMPLETE -- consistent with the job's
        non-zero exit, so it is claimed -- but the documents it happens to
        hold are not "still valid", and neither is any count derived from
        them. The summary names the abort instead."""
        run_id = "eeee4444" + "4" * 24
        self.write(artifacts, run_id, failing=False, status="aborted")
        summary = jobs.summarize([f"experiment=x-{run_id[:8]}"], 1, since=0.0)
        assert summary["run_id"] == run_id
        assert summary["run_incomplete"] == "auth_failure"
        assert summary["still_valid_documents"] is None
        assert summary["changed_documents"] is None
        assert summary["verdict"] == "RUN FAILED"

    def test_a_failing_artifact_under_a_passing_job_is_not_claimed(
        self, artifacts: Path
    ) -> None:
        run_id = "eeee4444" + "4" * 24
        self.write(artifacts, run_id, failing=True)
        summary = jobs.summarize([f"experiment=x-{run_id[:8]}"], 0, since=0.0)
        assert summary["run_id"] is None
        assert "artifact_discrepancy" in summary

    def test_an_agreeing_artifact_is_used(self, artifacts: Path) -> None:
        run_id = "ffff5555" + "5" * 24
        self.write(artifacts, run_id, failing=True)
        summary = jobs.summarize([f"experiment=x-{run_id[:8]}"], 1, since=0.0)
        assert summary["run_id"] == run_id
        assert summary["changed_documents"] == 1
        assert "artifact_discrepancy" not in summary


class TestF4NoChdirOnARequestThread:
    def test_loading_the_environment_never_changes_the_working_directory(
        self, tmp_path: Path
    ) -> None:
        """`/api/preflight` calls this, and FastAPI runs sync endpoints in
        a threadpool -- so an `os.chdir` here is a process-global race
        against any other request resolving a relative path."""
        import os

        previous = workspace.workspace_root()
        before = Path.cwd()
        try:
            (tmp_path / ".env").write_text("F4_MARKER=1\n", encoding="utf-8")
            workspace.set_workspace(tmp_path)
            workspace.load_environment()
            assert Path.cwd() == before
            assert os.environ.get("F4_MARKER") == "1"
        finally:
            os.environ.pop("F4_MARKER", None)
            workspace.set_workspace(previous)

    def test_the_dotenv_helper_takes_a_path_and_does_not_chdir(self, tmp_path: Path) -> None:
        import os

        from idp_regression.orchestration.dotenv_support import load_dotenv_file

        (tmp_path / "custom.env").write_text("F4_PATH_MARKER=2\n", encoding="utf-8")
        before = Path.cwd()
        try:
            load_dotenv_file(tmp_path / "custom.env")
            assert Path.cwd() == before
            assert os.environ.get("F4_PATH_MARKER") == "2"
        finally:
            os.environ.pop("F4_PATH_MARKER", None)
