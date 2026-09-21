"""`run_eval` CLI entry point (T-01.4.1).

::

    run_eval --version <v> --run <name> [--action <id>]

`load_dotenv()` is the first line of this script, before argparse even
resolves `--action`'s `IDP_ACTION_ID` default (ADR-0004, INV-05) -- `main`
is the outermost caller in this codebase, the one place the ADR's "first
line of the script" DoD language literally applies. `run_eval` (the
facade) ALSO calls `load_dotenv()` as its own first statement, since it
is the direct caller of `make_platform()`/`make_idp_adapter()`; the
second call is a harmless no-op (never overrides an already-set var --
see `dotenv_support.load_dotenv`) and guarantees INV-05 holds for any
caller of `run_eval`, not only this CLI.

Argument validation (TP-31, ADR-0004 amendment 2026-09-19): `--version`
is required with no env fallback (argparse enforces this, exit code 2);
`--action` defaults to `IDP_ACTION_ID`, exit non-zero if neither is set;
both are validated here (`action_id` UUID, `version`
`^[A-Za-z0-9._-]{1,64}$`) before `run_eval` -- and therefore before any
IDP/platform network call -- is ever entered. Error messages name only
the field, never the (untrusted, possibly attacker-controlled) value
(INV-02).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import uuid
from collections.abc import Sequence

from idp_regression.orchestration.dotenv_support import load_dotenv
from idp_regression.orchestration.facade import run_eval

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
    parser.add_argument("--action", dest="action", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentParsingFailed as exc:
        logger.error("run_eval: %s", exc)
        return 2

    action_id = args.action or os.environ.get("IDP_ACTION_ID")
    if not action_id:
        logger.error(
            "run_eval: no --action given and IDP_ACTION_ID is not set in the environment"
        )
        return 1

    if not _is_valid_action_id(action_id):
        logger.error("run_eval: --action is not a valid UUID")
        return 1

    if not _is_valid_version(args.version):
        logger.error("run_eval: --version has an invalid format")
        return 1

    return run_eval(action_id, args.version, args.run_name)


if __name__ == "__main__":  # pragma: no cover - thin process entry
    sys.exit(main())
