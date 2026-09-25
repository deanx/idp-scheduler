#!/usr/bin/env python
"""Print a grouped, human-readable summary of one run's results.

The local run artifact (`.idp-regression-run-artifacts/<run_id>.json`,
ADR-0007) is the only place the *values* live -- the evaluation platform
deliberately stores verdicts only (DEBT-18 option B). That makes the
artifact the right place to answer "what happened across all N documents",
but it is raw JSON keyed `run_id -> document_id -> field -> verdict`, which
is not readable at a glance.

This reads that file and prints:

  1. one line per document, with its gate and verdict counts
  2. every non-`match` cell, grouped by document, with expected vs actual
  3. a totals line and the overall gate

Usage
-----
    # the most recent run
    .venv/bin/python scripts/show_run.py

    # a specific run, by id or by path
    .venv/bin/python scripts/show_run.py e79e6f149b984582a0ce29d247fb03aa
    .venv/bin/python scripts/show_run.py .idp-regression-run-artifacts/xyz.json

    # only the failures, for a wide terminal on a screen share
    .venv/bin/python scripts/show_run.py --failures-only

    # drill into ONE document -- every field, matches included
    .venv/bin/python scripts/show_run.py --document inv-002 --all-fields

    # read the run AGAINST the measured noise floor
    .venv/bin/python scripts/show_run.py \
        --baseline .idp-regression-noise-floor/noise-floor-<stamp>.json

`--baseline` is the step that makes a difference rate mean something.
`scripts/noise_floor.py` measures how often the extractor disagrees with
ITSELF on the same document and the same version; this run's per-field
disagreement rate is only evidence where it EXCEEDS that floor. Below it,
a red field is the same variance measured twice, and the failure it
causes is noise wearing a gate's clothes. With `--baseline` every field
is labelled `above floor` (more disagreements than the floor predicts) / `within
floor` / `too few to tell`, and any
document whose failure rests ENTIRELY on within-floor fields is called
out by name -- those are the investigations not worth opening.

⚠️ The comparison is descriptive, not a significance test: two rates and
their observation counts, printed side by side. A field with a handful of
observations on either side is reported as `too few to tell` rather than
given a verdict this data cannot support.

⚠️ This prints EXTRACTED AND EXPECTED VALUES -- invoice totals, customer
names, dates. That is the whole point of the artifact, and it is why the
artifact is written owner-only and never leaves the machine. Think before
putting it on a shared screen with a real golden set: with the synthetic
demo/test packs it is safe, with real customer documents it is a
disclosure.

The per-document gate is RECOMPUTED here with the classifier's own
`overall_gate`, not read from a stored field -- the artifact holds verdict
maps, not gates. Same pure function the run itself used, so this cannot
disagree with the exit code.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from idp_regression.classifier import overall_gate

ARTIFACT_DIR = ".idp-regression-run-artifacts"

# Verdicts that fail a gate when the field opts in; everything else is
# informational. Kept for DISPLAY GROUPING only -- the authoritative
# decision is `overall_gate`, never this set.
_BAD = ("missing", "wrong_value")


def _resolve(arg: str | None) -> str:
    if arg:
        if os.path.isfile(arg):
            return arg
        candidate = os.path.join(ARTIFACT_DIR, f"{arg}.json")
        if os.path.isfile(candidate):
            return candidate
        raise SystemExit(f"show_run: no artifact for {arg!r}")
    files = glob.glob(os.path.join(ARTIFACT_DIR, "*.json"))
    if not files:
        raise SystemExit(
            f"show_run: no artifacts in {ARTIFACT_DIR}/ -- has a run completed?"
        )
    return max(files, key=os.path.getmtime)


def _counts(fields: dict[str, Any]) -> Counter[str]:
    """Count leaf verdicts, flattening a table's rows[] detail."""
    c: Counter[str] = Counter()
    for entry in fields.values():
        if entry.get("verdict") == "detail":
            for row in entry.get("rows", []):
                c[row["verdict"]] += 1
        else:
            c[entry["verdict"]] += 1
    return c


