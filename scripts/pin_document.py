#!/usr/bin/env python
"""Pin ONE already-validated file to the trusted Action version.

The scenario (user decision, 2026-09-25) -- distinct from watching an
Action for new versions, which is unchanged:

    "I have a file that is already validated against the current version
     of the Action. I change the LLM and create a new version. The file
     must still be valid in the new version."

This is the first half. It runs the file through the **trusted** version
once, and records what that version read as the golden for that file:
field names, values, tables. `scripts/verify_document.py` is the second
half -- it re-reads the same file with a new version and compares.

What the golden means here
--------------------------
The file is granted: it has already been validated against the trusted
version, so for THIS file that version's reading IS the reference. The
golden is per document and nothing is generalised across files -- no
corpus statistics, no sparsity rule. Every field the trusted version
actually read is `critical`, so a changed value fails.

One nuance, and it is a property of the document rather than a policy: a
field the trusted version returned EMPTY for on this file has no value to
compare against, and `classify` reports an empty actual as `missing`
(that rule is correct for the watched-Action path and is left alone). So
an empty field is pinned non-critical for this file only, and stays
critical on every file where a value was read. A new version that INVENTS
a value there shows up as a non-critical `wrong_value` -- visible in the
run artifact and in `show_run`, not gate-failing.

Where the pin lives
-------------------
The layout IS the relationship (2026-09-25)::

    <store>/goldens/<action-id>/<action-version>/<document>.json
    <store>/goldens/<action-id>/<action-version>/_pins.json
    <store>/captures/<action-id>/<action-version>/<document>.raw.json
    <store>/documents/<document>

* one golden FILE per document, under the action and version that
  produced it -- so the same document can be pinned at several versions
  and they do not collide, and a store holding goldens from more than one
  version says so in its own paths. Each file is a one-entry golden set
  keyed by `document_id`, i.e. exactly the shape
  `provision_golden_dataset.py` already validates and provisions;
* the item, provisioned into the Langfuse dataset on its deterministic
  `uuid5` id, so re-pinning the same file UPSERTS;
* the file's directory, in `<store>/<dataset>.pins.json`, so
  `verify_document.py` can set `IDP_DOCUMENT_DIR` itself;
* the raw capture, in `<store>/captures/` -- IDP drops a result after 24
  hours and this is the only durable copy of what was paid for.

⚠️ Costs ONE real IDP extraction per invocation.

Usage
-----
    # one file
    .venv/bin/python scripts/pin_document.py --file invoices/inv-001.pdf \\
        --dataset customer-invoices \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 --yes

    # a whole archive of already-validated files, one golden each
    .venv/bin/python scripts/pin_document.py --zip corpus.zip --all \\
        --dataset customer-invoices \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 --yes

    # ...or a directory that is already unpacked
    .venv/bin/python scripts/pin_document.py --document-dir invoices/ --all ...

`--zip` unpacks flat into `--extract-to` (default `<store>/documents/`)
with the same trust-boundary checks the other batch tools use -- path
traversal and symlinks refused, archive junk dropped, non-documents
skipped, caps on entry count, size and compression ratio. That directory
is what verification resolves documents against.

A batch spends ONE extraction per document and is re-runnable: a
document already pinned is skipped (not re-paid for) unless `--repin`, so
re-running the same command after a failure retries only what failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from _batch import (  # noqa: E402
    DEFAULT_DOCUMENT_PATTERNS,
    QuotaRefusedError,
    ZipRejectedError,
    confirm_cost,
    discover_documents,
    ensure_private_dir,
    extract_documents_from_zip,
    progress,
    read_json_if_present,
    write_private_json,
)

DEFAULT_STORE = Path(".idp-regression-pins")


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise RuntimeError(f"scripts/{name}.py is missing or unloadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_fields_critical(entry: dict[str, Any]) -> dict[str, Any]:
    """Every field the trusted version read is gated -- including the ones
    it read as EMPTY.

    `draft_golden` marks an empty field non-critical because the default
    `regression` classifier reports an empty actual as `missing`, so a
    critical empty field would fail forever. The `pinned-file` classifier
    (`--classifier pinned-file`, which `verify_document.py` always passes)
    calls empty-against-empty a `match`, so that trap is gone and the
    stronger reading applies: for THIS file, what the trusted version read
    is the reference, and a new version producing a value where it found
    none is `wrong_value` on a critical field -- a fail, not a footnote.
    """
    for spec in entry.get("fields", {}).values():
        spec["critical"] = True
    for block in entry.get("tables", {}).values():
        block["critical"] = True
    return entry


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="pin_document",
        description="Pin one validated file's golden to the trusted Action version.",
    )
    ap.add_argument("--file", type=Path, help="ONE already-validated document to pin")
    ap.add_argument(
        "--zip",
        dest="zip_path",
        type=Path,
        help="a ZIP of already-validated documents (unpacked flat; needs --all)",
    )
    ap.add_argument(
        "--document-dir", type=Path, help="a directory of documents (needs --all)"
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help=(
            "pin EVERY document found in --zip / --document-dir. Required for those "
            "two: pinning a batch spends one extraction per file, so it is never "
            "implied by pointing at a folder."
        ),
    )
    ap.add_argument(
        "--extract-to",
        type=Path,
        help="where --zip is unpacked (default: <store>/documents/). This is the "
        "directory verification resolves documents against.",
    )
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument(
        "--max-documents",
        type=int,
        default=200,
        help="ceiling on documents pinned in one invocation (default: 200)",
    )
    ap.add_argument("--dataset", required=True, help="Langfuse dataset holding the pins")
    ap.add_argument("--org", required=True, help="organisation / business group id")
    ap.add_argument("--action", required=True, help="IDP action id")
    ap.add_argument("--version", required=True, help="the TRUSTED action version")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE, help="where pins are kept")
    ap.add_argument(
        "--repin",
        action="store_true",
        help="re-read and overwrite an existing pin for this file (spends an extraction)",
    )
    ap.add_argument("--yes", action="store_true", help="approve the one extraction")
    return ap.parse_args(argv)


def _resolve_documents(args: argparse.Namespace) -> tuple[list[Path], int]:
    """The documents this invocation will pin, and the exit code to use
    if that list could not be built."""
    if args.file:
        return [args.file], 0
    if args.zip_path:
        destination = args.extract_to or (args.store / "documents")
        try:
            unpacked, skipped = extract_documents_from_zip(
                args.zip_path, destination, patterns=args.glob
            )
        except ZipRejectedError as exc:
            print(f"pin_document: {exc}", file=sys.stderr)
            return [], 2
        print(
            f"pin_document: unpacked {len(unpacked)} document(s) from "
            f"{args.zip_path.name} -> {destination}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        if len(skipped) > 10:
            print(f"    ... and {len(skipped) - 10} more skipped", file=sys.stderr)
        return unpacked[: args.max_documents], 0
    return discover_documents(args.document_dir, args.glob, args.max_documents), 0


#: Path components come from operator input (`--action`, `--version`) and
#: land in a filesystem path, so they are validated here rather than
#: trusted -- same posture as `run_artifact._RUN_ID_PATTERN`. An action id
#: is a UUID and a version is semver-ish; both fit comfortably.
_PATH_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def golden_dir(store: Path, action_id: str, version: str) -> Path:
    """`<store>/goldens/<action-id>/<action-version>/`.

    The layout IS the relationship (user decision, 2026-09-25): a golden's
    document is its filename, and the action and version that produced it
    are the two directories above it. Before this, every golden for a
    dataset lived in one flat `<dataset>.golden.json` and the version that
    produced them appeared only in a sibling `pins.json` -- so the same
    document could not be pinned at two versions at all, and a store
    holding goldens from several versions looked identical to one that did
    not.
    """
    for part in (action_id, version):
        if not _PATH_SAFE.match(part):
            raise ValueError(f"unsafe path component: {part!r}")
    return store / "goldens" / action_id / version


def capture_dir(store: Path, action_id: str, version: str) -> Path:
    """Captures follow the same axis: a raw response is only meaningful
    against the version that produced it."""
    for part in (action_id, version):
        if not _PATH_SAFE.match(part):
            raise ValueError(f"unsafe path component: {part!r}")
    return store / "captures" / action_id / version


def _pin_one(
    document: Path,
    args: argparse.Namespace,
    capture_fn: Any,
    provision: Any,
    goldens_dir: Path,
    pins_path: Path,
    captures_dir: Path,
) -> dict[str, Any]:
    """Pin one document. Returns a per-document record; never raises, so
    one bad document cannot end a batch that has already spent quota on
    the others."""
    document_id = document.name
    golden_path = goldens_dir / f"{document_id}.json"
    pins = read_json_if_present(pins_path)

    try:
        capture = capture_fn(document)
    except Exception as exc:  # noqa: BLE001 - INV-02: type name only, never str(exc)
        print(f"    FAILED extraction: {type(exc).__name__}", file=sys.stderr)
        return {"document_id": document_id, "error": type(exc).__name__}

    ensure_private_dir(captures_dir)
    write_private_json(captures_dir / f"{document_id}.raw.json", capture)

    draft_golden = _load("draft_golden")
    try:
        entry, notes = draft_golden._draft(capture, document_id)
        entry = _all_fields_critical(entry)
    except Exception as exc:  # noqa: BLE001 - INV-02
        print(
            f"    FAILED to build a golden: {type(exc).__name__} (the capture is kept)",
            file=sys.stderr,
        )
        return {"document_id": document_id, "error": type(exc).__name__}

    # One file per document, written as a ONE-ENTRY golden set keyed by
    # `document_id` -- so it is still exactly the shape
    # `provision_golden_dataset.py` validates and provisions, and
    # `--golden-file <that file> --seed <document_id>` needs no new mode.
    write_private_json(golden_path, {document_id: entry})
    pins[document_id] = {
        "document_dir": str(document.resolve().parent),
        "dataset": args.dataset,
        "org": args.org,
        "pinned_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    write_private_json(pins_path, pins)

    rc = provision.main(
        [
            "--dataset", args.dataset,
            "--seed", document_id,
            "--golden-file", str(golden_path),
            # Provenance on the platform item too, so a dataset opened
            # elsewhere still states which version produced it.
            "--source-action", args.action,
            "--source-version", args.version,
        ]
    )
    return {
        "document_id": document_id,
        "fields": len(entry["fields"]),
        "critical": sum(1 for f in entry["fields"].values() if f.get("critical")),
        "tables": len(entry.get("tables", {})),
        "notes": notes,
        "provision_exit_code": rc,
    }


def run(args: argparse.Namespace, capture_fn: Any, provision: Any) -> tuple[int, dict[str, Any]]:
    try:
        goldens_dir = golden_dir(args.store, args.action, args.version)
        captures_dir = capture_dir(args.store, args.action, args.version)
    except ValueError as exc:
        print(f"pin_document: {exc}", file=sys.stderr)
        return 2, {}
    pins_path = goldens_dir / "_pins.json"

    documents, failure = _resolve_documents(args)
    if failure:
        return failure, {}

    # "Already pinned" is now the existence of that document's file under
    # THIS action/version -- so the same document pinned at another version
    # is a different golden, not a duplicate.
    skipped = [
        d for d in documents if (goldens_dir / f"{d.name}.json").exists() and not args.repin
    ]
    todo = [d for d in documents if not (goldens_dir / f"{d.name}.json").exists() or args.repin]
    for document in skipped:
        print(
            f"pin_document: {document.name!r} is already pinned -- --repin to re-read "
            "it (that spends another extraction)",
            file=sys.stderr,
        )
    if not todo:
        return 0, {"already_pinned": True, "skipped": [d.name for d in skipped]}

    try:
        confirm_cost(documents=len(todo), extractions_each=1, approved=args.yes)
    except QuotaRefusedError as exc:
        print(f"pin_document: refusing to start — {exc}", file=sys.stderr)
        return 2, {}

    print(
        f"pin_document: reading {len(todo)} document(s) with the TRUSTED version "
        f"{args.version}",
        file=sys.stderr,
    )
    started = time.monotonic()
    pinned: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, document in enumerate(todo, start=1):
        if len(todo) > 1:
            progress(index, len(todo), document.name, started)
        record = _pin_one(
            document, args, capture_fn, provision, goldens_dir, pins_path, captures_dir
        )
        (failures if record.get("error") else pinned).append(record)

    summary = {
        "pinned": pinned,
        "failures": failures,
        "skipped": [d.name for d in skipped],
        "dataset": args.dataset,
        "goldens_dir": str(goldens_dir),
        "document_dir": str(todo[0].resolve().parent) if todo else "",
    }
    failed_provision = any(r.get("provision_exit_code") for r in pinned)
    return (1 if (failures or failed_provision) else 0), summary


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    sources = [bool(args.file), bool(args.zip_path), bool(args.document_dir)]
    if sum(sources) != 1:
        print(
            "pin_document: pass exactly one of --file, --zip or --document-dir",
            file=sys.stderr,
        )
        return 2
    if (args.zip_path or args.document_dir) and not args.all:
        # Never implied by pointing at a folder: a batch spends one real
        # extraction per document, and "I meant that folder" has to be
        # said out loud before the count is even computed.
        print(
            "pin_document: --zip / --document-dir pin EVERY document found. "
            "Add --all to confirm, or --file to pin one.",
            file=sys.stderr,
        )
        return 2
    if args.file and not args.file.is_file():
        print(f"pin_document: no such file: {args.file}", file=sys.stderr)
        return 2
    if args.document_dir and not args.document_dir.is_dir():
        print(f"pin_document: no such directory: {args.document_dir}", file=sys.stderr)
        return 2

    from idp_regression.orchestration.dotenv_support import load_dotenv

    load_dotenv()

    bootstrap = _load("bootstrap_golden_set")
    try:
        capture_fn = bootstrap._make_capture_fn(args.org, args.action, args.version)
    except Exception as exc:  # noqa: BLE001 - INV-02
        print(
            f"pin_document: could not build the IDP adapter: {type(exc).__name__} "
            "(check IDP_CLIENT_ID / IDP_CLIENT_SECRET / IDP_REGION)",
            file=sys.stderr,
        )
        return 1

    exit_code, summary = run(args, capture_fn, _load("provision_golden_dataset"))
    if not summary or summary.get("already_pinned"):
        return exit_code

    pinned = summary["pinned"]
    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(
        f"pin_document: PINNED {len(pinned)} document(s) -> dataset "
        f"{summary['dataset']!r}",
        file=sys.stderr,
    )
    for record in pinned:
        print(
            f"  {record['document_id']:<40} {record['critical']}/{record['fields']} "
            f"field(s) critical"
            + (f", {record['tables']} table(s)" if record["tables"] else ""),
            file=sys.stderr,
        )
    if summary["skipped"]:
        print(
            f"  ({len(summary['skipped'])} already pinned, left alone)", file=sys.stderr
        )
    if summary["failures"]:
        print(f"  {len(summary['failures'])} FAILED:", file=sys.stderr)
        for record in summary["failures"]:
            print(f"    {record['document_id']}: {record['error']}", file=sys.stderr)
        print(
            "  (re-run the same command -- pinned documents are skipped, so only "
            "these are retried)",
            file=sys.stderr,
        )
    print(
        "\n  every field is critical, including the ones the trusted version read as "
        "EMPTY: verification uses the `pinned-file` classifier, where empty-against-"
        "empty is agreement and invented content is a failure.",
        file=sys.stderr,
    )
    print(f"  goldens     {summary['goldens_dir']}", file=sys.stderr)
    if summary["document_dir"]:
        print(
            f"\n  IDP_DOCUMENT_DIR for verification: {summary['document_dir']}",
            file=sys.stderr,
        )
    print(
        f"\n  verify after publishing a new version:\n"
        f"    .venv/bin/python scripts/verify_document.py --all "
        f"--dataset {summary['dataset']} --version <new-version> --yes",
        file=sys.stderr,
    )
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
