#!/usr/bin/env python
"""Turn a DIRECTORY of documents into a DRAFT golden set, in one batch.

⚠️ **READ `scripts/draft_golden.py`'s header first. Everything it warns
about is true here, multiplied by the size of the batch.** The expected
values this writes come from the extractor itself, so they pin *what the
incumbent currently does*, not *what is correct*. Where the incumbent is
already wrong, this records the wrong answer as "expected" and the gate
will then defend that error forever.

What it is for
--------------
The realistic customer situation: thousands of documents already
processed and accepted in production, and a proposal to switch the
extraction model (a cheaper one, a newer action version). Before that
switch can be judged, there has to be *something to compare against* --
and hand-transcribing thousands of documents is not going to happen.

So this batches the one step that is pure typing:

    for each document:  submit to the INCUMBENT action version
                        keep the raw response (durable, see below)
                        draft a golden entry from it

and leaves the one step that is judgement -- reading the values against
the page -- to a human, who can now spend all of their time on it.

**What the output is:** a baseline that says "this is what the incumbent
produced on documents the customer accepted". A regression against it
answers *"does the candidate agree with the incumbent?"* -- which is the
real question when swapping models, and is worth having on day one.
**What the output is not:** ground truth. Promote a sample to reviewed,
hand-checked goldens (that is what `--review-sample` prints a worklist
for) before treating a green build as "the candidate is correct".

⚠️ **Read the noise floor first.** Run `scripts/noise_floor.py` before
this. If the incumbent disagrees with *itself* on 6% of fields, then a
golden set drafted from one pass through it carries that same 6% of
coin-flips baked in as "expected", and a candidate will be failed for
reproducing noise. The noise floor tells you how much of the eventual
red is meaningless.

⚠️ **IDP keeps a result for 24 hours.** The raw response is written to
`--captures-dir` as it arrives, before anything is derived from it.
After 24 hours it cannot be re-fetched, so the capture is the only
durable record of what you paid for -- and `--from-captures` can rebuild
every draft from it, for free, when the drafting rules change.

Usage
-----
    # 0. what would it cost? (no network, no quota)
    .venv/bin/python scripts/bootstrap_golden_set.py \\
        --document-dir ~/customer-invoices --out draft_golden_set.json \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 --plan

    # 1. spend the quota (resumable: re-run the same line after a crash)
    .venv/bin/python scripts/bootstrap_golden_set.py \\
        --document-dir ~/customer-invoices --out draft_golden_set.json \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 \\
        --max-documents 1000 --resume --yes

    # 2. re-draft from what you already captured -- free, no IDP call
    .venv/bin/python scripts/bootstrap_golden_set.py \\
        --out draft_golden_set.json --from-captures

Then: reconcile (the real work), and provision with
`scripts/provision_golden_dataset.py`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Protocol

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from _batch import (  # noqa: E402
    DEFAULT_DOCUMENT_PATTERNS,
    CorpusTooLargeError,
    QuotaRefusedError,
    ZipRejectedError,
    confirm_cost,
    discover_documents,
    ensure_private_dir,
    extract_documents_from_zip,
    progress,
    read_json_if_present,
    refuse_over_ceiling,
    write_private_json,
)

from idp_regression.orchestration.dotenv_support import load_dotenv  # noqa: E402

#: `scripts/` is not a package, so the drafting rules are loaded by path
#: rather than imported. Loaded, not reimplemented: a second copy of the
#: type/critical/match_key heuristics would drift from the single-document
#: tool, and then a batch draft and a hand draft of the same capture would
#: disagree -- which is exactly the class of silent difference this
#: project exists to catch.
_spec = importlib.util.spec_from_file_location("draft_golden", _SCRIPTS_DIR / "draft_golden.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - a broken checkout
    raise RuntimeError("scripts/draft_golden.py is missing or unloadable")
draft_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(draft_golden)

#: A guard rail, not a real IDP quota: the ceiling exists so a mistyped
#: `--glob` costs a re-run rather than an invoice. Raise it deliberately.
DEFAULT_MAX_DOCUMENTS = 200


class SupportsCapture(Protocol):
    def __call__(self, document: Path) -> dict[str, Any]: ...


def _make_capture_fn(org_id: str, action_id: str, version: str) -> SupportsCapture:
    """Build the real capture callable. Reaches into the adapter's
    private submit/poll pair for the same reason `scripts/capture_raw.py`
    does: `extract()` returns a NORMALIZED result, and the raw response
    is what has to be on disk before the 24-hour window closes."""
    from idp_regression.adapter.idp_client import make_idp_adapter

    adapter = make_idp_adapter(org_id)

    def capture(document: Path) -> dict[str, Any]:
        started = time.monotonic()
        token = adapter._token_cache.get()
        execution_id = adapter._submit(str(document), action_id, version, token)
        raw = adapter._poll(
            execution_id,
            action_id,
            version,
            token,
            started + adapter._poll_timeout_seconds,
        )
        if isinstance(raw, dict) and "id" in raw:
            # Per-run noise that makes two captures of the same document
            # diff for no reason (same rule as capture_raw.py).
            raw["id"] = "REDACTED-EXECUTION-ID"
        return dict(raw)

    return capture


def _capture_path(captures_dir: Path, document_id: str) -> Path:
    return captures_dir / f"{document_id}.raw.json"


def _draft_one(capture: dict[str, Any], document_id: str) -> tuple[dict[str, Any], list[str]]:
    entry, notes = draft_golden._draft(capture, document_id)
    # Fail here, per document, rather than at provisioning time for the
    # whole set: a draft that could never be provisioned is a drafting
    # bug, and the batch should name the one document it happened on.
    import jsonschema

    from idp_regression.classifier.gate import validate_golden_structure
    from idp_regression.platform.schema import load_golden_schema

    jsonschema.Draft7Validator(load_golden_schema()).validate(entry)
    validate_golden_structure(entry)
    return entry, notes


def _review_worklist(
    golden_set: dict[str, Any], notes_by_key: dict[str, list[str]], sample: int
) -> str:
    """The reconciliation worklist. Written next to the golden set so the
    batch hands over an explicit list of what a human still owes, rather
    than a file that merely *looks* finished."""
    keys = sorted(golden_set)
    sampled = keys[:: max(1, len(keys) // sample)][:sample] if sample and keys else []
    lines = [
        "# Draft golden set — reconciliation worklist",
        "",
        "**These values came from the extractor, not from the documents.** They",
        "record what the incumbent currently produces. Until a human has read a",
        "value against the page, a green build means *the candidate agrees with",
        "the incumbent*, not *the candidate is right*.",
        "",
        f"- drafted entries: **{len(keys)}**",
        f"- hand-check sample below: **{len(sampled)}** (spread across the set, "
        "not the first N — the first N are usually the same customer)",
        "",
        "## Before provisioning",
        "",
        "1. Every field is drafted `critical: true` (fail-closed). Turn it OFF for",
        "   fields that are legitimately absent on some documents (a PO number, a",
        "   tax line) — otherwise those documents fail forever.",
        "2. `format_critical` is never guessed. Add it where the FORMAT is the",
        "   contract (e.g. a date a downstream parser requires as ISO-8601).",
        "3. Confirm each table's `match_key` really identifies a line. A wrong one",
        "   silently mis-pairs every row.",
        "4. Run `scripts/noise_floor.py` if you have not. Fields that are unstable",
        "   between two runs of the SAME version should not be `critical` here.",
        "",
        "## Hand-check these documents against the page",
        "",
    ]
    lines += [f"- [ ] `{key}`" for key in sampled] or ["- (nothing drafted)"]
    lines += ["", "## Per-document drafting notes", ""]
    for key in keys:
        lines.append(f"### {key}")
        lines += notes_by_key.get(key, ["  (rebuilt from a capture; no notes recorded)"])
        lines.append("")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="bootstrap_golden_set",
        description="Batch-draft a golden set from a directory of documents (REVIEW REQUIRED).",
    )
    ap.add_argument("--out", required=True, type=Path, help="golden-set JSON to write/merge into")
    ap.add_argument("--document-dir", type=Path, help="directory of documents to process")
    ap.add_argument(
        "--zip",
        dest="zip_path",
        type=Path,
        help=(
            "a ZIP of documents to process instead of --document-dir. Unpacked flat "
            "into --extract-to, then processed exactly as a directory would be."
        ),
    )
    ap.add_argument(
        "--extract-to",
        type=Path,
        help=(
            "where --zip is unpacked (default: <out>.documents/). This directory is "
            "what IDP_DOCUMENT_DIR must point at for the later run_eval -- the golden "
            "set names these files and nothing copies them again."
        ),
    )
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument(
        "--org", help="organisation / business group id (required unless --from-captures)"
    )
    ap.add_argument("--action", help="IDP action id (required unless --from-captures)")
    ap.add_argument("--version", help="INCUMBENT action version, e.g. 1.0.0")
    ap.add_argument(
        "--captures-dir",
        type=Path,
        help="where raw responses are written (default: <out>.captures/)",
    )
    ap.add_argument(
        "--max-documents",
        type=int,
        default=DEFAULT_MAX_DOCUMENTS,
        help=f"ceiling on documents processed (default: {DEFAULT_MAX_DOCUMENTS})",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="skip documents already captured — re-run the same line after a crash",
    )
    ap.add_argument(
        "--from-captures",
        action="store_true",
        help="re-draft from --captures-dir only. No IDP call, no quota spent.",
    )
    ap.add_argument(
        "--plan", action="store_true", help="print what would be done and the cost, then stop"
    )
    ap.add_argument(
        "--review-sample",
        type=int,
        default=25,
        help="how many documents the worklist asks a human to hand-check (default: 25)",
    )
    ap.add_argument(
        "--yes", action="store_true", help="approve spending the extractions printed by --plan"
    )
    return ap.parse_args(argv)


def run(
    args: argparse.Namespace, capture_fn: SupportsCapture | None
) -> tuple[int, dict[str, Any]]:
    """The batch itself, with the capture callable injected so the tests
    can drive every path without an IDP call."""
    captures_dir = args.captures_dir or args.out.with_suffix(".captures")
    if not args.plan:
        ensure_private_dir(captures_dir)

    try:
        golden_set = read_json_if_present(args.out)
    except ValueError as exc:
        # A truncated or hand-mangled `--out` would otherwise abort the
        # batch with a traceback AFTER the quota was approved. Named by
        # path, never by contents: a half-written golden set is still a
        # golden set, and `json.JSONDecodeError`'s own message can quote
        # the offending text (INV-02).
        print(
            f"bootstrap_golden_set: {args.out} exists but is not readable as a golden "
            f"set ({type(exc).__name__}; contents withheld). Move it aside or point "
            "--out elsewhere -- the captures are untouched, and --from-captures "
            "rebuilds every draft without spending quota.",
            file=sys.stderr,
        )
        return 2, {}
    notes_by_key: dict[str, list[str]] = {}
    failures: list[dict[str, Any]] = []

    document_dir = args.document_dir
    if args.zip_path and not args.from_captures:
        document_dir = args.extract_to or args.out.with_suffix(".documents")
        try:
            unpacked, skipped = extract_documents_from_zip(
                args.zip_path, document_dir, patterns=args.glob, dry_run=args.plan
            )
        except ZipRejectedError as exc:
            print(f"bootstrap_golden_set: {exc}", file=sys.stderr)
            return 2, {}
        verb = "would unpack" if args.plan else "unpacked"
        print(
            f"bootstrap_golden_set: {verb} {len(unpacked)} document(s) from "
            f"{args.zip_path.name} -> {document_dir}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        if len(skipped) > 10:
            print(f"    ... and {len(skipped) - 10} more skipped", file=sys.stderr)
        if args.plan:
            # Nothing was written, so there is nothing on disk to discover.
            documents = unpacked
            try:
                refuse_over_ceiling(documents, args.max_documents)
            except CorpusTooLargeError as exc:
                print(f"bootstrap_golden_set: {exc}", file=sys.stderr)
                return 2, {}
            print(
                f"bootstrap_golden_set: {len(documents)} document(s) to draft",
                file=sys.stderr,
            )
            # `approved=True` -- this is the cost PREVIEW, so it prints the
            # number rather than refusing for not having approved it.
            confirm_cost(documents=len(documents), extractions_each=1, approved=True)
            print("bootstrap_golden_set: --plan, stopping before any IDP call.", file=sys.stderr)
            return 0, {}

    redraft_free: set[str] = set()
    if args.from_captures:
        sources = sorted(captures_dir.glob("*.raw.json"))
        documents = [Path(p.name.removesuffix(".raw.json")) for p in sources]
    else:
        # Refused BEFORE the resume filter: the ceiling is about the corpus,
        # and capping first is what made --resume re-select the same first
        # N forever on a larger one (DEBT-114).
        documents = discover_documents(document_dir, args.glob, None)
        try:
            refuse_over_ceiling(documents, args.max_documents)
        except CorpusTooLargeError as exc:
            print(f"bootstrap_golden_set: {exc}", file=sys.stderr)
            return 2, {}
        if args.resume:
            # "Done" is DRAFTED, not captured. Keying resume on the capture
            # alone meant a document whose capture succeeded and whose draft
            # failed was never retried: the re-run made 0 calls, reported no
            # failures and exited 0 without it (DEBT-94). A captured but
            # undrafted document is re-drafted from its capture, free.
            drafted = {
                entry.get("document_id")
                for entry in golden_set.values()
                if isinstance(entry, dict)
            }
            before = len(documents)
            documents = [d for d in documents if d.name not in drafted]
            redraft_free = {
                d.name for d in documents if _capture_path(captures_dir, d.name).exists()
            }
            print(
                f"  --resume: {before - len(documents)} already drafted, "
                f"{len(redraft_free)} re-drafted from their capture (free), "
                f"{len(documents) - len(redraft_free)} to extract",
                file=sys.stderr,
            )

    print(f"bootstrap_golden_set: {len(documents)} document(s) to draft", file=sys.stderr)
    try:
        confirm_cost(
            documents=0 if args.from_captures else len(documents) - len(redraft_free),
            extractions_each=1,
            # `--plan` is the cost preview, so it must print the number
            # rather than be refused for not having approved it.
            approved=args.yes or args.from_captures or args.plan,
        )
    except QuotaRefusedError as exc:
        print(f"bootstrap_golden_set: refusing to start — {exc}", file=sys.stderr)
        return 2, {}

    if args.plan:
        print("bootstrap_golden_set: --plan, stopping before any IDP call.", file=sys.stderr)
        return 0, {}

    started = time.monotonic()
    total = len(documents)
    for index, document in enumerate(documents, start=1):
        document_id = document.name
        key = document_id.rsplit(".", 1)[0]
        progress(index, total, document_id, started)
        try:
            if args.from_captures or document_id in redraft_free:
                with _capture_path(captures_dir, document_id).open(encoding="utf-8") as fh:
                    capture = json.load(fh)
            elif capture_fn is None:  # pragma: no cover - guarded by main()
                raise RuntimeError("no capture callable and --from-captures not set")
            else:
                capture = capture_fn(document)
                # Written BEFORE anything is derived from it: this is the
                # only durable copy of what the extraction cost, and the
                # 24-hour window does not reopen.
                write_private_json(_capture_path(captures_dir, document_id), capture)
            entry, notes = _draft_one(capture, document_id)
        except Exception as exc:  # noqa: BLE001 - INV-02: type name only, never str(exc)
            # One handler, not two: the capture callable is injected and
            # raises whatever the IDP seam raises, so capture failures and
            # drafting failures arrive here identically -- and are recorded
            # identically, by type name only (INV-02).
            failures.append(
                {"document_id": document_id, "attempt": 1, "error_type": type(exc).__name__}
            )
            print(f"    FAILED: {type(exc).__name__}", file=sys.stderr)
            continue

        golden_set[key] = entry
        notes_by_key[key] = notes
        # Flushed per document, not at the end: a crash at document 700
        # of 1,000 must leave 699 drafted entries on disk.
        write_private_json(args.out, golden_set)

    review_path = args.out.with_suffix(".review.md")
    review_path.write_text(_review_worklist(golden_set, notes_by_key, args.review_sample))

    summary = {
        "document_dir": str(document_dir) if document_dir else "",
        "drafted": len(golden_set),
        "this_run": total - len(failures),
        "failures": failures,
        "out": str(args.out),
        "captures_dir": str(captures_dir),
        "review": str(review_path),
    }
    return (1 if failures else 0), summary


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.from_captures:
        if args.document_dir and args.zip_path:
            print(
                "bootstrap_golden_set: pass --document-dir OR --zip, not both -- "
                "which files a golden set was drafted from must be unambiguous",
                file=sys.stderr,
            )
            return 2
        missing = [
            flag
            for flag, value in (
                ("--document-dir or --zip", args.document_dir or args.zip_path),
                ("--org", args.org),
                ("--action", args.action),
                ("--version", args.version),
            )
            if not value
        ]
        if missing:
            # No environment fallback, by design (ADR-0004 A8/A9): what a
            # golden set was drafted FROM must be visible in the command
            # that drafted it.
            print(
                f"bootstrap_golden_set: missing required flag(s): {', '.join(missing)}",
                file=sys.stderr,
            )
            return 2

    load_dotenv()

    capture_fn: SupportsCapture | None = None
    if not (args.from_captures or args.plan):
        try:
            capture_fn = _make_capture_fn(args.org, args.action, args.version)
        except Exception as exc:  # noqa: BLE001 - INV-02
            print(
                f"bootstrap_golden_set: could not build the IDP adapter: {type(exc).__name__} "
                "(check IDP_CLIENT_ID / IDP_CLIENT_SECRET / IDP_REGION)",
                file=sys.stderr,
            )
            return 1

    exit_code, summary = run(args, capture_fn)
    if not summary:
        return exit_code

    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"DRAFT golden set: {summary['drafted']} entries -> {summary['out']}", file=sys.stderr)
    print(f"raw captures (24h window beaten): {summary['captures_dir']}", file=sys.stderr)
    if summary["document_dir"]:
        # The golden set NAMES these files; nothing copies them again, so
        # the later run_eval resolves every document_id against this exact
        # directory (facade._resolve_document_path).
        print(
            f"IDP_DOCUMENT_DIR for the run:     {Path(summary['document_dir']).resolve()}",
            file=sys.stderr,
        )
    print(f"reconciliation worklist:          {summary['review']}", file=sys.stderr)
    if summary["failures"]:
        print(f"{len(summary['failures'])} document(s) FAILED:", file=sys.stderr)
        for failure in summary["failures"]:
            print(
                f"  {failure['document_id']}: {failure['error_type']}",
                file=sys.stderr,
            )
        print("  (re-run the same command with --resume to retry only these)", file=sys.stderr)
    print("", file=sys.stderr)
    print("THIS IS NOT A GOLDEN SET YET. It records what the extractor does,", file=sys.stderr)
    print("not what is correct. Work through the worklist before you trust a", file=sys.stderr)
    print("green build from it.", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
