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
import stat
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
            format_critical=False,
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
            format_critical=False,
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

    run_artifact.write_run_artifact(run_id, _SAMPLE_VERDICT_MAPS, status="complete")

    written = json.loads(Path(run_artifact.artifact_path(run_id)).read_text(encoding="utf-8"))
    assert written == {
        "format": run_artifact.ARTIFACT_FORMAT,
        "run_id": run_id,
        "status": "complete",
        "abort_reason": None,
        "documents": _SAMPLE_VERDICT_MAPS,
    }


def test_write_run_artifact_creates_the_directory_if_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / run_artifact.ARTIFACT_DIR_NAME).exists()

    run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

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
        # must not raise
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

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
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

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


# --- Required review finding: world-readable artifact (independent review
# of 318c9ff) -- the directory and file must be private to the owner. ---


def test_write_run_artifact_creates_the_directory_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A freshly-created artifact directory must be `0700`, not the
    default-umask `0755` `os.makedirs` would otherwise produce."""
    monkeypatch.chdir(tmp_path)

    run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

    mode = os.stat(run_artifact.ARTIFACT_DIR_NAME).st_mode
    assert stat.S_IMODE(mode) & 0o077 == 0, oct(stat.S_IMODE(mode))


def test_write_run_artifact_writes_the_file_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The JSON file itself must be `0600`, not the default-umask `0644`
    `open(path, "w")` would otherwise produce -- this is the Required
    finding: `bill_to`, `currency`, `invoice_date` and every expected/
    actual/confidence pair for the run were world-readable."""
    monkeypatch.chdir(tmp_path)
    run_id = "0123456789abcdef0123456789abcdef"

    run_artifact.write_run_artifact(run_id, _SAMPLE_VERDICT_MAPS, status="complete")

    mode = os.stat(run_artifact.artifact_path(run_id)).st_mode
    assert stat.S_IMODE(mode) & 0o077 == 0, oct(stat.S_IMODE(mode))


def test_write_run_artifact_tightens_a_pre_existing_world_readable_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`os.makedirs(..., exist_ok=True)` does NOT chmod an already-existing
    directory -- reproduced live against this working tree's own
    `.idp-regression-run-artifacts` (`0755`) before this fix. An existing
    install must be tightened on the next write, not left wrong forever."""
    monkeypatch.chdir(tmp_path)
    os.makedirs(run_artifact.ARTIFACT_DIR_NAME, mode=0o755)
    os.chmod(run_artifact.ARTIFACT_DIR_NAME, 0o755)
    assert stat.S_IMODE(os.stat(run_artifact.ARTIFACT_DIR_NAME).st_mode) == 0o755

    run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

    mode = stat.S_IMODE(os.stat(run_artifact.ARTIFACT_DIR_NAME).st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_write_run_artifact_never_raises_on_an_open_or_dump_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The previous failure-injection test only covered `os.makedirs`;
    nothing exercised a failure once the directory step succeeds -- this
    pins the `open`/`json.dump` leg too (review Suggestion 2)."""
    monkeypatch.chdir(tmp_path)

    def _boom(*args: object, **kwargs: object) -> int:
        raise OSError("[Errno 13] Permission denied")

    monkeypatch.setattr(os, "open", _boom)

    with caplog.at_level(logging.WARNING):
        # must not raise
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

    assert not (tmp_path / run_artifact.artifact_path("run-a")).exists()
    assert "OSError" in caplog.text


def test_write_run_artifact_rejects_a_run_id_with_a_path_separator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Suggestion 1: `write_run_artifact` must not trust `run_id` to stay
    inside `ARTIFACT_DIR_NAME` just because its one production caller
    passes a `uuid4().hex`. A fail-closed guard makes containment a
    property of the writer, not of its caller."""
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING):
        # must not raise
        run_artifact.write_run_artifact("../escape", _SAMPLE_VERDICT_MAPS, status="complete")

    assert not (tmp_path.parent / "escape.json").exists()
    assert not (tmp_path / run_artifact.ARTIFACT_DIR_NAME).exists()
    assert "run_id" in caplog.text.lower()


def test_write_run_artifact_accepts_a_well_formed_uuid4_hex_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard must not be so tight it rejects the one shape the real
    caller (`run_naming.generate_run_id`) ever produces."""
    monkeypatch.chdir(tmp_path)
    run_id = "0123456789abcdef0123456789abcdef"

    run_artifact.write_run_artifact(run_id, _SAMPLE_VERDICT_MAPS, status="complete")

    assert Path(run_artifact.artifact_path(run_id)).exists()


