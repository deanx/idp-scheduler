"""T-01.4.10 (DEBT-45) -- the redefined gitleaks gate has three legs. This
suite pins the *shape* of each leg (file presence, key flags/commands) so a
future edit cannot silently regress leg 1 back to full-history, drop leg 2's
wiring, or turn leg 3 into a scan. It intentionally does NOT re-run
`gitleaks git .` over history in CI (that would reintroduce exactly the
O(history)-per-PR problem T-01.4.10 exists to remove) -- leg-firing proof
lives in the implementation transcript, not in the regular suite.

⛔ None of these tests may assert or encode a `paths = ["tests/"]`
allowlist anywhere in the gitleaks config surface -- see
`.gitleaksignore`'s header and DEBT-45.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text()


class TestLeg1DiffScopedGate:
    """Blocking, diff-scoped -- never full-history, never unbounded."""

    def test_workflow_file_exists(self) -> None:
        assert (REPO_ROOT / ".github/workflows/secrets-gate.yml").exists()

    def test_diff_scoped_not_full_history(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "log-opts" in content, "leg 1 must scope the scan via --log-opts, not full history"
        # The bare, unscoped invocation this leg replaces -- must not appear
        # on its own line (the scoped invocation below always carries
        # --log-opts on the same line).
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("gitleaks git ."):
                assert "log-opts" in stripped, f"unscoped gitleaks invocation: {stripped!r}"

    def test_triggers_on_pull_request(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "pull_request:" in content

    def test_merge_base_resolved_against_base_ref(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "merge-base" in content
        assert "base_ref" in content

    def test_no_tests_paths_allowlist_anywhere_in_workflow(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "paths = " not in content
        assert "--path" not in content


class TestLeg2PreCommitHook:
    """Blocking, client-side, staged-only."""

    def test_hook_script_exists_and_is_executable(self) -> None:
        hook = REPO_ROOT / ".githooks/pre-commit"
        assert hook.exists()
        assert hook.stat().st_mode & 0o111, "pre-commit hook must be executable"

    def test_hook_scans_staged_not_history_or_working_tree(self) -> None:
        content = _read(".githooks/pre-commit")
        assert "gitleaks git --staged" in content

    def test_hook_fails_closed_when_gitleaks_missing(self) -> None:
        content = _read(".githooks/pre-commit")
        assert "set -euo pipefail" in content
        assert "command -v gitleaks" in content
        assert "exit 1" in content

    def test_install_script_wires_hookspath(self) -> None:
        content = _read("scripts/install-git-hooks.sh")
        assert "core.hooksPath" in content
        assert ".githooks" in content

    def test_install_script_is_executable(self) -> None:
        script = REPO_ROOT / "scripts/install-git-hooks.sh"
        assert script.stat().st_mode & 0o111


class TestLeg3AssertionNotScan:
    """No gitleaks invocation -- two shell assertions."""

    def test_workflow_asserts_env_ignored_and_untracked(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "git check-ignore -q .env" in content
        assert "git ls-files --error-unmatch .env" in content
        assert "gitleaks" not in content.split("env-hygiene:")[1].split("lockfile-pin:")[0]

    def test_env_is_actually_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, ".env must be gitignored"

    def test_env_is_actually_untracked(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert result.returncode != 0, ".env must never be tracked"


class TestFullHistoryAuditIsSeparateAndNonBlocking:
    """Leg 0 (informational): scheduled, its own file, continue-on-error."""

    def test_audit_workflow_exists_separately(self) -> None:
        assert (REPO_ROOT / ".github/workflows/secrets-audit.yml").exists()

    def test_audit_is_scheduled_not_pull_request_triggered(self) -> None:
        content = _read(".github/workflows/secrets-audit.yml")
        assert "schedule:" in content
        assert "pull_request" not in content

    def test_audit_is_non_blocking(self) -> None:
        content = _read(".github/workflows/secrets-audit.yml")
        assert "continue-on-error: true" in content

    def test_gate_workflow_never_schedules_full_history(self) -> None:
        gate_content = _read(".github/workflows/secrets-gate.yml")
        assert "schedule:" not in gate_content


class TestLockFilePin:
    """CLAUDE.md ## Tooling: install, then `pip freeze` to a committed lock
    file; install from the lock thereafter. Under uv, that's `uv.lock`
    committed and in sync with `pyproject.toml`, with CI installing
    `--locked` (no silent re-resolution).
    """

    def test_lockfile_is_committed(self) -> None:
        assert (REPO_ROOT / "uv.lock").exists()

    def test_lockfile_is_tracked_by_git(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "uv.lock"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert result.returncode == 0, "uv.lock must be committed, not gitignored"

    def test_lockfile_matches_pyproject(self) -> None:
        result = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"uv.lock is out of sync with pyproject.toml: {result.stderr}"
        )

    def test_ci_installs_locked_not_resolved(self) -> None:
        content = _read(".github/workflows/secrets-gate.yml")
        assert "--locked" in content, "CI must install from the lock file, not re-resolve"


class TestGitleaksignoreBaselineNeverBlindedByPathAllowlist:
    def test_no_tests_paths_allowlist_in_gitleaksignore(self) -> None:
        content = _read(".gitleaksignore")
        assert "[allowlist]" not in content

    def test_baseline_is_fingerprint_scoped_not_broad(self) -> None:
        content = _read(".gitleaksignore")
        # A fingerprint line is `commit:file:rule:line`; assert at least one
        # exists and nothing resembling a bare directory glob does.
        fingerprint_lines = [
            line for line in content.splitlines() if line and not line.startswith("#")
        ]
        assert fingerprint_lines
        for line in fingerprint_lines:
            assert line.count(":") >= 3, f"expected a fingerprint, got: {line!r}"
