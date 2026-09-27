#!/usr/bin/env python
"""Measure how much the extractor disagrees with ITSELF. The noise floor.

Why this is the first thing to run
----------------------------------
A regression run reports a difference rate between two action versions.
That number is only interpretable against a baseline: **how much
difference does the same version produce against itself?**

These extractors are LLM-backed. Run the same document through the same
action version twice and some fields come back different — a date
formatted another way, a trailing zero, a line item split differently.
That is the noise floor. Until it is measured:

* a candidate model showing **4% disagreement** might be indistinguishable
  from the incumbent, or might be meaningfully worse — you cannot tell;
* a golden set drafted from one pass through the incumbent
  (`scripts/bootstrap_golden_set.py`) bakes every one of those coin-flips
  in as "expected", and the gate then fails candidates for reproducing
  ordinary variance;
* every red build costs an investigation, and the ones caused by noise
  are the ones that teach people to ignore the gate.

With the floor measured, the comparison becomes sayable: *"the incumbent
disagrees with itself on 3% of fields; the candidate disagrees with the
incumbent on 25%. That is real."* Or: *"...on 4%. That is noise."*

What it does
------------
For each document: extract `--repeats` times against ONE action version.
Take the first pass as the reference, draft a golden from it (the same
rules `scripts/draft_golden.py` uses: a field with a value is `critical`,
an empty one is not), and classify every later pass against it with the
`pinned-file` classifier. The differences are, by construction, pure
self-inconsistency: same document, same prompt, same version.

**Why `pinned-file` and not the default `regression` classifier
(DEBT-88).** The two differ in exactly one rule: under `regression` an
empty expected value read empty again is `missing`; under `pinned-file` it
is `match`. For a measurement of self-consistency the second is the only
honest reading -- the extractor read nothing, twice. With `regression` a
field empty on every pass scored 100% unstable, which inflated the
headline rate, every per-field rate `calibrate_golden` demotes on (widening
the gate's blind spots), and the rates `show_run --baseline` uses to label
real reds `within floor`. Invented content (empty, then a value) is still
`wrong_value` under both.

It reports two rates, and they answer different questions:

* **field instability** — of every (document, field) observation, what
  share came back different? This is the number to compare a candidate's
  disagreement rate against.
* **gate noise** — what share of repeat runs would have FAILED the gate
  against a golden drafted from the first run? This is the number that
  predicts how many red builds you will get for no reason.

Values, and what is written
---------------------------
The report records field NAMES, verdicts and counts — never extracted
values — matching the rule in `CLAUDE.md ## Domain`. `--include-values`
adds the differing values for diagnosis; the file is written owner-only
either way, and belongs in the same "never commit, never upload"
category as `.idp-regression-run-artifacts/`.

⚠️ **This spends `documents x repeats` real IDP extractions.** A
100-document, 2-repeat sample (200 extractions) is enough to size the
floor and is the recommended starting point — not the whole corpus.

Usage
-----
    # what would it cost? no network, no quota
    .venv/bin/python scripts/noise_floor.py --document-dir ~/customer-invoices \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 --plan

    # the real measurement
    .venv/bin/python scripts/noise_floor.py --document-dir ~/customer-invoices \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 \\
        --max-documents 100 --repeats 2 --yes
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from _batch import (  # noqa: E402
    DEFAULT_DOCUMENT_PATTERNS,
    DocumentFailedError,
    OutputNotWritableError,
    QuotaRefusedError,
    SupportsExtract,
    ZipRejectedError,
    assert_writable_output,
    confirm_cost,
    discover_documents,
    extract_documents_from_zip,
    extract_with_containment,
    progress,
    write_private_json,
)

from idp_regression.classifier.gate import classify_pinned_file, overall_gate  # noqa: E402
from idp_regression.orchestration.dotenv_support import load_dotenv  # noqa: E402

_spec = importlib.util.spec_from_file_location("draft_golden", _SCRIPTS_DIR / "draft_golden.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - a broken checkout
    raise RuntimeError("scripts/draft_golden.py is missing or unloadable")
draft_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(draft_golden)

DEFAULT_MAX_DOCUMENTS = 100
DEFAULT_OUT_DIR = Path(".idp-regression-noise-floor")

#: The one verdict that means "these two runs agreed". Everything else —
#: `wrong_value`, `wrong_format`, `missing`, `new_field`, `new_line` — is
#: the same version contradicting itself, which is what is being counted.
#: `wrong_format` is counted as instability on purpose: BR3 calls format
#: informational for the gate, but a version that cannot format its own
#: output consistently is exactly what a `format_critical` field would
#: later be failed for, so the floor must show it.
_AGREEMENT = "match"


def _observations(verdicts: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten a verdict map to `(field_key, verdict)` pairs, one per
    comparable leaf. A table is a container, so its rows contribute one
    observation per column, keyed `table.column` — otherwise a 40-line
    invoice would count as a single observation and line-item noise, the
    noisiest part of these extractions, would be invisible."""
    flat: list[tuple[str, str]] = []
    for name, entry in verdicts.items():
        verdict = entry.get("verdict")
        if verdict == "detail":
            for row in entry.get("rows", []):
                column = row.get("column") or "?"
                flat.append((f"{name}.{column}", str(row.get("verdict"))))
            continue
        flat.append((name, str(verdict)))
    return flat


