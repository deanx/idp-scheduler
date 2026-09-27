"""`run_eval` facade (T-01.4.1, T-01.4.11, T-01.4.2, T-01.4.5, T-01.4.6,
ADR-0004, ADR-0005 Decisions #8/#9).

⚠️ **Slice boundary (S-01.4-KICKOFF.md), updated for this batch.** This
function now builds the FULL run: the pre-run chain, the per-document
loop, and the post-loop record phase (S-01.4-KICKOFF.md, TP-40;
ADR-0005 #9)::

    load_dotenv() -> credential validation (N6) -> IDP config validation
    -> IDP_DOCUMENT_DIR validation -> platform construction
    -> get_dataset (dataset_fetch_failed) -> schema-drift (schema_drift)
    -> empty-set guard (empty_set) -> N28 structural validation
       (malformed_golden)
    -> quota-ceiling pre-flight guard (quota_ceiling_exceeded, ADR-0004
       A10, 2026-09-22) -- zero quota spent on a refusal
    -> golden_version = hash_dataset(...) + run_id/experiment_name
    -> per document, sequentially: resolve document_id -> path
       -> extract -> classify -> overall_gate -> build_score_inputs
       -> append DocumentRecord (NO platform write in this loop, #9 step 2;
          any typed failure aborts the WHOLE run immediately, INV-06)
    -> a single post-loop record_run(...) (#9 step 4)
    -> mark_run_status("complete", ...) (best-effort, #9 step 5)
    -> exit 0 iff every gate was PASS and no error occurred (INV-08: the
       exit code is a pure function of the in-process gates, never of
       what landed on the platform)

**This is a change from the T-01.4.1 batch's boundary**, which
deliberately did NOT construct a platform client (DEBT-30/C-1/R-3 fix).
That constraint applied ONLY to the credential-presence check itself --
it was never a claim that `run_eval` would stay client-free forever.

**Still NOT built** (next batch, T-01.4.7/.9): the CT-04 contract
test and the e2e harness. Observability (T-01.4.8, NFR N10) IS built as
of this batch -- a run-start line (item count), a run-end line (outcome,
exit code, pass/fail counts, elapsed on a monotonic clock -- emitted at
EVERY exit point, not only on success), and per-document elapsed
attached to the SAME log line that already carries `document_id` (never
a separate untethered timing line). Hard rule, asserted by
`tests/orchestration/test_facade.py`: no golden value, extracted value,
token or path ever reaches any of these lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Sequence
from typing import Any, cast

from idp_regression.adapter.errors import (
    IDPAdapterError,
    IDPAuthenticationError,
    IDPConfigurationError,
    IDPExecutionFailedError,
    IDPPollTimeoutError,
    MalformedIDPOutputError,
)
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter, make_idp_adapter
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.classifier.registry import UnknownClassifierError
from idp_regression.classifier.registry import resolve as resolve_classifier
from idp_regression.classifier.types import MalformedActualError, MalformedGoldenError, VerdictMap
from idp_regression.orchestration.bootstrap import (
    MissingCredentialError,
    validate_platform_credentials,
)
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.errors import AbortReason, RunAbortedError
from idp_regression.orchestration.log_sanitize import (
    format_execution_failed_status_for_log,
    frame_location,
    redact_secrets_for_log,
)
from idp_regression.orchestration.prerun import (
    check_empty_set,
    check_schema_drift,
    validate_golden_set,
)
from idp_regression.orchestration.run_artifact import write_run_artifact
from idp_regression.orchestration.run_naming import compose_experiment_name, generate_run_id
from idp_regression.platform import make_platform
from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    ExperimentRecordFailedError,
    FlushFailedError,
    PlatformConfigurationError,
    ScoreWriteFailedError,
)
from idp_regression.platform.hashing import hash_dataset
from idp_regression.platform.scoring import build_score_inputs
from idp_regression.platform.types import (
    DatasetItem,
    DocumentRecord,
    PlatformAdapter,
    RunMetadata,
    RunStatus,
)

logger = logging.getLogger(__name__)

#: ⚠️ Updated 2026-09-22 (user decision): `GOLDEN_DATASET_NAME` is no
#: longer read anywhere in this codebase, not even as a fallback --
#: `--dataset` is a required CLI flag with no environment fallback
#: (`cli.py::main`), and `run_eval` itself takes `dataset_name` as a
#: required parameter. `GOLDEN_DATASET_NAME` survives only as a
#: test-harness convenience var (see `.env.example`), unread by the app.
#: The `GOLDEN_DATASET_NAME_VAR` constant that used to live here (ADR-0004
#: amendment T-01.4.12 A6 / DEBT-48) is retired along with the fallback it
#: named -- nothing in `src/` reads it anymore.

#: The local directory holding the document files under test
#: (ADR-0004 Flow step 5a: "the orchestrator resolves `IDP_DOCUMENT_DIR /
#: {item.document_id}` to a local path"). Read and validated fail-closed
#: (N6 shape), before any network call.
IDP_DOCUMENT_DIR_VAR = "IDP_DOCUMENT_DIR"


class _PathContainmentViolationError(Exception):
    """Raised by `_resolve_document_path` when `document_id` would resolve
    outside `IDP_DOCUMENT_DIR` -- an absolute `document_id`, a `..`
    traversal, or a symlink escape (security fix, 2026-09-21: N28
    validates JSON shape only, never filesystem safety). Carries the
    offending `document_id` as `.document_id` -- callers must route it
    through `sanitize_for_log` before it ever reaches a log line or
    exception message (INV-02); the resolved/candidate path itself is
    NEVER attached to this exception or logged anywhere."""

    def __init__(self, document_id: str) -> None:
        super().__init__("document_id resolved outside IDP_DOCUMENT_DIR")
        self.document_id = document_id


def _resolve_document_path(document_dir: str, document_id: str) -> str:
    """Resolve `document_id` to a local path under `IDP_DOCUMENT_DIR`
    (ADR-0004 Flow step 5a). The orchestrator owns this resolution; the
    adapter takes only an already-resolved path (INV-01 -- the platform
    stores `document_id` only, and no path blob ever reaches it; this
    function is purely local composition plus a containment check, no
    I/O of its own beyond `os.path.realpath`'s symlink resolution).

    **Containment (security fix, 2026-09-21)**: `document_id` is platform
    (golden-set) content -- data a Curator or anyone with platform write
    access controls. ⚠️ Corrected 2026-09-21 (R-1 gate finding): this
    docstring used to claim `document_id` was "already pre-run-validated
    as a non-empty string by `validate_golden_set` (N28)" -- that was
    false. `validate_golden_set` (`prerun.py`) validates `item["golden"]`
    only; it never inspects `document_id`, so a non-`str`, empty, or
    NUL-containing `document_id` reached here unvalidated and escaped as
    a raw `TypeError`/`ValueError` (2026-09-21 live repro). This
    function's FIRST statement below now rejects exactly those three
    shapes itself, as `_PathContainmentViolationError`, before any `os.path`
    call. Both the configured root and the candidate are then resolved
    to real absolute paths (`os.path.realpath` -- this also
    resolves a symlink to its real target, so a symlink planted *inside*
    `document_dir` that points *outside* it is caught, not just a literal
    `..` in `document_id`), and the candidate must land strictly inside
    the root (`root + os.sep` prefix -- the root itself is never a valid
    resolution, since it is a directory, not a document). An absolute
    `document_id` is rejected up front for a clearer failure signal, even
    though the containment check below would also catch it (`os.path.join`
    discards `document_dir` entirely when its second argument is
    absolute, so an unchecked absolute `document_id` would otherwise
    resolve to itself verbatim).

    Raises `_PathContainmentViolationError` (never returns a path outside the
    root) -- the caller (`run_eval`'s per-document loop) converts this
    into a typed `path_containment_violation` abort (CT-04: 0-vs-non-zero
    exit-code contract stays intact) and logs the `document_id` only via
    `sanitize_for_log`, never the resolved/candidate path.
    """
    if not isinstance(document_id, str) or not document_id or "\x00" in document_id:
        # R-1: reject non-`str`, empty, and NUL-containing `document_id`
        # up front -- none of these are safe to hand to `os.path.isabs`/
        # `os.path.realpath` below, which raise raw `TypeError`/
        # `ValueError` on exactly these shapes instead of the typed
        # `_PathContainmentViolationError` this function otherwise always
        # raises. `document_id` may not be a `str` at all here (the type
        # hint is aspirational, not enforced at this boundary), so no
        # f-string/`sanitize_for_log` call touches it before this check.
        raise _PathContainmentViolationError(document_id if isinstance(document_id, str) else "")

    if os.path.isabs(document_id):
        raise _PathContainmentViolationError(document_id)

    root = os.path.realpath(document_dir)
    candidate = os.path.realpath(os.path.join(document_dir, document_id))
    if not candidate.startswith(root + os.sep):
        raise _PathContainmentViolationError(document_id)
    return candidate


class _DocumentSelectionError(Exception):
    """A `--document` selector matched no item, or matched ambiguously.

    A pre-run refusal, like the quota ceiling above it: raised before any
    submit, so it costs zero quota, and carries only `document_id`-shaped
    text the caller must still route through `sanitize_for_log`
    (INV-02) -- selectors are operator input, not golden content, but
    they are echoed back into a log line and get the same treatment.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def select_items(
    items: list[DatasetItem], selectors: Sequence[str] | None
) -> list[DatasetItem]:
    """The subset of `items` a `--document` selector names, in dataset
    order (ADR-0004 A8/A9's spirit: what a run measured is visible in the
    invocation that produced it).

    `None`/empty selectors return `items` unchanged -- the filter is
    strictly additive, and a run without it behaves exactly as it always
    has.

    Each selector is matched EXACTLY first, then as a substring; a
    substring that matches more than one document is refused rather than
    guessed, because the alternative is spending an extraction on a file
    the operator did not name. A selector matching nothing is refused for
    the same reason: a filtered run that silently measured zero documents
    would exit 0 and read as a pass.
    """
    if not selectors:
        return items
    by_id = {item["document_id"]: item for item in items}
    chosen: dict[str, DatasetItem] = {}
    for selector in selectors:
        if selector in by_id:
            chosen[selector] = by_id[selector]
            continue
        matches = [document_id for document_id in by_id if selector in document_id]
        if not matches:
            raise _DocumentSelectionError(f"no dataset item matches {selector!r}")
        if len(matches) > 1:
            raise _DocumentSelectionError(
                f"{selector!r} matches {len(matches)} items ({', '.join(sorted(matches)[:5])}"
                f"{', ...' if len(matches) > 5 else ''}) -- name one exactly"
            )
        chosen[matches[0]] = by_id[matches[0]]
    # Dataset order, not selector order: the run's per-document sequence
    # must not depend on how the flags were typed.
    return [item for item in items if item["document_id"] in chosen]


def _validate_dataset_shape(dataset: object) -> None:
    """HARDEN-01 GAP-1 (2026-09-21): the orchestrator used to trust the
    `Dataset`/`DatasetItem` shape the `PlatformAdapter` returned at face
    value -- a non-list `items`, a missing `items` key, an item missing
    `document_id`, or a non-`str` `document_id` all raised a raw
    `KeyError`/`TypeError`/`AttributeError` deep inside `check_empty_set`,
    `validate_golden_set`, or the per-document loop, escaping `run_eval`
    entirely (breaking its own `-> int` contract, per Branca's `/harden`
    report). Validated here, BEFORE the schema-drift/empty-set/N28 chain
    even runs, and mapped to `DatasetFetchFailedError` -- the same
    reason `get_dataset` itself already uses for a malformed response,
    since a structurally-invalid dataset is exactly that class of
    failure regardless of which layer detects it. This checks SHAPE
    only (types, key presence) -- it never inspects `golden` content
    (N28's job) and never a `value` (INV-02)."""
    if not isinstance(dataset, dict):
        raise DatasetFetchFailedError("get_dataset returned a non-dict dataset")
    items = dataset.get("items")
    if not isinstance(items, list):
        raise DatasetFetchFailedError("dataset 'items' is missing or not a list")
    for item in items:
        if not isinstance(item, dict):
            raise DatasetFetchFailedError("a dataset item is not a dict")
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            # DEBT-57 A-4 (Branca /harden re-run, 2026-09-21): a
            # whitespace-only item_id used to pass this guard (only the
            # credential path in bootstrap.py applied `.strip()`) --
            # matches the N6 credential shape now, same reasoning: a
            # value that is technically present but practically empty
            # must fail the same way an actually-empty value does.
            raise DatasetFetchFailedError("a dataset item has a missing/non-string item_id")
        if not isinstance(item.get("document_id"), str) or not item["document_id"]:
            raise DatasetFetchFailedError("a dataset item has a missing/non-string document_id")

    # DEBT-83: a duplicate `document_id` is individually valid on every
    # per-item guard above and collapses TWO structures downstream, both
    # last-write-wins and both silent:
    #   * `verdict_maps[document_id] = verdicts` -- the run artifact keeps
    #     only the LAST occurrence. The artifact is the only local record
    #     of the values behind a verdict, so if an EARLIER occurrence was
    #     the one that failed, the artifact disagrees with the exit code:
    #     a red build whose own evidence shows nothing wrong. The Epic E
    #     console reads that artifact as the authoritative job result.
    #   * `select_items`' `by_id` comprehension -- `--document <id>`
    #     silently resolves to whichever item happened to come last.
    # Refused rather than de-duplicated: two items sharing a document_id
    # carry DIFFERENT goldens (that is the only reason to have two), so
    # there is no correct one to pick and guessing measures something
    # nobody asked for. Pre-run, before any submit, at zero IDP quota --
    # the same posture as every other guard in this chain.
    seen: set[str] = set()
    for item in items:
        document_id = item["document_id"]
        if document_id in seen:
            raise DatasetFetchFailedError(
                "the dataset has more than one item with document_id "
                f"{sanitize_for_log(document_id)} -- each document must appear once "
                "(a run artifact and a --document selector are both keyed by it)"
            )
        seen.add(document_id)


def _mark_run_status_best_effort(
    platform: PlatformAdapter,
    run_id: str,
    status: RunStatus,
    *,
    action_id: str,
    action_version: str,
    golden_version: str,
    golden_dataset_name: str,
) -> None:
    """ADR-0004 #14 (Atchim non-blocking debt, carried forward by
    ADR-0005 #9 steps 3/4/5): the `run_status` marker write is
    best-effort -- ONE attempt, never retried. The exit code is the CI
    gate's truth regardless of whether this marker lands on the
    platform; a failure here must never itself abort or crash
    `run_eval`. Never logs the caught exception's message (INV-02 -- a
    platform error body could echo request content).

    `golden_dataset_name` (A6 / DEBT-48): the fourth INV-04 field,
    carried alongside the other three so the marker also answers "which
    named golden set was this run measured against".

    ⚠️ Widened 2026-09-21 (Atchim gate, GAP-5's fifth site,
    live-reproduced): this guarded only `except Exception`, unlike every
    other catch-all in this module (all widened for GAP-5 to also catch
    `asyncio.CancelledError`, a `BaseException` subclass). The tail
    `status="complete"` call site (after a fully successful run) sits
    OUTSIDE every try-block in `run_eval`, so a `CancelledError` raised
    here on that call escaped `run_eval` raw. Now matches every other
    catch-all's shape: `KeyboardInterrupt`/`SystemExit` still propagate,
    everything else is swallowed best-effort."""
    try:
        platform.mark_run_status(
            run_id,
            status,
            action_id=action_id,
            action_version=action_version,
            golden_version=golden_version,
            golden_dataset_name=golden_dataset_name,
        )
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 - best-effort by design, must never raise
        logger.warning(
            "run_eval: mark_run_status(%s) failed (best-effort, not retried)",
            sanitize_for_log(status),
        )


#: ADR-0004 A10 (2026-09-22), MVP override of A10's own "required, no
#: default" text (user decision): a round, obviously-arbitrary number so
#: its arbitrariness is impossible to miss -- this bounds a runaway loop
#: (an unexpectedly huge golden set, a future resubmit path), it is NOT
#: derived from the org's real IDP allotment, and it must never be
#: presented as a real quota control. See `cli.py`'s `--max-documents-
#: per-run` help text and the run-start log line, which always records
#: the value actually in force.
DEFAULT_MAX_DOCUMENTS_PER_RUN = 1000

#: What a run tells the evaluation platform (DEBT-18 REVERSED 2026-09-25,
#: user decision -- "we need to have as much information as possible at
#: [the platform], as [it] is the information point here"; PII is handled
#: at the IDP action, which can be configured not to parse it, and by a
#: filter or a later deletion pass over the platform).
#:
#:   ``full``           verdicts AND the expected/actual/confidence behind
#:                      them -- score comments, the trace span's `detail`,
#:                      and the experiment item's `expected_output`.
#:   ``verdicts-only``  the pre-reversal payload (DEBT-18 option B,
#:                      2026-09-19): verdict literals, `document_id` and
#:                      run metadata, and nothing else. Kept reachable so
#:                      the stricter posture is one flag away, not a
#:                      rewrite.
PLATFORM_VALUE_MODES = ("full", "verdicts-only")
DEFAULT_PLATFORM_VALUES = "full"


def run_eval(
    action_id: str,
    version: str,
    run_name: str,
    dataset_name: str,
    org_id: str,
    max_documents_per_run: int = DEFAULT_MAX_DOCUMENTS_PER_RUN,
    documents: Sequence[str] | None = None,
    classifier: str | None = None,
    platform_values: str = DEFAULT_PLATFORM_VALUES,
) -> int:
    """Run the baseline regression for `action_id` at `version` over the
    named golden set (`dataset_name`), writing per-field + gate scores to
    a run derived from `run_name` on the platform (ADR-0004, ADR-0005 #9).

    `dataset_name` (ADR-0004 amendment T-01.4.12 A6 / DEBT-48; tightened
    2026-09-22 -- user decision): a required, plain parameter -- like
    `action_id`/`version` above, this function does NOT read
    `GOLDEN_DATASET_NAME` from the environment, and neither does the CLI
    anymore: `--dataset` is a required flag with no env fallback
    (`cli.py::main`), exactly mirroring `--action`'s own required-flag
    shape. A caller of this public function directly (bypassing the CLI)
    must supply `dataset_name` explicitly.

    `org_id` (ADR-0004 A9, 2026-09-22 -- amends A8): likewise a required,
    plain parameter, passed straight through to `make_idp_adapter(org_id)`
    below -- `IDP_ORG_ID` is read by no production code path anymore. An
    action is addressed by `(org, action, version)`, and the credential
    alone cannot tell a wrong org id from a right one (IDP access is
    granted at the business-group level) -- so the org must be visible in
    the invocation, exactly like `action_id`/`dataset_name`.

    `max_documents_per_run` (ADR-0004 A10, 2026-09-22): a pre-flight
    ceiling on documents submitted this run, checked once `get_dataset`
    has returned and before any IDP submit call (a refusal costs zero
    quota). Defaults to `DEFAULT_MAX_DOCUMENTS_PER_RUN` -- an MVP guard
    rail against a runaway loop, deliberately NOT derived from the org's
    real IDP allotment (see the constant's own docstring and `cli.py`'s
    `--max-documents-per-run` help text).

    Returns a process exit code: `0` iff every document's gate was
    `PASS` and no error occurred anywhere in the run; non-zero on any
    gate `FAIL` or any abort (CT-04 -- 0 vs non-zero only, the abort
    *reason* is logged for observability and never fragments the exit
    code, `orchestration/errors.py`).

    `load_dotenv()` runs FIRST, before any credential is read or any SDK
    client is constructed (ADR-0004 Flow step 1, INV-05). Credential
    PRESENCE is validated (`bootstrap.validate_platform_credentials`, N6)
    before `make_platform()` is ever called, so a missing/empty var is
    still caught with zero network calls -- `make_platform()` itself is
    called strictly after that presence check, as defence in depth
    against the one thing presence validation cannot catch: a value that
    resolves but is internally contradictory (`PlatformConfigurationError`,
    the platform's own base-URL/host split-brain guard; N24 -- this
    module names no vendor).

    Callers are expected to have already validated `action_id` (UUID) and
    `version` (`^[A-Za-z0-9._-]{1,64}$`) at the CLI boundary
    (ADR-0004 amendment 2026-09-19) -- this function trusts its inputs
    and does not re-validate their shape.
    """
    started_at = time.monotonic()

    def _log_run_end(
        outcome: str, exit_code: int, *, pass_count: int = 0, fail_count: int = 0
    ) -> None:
        """T-01.4.8 (NFR N10): emitted at EVERY exit point of `run_eval`,
        not only on success (Zangado's S-01.2 note) -- `outcome`/
        `exit_code`/the counts are fixed, non-secret values, and
        `elapsed_seconds` comes from stdlib's monotonic clock, so this
        line needs no `sanitize_for_log` pass (nothing here is a golden
        value, an extracted value, a token, or a path).

        DEBT-57 A-5 (Branca /harden re-run, 2026-09-21): this is reached
        from all 13 of `run_eval`'s exit points with no guard of its own
        -- a raising log handler (a full disk, a broken formatter
        installed by the caller's own logging config, ...) at any one of
        them used to propagate raw out of `run_eval`, breaking the
        `-> int` exit-code contract on the observability call itself.
        Swallowed best-effort, matching every other best-effort site in
        this module (`_mark_run_status_best_effort`) -- `KeyboardInterrupt`/
        `SystemExit` still propagate, everything else does not."""
        with contextlib.suppress(Exception, asyncio.CancelledError):
            logger.info(
                "run_eval: run_end outcome=%s exit_code=%s pass_count=%d fail_count=%d "
                "elapsed_seconds=%.3f",
                outcome,
                exit_code,
                pass_count,
                fail_count,
                time.monotonic() - started_at,
            )

    # C-1 (Atchim gate, 2026-09-21) widened after a `/harden` re-run
    # (Branca) found the first merge (`get_dataset` +
    # shape/schema/empty-set/N28) closed only ONE of five raw-escape
    # seams: `make_platform`, `make_idp_adapter` and
    # `validate_platform_credentials` each still sat under their OWN
    # narrow tuple with no catch-all, and the window between the two
    # former try-blocks -- `hash_dataset`, `generate_run_id`,
    # `compose_experiment_name` -- had no try at all. ONE try-block now
    # spans every pre-run step, `validate_platform_credentials` through
    # `compose_experiment_name`, so an untyped exception raised ANYWHERE
    # in that chain (a transport timeout, a version-drift attribute
    # error, ...) is caught by the SAME trailing catch-all instead of
    # escaping through whichever seam's tuple didn't happen to name it.
    # No `run_id` exists at any point in this block (it is the last
    # thing generated inside it), so no branch here ever writes the
    # best-effort marker -- unlike the in-loop/record-phase catch-all
    # further down, which always has a `run_id` to mark.
    #
    # ⚠️ Widened again 2026-09-21 (Atchim gate, live-reproduced): this try
    # used to START one line too late -- `load_dotenv()` itself sat
    # OUTSIDE it, so an unreadable/undecodable `.env` (e.g. a
    # permission-denied `OSError`) escaped `run_eval` raw. It also used
    # to END too early, right after `compose_experiment_name()` -- the
    # "pre-run checks passed" log line below (specifically its own
    # `len(dataset["items"])` call) sat in the WINDOW between this
    # try-block and the per-document loop's, with only `_abort`'s
    # definition next to it. The try now starts at `load_dotenv()` and
    # ends only after that log line is fully emitted, so nothing sits
    # between the two catch-alls except the `_abort` closure's own
    # definition (never executed at definition time).
    try:
        load_dotenv()

        validate_platform_credentials()

        idp_adapter: MuleSoftIDPAdapter = make_idp_adapter(org_id)

        # A6: `dataset_name` is caller-supplied now (see the docstring
        # above) -- still fail-closed on an empty/whitespace-only value,
        # the same N6 shape the env-read version used, so a caller that
        # passes "" through doesn't reach any network call either. Not
        # an exception (nothing to catch below) -- a plain early return.
        dataset_name = dataset_name.strip()
        if not dataset_name:
            logger.error("run_eval: dataset_name must not be empty")
            _log_run_end("aborted", 1)
            return 1

        document_dir = (os.environ.get(IDP_DOCUMENT_DIR_VAR) or "").strip()
        if not document_dir:
            logger.error("run_eval: missing required env var %s", IDP_DOCUMENT_DIR_VAR)
            _log_run_end("aborted", 1)
            return 1

        # Resolved before the platform is even constructed: an unknown
        # `--classifier` must cost nothing, and a run may never silently
        # fall back to a comparison nobody asked for.
        comparison = resolve_classifier(classifier)

        if platform_values not in PLATFORM_VALUE_MODES:
            logger.error(
                "run_eval: unknown platform_values %s (expected: %s)",
                sanitize_for_log(platform_values),
                ", ".join(sorted(PLATFORM_VALUE_MODES)),
            )
            _log_run_end("aborted", 1)
            return 1
        record_values = platform_values == "full"

        platform: PlatformAdapter = make_platform()

        dataset = platform.get_dataset(dataset_name)
        # GAP-1: validate shape BEFORE trusting it structurally anywhere
        # else -- same except-clause, same reason, as a malformed
        # `get_dataset` response.
        _validate_dataset_shape(dataset)
        # Pinned pre-run order (S-01.4-KICKOFF.md, TP-40): schema-drift,
        # THEN empty-set, THEN N28 structural validation. An empty
        # dataset whose schema ALSO drifted reports schema_drift,
        # because check_schema_drift runs first and never looks at
        # `items`.
        check_schema_drift(dataset)
        check_empty_set(dataset)
        validate_golden_set(dataset)

        # ADR-0004 A10 (2026-09-22): pre-flight IDP quota ceiling -- item
        # count is known now (get_dataset already ran) and no submit has
        # happened yet, so a refusal here costs ZERO quota. Deliberately
        # placed BEFORE golden_version/run_id below (no run exists yet),
        # so this is a pre-run guard per A7: log, run_end outcome=aborted,
        # return 1, NO run_status marker -- not a RunAbortedError/`_abort()`
        # call, which both require a `run_id` to mark.
        #
        # ⚠️ User override of A10's own text (2026-09-22, MVP decision):
        # A10 specifies this ceiling as REQUIRED with NO DEFAULT ("a
        # guessed ceiling is worse than none because it looks like a
        # control"). The user overrode that for the MVP: `--max-
        # documents-per-run` is OPTIONAL with a high, deliberately
        # arbitrary default (`DEFAULT_MAX_DOCUMENTS_PER_RUN`, `cli.py`) --
        # this bounds a runaway loop (a golden set that unexpectedly holds
        # thousands of items, or a future resubmit path), it is NOT
        # derived from the org's real IDP allotment and must never be
        # presented as one. B-3 (`/signoff`, N27) stays OPEN as a
        # validation point, not closed by this default.
        # Filtered AFTER the whole golden set has been fetched, shape-checked,
        # drift-checked and N28-validated: a `--document` run must not be able
        # to skip a validation the unfiltered run performs, or a broken golden
        # set could be worked around one document at a time.
        try:
            selected_items = select_items(dataset["items"], documents)
        except _DocumentSelectionError as exc:
            # Pre-run refusal, same shape as the quota ceiling below: log,
            # run_end outcome=aborted, return 1, NO run_status marker (no
            # run exists yet, ADR-0004 A7). Never exit 0 -- a filtered run
            # that measured nothing must not read as a pass.
            logger.error(
                "run_eval: document_filter_no_match %s", sanitize_for_log(exc.detail)
            )
            _log_run_end("aborted", 1)
            return 1
        
        if documents:
            logger.info(
                "run_eval: document filter selected %d of %d item(s): %s",
                len(selected_items),
                len(dataset["items"]),
                sanitize_for_log(", ".join(i["document_id"] for i in selected_items)),
            )

        # The ceiling guards QUOTA, so it counts what will actually be
        # submitted -- the selected items, not the whole dataset. With no
        # filter the two are identical and this is unchanged.
        item_count = len(selected_items)
        if item_count > max_documents_per_run:
            logger.error(
                "run_eval: quota_ceiling_exceeded item_count=%d max_documents_per_run=%d",
                item_count,
                max_documents_per_run,
            )
            _log_run_end("aborted", 1)
            return 1

        # T-01.4.6 (INV-04): golden_version is a content hash over the
        # SAME `dataset["items"]` object just validated above -- no
        # second fetch (TOCTOU guard, ADR-0005 #7). `hash_dataset` takes
        # `list[dict[str, Any]]` (it hashes whatever it is given
        # verbatim, no opinion on item shape -- see its own docstring);
        # `DatasetItem` is structurally a dict, so this is a
        # shape-preserving cast, not an unsafe one.
        # Over the FULL item set, never the selection: `golden_version`
        # identifies the state of the GOLDEN SET (INV-04), so a filtered run
        # and a full run of the same golden set must report the same version.
        # What the run actually covered is the `document filter` log line
        # above plus `items=` below.
        golden_version = hash_dataset(cast(list[dict[str, Any]], dataset["items"]))
        run_id = generate_run_id()
        experiment_name = compose_experiment_name(run_name, run_id)

        logger.info(
            "run_eval: pre-run checks passed run=%s experiment=%s action=%s "
            "version=%s golden_version=%s golden_dataset_name=%s items=%d "
            "max_documents_per_run=%d classifier=%s platform_values=%s",
            sanitize_for_log(run_name),
            sanitize_for_log(experiment_name),
            sanitize_for_log(action_id),
            sanitize_for_log(version),
            sanitize_for_log(golden_version),
            sanitize_for_log(dataset_name),
            len(selected_items),
            max_documents_per_run,
            comparison.name,
            platform_values,
        )
    except UnknownClassifierError as exc:
        # Pre-run refusal: log, run_end outcome=aborted, exit 1, no status
        # marker (no run exists yet, ADR-0004 A7). The valid names are
        # printed because a typo is the likely cause.
        logger.error(
            "run_eval: unknown_classifier %s (available: %s)",
            sanitize_for_log(exc.name),
            ", ".join(exc.available),
        )
        _log_run_end("aborted", 1)
        return 1
    except MissingCredentialError as exc:
        logger.error("run_eval: missing required env var %s", exc.variable_name)
        _log_run_end("aborted", 1)
        return 1
    except (RuntimeError, IDPConfigurationError, ValueError, PlatformConfigurationError) as exc:
        # `ValueError` closes a Required gate finding (2026-09-21,
        # live-reproduced): `MuleSoftIDPAdapter.__init__` raises a raw
        # `ValueError` when `success_statuses` is not a subset of
        # `terminal_statuses` (adapter/idp_client.py:109), which escaped
        # this except-block and broke the `-> int` / ADR-0004 exit-code
        # contract.
        #
        # ⚠️ Corrected 2026-09-21 (Branca `/harden` GAP-6, live-reproduced,
        # 8 cases): this clause used to log `sanitize_for_log(str(exc))`
        # on the assumption that only two vetted constructors
        # (`make_idp_adapter`'s and `make_platform`'s own guards) ever
        # raised these types here -- true when this except-clause was
        # first written, but 7d7aed3 later widened the SAME try-block to
        # span the whole pre-run chain, including `check_schema_drift`
        # and `validate_golden_set`, both of which handle golden content
        # and can raise a bare `RuntimeError`/`ValueError` built from it.
        # `sanitize_for_log` only escapes/quotes (`json.dumps`) -- it does
        # NOT redact -- so a golden value or a credential sentinel
        # embedded in one of those messages reached the log verbatim
        # aside from quoting. `RecursionError` (a `RuntimeError`
        # subclass) made this worse: a stack overflow deep in golden
        # validation landed in THIS clause, not the safe type-name-only
        # catch-all below. Now logs only `type(exc).__name__` plus the
        # last traceback frame's location (R-2 shape, same as every
        # catch-all in this function) -- never `str(exc)`, for ANY
        # raiser of these four types, vetted or not.
        logger.error(
            "run_eval: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1)
        return 1
    except DatasetFetchFailedError as exc:
        logger.error("run_eval: dataset_fetch_failed: %s", sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1)
        return 1
    except RunAbortedError as exc:
        logger.error("run_eval: %s: %s", exc.reason, sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1)
        return 1
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - HARDEN-01 GAP-1/GAP-5
        # `type(exc).__name__` plus the last traceback frame's location
        # (R-2) only, never `str(exc)` (INV-02: an unanticipated
        # exception's message is not vetted the way every typed one in
        # this codebase is).
        #
        # ⚠️ Widened 2026-09-21 (Branca `/harden` GAP-5): `asyncio.
        # CancelledError` is a `BaseException` subclass (Python 3.8+), NOT
        # an `Exception` subclass -- it used to slip straight through
        # `except Exception`, escaping this catch-all raw. Added
        # explicitly (never a bare `except BaseException`, which would
        # also swallow `KeyboardInterrupt`/`SystemExit` -- those must
        # keep propagating).
        logger.error(
            "run_eval: unexpected pre-run error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1)
        return 1

    def _abort(reason: AbortReason, document_id: str | None, detail: str) -> RunAbortedError:
        """Write the best-effort `run_status=aborted` marker (a run now
        exists -- `run_id` was just generated above, unlike the pre-run
        guards) then build (not raise -- see call sites) the `RunAbortedError`
        to propagate, so every caller's line is `raise _abort(...)` and
        stays a single statement, consistent with the pre-run guards'
        style. `detail` MUST already be `sanitize_for_log`-clean; this
        function logs it via a `%s` placeholder, never interpolates it
        into the exception message itself (INV-02 -- see `RunAbortedError`'s
        own docstring).

        DEBT-54 A-1 (Branca /harden, 2026-09-21): `detail` is
        escaped/quoted by `sanitize_for_log` below but that alone never
        REMOVES content -- every current call site is vetted (typed
        adapter/platform error messages, or a literal), so this is safe
        today only by construction, per HARDEN-01's own framing. `detail`
        is now also run through `redact_secrets_for_log` first, so a
        credential value that ever reached this field (a future raiser
        this file's authors didn't vet, an upstream error that echoes
        request content) is stripped before it is escaped, not merely
        escaped."""
        _mark_run_status_best_effort(
            platform,
            run_id,
            "aborted",
            action_id=action_id,
            action_version=version,
            golden_version=golden_version,
            golden_dataset_name=dataset_name,
        )
        logger.error(
            "run_eval: %s document_id=%s detail=%s",
            reason,
            sanitize_for_log(document_id) if document_id is not None else "<none>",
            sanitize_for_log(redact_secrets_for_log(detail)),
        )
        return RunAbortedError(reason, f"per-document/record-phase abort: {reason}")

    # ADR-0005 #9 step 2: sequential, in-process loop. NO platform write
    # happens inside it -- every gate is computed and every DocumentRecord
    # is fully built BEFORE record_run is ever called once, after the
    # loop (INV-08). ADR-0005 #9 step 3 / INV-06: any typed failure here
    # aborts the WHOLE run immediately -- no remaining document is
    # processed, and the loop's own `except` blocks below are the only
    # path out of it.
    records: list[DocumentRecord] = []
    # ADR-0007 Option E (2026-09-23): the full CT-02 verdict map per
    # document, accumulated here purely so it can be handed to
    # `write_run_artifact` below -- it is NEVER passed to
    # `build_score_inputs`/`record_run` (those already have `verdicts`
    # locally, unchanged) and never reaches the platform. Collected
    # incrementally (not only on success) so an aborted run still leaves
    # an artifact for whatever documents were classified before the
    # abort -- exactly the run a human is most likely to want "what did
    # it extract instead" for.
    verdict_maps: dict[str, VerdictMap] = {}
    any_gate_failed = False
    passed_count = 0
    failed_count = 0
    submits_made = 0
    try:
        for item in selected_items:
            document_started_at = time.monotonic()
            document_id = item["document_id"]
            golden = item["golden"]
            try:
                document_path = _resolve_document_path(document_dir, document_id)
            except _PathContainmentViolationError as exc:
                raise _abort(
                    "path_containment_violation", exc.document_id, "containment check failed"
                ) from None

            # ADR-0004 A10: "documents submitted, counted at the submit
            # call site" -- one `extract()` call is one IDP execution is
            # one document, so the counter increments immediately before
            # the call that actually submits, not before path resolution
            # (a path-containment rejection never reaches the IDP) and
            # not derived from `len(dataset["items"])`, so any future
            # resubmit/retry path is counted by construction. The
            # assertion is a BUG DETECTOR, not a policy (A10): the
            # pre-flight check above already refused any dataset whose
            # item count exceeds the ceiling, so `submits_made` can never
            # legitimately reach it mid-loop -- if it ever fires, the
            # pre-flight computation was wrong, not the operator's budget.
            submits_made += 1
            # HARDEN 2026-09-24 (ruff S101, once the `S` family was finally
            # selected -- DEBT-41): this was an `assert`, which `python -O`
            # STRIPS. A bug detector that disappears depending on how the
            # interpreter was invoked is not a detector; and this one sits on
            # the path that spends real IDP quota, so its absence would be
            # discovered by an over-spend rather than by a failure. Raised
            # explicitly instead -- same fail-closed direction, same message,
            # but it cannot be optimised away.
            #
            # Deliberately NOT a `RunAbortedError`/`AbortReason`: the pre-flight
            # ceiling refusal is a different, operator-facing path with its own
            # reason (`quota_ceiling_exceeded`). Reaching here means the
            # pre-flight computation itself was wrong (A10: "a bug detector,
            # not a policy"), which is ours, not the operator's budget.
            if submits_made > max_documents_per_run:
                raise RuntimeError(
                    "internal invariant violated: submits_made exceeded "
                    "max_documents_per_run despite the pre-flight guard"
                )
            try:
                actual = idp_adapter.extract(document_path, action_id, version)
            except IDPAuthenticationError as exc:
                raise _abort("auth_failure", document_id, str(exc)) from None
            except IDPPollTimeoutError as exc:
                raise _abort("unknown_status_timeout", document_id, str(exc)) from None
            except MalformedIDPOutputError as exc:
                raise _abort("malformed_actual", document_id, f"{exc.reason}: {exc}") from None
            except IDPExecutionFailedError as exc:
                # `.status` is IDP-controlled -- already sanitized at
                # construction (adapter/errors.py) and again here through
                # the shared T-01.4.14 helper (defense in depth, N5).
                raise _abort(
                    "hard_failure", document_id, format_execution_failed_status_for_log(exc)
                ) from None
            except IDPAdapterError as exc:
                # Every other typed adapter error: submit failure, a
                # non-2xx poll response, retry-budget exhaustion, or an
                # ambiguous/missing poll status.
                raise _abort("hard_failure", document_id, str(exc)) from None

            try:
                verdicts = comparison.classify(golden, actual)
                gate = comparison.gate(verdicts)
            except MalformedGoldenError as exc:  # pragma: no cover
                # Coverage audit gap 2 (2026-09-21): provably unreachable,
                # not merely untested -- `validate_golden_set` (N28,
                # `prerun.py`) runs `validate_golden_structure` (the
                # `is`-identical alias of this SAME `_validate_golden`
                # function `classify()` calls) over EVERY item's `golden`
                # in the pre-run chain, BEFORE the per-document loop ever
                # starts, and aborts `malformed_golden` on the first
                # failure. So by the time this line runs, `golden` has
                # already passed the exact validator `classify()` is
                # about to run again -- it cannot raise here. Kept only
                # as defense-in-depth against N28/N22 ever drifting apart
                # (they can't, by construction: `validate_golden_structure
                # = _validate_golden`), not because this branch is
                # expected to fire. `except MalformedActualError` right
                # below IS reachable and IS pinned by
                # `test_facade.py::test_run_eval_aborts_malformed_actual_from_classify`
                # -- `actual` comes from a live per-document IDP
                # extraction, never pre-validated by any N28-equivalent
                # pass.
                raise _abort("malformed_golden", document_id, str(exc)) from None
            except MalformedActualError as exc:
                raise _abort("malformed_actual", document_id, str(exc)) from None

            verdict_maps[document_id] = verdicts

            if gate == "FAIL":
                any_gate_failed = True
                failed_count += 1
            else:
                passed_count += 1

            scores = build_score_inputs(
                golden=golden,
                verdicts=verdicts,
                gate=gate,
                run_id=run_id,
                document_id=document_id,
                include_values=record_values,
            )
            # DEBT-18 REVERSED 2026-09-25 (user decision): the ORCHESTRATOR
            # decides what the platform is told, in this one place -- the
            # adapter only renders what it is handed. `verdicts=None`
            # reproduces option B's payload exactly.
            records.append(
                {
                    "item_id": item["item_id"],
                    "document_id": document_id,
                    "scores": scores,
                    "verdicts": verdicts if record_values else None,
                }
            )
            # T-01.4.8: elapsed for THIS document, on the SAME log line
            # that already carries its document_id (Zangado's S-01.2 note
            # -- a timing line must be linked to the document it
            # describes, not a separate untethered one).
            logger.info(
                "run_eval: document processed document_id=%s gate=%s elapsed_seconds=%.3f",
                sanitize_for_log(document_id),
                gate,
                time.monotonic() - document_started_at,
            )

        # ADR-0005 #9 step 4: a single record_run call, after the loop.
        metadata: RunMetadata = {
            "action_id": action_id,
            "action_version": version,
            "golden_version": golden_version,
            "golden_dataset_name": dataset_name,
        }
        try:
            platform.record_run(
                dataset_name=dataset_name,
                run_name=experiment_name,
                run_id=run_id,
                records=records,
                metadata=metadata,
            )
        except FlushFailedError as exc:
            raise _abort("flush_failed", None, str(exc)) from None
        except (ExperimentRecordFailedError, ScoreWriteFailedError) as exc:
            # ADR-0005 #9 step 4: "any other platform error -> hard_failure".
            raise _abort("hard_failure", None, str(exc)) from None
    except RunAbortedError as exc:
        # ADR-0007 Option E: best-effort, whatever was classified before
        # the abort -- never affects the exit code below (INV-08/CT-04),
        # see `write_run_artifact`'s own docstring for the failure posture.
        write_run_artifact(run_id, verdict_maps, status="aborted", abort_reason=exc.reason)
        logger.error("run_eval: %s: %s", exc.reason, sanitize_for_log(str(exc)))
        _log_run_end("aborted", 1, pass_count=passed_count, fail_count=failed_count)
        return 1
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - HARDEN-01 GAP-1/GAP-5
        # Unlike the pre-run catch-all above, `run_id` DOES exist here
        # (generated before this try-block) -- ADR-0004 #14 says every
        # run that exists gets a best-effort `aborted` marker, so this
        # writes one, exactly like every `_abort()` call site above,
        # before ever logging or returning. `type(exc).__name__` plus the
        # last traceback frame's location (R-2) only, never `str(exc)`
        # (INV-02 -- an untyped exception's message is not vetted the way
        # every typed one this codebase raises is; it could echo IDP or
        # platform response content). Widened 2026-09-21 (Branca `/harden`
        # GAP-5) to also catch `asyncio.CancelledError` -- see the
        # pre-run catch-all's comment above for why.
        _mark_run_status_best_effort(
            platform,
            run_id,
            "aborted",
            action_id=action_id,
            action_version=version,
            golden_version=golden_version,
            golden_dataset_name=dataset_name,
        )
        # ADR-0007 Option E: same best-effort shape as the marker above --
        # whatever was classified before the unexpected error, never
        # touching the exit code (INV-08/CT-04).
        write_run_artifact(
            run_id, verdict_maps, status="aborted", abort_reason="unexpected_error"
        )
        logger.error(
            "run_eval: unexpected error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        _log_run_end("aborted", 1, pass_count=passed_count, fail_count=failed_count)
        return 1

    # ADR-0007 Option E: the full run's verdict maps, written once every
    # document succeeded and BEFORE the success marker below -- same
    # ordering rationale as `record_run` itself (ADR-0005 #9 step 4/5):
    # best-effort, never able to turn this otherwise-passing run into a
    # failure (INV-08/CT-04 -- the exit code a few lines down never reads
    # anything this call did or didn't do).
    write_run_artifact(run_id, verdict_maps, status="complete")

    # ADR-0005 #9 step 5: mark_run_status("complete", ...), best-effort --
    # INV-08 holds trivially, the exit code below never reads this marker
    # or anything else from the platform, only the in-process gates.
    _mark_run_status_best_effort(
        platform,
        run_id,
        "complete",
        action_id=action_id,
        action_version=version,
        golden_version=golden_version,
        golden_dataset_name=dataset_name,
    )

    exit_code = 1 if any_gate_failed else 0
    _log_run_end(
        "gate_failed" if any_gate_failed else "success",
        exit_code,
        pass_count=passed_count,
        fail_count=failed_count,
    )
    return exit_code
