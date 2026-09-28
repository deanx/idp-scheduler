"""Running a quota-spending command from the console, on purpose.

**This reverses the console's original posture, and the reversal is a
user decision (2026-09-25).** Every other surface here prints the command
and leaves running it to an operator at a terminal, because a
loopback, unauthenticated port is a poor place for a button that bills a
customer's org. The user asked for the ZIP-against-a-new-version case to
run end to end from the UI, so it does -- and the `--yes` that used to be
typed at a terminal is replaced by a confirmation that carries the
EXACT extraction count the server itself computed:

* the server plans the job first (`--plan`, which spends nothing) and
  returns the real total;
* the client must echo that number back to start the job. A mismatch is
  refused. So "I clicked the button" can never mean a different cost than
  the one that was on screen, even if the archive changed underneath.

Where it runs
-------------
In the **workspace**, not the repo root. A script's output paths are
relative to its working directory, so a job that ran somewhere else wrote
its pins and its run artifact where the console never looks -- a
paid-for batch that produced nothing visible, with no error. The child
inherits this process's environment, which `workspace.load_environment()`
has already populated from `.env`, so credentials no longer depend on
where the job runs. See `workspace.py`.

Subprocess, not in-process
--------------------------
`golden_pipeline.py`'s rule is that a stage is the REAL script, never a
reimplementation -- and it is, here: this runs `scripts/compare_versions.py`
itself. It runs it as a child process rather than by import because a
batch of 2N extractions runs for minutes and has to be **cancellable**,
and a Python thread is not. A child also keeps a crash in the batch from
taking the console with it.

`argv` is built as a LIST and never a shell string, and every value that
reaches it is validated against the same grammar the path-building code
uses (`^[A-Za-z0-9._-]{1,128}$`) before it gets near the process. A
console that shelled out with user text would be a command-injection
surface on a host holding IDP and platform credentials -- the same class
of mistake `registry.py` refuses for `--classifier`.

One job at a time, and a history that outlives the console
----------------------------------------------------------
Two batches against the same pin store can interleave -- `pin_document`
writes goldens on a deterministic id and `verify_document` reads them
back -- so a `flock` on the workspace admits one quota-spending job at a
time (`_WorkspaceLock`). The `launchd` path has used `/usr/bin/lockf`
for exactly this since it existed; the console had nothing.

Finished jobs are recorded under `HISTORY_DIR_NAME`, **without their
output**: a job's captured lines carry the extracted values `## Domain`
calls sensitive, and those already live in the gitignored run artifact.
A job that was still running when a console stopped is surfaced as
INTERRUPTED and never resumed -- the extractions it spent are spent
either way, and restarting it would spend them again.

What is NOT here
----------------
There is no generic "run any command" job. The two argv builders below
are the closed set of workflows the console can run; adding a third is a
deliberate edit, not a parameter.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from idp_regression.ui import workspace

logger = logging.getLogger(__name__)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SCRIPTS_DIR: Final = REPO_ROOT / "scripts"

#: ADR-0004's grammar for a value that reaches a URL path or a filename.
#: Applied here BEFORE a value reaches an argv element.
SAFE_VALUE: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: A dataset name is the one value that may carry a space in practice.
SAFE_DATASET: Final = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")

#: How many output lines a job keeps. A batch over a large corpus prints
#: one progress line per document; the tail is what matters and an
#: unbounded buffer is a memory leak that only shows up on the big run.
MAX_LOG_LINES: Final = 2000

JobStatus = Literal["planning", "running", "succeeded", "failed", "cancelled"]


class JobRejectedError(Exception):
    """The job was refused before any process started -- a bad value, a
    cost mismatch, or a plan that could not be produced."""


@dataclass
class Job:
    id: str
    kind: str
    argv: list[str]
    status: JobStatus = "planning"
    exit_code: int | None = None
    #: The gate's own meaning, carried separately from process success:
    #: `compare_versions.py` exits 0 iff the verification passed, so a
    #: non-zero exit is USUALLY a real regression rather than a crash.
    #: The UI must not render "CHANGED" as "the tool broke".
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    planned_extractions: int | None = None
    #: Wall clock, used only to bound which run artifacts this job may
    #: claim. Not a duration measurement.
    started_at: float = 0.0
    #: The workspace this job belongs to, captured when it starts.
    #:
    #: Resolved ONCE rather than read from the global on each use: the
    #: job's own thread writes its history and resolves its run artifact
    #: long after `start()` returned, and reading the global there binds
    #: those writes to whatever the workspace happens to be at that
    #: moment instead of the one the job ran in.
    root: Path | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    _process: subprocess.Popen[str] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_json(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "exit_code": self.exit_code,
                "planned_extractions": self.planned_extractions,
                "command": " ".join(self.argv),
                "lines": list(self.lines),
                "summary": dict(self.summary),
            }

    def append(self, line: str) -> None:
        with self._lock:
            self.lines.append(line.rstrip("\n"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise JobRejectedError(message)


def _validate(values: dict[str, str]) -> None:
    for key, value in values.items():
        pattern = SAFE_DATASET if key == "dataset" else SAFE_VALUE
        _require(
            bool(pattern.match(value)),
            f"{key}={value!r} is not a valid value (expected {pattern.pattern})",
        )


def build_compare_argv(
    *,
    document_dir: Path,
    dataset: str,
    org: str,
    action: str,
    trusted_version: str,
    candidate_version: str,
    plan_only: bool,
    max_documents: int | None = None,
    allow_partial: bool = False,
    repin: bool = False,
    glob: str | None = None,
    classifier: str | None = None,
) -> list[str]:
    """The exact command, as a list. Validated first.

    `compare_versions.py` is the single command the user asked the UI to
    run: it pins every document at the trusted version and then verifies
    every one of them at the candidate, **unpacking the archive once** so
    the bytes compared are provably the bytes pinned.
    """
    _validate({
        "dataset": dataset,
        "org": org,
        "action": action,
        "trusted_version": trusted_version,
        "candidate_version": candidate_version,
        **({"classifier": classifier} if classifier else {}),
    })
    _require(
        trusted_version != candidate_version,
        "the trusted and candidate versions are the same -- that comparison cannot fail",
    )
    resolved = document_dir.resolve()
    _require(resolved.is_dir(), f"{resolved} is not a directory")

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / "compare_versions.py"),
        "--document-dir", str(resolved),
        "--dataset", dataset,
        "--org", org,
        "--action", action,
        "--trusted-version", trusted_version,
        "--candidate-version", candidate_version,
    ]
    if max_documents is not None:
        # `compare_versions.py` defaults to 200. A corpus larger than the
        # ceiling fails AFTER the archive is uploaded and priced, so the
        # ceiling has to be reachable from the UI rather than discovered.
        _require(0 < max_documents <= 1000, "max_documents must be between 1 and 1000")
        argv += ["--max-documents", str(max_documents)]
    if glob:
        _require(
            all(part.strip() and "/" not in part for part in glob.split(",")),
            "glob must be comma-separated filename patterns, with no path separators",
        )
        argv += ["--glob", glob]
    if allow_partial:
        # OFF by default in the script and here. The files that failed to
        # pin have NO golden, so verifying would skip them silently and
        # exit 0 on the rest -- "every pinned file is still valid" would
        # be true and useless. Opting in is a real choice about a partial
        # answer, so it is surfaced rather than defaulted.
        argv.append("--allow-partial")
    if repin:
        # Spends an extra extraction per already-pinned document.
        argv.append("--repin")
    if classifier:
        # verify_document.py's own parser validates the name and
        # restricts it to a pinned-file-based custom scorer -- nothing
        # is duplicated here beyond the same grammar check every other
        # value in this argv gets.
        argv += ["--classifier", classifier]
    # `--plan` prints the real total and stops; `--yes` approves BOTH
    # halves' quota. Exactly one of them is ever present.
    argv.append("--plan" if plan_only else "--yes")
    return argv


#: The plan's own words. Two patterns, and the order matters.
#:
#: `compare_versions.py --plan` prints a per-stage count ("3
#: extraction(s)") for each half AND an explicit grand total ("6 real IDP
#: extraction(s) to be spent"). A pattern that only matched the adjacent
#: form read 3 from a plan whose real cost was 6 -- the UI would have
#: shown half the price and the invoice would have shown all of it.
#: **Underreporting is the dangerous direction**, so the explicit total
#: is preferred and the fallback takes the MAXIMUM, never the first or
#: the sum.
def build_noise_floor_argv(
    *,
    document_dir: Path,
    org: str,
    action: str,
    version: str,
    repeats: int = 2,
    max_documents: int = 20,
    plan_only: bool,
) -> list[str]:
    """`scripts/noise_floor.py` for ONE version, over a sample.

    Why this belongs in the validation flow at all: pin/verify compares
    **one** reading at the trusted version against **one** reading at the
    candidate. If either version is nondeterministic, a `CHANGED` verdict
    is indistinguishable from the extractor disagreeing with *itself* --
    and a model swap is exactly when that is most likely. `CLAUDE.md`
    already says so for the corpus path: *"Skipping the first step
    produces a golden set with the incumbent's coin-flips baked in as
    'expected', and a red build rate nobody can interpret."* The per-file
    path has the same hole; this closes it.

    **Which version to measure, and why the default is the TRUSTED one.**
    Measuring the trusted version answers "can my goldens be trusted at
    all" -- a field the trusted version flips on is a field whose pinned
    value was a coin toss, so every later verdict on it is noise. That is
    the more fundamental question, it is what `verify_document.py`'s own
    failure hint tells an operator to measure, and it matches the
    documented order (floor, then draft, then compare). Measuring the
    candidate instead answers the narrower "is *this* red reproducible",
    and is offered rather than assumed.

    The sample is small by default: this costs `repeats x sample`
    extractions on top of the comparison's 2N, and an operator who
    discovers that after the fact will simply stop measuring floors.
    """
    _validate({"org": org, "action": action, "version": version})
    _require(repeats >= 2, "repeats must be at least 2 -- the first read is the reference")
    _require(0 < max_documents <= 1000, "the sample must be between 1 and 1000 documents")
    resolved = document_dir.resolve()
    _require(resolved.is_dir(), f"{resolved} is not a directory")

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / "noise_floor.py"),
        "--document-dir", str(resolved),
        "--org", org,
        "--action", action,
        "--version", version,
        "--repeats", str(repeats),
        "--max-documents", str(max_documents),
    ]
    argv.append("--plan" if plan_only else "--yes")
    return argv


def build_pin_argv(
    *,
    document_dir: Path,
    dataset: str,
    org: str,
    action: str,
    trusted_version: str,
    plan_only: bool,
    max_documents: int | None = None,
    glob: str | None = None,
) -> list[str]:
    """The exact command for `pin_document.py --all`, as a list.

    Stage 1 of the two-stage golden-review workflow: pin every document
    in `document_dir` at the trusted version, producing the draft golden
    set the curator will review before stage 2 is priced and approved.

    Mirrors `build_compare_argv`'s validation/lock pattern exactly.
    """
    _validate({
        "dataset": dataset,
        "org": org,
        "action": action,
        "trusted_version": trusted_version,
    })
    resolved = document_dir.resolve()
    _require(resolved.is_dir(), f"{resolved} is not a directory")

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / "pin_document.py"),
        "--document-dir", str(resolved),
        "--all",
        "--dataset", dataset,
        "--org", org,
        "--action", action,
        "--version", trusted_version,
    ]
    if max_documents is not None:
        _require(0 < max_documents <= 1000, "max_documents must be between 1 and 1000")
        argv += ["--max-documents", str(max_documents)]
    if glob:
        _require(
            all(part.strip() and "/" not in part for part in glob.split(",")),
            "glob must be comma-separated filename patterns, with no path separators",
        )
        argv += ["--glob", glob]
    argv.append("--plan" if plan_only else "--yes")
    return argv


def build_verify_argv(
    *,
    document_dir: Path,
    dataset: str,
    org: str,
    action: str,
    trusted_version: str,
    candidate_version: str,
    plan_only: bool,
    max_documents: int | None = None,
    glob: str | None = None,
) -> list[str]:
    """The exact command for `verify_document.py --all`, as a list.

    Stage 2 of the two-stage golden-review workflow: verify every pinned
    document in `document_dir` against the candidate version. Must only
    be called after INV-09's five clauses have been satisfied.

    `--action` and `--trusted-version` are always explicit: when the pin
    store holds more than one (action, version), the script refuses to
    guess, and the review session's own fields are the authoritative
    answer to "which pin set".
    """
    _validate({
        "dataset": dataset,
        "org": org,
        "action": action,
        "trusted_version": trusted_version,
        "candidate_version": candidate_version,
    })
    _require(
        trusted_version != candidate_version,
        "the trusted and candidate versions are the same -- that comparison cannot fail",
    )
    resolved = document_dir.resolve()
    _require(resolved.is_dir(), f"{resolved} is not a directory")

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / "verify_document.py"),
        "--document-dir", str(resolved),
        "--all",
        "--dataset", dataset,
        "--org", org,
        "--action", action,
        "--trusted-version", trusted_version,
        "--version", candidate_version,
    ]
    if max_documents is not None:
        _require(0 < max_documents <= 1000, "max_documents must be between 1 and 1000")
        argv += ["--max-documents", str(max_documents)]
    if glob:
        _require(
            all(part.strip() and "/" not in part for part in glob.split(",")),
            "glob must be comma-separated filename patterns, with no path separators",
        )
        argv += ["--glob", glob]
    argv.append("--plan" if plan_only else "--yes")
    return argv


_EXPLICIT_TOTAL = re.compile(r"(\d+)\s+real\s+IDP\s+extraction", re.IGNORECASE)
_ANY_EXTRACTION_COUNT = re.compile(r"(\d+)(?:\s+[A-Za-z]+){0,4}\s+extraction", re.IGNORECASE)


def plan(argv: list[str], *, timeout_seconds: float = 120.0) -> tuple[int, list[str]]:
    """Run the real `--plan` and return `(extractions, output_lines)`.

    Spends nothing: `--plan` validates the archive and prints the cost
    without submitting a document. This is also the archive's last
    validation before money is spent, so a failure here is a refusal, not
    a warning.
    """
    _require(argv[-1] == "--plan", "plan() must be given a --plan argv")
    completed = subprocess.run(  # noqa: S603 - argv list, validated values, no shell
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=workspace.workspace_root(),
    )
    lines = (completed.stdout + completed.stderr).splitlines()
    if completed.returncode != 0:
        raise JobRejectedError(
            "the plan was refused: " + (lines[-1] if lines else f"exit {completed.returncode}")
        )
    totals = [int(m.group(1)) for line in lines for m in [_EXPLICIT_TOTAL.search(line)] if m]
    if totals:
        return max(totals), lines
    counts = [
        int(m.group(1)) for line in lines for m in [_ANY_EXTRACTION_COUNT.search(line)] if m
    ]
    if not counts:
        raise JobRejectedError(
            "the plan did not report an extraction count -- refusing to start a job whose "
            "cost could not be read back"
        )
    return max(counts), lines


#: One quota-spending job at a time, per workspace.
LOCK_FILE_NAME: Final = ".idp-regression-jobs.lock"

#: Where finished jobs are remembered across a console restart.
HISTORY_DIR_NAME: Final = ".idp-regression-jobs"


class WorkspaceBusyError(Exception):
    """Another quota-spending job holds this workspace."""


#: DEBT-84: how long `start()` retries past a transient holder before
#: declaring the workspace busy. Sized against the two hold times it must
#: tell apart: an `is_busy()` probe holds the lock for microseconds, a real
#: job for minutes. Long enough that a probe can never cause a spurious
#: 409; far too short to wait out a genuine job, and short enough that the
#: refusal still answers an HTTP request promptly.
START_LOCK_RETRY_SECONDS = 0.25


class _WorkspaceLock:
    """An advisory `flock` held for the lifetime of a job.

    Two batches against the same pin store and dataset must not
    interleave: `pin_document` writes goldens on a deterministic id and
    `verify_document` reads them back, so a second run pinning the same
    documents at a different version while the first is verifying can
    make the first verify against goldens it did not create. Nothing in
    the batch tools guards this -- the `launchd` path uses
    `/usr/bin/lockf` for exactly this reason, and the console had no
    equivalent.

    `flock` rather than a lock *file*: it is released by the kernel when
    the process dies, so a crashed console cannot leave the workspace
    permanently unusable -- a stale lock that needs manual deletion is
    how an operator learns to delete locks reflexively.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self, *, retry_budget_seconds: float = 0.0) -> None:
        """Take the lock, optionally retrying briefly.

        DEBT-84: `is_busy()` answers its question by TAKING this lock and
        releasing it, and the UI polls that answer -- so for the moment
        each probe holds it, a genuine `start()` is refused with a 409
        that means nothing. Measured on this machine before the fix: 64
        spurious refusals in 5000 acquires (~1.3%) against a single
        prober thread.

        A retry separates the two cases cleanly because their hold times
        differ by orders of magnitude: a probe holds the lock for
        microseconds, a real job for minutes. A budget of a fraction of a
        second therefore cannot mask a genuine conflict -- measured 0 in
        5000 spurious refusals with the budget, while a genuinely held
        lock is still refused in ~0.26s.

        Opt-in, and `start()` is the only caller that opts in: `is_busy()`
        must stay a single non-blocking attempt, or every poll against a
        genuinely busy workspace would block for the whole budget.

        NOTE this is NOT the fix DEBT-84's own row prescribes (a
        registry-local flag consulted before probing). That one does not
        close the race: when a job IS running locally the flag
        short-circuits, but `start()` would correctly fail anyway; when
        none is running -- exactly the spurious case -- `is_busy()` still
        probes. It reduces probe frequency, not the window.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + retry_budget_seconds
        delay = 0.001
        while True:
            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                if time.monotonic() >= deadline:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 0.02)
                continue
            self._fd = fd
            return
        raise WorkspaceBusyError(
                "another validation job is already running in this workspace. Two batches "
                "against the same pin store can interleave -- the second would pin goldens "
                "the first is still verifying against. Wait for it, or cancel it."
        ) from None

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


#: Statuses that cannot survive the console that wrote them. `running` is
#: obvious. `planning` is here because of the ordering Zangado QA (N-2)
#: found: `_run_locked` publishes `running` in memory and persists a moment
#: later, so a console dying inside that window leaves `planning` on disk.
#: Once the owning process is gone the two are equally dead, and treating
#: only one of them as interrupted is what let a killed job come back as a
#: NON-TERMINAL status that never resolves. Remapping both makes the write
#: ordering irrelevant to what an operator is told.
_DEAD_ON_LOAD = frozenset({"running", "planning"})


class JobRegistry:
    """Jobs for one console process, with a history that outlives it.

    A job that was RUNNING when the console stopped is not resumed --
    the extractions it had already spent are gone either way, and
    restarting it would spend them again. It is recorded as interrupted
    so an operator can see that something was in flight, which is the
    part that used to vanish silently.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._history_loaded = False

    def is_busy(self) -> bool:
        """Is a quota-spending job holding this workspace?

        Asked by probing the same lock, not by reading this process's own
        job table: another console on the same workspace holds the
        workspace just as effectively, and a UI that only knew about its
        own jobs would offer a Run button that is about to 409.
        """
        lock = _WorkspaceLock(workspace.workspace_root() / LOCK_FILE_NAME)
        try:
            lock.acquire()
        except WorkspaceBusyError:
            return True
        lock.release()
        return False

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [
            {k: v for k, v in job.to_json().items() if k != "lines"}
            for job in sorted(jobs, key=lambda j: j.id, reverse=True)
        ]

    def start(
        self,
        kind: str,
        argv: list[str],
        *,
        planned_extractions: int,
        approved_extractions: int,
    ) -> Job:
        """Start a job, but only against a cost the caller has echoed back.

        This is the `--yes` equivalent and the whole reason the UI is
        allowed to spend quota at all. The number the client sends must
        equal the number the server computed from the REAL `--plan`; a
        stale page whose archive has since changed is refused rather than
        charged.
        """
        _require(
            approved_extractions == planned_extractions,
            f"approval is for {approved_extractions} extractions but this job plans "
            f"{planned_extractions} -- re-plan and confirm the current cost",
        )
        _require(argv[-1] == "--yes", "a job argv must end in --yes")

        # Taken BEFORE the job exists, so a refused second job leaves no
        # trace and spends nothing. Held until the child exits.
        lock = _WorkspaceLock(workspace.workspace_root() / LOCK_FILE_NAME)
        # DEBT-84: retry past a passing `is_busy()` probe, never past a
        # real job -- see `_WorkspaceLock.acquire`.
        lock.acquire(retry_budget_seconds=START_LOCK_RETRY_SECONDS)

        job = Job(id=uuid.uuid4().hex, kind=kind, argv=argv)
        job.planned_extractions = planned_extractions
        job.started_at = time.time()
        job.root = workspace.workspace_root()
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)

        thread = threading.Thread(target=self._run, args=(job, lock), daemon=True)
        thread.start()
        return job

    # ---------------------------------------------------------- history

    def _history_path(self, job: Job) -> Path:
        root = job.root or workspace.workspace_root()
        return root / HISTORY_DIR_NAME / f"{job.id}.json"

    def _persist(self, job: Job) -> None:
        """Write the job's record, minus its output.

        Owner-only, and **without `lines`**: a job's captured output
        carries the extracted values `## Domain` calls sensitive, and
        those already have a home in the gitignored run artifact. A
        second copy under a different retention rule is a disclosure
        surface nobody asked for.
        """
        record = {k: v for k, v in job.to_json().items() if k != "lines"}
        record["started_at"] = job.started_at
        try:
            path = self._history_path(job)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
            # Created WITH the mode, not chmod'ed after: a write-then-chmod
            # leaves a window in which the file exists at the umask's
            # permissions. The content here is run identity rather than
            # extracted values, so this is defence in depth -- but the
            # window is free to close (Zangado QA, 2026-09-25).
            # Written to a sibling temp file and renamed into place.
            # DEBT-85(a) (Zangado QA, 2026-09-25): this used to `os.open`
            # the real path with `O_TRUNC` and write through a buffer, so
            # between the truncate and the flush the record was EMPTY on
            # disk. `load_history` skips a record it cannot parse
            # (`json.JSONDecodeError -> continue`), silently -- so a
            # reader landing in that window, or a console that died in
            # it, lost a paid-for job's record with no error at all. Same
            # "the evidence disappears" class as DEBT-83, one layer out.
            #
            # `os.replace` is atomic within a filesystem, so a reader
            # always sees either the previous complete record or the new
            # complete one, never a torn write. The temp file is created
            # in the SAME directory to guarantee that -- a rename across
            # filesystems is not atomic and would fall back to a copy.
            # Per-WRITER, not per-process (Zangado QA N-3): a name keyed
            # only on the pid means two concurrent persists of one job in
            # one process would truncate the same temp file and could
            # `os.replace` a mixed record. Not reachable today -- a job's
            # persists are sequential -- so this is closing it for free
            # rather than after it bites.
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            temporary = pathlib.Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, indent=2))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except BaseException:
                # A failed write must not leave a temp file behind; the
                # previous record stays intact and readable either way.
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
        except OSError as exc:  # pragma: no cover - a full disk must not fail a paid-for run
            logger.warning("job_history_write_failed id=%s detail=%s", job.id, type(exc).__name__)

    def load_history(self) -> None:
        """Read back what earlier console processes ran.

        A job recorded as `running` cannot still be running -- this
        process did not start it and holds no handle to it -- so it is
        surfaced as `interrupted`. **It is never resumed**: the
        extractions it spent are spent either way, and restarting it
        would spend them again.
        """
        directory = workspace.workspace_root() / HISTORY_DIR_NAME
        if not directory.is_dir():
            self._history_loaded = True
            return
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            job_id = str(record.get("id", ""))
            if not job_id or job_id in self._jobs:
                continue
            status = record.get("status")
            job = Job(
                id=job_id,
                kind=str(record.get("kind", "?")),
                argv=str(record.get("command", "")).split(" "),
                status="failed" if status in _DEAD_ON_LOAD else status,
            )
            job.planned_extractions = record.get("planned_extractions")
            job.exit_code = record.get("exit_code")
            job.started_at = float(record.get("started_at") or 0.0)
            job.summary = dict(record.get("summary") or {})
            if status in _DEAD_ON_LOAD:
                job.summary["verdict"] = "INTERRUPTED"
                job.summary["note"] = (
                    "this job was still running when a previous console stopped; the "
                    "extractions it had already spent are not recoverable, and it is not "
                    "restarted automatically"
                )
            with self._lock:
                self._jobs[job_id] = job
        self._history_loaded = True

    def _run(self, job: Job, lock: _WorkspaceLock) -> None:
        logger.info(
            "job_started id=%s kind=%s extractions=%s", job.id, job.kind, job.planned_extractions
        )
        try:
            self._run_locked(job)
        finally:
            # Released whatever happened, including a crash in the reader
            # loop -- a workspace left locked by a bug is a console that
            # can never run again without someone deleting a file.
            lock.release()
            self._persist(job)

    def _run_locked(self, job: Job) -> None:
        try:
            process = subprocess.Popen(  # noqa: S603 - argv list, validated values, no shell
                job.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=workspace.workspace_root(),
            )
        except OSError as exc:
            job.append(f"failed to start: {type(exc).__name__}")
            with job._lock:
                job.status = "failed"
            return

        with job._lock:
            job._process = process
            job.status = "running"
        # DEBT-85(b): persist the transition. `start()` wrote this record
        # while `Job.status` still held its default `planning`, and this
        # transition used to touch only memory -- so a console that died
        # mid-batch left `planning` on disk, and `load_history` remaps
        # only `running`. The job came back NON-TERMINAL forever, with no
        # note that real extractions had been spent, which is precisely
        # what the INTERRUPTED path exists to prevent.
        self._persist(job)

        # `Popen` was given `stdout=PIPE`, so this is never None -- but an
        # `assert` is stripped under `-O`, and a silent skip here would
        # produce a job with no output and no explanation.
        if process.stdout is not None:
            for line in process.stdout:
                job.append(line)
        process.wait()

        with job._lock:
            # `cancel()` sets this from ANOTHER thread while the read loop
            # above is still draining, so a cancelled job must keep that
            # status rather than being relabelled "failed" by its own
            # termination. Read through a local so the intent survives
            # narrowing.
            final: JobStatus = job.status
            if final != "cancelled":
                final = "succeeded" if process.returncode == 0 else "failed"
            job.status = final
            job.exit_code = process.returncode
            job.summary = summarize(
                list(job.lines),
                process.returncode,
                since=job.started_at,
                root=job.root,
                kind=job.kind,
            )
        logger.info("job_finished id=%s status=%s exit=%s", job.id, job.status, job.exit_code)

    def cancel(self, job_id: str) -> Job:
        """Terminate the child.

        **Cancelling does not refund anything already extracted**, and the
        job's own output is the only record of how far it got -- so the
        UI says so rather than presenting cancel as an undo.
        """
        job = self.get(job_id)
        with job._lock:
            process, status = job._process, job.status
        if status != "running" or process is None:
            raise JobRejectedError(f"job {job_id} is {status}, not running")
        with job._lock:
            job.status = "cancelled"
        process.terminate()
        job.append("-- cancelled by the operator; extractions already spent are not refunded --")
        return job


