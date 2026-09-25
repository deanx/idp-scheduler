"""Lane F: the job-history record must survive a console that dies mid-run.

Both defects here were found by an independent QA pass (Zangado,
2026-09-25) while investigating a test that failed roughly once per
hundreds of full-suite runs. The flake was real, and it was the
observable edge of two production defects in `ui/jobs.py` -- not a test
problem.
"""

from __future__ import annotations

import json
import os
import threading
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


def _slow_argv(seconds: float) -> list[str]:
    """A job that stays running long enough to be caught mid-flight.

    `sh -c` rather than `sleep` directly: `start()` requires argv to end
    in `--yes` (the approval contract), and `sleep 5 --yes` exits
    immediately with a usage error. Under `sh -c CMD NAME` the trailing
    word becomes `$0`, so the mandatory `--yes` is carried without being
    interpreted.
    """
    return ["/bin/sh", "-c", f"sleep {seconds}", "--yes"]


def _wait_for_disk_status(
    record_path: Path, status: str, timeout: float = 5.0
) -> dict[str, object]:
    """Poll the RECORD, not the in-memory job.

    Zangado QA N-2: `_run_locked` publishes `running` in memory and
    persists a moment later, so a test that waits on memory and then
    reads disk is asserting "it reached disk before my next read" --
    which is the very ordering the original flake came from, inherited
    into the test written to catch it. Polling disk constrains the thing
    that actually matters: the transition reaches the record at all.
    """
    deadline = time.time() + timeout
    last: dict[str, object] = {}
    while time.time() < deadline:
        try:
            last = json.loads(record_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
            continue
        if last.get("status") == status:
            return last
        time.sleep(0.01)
    return last


def test_a_job_that_is_running_is_persisted_as_running(
    space: Path, documents: Path
) -> None:
    """DEBT-85(b): `running` was never written to disk, so INTERRUPTED
    could not fire in production.

    `start()` persists while `Job.status` still holds its default
    `planning`, and the transition to `running` in `_run_locked` wrote
    nothing. `load_history` only remaps `running`, so a job killed
    mid-batch came back as **`planning` forever** -- a non-terminal
    status, with no note that real extractions had been spent.

    The old pin for this (`test_a_job_interrupted_by_a_restart_is_named_
    not_resumed`) hand-wrote a `status: "running"` record, a shape
    production never produced. It constrained the setup, not the
    invariant, and passed throughout.
    """
    registry = jobs.JobRegistry()
    job = registry.start(
        "compare-versions", _slow_argv(5), planned_extractions=2, approved_extractions=2
    )

    record_path = space / jobs.HISTORY_DIR_NAME / f"{job.id}.json"
    on_disk = _wait_for_disk_status(record_path, "running")
    assert on_disk.get("status") == "running", (
        f"the running transition never reached disk (last saw "
        f"{on_disk.get('status')!r}) -- a console death here would surface "
        "this job as non-terminal forever"
    )


def test_a_job_interrupted_by_a_console_death_is_named_interrupted(
    space: Path, documents: Path
) -> None:
    """The real end-to-end path, driven through `start()`.

    A second `JobRegistry` stands in for the console that comes back
    after the first one died: it reads only what reached disk, which is
    exactly what a restarted console sees.
    """
    registry = jobs.JobRegistry()
    job = registry.start(
        "compare-versions", _slow_argv(5), planned_extractions=2, approved_extractions=2
    )
    record_path = space / jobs.HISTORY_DIR_NAME / f"{job.id}.json"
    assert _wait_for_disk_status(record_path, "running").get("status") == "running"

    restarted = jobs.JobRegistry()
    restarted.load_history()
    recovered = restarted.get(job.id)

    assert recovered.status != "running"
    assert recovered.status != "planning", (
        "a job that died mid-run came back as `planning` -- a non-terminal "
        "status that never resolves and never warns about spent quota"
    )
    assert recovered.summary["verdict"] == "INTERRUPTED"
    assert "not restarted automatically" in recovered.summary["note"]


def test_the_history_record_is_never_observed_half_written(
    space: Path, documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-85(a): `_persist` truncated the real file in place.

    The old code did `os.open(path, O_TRUNC)` and then wrote through a
    buffer, so between the truncate and the flush the record was EMPTY on
    disk. `load_history` skips a record it cannot parse
    (`json.JSONDecodeError -> continue`), *silently* -- so a reader
    landing in that window, or a console that died in it, lost a
    paid-for job's record with no error. Same "the evidence disappears"
    class as DEBT-83, one layer out.

    **The window is widened deliberately, and that is the point.** A
    first attempt at this test just hammered reads during a real job and
    passed against the BROKEN implementation -- the window is
    microseconds, so luck decided the verdict, which is the vacuous-test
    shape this project keeps finding. Slowing serialization makes the
    window certain, so the test constrains the invariant (a reader never
    sees a torn record) instead of the scheduler.
    """
    registry = jobs.JobRegistry()
    job = jobs.Job(id="abc123", kind="compare-versions", argv=["/bin/true", "--yes"])
    job.planned_extractions = 7
    registry._persist(job)

    record_path = space / jobs.HISTORY_DIR_NAME / f"{job.id}.json"
    first = json.loads(record_path.read_text(encoding="utf-8"))
    assert first["planned_extractions"] == 7

    real_dumps = json.dumps

    def _slow_dumps(obj: object, **kwargs: object) -> str:
        # Widen the truncate-to-flush window deterministically. Patched on
        # the `json` module itself, which `jobs` imports and calls through.
        time.sleep(0.3)
        return real_dumps(obj, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(json, "dumps", _slow_dumps)

    job.planned_extractions = 9
    writer = threading.Thread(target=registry._persist, args=(job,))
    writer.start()
    try:
        torn = 0
        reads = 0
        deadline = time.time() + 0.28
        while time.time() < deadline:
            try:
                text = record_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                torn += 1
                reads += 1
                continue
            reads += 1
            if not text.strip():
                torn += 1
                continue
            try:
                json.loads(text)
            except json.JSONDecodeError:
                torn += 1
    finally:
        writer.join(timeout=5)

    assert reads > 0, "never managed to read the record"
    assert torn == 0, (
        f"{torn} of {reads} reads saw a missing, empty or partial record while a "
        "rewrite was in flight; load_history skips those silently, so the job's "
        "record disappears"
    )
    assert json.loads(record_path.read_text(encoding="utf-8"))["planned_extractions"] == 9


def test_a_planning_record_left_by_a_dead_console_is_also_interrupted(
    space: Path,
) -> None:
    """DEBT-85(b) residual (Zangado QA N-2a), pinned directly.

    `_run_locked` publishes `running` in memory and persists a moment
    later. The window is one `fsync` wide now rather than the whole run,
    but a console dying inside it still leaves `planning` on disk — and
    `planning` is a NON-TERMINAL status, so before this it stayed
    unresolved forever with no note that quota had been spent.

    This has to be a unit pin on `load_history`. The end-to-end tests
    above cannot reach it: they wait for `running` to land on disk, which
    is exactly the case where the window did NOT bite. A mutant removing
    `planning` from `_DEAD_ON_LOAD` survived the whole UI suite until
    this test existed.

    Once the owning process is gone, `planning` and `running` are equally
    dead — neither will ever advance, because nothing holds the job.
    """
    history = space / jobs.HISTORY_DIR_NAME
    history.mkdir(parents=True, exist_ok=True)
    (history / "c0ffee.json").write_text(
        json.dumps({
            "id": "c0ffee",
            "kind": "compare-versions",
            "status": "planning",
            "planned_extractions": 40,
            "command": "/bin/echo x --yes",
            "summary": {},
        }),
        encoding="utf-8",
    )

    registry = jobs.JobRegistry()
    registry.load_history()
    recovered = registry.get("c0ffee")

    assert recovered.status not in ("planning", "running"), (
        f"a dead console's job came back as {recovered.status!r} -- a "
        "non-terminal status that never resolves"
    )
    assert recovered.summary["verdict"] == "INTERRUPTED"
    assert "not restarted automatically" in recovered.summary["note"]


def test_the_record_is_written_by_rename_and_never_truncated_in_place(
    space: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant, pinned STRUCTURALLY rather than by timing.

    Atchim's `/test` gate (F-2) showed the timing-based test above is
    narrower than its own docstring: a mutant that serializes FIRST and
    then truncates-and-writes in one syscall survives it 3/3, because
    the slowed `json.dumps` no longer widens anything. That mutant is
    still broken -- the window is one syscall instead of two, and
    `load_history` still skips a reader who lands in it silently -- so
    the timing test alone would let it ship.

    Write-then-rename IS the invariant, so assert exactly that: the real
    record path is never opened for writing, and `os.replace` lands on
    it. No sleeps, no widened window, nothing machine-dependent.
    """
    registry = jobs.JobRegistry()
    job = jobs.Job(id="d00d", kind="compare-versions", argv=["/bin/true", "--yes"])
    record_path = space / jobs.HISTORY_DIR_NAME / f"{job.id}.json"

    opened_for_write: list[str] = []
    replaced_onto: list[str] = []
    real_open, real_replace = os.open, os.replace

    def _watch_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == str(record_path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC):
            opened_for_write.append(str(path))
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    def _watch_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        replaced_onto.append(str(dst))
        real_replace(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", _watch_open)
    monkeypatch.setattr(os, "replace", _watch_replace)
    registry._persist(job)
    monkeypatch.undo()

    assert opened_for_write == [], (
        f"the live record was opened for writing ({opened_for_write}); a reader "
        "landing between truncate and flush sees a torn record, and load_history "
        "skips what it cannot parse SILENTLY"
    )
    assert str(record_path) in replaced_onto, (
        f"os.replace never landed on {record_path}; the write is not atomic"
    )
    assert json.loads(record_path.read_text(encoding="utf-8"))["id"] == "d00d"
