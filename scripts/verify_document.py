#!/usr/bin/env python
"""Is this pinned file still read the same way by a NEW Action version?

The second half of the per-file scenario (user decision, 2026-09-25):
`scripts/pin_document.py` recorded what the trusted version read from
this file; this re-reads the same file with a new version -- typically
one built on a different LLM -- and compares against that pin.

It is a normal `run_eval`, narrowed to one dataset item by the
`--document` flag, so everything the regression path already guarantees
still holds: the same classifier, the same gate, the same exit-code
contract (0 iff the gate passed), the same run artifact, the same scores
on the platform. Nothing here re-implements a comparison.

Two conveniences it adds, both of which are otherwise manual steps:

* `IDP_DOCUMENT_DIR` is set from the pin, because the pin recorded where
  the file lives and `run_eval` resolves `document_id` against that
  directory;
* the run name is composed from the document and the version under test,
  so a run is identifiable in the platform UI without inventing a name.

⚠️ Costs ONE real IDP extraction per invocation.

Usage
-----
    .venv/bin/python scripts/verify_document.py --file invoices/inv-001.pdf \\
        --dataset customer-invoices --version 2.0.0 --yes

    # everything pinned in this dataset, one run
    .venv/bin/python scripts/verify_document.py --all \\
        --dataset customer-invoices --version 2.0.0 --yes
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
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
    changed_since_recorded,
    confirm_cost,
    discover_documents,
    extract_documents_from_zip,
    pinned_elsewhere,
    read_json_if_present,
    run_outcome,
)

DEFAULT_STORE = Path(".idp-regression-pins")

#: The comparison a pinned file is verified with. Named, never imported:
#: `run_eval` resolves it through `classifier/registry.py`.
PINNED_FILE_CLASSIFIER = "pinned-file"


def _pinned_file_classifier_names(custom_specs: dict[str, Any] | None) -> list[str]:
    """The custom scorer names a per-file pin may be verified with:
    `base == "pinned-file"` only. ONE function, so `_parse_args`'s
    `choices` and `resolve_classifier`'s pre-spend check (below) can
    never drift apart on which names are actually offered."""
    return sorted(
        name for name, spec in (custom_specs or {}).items() if spec.base == PINNED_FILE_CLASSIFIER
    )


def resolve_classifier(name: str | None) -> str:
    """Validate a `--classifier` name against the SAME pinned-file-base
    restriction `_parse_args`'s `choices` enforces, WITHOUT building an
    argparse parser or spending anything -- so a caller that reaches
    this file only through `compare_versions.py` (which spends the pin
    half's quota BEFORE `verify_document.py`'s own argparse stage ever
    runs) can refuse a bad name before ANY extraction, not just before
    its own verify half (Epic E custom-scorer wiring, gate F-1: the pin
    half was spending N extractions on a `--classifier` that would only
    fail to parse afterwards).

    Reads specs with `load_specs()`, NOT `register_custom_classifiers()`
    -- the latter WRITES into the global `CLASSIFIERS` registry, and
    `compare_versions.py` loads `verify_document.py` as a module and
    runs `verify.main()` (the actual registrar) in the SAME process,
    later in the same call. A second `register_custom_classifiers()`
    call would hit `parse_spec`'s shadow guard, which refuses a name
    already in `CLASSIFIERS` -- including one THIS function registered
    a moment ago -- so calling it here would make every VALID custom
    name fail on the SECOND (verify) half, after the FIRST (pin) half
    had already spent (re-gate #2, gate F-6, reproduced live).
    `load_specs` only parses and returns specs; it never touches the
    registry, so resolving a name here can never collide with
    `main()`'s own, single, later registration.

    Raises `ValueError` (never `SystemExit`) naming the allowed choices.
    Returns the resolved classifier: `name`, or the shipped
    `pinned-file` default when `name` is falsy."""
    from idp_regression.orchestration.scorer_store import load_specs

    specs, _ = load_specs()
    custom_specs = {spec.name: spec for spec in specs}
    allowed = {PINNED_FILE_CLASSIFIER, *_pinned_file_classifier_names(custom_specs)}
    if name and name not in allowed:
        raise ValueError(
            f"--classifier {name!r} is not valid here (expected one of "
            f"{', '.join(sorted(allowed))}) -- a regression-based custom scorer reads "
            "empty-vs-empty as missing, wrong for a per-file pin"
        )
    return name or PINNED_FILE_CLASSIFIER


def _parse_args(
    argv: list[str] | None, *, custom_specs: dict[str, Any] | None = None
) -> argparse.Namespace:
    """`custom_specs` is `register_custom_classifiers()`'s return value,
    passed in by `main()` (which calls it BEFORE this parser is built --
    argparse freezes `choices` at construction time, `cli.py`'s own
    reason). Only a custom spec whose `base` is `pinned-file` may be
    named: a `regression`-based spec reads empty-vs-empty as `missing`,
    not agreement, which is wrong for a per-file pin (every field is
    critical here, including the ones the trusted version read as
    empty). Restricting the CHOICES, not just documenting the rule,
    means a typo or a wrong-base name is refused before any extraction,
    the same posture every other guard in this script takes."""
    pinned_file_custom_names = _pinned_file_classifier_names(custom_specs)
    ap = argparse.ArgumentParser(
        prog="verify_document",
        description="Re-check a pinned file against a new Action version.",
    )
    ap.add_argument("--file", type=Path, help="the pinned document to verify")
    ap.add_argument("--all", action="store_true", help="verify every pinned file in the dataset")
    ap.add_argument("--dataset", required=True, help="the dataset holding the pins")
    ap.add_argument("--version", required=True, help="the NEW action version under test")
    ap.add_argument("--org", help="override the org recorded in the pin")
    ap.add_argument(
        "--action",
        help="the pinned action id (only needed when the store holds more than one)",
    )
    ap.add_argument(
        "--trusted-version",
        help=(
            "which pinned version's goldens to verify against (only needed when the "
            "store holds more than one for this action)"
        ),
    )
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE, help="where pins are kept")
    ap.add_argument(
        "--zip",
        dest="zip_path",
        type=Path,
        help=(
            "verify against the documents in this ARCHIVE rather than wherever they "
            "were when pinned (a fresh checkout, another machine, the archive re-sent)"
        ),
    )
    ap.add_argument(
        "--document-dir",
        type=Path,
        help="verify against the documents in this directory instead of the pinned path",
    )
    ap.add_argument(
        "--extract-to",
        type=Path,
        help=(
            "where --zip is unpacked (default: <store>/verify-documents/). Kept apart "
            "from the pin-time copies, which are what the goldens were read from."
        ),
    )
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help=(
            "proceed when a pinned document is absent from --zip/--document-dir. "
            "Off by default: a partial validation that exits 0 reads as 'every file "
            "is still valid'."
        ),
    )
    ap.add_argument("--run", dest="run_name", help="run name (default: composed from the file)")
    ap.add_argument(
        "--classifier",
        choices=[PINNED_FILE_CLASSIFIER, *pinned_file_custom_names],
        default=None,
        help=(
            f"comparison strategy (default: {PINNED_FILE_CLASSIFIER}). A custom scorer "
            f"registered under .idp-regression-scorers/ may be named here only if its "
            f"base is {PINNED_FILE_CLASSIFIER!r} -- a regression-based one is not offered."
        ),
    )
    ap.add_argument("--yes", action="store_true", help="approve the extraction(s)")
    return ap.parse_args(argv)


def _select_pin_set(args: argparse.Namespace) -> tuple[Path, str, str, int]:
    """`(goldens_dir, action_id, trusted_version, failure_exit_code)`.

    The store's own paths carry the relationship
    (`goldens/<action>/<version>/`), so selecting a pin set is reading a
    directory rather than trusting a recorded field. With exactly one
    (action, version) present it is unambiguous; with several, the
    operator must say which -- guessing would silently verify against a
    different version's goldens than they meant, and the run would look
    entirely normal.
    """
    root = args.store / "goldens"
    available = sorted(
        (action.name, version.name)
        for action in root.iterdir()
        if action.is_dir()
        for version in action.iterdir()
        if version.is_dir() and any(version.glob("*.json"))
    ) if root.is_dir() else []

    if not available:
        legacy = list(args.store.glob("*.golden.json"))
        print(
            f"verify_document: no pinned goldens under {root}"
            + (
                " -- this store uses the pre-2026-09-25 flat layout "
                f"({legacy[0].name}); re-pin to the goldens/<action>/<version>/ layout"
                if legacy
                else " -- run scripts/pin_document.py first"
            ),
            file=sys.stderr,
        )
        return Path(), "", "", 2

    candidates = [
        (action, version)
        for action, version in available
        if (args.action is None or action == args.action)
        and (args.trusted_version is None or version == args.trusted_version)
    ]
    if not candidates:
        print(
            f"verify_document: no goldens pinned for action={args.action!r} "
            f"version={args.trusted_version!r}. Available: "
            + ", ".join(f"{a}/{v}" for a, v in available),
            file=sys.stderr,
        )
        return Path(), "", "", 2
    if len(candidates) > 1:
        print(
            "verify_document: this store holds goldens pinned at several "
            "action/version pairs -- name one with --action / --trusted-version: "
            + ", ".join(f"{a}/{v}" for a, v in candidates),
            file=sys.stderr,
        )
        return Path(), "", "", 2

    action, version = candidates[0]
    return root / action / version, action, version, 0


def _resolve_source(args: argparse.Namespace) -> tuple[str, set[str], int]:
    """`(document_dir, document names present, failure_exit_code)` for a
    `--zip`/`--document-dir` run."""
    if args.zip_path:
        destination = args.extract_to or (args.store / "verify-documents")
        try:
            unpacked, skipped = extract_documents_from_zip(
                args.zip_path, destination, patterns=args.glob
            )
        except ZipRejectedError as exc:
            print(f"verify_document: {exc}", file=sys.stderr)
            return "", set(), 2
        print(
            f"verify_document: unpacked {len(unpacked)} document(s) from "
            f"{args.zip_path.name} -> {destination}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        return str(destination.resolve()), {p.name for p in unpacked}, 0

    found = discover_documents(args.document_dir, args.glob, 100_000)
    return str(args.document_dir.resolve()), {p.name for p in found}, 0


def run(args: argparse.Namespace, run_eval: Any) -> tuple[int, dict[str, Any]]:
    goldens_dir, action_id, trusted, failure = _select_pin_set(args)
    if failure:
        return failure, {}
    pins = read_json_if_present(goldens_dir / "_pins.json")
    pinned_ids = sorted(p.name.removesuffix(".json") for p in goldens_dir.glob("*.json")
                        if p.name != "_pins.json")
    if not pinned_ids:
        print(f"verify_document: nothing pinned in {goldens_dir}", file=sys.stderr)
        return 2, {}

    if args.all:
        # Only THIS dataset's pins (DEBT-93). A pin records the dataset it
        # was provisioned into; selecting another dataset's pins made a
        # second corpus in the same store unverifiable (refused as MISSING
        # after its pin half had spent) and turned foreign pins into
        # `--document` selectors that `select_items`' substring fallback
        # could resolve to OTHER items. A pin with no record predates the
        # field and is kept: `run_eval` refuses a selector it cannot match,
        # before any submit.
        selected = [
            name for name in pinned_ids
            if pins.get(name, {}).get("dataset", args.dataset) == args.dataset
        ]
        if not selected:
            print(
                f"verify_document: nothing pinned for dataset {args.dataset!r} at "
                f"{action_id}/{trusted}",
                file=sys.stderr,
            )
            return 2, {}
    else:
        if not args.file:
            print("verify_document: pass --file or --all", file=sys.stderr)
            return 2, {}
        document_id = args.file.name
        if document_id not in pinned_ids:
            print(
                f"verify_document: {document_id!r} is not pinned at "
                f"{action_id}/{trusted} (pinned: {', '.join(pinned_ids) or 'none'})",
                file=sys.stderr,
            )
            return 2, {}
        selected = [document_id]

    elsewhere = pinned_elsewhere(args.store, args.dataset, set(selected), goldens_dir)
    if elsewhere:
        name, where = next(iter(sorted(elsewhere.items())))
        print(
            f"verify_document: {len(elsewhere)} selected document(s) are ALSO pinned for "
            f"dataset {args.dataset!r} at another action/version (e.g. {name!r} at {where}). "
            "The platform holds one item per (dataset, document) -- whichever was pinned "
            f"last -- so this run could compare against that reading while reporting "
            f"'pinned against {trusted}'. Refusing (DEBT-89).",
            file=sys.stderr,
        )
        return 2, {}

    # Where the bytes come from. By default, wherever each file was when
    # it was pinned; with --zip/--document-dir, from there instead -- the
    # golden is the source of truth either way, and the local files only
    # supply the bytes to re-extract.
    unpinned: list[str] = []
    if args.zip_path or args.document_dir:
        document_dir, present, failure = _resolve_source(args)
        if failure:
            return failure, {}
        # Reported and SKIPPED, never auto-pinned: creating a golden during
        # a validation run would mean the run validates against something
        # it just invented.
        unpinned = sorted(name for name in present if name not in pins)
        missing = sorted(name for name in selected if name not in present)
        selected = [name for name in selected if name in present]
        print(
            f"verify_document: {len(present)} document(s) in the source · "
            f"{len(selected)} pinned and present · {len(unpinned)} with no golden "
            f"(ignored) · {len(missing)} pinned but MISSING",
            file=sys.stderr,
        )
        for name in unpinned[:10]:
            print(f"    no golden, ignored: {name}", file=sys.stderr)
        for name in missing:
            print(f"    pinned but missing: {name}", file=sys.stderr)
        if missing and not args.allow_missing:
            print(
                "verify_document: refusing to run a PARTIAL validation -- its exit 0 "
                "would read as 'every pinned file is still valid'. Pass "
                "--allow-missing to check the ones that are present.",
                file=sys.stderr,
            )
            return 2, {}
        if not selected:
            print("verify_document: nothing to verify", file=sys.stderr)
            return 2, {}
    else:
        # Every pinned file must agree on where it lives: `run_eval` resolves
        # every document_id against ONE `IDP_DOCUMENT_DIR`, so a pin set that
        # spans directories cannot be verified in a single run. Caught here,
        # with both directories named, rather than as a path-containment abort
        # mid-run after quota has been spent.
        directories = {
            pins[document_id]["document_dir"] for document_id in selected if document_id in pins
        }
        if len(directories) > 1:
            print(
                "verify_document: the selected pins live in different directories "
                f"({', '.join(sorted(directories))}) -- run_eval resolves every document "
                "against one IDP_DOCUMENT_DIR. Verify them in separate runs.",
                file=sys.stderr,
            )
            return 2, {}
        if len(directories) != 1:
            print(
                "verify_document: the pin records no usable document directory -- "
                "pass --zip or --document-dir",
                file=sys.stderr,
            )
            return 2, {}
        document_dir = directories.pop()

    # The golden is what the trusted version read from particular BYTES.
    # Re-reading different bytes under the same name would be a comparison
    # of two documents, reported as a regression or as STILL VALID
    # (DEBT-100). Pins written before the digest existed are not checked.
    changed = changed_since_recorded(
        [Path(document_dir) / name for name in selected], pins
    )
    if changed:
        print(
            f"verify_document: {len(changed)} document(s) are not the bytes they were "
            f"pinned from (e.g. {changed[0]!r}) -- refusing to compare a different "
            "document against their golden. Re-pin them, or point at the original files.",
            file=sys.stderr,
        )
        return 2, {}

    # One run authenticates against ONE org. Taking the first pin's silently
    # would verify every other pin against the wrong org (DEBT-109); pins
    # from several orgs must be named explicitly, like several directories.
    orgs = {pins[name].get("org") for name in selected if name in pins} - {None, ""}
    if not args.org and len(orgs) > 1:
        print(
            f"verify_document: the selected pins were taken in {len(orgs)} different orgs -- "
            "pass --org to say which, or verify them in separate runs.",
            file=sys.stderr,
        )
        return 2, {}
    org = args.org or (next(iter(orgs)) if orgs else "")
    action = action_id
    if args.version == trusted:
        print(
            f"verify_document: --version {args.version} is the version the pin was "
            "taken FROM. That compares the trusted version against itself: useful as "
            "a noise check, not as a regression check.",
            file=sys.stderr,
        )

    try:
        confirm_cost(documents=len(selected), extractions_each=1, approved=args.yes)
    except QuotaRefusedError as exc:
        print(f"verify_document: refusing to start — {exc}", file=sys.stderr)
        return 2, {}

    os.environ["IDP_DOCUMENT_DIR"] = document_dir
    run_name = args.run_name or (
        f"verify-{Path(selected[0]).stem if len(selected) == 1 else 'all'}"
        f"-{args.version}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
    )
    print(
        f"verify_document: {len(selected)} pinned file(s) vs version {args.version} "
        f"(pinned against {trusted})",
        file=sys.stderr,
    )

    # A caller-named custom classifier (parser-restricted to a
    # `pinned-file` base, see `_parse_args`) takes the same empty-vs-
    # empty-is-agreement contract as the shipped one and simply tightens
    # it further -- the default remains the shipped `pinned-file`
    # comparison, never `regression`.
    classifier = args.classifier or PINNED_FILE_CLASSIFIER
    started = time.time()
    gate = run_eval(
        action,
        args.version,
        run_name,
        args.dataset,
        org,
        len(selected),
        documents=selected,
        classifier=classifier,
        # Ids this script resolved itself: a non-exact match is a bug, and
        # the substring fallback would measure a different item (DEBT-117).
        exact_documents=True,
    )
    return gate, {
        "classifier": classifier,
        "document_dir": document_dir,
        "unpinned": unpinned,
        "documents": selected,
        "run": run_name,
        "version": args.version,
        "trusted_version": trusted,
        "action": action_id,
        "goldens_dir": str(goldens_dir),
        "gate": gate,
        "started": started,
    }


def main(argv: list[str] | None = None) -> int:
    import logging

    from idp_regression.adapter.transport import sanitize_for_log
    from idp_regression.orchestration.cli import configure_logging
    from idp_regression.orchestration.dotenv_support import load_dotenv
    from idp_regression.orchestration.facade import run_eval
    from idp_regression.orchestration.scorer_store import register_custom_classifiers

    configure_logging()
    load_dotenv()

    # `register_custom_classifiers()` runs BEFORE the parser is built --
    # `--classifier`'s `choices` are frozen at construction time (mirrors
    # `cli.py`). A broken spec is reported and skipped, never fatal to a
    # run that never names it.
    custom_specs, spec_errors = register_custom_classifiers()
    logger = logging.getLogger("idp_regression.orchestration.verify_document")
    for message in spec_errors:
        logger.warning("custom_scorer_not_loaded detail=%s", sanitize_for_log(message))

    args = _parse_args(argv, custom_specs=custom_specs)

    if args.classifier in custom_specs:
        # Run identity (INV-04's reasoning): a spec file can be edited
        # between two runs that both name --classifier my-rule.
        logger.info(
            "custom_scorer_selected name=%s base=%s spec_digest=%s",
            args.classifier,
            custom_specs[args.classifier].base,
            custom_specs[args.classifier].digest(),
        )

    exit_code, summary = run(args, run_eval)
    if not summary:
        return exit_code

    verdict, reason = run_outcome(exit_code, since=summary["started"])
    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(
        f"{verdict}: {len(summary['documents'])} file(s) pinned against "
        f"{summary['trusted_version']}, re-read with {summary['version']}",
        file=sys.stderr,
    )
    if verdict == "RUN FAILED":
        print(
            f"  Not a verdict on the new version: {reason}. Nothing here says the files "
            "changed -- fix the cause and re-run.",
            file=sys.stderr,
        )
    elif exit_code != 0:
        print(f"  {reason}.", file=sys.stderr)
        print(
            "  A critical field's value differs from what the trusted version read.\n"
            "  See the detail:  .venv/bin/python scripts/show_run.py --failures-only",
            file=sys.stderr,
        )
        print(
            "  If the trusted version is not reproducible on these fields, measure it:\n"
            "    .venv/bin/python scripts/noise_floor.py --document-dir <dir> ... \n"
            "  then re-read this run with scripts/show_run.py --baseline <report>.",
            file=sys.stderr,
        )
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
