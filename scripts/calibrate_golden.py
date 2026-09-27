#!/usr/bin/env python
"""Do mechanically what the reconciliation worklist asked a human to do.

`scripts/bootstrap_golden_set.py` drafts a golden set and hands over a
worklist with four instructions. Three of them are decidable from data
this pipeline already has, and this script decides them across the whole
corpus at once:

  worklist #1  "turn OFF critical for fields legitimately absent on some
               documents"                        -> corpus presence stats
  worklist #3  "confirm each table's match_key"  -> corpus-wide agreement
  worklist #4  "fields unstable between two runs of the SAME version
               should not be critical"           -> the noise-floor report

The fourth -- *is the extractor's answer actually right?* -- is not
decidable here and never will be: the only oracle is the document. What
this script does about that is refuse to hide it. The output is labelled
an **agreement baseline**, and the one consequential side effect of
calibrating, the set of fields the gate can no longer see a regression
in, is reported as a BLIND-SPOT list rather than left implicit.

Why blind spots are the headline
--------------------------------
Every rule here demotes a field from `critical` to not-critical. That is
a FAIL-OPEN change, and this project's stated worst outcome is a
silently-wrong green build (`CLAUDE.md ## Rigor`). So:

* demotion happens only on evidence -- a measured floor above
  `--noise-tolerance`, or absence across a measured share of the corpus;
* every demotion is recorded with its reason and its numbers;
* the blind-spot list is printed, written to the report, and counted, so
  "this gate is green" can always be read alongside "and here is what it
  is not looking at".

Nothing is deleted and no value is ever rewritten: calibration only ever
flips `critical`, unifies a table's `match_key`, and unifies a field's
inferred `type`. The expected values are exactly what was drafted.

Usage
-----
    .venv/bin/python scripts/calibrate_golden.py \\
        --golden-file golden/customer.json \\
        --noise-floor .idp-regression-noise-floor/noise-floor-<stamp>.json \\
        --out golden/customer.calibrated.json

Writes the calibrated set, `<out>.calibration.json` (every decision, with
numbers) and `<out>.calibration.md` (the same, readable). Without
`--noise-floor` it still applies the corpus-presence and match_key rules
and says plainly that the stability rule did not run.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import jsonschema  # noqa: E402
from _batch import write_private_json  # noqa: E402

from idp_regression.platform.schema import load_golden_schema  # noqa: E402

#: A field whose own version disagrees with itself MORE often than this
#: cannot carry a gate: at 2% over a 1,000-document dataset it is ~20 red
#: documents per run, none of them a regression. Above the threshold the
#: field is demoted; below it, the occasional red is rare enough to be
#: worth investigating, which is what a gate is for.
DEFAULT_NOISE_TOLERANCE = 0.02

#: A field missing from more than this share of the corpus is a field
#: some documents legitimately do not carry (a PO number, a tax line).
#: Left critical, it fails those documents forever.
DEFAULT_SPARSE_THRESHOLD = 0.10

#: Below this many floor observations, the stability rule abstains rather
#: than demoting on a rate it cannot support (same bar `show_run
#: --baseline` uses).
DEFAULT_MIN_OBSERVATIONS = 20

_MATCH_KEY_PREFERENCE = ("sku", "id", "code", "item_code", "line_id")


def _floor_index(report: dict[str, Any]) -> dict[str, tuple[float, int]]:
    by_field = report.get("by_field")
    if not isinstance(by_field, dict):
        raise SystemExit(
            "calibrate_golden: --noise-floor is not a noise-floor report (no 'by_field')."
        )
    return {
        key: (float(s.get("instability_rate", 0.0)), int(s.get("observations", 0)))
        for key, s in by_field.items()
        if isinstance(s, dict)
    }


def _presence(golden_set: dict[str, Any]) -> tuple[Counter[str], int]:
    """How many documents carry a non-empty value for each field."""
    present: Counter[str] = Counter()
    for entry in golden_set.values():
        for name, spec in entry.get("fields", {}).items():
            if str(spec.get("value", "")).strip():
                present[name] += 1
    return present, len(golden_set)


def _decide_field(
    *,
    present: int,
    documents: int,
    floor: tuple[float, int] | None,
    noise_tolerance: float,
    sparse_threshold: float,
    min_observations: int,
) -> dict[str, Any]:
    """One field's `critical`, with the reason and the numbers behind it.

    Order matters: the stability rule is checked first, because a field
    that is both unstable and sparse is demoted for the reason that will
    still be true after more documents are added.
    """
    absent_share = 1.0 - (present / documents) if documents else 0.0
    if floor is not None:
        rate, observations = floor
        if observations >= min_observations and rate > noise_tolerance:
            return {
                "critical": False,
                "reason": "unstable",
                "detail": (
                    f"the same version disagreed with itself on {rate:.0%} of "
                    f"{observations} observations (tolerance {noise_tolerance:.0%})"
                ),
                "floor_rate": rate,
                "floor_observations": observations,
                "documents_present": present,
                "documents": documents,
            }
    if absent_share > sparse_threshold:
        return {
            "critical": False,
            "reason": "sparse",
            "detail": (
                f"absent from {absent_share:.0%} of the corpus "
                f"({documents - present} of {documents} documents; "
                f"threshold {sparse_threshold:.0%})"
            ),
            "floor_rate": floor[0] if floor else None,
            "floor_observations": floor[1] if floor else 0,
            "documents_present": present,
            "documents": documents,
        }
    return {
        "critical": True,
        "reason": "stable and present",
        "detail": (
            f"present in {present} of {documents} documents"
            + (
                f"; floor {floor[0]:.0%} of {floor[1]} observations"
                if floor
                else "; no floor measured for this field"
            )
        ),
        "floor_rate": floor[0] if floor else None,
        "floor_observations": floor[1] if floor else 0,
        "documents_present": present,
        "documents": documents,
    }


def _unify_match_keys(golden_set: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One `match_key` per table, across the whole corpus.

    `draft_golden` picks a match_key per document, from that document's
    rows alone. Over a corpus that silently produces DIFFERENT keys for
    the same table -- document A joined on `sku`, document B on
    `description` -- and a join key that varies by document is not a
    contract. This picks one: the preferred column if it is present and
    non-empty in every row of every document, else the most common
    drafted choice that satisfies the same test.
    """
    drafted: dict[str, Counter[str]] = {}
    usable: dict[str, set[str]] = {}
    for entry in golden_set.values():
        for tname, block in entry.get("tables", {}).items():
            rows = block.get("rows", [])
            drafted.setdefault(tname, Counter())[block.get("match_key", "")] += 1
            columns = {
                col
                for col in (rows[0] if rows else {})
                if all(str(row.get(col, "")).strip() for row in rows)
            }
            usable[tname] = usable[tname] & columns if tname in usable else columns

    out: dict[str, dict[str, Any]] = {}
    for tname, counts in drafted.items():
        candidates = usable.get(tname, set())
        chosen = next((c for c in _MATCH_KEY_PREFERENCE if c in candidates), None)
        if chosen is None:
            chosen = next(
                (name for name, _ in counts.most_common() if name in candidates), None
            )
        out[tname] = {
            "chosen": chosen,
            "drafted": dict(counts),
            "usable_in_every_document": sorted(candidates),
            "agreed": len(counts) == 1 and chosen in counts,
        }
    return out