def _leaves(fields: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(label, cell) for every leaf, tables flattened to name[key].column."""
    out: list[tuple[str, dict[str, Any]]] = []
    for name, entry in fields.items():
        if entry.get("verdict") == "detail":
            for row in entry.get("rows", []):
                col = row.get("column")
                label = f"{name}[{row.get('match_key')}]" + (f".{col}" if col else "")
                out.append((label, row))
        else:
            out.append((name, entry))
    return out


#: Below this many observations on EITHER side, a per-field rate is not
#: reported as above or within the floor. Two documents disagreeing once
#: is a 50% rate and means nothing; saying so is the honest output, and
#: silently ranking it alongside a rate measured over 200 observations is
#: how a baseline gets quoted into a decision it cannot support.
MIN_OBSERVATIONS = 20


def _field_key(label: str) -> str:
    """The key a leaf label shares with a noise-floor report.

    `show_run` labels a table leaf `line_items[SKU-1].amount` (the row
    matters when you are reading one document); `noise_floor` aggregates
    it as `line_items.amount` (the row does not matter when you are
    measuring a column's stability). This is the one translation between
    them -- get it wrong and every table column silently reports `too few
    to tell`, which looks like caution and is actually a broken join.
    """
    if "[" in label and "]" in label:
        name, _, rest = label.partition("[")
        column = rest.partition("]")[2].lstrip(".")
        return f"{name}.{column}" if column else name
    return label


def _baseline_index(report: dict[str, Any]) -> dict[str, tuple[float, int]]:
    """`field_key -> (instability_rate, observations)` from a noise-floor
    report's `by_field`."""
    by_field = report.get("by_field")
    if not isinstance(by_field, dict):
        raise SystemExit(
            "show_run: --baseline is not a noise-floor report (no 'by_field'). "
            "Pass a file written by scripts/noise_floor.py."
        )
    index: dict[str, tuple[float, int]] = {}
    for key, stats in by_field.items():
        if isinstance(stats, dict):
            index[key] = (
                float(stats.get("instability_rate", 0.0)),
                int(stats.get("observations", 0)),
            )
    return index


def _run_rates(documents: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """`field_key -> (disagreements, observations)` for THIS run. A
    disagreement is any non-`match` leaf -- the same definition
    `noise_floor` counts with, or the two rates would not be comparable."""
    rates: dict[str, list[int]] = {}
    for fields in documents.values():
        for label, cell in _leaves(fields):
            key = _field_key(label)
            slot = rates.setdefault(key, [0, 0])
            slot[1] += 1
            if cell.get("verdict") != "match":
                slot[0] += 1
    return {key: (bad, total) for key, (bad, total) in rates.items()}


def _above_floor_threshold(floor_rate: float, observations: int) -> float:
    """How many disagreements this field can produce before the floor
    stops explaining them: the count the floor predicts, plus two
    binomial standard deviations of that count.

    Deliberately crude and dependency-free, and NOT a significance test --
    it is a margin, chosen so that a handful of disagreements on a small
    run is not announced as a regression. Two consequences worth stating:
    a field whose floor is 0% is `above` the moment it disagrees once
    (nothing else could explain it), and a field with a high floor needs
    a large excess before this tool will call it real.
    """
    expected = floor_rate * observations
    sigma = math.sqrt(expected * (1.0 - floor_rate))
    return expected + 2.0 * sigma


def _compare(
    run_rates: dict[str, tuple[int, int]],
    baseline: dict[str, tuple[float, int]],
    min_observations: int = MIN_OBSERVATIONS,
) -> dict[str, dict[str, Any]]:
    """Per field: this run's rate, the floor, and a status.

    `above` means this run produced MORE disagreements than the floor
    predicts, by more than `_above_floor_threshold`'s margin. A bare
    rate comparison is not enough and the first run of this tool proved
    it: one disagreement in 30 documents against a 2% floor is 3% > 2%,
    which would read `ABOVE floor` off a rate comparison while being
    exactly what a 2% floor produces. `within` therefore means "no more
    than the floor predicts, allowing for sampling variation" -- not "at
    or below the floor's rate".

    `unknown` means one side has too few observations to say either, and
    `no_floor` means the field was never observed in the baseline at all
    (a field the noise-floor sample did not carry -- not a pass, just an
    absence).
    """
    out: dict[str, dict[str, Any]] = {}
    for key, (bad, total) in run_rates.items():
        run_rate = bad / total if total else 0.0
        floor = baseline.get(key)
        if floor is None:
            status = "no_floor"
            floor_rate, floor_n = None, 0
        else:
            floor_rate, floor_n = floor
            if total < min_observations or floor_n < min_observations:
                status = "unknown"
            elif bad > _above_floor_threshold(floor_rate, total):
                status = "above"
            else:
                status = "within"
        out[key] = {
            "run_rate": run_rate,
            "run_observations": total,
            "run_disagreements": bad,
            "floor_rate": floor_rate,
            "floor_observations": floor_n,
            "status": status,
        }
    return out


def _failure_rests_on_noise(fields: dict[str, Any], comparison: dict[str, dict[str, Any]]) -> bool:
    """True when EVERY cell this document's gate fails on is on a field
    the floor explains (`within`) -- i.e. the whole red verdict is
    accounted for by the extractor's own variance.

    Fail-closed on ignorance: a gate-failing cell whose status is
    `unknown` or `no_floor` keeps the failure real. The claim "this red
    is noise" is the one that stops an investigation, so it is only made
    where the floor actually supports it.
    """
    failing = [
        _field_key(label) for label, cell in _leaves(fields) if _fails(cell)
    ]
    if not failing:
        return False
    return all(comparison.get(key, {}).get("status") == "within" for key in failing)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grouped summary of one run's results.")
    ap.add_argument("run", nargs="?", help="run id, or a path to the artifact JSON")
    ap.add_argument(
        "--failures-only",
        action="store_true",
        help="skip the per-document table; print only the non-match detail",
    )
    ap.add_argument(
        "--document",
        metavar="SUBSTRING",
        help="drill into one document (substring match on the document_id)",
    )
    ap.add_argument(
        "--all-fields",
        action="store_true",
        help="show every field per document, not only the non-matches",
    )
    ap.add_argument(
        "--baseline",
        metavar="NOISE_FLOOR_JSON",
        help=(
            "a scripts/noise_floor.py report: label each field above/within the "
            "floor and name the failures that rest entirely on noise"
        ),
    )
    ap.add_argument(
        "--min-observations",
        type=int,
        default=MIN_OBSERVATIONS,
        help=(
            "below this many observations on either side, a field is reported as "
            f"'too few to tell' rather than judged (default: {MIN_OBSERVATIONS})"
        ),
    )
    args = ap.parse_args(argv)

    path = _resolve(args.run)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    run_id = next(iter(data))
    documents: dict[str, Any] = data[run_id]

    baseline: dict[str, tuple[float, int]] = {}
    baseline_report: dict[str, Any] = {}
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as fh:
            baseline_report = json.load(fh)
        baseline = _baseline_index(baseline_report)

    if args.document:
        picked = {d: f for d, f in documents.items() if args.document.lower() in d.lower()}
        if not picked:
            raise SystemExit(
                f"show_run: no document matching {args.document!r} in this run. "
                f"Present: {', '.join(sorted(documents))}"
            )
        documents = picked

    print(f"\nrun_id   {run_id}")
    print(f"artifact {path}")
    print(f"documents {len(documents)}\n")

    gates: dict[str, str] = {}
    for doc, fields in documents.items():
        gates[doc] = overall_gate(fields)

    if not args.failures_only:
        header = (
            f"{'DOCUMENT':34} {'GATE':6} {'MATCH':>6} {'DIFF':>5} "
            f"{'MISSING':>8} {'FORMAT':>7} {'NEW':>4}"
        )
        print(header)
        print("-" * 78)
        for doc in sorted(documents):
            c = _counts(documents[doc])
            new = c["new_field"] + c["new_line"]
            mark = "FAIL" if gates[doc] == "FAIL" else "pass"
            print(
                f"{doc:34} {mark:6} {c['match']:>6} {c['wrong_value']:>5} "
                f"{c['missing']:>8} {c['wrong_format']:>7} {new:>4}"
            )
        print("-" * 78)

    # Every cell that is not a plain match, grouped by document.
    any_detail = False
    for doc in sorted(documents):
        rows = [
            (label, cell)
            for label, cell in _leaves(documents[doc])
            if args.all_fields or cell["verdict"] != "match"
        ]
        if not rows:
            continue
        any_detail = True
        print(f"\n{doc}   [{gates[doc]}]")
        for label, cell in sorted(rows):
            v = cell["verdict"]
            flag = "  <-- FAILS THE GATE" if _fails(cell) else ""
            print(f"    {label:38} {v}{_floor_note(label, baseline)}")
            if v in _BAD or v == "wrong_format" or args.all_fields:
                print(f"        expected  {cell.get('expected')!r}")
                print(f"        actual    {cell.get('actual')!r}{flag}")
            elif flag:
                print(f"       {flag.strip()}")

    if not any_detail:
        print("\nevery field matched in every document.")

    comparison: dict[str, dict[str, Any]] = {}
    if baseline:
        comparison = _compare(_run_rates(documents), baseline, args.min_observations)
        _print_baseline_section(comparison, baseline_report)

    total: Counter[str] = Counter()
    for fields in documents.values():
        total += _counts(fields)
    failed = [d for d, g in gates.items() if g == "FAIL"]
    print(
        f"\ntotals   match={total['match']} wrong_value={total['wrong_value']} "
        f"missing={total['missing']} wrong_format={total['wrong_format']} "
        f"new={total['new_field'] + total['new_line']}"
    )
    if failed:
        tail = f"  ({len(failed)} of {len(documents)} documents: {', '.join(sorted(failed))})"
    else:
        tail = f"  (all {len(documents)} documents passed)"
    print(f"OVERALL  {'FAIL' if failed else 'PASS'}{tail}")
    if comparison and failed:
        noise_only = sorted(
            doc for doc in failed if _failure_rests_on_noise(documents[doc], comparison)
        )
        if noise_only:
            print(
                f"         {len(noise_only)} of those {len(failed)} failure(s) rest ENTIRELY "
                "on fields the floor explains:"
            )
            for doc in noise_only:
                print(f"           {doc}")
            print(
                "         Those are the extractor disagreeing with itself, not a "
                "regression. The gate still FAILS -- the floor explains a red, it "
                "does not clear it."
            )
        else:
            print(
                "         every failing document fails on at least one field ABOVE "
                "the floor (or on one the floor cannot speak to)."
            )
    print()
    # The exit code is the GATE's, never the floor's: a run that fails is
    # a run that fails, however well its failure is explained (INV-08).
    return 1 if failed else 0


def _floor_note(label: str, baseline: dict[str, tuple[float, int]]) -> str:
    """The inline `[floor N%]` marker on a detail line. Empty when no
    baseline was given, so the unannotated output is byte-identical to
    what this script printed before `--baseline` existed."""
    if not baseline:
        return ""
    floor = baseline.get(_field_key(label))
    if floor is None:
        return "   [no floor for this field]"
    rate, observations = floor
    return f"   [floor {rate:.0%} of {observations}]"


def _print_baseline_section(
    comparison: dict[str, dict[str, Any]], report: dict[str, Any]
) -> None:
    """The run's per-field disagreement rate beside the floor's."""
    identity = report.get("run_identity", {})
    summary = report.get("summary", {})
    print("\nNOISE FLOOR COMPARISON")
    print(
        f"  baseline  {report.get('generated_at', '?')}  "
        f"action={identity.get('action', '?')} version={identity.get('version', '?')}  "
        f"({summary.get('comparisons', '?')} repeat comparison(s) of "
        f"{summary.get('documents', '?')} document(s))"
    )
    run_bad = sum(c["run_disagreements"] for c in comparison.values())
    run_total = sum(c["run_observations"] for c in comparison.values())
    run_rate = run_bad / run_total if run_total else 0.0
    floor_rate = float(summary.get("field_instability_rate", 0.0))
    print(
        f"  this run disagrees with the golden on {run_rate:.1%} of field "
        f"observations ({run_bad}/{run_total});"
    )
    print(
        f"  the SAME version disagreed with itself on {floor_rate:.1%}."
        + ("  -> the difference is within the floor." if run_rate <= floor_rate else "")
    )
    print()
    label = {
        "above": "ABOVE floor",
        "within": "within floor",
        "unknown": "too few to tell",
        "no_floor": "no floor",
    }
    order = {"above": 0, "unknown": 1, "no_floor": 2, "within": 3}
    print(
        "  'within floor' = no more disagreements than the floor predicts, allowing "
        "for sampling\n  variation (the floor's own count + 2 SD). Not a significance "
        "test -- read the counts."
    )
    print()
    print(f"  {'FIELD':32} {'RUN':>14} {'FLOOR':>14}   VERDICT")
    print("  " + "-" * 76)
    for key, c in sorted(
        comparison.items(), key=lambda kv: (order[kv[1]["status"]], -kv[1]["run_rate"], kv[0])
    ):
        if c["status"] == "within" and c["run_disagreements"] == 0:
            continue  # a field that matched everywhere needs no line
        floor_cell = (
            f"{c['floor_rate']:.0%} of {c['floor_observations']}"
            if c["floor_rate"] is not None
            else "-"
        )
        print(
            f"  {key:32} {c['run_rate']:>6.0%} of {c['run_observations']:<5} "
            f"{floor_cell:>14}   {label[c['status']]}"
        )


def _fails(cell: dict[str, Any]) -> bool:
    """Whether this individual cell is one the gate fails on.

    Display helper only. Mirrors BR2/BR3 + the DEBT-80 `format_critical`
    opt-in; `overall_gate` above remains the authority for the verdict
    actually reported.
    """
    v = cell["verdict"]
    if v in _BAD and cell.get("critical"):
        return True
    return v == "wrong_format" and bool(cell.get("format_critical"))


if __name__ == "__main__":
    raise SystemExit(main())
