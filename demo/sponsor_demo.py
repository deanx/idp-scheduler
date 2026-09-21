#!/usr/bin/env python
"""Sponsor demo — the IDP Regression Tester's working core, end to end.

WHAT THIS SHOWS (all real):
  1. A golden set stored on the evaluation platform, guarded by a committed
     JSON Schema that the SERVER enforces -- a bad curator edit is refused.
  2. The classifier judging an extraction against that golden, per field.
  3. A PASS run and a FAIL run recorded to the platform, with the per-field
     and gate scores visible in the Langfuse UI.
  4. The exit-code contract CI would key on.

WHAT THIS DOES *NOT* SHOW -- state this plainly when demoing:
  * The extraction outputs below are SYNTHETIC. The system has never run
    against a live MuleSoft Anypoint IDP org, because no published action
    id + version exists yet (story S-01.6 is blocked on exactly that).
    Everything downstream of extraction is real; extraction itself is stubbed.
  * There is no CLI or orchestrator yet (story S-01.4). This script does by
    hand what `run_eval` will do. The components are real; the wiring is not.
  * Dates are compared after canonicalisation but are NOT converted between
    locales: '14/03/2026' against an ISO golden is `wrong_format`, which by
    BR3 does not fail the gate. Worth knowing before someone asks.

COMPLIANCE: the data here is invented. Per the project's design rule,
document files never reach the platform, and no expected/actual VALUE is
written to it -- only `document_id` and verdicts. This script honours that.

USAGE:
    set -a && . ./.env && set +a && uv run python demo/sponsor_demo.py
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

from idp_regression.classifier import classify, overall_gate
from idp_regression.classifier.types import Golden, NormalizedOutput
from idp_regression.platform.langfuse_adapter import make_platform
from idp_regression.platform.schema_provisioning import provision_golden_schema
from idp_regression.platform.transport import UrllibHttpClient
from idp_regression.platform.types import DocumentRecord, RunMetadata
from idp_regression.platform.scoring import build_score_inputs

BASELINE_ACTION_VERSION = "v-baseline-1"
CANDIDATE_ACTION_VERSION = "v-candidate-2"


def rule(title: str) -> None:
    print(f"\n\033[1m{'─' * 72}\n{title}\n{'─' * 72}\033[0m")


def _client() -> UrllibHttpClient:
    host = os.environ["LANGFUSE_HOST"]
    return UrllibHttpClient(
        host=host,
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    )


# ---------------------------------------------------------------- the golden
# What a Golden Set Curator maintains: the known-good answer for one document.
GOLDEN: Golden = {
    "fields": {
        "invoice_number": {"value": "INV-2026-0042", "type": "id", "critical": True},
        "total": {"value": "1250.00", "type": "number", "critical": True},
        "invoice_date": {"value": "2026-03-14", "type": "date", "critical": True},
        "supplier_name": {"value": "Acme Widgets Ltd", "type": "text", "critical": False},
    }
}

# ------------------------------------------------------- simulated extractions
# STUB. This is what the IDP adapter's normalize() will return once S-01.6
# unblocks a live org. The shape is the real NormalizedOutput contract.
BASELINE_EXTRACTION: NormalizedOutput = {
    "status": "SUCCEEDED",
    "fields": {
        "invoice_number": {"value": "INV-2026-0042", "confidence": 0.99},
        "total": {"value": "1,250.00", "confidence": 0.97},  # canonicalises to a match
        "invoice_date": {"value": "14/03/2026", "confidence": 0.95},  # same date, other format
        "supplier_name": {"value": "Acme Widgets Ltd", "confidence": 0.98},
    },
}

# The same document after someone edits the extraction prompt. `total` is now
# wrong -- the exact regression this product exists to catch.
CANDIDATE_EXTRACTION: NormalizedOutput = {
    "status": "SUCCEEDED",
    "fields": {
        "invoice_number": {"value": "INV-2026-0042", "confidence": 0.99},
        "total": {"value": "125.00", "confidence": 0.62},  # ← a decimal point lost
        "invoice_date": {"value": "14/03/2026", "confidence": 0.95},
        "supplier_name": {"value": "Acme Widgets", "confidence": 0.71},  # non-critical drift
    },
}


def seed_golden_set(client: UrllibHttpClient, document_id: str) -> str:
    rule("1 · The golden set lives on the platform, guarded by a schema")
    dataset_name = f"sponsor-demo-{uuid.uuid4().hex[:8]}"
    provision_golden_schema(client, dataset_name=dataset_name)
    print(f"  Created dataset      : {dataset_name}")
    print("  Committed JSON Schema: provisioned as the dataset's expectedOutputSchema")

    status, body = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": {"document_id": document_id},
            "expectedOutput": GOLDEN,
        },
    )
    assert status == 200, f"seeding failed: {status} {body}"
    print(f"  Seeded golden item   : document_id={document_id!r}  ({len(GOLDEN['fields'])} fields)")
    return dataset_name


def show_schema_guard(client: UrllibHttpClient, dataset_name: str) -> None:
    rule("2 · The server REFUSES a malformed curator edit")
    print('  A curator fat-fingers a number field:  total = "twelve fifty"')
    status, body = client.request(
        "POST",
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": {"document_id": "doc-bad-edit"},
            "expectedOutput": {
                "fields": {"total": {"value": "twelve fifty", "type": "number", "critical": True}}
            },
        },
    )
    if status >= 400:
        print(f"  → Platform rejected it with HTTP {status}. The golden set cannot rot.")
    else:
        print(f"  → ⚠️  Accepted (HTTP {status}) — schema guard NOT enforced here.")


def judge(label: str, extraction: NormalizedOutput) -> tuple[dict[str, Any], str]:
    verdicts = classify(GOLDEN, extraction)
    gate = overall_gate(verdicts)
    colour = "\033[32m" if gate == "PASS" else "\033[31m"
    print(f"\n  {label}  →  {colour}\033[1mgate = {gate}\033[0m")
    for name, v in sorted(verdicts.items()):
        mark = "✓" if v["verdict"] == "match" else "✗"
        crit = " (critical)" if v.get("critical") else ""
        print(f"    {mark} {name:<16} {v['verdict']}{crit}")
    return verdicts, gate


def record(
    platform: Any, dataset_name: str, document_id: str,
    verdicts: dict[str, Any], gate: str, action_version: str,
) -> str:
    run_id = f"demo-{uuid.uuid4().hex[:8]}"
    dataset = platform.get_dataset(dataset_name)
    item = next(i for i in dataset["items"] if i["document_id"] == document_id)

    scores = build_score_inputs(
        golden=GOLDEN, verdicts=verdicts, gate=gate,  # type: ignore[arg-type]
        run_id=run_id, document_id=document_id,
    )
    record_: DocumentRecord = {
        "item_id": item["item_id"], "document_id": document_id, "scores": scores,
    }
    metadata: RunMetadata = {
        "action_id": "demo-action",
        "action_version": action_version,
        "golden_version": "demo-golden-v1",
    }
    platform.record_run(
        dataset_name=dataset_name, run_name=run_id, run_id=run_id,
        records=[record_], metadata=metadata,
    )
    print(f"    recorded run {run_id!r}: {len(scores)} scores written")
    return run_id


def main() -> int:
    for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        if not os.environ.get(var):
            print(f"✗ {var} is not set. Run:  set -a && . ./.env && set +a")
            return 2

    document_id = "invoice-demo-001"
    client = _client()
    dataset_name = seed_golden_set(client, document_id)
    show_schema_guard(client, dataset_name)

    rule("3 · The classifier judges each field against the golden")
    print("  Two things to point out on screen:")
    print("   • total   '1,250.00' vs '1250.00'  → MATCH. Number fields are")
    print("     canonicalised, so thousands separators are not regressions.")
    print("   • invoice_date '14/03/2026' vs '2026-03-14' → WRONG_FORMAT, not")
    print("     wrong_value. The date is right; the format is not. Per business")
    print("     rule BR3 that does NOT fail the gate, even on a critical field —")
    print("     only a MISSING or WRONG_VALUE critical field does. That is the")
    print("     difference between a useful gate and one teams learn to ignore.")
    base_verdicts, base_gate = judge("BASELINE  (current prompt)", BASELINE_EXTRACTION)
    cand_verdicts, cand_gate = judge("CANDIDATE (prompt was edited)", CANDIDATE_EXTRACTION)

    rule("4 · Both runs recorded to the platform")
    print("  Only document_id + verdicts leave the app — never the expected or")
    print("  extracted VALUES, and never the document itself.")
    platform = make_platform()
    record(platform, dataset_name, document_id, base_verdicts, base_gate, BASELINE_ACTION_VERSION)
    record(platform, dataset_name, document_id, cand_verdicts, cand_gate, CANDIDATE_ACTION_VERSION)

    rule("5 · What CI does with this")
    exit_code = 0 if cand_gate == "PASS" else 1
    print(f"  Baseline gate : {base_gate}")
    print(f"  Candidate gate: {cand_gate}   → CI exit code {exit_code}")
    print("  A prompt change that breaks a critical field fails the build.")
    print(f"\n  Inspect in Langfuse: {os.environ['LANGFUSE_HOST']}  → dataset {dataset_name}")
    print("\n  \033[1mNot yet built:\033[0m the IDP extraction above is synthetic (S-01.6 is")
    print("  blocked on a published action id), and there is no CLI yet (S-01.4).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