def _unify_types(golden_set: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One inferred `type` per field. A field drafted `number` on 29
    documents and `text` on one is a field whose values vary in shape;
    the majority type is used everywhere so the classifier applies one
    canonical comparison to it, and the minority is reported."""
    counts: dict[str, Counter[str]] = {}
    for entry in golden_set.values():
        for name, spec in entry.get("fields", {}).items():
            counts.setdefault(name, Counter())[str(spec.get("type", "text"))] += 1
    return {
        name: {
            "chosen": c.most_common(1)[0][0],
            "counts": dict(c),
            "agreed": len(c) == 1,
        }
        for name, c in counts.items()
    }


def calibrate(
    golden_set: dict[str, Any],
    floor: dict[str, tuple[float, int]] | None,
    *,
    noise_tolerance: float = DEFAULT_NOISE_TOLERANCE,
    sparse_threshold: float = DEFAULT_SPARSE_THRESHOLD,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return `(calibrated_set, report)`. Pure -- no I/O, so the rules
    that decide what a gate can no longer see are unit-testable on their
    own."""
    calibrated = copy.deepcopy(golden_set)
    present, documents = _presence(calibrated)
    types = _unify_types(calibrated)
    match_keys = _unify_match_keys(calibrated)

    field_names: set[str] = set()
    for entry in calibrated.values():
        field_names |= set(entry.get("fields", {}))

    decisions = {
        name: _decide_field(
            present=present.get(name, 0),
            documents=documents,
            floor=(floor or {}).get(name),
            noise_tolerance=noise_tolerance,
            sparse_threshold=sparse_threshold,
            min_observations=min_observations,
        )
        for name in sorted(field_names)
    }

    table_decisions: dict[str, dict[str, Any]] = {}
    for tname, info in match_keys.items():
        columns = [
            key.split(".", 1)[1]
            for key in (floor or {})
            if key.startswith(f"{tname}.") and "." in key
        ]
        unstable = [
            col
            for col in columns
            if (floor or {})[f"{tname}.{col}"][1] >= min_observations
            and (floor or {})[f"{tname}.{col}"][0] > noise_tolerance
        ]
        critical = not unstable and info["chosen"] is not None
        table_decisions[tname] = {
            "critical": critical,
            "match_key": info["chosen"],
            "reason": (
                "no usable match_key in every document"
                if info["chosen"] is None
                else ("unstable columns: " + ", ".join(sorted(unstable)))
                if unstable
                else "stable, one match_key across the corpus"
            ),
            "match_key_agreed": info["agreed"],
            "drafted_match_keys": info["drafted"],
        }

    validator = jsonschema.Draft7Validator(load_golden_schema())
    for document_key, entry in calibrated.items():
        for name, spec in entry.get("fields", {}).items():
            spec["critical"] = decisions[name]["critical"]
            drafted = spec.get("type", "text")
            chosen = types[name]["chosen"]
            if chosen == drafted:
                continue
            spec["type"] = chosen
            # The majority type is applied only where THIS entry's value
            # has that shape. An empty value, or a `1,250.00` drafted as
            # text, fails the committed schema's `if/then` for `number` /
            # `date` / `id`; writing it anyway produced a set that
            # provisioned partially, and a run against the partial dataset
            # could pass (DEBT-92). Checked with the same schema provisioning
            # uses, so the two can never disagree about what is valid.
            if not validator.is_valid(entry):
                spec["type"] = drafted
                types[name].setdefault("kept_drafted", []).append(document_key)
                types[name]["agreed"] = False
        for tname, block in entry.get("tables", {}).items():
            decision = table_decisions[tname]
            block["critical"] = decision["critical"]
            if decision["match_key"]:
                block["match_key"] = decision["match_key"]

    blind_spots = [n for n, d in decisions.items() if not d["critical"]]
    blind_spots += [f"{t} (table)" for t, d in table_decisions.items() if not d["critical"]]

    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "thresholds": {
            "noise_tolerance": noise_tolerance,
            "sparse_threshold": sparse_threshold,
            "min_observations": min_observations,
        },
        "corpus": {"documents": documents, "fields": len(decisions)},
        "noise_floor_applied": bool(floor),
        "fields": decisions,
        "tables": table_decisions,
        "types": {n: t for n, t in types.items() if not t["agreed"]},
        "blind_spots": sorted(blind_spots),
        "still_human": _still_human(bool(floor), table_decisions),
    }
    return calibrated, report


