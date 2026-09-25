#!/usr/bin/env python
"""Turn a captured IDP response into a DRAFT golden entry, for human review.

⚠️ **READ THIS FIRST. A drafted golden is not a known-good answer.**

This derives the expected values from the system under test. That means it
pins *what the extractor currently does*, not *what is correct*. If the
extractor is already getting a field wrong, this will faithfully record the
wrong answer as "expected", and the gate will then defend that error
forever -- passing every future version that repeats it and failing the
version that finally fixes it.

So this script does exactly one job: it removes the transcription work.
**A human must read every value against the actual document before the
draft becomes a golden.** The reconciliation step is not optional and it is
not a formality; it is the step that makes the golden set worth anything.

Where this fits
---------------
    scripts/capture_raw.py    one PDF  -> one raw IDP response   (costs quota)
    scripts/draft_golden.py   capture  -> a draft golden entry   (this file)
    <human reconciliation>    draft    -> a golden               (the real work)
    scripts/provision_golden_dataset.py   golden -> Langfuse

Usage
-----
    # from a capture you already have
    .venv/bin/python scripts/draft_golden.py \\
        --capture testpack/captures/inv-001-clean.raw.json \\
        --document-id inv-001-clean.pdf

    # write/merge straight into a golden-set file
    .venv/bin/python scripts/draft_golden.py \\
        --capture demopack/captures/demo-001-clean.raw.json \\
        --document-id demo-001-clean.pdf \\
        --key demo-001-clean --out demopack/golden_set.json

What it guesses, and how
------------------------
* ``type`` -- inferred from the value: an ISO ``YYYY-MM-DD`` string is
  ``date``, a bare number is ``number``, a field whose NAME ends in
  ``_number``/``_id``/``_no`` is ``id``, everything else is ``text``.
* ``critical`` -- defaults to **true** for every field. This is a
  deliberate fail-CLOSED guess: an over-strict golden produces a red build
  someone investigates, while an over-loose one produces a silent green,
  and silent green is the failure this whole project exists to prevent.
  Expect to turn some off -- a field that is legitimately absent on some
  documents (a PO number, a tax line) must NOT be critical.
* ``format_critical`` -- **never** guessed, always omitted. It says "this
  field's FORMAT is part of the contract", which is a policy call no
  capture can imply. Add it by hand where it is true.
* ``match_key`` for a table -- prefers a column named ``sku``/``id``/
  ``code``, else the first column whose values are all non-empty and
  unique. Always printed in the review checklist, because getting it wrong
  silently mis-pairs every row.

The draft is validated against the committed golden JSON Schema before it
is written, so a draft that could never be provisioned fails here instead
of at the platform.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import jsonschema

from idp_regression.adapter.normalize import normalize
from idp_regression.classifier.gate import validate_golden_structure
from idp_regression.platform.schema import load_golden_schema

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
_ID_SUFFIXES = ("_number", "_id", "_no", "_ref", "_code")
_MATCH_KEY_PREFERENCE = ("sku", "id", "code", "item_code", "line_id")


def _infer_type(name: str, value: str) -> str:
    if _ISO_DATE.match(value):
        return "date"
    if _NUMERIC.match(value):
        return "number"
    if name.lower().endswith(_ID_SUFFIXES):
        return "id"
    return "text"


def _pick_match_key(rows: list[dict[str, str]]) -> tuple[str | None, str]:
    """Return (column, why) -- `why` is shown in the review checklist."""
    if not rows:
        return None, "no rows"
    columns = list(rows[0])
    for preferred in _MATCH_KEY_PREFERENCE:
        if preferred in columns:
            return preferred, f"column named {preferred!r}"
    for col in columns:
        values = [r.get(col, "") for r in rows]
        if all(v for v in values) and len(set(values)) == len(values):
            return col, "first column with all values present and unique"
    return columns[0], "FALLBACK: first column -- almost certainly wrong, fix it"


def _draft(capture: dict[str, Any], document_id: str) -> tuple[dict[str, Any], list[str]]:
    """Draft from a RAW capture (the file `capture_raw.py` writes)."""
    return _draft_from_normalized(normalize(capture, {"SUCCEEDED"}), document_id)


def _draft_from_normalized(
    normalized: dict[str, Any], document_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Draft from an ALREADY-normalized output -- the seam
    `scripts/noise_floor.py` needs, since it consumes `adapter.extract()`
    (normalized) rather than a raw capture. Split out rather than
    duplicated so both tools apply exactly the same type/critical/
    match_key rules; two copies would drift, and a batch draft that
    disagreed with a hand draft of the same document is precisely the
    silent difference this project exists to catch."""
    notes: list[str] = []

    fields: dict[str, Any] = {}
    for name, cell in sorted(normalized.get("fields", {}).items()):
        value = cell.get("value")
        if value is None or not str(value).strip():
            notes.append(
                f"  field {name!r}: came back EMPTY. Drafted as text/critical:false -- "
                "decide whether this document genuinely lacks it."
            )
            fields[name] = {"value": "", "type": "text", "critical": False}
            continue
        value = str(value)
        ftype = _infer_type(name, value)
        fields[name] = {"value": value, "type": ftype, "critical": True}
        notes.append(f"  field {name!r}: type={ftype} critical=true  value={value!r}")

    entry: dict[str, Any] = {"document_id": document_id, "fields": fields}

    tables: dict[str, Any] = {}
    for tname, arows in sorted(normalized.get("tables", {}).items()):
        rows = [
            {col: str(cell.get("value") or "") for col, cell in row.items()} for row in arows
        ]
        match_key, why = _pick_match_key(rows)
        if match_key is None:
            notes.append(f"  table {tname!r}: no rows captured -- omitted from the draft.")
            continue
        tables[tname] = {"match_key": match_key, "critical": True, "rows": rows}
        notes.append(
            f"  table {tname!r}: {len(rows)} rows, match_key={match_key!r} ({why})"
        )
        keys = [r.get(match_key, "") for r in rows]
        if len(set(keys)) != len(keys):
            notes.append(
                f"    NOTE: {match_key!r} repeats across rows. That is legitimate "
                "(a split shipment) and is handled, but confirm it is intended."
            )
    if tables:
        entry["tables"] = tables

    return entry, notes


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Draft a golden entry from a captured IDP response (REVIEW REQUIRED)."
    )
    ap.add_argument("--capture", required=True, help="path to a raw capture JSON")
    ap.add_argument("--document-id", required=True, help="the document_id / PDF filename")
    ap.add_argument("--key", help="key in the golden-set file (default: document-id stem)")
    ap.add_argument("--out", help="golden-set JSON to merge into (default: print to stdout)")
    args = ap.parse_args()

    with open(args.capture, encoding="utf-8") as fh:
        capture = json.load(fh)

    entry, notes = _draft(capture, args.document_id)

    # Fail here, not at the platform, if the draft could never be provisioned.
    jsonschema.Draft7Validator(load_golden_schema()).validate(entry)
    validate_golden_structure(entry)

    key = args.key or args.document_id.rsplit(".", 1)[0]

    print("=" * 72, file=sys.stderr)
    print(f"DRAFT golden for {args.document_id}  (key: {key})", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    for line in notes:
        print(line, file=sys.stderr)
    print("", file=sys.stderr)
    print("REVIEW CHECKLIST -- do all of these before provisioning:", file=sys.stderr)
    print("  1. Open the PDF. Check EVERY value above against the page.", file=sys.stderr)
    print("     These values came from the extractor, so they encode its", file=sys.stderr)
    print("     current mistakes as 'expected'.", file=sys.stderr)
    print("  2. Turn OFF critical for fields that are legitimately absent on", file=sys.stderr)
    print("     some documents -- otherwise those documents fail forever.", file=sys.stderr)
    print("  3. Add format_critical: true where the FORMAT is the contract", file=sys.stderr)
    print("     (e.g. a date the prompt is required to emit as ISO-8601).", file=sys.stderr)
    print("  4. Confirm the table match_key actually identifies a line.", file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    if not args.out:
        print(json.dumps({key: entry}, indent=2))
        return 0

    existing: dict[str, Any] = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            existing = json.load(fh)
        if key in existing:
            print(
                f"\ndraft_golden: {key!r} already exists in {args.out} -- "
                "OVERWRITING the drafted entry. Any hand-reconciliation you "
                "already did to it is lost.",
                file=sys.stderr,
            )
    existing[key] = entry
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(existing, indent=2) + "\n")
    print(
        f"\ndraft_golden: wrote {key!r} into {args.out} ({len(existing)} entries)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