def _differing_values(verdicts: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """The expected/actual pair for each disagreeing leaf — only written
    under `--include-values`."""
    out: dict[str, dict[str, str | None]] = {}
    for name, entry in verdicts.items():
        if entry.get("verdict") == "detail":
            for row in entry.get("rows", []):
                if row.get("verdict") != _AGREEMENT:
                    key = f"{name}.{row.get('column')}[{row.get('match_key')}]"
                    out[key] = {"first_run": row.get("expected"), "repeat": row.get("actual")}
            continue
        if entry.get("verdict") != _AGREEMENT:
            out[name] = {"first_run": entry.get("expected"), "repeat": entry.get("actual")}
    return out


def aggregate(comparisons: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure: turn per-comparison records into the report body. Kept
    separate from the batch loop so the arithmetic — the part that would
    silently mis-state a baseline — is unit-testable without an IDP
    call."""
    field_totals: Counter[str] = Counter()
    field_unstable: Counter[str] = Counter()
    field_verdicts: dict[str, Counter[str]] = defaultdict(Counter)
    per_document: dict[str, dict[str, Any]] = {}

    for comparison in comparisons:
        document_id = comparison["document_id"]
        doc = per_document.setdefault(
            document_id,
            {"document_id": document_id, "comparisons": 0, "gate": [], "unstable_fields": []},
        )
        doc["comparisons"] += 1
        doc["gate"].append(comparison["gate"])
        for field, verdict in comparison["observations"]:
            field_totals[field] += 1
            if verdict != _AGREEMENT:
                field_unstable[field] += 1
                field_verdicts[field][verdict] += 1
                if field not in doc["unstable_fields"]:
                    doc["unstable_fields"].append(field)

    observations = sum(field_totals.values())
    unstable = sum(field_unstable.values())
    failing = sum(1 for c in comparisons if c["gate"] == "FAIL")

    by_field = {
        field: {
            "observations": field_totals[field],
            "unstable": field_unstable[field],
            "instability_rate": round(field_unstable[field] / field_totals[field], 4),
            "verdicts": dict(field_verdicts[field]),
        }
        for field in sorted(field_totals, key=lambda f: (-field_unstable[f], f))
    }

    return {
        "summary": {
            "documents": len(per_document),
            "comparisons": len(comparisons),
            "field_observations": observations,
            "unstable_observations": unstable,
            # THE number: the share of field observations on which the
            # same version contradicted itself.
            "field_instability_rate": round(unstable / observations, 4) if observations else 0.0,
            "comparisons_failing_gate": failing,
            "gate_noise_rate": round(failing / len(comparisons), 4) if comparisons else 0.0,
            "documents_with_any_instability": sum(
                1 for d in per_document.values() if d["unstable_fields"]
            ),
            "extraction_failures": len(failures),
        },
        "by_field": by_field,
        "by_document": [per_document[k] for k in sorted(per_document)],
        "failures": failures,
    }


def _interpretation(summary: dict[str, Any]) -> list[str]:
    """The sentences that make the number usable. Printed and embedded in
    the report, because a bare rate in a JSON file gets quoted later
    without the caveat that makes it mean anything."""
    rate = summary["field_instability_rate"]
    gate = summary["gate_noise_rate"]
    lines = [
        f"The incumbent disagrees with ITSELF on {rate:.1%} of field observations "
        f"({summary['unstable_observations']}/{summary['field_observations']}), "
        f"over {summary['comparisons']} repeat comparison(s) of "
        f"{summary['documents']} document(s).",
        "",
        f"A candidate version must differ from the incumbent by MORE than {rate:.1%} "
        "before that difference is evidence of anything. Below it, you are "
        "measuring the same variance twice.",
        f"Expect roughly {gate:.1%} of documents to fail the gate for no reason at "
        "all, if a golden is drafted from a single pass.",
    ]
    if summary["field_observations"] == 0:
        return ["No comparable field observations. Nothing can be concluded."]
    if rate == 0.0:
        lines += [
            "",
            "A floor of exactly 0% over a small sample is a weak result, not a",
            "strong one: it may mean deterministic extraction, or it may mean the",
            "sample was too small or too easy. Widen it before relying on it.",
        ]
    elif rate > 0.10:
        lines += [
            "",
            "Above 10%, this extractor is not stable enough for a per-field gate",
            "on the unstable fields. Either mark them non-critical, or the gate",
            "will spend its credibility on noise.",
        ]
    return lines


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="noise_floor",
        description="Measure one action version's self-consistency (the noise floor).",
    )
    ap.add_argument("--document-dir", type=Path, help="directory of documents to sample")
    ap.add_argument(
        "--zip",
        dest="zip_path",
        type=Path,
        help="a ZIP of documents to sample instead of --document-dir (unpacked flat)",
    )
    ap.add_argument(
        "--extract-to",
        type=Path,
        help=f"where --zip is unpacked (default: {DEFAULT_OUT_DIR}/documents/)",
    )
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument("--org", required=True, help="organisation / business group id")
    ap.add_argument("--action", required=True, help="IDP action id")
    ap.add_argument("--version", required=True, help="the ONE action version to measure")
    ap.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="extractions per document (>=2; the 1st is the reference). Default: 2",
    )
    ap.add_argument(
        "--max-documents",
        type=int,
        default=DEFAULT_MAX_DOCUMENTS,
        help=f"ceiling on documents sampled (default: {DEFAULT_MAX_DOCUMENTS})",
    )
    ap.add_argument(
        "--out", type=Path, help="report path (default: under .idp-regression-noise-floor/)"
    )
    ap.add_argument(
        "--include-values",
        action="store_true",
        help="record the differing values for diagnosis (default: names + verdicts only)",
    )
    ap.add_argument("--plan", action="store_true", help="print the cost and stop")
    ap.add_argument("--yes", action="store_true", help="approve spending the extractions")
    return ap.parse_args(argv)


def run(args: argparse.Namespace, adapter: SupportsExtract | None) -> tuple[int, dict[str, Any]]:
    """The measurement, with the adapter injected so the tests can drive
    a scripted sequence of outputs and assert the arithmetic."""
    document_dir = args.document_dir
    if args.zip_path:
        document_dir = args.extract_to or DEFAULT_OUT_DIR / "documents"
        try:
            unpacked, skipped = extract_documents_from_zip(
                args.zip_path, document_dir, patterns=args.glob, dry_run=args.plan
            )
        except ZipRejectedError as exc:
            print(f"noise_floor: {exc}", file=sys.stderr)
            return 2, {}
        verb = "would unpack" if args.plan else "unpacked"
        print(
            f"noise_floor: {verb} {len(unpacked)} document(s) from {args.zip_path.name} "
            f"-> {document_dir}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        if len(skipped) > 10:
            print(f"    ... and {len(skipped) - 10} more skipped", file=sys.stderr)
        # Sampled from what THIS archive unpacked, never by re-listing the
        # shared directory: that held every earlier corpus, so a plan of 2
        # extractions spent 14 and the report described a mixed corpus
        # (DEBT-90). Same `sorted()[:N]` rule as `discover_documents`.
        documents = sorted(unpacked)[: args.max_documents]
        if args.plan:
            print(f"noise_floor: {len(documents)} document(s) sampled", file=sys.stderr)
            confirm_cost(
                documents=len(documents), extractions_each=args.repeats, approved=True
            )
            print("noise_floor: --plan, stopping before any IDP call.", file=sys.stderr)
            return 0, {}
    else:
        documents = discover_documents(document_dir, args.glob, args.max_documents)
    print(f"noise_floor: {len(documents)} document(s) sampled", file=sys.stderr)

    # Where the report goes is decided and CHECKED before anything is spent:
    # it is written once, at the end, so an unwritable path used to lose the
    # whole paid-for measurement (DEBT-99).
    out = args.out or DEFAULT_OUT_DIR / (
        f"noise-floor-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    if not args.plan:
        try:
            assert_writable_output(out)
        except OutputNotWritableError as exc:
            print(f"noise_floor: refusing to start — {exc}", file=sys.stderr)
            return 2, {}

    try:
        confirm_cost(
            documents=len(documents), extractions_each=args.repeats, approved=args.yes or args.plan
        )
    except QuotaRefusedError as exc:
        print(f"noise_floor: refusing to start — {exc}", file=sys.stderr)
        return 2, {}

    if args.plan:
        print("noise_floor: --plan, stopping before any IDP call.", file=sys.stderr)
        return 0, {}

    comparisons: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    started = time.monotonic()
    total = len(documents)
    # Counted per attempt, not planned (DEBT-109): a document whose reference
    # read fails never has its repeats attempted, so `total * repeats`
    # overstated the spend. An attempt that failed still counts: it may have
    # been submitted, and an over-report is the safe direction for quota.
    attempted = 0

    for index, document in enumerate(documents, start=1):
        document_id = document.name
        progress(index, total, document_id, started)
        attempted += 1
        try:
            reference = extract_with_containment(
                adapter, document, action_id=args.action, version=args.version, attempt=1
            )
            golden, _notes = draft_golden._draft_from_normalized(reference, document_id)
        except DocumentFailedError as exc:
            failures.append(exc.as_record())
            print(f"    FAILED (reference pass): {exc.error_type}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - INV-02: type name only
            failures.append(
                {"document_id": document_id, "attempt": 1, "error_type": type(exc).__name__}
            )
            print(f"    FAILED (drafting the reference): {type(exc).__name__}", file=sys.stderr)
            continue

        for attempt in range(2, args.repeats + 1):
            attempted += 1
            try:
                repeat = extract_with_containment(
                    adapter,
                    document,
                    action_id=args.action,
                    version=args.version,
                    attempt=attempt,
                )
                verdicts = classify_pinned_file(golden, repeat)
            except DocumentFailedError as exc:
                failures.append(exc.as_record())
                print(f"    FAILED (repeat {attempt}): {exc.error_type}", file=sys.stderr)
                continue
            except Exception as exc:  # noqa: BLE001 - INV-02
                failures.append(
                    {
                        "document_id": document_id,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    }
                )
                print(f"    FAILED (classifying repeat {attempt}): "
                      f"{type(exc).__name__}", file=sys.stderr)
                continue

            record: dict[str, Any] = {
                "document_id": document_id,
                "repeat": attempt,
                "gate": overall_gate(verdicts),
                "observations": _observations(verdicts),
            }
            if args.include_values:
                record["differences"] = _differing_values(verdicts)
            comparisons.append(record)

    body = aggregate(comparisons, failures)
    if not args.include_values:
        # `observations` is name+verdict only, but it is per-comparison
        # bulk that nobody reads; the aggregate is the product. Dropped
        # so the report stays readable.
        for comparison in comparisons:
            comparison.pop("observations", None)
    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "run_identity": {"org": args.org, "action": args.action, "version": args.version},
        "sample": {
            "document_dir": str(document_dir),
        "zip": str(args.zip_path) if args.zip_path else None,
            "glob": args.glob,
            "repeats": args.repeats,
            "extractions_spent": attempted,
            "extractions_planned": total * args.repeats,
        },
        "includes_values": bool(args.include_values),
        **body,
        "interpretation": _interpretation(body["summary"]),
        "caveats": [
            "The reference golden is drafted from the FIRST pass with every field "
            "critical, so this measures disagreement, not correctness.",
            "A table's match_key is guessed (see scripts/draft_golden.py). A wrong "
            "guess mis-pairs rows and inflates line-item instability — check "
            "by_field before trusting a high table rate.",
            "Measured against ONE action version. It says nothing about any other.",
        ],
    }
    if args.include_values:
        report["comparisons"] = comparisons

    write_private_json(out, report)
    report["_out"] = str(out)
    return (1 if failures else 0), report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if bool(args.document_dir) == bool(args.zip_path):
        print(
            "noise_floor: pass --document-dir OR --zip (exactly one) -- what was "
            "sampled must be unambiguous",
            file=sys.stderr,
        )
        return 2
    if args.repeats < 2:
        print(
            "noise_floor: --repeats must be at least 2 — the first pass is the "
            "reference and a single pass has nothing to disagree with.",
            file=sys.stderr,
        )
        return 2

    load_dotenv()

    adapter: SupportsExtract | None = None
    if not args.plan:
        try:
            from idp_regression.adapter.idp_client import make_idp_adapter

            adapter = make_idp_adapter(args.org)
        except Exception as exc:  # noqa: BLE001 - INV-02
            print(
                f"noise_floor: could not build the IDP adapter: {type(exc).__name__} "
                "(check IDP_CLIENT_ID / IDP_CLIENT_SECRET / IDP_REGION)",
                file=sys.stderr,
            )
            return 1

    exit_code, report = run(args, adapter)
    if not report:
        return exit_code

    summary = report["summary"]
    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print("NOISE FLOOR", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    for line in report["interpretation"]:
        print(line, file=sys.stderr)
    print("", file=sys.stderr)
    print("Least stable fields:", file=sys.stderr)
    unstable = [(f, s) for f, s in report["by_field"].items() if s["unstable"]][:10]
    for field, stats in unstable or []:
        print(
            f"  {field:<32} {stats['instability_rate']:>7.1%}  "
            f"({stats['unstable']}/{stats['observations']}) {stats['verdicts']}",
            file=sys.stderr,
        )
    if not unstable:
        print("  (none disagreed)", file=sys.stderr)
    if summary["extraction_failures"]:
        print(
            f"\n{summary['extraction_failures']} extraction(s) failed and were excluded.",
            file=sys.stderr,
        )
    print(f"\nreport: {report['_out']}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