def _still_human(floor_applied: bool, tables: dict[str, dict[str, Any]]) -> list[str]:
    """What calibration cannot decide. Stated every time, because a
    pipeline that prints nothing here would read as "nothing left to
    do"."""
    items = [
        "CORRECTNESS: every expected value came from the extractor, not from the "
        "document. A green run means the candidate AGREES WITH THE INCUMBENT. Only "
        "reading values against pages turns this into ground truth -- sample it.",
        "format_critical is never inferred: a format being part of the contract is a "
        "policy call. Add it by hand where a downstream parser is strict.",
    ]
    if not floor_applied:
        items.append(
            "NO NOISE FLOOR was supplied, so the stability rule did not run: fields "
            "the extractor cannot reproduce are still critical and will produce reds "
            "that are not regressions. Run scripts/noise_floor.py."
        )
    disagreed = [t for t, d in tables.items() if not d["match_key_agreed"]]
    if disagreed:
        items.append(
            "match_key was NOT unanimous across the corpus for: "
            + ", ".join(sorted(disagreed))
            + ". One key was chosen; confirm it identifies a line."
        )
    return items


def _markdown(report: dict[str, Any], out: Path) -> str:
    lines = [
        "# Golden-set calibration",
        "",
        f"- corpus: **{report['corpus']['documents']}** documents, "
        f"**{report['corpus']['fields']}** fields",
        f"- noise floor applied: **{'yes' if report['noise_floor_applied'] else 'NO'}**",
        f"- calibrated set: `{out}`",
        "",
        "## ⚠️ Gate blind spots",
        "",
        "These are no longer `critical`. A regression in them will NOT fail a run.",
        "",
    ]
    lines += [
        f"- `{name}` — {report['fields'].get(name, {}).get('detail', 'see tables below')}"
        for name in report["blind_spots"]
    ] or ["- none: every field and table stayed critical."]
    lines += ["", "## Still a human's job", ""]
    lines += [f"- {item}" for item in report["still_human"]]
    lines += ["", "## Every field", "", "| field | critical | why |", "|---|---|---|"]
    for name, decision in report["fields"].items():
        lines.append(
            f"| `{name}` | {'yes' if decision['critical'] else 'NO'} | {decision['detail']} |"
        )
    if report["tables"]:
        lines += ["", "## Tables", ""]
        lines += ["| table | critical | match_key | why |", "|---|---|---|---|"]
        for name, decision in report["tables"].items():
            lines.append(
                f"| `{name}` | {'yes' if decision['critical'] else 'NO'} | "
                f"`{decision['match_key']}` | {decision['reason']} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="calibrate_golden",
        description="Apply the mechanical half of golden-set reconciliation, corpus-wide.",
    )
    ap.add_argument("--golden-file", required=True, type=Path, help="drafted golden set")
    ap.add_argument("--out", type=Path, help="calibrated set (default: in place)")
    ap.add_argument("--noise-floor", type=Path, help="a scripts/noise_floor.py report")
    ap.add_argument("--noise-tolerance", type=float, default=DEFAULT_NOISE_TOLERANCE)
    ap.add_argument("--sparse-threshold", type=float, default=DEFAULT_SPARSE_THRESHOLD)
    ap.add_argument("--min-observations", type=int, default=DEFAULT_MIN_OBSERVATIONS)
    args = ap.parse_args(argv)

    with args.golden_file.open(encoding="utf-8") as fh:
        golden_set = json.load(fh)
    if not isinstance(golden_set, dict) or not golden_set:
        print(f"calibrate_golden: {args.golden_file} is not a non-empty JSON object",
              file=sys.stderr)
        return 1

    floor = None
    if args.noise_floor:
        with args.noise_floor.open(encoding="utf-8") as fh:
            floor = _floor_index(json.load(fh))

    calibrated, report = calibrate(
        golden_set,
        floor,
        noise_tolerance=args.noise_tolerance,
        sparse_threshold=args.sparse_threshold,
        min_observations=args.min_observations,
    )

    out = args.out or args.golden_file
    write_private_json(out, calibrated)
    write_private_json(out.with_suffix(".calibration.json"), report)
    markdown = out.with_suffix(".calibration.md")
    markdown.write_text(_markdown(report, out))

    print(f"calibrate_golden: {out}  ({report['corpus']['documents']} documents)")
    print(f"  report: {markdown}")
    if report["blind_spots"]:
        print(
            f"  ⚠️  {len(report['blind_spots'])} GATE BLIND SPOT(S) -- a regression in "
            "these will not fail a run:",
            file=sys.stderr,
        )
        for name in report["blind_spots"]:
            print(f"        {name}", file=sys.stderr)
    for item in report["still_human"]:
        print(f"  ! {item}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
