"""T-01.4.10 (DEBT-45) -- the redefined gitleaks gate has three legs. This
suite pins the *shape* of each leg (file presence, key flags/commands) so a
future edit is caught, not merely likely to be caught, if it silently
regresses leg 1 back to full-history, drops leg 2's wiring, or turns leg 3
into a scan. It intentionally does NOT re-run `gitleaks git .` over history
in CI (that would reintroduce exactly the O(history)-per-PR problem
T-01.4.10 exists to remove) -- leg-firing proof lives in the implementation
transcript, not in the regular suite.

⚠️ **Honesty note (DEBT-44 gate, fourth instance, findings T1/T3, 2026-09-21):
this file has already overclaimed "cannot" once** -- the prior wording here
said a regression "cannot silently" happen, while two of the load-bearing
assertions (leg 1's scoping, leg 3's assertion-not-scan property) were still
whole-file-text substring matches a mutant satisfied via a surviving
comment or an echoed-but-never-executed string. Both are now resolved
against `yaml.safe_load`'s parsed job/step structure and mutation-verified
against the exact mutants that beat the prior version (see
`test_diff_scoped_not_full_history` and
`test_workflow_asserts_env_ignored_and_untracked`). This suite is tested
against every mutant devised against it SO FAR, not proven exhaustive --
treat "the suite is green" as evidence, not proof, and re-mutate before
trusting a future edit here on the strength of this docstring alone.

⛔ None of these tests may assert or encode a `paths = ["tests/"]`
allowlist anywhere in the gitleaks config surface -- see
`.gitleaksignore`'s header and DEBT-45.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text()


def _load_workflow(relpath: str) -> dict[Any, Any]:
    """Parse a workflow file and resolve it against ITS ACTUAL structure,
    never a substring of its text (Atchim review finding 6, DEBT-44 gate
    #2: a string surviving in a *comment* satisfied a substring
    assertion while the resolved job key it was meant to pin had
    changed). PyYAML uses the YAML 1.1 resolver, under which a bare `on:`
    key parses as the boolean `True`, not the string `"on"` -- GitHub
    Actions' own quirk, not a bug here; callers needing the trigger block
    must look it up as `workflow[True]`."""
    loaded = yaml.safe_load(_read(relpath))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[Any, Any]) -> Any:
    """The `on:` block, resolved past PyYAML's YAML-1.1 boolean-key quirk."""
    return workflow.get("on", workflow.get(True))


def _shell_executed_lines(relpath: str) -> list[str]:
    """Lines of a shell script that are actually EXECUTED, not merely
    mentioned.

    ⚠️ DEBT-44 gate, fifth instance, finding (a) (2026-09-21): the prior
    version of this helper only stripped `#`-comment lines. That closed
    the WRONG bypass class for leg 2 -- comments were never the mutant
    here. The gate's M11/M13 mutants replaced a script's real
    invocations with `echo "would run: <the exact substring this suite
    checks for>"`. A comment-stripped read still finds the substring,
    because it's sitting inside an EXECUTED `echo` statement's string
    argument -- present in the text, never actually run as the command
    it names. This helper additionally drops any line whose first shell
    word is `echo`, so a token that lives only as an echoed string no
    longer satisfies "this script does X"."""
    import shlex

    executed = []
    for raw_line in _read(relpath).splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=False)
        except ValueError:
            tokens = stripped.split()
        if tokens and tokens[0] == "echo":
            continue
        executed.append(stripped)
    return executed


class TestLeg1DiffScopedGate:
    """Blocking, diff-scoped -- never full-history, never unbounded."""

    def test_workflow_file_exists(self) -> None:
        assert (REPO_ROOT / ".github/workflows/secrets-gate.yml").exists()

    def test_diff_scoped_not_full_history(self) -> None:
        """DEBT-44 gate, fourth instance, finding T1: a whole-file
        substring match on `"log-opts"` is satisfied by that string
        surviving in a COMMENT (stripped by the YAML parser, so this
        alone would already close it) -- but the prior per-line
        `.strip().startswith("gitleaks git .")` guard was ALSO blind to
        an inline `run: gitleaks git . ...` form (only multi-line `run:
        |` blocks were split and inspected per-line), a form that
        genuinely exists elsewhere in this repo (`secrets-audit.yml`).
        Resolve the job's steps and check every ACTUAL gitleaks
        invocation, regardless of the YAML scalar style it's written in."""
        workflow = _load_workflow(".github/workflows/secrets-gate.yml")
        steps = workflow["jobs"]["gitleaks-diff"]["steps"]
        run_commands = [step["run"] for step in steps if "run" in step]

        gitleaks_invocations = [cmd for cmd in run_commands if "gitleaks git" in cmd]
        assert gitleaks_invocations, "leg 1 must invoke `gitleaks git`"
        for cmd in gitleaks_invocations:
            assert "--log-opts" in cmd, f"unscoped gitleaks invocation: {cmd!r}"

    def test_triggers_on_pull_request(self) -> None:
        workflow = _load_workflow(".github/workflows/secrets-gate.yml")
        assert "pull_request" in _triggers(workflow)

    def test_merge_base_resolved_against_base_ref(self) -> None:
        """Same class as T1 (DEBT-44 gate, fourth instance) -- resolved
        against the job's actual step commands, not whole-file text, so a
        comment mentioning these tokens can't substitute for the real
        resolution logic."""
        workflow = _load_workflow(".github/workflows/secrets-gate.yml")
        steps = workflow["jobs"]["gitleaks-diff"]["steps"]
        run_commands = [step["run"] for step in steps if "run" in step]
        assert any("merge-base" in cmd for cmd in run_commands)
        assert any("base_ref" in cmd for cmd in run_commands)

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
        """DEBT-44 gate, fifth instance, finding (a): M11 replaced this
        hook's entire body with `echo "would run: gitleaks git --staged
        . ; exit 1"` -- the substring survived, the scan didn't run.
        `_shell_executed_lines` drops echo-only lines, so this now
        requires the invocation as an actually-executed command."""
        executed = _shell_executed_lines(".githooks/pre-commit")
        assert any("gitleaks git --staged" in line for line in executed), (
            "leg 2 must actually RUN gitleaks git --staged, not merely mention it"
        )

    def test_hook_fails_closed_when_gitleaks_missing(self) -> None:
        executed = _shell_executed_lines(".githooks/pre-commit")
        assert any(line == "set -euo pipefail" for line in executed)
        assert any("command -v gitleaks" in line for line in executed)
        assert any(line == "exit 1" for line in executed), (
            "the missing-gitleaks branch must actually fail the job (a bare `exit 1`), "
            "not merely log it"
        )

    def test_install_script_wires_hookspath(self) -> None:
        """DEBT-44 gate, fifth instance, finding (a): M13 reduced this
        script to its final `echo` line, deleting the real `git config
        core.hooksPath .githooks` invocation -- leg 2 is then never wired
        into any clone, and the prior comment-stripped-only check still
        passed because both tokens survived inside that echo's string."""
        executed = _shell_executed_lines("scripts/install-git-hooks.sh")
        assert any(
            "core.hooksPath" in line and ".githooks" in line for line in executed
        ), "the hook path must actually be configured (git config core.hooksPath), not just echoed"

    def test_install_script_is_executable(self) -> None:
        script = REPO_ROOT / "scripts/install-git-hooks.sh"
        assert script.stat().st_mode & 0o111


