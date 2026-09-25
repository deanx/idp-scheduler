"""DEBT-84: the workspace lock's probe must not refuse a genuine start.

`is_busy()` answers "is a quota-spending job holding this workspace?" by
TAKING the `flock` and releasing it. The UI polls that answer, so the
probe runs continuously while a page is open -- and for the moment it
holds the lock, a real `start()` is refused with a 409 that means
nothing.

Measured before fixing (this file's `test_the_probe_does_not_refuse_a_
genuine_acquire` is the regression form of it): 64 spurious refusals in
5000 acquires, ~1.3%, with a single prober thread. DEBT-84 described the
race as "two `start()` calls within microseconds"; it is actually
probe-vs-start, which the UI generates by design.
"""

from __future__ import annotations

import pathlib
import threading
import time
from collections.abc import Iterator

import pytest

from idp_regression.ui import jobs, workspace


@pytest.fixture
def isolated_workspace(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        yield tmp_path
    finally:
        workspace.set_workspace(previous)


def test_the_probe_does_not_refuse_a_genuine_acquire(
    isolated_workspace: pathlib.Path,
) -> None:
    """The defect, in its measurable form.

    A prober hammers `is_busy()` while the main thread acquires the
    workspace lock the way `start()` does -- i.e. WITH the retry budget,
    since the retry is opt-in and `start()` is its only caller
    (`is_busy()` must stay a single non-blocking attempt or every poll
    against a busy workspace would block for the whole budget).

    Every refusal here is spurious: no job holds the workspace at any
    point. Measured before the fix: 64 refusals in 5000 acquires.
    """
    registry = jobs.JobRegistry()
    lock_path = workspace.workspace_root() / jobs.LOCK_FILE_NAME
    stop = threading.Event()
    refusals = 0

    def _probe() -> None:
        while not stop.is_set():
            registry.is_busy()
            # Zangado QA N-4: yield. Without this the prober makes no
            # syscall on a cheap `is_busy()` and starves the main thread
            # of the GIL -- a future optimisation there would turn this
            # into a mysteriously slow test rather than a failing one.
            time.sleep(0)

    prober = threading.Thread(target=_probe, daemon=True)
    prober.start()
    try:
        for _ in range(2000):
            lock = jobs._WorkspaceLock(lock_path)
            try:
                lock.acquire(retry_budget_seconds=jobs.START_LOCK_RETRY_SECONDS)
                lock.release()
            except jobs.WorkspaceBusyError:
                refusals += 1
    finally:
        stop.set()
        prober.join(timeout=5)

    assert refusals == 0, (
        f"{refusals} spurious refusals: is_busy()'s probe refused an acquire "
        "while no job held the workspace"
    )


def test_start_uses_the_retry_budget(
    isolated_workspace: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: the retry is useless if `start()` does not ask for it.

    Without this, a later edit could drop the keyword at the call site
    and the test above would still pass -- it exercises `acquire`
    directly, not the path a Run button actually takes.
    """
    captured: dict[str, object] = {}
    original = jobs._WorkspaceLock.acquire

    def _recording_acquire(
        self: jobs._WorkspaceLock, *, retry_budget_seconds: float = 0.0
    ) -> None:
        captured["budget"] = retry_budget_seconds
        original(self, retry_budget_seconds=retry_budget_seconds)

    monkeypatch.setattr(jobs._WorkspaceLock, "acquire", _recording_acquire)

    registry = jobs.JobRegistry()
    job = registry.start(
        "compare-versions",
        ["/bin/sh", "-c", "true", "--yes"],
        planned_extractions=1,
        approved_extractions=1,
    )
    deadline = time.time() + 10.0
    while registry.get(job.id).status in ("planning", "running") and time.time() < deadline:
        time.sleep(0.02)

    budget = captured.get("budget")
    # The EXACT budget, not merely "> 0" (Atchim /test F-3): a mutant
    # passing 1e-6 satisfies "positive" while retrying past nothing at
    # all, which is the defect with extra steps.
    assert budget == jobs.START_LOCK_RETRY_SECONDS, (
        f"start() acquired the workspace lock with retry_budget_seconds={budget!r}, "
        f"not the declared {jobs.START_LOCK_RETRY_SECONDS}; a passing is_busy() "
        "probe can then refuse a genuine run"
    )


def test_a_genuinely_held_workspace_is_still_refused(
    isolated_workspace: pathlib.Path,
) -> None:
    """The half that must NOT regress.

    Retrying past a probe must never become retrying past a real job.
    Two batches interleaving against one pin store is the entire reason
    the lock exists: `pin_document` writes goldens on a deterministic id
    and `verify_document` reads them back, so the second run can make
    the first verify against goldens it did not create.
    """
    lock_path = workspace.workspace_root() / jobs.LOCK_FILE_NAME
    held = jobs._WorkspaceLock(lock_path)
    held.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(jobs.WorkspaceBusyError):
            second = jobs._WorkspaceLock(lock_path)
            # WITH the budget -- this is `start()`'s path. Zangado QA N-1:
            # this used to call the default `acquire()`, so the retry loop
            # broke on its first attempt and the bound below constrained
            # nothing. A mutant multiplying the budget by 100 (a retry
            # that waits out a REAL job) passed this whole file.
            second.acquire(retry_budget_seconds=jobs.START_LOCK_RETRY_SECONDS)
        elapsed = time.monotonic() - started
    finally:
        held.release()

    # Two assertions, because either alone is escapable.
    #
    # An ABSOLUTE wall-clock bound. Bounding against
    # `START_LOCK_RETRY_SECONDS` was the obvious move and it is wrong:
    # the mutant that matters inflates that very constant, so the bound
    # moves with the thing it is supposed to bound and the test passes
    # while taking 25 seconds. (Found by re-running Zangado's M8 after
    # fixing N-1 -- the first fix swapped one vacuous assertion for
    # another.) This number is the requirement itself: a 409 answers an
    # HTTP request, so it has to be prompt in absolute terms, whatever
    # the budget is set to.
    assert elapsed < 2.0, f"a real conflict took {elapsed:.2f}s to refuse"

    # And the constant itself stays sane, so a budget large enough to
    # wait out a real job is caught at its source rather than only when
    # some timing assertion happens to notice.
    assert jobs.START_LOCK_RETRY_SECONDS <= 1.0, (
        f"the retry budget is {jobs.START_LOCK_RETRY_SECONDS}s -- long enough "
        "to start waiting out a genuine job rather than a passing probe"
    )


def test_is_busy_reports_true_while_a_job_holds_the_workspace(
    isolated_workspace: pathlib.Path,
) -> None:
    """The probe must still answer its actual question.

    A fix that made `is_busy()` cheap by always returning False would
    pass both tests above and break the Run button's only guard.
    """
    registry = jobs.JobRegistry()
    lock_path = workspace.workspace_root() / jobs.LOCK_FILE_NAME
    held = jobs._WorkspaceLock(lock_path)
    held.acquire()
    try:
        assert registry.is_busy() is True
    finally:
        held.release()
    assert registry.is_busy() is False


def test_is_busy_probes_with_a_single_non_blocking_attempt(
    isolated_workspace: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_busy()` must NOT retry -- pinned structurally, not by timing.

    `acquire`'s own docstring makes this a requirement: the UI polls this
    answer, so a probe that retried would block for the whole budget on
    every poll against a genuinely busy workspace.

    Atchim's `/test` gate (F-3) found this unpinned, and found it was a
    coverage REGRESSION I introduced: at `5ab2f41` the default-budget
    path happened to be exercised by the genuinely-held test, and the
    N-1 fix correctly moved that test onto the budget path without
    replacing what it had been covering incidentally. Two mutants then
    survived -- a 30-second DEFAULT budget, and `is_busy()` opting into
    the budget -- because the only consequence is slowness, and no test
    asserts on speed.

    Asserted on the call and on the signature rather than on elapsed
    time: a timing assertion here would be exactly the flaky, machine-
    dependent shape this file exists to get away from.
    """
    import inspect

    assert (
        inspect.signature(jobs._WorkspaceLock.acquire)
        .parameters["retry_budget_seconds"]
        .default
        == 0.0
    ), "acquire()'s default budget must stay 0.0 -- is_busy() relies on it"

    seen: list[float] = []
    original = jobs._WorkspaceLock.acquire

    def _recording_acquire(
        self: jobs._WorkspaceLock, *, retry_budget_seconds: float = 0.0
    ) -> None:
        seen.append(retry_budget_seconds)
        original(self, retry_budget_seconds=retry_budget_seconds)

    monkeypatch.setattr(jobs._WorkspaceLock, "acquire", _recording_acquire)
    jobs.JobRegistry().is_busy()

    assert seen == [0.0], (
        f"is_busy() probed with retry_budget_seconds={seen!r}; it must be a "
        "single non-blocking attempt or every poll against a busy workspace "
        "blocks for the whole budget"
    )
