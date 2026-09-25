"""Tickets 5-7: the operational gaps around a quota-spending job.

T5 -- four flags the UI could not reach, so a corpus over 200 documents
      failed AFTER being uploaded and priced, and one document failing to
      pin aborted the batch with no way to opt into a partial answer.
T6 -- nothing stopped two batches running against the same pin store at
      once. The `launchd` path has used `/usr/bin/lockf` for exactly this
      since it existed; the console had no equivalent.
T7 -- jobs lived only in memory, so a console restart lost the view of a
      batch in flight while the extractions it spent stayed spent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from idp_regression.ui import jobs, workspace


@pytest.fixture
def space(tmp_path: Path) -> Iterator[Path]:
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    yield tmp_path
    workspace.set_workspace(previous)


@pytest.fixture
def documents(space: Path) -> Path:
    directory = space / "docs"
    directory.mkdir()
    (directory / "a.pdf").write_bytes(b"%PDF-1.4")
    return directory


#: Every non-terminal status. Waiting for `status != "running"` is the
#: trap: a job starts as `planning`, so that test passes instantly on a
#: job that has not begun -- and then asserts on an empty result.
PENDING = ("planning", "running")


def wait_for(registry: jobs.JobRegistry, job_id: str, timeout: float = 10.0) -> jobs.Job:
    deadline = time.time() + timeout
    while registry.get(job_id).status in PENDING and time.time() < deadline:
        time.sleep(0.02)
    job = registry.get(job_id)
    assert job.status not in PENDING, f"job {job_id} never finished (status {job.status})"
    return job


def compare_argv(documents: Path, **overrides: object) -> list[str]:
    kwargs: dict[str, object] = {
        "document_dir": documents,
        "dataset": "d",
        "org": "org-1",
        "action": "action-1",
        "trusted_version": "1.0.0",
        "candidate_version": "2.0.0",
        "plan_only": False,
    }
    kwargs.update(overrides)
    return jobs.build_compare_argv(**kwargs)  # type: ignore[arg-type]


class TestT5ExposedFlags:
    def test_a_corpus_larger_than_the_scripts_default_can_be_run(
        self, documents: Path
    ) -> None:
        """`compare_versions.py` defaults to 200. Without this, a
        500-document corpus failed after upload with no way to raise
        it."""
        assert "--max-documents" in compare_argv(documents, max_documents=500)
        assert "500" in compare_argv(documents, max_documents=500)

    def test_allow_partial_is_off_unless_asked_for(self, documents: Path) -> None:
        """Files that failed to pin have NO golden, so verifying would
        skip them silently and exit 0 on the rest -- "every pinned file is
        still valid" would be true and useless."""
        assert "--allow-partial" not in compare_argv(documents)
        assert "--allow-partial" in compare_argv(documents, allow_partial=True)

    def test_repin_is_off_unless_asked_for(self, documents: Path) -> None:
        """It spends an extra extraction per already-pinned document."""
        assert "--repin" not in compare_argv(documents)
        assert "--repin" in compare_argv(documents, repin=True)

    @pytest.mark.parametrize(
        "bad", ["../evil/*.pdf", "/etc/*.pdf", "*.pdf,", "", "a/b.pdf"]
    )
    def test_a_glob_that_is_a_path_is_refused(self, documents: Path, bad: str) -> None:
        """A pattern is matched against a BASENAME by the batch tools; a
        path here would either match nothing or reach outside the
        corpus."""
        if bad == "":
            assert "--glob" not in compare_argv(documents, glob=bad)
            return
        with pytest.raises(jobs.JobRejectedError, match="no path separators"):
            compare_argv(documents, glob=bad)

    def test_a_normal_glob_passes_through(self, documents: Path) -> None:
        argv = compare_argv(documents, glob="*.pdf,*.tif")
        assert argv[argv.index("--glob") + 1] == "*.pdf,*.tif"


