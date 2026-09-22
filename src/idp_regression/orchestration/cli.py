"""`run_eval` CLI entry point (T-01.4.1, `--dataset` added by ADR-0004
amendment T-01.4.12 A6 / DEBT-48, `--org` added by ADR-0004 A9,
`--max-documents-per-run` added by ADR-0004 A10, both 2026-09-22).

::

    run_eval --org <id> --version <v> --run <name> --action <id> \\
        --dataset <name> [--max-documents-per-run <n>]

`load_dotenv()` is the first line of this script, before argparse even
parses argv (ADR-0004, INV-05) -- `main` is the outermost caller in this
codebase, the one place the ADR's "first line of the script" DoD
language literally applies. `run_eval` (the facade) ALSO calls
`load_dotenv()` as its own first statement -- ⚠️ corrected 2026-09-21
(DEBT-44 gate overclaim sweep): NOT because it is "the direct caller of
`make_platform()`" (it no longer calls that function at all since the
C-1/R-3 fix; see `facade.py`'s own corrected docstring), simply because
INV-05 requires `load_dotenv()` before ANY credential read, and
`run_eval` is a public function other callers may invoke directly,
bypassing this CLI. The second call is a harmless no-op (never overrides
an already-set var -- see `dotenv_support.load_dotenv`) and holds INV-05
for any caller of `run_eval`, not only this CLI.

Argument validation (TP-31, ADR-0004 amendment 2026-09-19; tightened
2026-09-22 -- user decision, then amended 2026-09-22 by ADR-0004 A9):
`--version`, `--action`, `--dataset` and `--org` are ALL required with no
environment fallback (argparse enforces this, exit code 2) -- every input
that defines *what was tested* comes from the command line, so it is
visible in a CI invocation and its PR diff. Ambient environment can no
longer decide what a run measured; it still supplies what describes the
*machine and account* (IDP credentials, region, `IDP_DOCUMENT_DIR`, the
evaluation platform's own host/key vars (N24 -- this module names no
vendor), the timeouts). `--action` and `--version` are validated here
(`action_id` UUID, `version` `^[A-Za-z0-9._-]{1,64}$`) and `--dataset`/
`--org` are rejected if blank after `.strip()` (the same fail-closed
shape `bootstrap.py`'s N6 guard uses for credentials) -- all before
`run_eval`, and therefore before any IDP/platform network call, is ever
entered. `--org` moved here from `IDP_ORG_ID` (ADR-0004 A9, 2026-09-22):
an action is addressed by `(org, action, version)`, and the credential in
`.env` is valid for more than one org id (IDP access is granted at the
business-group level), so the org id is not derivable from the credential
and can be wrong while the credential is right -- exactly the silent-fork
failure mode A8 already closed for `--action`/`--dataset`.

`--max-documents-per-run` (ADR-0004 A10, 2026-09-22) is OPTIONAL, unlike
the four flags above -- a **deliberate deviation from A10's own text**
(A10 specifies the ceiling as required with no default; the user
overrode this for the MVP: "just put a high number as parameter for the
POC/MVP. Don't make it a blocker, maybe a validation point for later.").
Defaults to `facade.DEFAULT_MAX_DOCUMENTS_PER_RUN` -- a round,
deliberately arbitrary number that bounds a runaway loop (an
unexpectedly huge golden set, a future resubmit path), NOT the org's
real IDP allotment; it must never be presented as a real quota control.
The run-start log line always records the effective value. `0` or a
negative value is a usage error (exit 1, pre-network) -- there is no
opt-out; anyone who wants effectively-unbounded passes a large number
they chose, on the record in the invocation.

INV-02 (message hygiene, corrected 2026-09-21 -- DEBT-44 gate, fifth
instance, finding (b)): the three validation error messages this
function writes itself (missing `--action`, malformed `--action`,
malformed `--version`) name only the field, never the value -- they were
already correct and are pinned that way. `argparse`'s OWN error message
(the `_ArgumentParsingFailed` branch) is different: it is built by
`argparse` from raw argv tokens and DOES embed attacker-controlled text
(e.g. an unrecognized flag's value) -- an earlier version of this
docstring claimed "never the value" for ALL error messages, which was
false for this one path and, reproduced live, let a crafted argv value
forge a second, fabricated log line via an embedded newline (N5). That
path is now routed through `sanitize_for_log` like every other
untrusted-value boundary in this codebase, so the value CAN appear, but
never unescaped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import uuid
from collections.abc import Sequence

from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.facade import DEFAULT_MAX_DOCUMENTS_PER_RUN, run_eval
from idp_regression.orchestration.log_sanitize import frame_location

logger = logging.getLogger(__name__)

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _is_valid_action_id(candidate: str) -> bool:
    try:
        uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _is_valid_version(candidate: str) -> bool:
    return bool(_VERSION_PATTERN.match(candidate))


class _NonExitingArgumentParser(argparse.ArgumentParser):
    """`argparse`'s default `error()` calls `sys.exit(2)` directly --
    that bypasses `run_eval`'s exit-code contract (a caller invoking
    `main()` as a library function, e.g. these tests, would see an
    uncaught `SystemExit` instead of a return value). Raise instead, so
    `main()` can convert it into a controlled non-zero return."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise _ArgumentParsingFailed(message)


