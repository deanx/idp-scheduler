"""L12 -- `scripts/` (the credential-handling, unattended-run surface) had
zero test coverage before this file: `grep` across `tests/` found nothing
referencing `run_eval_scheduled`, `run_eval_local` or
`provision_golden_dataset`. Pins the fixes from the 2026-09-23 resilience
review (H1/H2/M3/M4/L7/L9) as real, hermetic tests -- no live IDP or
Langfuse call, entirely subprocess/temp-checkout based, per that review's
own constraint.

Each test builds an isolated scratch checkout (never runs against the
real repo's `.env`/lock file/logs) so these are safe to run concurrently
and repeatedly.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _make_scratch_checkout(tmp_path: Path, *, run_eval_local_body: str) -> Path:
    """A minimal scratch copy of the two scheduled-run scripts, with
    `run_eval_local.sh` replaced by a stub so no real CLI (and therefore
    no real credential/network path) is ever invoked."""
    scratch = tmp_path / "checkout"
    (scratch / "scripts").mkdir(parents=True)
    shutil.copy(
        SCRIPTS_DIR / "run_eval_scheduled.sh", scratch / "scripts" / "run_eval_scheduled.sh"
    )
    stub = scratch / "scripts" / "run_eval_local.sh"
    stub.write_text(run_eval_local_body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return scratch


# ── M3: a malformed `.env` must never leak bash's own parse diagnostic ──


def test_run_eval_local_env_parse_failure_withholds_contents(tmp_path: Path) -> None:
    scratch = tmp_path / "checkout"
    (scratch / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS_DIR / "run_eval_local.sh", scratch / "scripts" / "run_eval_local.sh")
    # The exact unquoted-space mistake the review reproduced against.
    (scratch / ".env").write_text("IDP_CLIENT_SECRET=Abc123 SeCrEtTaIl\n")

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_local.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "SeCrEtTaIl" not in result.stdout
    assert "SeCrEtTaIl" not in result.stderr
    assert "command not found" not in result.stderr
    assert "run_eval_local: .env failed to parse (contents withheld)" in result.stderr


def test_run_eval_local_valid_env_still_composes_the_command(tmp_path: Path) -> None:
    """The M3 fix must not break the well-formed path -- a valid `.env`
    still reaches the real composed command line (here failing later,
    harmlessly, because `.venv/bin/python` doesn't exist in the scratch
    checkout; that's fine, we only assert the parse succeeded)."""
    scratch = tmp_path / "checkout"
    (scratch / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS_DIR / "run_eval_local.sh", scratch / "scripts" / "run_eval_local.sh")
    (scratch / ".env").write_text(
        "IDP_ORG_ID=org1\n"
        "IDP_ACTION_ID=act1\n"
        "IDP_TEST_ACTION_VERSION=v1\n"
        "GOLDEN_DATASET_NAME=ds1\n"
    )

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_local.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert ".env failed to parse" not in result.stderr
    assert "--org org1 --action act1 --version v1 --dataset ds1" in result.stderr


def test_run_eval_local_env_trailing_command_never_leaks_its_stdout(tmp_path: Path) -> None:
    """F-2 (resilience re-review, 2026-09-23): M3 only closed the
    stderr/non-zero-rc leak channel. A malformed `.env` whose trailing
    word is a *valid* command (e.g. the reproduced
    `IDP_CLIENT_SECRET=SuperSecret123 env`) runs that command
    successfully -- `source` returns 0, M3's check never fires, and the
    command's STDOUT (here, a full env dump including the secret) used
    to be inherited straight through into the caller's log. Sourcing a
    well-formed `.env` never produces output at all, so ANY output --
    even on a zero return code -- must be treated as suspect and
    withheld.
    """
    scratch = tmp_path / "checkout"
    (scratch / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS_DIR / "run_eval_local.sh", scratch / "scripts" / "run_eval_local.sh")
    (scratch / ".env").write_text(
        "IDP_ORG_ID=org1\n"
        "IDP_ACTION_ID=act1\n"
        "IDP_TEST_ACTION_VERSION=v1\n"
        "GOLDEN_DATASET_NAME=ds1\n"
        "IDP_CLIENT_SECRET=SuperSecret123 env\n"
    )

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_local.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "SuperSecret123" not in result.stdout
    assert "SuperSecret123" not in result.stderr
    assert "withheld" in result.stderr


# ── L9: a real child exit of 75 must never be reported as SKIPPED ───────


def test_run_eval_scheduled_reports_a_real_child_exit_75_as_a_failure(tmp_path: Path) -> None:
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\necho 'child ran and is failing on purpose'\nexit 75\n",
    )

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 75
    log_dir = scratch / "logs" / "scheduled-runs"
    logs = list(log_dir.glob("run-*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text()
    assert "SKIPPED" not in log_text
    assert "exit=75" in log_text
    markers = list(log_dir.glob("FAILED-*.marker"))
    assert len(markers) == 1


def test_run_eval_scheduled_distinguishes_busy_lock_from_child_exit(tmp_path: Path) -> None:
    """A genuinely busy lock (the OTHER invocation still holding it) must
    exit 0/SKIPPED -- proving L9's fix didn't just flip the polarity and
    start reporting busy-lock as a failure too."""
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\nsleep 5\nexit 0\n",
    )

    first = subprocess.Popen(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Give the first invocation time to acquire the lock before the
        # second one starts.
        time.sleep(1)
        second = subprocess.run(
            ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        first.wait(timeout=30)

    assert first.returncode == 0
    assert second.returncode == 0

    log_dir = scratch / "logs" / "scheduled-runs"
    logs = sorted(log_dir.glob("run-*.log"))
    assert len(logs) == 2
    combined = "\n".join(p.read_text() for p in logs)
    assert "SKIPPED" in combined
    assert not list(log_dir.glob("FAILED-*.marker"))


def test_run_eval_scheduled_log_dir_is_not_world_or_group_readable(tmp_path: Path) -> None:
    """F-2 log-dir hardening: `umask 077` plus an unconditional
    `chmod 0700` must keep `logs/scheduled-runs/` closed regardless of
    the ambient umask the test runs under, and regardless of whether
    the directory pre-existed with a looser mode (the launchd plist's
    own `Umask 0077` only covers launchd-started runs and only new
    files -- an already-0755 directory from an earlier run or a manual
    `mkdir` stays 0755 without this explicit fix, same trap 852e06f had
    to close for the run-artifact file)."""
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\necho 'ok'\nexit 0\n",
    )
    # Simulate a pre-existing, loosely-permissioned log dir.
    log_dir = scratch / "logs" / "scheduled-runs"
    log_dir.mkdir(parents=True)
    log_dir.chmod(0o755)

    old_umask = os.umask(0o022)
    try:
        result = subprocess.run(
            ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        os.umask(old_umask)

    assert result.returncode == 0
    mode = stat.S_IMODE(log_dir.stat().st_mode)
    assert mode == 0o700, oct(mode)
    for log_file in log_dir.glob("run-*.log"):
        file_mode = stat.S_IMODE(log_file.stat().st_mode)
        assert file_mode & 0o077 == 0, oct(file_mode)


def test_run_eval_scheduled_success_path_is_clean(tmp_path: Path) -> None:
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\necho 'ok'\nexit 0\n",
    )

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    log_dir = scratch / "logs" / "scheduled-runs"
    assert not list(log_dir.glob("FAILED-*.marker"))
    # No leftover status-file bookkeeping.
    assert not list(log_dir.glob(".status-*"))


# ── F-1: lockf's OWN exec failure must never collapse into "busy lock" ──


def test_run_eval_scheduled_lockf_exec_failure_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    """A re-review (2026-09-23) reproduced this live by pointing
    `LOCKF_BIN` at a non-existent binary: the pre-fix script folded
    "lockf could not exec at all" into the exact same SKIPPED/exit-0
    path as "the lock is genuinely busy" -- because it read the
    absence of a status file as sufficient evidence of a busy lock,
    without checking that lockf's own exit code was actually
    EX_TEMPFAIL (75). A broken `lockf` install would then make the
    regression never run, on every tick, forever, with launchd
    recording success and no marker and no notification -- the
    silently-wrong-GREEN failure CLAUDE.md's Rigor note names as the
    worst this system can produce.
    """
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\necho 'must never run'\nexit 0\n",
    )

    env = dict(os.environ)
    env["LOCKF_BIN"] = str(tmp_path / "no-such-lockf-binary")

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode != 0

    log_dir = scratch / "logs" / "scheduled-runs"
    logs = list(log_dir.glob("run-*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text()
    assert "SKIPPED" not in log_text
    assert "infrastructure failure" in log_text

    markers = list(log_dir.glob("FAILED-*.marker"))
    assert len(markers) == 1


def test_run_eval_scheduled_busy_lock_exit_75_is_still_a_skip(tmp_path: Path) -> None:
    """Companion to the above -- proves the F-1 fix didn't just flip
    polarity and start treating a genuinely busy lock (lockf's real
    EX_TEMPFAIL) as a failure too."""
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\nsleep 5\nexit 0\n",
    )

    first = subprocess.Popen(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        second = subprocess.run(
            ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        first.wait(timeout=30)

    assert first.returncode == 0
    assert second.returncode == 0
    log_dir = scratch / "logs" / "scheduled-runs"
    assert not list(log_dir.glob("FAILED-*.marker"))


# ── F-3: a corrupt/unreadable status file must fail closed ──────────────


def test_classify_and_report_corrupt_status_is_fail_closed(tmp_path: Path) -> None:
    """A partial write, full disk, or FS error can leave the child's
    status file empty or non-numeric. The pre-fix script piped that
    raw value straight into `[ "${status}" -ne 0 ]`, which errors on a
    non-integer -- the `if` was then never entered, so a genuinely
    failed child (observed live: child exited 4, wrapper rc 255)
    produced no marker and no notification. `classify_and_report` is
    exercised directly here (via `source`, guarded by this script's own
    `BASH_SOURCE == $0` check so sourcing it does not also trigger the
    real lockf/subprocess dance) so the corrupt-status path can be
    pinned without racing a real partial write.
    """
    log_file = tmp_path / "run.log"
    log_file.write_text("")
    marker = tmp_path / "FAILED.marker"
    script = SCRIPTS_DIR / "run_eval_scheduled.sh"

    harness = f"""
set -u
source "{script}"
result="$(classify_and_report "" "{log_file}" "{marker}")"
echo "EXITCODE=${{result}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "EXITCODE=1" in result.stdout, result.stdout + result.stderr
    assert marker.exists()
    log_text = log_file.read_text()
    assert "corrupt" in log_text.lower()


def test_classify_and_report_valid_status_is_unaffected(tmp_path: Path) -> None:
    """The F-3 fix must not disturb the ordinary numeric-status path."""
    log_file = tmp_path / "run.log"
    log_file.write_text("")
    marker = tmp_path / "FAILED.marker"
    script = SCRIPTS_DIR / "run_eval_scheduled.sh"

    harness = f"""
set -u
source "{script}"
result="$(classify_and_report "0" "{log_file}" "{marker}")"
echo "EXITCODE=${{result}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "EXITCODE=0" in result.stdout, result.stdout + result.stderr
    assert not marker.exists()
    assert "corrupt" not in log_file.read_text().lower()


# ── M4: the AppleScript notification must survive a `"` in the log path ─


@pytest.mark.skipif(sys.platform != "darwin", reason="osascript is macOS-only")
def test_run_eval_scheduled_notification_survives_a_quote_in_the_path(tmp_path: Path) -> None:
    """A `"` in the checkout path used to terminate the AppleScript
    string literal early and silently swallow the failure notification
    (the review's live repro). The argument form must not break, and
    must not need the `|| true` escape hatch to look healthy."""
    quoted_dir = tmp_path / 'qu"ote-checkout'
    quoted_dir.mkdir()
    scratch = quoted_dir / "checkout"
    (scratch / "scripts").mkdir(parents=True)
    shutil.copy(
        SCRIPTS_DIR / "run_eval_scheduled.sh", scratch / "scripts" / "run_eval_scheduled.sh"
    )
    stub = scratch / "scripts" / "run_eval_local.sh"
    stub.write_text("#!/bin/bash\nexit 3\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    result = subprocess.run(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 3
    log_files = list((scratch / "logs" / "scheduled-runs").glob("run-*.log"))
    assert len(log_files) == 1
    log_text = log_files[0].read_text()
    # The wrapper must not have crashed trying to build/run the
    # notification command, and if delivery itself failed (e.g. no TCC
    # permission in this sandboxed test environment), that failure must
    # be a logged line, never silent.
    assert "syntax error" not in log_text.lower()


def test_run_eval_scheduled_notification_is_argument_passed_never_interpolated() -> None:
    """M4 static pin (F-6 fix, resilience re-review 2026-09-23).

    Replaces the assertion above as the thing that actually constrains
    the fix -- the reviewer ran this exact prior test against the
    PRE-FIX script (`c8d332d^`, `qu"ote` path) and it PASSED, because
    the pre-fix osascript call was `... >/dev/null 2>&1 || true`: it
    swallows osascript's diagnostic entirely, so "syntax error" never
    reaches the log either way. The test constrained nothing while
    looking like it did.

    This pins the real fix statically: the `on run argv` handler form
    is present, no `${...}` is interpolated inside any `-e` source
    (that's the string-terminating/injection vector), and the `|| true`
    escape hatch that silenced delivery failures is gone. Also: unlike
    the replaced test, this one needs no `osascript` and no
    `sys.platform == "darwin"` guard -- every CI runner here is
    `ubuntu-latest` (F-7), so the darwin-only predecessor never ran in
    CI at all; this static form runs everywhere.
    """
    text = (SCRIPTS_DIR / "run_eval_scheduled.sh").read_text()
    code_lines = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    code_text = "\n".join(code_lines)

    assert "on run argv" in code_text
    assert "|| true" not in code_text

    for line in code_lines:
        if "-e '" in line or '-e "' in line:
            assert "${" not in line, line


# ── H1 / L7: provision_golden_dataset.py never prints golden values ─────


def _load_provision_module() -> ModuleType:
    path = SCRIPTS_DIR / "provision_golden_dataset.py"
    spec = importlib.util.spec_from_file_location("provision_golden_dataset", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def provision_module() -> ModuleType:
    return _load_provision_module()


class _FakeFailingClient:
    """Stands in for `UrllibHttpClient` -- returns a 400 whose body
    echoes back a golden value, the exact Langfuse behavior H1 must never
    surface."""

    def __init__(self, *, host: str, public_key: str, secret_key: str) -> None:
        del host, public_key, secret_key

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        del method, body
        if path == "/api/public/dataset-items":
            return 400, {
                "message": "expectedOutput.fields.invoice_number.value INV-1001 is invalid"
            }
        raise AssertionError(f"unexpected path {path}")


def test_provision_golden_dataset_http_error_never_prints_the_response_body(
    monkeypatch: pytest.MonkeyPatch, provision_module: ModuleType
) -> None:
    monkeypatch.setattr(provision_module, "UrllibHttpClient", _FakeFailingClient)
    monkeypatch.setattr(
        provision_module, "provision_golden_schema", lambda client, dataset_name: None
    )
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.chdir(REPO_ROOT)  # so the default golden file path resolves

    stderr_buf = io.StringIO()
    with contextlib.redirect_stderr(stderr_buf):
        rc = provision_module.main(["--dataset", "test-ds", "--seed", "SEED-001"])

    assert rc == 1
    err = stderr_buf.getvalue()
    assert "INV-1001" not in err
    assert "invoice_number" not in err
    assert "HTTP 400" in err
    assert "withheld" in err


def test_provision_golden_dataset_dry_run_makes_no_network_call_and_withholds_values(
    monkeypatch: pytest.MonkeyPatch, provision_module: ModuleType
) -> None:
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run must never touch the network")

    monkeypatch.setattr(provision_module, "UrllibHttpClient", _explode)
    monkeypatch.setattr(provision_module, "provision_golden_schema", _explode)
    monkeypatch.chdir(REPO_ROOT)

    stdout_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf):
        rc = provision_module.main(["--dataset", "test-ds", "--seed", "SEED-001", "--dry-run"])

    assert rc == 0
    out = stdout_buf.getvalue()
    assert "INV-1001" not in out
    assert "Acme Office Supplies" not in out
    assert "field_names=" in out
    assert "invoice_number" in out  # field NAME is fine, the value is not
    assert "payload_sha256=" in out


def test_provision_golden_dataset_dry_run_show_values_reveals_full_payload(
    monkeypatch: pytest.MonkeyPatch, provision_module: ModuleType
) -> None:
    """`--show-values` is the explicit opt-in the review asked for --
    confirms the summarized default isn't accidentally the only path."""

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run must never touch the network")

    monkeypatch.setattr(provision_module, "UrllibHttpClient", _explode)
    monkeypatch.setattr(provision_module, "provision_golden_schema", _explode)
    monkeypatch.chdir(REPO_ROOT)

    stdout_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf):
        rc = provision_module.main(
            ["--dataset", "test-ds", "--seed", "SEED-001", "--dry-run", "--show-values"]
        )

    assert rc == 0
    assert "INV-1001" in stdout_buf.getvalue()


# ── H2 / M6 / L10: the workflow file's own shape ─────────────────────────


def test_regression_run_workflow_passes_untrusted_input_only_via_env(tmp_path: Path) -> None:
    """Static pin against H2 regressing -- no `${{ inputs.` or
    `${{ steps.params.outputs.` expression may appear inside a `run:`
    block in the credential-bearing job; those values must flow through
    `env:` instead."""
    import yaml

    workflow_path = REPO_ROOT / ".github" / "workflows" / "regression-run.yml"
    text = workflow_path.read_text()
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict)

    job = doc["jobs"]["regression"]
    for step in job["steps"]:
        run_block = step.get("run")
        if not run_block:
            continue
        assert "${{ inputs." not in run_block, step.get("name")
        assert "${{ steps.params.outputs." not in run_block, step.get("name")

    # M6: the two `uses:` actions in this credential-bearing job are
    # pinned to a commit SHA, not a mutable tag.
    for step in job["steps"]:
        uses = step.get("uses")
        if uses is None:
            continue
        ref = uses.split("@", 1)[1]
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses

    # L10: a timeout and a concurrency guard exist.
    assert job.get("timeout-minutes")
    assert doc.get("concurrency", {}).get("group")


def test_two_runs_started_in_the_same_second_keep_separate_files(tmp_path: Path) -> None:
    """DEBT-113. A fake `date` pins every call to ONE timestamp, so the
    same-second collision is certain rather than a race to win. Named by
    timestamp alone, the lock-busy second run deleted the holder's status
    file (the holder then reported a false infrastructure failure) and both
    wrote one log."""
    scratch = _make_scratch_checkout(
        tmp_path,
        run_eval_local_body="#!/bin/bash\nsleep 3\nexit 0\n",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/bin/bash\necho 20260927T120000Z\n")
    fake_date.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    first = subprocess.Popen(
        ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        time.sleep(1)
        second = subprocess.run(
            ["bash", str(scratch / "scripts" / "run_eval_scheduled.sh")],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    finally:
        first.wait(timeout=30)

    assert first.returncode == 0, "the holder's result must not be clobbered"
    assert second.returncode == 0, "the second run is a lock-busy skip"
    log_dir = scratch / "logs" / "scheduled-runs"
    assert len(list(log_dir.glob("run-*.log"))) == 2
    assert not list(log_dir.glob("FAILED-*.marker"))


def test_every_per_run_file_in_the_scheduled_script_is_keyed_by_run_key() -> None:
    """Gate F-5 (M45): the status file's own write-to-read window is too short
    for the same-second test to hit deterministically, so this pins the
    naming statically -- no per-run file may be keyed by the bare timestamp."""
    script = (SCRIPTS_DIR / "run_eval_scheduled.sh").read_text()
    for name in ('run-${RUN_KEY}.log', 'FAILED-${RUN_KEY}.marker', '.status-${RUN_KEY}"'):
        assert name in script, name
    for stale in ('run-${TS}.log', 'FAILED-${TS}.marker', '.status-${TS}"'):
        assert stale not in script, stale
