#!/usr/bin/env python
"""Capture ONE raw IDP execution response, verbatim, to stdout as JSON.

Why this exists
---------------
`normalize()` turns IDP's wire shape into the stable `NormalizedOutput`. Every
defect this project has shipped on that seam came from a *fixture authored to
match the parser* rather than a recorded real response — so `docs/state/
REGRESSIONS.md` **SR-1** requires wire-contract assumptions to be pinned against
a recorded live response. This script is how that recording is made.

⚠️ **IDP retains a successful result for 24 hours.** After that the execution is
gone and cannot be re-fetched by id. Whatever this writes is the only durable
copy — that is the whole point of saving it.

Run identity is passed as **flags, never read from the environment**
(ADR-0004 A8/A9): `--org`, `--action`, `--version` and `--document` define what
was captured, so they must be visible in the invocation. Only credentials and
the region come from the environment, which is what `.env` is for.

⚠️ **This spends one real IDP extraction per invocation.**

Usage
-----
    .venv/bin/python scripts/capture_raw.py \
        --org <org-id> --action <action-id> --version 1.0.0 \
        --document testpack/inv-001-clean.pdf \
        > testpack/captures/inv-001-clean.raw.json

Add `--keep-execution-id` only if you have a reason to; by default the
execution id is redacted so the capture is safe to commit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from idp_regression.adapter.idp_client import make_idp_adapter  # noqa: E402
from idp_regression.orchestration.dotenv_support import load_dotenv  # noqa: E402

#: What replaces the execution id unless `--keep-execution-id` is passed. A real
#: execution id is not secret, but it is per-run noise that makes two captures of
#: the same document diff against each other for no reason.
REDACTED_EXECUTION_ID = "REDACTED-EXECUTION-ID"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="capture_raw",
        description="Capture one raw IDP execution response to stdout (SR-1 evidence).",
    )
    parser.add_argument("--org", required=True, help="organisation / business group id")
    parser.add_argument("--action", required=True, help="IDP action id")
    parser.add_argument("--version", required=True, help="action version, e.g. 1.0.0")
    parser.add_argument("--document", required=True, help="path to the document to submit")
    parser.add_argument(
        "--keep-execution-id",
        action="store_true",
        help="do not redact the execution id (default: redact, so the capture is committable)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    document = Path(args.document)
    if not document.is_file():
        print(f"capture_raw: no such document: {args.document}", file=sys.stderr)
        return 1

    load_dotenv()

    try:
        adapter = make_idp_adapter(args.org)
    except Exception as exc:  # noqa: BLE001 - INV-02: type + location only, never str(exc)
        print(
            f"capture_raw: could not build the IDP adapter: {type(exc).__name__} "
            "(check IDP_CLIENT_ID / IDP_CLIENT_SECRET / IDP_REGION)",
            file=sys.stderr,
        )
        return 1

    started = time.monotonic()
    print(
        f"capture_raw: submitting {document.name} to action={args.action} "
        f"version={args.version} -- this spends one real extraction",
        file=sys.stderr,
    )

    try:
        # Deliberately reaching into the adapter's private submit/poll pair: this
        # is a capture tool, not a consumer. `extract()` would hand back a
        # NORMALIZED result, and a normalized result is exactly what SR-1 says is
        # not evidence -- the whole point here is the bytes before normalize()
        # touches them. No SLF001 suppression here: that rule is deliberately
        # not selected yet, so a marker would be a dead suppression that RUF100
        # rejects -- see DEBT-73.
        token = adapter._token_cache.get()
        execution_id = adapter._submit(str(document), args.action, args.version, token)
        raw = adapter._poll(
            execution_id,
            args.action,
            args.version,
            token,
            started + adapter._poll_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - INV-02
        print(
            f"capture_raw: extraction failed: {type(exc).__name__} "
            f"(after {time.monotonic() - started:.1f}s)",
            file=sys.stderr,
        )
        return 1

    if not args.keep_execution_id and isinstance(raw, dict) and "id" in raw:
        raw["id"] = REDACTED_EXECUTION_ID

    json.dump(raw, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    # Everything below goes to stderr so stdout stays a clean JSON document.
    elapsed = time.monotonic() - started
    keys = sorted(raw) if isinstance(raw, dict) else []
    print(f"capture_raw: done in {elapsed:.1f}s -- top-level keys: {keys}", file=sys.stderr)
    if isinstance(raw, dict):
        # The one question Phase 2.3 of the test plan exists to answer.
        shape = []
        if "pages" in raw:
            shape.append("pages[]")
        if {"fields", "tables", "prompts"} & set(raw):
            shape.append("top-level rollup")
        print(
            f"capture_raw: envelope shape -> {' + '.join(shape) or 'NEITHER (unrecognised)'}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