# --- DEBT-91: completeness travels with the verdicts ------------------------


def test_an_aborted_run_writes_its_status_and_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    run_artifact.write_run_artifact(
        "run-a", _SAMPLE_VERDICT_MAPS, status="aborted", abort_reason="auth_failure"
    )
    written = json.loads(Path(run_artifact.artifact_path("run-a")).read_text(encoding="utf-8"))
    parsed = run_artifact.parse_run_artifact(written)
    assert (parsed.status, parsed.abort_reason) == ("aborted", "auth_failure")
    assert parsed.documents == _SAMPLE_VERDICT_MAPS


def test_a_legacy_artifact_still_parses_but_cannot_claim_it_completed() -> None:
    parsed = run_artifact.parse_run_artifact({"r1": {"a.pdf": {}}})
    assert parsed.run_id == "r1"
    assert parsed.status is None
    assert parsed.documents == {"a.pdf": {}}


@pytest.mark.parametrize(
    "data",
    [
        {"format": "idp-regression-run-artifact/999", "run_id": "r", "status": "complete",
         "documents": {}},
        {"format": run_artifact.ARTIFACT_FORMAT, "run_id": "r", "status": "done",
         "documents": {}},
        {"format": run_artifact.ARTIFACT_FORMAT, "run_id": "r", "status": "complete",
         "documents": []},
        {"format": run_artifact.ARTIFACT_FORMAT, "run_id": "", "status": "complete",
         "documents": {}},
        {"a": {}, "b": {}},
        [],
    ],
)
def test_anything_that_is_not_an_artifact_is_refused(data: object) -> None:
    with pytest.raises(ValueError):
        run_artifact.parse_run_artifact(data)


@pytest.mark.parametrize(
    ("gates", "status", "expected"),
    [
        (["PASS", "PASS"], "complete", "PASS"),
        (["PASS", "FAIL"], "complete", "FAIL"),
        # A known failure stays known however the run ended.
        (["FAIL"], "aborted", "FAIL"),
        (["FAIL"], None, "FAIL"),
        # The DEBT-91 cases: none of these is a pass.
        (["PASS"], "aborted", "INCOMPLETE"),
        ([], "aborted", "INCOMPLETE"),
        (["PASS"], None, "INCOMPLETE"),
        (["PASS", "UNKNOWN"], "complete", "INCOMPLETE"),
        ([], "complete", "INCOMPLETE"),
    ],
)
def test_run_level_gate_is_pass_only_for_a_complete_run_of_passing_documents(
    gates: list[str], status: run_artifact.RunStatus | None, expected: str
) -> None:
    assert run_artifact.run_level_gate(gates, status) == expected


# --- S-01.4 re-stamp #7 F-6 (RA5, RA7): two guards with no test -----------

def test_write_run_artifact_refuses_a_preplanted_symlink_at_the_target_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`O_NOFOLLOW` on the open: a symlink sitting where the artifact
    would be written is refused, never followed. `run_id` is an
    unpredictable uuid4 in production, so this is defense in depth, not
    a reachable live path -- but nothing pinned it until now."""
    monkeypatch.chdir(tmp_path)
    os.makedirs(run_artifact.ARTIFACT_DIR_NAME, mode=0o700, exist_ok=True)
    target = tmp_path / "elsewhere.json"
    target.write_text("not a real artifact")
    link_path = Path(run_artifact.artifact_path("run-a"))
    link_path.symlink_to(target)

    with caplog.at_level(logging.WARNING):
        run_artifact.write_run_artifact("run-a", _SAMPLE_VERDICT_MAPS, status="complete")

    assert link_path.is_symlink()  # untouched, not followed and overwritten
    assert target.read_text() == "not a real artifact"


def test_artifact_envelope_never_carries_an_abort_reason_on_a_complete_run() -> None:
    """A caller passing `abort_reason` alongside `status="complete"`
    (a bug, since a completed run has no reason to abort) must not have
    it silently written -- the envelope's own `if status == "aborted"`
    guard, not the caller, is what keeps the two fields consistent."""
    envelope = run_artifact.artifact_envelope(
        "run-a", _SAMPLE_VERDICT_MAPS, status="complete", abort_reason="should_be_dropped"
    )
    assert envelope["abort_reason"] is None
