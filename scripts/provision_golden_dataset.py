#!/usr/bin/env python
"""Provision a scratch Langfuse dataset with a single golden item, read
straight out of ``id_seeds/golden_set.json`` (S-01.4 seam verification,
2026-09-23 — closing /signoff blocker B-5).

This is intentionally a real script, not a throwaway one-liner: it has a
``--dry-run`` mode, reuses the existing production seams
(``provision_golden_schema``, ``UrllibHttpClient``, ``load_dotenv``)
rather than reimplementing them, and can also *perturb* one field's
expected value so the same tool can produce both the "gate should pass"
and "gate should fail" fixtures needed to prove the regression gate
actually discriminates.

Design constraints this script honours (CLAUDE.md ## Domain):
  - the document FILE never goes anywhere near the platform -- only
    ``document_id`` (a string naming a file under ``IDP_DOCUMENT_DIR``)
    and the golden `expectedOutput` are sent.
  - no golden value is printed with its real content beyond what the
    operator explicitly asked to perturb (and even then, only the field
    name + new value the operator supplied on the command line, never
    logged to a persistent file); ``--dry-run`` prints field names + a
    payload digest by default, full values only under ``--show-values``;
    and an HTTP error from the platform never echoes its response body,
    since Langfuse's own 400 for a bad dataset item can echo back the
    submitted golden value.

Usage::

    .venv/bin/python scripts/provision_golden_dataset.py \\
        --dataset idp-regression-seed001 --seed SEED-001

    # perturb one critical field to prove the gate can fail:
    .venv/bin/python scripts/provision_golden_dataset.py \\
        --dataset idp-regression-seed001-perturbed --seed SEED-001 \\
        --perturb-field total --perturb-value 1.00

    # see the exact request bodies without touching the network:
    .venv/bin/python scripts/provision_golden_dataset.py \\
        --dataset idp-regression-seed001 --seed SEED-001 --dry-run

Batch mode (2026-09-24)
-----------------------
``--all`` provisions EVERY entry in ``--golden-file`` instead of one
``--seed``. This is what turns a drafted golden set
(``scripts/bootstrap_golden_set.py``) into a runnable dataset -- until it
existed, a thousand-entry golden set could only reach the platform
through a shell loop::

    .venv/bin/python scripts/provision_golden_dataset.py \\
        --dataset customer-baseline-v1 --all \\
        --golden-file draft_golden_set.json

Three things batch mode does that a shell loop would not:

* **Every entry is validated locally first.** A reconciliation typo is
  caught here, by name, against the committed schema -- rather than as a
  platform ``HTTP 400`` whose body this script may never print (H1),
  which would leave an operator with a failure and no reason for it.
* **It refuses to build a dataset that cannot be run.** ``run_eval``
  ABORTS a run whose dataset exceeds ``--max-documents-per-run``
  (``facade.DEFAULT_MAX_DOCUMENTS_PER_RUN``) rather than truncating it,
  so silently provisioning 1,400 items produces a dataset that is
  unrunnable at the default ceiling. Over the ceiling, this stops and
  says so; ``--max-items`` raises it deliberately.
* **One bad entry does not end the batch**, and the run is re-runnable:
  item ids are the deterministic ``uuid5`` below, so re-running after
  fixing the bad entries UPSERTS rather than duplicating (DEBT-82).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

import jsonschema  # noqa: E402

from idp_regression.classifier.gate import validate_golden_structure  # noqa: E402
from idp_regression.orchestration.dotenv_support import load_dotenv  # noqa: E402
from idp_regression.orchestration.facade import (  # noqa: E402
    DEFAULT_MAX_DOCUMENTS_PER_RUN,
)
from idp_regression.platform.errors import PlatformError, TransportError  # noqa: E402
from idp_regression.platform.schema import load_golden_schema  # noqa: E402
from idp_regression.platform.schema_provisioning import provision_golden_schema  # noqa: E402
from idp_regression.platform.transport import UrllibHttpClient  # noqa: E402

DEFAULT_GOLDEN_FILE = _REPO_ROOT / "id_seeds" / "golden_set.json"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"provision_golden_dataset: missing required env var {name}", file=sys.stderr)
        raise SystemExit(1)
    return value


def _load_seed(golden_file: Path, seed_key: str) -> dict[str, Any]:
    with golden_file.open(encoding="utf-8") as f:
        golden_set = json.load(f)
    if seed_key not in golden_set:
        available = ", ".join(sorted(golden_set))
        print(
            f"provision_golden_dataset: seed {seed_key!r} not found in {golden_file} "
            f"(available: {available})",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return copy.deepcopy(golden_set[seed_key])


def _load_all(golden_file: Path) -> list[tuple[str, dict[str, Any]]]:
    """Every entry in the golden file, key-sorted so two provisioning
    runs over an unchanged file process the same items in the same
    order -- a batch whose order moves is a batch whose partial failures
    cannot be compared against the previous attempt."""
    with golden_file.open(encoding="utf-8") as f:
        golden_set = json.load(f)
    if not isinstance(golden_set, dict) or not golden_set:
        print(
            f"provision_golden_dataset: {golden_file} is not a non-empty JSON object",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return [(key, copy.deepcopy(golden_set[key])) for key in sorted(golden_set)]


def _validation_error(entry: dict[str, Any]) -> str | None:
    """`None` if `entry` could be provisioned, else a SHORT reason.

    Deliberately reduced to a reason the caller can print: a
    `jsonschema` message quotes the offending instance, and for a golden
    that instance IS a golden value (INV-02, and the same rule H1 closed
    on the HTTP error path). The validator's `json_path` names WHERE the
    problem is without saying what the value was, which is what an
    operator needs to go and fix the entry by hand.
    """
    try:
        jsonschema.Draft7Validator(load_golden_schema()).validate(entry)
        validate_golden_structure(entry)
    except jsonschema.ValidationError as exc:
        return f"schema: {exc.json_path} is invalid ({exc.validator})"
    except Exception as exc:  # noqa: BLE001 - INV-02: type name only, never str(exc)
        return f"structure: {type(exc).__name__}"
    return None


def _apply_perturbation(
    seed: dict[str, Any], *, field: str | None, value: str | None
) -> dict[str, Any]:
    """Overwrite ``fields[field]['value']`` in place -- used to build a
    deliberately-wrong golden so the gate can be proven to FAIL, not just
    observed passing. Raises ``SystemExit`` if the field doesn't exist,
    so a typo never silently provisions an unperturbed (falsely green)
    dataset."""
    if field is None:
        return seed
    if value is None:
        print(
            "provision_golden_dataset: --perturb-field requires --perturb-value", file=sys.stderr
        )
        raise SystemExit(1)
    fields = seed.get("fields", {})
    if field not in fields:
        print(
            f"provision_golden_dataset: --perturb-field {field!r} not present in this seed's "
            f"fields ({', '.join(sorted(fields))})",
            file=sys.stderr,
        )
        raise SystemExit(1)
    fields[field]["value"] = value
    return seed


#: Pinned namespace for deterministic dataset-item ids. Distinct from
#: `platform/scoring.py`'s NAMESPACE on purpose: these are different id
#: spaces and must never collide.
_ITEM_NAMESPACE = uuid.UUID("9d3c7e14-5a62-4b8f-9e07-3c1d6f2a8b45")


def _build_item_payload(
    dataset_name: str,
    seed: dict[str, Any],
    source: dict[str, str] | None = None,
) -> dict[str, Any]:
    """`input.document_id` is what `facade.py::_resolve_document_path`
    resolves against `IDP_DOCUMENT_DIR` -- the seed's own `document_id`
    key IS that filename already (see `id_seeds/README.md`). The document
    file itself never appears in this payload."""
    document_id = seed["document_id"]
    expected_output: dict[str, Any] = {"fields": seed["fields"]}
    if seed.get("tables"):
        expected_output["tables"] = seed["tables"]
    if seed.get("prompts"):
        expected_output["prompts"] = seed["prompts"]
    payload: dict[str, Any] = {
        # A DETERMINISTIC item id makes re-provisioning an UPSERT instead of
        # an append. Without it Langfuse mints a fresh id per POST, so
        # re-running this script duplicates every item: observed live on
        # 2026-09-24, when re-provisioning after the `format_critical`
        # schema change left the dataset with 10 items -- 5 carrying the new
        # golden and 5 stale ones carrying the old. Every later run then
        # processed each document TWICE (double IDP quota), and the stale
        # copy of `inv-002-format-variance.pdf` PASSED the very regression
        # its refreshed twin caught, because the stale golden predates the
        # `format_critical` opt-in. Same uuid5 idempotency pattern the score
        # writes already use (`platform/scoring.py`, N26).
        "id": str(uuid.uuid5(_ITEM_NAMESPACE, f"{dataset_name}|{document_id}")),
        "datasetName": dataset_name,
        "input": {"document_id": document_id},
        "expectedOutput": expected_output,
    }
    if source:
        # PROVENANCE, not content (2026-09-25): which action and version
        # produced these expected values. Without it the item knows WHAT
        # is expected and for WHICH document, and nothing about where it
        # came from -- so a dataset opened on another machine cannot say
        # whether it was pinned at 1.0.0 or 1.4.2, and a dataset holding
        # items pinned at DIFFERENT versions looks identical to one that
        # is not.
        #
        # INV-01 / DEBT-18 option B are unaffected: those forbid actual,
        # expected and confidence VALUES on the platform. An action id, a
        # version string and a timestamp are run identity -- the same
        # class INV-04 already writes to run metadata -- and carry no
        # extracted content.
        payload["metadata"] = dict(source)
    return payload


def _summarize_payload(item_payload: dict[str, Any]) -> str:
    """A `--dry-run`-safe stand-in for the real item payload: field/table
    *names* and a digest of the whole payload, never a value (L7 -- the
    prior dry-run printed the full payload including real golden field
    values, the same class of leak H1 closed on the error path)."""
    expected = item_payload.get("expectedOutput", {})
    field_names = sorted(expected.get("fields", {}))
    table_names = sorted(expected.get("tables", {})) if expected.get("tables") else []
    digest = hashlib.sha256(json.dumps(item_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return (
        f"document_id={item_payload['input']['document_id']!r} "
        f"field_names={field_names} table_names={table_names} "
        f"payload_sha256={digest[:16]}..."
    )


def _post_items(
    client: Any,
    items: list[tuple[str, dict[str, Any]]],
    *,
    stop_on_error: bool,
) -> tuple[list[str], list[tuple[str, str]]]:
    """POST each `(key, item_payload)`. Returns `(created_keys, failures)`.

    One entry's failure does not end the batch by default: a thousand-item
    provisioning run that aborts on item 900 has spent 899 successful
    writes and produced a dataset that is neither complete nor clearly
    incomplete. Because item ids are the deterministic `uuid5` below, the
    fix-and-re-run is an UPSERT of everything, not a duplicate of the 899
    (DEBT-82) -- which is what makes continuing the safe default.
    """
    created: list[str] = []
    failures: list[tuple[str, str]] = []
    total = len(items)
    for index, (key, item_payload) in enumerate(items, start=1):
        document_id = item_payload["input"]["document_id"]
        try:
            status, body = client.request("POST", "/api/public/dataset-items", item_payload)
        except TransportError as exc:
            # INV-02: the typed error's own message is safe (it never
            # carries a value), but keep the printed form to its class.
            failures.append((key, f"transport: {type(exc).__name__}"))
            status = None
        if status is not None and status >= 400:
            # Never print `body`: Langfuse's dataset-item 400 echoes back
            # the offending part of the submitted `expectedOutput` (golden
            # values) in its validation message -- the exact rule
            # `platform/errors.py` states (NFR N5, INV-02). Status only.
            print(
                f"provision_golden_dataset: item create failed: HTTP {status} "
                "(response body withheld -- may echo golden values)",
                file=sys.stderr,
            )
            failures.append((key, f"HTTP {status}"))
        elif status is not None:
            created.append(key)
            item_id = body.get("id") if isinstance(body, dict) else None
            if total > 1:
                print(
                    f"  [{index}/{total}] {key} -> item_id={item_id!r} "
                    f"document_id={document_id!r}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"provision_golden_dataset: OK item_id={item_id!r} "
                    f"document_id={document_id!r}"
                )
        if failures and stop_on_error:
            print(
                f"provision_golden_dataset: --stop-on-error, aborting at {key!r} "
                f"({len(created)} item(s) already written)",
                file=sys.stderr,
            )
            break
    return created, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Langfuse dataset name to provision")
    parser.add_argument("--seed", default="SEED-001", help="key in --golden-file to provision")
    parser.add_argument(
        "--golden-file",
        type=Path,
        default=DEFAULT_GOLDEN_FILE,
        help=f"path to golden_set.json (default: {DEFAULT_GOLDEN_FILE})",
    )
    parser.add_argument(
        "--perturb-field",
        default=None,
        help="name of a top-level `fields` entry to overwrite (for a deliberate-FAIL fixture)",
    )
    parser.add_argument(
        "--perturb-value",
        default=None,
        help="new string value for --perturb-field",
    )
    parser.add_argument(
        "--source-action",
        default=None,
        help="record which action produced these goldens, as item metadata",
    )
    parser.add_argument(
        "--source-version",
        default=None,
        help="record which action VERSION produced these goldens, as item metadata",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="provision EVERY entry in --golden-file (batch mode), not just --seed",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_DOCUMENTS_PER_RUN,
        help=(
            "refuse to provision more entries than this "
            f"(default {DEFAULT_MAX_DOCUMENTS_PER_RUN}, matching run_eval's "
            "--max-documents-per-run ceiling, ABOVE WHICH A RUN ABORTS)"
        ),
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="abort the batch at the first invalid or rejected entry (default: continue)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "print the schema-provision + item-create request shape (field names + a digest, "
            "not values -- pass --show-values too for full field values), make no network call"
        ),
    )
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="with --dry-run, print full golden field values instead of just names + a digest",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    if args.all and args.perturb_field:
        # Perturbation builds a deliberately-wrong SINGLE fixture; applied
        # across a whole set it would quietly corrupt every entry that
        # happens to carry the field, and pass over the ones that do not.
        print(
            "provision_golden_dataset: --perturb-field is a single-entry fixture tool "
            "and cannot be combined with --all",
            file=sys.stderr,
        )
        return 2

    if args.all:
        entries = _load_all(args.golden_file)
    else:
        seed = _apply_perturbation(
            _load_seed(args.golden_file, args.seed),
            field=args.perturb_field,
            value=args.perturb_value,
        )
        entries = [(args.seed, seed)]

    if len(entries) > args.max_items:
        # `run_eval` ABORTS above its ceiling rather than truncating, so a
        # dataset provisioned past it is one that can never be run -- a
        # failure that would otherwise only surface after the whole batch
        # had been written.
        print(
            f"provision_golden_dataset: {len(entries)} entries exceeds --max-items "
            f"({args.max_items}). run_eval refuses a dataset larger than its "
            f"--max-documents-per-run ceiling (default "
            f"{DEFAULT_MAX_DOCUMENTS_PER_RUN}), so this dataset could not be run. "
            "Split the golden set across datasets, or raise both ceilings together.",
            file=sys.stderr,
        )
        return 2

    invalid: list[tuple[str, str]] = []
    valid: list[tuple[str, dict[str, Any]]] = []
    for key, seed in entries:
        reason = _validation_error(seed)
        if reason is None:
            valid.append((key, seed))
        else:
            # Named, with a reason, and never a value: this is the message
            # that saves an operator from a platform 400 they cannot read.
            invalid.append((key, reason))
            print(f"provision_golden_dataset: INVALID {key}: {reason}", file=sys.stderr)

    if invalid and (args.stop_on_error or not valid):
        print(
            f"provision_golden_dataset: {len(invalid)} invalid entr(ies); nothing provisioned",
            file=sys.stderr,
        )
        return 1

    source = None
    if args.source_action or args.source_version:
        source = {
            "trusted_action_id": args.source_action or "",
            "trusted_action_version": args.source_version or "",
            "pinned_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    items = [(key, _build_item_payload(args.dataset, seed, source)) for key, seed in valid]

    if args.dry_run:
        print(
            f"provision_golden_dataset: DRY RUN -- dataset={args.dataset!r} "
            f"entries={len(items)}"
        )
        print("  would POST /api/public/v2/datasets  {name, expectedOutputSchema=<committed>}")
        for _key, item_payload in items:
            if args.show_values:
                print(f"  would POST /api/public/dataset-items  {json.dumps(item_payload)}")
            else:
                print(
                    f"  would POST /api/public/dataset-items  {_summarize_payload(item_payload)}"
                )
        return 1 if invalid else 0

    host = _require_env("LANGFUSE_HOST")
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = _require_env("LANGFUSE_SECRET_KEY")
    client = UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)

    try:
        # Once per batch, not once per item: the schema is a property of
        # the DATASET.
        provision_golden_schema(client, dataset_name=args.dataset)
    except PlatformError as exc:
        print(f"provision_golden_dataset: schema provisioning failed: {exc}", file=sys.stderr)
        return 1

    created, failures = _post_items(client, items, stop_on_error=args.stop_on_error)

    if len(items) > 1 or failures or invalid:
        print(
            f"provision_golden_dataset: dataset={args.dataset!r} "
            f"created/updated={len(created)} failed={len(failures)} invalid={len(invalid)}",
            file=sys.stderr,
        )
        for key, reason in failures + invalid:
            print(f"  {key}: {reason}", file=sys.stderr)
        if failures or invalid:
            print(
                "  item ids are deterministic -- fix these entries and re-run the same "
                "command; the successful items are upserted, not duplicated.",
                file=sys.stderr,
            )
    return 1 if (failures or invalid) else 0


if __name__ == "__main__":
    raise SystemExit(main())
