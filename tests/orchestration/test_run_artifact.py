"""ADR-0007 Option E (ACCEPTED 2026-09-23, user decision): the local,
gitignored per-run artifact carrying the FULL CT-02 verdict map
(expected, actual, confidence, verdict), keyed `run_id -> document_id ->
field`. `classify()` already returns this map; `facade.py` used it for
the gate and `build_score_inputs` and then discarded it. This module
persists it -- it computes nothing new.

Binding obligations from the ADR (§ "What implementation owes"): the
writer (this module), a path + retention/deletion policy (module
docstring), a `.gitignore` rule, and an INV-01-style leak test proving
the artifact path can never enter the repo. **Option D's fingerprint is
explicitly deferred, not implemented here** -- nothing new is written to
the platform by this change; `## Domain` / DEBT-18 option B are
untouched.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from idp_regression.classifier.types import Verdict, VerdictMap
from idp_regression.orchestration import run_artifact

_SAMPLE_VERDICT_MAPS: dict[str, VerdictMap] = {
    "doc-1": {
        "total": Verdict(
            verdict="wrong_value",
            expected="1250.00",
            actual="1250.01",
            confidence=0.87,
            critical=True,
            type="number",
        )
    },
    "doc-2": {
        "total": Verdict(
            verdict="match",
            expected="999.00",
            actual="999.00",
            confidence=0.99,
            critical=True,
            type="number",
        )
    },
}


def test_artifact_path_is_under_the_gitignored_directory_and_named_by_run_id() -> None:
    path = run_artifact.artifact_path("0123456789abcdef0123456789abcdef")

    assert path.startswith(run_artifact.ARTIFACT_DIR_NAME + "/") or path.startswith(
        run_artifact.ARTIFACT_DIR_NAME + "\\"
    )
    assert path.endswith("0123456789abcdef0123456789abcdef.json")


def test_write_run_artifact_persists_the_full_verdict_map_keyed_by_run_id_then_document_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = "0123456789abcdef0123456789abcdef"

    run_artifact.write_run_artifact(run_id, _SAMPLE_VERDICT_MAPS)

    written = json.loads(Path(run_artifact.artifact_path(run_id)).read_text(encoding="utf-8"))
    assert written == {run_id: _SAMPLE_VERDICT_MAPS}


def test_write_run_artifact_creates_the_directory_if_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / run_artifact.ARTIFACT_DIR_NAME).exists()

    run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS)

    assert (tmp_path / run_artifact.ARTIFACT_DIR_NAME).is_dir()


def test_write_run_artifact_never_raises_on_a_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Binding obligation: "artifact writing must not be able to fail a
    run that otherwise passed" -- a disk error here must be swallowed,
    not propagated, and never change `run_eval`'s exit code (INV-08,
    CT-04). Simulated by making the target directory unwritable-in-effect
    (monkeypatching `os.makedirs` to blow up, the same class of failure a
    real permission error or a full disk would cause)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "makedirs", _boom)

    with caplog.at_level(logging.WARNING):
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS)  # must not raise

    assert not (tmp_path / run_artifact.ARTIFACT_DIR_NAME).exists()
    assert "OSError" in caplog.text


def test_write_run_artifact_failure_never_leaks_the_exception_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """INV-02: the underlying OSError's message could embed a path or
    other detail -- only the exception TYPE NAME + frame location are
    logged, matching every other best-effort catch-all in this codebase
    (`_mark_run_status_best_effort`)."""
    sentinel = "distinctive-sekrit-disk-detail-8b2f"

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError(sentinel)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "makedirs", _boom)

    with caplog.at_level(logging.WARNING):
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS)

    assert sentinel not in caplog.text


def test_artifact_directory_is_git_ignored() -> None:
    """INV-01-style leak test: proves the artifact path can never enter
    the repo, by asking git itself (not by re-parsing `.gitignore` by
    hand, which could drift from what git actually honours)."""
    repo_root = Path(__file__).resolve().parents[2]
    candidate = run_artifact.artifact_path("any-run-id-at-all")

    result = subprocess.run(
        ["git", "check-ignore", "-q", candidate],
        cwd=repo_root,
        check=False,
    )

    assert result.returncode == 0, (
        f"{candidate!r} is NOT git-ignored -- a run artifact could be committed, "
        "leaking golden/actual/confidence values (DEBT-18 option B, ADR-0007)"
    )


def test_gitignore_names_the_artifact_directory() -> None:
    """Static half of the INV-01-style check, alongside the runtime
    `git check-ignore` above: the rule must be readable in `.gitignore`
    itself, not merely satisfied by some broader accidental pattern."""
    repo_root = Path(__file__).resolve().parents[2]
    gitignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")

    assert run_artifact.ARTIFACT_DIR_NAME in gitignore_text