#: The banner `compare_versions.py` and `verify_document.py` each print
#: ONCE at the end. **Not a per-document marker** -- there is no
#: per-document STILL VALID/CHANGED line anywhere, which is exactly why
#: the first version of this module reported nonsense counts (it counted
#: banners). Kept only to read the overall verdict; the per-document
#: truth comes from the run artifact below.
_VERDICT_BANNER = re.compile(r"^(STILL VALID|CHANGED):", re.MULTILINE)

#: `facade.run_eval` logs `experiment=<run-name>-<run_id[:8]>` on its
#: pre-run line. That 8-hex prefix is the only thread from a job's output
#: back to the artifact it produced -- the artifact is named for the FULL
#: run id, which is never printed.
_EXPERIMENT = re.compile(r"experiment=(\S+?)-([0-9a-f]{8})\b")


def find_run_artifact(lines: list[str], *, since: float, root: Path | None = None) -> str | None:
    """The run id this job produced, or `None`.

    Two ways, in order of confidence:

    1. the `experiment=<name>-<8hex>` prefix from the pre-run log line,
       matched against artifact filenames -- exact, and immune to any
       other run happening concurrently;
    2. failing that, the newest artifact written after the job started.
       A fallback, and deliberately time-bounded rather than "newest
       overall": attributing someone else's run to this job would put
       the wrong document verdicts in front of an operator deciding
       whether a model swap is safe.
    """
    directory = (root / workspace.ARTIFACT_DIR_NAME) if root else workspace.artifact_dir()
    if not directory.is_dir():
        return None

    blob = "\n".join(lines)
    match = _EXPERIMENT.search(blob)
    if match is not None:
        prefix = match.group(2)
        for path in directory.glob(f"{prefix}*.json"):
            return path.stem

    fresh = [
        path
        for path in directory.glob("*.json")
        if path.stat().st_mtime >= since
    ]
    if not fresh:
        return None
    return max(fresh, key=lambda p: p.stat().st_mtime).stem