class _ArgumentParsingFailed(Exception):
    pass


def _build_parser() -> argparse.ArgumentParser:
    parser = _NonExitingArgumentParser(prog="run_eval")
    parser.add_argument("--version", dest="version", required=True)
    parser.add_argument("--run", dest="run_name", required=True)
    # Tightened 2026-09-22 (user decision): required, no env fallback --
    # what a run measured must be visible in the invocation itself and
    # its PR diff, not resolvable from ambient environment.
    parser.add_argument("--action", dest="action", required=True)
    parser.add_argument("--dataset", dest="dataset", required=True)
    # ADR-0004 A9 (2026-09-22): `--org` joins the required-flags group --
    # an action is addressed by (org, action, version), and the org id is
    # not derivable from the credential (see the module docstring above).
    parser.add_argument("--org", dest="org", required=True)
    # ADR-0004 A10 (2026-09-22), MVP override of A10's own required-no-
    # default text (user decision): OPTIONAL, with a deliberately
    # arbitrary high default -- an MVP guard rail against a runaway loop,
    # NOT the org's real IDP allotment (see the module docstring above
    # and `facade.DEFAULT_MAX_DOCUMENTS_PER_RUN`'s own docstring).
    parser.add_argument(
        "--max-documents-per-run",
        dest="max_documents_per_run",
        type=int,
        default=DEFAULT_MAX_DOCUMENTS_PER_RUN,
        help=(
            "MVP guard rail bounding a runaway loop (e.g. an unexpectedly "
            "huge golden set) -- NOT derived from the org's real IDP "
            "allotment, and not a substitute for one. Must be a positive "
            f"integer; defaults to {DEFAULT_MAX_DOCUMENTS_PER_RUN}."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # ⚠️ Fixed 2026-09-21 (Atchim gate, live-reproduced): `load_dotenv()`
    # used to sit BEFORE this function's own try/except (which only wraps
    # the `run_eval(...)` call below) -- an `OSError` from an unreadable
    # or undecodable `.env` (a real trigger: a permission-denied or
    # non-UTF-8 path) escaped `main()` entirely. `main()` is the outermost
    # caller in this codebase, so an uncaught exception here means Python
    # prints a RAW TRACEBACK -- carrying the `.env` path in `str(exc)` --
    # straight to stderr: a live INV-02 path-disclosure, not merely a
    # broken `-> int` contract. Same shape as every other catch-all in
    # this module: only the type name and frame location are logged,
    # never `str(exc)`.
    try:
        load_dotenv()
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - see comment above
        # ⚠️ Fixed 2026-09-21 (Atchim gate): this used to log
        # "run_eval: unexpected error", misattributing a `.env`-loading
        # failure to `run_eval` (which hasn't even been called yet) --
        # confusing in triage, since the same message also covers a
        # genuine `run_eval` failure below. Own message, own cause.
        logger.error(
            "cli: unexpected error loading .env: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        return 1

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentParsingFailed as exc:
        # DEBT-44 gate, fifth instance, finding (b) (2026-09-21, reproduced
        # live): argparse's own error() builds its message from raw argv
        # tokens (e.g. "unrecognized arguments: --x <value>"), so an
        # unrecognized flag's VALUE is attacker-controlled text reaching
        # this log line verbatim -- unlike every other error path in this
        # function, none of which ever interpolates a value (INV-02).
        # Route it through the same sanitize_for_log every other
        # untrusted-value boundary in this codebase uses, so an embedded
        # newline can't forge a second, fabricated log line (N5) -- e.g.
        # a crafted value containing "\nERROR:root:run_eval: gate PASSED"
        # must not render as a believable extra log entry.
        logger.error("run_eval: invalid arguments: %s", sanitize_for_log(str(exc)))
        return 2

    action_id = args.action

    if not _is_valid_action_id(action_id):
        logger.error("run_eval: --action is not a valid UUID")
        return 1

    if not _is_valid_version(args.version):
        logger.error("run_eval: --version has an invalid format")
        return 1

    # Tightened 2026-09-22 (user decision): `--dataset` is required, no
    # env fallback. `.strip()` still rejects a whitespace-only value the
    # same way `bootstrap.py`'s N6 guard does for the platform/IDP
    # credentials -- a blank string is still a blank string when it
    # arrives via a required flag instead of an env var.
    dataset_name = (args.dataset or "").strip()
    if not dataset_name:
        logger.error("run_eval: --dataset must not be blank")
        return 1

    # ADR-0004 A9 (2026-09-22): `--org` is required, no env fallback --
    # same N6 shape as `--dataset` above (`.strip()` fail-closed on a
    # whitespace-only value).
    org_id = (args.org or "").strip()
    if not org_id:
        logger.error("run_eval: --org must not be blank")
        return 1

    # ADR-0004 A10 (2026-09-22): `--max-documents-per-run` is optional
    # (MVP override of A10's own required-no-default text) but a
    # non-positive value is still a usage error, pre-network -- there is
    # no opt-out flag; anyone who wants effectively-unbounded passes a
    # large number they chose.
    max_documents_per_run = args.max_documents_per_run
    if max_documents_per_run <= 0:
        logger.error("run_eval: --max-documents-per-run must be a positive integer")
        return 1

    try:
        return run_eval(
            action_id,
            args.version,
            args.run_name,
            dataset_name,
            org_id,
            max_documents_per_run,
        )
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - defense in depth
        # `run_eval`'s own contract (facade.py, `orchestration/errors.py`)
        # is that no exception may ever escape it -- this batch (T-01.4.6)
        # finished building the function, closing the slice boundary that
        # used to make `NotImplementedError` the one real path every
        # invocation took (formerly caught here as a reserved exit code
        # 3, not part of the 0/non-zero CI-gate contract). That reserved
        # code is now retired: nothing raises it anymore. This catch-all
        # stays as defense in depth for CT-04 (0 iff success, non-zero
        # otherwise) -- an uncaught exception would still exit non-zero
        # via Python's own default, but only by coincidence, and would
        # print a raw traceback that could echo exception-args content
        # this codebase is otherwise careful never to log (INV-02).
        # ⚠️ Corrected 2026-09-21 (C-1 follow-up, Atchim gate): this used
        # to route `str(exc)` through `sanitize_for_log`, which only
        # quotes/escapes (`json.dumps`) -- it does NOT redact, so a
        # platform/IDP error body embedded in `str(exc)` would still
        # reach the log verbatim aside from quoting. Now logs only
        # `type(exc).__name__` plus its last traceback frame's location
        # (R-2 shape, matching `run_eval`'s own catch-alls) -- the exit
        # code is a plain non-zero, not a distinguishing value, so no
        # detail is lost by this.
        logger.error(
            "run_eval: unexpected error: %s at %s",
            type(exc).__name__,
            frame_location(exc),
        )
        return 1


if __name__ == "__main__":  # pragma: no cover - thin process entry
    sys.exit(main())