class TestLeg3AssertionNotScan:
    """No gitleaks invocation -- two shell assertions."""

    def test_workflow_asserts_env_ignored_and_untracked(self) -> None:
        """DEBT-44 gate, fourth instance, finding T3: two mutants survived
        the whole-file-text version of this test. (1) Replacing the
        failure block with `echo "skipping: git ls-files
        --error-unmatch .env"` still contains the invocation text --
        as a STRING ARGUMENT to echo, never executed as a command -- so a
        plain "is this substring anywhere in the resolved run command"
        check is not enough by itself; the assertion below additionally
        requires a bare `exit 1` line, which only exists when the
        conditional actually executes (the echo-only replacement has no
        `exit 1` anywhere). (2) Replacing the whole step with `run:
        "true"` is caught by scoping to `jobs["env-hygiene"]["steps"]`'s
        own resolved run commands, not the whole file's text."""
        workflow = _load_workflow(".github/workflows/secrets-gate.yml")
        steps = workflow["jobs"]["env-hygiene"]["steps"]
        run_commands = [step["run"] for step in steps if "run" in step]

        assert any("git check-ignore -q .env" in cmd for cmd in run_commands)

        ls_files_commands = [
            cmd for cmd in run_commands if "git ls-files --error-unmatch .env" in cmd
        ]
        assert ls_files_commands, "leg 3 must invoke `git ls-files --error-unmatch .env`"
        assert any(
            line.strip() == "exit 1" for cmd in ls_files_commands for line in cmd.splitlines()
        ), (
            "the .env-must-not-be-tracked step must actually be able to fail the job "
            "(a bare `exit 1` line), not merely log the invocation's text"
        )

        assert not any("gitleaks" in cmd for cmd in run_commands), (
            "leg 3 is assertion-only -- no gitleaks invocation belongs in this job"
        )

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
        """Presence half migrated to the resolved trigger block (same
        class as T1/T3, DEBT-44 gate fourth instance); the absence half
        stays a whole-text check on purpose -- the gate's own note: an
        absence assertion over whole text is over-strict, never
        tautological (nothing can satisfy `not in` by being a comment)."""
        workflow = _load_workflow(".github/workflows/secrets-audit.yml")
        assert "schedule" in _triggers(workflow)
        content = _read(".github/workflows/secrets-audit.yml")
        assert "pull_request" not in content

    def test_audit_is_non_blocking(self) -> None:
        """Atchim review finding 6, DEBT-44 gate #2: a substring match on
        `"continue-on-error: true"` is satisfied by that string surviving
        ANYWHERE in the file -- including a header comment -- while a
        mutant sets the actual job's `continue-on-error: false`. Resolve
        the workflow and assert the RESOLVED job key, so a change to what
        the workflow actually does is what's being pinned."""
        workflow = _load_workflow(".github/workflows/secrets-audit.yml")
        job = workflow["jobs"]["full-history-audit"]
        assert job["continue-on-error"] is True

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
        """`yaml.safe_load`-verified: assert the flag appears in the
        RESOLVED `lockfile-pin` job's own step commands, not merely
        somewhere in the file text (Atchim review finding 6's class)."""
        workflow = _load_workflow(".github/workflows/secrets-gate.yml")
        steps = workflow["jobs"]["lockfile-pin"]["steps"]
        run_commands = [step["run"] for step in steps if "run" in step]
        assert any("--locked" in cmd for cmd in run_commands), (
            "CI must install from the lock file, not re-resolve"
        )


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
