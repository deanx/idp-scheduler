#!/usr/bin/env python
"""Does a NEW Action version still read these files the way the trusted
one did? One command, two versions, one archive.

The scenario (user decision, 2026-09-25): a set of documents already
validated against the current Action version; the LLM behind the action
changes; a new version is published. Nothing about the documents
changed, so any difference in what comes back is the model's.

This runs the two halves back to back:

    1. `pin_document.py`    read every file with --trusted-version,
                            record that reading as its golden
    2. `verify_document.py` re-read every file with --candidate-version,
                            compare against those goldens

and spends the quota once you have approved the total for BOTH.

Why it exists rather than two invocations
-----------------------------------------
* **One archive, unpacked ONCE.** Both halves are pointed at the same
  directory, so the bytes compared are provably the same bytes pinned.
  Run separately, each would unpack its own copy and nothing would check
  they matched -- which is the one assumption the whole comparison rests
  on.
* **One approval for the real total.** Pinning costs one extraction per
  file and verifying costs another; the number printed here is 2N, not N
  twice.
* **A partial pin cannot become a quiet pass.** If a file fails to pin it
  has no golden, and verification would report it as "no golden, ignored"
  and exit 0 on the rest. That is refused unless `--allow-partial`.

⚠️ Costs TWO real IDP extractions per document.

Usage
-----
    # what would it cost?
    .venv/bin/python scripts/compare_versions.py --zip corpus.zip \\
        --dataset customer-invoices --org $IDP_ORG_ID --action $IDP_ACTION_ID \\
        --trusted-version 1.0.0 --candidate-version 2.0.0 --plan

    # the whole comparison
    ... --yes

Already pinned these files? Then you do not need this command -- run
`verify_document.py` alone against the existing goldens and spend N, not
2N. This is for the case where the golden set does not exist yet.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
    CorpusTooLargeError,
    QuotaRefusedError,
    ZipRejectedError,
    confirm_cost,
    discover_documents,
    extract_documents_from_zip,
    read_json_if_present,
    refuse_over_ceiling,
    run_outcome,
)

DEFAULT_STORE = Path(".idp-regression-pins")


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise RuntimeError(f"scripts/{name}.py is missing or unloadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="compare_versions",
        description="Pin an archive to a trusted Action version, then verify a candidate.",
    )
    ap.add_argument("--zip", dest="zip_path", type=Path, help="archive of documents")
    ap.add_argument("--document-dir", type=Path, help="an already-unpacked directory instead")
    ap.add_argument("--dataset", required=True, help="dataset the per-file goldens live in")
    ap.add_argument("--org", required=True, help="organisation / business group id")
    ap.add_argument("--action", required=True, help="IDP action id")
    ap.add_argument(
        "--trusted-version", required=True, help="the version the goldens are READ FROM"
    )
    ap.add_argument(
        "--candidate-version", required=True, help="the version being VALIDATED"
    )
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE, help="where pins are kept")
    ap.add_argument(
        "--extract-to", type=Path, help="where --zip is unpacked (default: <store>/documents/)"
    )
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument("--max-documents", type=int, default=200, help="ceiling on documents")
    ap.add_argument(
        "--repin",
        action="store_true",
        help="re-read files that are already pinned (spends an extra extraction each)",
    )
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "verify the files that pinned successfully even if some failed. Off by "
            "default: the failures have no golden, so they would be silently skipped "
            "and the run would exit 0 on the rest."
        ),
    )
    ap.add_argument("--run", dest="run_name", help="run name for the verification")
    ap.add_argument(
        "--classifier",
        default=None,
        help=(
            "comparison strategy for the VERIFY half only (default: pinned-file). "
            "Forwarded verbatim to verify_document.py, which is the one that "
            "validates the name and restricts it to a pinned-file-based custom "
            "scorer -- see that script's own --classifier help."
        ),
    )
    ap.add_argument("--plan", action="store_true", help="print the cost and stop")
    ap.add_argument("--yes", action="store_true", help="approve BOTH halves' quota")
    return ap.parse_args(argv)


def _archive_digest(archive: Path) -> str:
    digest = hashlib.sha256()
    with open(archive, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _pin_set_conflict(args: argparse.Namespace, corpus: set[str]) -> str | None:
    """Why the existing pins at (action, trusted version) would make this
    comparison measure something other than THIS corpus -- checked before
    anything is spent (DEBT-90/DEBT-93).

    Pins are keyed by document name under `goldens/<action>/<version>/`,
    and the verify half selects every pin of the dataset. So (a) a pin of
    this dataset that is not in this corpus would be verified too, or
    refused as MISSING after the pin half has already paid; and (b) a
    document in this corpus already pinned for ANOTHER dataset would be
    skipped by the pin half as "already pinned" and never reach this
    dataset. Either way, `STILL VALID` would describe a different set of
    documents from the one the operator sent."""
    pins = read_json_if_present(
        args.store / "goldens" / args.action / args.trusted_version / "_pins.json"
    )
    foreign = sorted(n for n, r in pins.items() if r.get("dataset") == args.dataset
                     and n not in corpus)
    if foreign:
        return (
            f"dataset {args.dataset!r} already has {len(foreign)} document(s) pinned at "
            f"{args.action}/{args.trusted_version} that are not in this corpus "
            f"(e.g. {foreign[0]}). Use a new --dataset or --store for a different corpus."
        )
    elsewhere = sorted(n for n, r in pins.items() if n in corpus
                       and r.get("dataset") not in (None, args.dataset))
    if elsewhere and not args.repin:
        return (
            f"{len(elsewhere)} document(s) of this corpus are already pinned at "
            f"{args.action}/{args.trusted_version} for ANOTHER dataset (e.g. {elsewhere[0]}); "
            "they would be skipped and never reach this one. Use a separate --store."
        )
    return None


def run(args: argparse.Namespace, pin: Any, verify: Any) -> tuple[int, dict[str, Any]]:
    """`pin` and `verify` are the two scripts, injected so the tests drive
    the whole comparison without an IDP or a platform."""
    # Unpacked ONCE, here, and both halves are pointed at the result --
    # see the module docstring.
    if args.zip_path:
        # One directory PER ARCHIVE, named by its content. The shared
        # `<store>/documents/` accumulated every earlier archive, and the pin
        # half -- pointed at the directory -- pinned all of them: a plan of 4
        # extractions spent 9 (DEBT-90). The same archive re-sent lands in
        # the same directory, so a re-run still skips what is pinned.
        document_dir = args.extract_to or (
            args.store / "documents" / _archive_digest(args.zip_path)
        )
        try:
            documents, skipped = extract_documents_from_zip(
                args.zip_path, document_dir, patterns=args.glob, dry_run=args.plan
            )
        except ZipRejectedError as exc:
            print(f"compare_versions: {exc}", file=sys.stderr)
            return 2, {}
        print(
            f"compare_versions: {'would unpack' if args.plan else 'unpacked'} "
            f"{len(documents)} document(s) -> {document_dir}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        if not args.plan:
            # The pin half lists the DIRECTORY, so the directory must hold
            # exactly this archive -- an --extract-to that already held other
            # documents would be priced as N and spent as more.
            listed = {p.name for p in discover_documents(document_dir, args.glob, None)}
            extra = sorted(listed - {p.name for p in documents})
            if extra:
                print(
                    f"compare_versions: {document_dir} already holds {len(extra)} "
                    f"document(s) that are not in {args.zip_path.name} (e.g. {extra[0]}); "
                    "the pin half would pay for them too. Unpack to an empty --extract-to.",
                    file=sys.stderr,
                )
                return 2, {}
    else:
        document_dir = args.document_dir
        documents = discover_documents(document_dir, args.glob, None)

    try:
        refuse_over_ceiling(documents, args.max_documents)
    except CorpusTooLargeError as exc:
        print(f"compare_versions: {exc}", file=sys.stderr)
        return 2, {}
    count = len(documents)
    if not count:
        print("compare_versions: no documents found", file=sys.stderr)
        return 2, {}
    conflict = _pin_set_conflict(args, {p.name for p in documents})
    if conflict:
        print(f"compare_versions: {conflict}", file=sys.stderr)
        return 2, {}

    if args.trusted_version == args.candidate_version:
        print(
            f"compare_versions: --trusted-version and --candidate-version are both "
            f"{args.trusted_version!r}. That compares a version against ITSELF -- a "
            "self-consistency check, not a regression check (scripts/noise_floor.py "
            "is the tool built for that).",
            file=sys.stderr,
        )

    # A bad --classifier used to be refused only once verify.main()'s OWN
    # argparse stage ran -- AFTER the pin half had already spent its N
    # extractions (Epic E custom-scorer wiring, gate F-1). `verify`'s
    # `resolve_classifier` runs the SAME pinned-file-base check with no
    # parser and no spend, so a wrong name is refused before the plan is
    # even printed.
    if args.classifier:
        try:
            verify.resolve_classifier(args.classifier)
        except ValueError as exc:
            print(f"compare_versions: {exc}", file=sys.stderr)
            return 2, {}

    print(f"compare_versions: PLAN for dataset {args.dataset!r}", file=sys.stderr)
    print(f"  documents            {count}", file=sys.stderr)
    print(
        f"  1. pin    @ {args.trusted_version:<12} {count} extraction(s)", file=sys.stderr
    )
    print(
        f"  2. verify @ {args.candidate_version:<12} {count} extraction(s)",
        file=sys.stderr,
    )
    try:
        confirm_cost(documents=count * 2, extractions_each=1, approved=args.yes or args.plan)
    except QuotaRefusedError as exc:
        print(f"compare_versions: refusing to start — {exc}", file=sys.stderr)
        return 2, {}
    if args.plan:
        print("compare_versions: --plan, stopping before any IDP call.", file=sys.stderr)
        return 0, {}

    common = ["--dataset", args.dataset, "--store", str(args.store)]

    # ── 1. generate the goldens ───────────────────────────────────────
    print(
        f"compare_versions: [1/2] pinning {count} document(s) @ {args.trusted_version}",
        file=sys.stderr,
    )
    pin_argv = [
        *common,
        "--document-dir", str(document_dir),
        "--all",
        "--org", args.org,
        "--action", args.action,
        "--version", args.trusted_version,
        "--glob", args.glob,
        "--max-documents", str(args.max_documents),
        "--yes",
    ]
    if args.repin:
        pin_argv.append("--repin")
    pin_rc = pin.main(pin_argv)
    if pin_rc != 0 and not args.allow_partial:
        print(
            "compare_versions: pinning reported failures — those files have NO golden, "
            "so verifying now would skip them silently and exit 0 on the rest. Re-run "
            "to retry them (pinned files are not re-paid for), or pass --allow-partial.",
            file=sys.stderr,
        )
        return pin_rc, {"pin_exit_code": pin_rc, "document_dir": str(document_dir)}

    # ── 2. validate the candidate against them ────────────────────────
    print(
        f"compare_versions: [2/2] verifying @ {args.candidate_version}", file=sys.stderr
    )
    verify_argv = [
        *common,
        "--all",
        "--document-dir", str(document_dir),
        "--version", args.candidate_version,
        # Name the pin set explicitly: the store may already hold goldens
        # from other actions/versions, and verifying against the wrong
        # one would look entirely normal.
        "--action", args.action,
        "--trusted-version", args.trusted_version,
        "--glob", args.glob,
        "--yes",
    ]
    if args.allow_partial:
        verify_argv.append("--allow-missing")
    if args.run_name:
        verify_argv += ["--run", args.run_name]
    if args.classifier:
        verify_argv += ["--classifier", args.classifier]
    verify_started = time.time()
    gate = verify.main(verify_argv)

    return gate, {
        "documents": count,
        "document_dir": str(document_dir),
        "dataset": args.dataset,
        "trusted_version": args.trusted_version,
        "candidate_version": args.candidate_version,
        "pin_exit_code": pin_rc,
        "gate_exit_code": gate,
        "verify_started": verify_started,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if bool(args.zip_path) == bool(args.document_dir):
        print("compare_versions: pass --zip OR --document-dir (exactly one)", file=sys.stderr)
        return 2
    if args.document_dir and not args.document_dir.is_dir():
        print(f"compare_versions: no such directory: {args.document_dir}", file=sys.stderr)
        return 2

    exit_code, summary = run(args, _load("pin_document"), _load("verify_document"))
    if not summary or "gate_exit_code" not in summary:
        return exit_code

    verdict, reason = run_outcome(exit_code, since=summary["verify_started"])
    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(
        f"{verdict}: {summary['documents']} document(s) read with "
        f"{summary['trusted_version']}, re-read with {summary['candidate_version']}",
        file=sys.stderr,
    )
    print(f"  goldens     dataset {summary['dataset']!r}", file=sys.stderr)
    print(f"  documents   {summary['document_dir']}", file=sys.stderr)
    if verdict == "RUN FAILED":
        print(
            f"\n  Not a verdict on {summary['candidate_version']}: {reason}. Nothing here "
            "says the documents changed -- fix the cause and re-run.",
            file=sys.stderr,
        )
    elif exit_code != 0:
        print(
            f"\n  {reason}: a critical field's value differs from what the trusted "
            "version read.\n"
            "  Detail:  .venv/bin/python scripts/show_run.py --failures-only",
            file=sys.stderr,
        )
    else:
        print(
            f"\n  The goldens stay pinned: re-check a later version with\n"
            f"    scripts/verify_document.py --all --dataset {summary['dataset']} "
            "--version <next> --yes",
            file=sys.stderr,
        )
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
