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
