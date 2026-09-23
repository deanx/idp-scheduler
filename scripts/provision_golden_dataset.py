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
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from idp_regression.orchestration.dotenv_support import load_dotenv  # noqa: E402
from idp_regression.platform.errors import PlatformError, TransportError  # noqa: E402
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


def _build_item_payload(dataset_name: str, seed: dict[str, Any]) -> dict[str, Any]:
    """`input.document_id` is what `facade.py::_resolve_document_path`
    resolves against `IDP_DOCUMENT_DIR` -- the seed's own `document_id`
    key IS that filename already (see `id_seeds/README.md`). The document
    file itself never appears in this payload."""
    document_id = seed["document_id"]
    expected_output = {"fields": seed["fields"]}
    if seed.get("tables"):
        expected_output["tables"] = seed["tables"]
    if seed.get("prompts"):
        expected_output["prompts"] = seed["prompts"]
    return {
        "datasetName": dataset_name,
        "input": {"document_id": document_id},
        "expectedOutput": expected_output,
    }


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

    seed = _load_seed(args.golden_file, args.seed)
    seed = _apply_perturbation(seed, field=args.perturb_field, value=args.perturb_value)
    item_payload = _build_item_payload(args.dataset, seed)

    if args.dry_run:
        print(f"provision_golden_dataset: DRY RUN -- dataset={args.dataset!r} seed={args.seed!r}")
        print("  would POST /api/public/v2/datasets  {name, expectedOutputSchema=<committed>}")
        if args.show_values:
            print(f"  would POST /api/public/dataset-items  {json.dumps(item_payload)}")
        else:
            print(f"  would POST /api/public/dataset-items  {_summarize_payload(item_payload)}")
        return 0

    host = _require_env("LANGFUSE_HOST")
    public_key = _require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = _require_env("LANGFUSE_SECRET_KEY")
    client = UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)

    try:
        provision_golden_schema(client, dataset_name=args.dataset)
    except PlatformError as exc:
        print(f"provision_golden_dataset: schema provisioning failed: {exc}", file=sys.stderr)
        return 1

    try:
        status, body = client.request("POST", "/api/public/dataset-items", item_payload)
    except TransportError as exc:
        print(f"provision_golden_dataset: item create transport failure: {exc}", file=sys.stderr)
        return 1
    if status >= 400:
        # Never print `body` here: Langfuse's dataset-item 400 echoes back
        # the offending part of the submitted `expectedOutput` (golden
        # values) in its validation message -- the exact rule
        # `platform/errors.py` states (NFR N5, INV-02). Status code only.
        print(
            f"provision_golden_dataset: item create failed: HTTP {status} "
            "(response body withheld -- may echo golden values)",
            file=sys.stderr,
        )
        return 1

    item_id = body.get("id") if isinstance(body, dict) else None
    print(
        f"provision_golden_dataset: OK dataset={args.dataset!r} item_id={item_id!r} "
        f"document_id={item_payload['input']['document_id']!r}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