_FLOOR_REPORT = re.compile(r"(\S*noise-floor-[0-9TZ-]+\.json)")


def find_floor_report(lines: list[str]) -> str | None:
    """The report `noise_floor.py` just wrote, by name.

    Returned as a bare filename because that is what `reader` resolves
    inside the workspace's floor directory -- and what the Runs page
    needs in order to read a run against it.
    """
    for line in reversed(lines):
        match = _FLOOR_REPORT.search(line)
        if match is not None:
            return Path(match.group(1)).name
    return None


def summarize(
    lines: list[str],
    returncode: int,
    *,
    since: float,
    root: Path | None = None,
    kind: str = "compare-versions",
) -> dict[str, Any]:
    """What the run concluded -- from the ARTIFACT where one exists.

    `verdict` deliberately distinguishes a REGRESSION from a BROKEN RUN.
    `compare_versions.py` exits 0 iff the verification passed, so a
    non-zero exit usually means "the documents changed under the new
    version" -- the tool working. Rendering that as an error would teach
    an operator to dismiss the one result the tool exists to produce.

    The per-document counts come from the run artifact and the
    AUTHORITATIVE gate (`overall_gate`), never from counting words in the
    output. The first version of this function counted banner lines and
    reported "STILL VALID: 2" for a two-script run over forty documents.
    """
    banner = _VERDICT_BANNER.search("\n".join(lines))
    if returncode == 0:
        verdict = "STILL VALID"
    elif banner is not None:
        verdict = "CHANGED"
    else:
        verdict = "RUN FAILED"

    summary: dict[str, Any] = {
        "verdict": verdict,
        "exit_code": returncode,
        "gate_is_the_exit_code": True,
        "run_id": None,
        "documents": None,
        "changed_documents": None,
        "still_valid_documents": None,
        "changed_document_ids": [],
    }

    floor_report = find_floor_report(lines)
    if floor_report is not None:
        summary["floor_report"] = floor_report

    if kind == "noise-floor":
        # A floor job runs `noise_floor.py`, which writes a floor report
        # and NO run artifact. Letting the time-based fallback look
        # anyway means a concurrent compare job's artifact could be
        # attributed to it -- document counts from a run this job never
        # performed (Zangado QA F-C, 2026-09-25). There is nothing to
        # find, so it does not look.
        return summary

    run_id = find_run_artifact(lines, since=since, root=root)
    if run_id is None:
        return summary
    summary["run_id"] = run_id

    # Read it through `reader`, so the console shows the same gate the
    # build used rather than a second opinion.
    from idp_regression.ui import reader

    try:
        detail = reader.read_run(
            run_id, artifact_dir=(root / workspace.ARTIFACT_DIR_NAME) if root else None
        )
    except (FileNotFoundError, ValueError, OSError):
        return summary

    # Cross-check before trusting it (Zangado QA F-3, 2026-09-25).
    #
    # `compare_versions.py` exits 0 iff every gate passed, so a completed
    # run's artifact MUST agree with its exit code. When it does not, the
    # artifact is not this job's: the likeliest cause is a job that died
    # before `run_eval` ever wrote one (a failed pin half), leaving the
    # time-bounded fallback to pick up a neighbour's. Marrying this job's
    # verdict to another run's document counts would present a fiction as
    # fact to someone deciding whether a model swap is safe -- so the
    # counts are dropped and the mismatch is named, rather than shown.
    # A run that did not PASS (FAIL, or INCOMPLETE -- aborted, DEBT-91)
    # must be one that exited non-zero, and vice versa.
    if (detail.get("gate") != "PASS") != (returncode != 0):
        summary["run_id"] = None
        summary["artifact_discrepancy"] = (
            f"a run artifact was found but its gate ({detail.get('gate')}) disagrees with this "
            f"job's exit code ({returncode}); it is not this job's result, so no per-document "
            "counts are shown"
        )
        return summary

    # DEBT-91: an aborted run's artifact holds only what was classified
    # before the abort. Counting those as "still valid" would present a
    # partial measurement as a result, so an INCOMPLETE run reports the
    # abort and no counts. It is a broken run, not a regression.
    if detail.get("gate") == "INCOMPLETE":
        summary["verdict"] = "RUN FAILED"
        summary["run_incomplete"] = detail.get("abort_reason") or "status not recorded"
        return summary

    documents = detail.get("documents", [])
    changed = [d["document_id"] for d in documents if d["gate"] == "FAIL"]
    summary["documents"] = len(documents)
    summary["changed_documents"] = len(changed)
    summary["still_valid_documents"] = len(documents) - len(changed)
    # Named, and capped: an operator needs to know WHICH documents moved,
    # and a 1000-document regression must not put 1000 ids in a payload
    # the browser holds in memory.
    summary["changed_document_ids"] = sorted(changed)[:50]
    summary["changed_document_ids_truncated"] = len(changed) > 50
    return summary