class TestT6OneJobPerWorkspace:
    def test_a_second_job_is_refused_while_the_first_holds_the_workspace(
        self, documents: Path
    ) -> None:
        """Two batches against the same pin store can interleave:
        `pin_document` writes goldens on a deterministic id and
        `verify_document` reads them back, so the second could pin
        goldens the first is still verifying against."""
        registry = jobs.JobRegistry()
        sleeper = ["/bin/sh", "-c", "sleep 2", "--yes"]
        registry.start("compare-versions", sleeper, planned_extractions=1, approved_extractions=1)
        with pytest.raises(jobs.WorkspaceBusyError, match="already running"):
            registry.start(
                "compare-versions", sleeper, planned_extractions=1, approved_extractions=1
            )

    def test_the_lock_is_released_when_the_job_finishes(self, documents: Path) -> None:
        registry = jobs.JobRegistry()
        quick = ["/bin/echo", "done", "--yes"]
        first = registry.start(
            "compare-versions", quick, planned_extractions=1, approved_extractions=1
        )
        wait_for(registry, first.id)
        deadline = time.time() + 5
        while registry.is_busy() and time.time() < deadline:
            time.sleep(0.02)
        assert not registry.is_busy(), "a finished job must not hold the workspace"
        registry.start("compare-versions", quick, planned_extractions=1, approved_extractions=1)

    def test_a_refused_job_leaves_no_trace_and_spends_nothing(
        self, documents: Path
    ) -> None:
        """The lock is taken BEFORE the job record exists, so a refusal
        does not litter the history with a job that never ran."""
        registry = jobs.JobRegistry()
        sleeper = ["/bin/sh", "-c", "sleep 2", "--yes"]
        registry.start("compare-versions", sleeper, planned_extractions=1, approved_extractions=1)
        with pytest.raises(jobs.WorkspaceBusyError):
            registry.start(
                "compare-versions", sleeper, planned_extractions=1, approved_extractions=1
            )
        assert len(registry.summaries()) == 1

    def test_another_console_holding_the_lock_makes_this_one_busy(
        self, space: Path
    ) -> None:
        """`is_busy` probes the LOCK, not this process's job table: a
        second console on the same workspace holds it just as
        effectively, and a UI that only knew about its own jobs would
        offer a Run button that is about to 409."""
        registry = jobs.JobRegistry()
        assert registry.is_busy() is False
        other = jobs._WorkspaceLock(space / jobs.LOCK_FILE_NAME)
        other.acquire()
        try:
            assert registry.is_busy() is True
        finally:
            other.release()
        assert registry.is_busy() is False


class TestT7History:
    def test_a_finished_job_survives_a_console_restart(self, documents: Path) -> None:
        registry = jobs.JobRegistry()
        job = registry.start(
            "compare-versions", ["/bin/echo", "x", "--yes"],
            planned_extractions=4, approved_extractions=4,
        )
        wait_for(registry, job.id)

        restarted = jobs.JobRegistry()
        restarted.load_history()
        assert job.id in {row["id"] for row in restarted.summaries()}
        assert restarted.get(job.id).planned_extractions == 4

    def test_a_job_interrupted_by_a_restart_is_named_not_resumed(
        self, space: Path
    ) -> None:
        """The extractions it spent are spent either way; restarting it
        would spend them again. What used to vanish silently was the
        knowledge that anything was in flight at all."""
        history = space / jobs.HISTORY_DIR_NAME
        history.mkdir(parents=True)
        (history / "deadbeef.json").write_text(
            json.dumps({
                "id": "deadbeef",
                "kind": "compare-versions",
                "status": "running",
                "planned_extractions": 80,
                "command": "/bin/echo x --yes",
                "summary": {},
            }),
            encoding="utf-8",
        )
        registry = jobs.JobRegistry()
        registry.load_history()
        job = registry.get("deadbeef")
        assert job.status != "running"
        assert job.summary["verdict"] == "INTERRUPTED"
        assert "not restarted automatically" in job.summary["note"]

    def test_the_history_never_stores_the_jobs_output(self, documents: Path) -> None:
        """A job's captured output carries the extracted values
        `## Domain` calls sensitive. They already have a home in the
        gitignored run artifact; a second copy under a different
        retention rule is a disclosure surface nobody asked for."""
        # The value must reach the job's OUTPUT without appearing in its
        # command: the command is run identity and is stored on purpose.
        secret = documents.parent / "extracted.txt"
        secret.write_text("SENSITIVE-TOTAL-1250.00\n", encoding="utf-8")
        registry = jobs.JobRegistry()
        job = registry.start(
            "compare-versions", ["/bin/cat", str(secret), "--yes"],
            planned_extractions=1, approved_extractions=1,
        )
        finished = wait_for(registry, job.id)
        assert any("SENSITIVE-TOTAL" in line for line in finished.lines), (
            "the value must actually have reached the job's captured output"
        )
        record = json.loads(
            (workspace.workspace_root() / jobs.HISTORY_DIR_NAME / f"{job.id}.json").read_text()
        )
        assert "lines" not in record
        assert "SENSITIVE-TOTAL" not in json.dumps(record)

    def test_the_history_directory_is_owner_only(self, documents: Path) -> None:
        registry = jobs.JobRegistry()
        job = registry.start(
            "compare-versions", ["/bin/echo", "x", "--yes"],
            planned_extractions=1, approved_extractions=1,
        )
        wait_for(registry, job.id)
        path = workspace.workspace_root() / jobs.HISTORY_DIR_NAME / f"{job.id}.json"
        assert oct(path.stat().st_mode)[-3:] == "600"
        assert oct(path.parent.stat().st_mode)[-3:] == "700"

    def test_an_unreadable_history_file_is_skipped_not_fatal(self, space: Path) -> None:
        history = space / jobs.HISTORY_DIR_NAME
        history.mkdir(parents=True)
        (history / "broken.json").write_text("{not json", encoding="utf-8")
        registry = jobs.JobRegistry()
        registry.load_history()
        assert registry.summaries() == []
