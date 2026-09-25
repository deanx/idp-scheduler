"""One directory, agreed on by both sides.

The bug: the job runner launched child processes with `cwd=<repo root>`
while `reader.py` resolved its paths against the CONSOLE's working
directory. Every tool here names its output relatively, so a validation
started from a console running anywhere but the repo root wrote its pins
and its run artifact where the Pins and Runs pages never look. The user
paid for a batch and saw nothing -- with no error, because nothing had
failed.

These tests pin the two halves of the fix: every path the console reads
comes from the workspace, and every job runs IN it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from idp_regression.ui import jobs, reader, uploads, workspace


@pytest.fixture
def elsewhere(tmp_path: Path) -> Iterator[Path]:
    """A workspace that is deliberately NOT the repo root -- the case the
    old code got wrong."""
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    yield tmp_path
    workspace.set_workspace(previous)


def test_every_console_directory_lives_under_the_workspace(elsewhere: Path) -> None:
    for directory in (
        workspace.artifact_dir(),
        workspace.noise_floor_dir(),
        workspace.pin_store_dir(),
        workspace.upload_dir(),
        workspace.scorer_dir(),
    ):
        assert directory.is_relative_to(elsewhere), f"{directory} escapes the workspace"


def test_the_reader_follows_the_workspace(elsewhere: Path) -> None:
    assert reader.ARTIFACT_DIR() == elsewhere / workspace.ARTIFACT_DIR_NAME
    assert reader.PIN_STORE_DIR() == elsewhere / workspace.PIN_STORE_DIR_NAME
    assert reader.NOISE_FLOOR_DIR() == elsewhere / workspace.NOISE_FLOOR_DIR_NAME


def test_uploads_follow_the_workspace(elsewhere: Path) -> None:
    assert uploads.UPLOAD_DIR() == elsewhere / workspace.UPLOAD_DIR_NAME
    assert uploads.DEFAULT_EXTRACT_DIR().is_relative_to(elsewhere)


def test_a_job_runs_in_the_workspace_so_its_output_lands_where_the_console_looks(
    elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual regression. A job launched anywhere else writes
    `.idp-regression-run-artifacts/` beside ITS cwd, and the Runs page
    -- reading the workspace -- shows nothing."""
    seen: dict[str, object] = {}

    class FakeCompleted:
        returncode = 0
        stdout = "  6 real IDP extraction(s) to be spent\n"
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> FakeCompleted:
        seen.update(kwargs)
        return FakeCompleted()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    documents = elsewhere / "docs"
    documents.mkdir()
    jobs.plan(
        jobs.build_compare_argv(
            document_dir=documents,
            dataset="d",
            org="o",
            action="a",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
    )
    assert seen["cwd"] == elsewhere, (
        "a job must run in the workspace, or its artifacts land where the console cannot read them"
    )


def test_the_workspace_is_resolved_not_relative(tmp_path: Path) -> None:
    """A relative workspace would re-point every path the moment anything
    called `chdir` -- which is how the original bug behaved."""
    previous = workspace.workspace_root()
    try:
        workspace.set_workspace(Path("."))
        assert workspace.workspace_root().is_absolute()
    finally:
        workspace.set_workspace(previous)


def test_load_environment_reports_where_it_looked_and_never_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credentials are decoupled from the workspace on purpose: the
    console loads `.env` into ITS environment so children inherit it,
    which is what lets the workspace be any directory at all.

    The report names paths, never a variable name found inside the file
    and never a value (INV-02).
    """
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        (tmp_path / ".env").write_text("E2E_PROBE_MARKER=super-secret\n", encoding="utf-8")
        monkeypatch.delenv("E2E_PROBE_MARKER", raising=False)
        report = workspace.load_environment()
        assert report["env_file"] == str(tmp_path / ".env")
        assert "super-secret" not in str(report)
        assert "E2E_PROBE_MARKER" not in str(report)
        import os

        assert os.environ["E2E_PROBE_MARKER"] == "super-secret"
    finally:
        workspace.set_workspace(previous)


def test_a_missing_env_file_is_reported_not_raised(tmp_path: Path) -> None:
    """CI and a shell export supply the same variables; no `.env` is a
    fact to report, not a failure."""
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path / "empty")
    try:
        report = workspace.load_environment()
        assert report["env_file"] is None or Path(str(report["env_file"])).is_file()
        assert report["searched"]
    finally:
        workspace.set_workspace(previous)


def test_loading_env_restores_the_working_directory(tmp_path: Path) -> None:
    """`load_dotenv()` reads `./.env`, so this has to chdir -- and a
    console left in the wrong directory would break every relative path
    afterwards."""
    import os

    previous_workspace = workspace.workspace_root()
    before = Path.cwd()
    try:
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
        workspace.set_workspace(tmp_path)
        workspace.load_environment()
        assert Path.cwd() == before
    finally:
        os.chdir(before)
        workspace.set_workspace(previous_workspace)
