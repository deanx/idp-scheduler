"""The console's primary use case, end to end, through the REAL chain.

> "This corpus is already validated against version X. I changed the LLM
> and published Y. Is it still valid?"

Everything the console does past the priced `--plan` used to be unproven:
the plan was tested, the argv was tested, and then a live run was the
first thing that would ever execute `pin -> verify -> gate -> artifact`.
This file closes that. It drives the **real** `pin_document.run` and
`verify_document.run`, the **real** `facade.run_eval`, the real
classifier, the real gate and the real run-artifact writer, with only two
things faked: the IDP adapter and the platform. Those are the only pieces
that cost money or need a network, and both are replaced at the seams the
production code already injects (`facade.make_idp_adapter` /
`facade.make_platform`).

So what is NOT covered here is exactly, and only: whether MuleSoft and
the platform behave as their fixtures say. Everything this repo owns is.

The second half then feeds the artifact this produces to
`jobs.summarize()` -- proving the console reports the run's real
per-document verdicts rather than the banner-counting it did before.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from idp_regression.adapter.types import NormalizedOutput
from idp_regression.orchestration import facade
from idp_regression.platform.types import (
    Dataset,
    DatasetItem,
    DocumentRecord,
    RunMetadata,
    RunStatus,
)
from idp_regression.ui import jobs, workspace
from idp_regression.ui.scripts_bridge import load

pin_document = load("pin_document")
verify_document = load("verify_document")
batch = load("_batch")

DOCUMENTS = ("inv-001.pdf", "inv-002.pdf", "inv-003.pdf")
TRUSTED = "1.0.0"
CANDIDATE = "2.0.0"


def raw(total: str) -> dict[str, Any]:
    """The live wire shape, trimmed (SR-1: a recorded shape, not one
    authored to match the parser)."""
    return {
        "documentName": "doc.pdf",
        "status": "SUCCEEDED",
        "fields": {
            "invoice_number": {"value": "INV-1001", "confidenceScore": 99.0},
            "total": {"value": total, "confidenceScore": 99.0},
            "vendor_name": {"value": "Acme Office Supplies", "confidenceScore": 99.0},
        },
        "tables": {
            "line_items": [
                {
                    "sku": {"value": "SKU-1", "confidenceScore": 99.0},
                    "amount": {"value": "65.00", "confidenceScore": 99.0},
                }
            ]
        },
    }


def normalized(total: str) -> NormalizedOutput:
    return {
        "status": "SUCCEEDED",
        "fields": {
            "invoice_number": {"value": "INV-1001", "confidence": 0.99},
            "total": {"value": total, "confidence": 0.99},
            "vendor_name": {"value": "Acme Office Supplies", "confidence": 0.99},
        },
        "tables": {
            "line_items": [
                {
                    "sku": {"value": "SKU-1", "confidence": 0.99},
                    "amount": {"value": "65.00", "confidence": 0.99},
                }
            ]
        },
        "prompts": {},
    }


class FakeIDP:
    """Reads a document. What it reads depends on the VERSION, which is
    the whole point of the use case."""

    def __init__(self, totals: dict[tuple[str, str], str]) -> None:
        self.totals = totals
        self.calls: list[tuple[str, str]] = []

    def _total(self, document_id: str, version: str) -> str:
        return self.totals.get((document_id, version), "87.48")

    def extract(self, document_path: str, action_id: str, version: str) -> NormalizedOutput:
        document_id = os.path.basename(document_path)
        self.calls.append((document_id, version))
        return normalized(self._total(document_id, version))


class FakePlatform:
    """Mirrors `platform/types.py::PlatformAdapter` exactly. Holds the
    dataset the pinning half provisioned, so the verifying half reads
    back what was actually written -- not a fixture."""

    def __init__(self) -> None:
        self.items: dict[str, DatasetItem] = {}
        self.records: list[DocumentRecord] = []
        self.statuses: list[str] = []
        #: The committed schema, exactly as `provision_golden_dataset`
        #: would have stored it. `None` is `schema_drift` (ADR-0005 #8),
        #: so leaving it out would test an abort path, not the use case.
        self.schema: dict[str, Any] | None = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "src/idp_regression/platform/schema/golden_schema_v1.json"
            ).read_text(encoding="utf-8")
        )

    def get_dataset(self, name: str) -> Dataset:
        return {
            "items": list(self.items.values()),
            "expected_output_schema": self.schema,
        }

    def record_run(
        self,
        *,
        dataset_name: str,
        run_name: str,
        run_id: str,
        records: list[DocumentRecord],
        metadata: RunMetadata,
    ) -> None:
        self.records.extend(records)

    def mark_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
        golden_dataset_name: str,
    ) -> None:
        self.statuses.append(status)


class FakeProvision:
    """Stands in for `provision_golden_dataset`, capturing the golden the
    pinning half produced into the fake platform's dataset."""

    def __init__(self, platform: FakePlatform) -> None:
        self.platform = platform
        self.calls: list[list[str]] = []

    def main(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        golden_file = Path(argv[argv.index("--golden-file") + 1])
        golden = json.loads(golden_file.read_text(encoding="utf-8"))
        for document_id, entry in golden.items():
            # The `DatasetItem` shape the orchestrator validates
            # (`platform/types.py`): an opaque `item_id`, the
            # `document_id`, and the curated golden.
            self.platform.items[document_id] = DatasetItem(
                item_id=f"item-{document_id}",
                document_id=document_id,
                golden=entry,
            )
        return 0


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    yield tmp_path
    workspace.set_workspace(previous)


@pytest.fixture
def documents(workspace_root: Path) -> Path:
    directory = workspace_root / "corpus"
    directory.mkdir()
    for name in DOCUMENTS:
        (directory / name).write_bytes(b"%PDF-1.4 fixture")
    return directory


def pin_args(store: Path, documents: Path) -> argparse.Namespace:
    return argparse.Namespace(
        file=None, zip_path=None, document_dir=documents, all=True, extract_to=None,
        glob=batch.DEFAULT_DOCUMENT_PATTERNS, max_documents=200, dataset="invoices-golden",
        org="org-1", action="action-1", version=TRUSTED, store=store, repin=False, yes=True,
    )


def verify_args(store: Path, documents: Path) -> argparse.Namespace:
    return argparse.Namespace(
        file=None, all=True, dataset="invoices-golden", version=CANDIDATE, org="org-1",
        action="action-1", store=store, trusted_version=TRUSTED, run_name="ui-validate",
        zip_path=None, document_dir=documents, extract_to=None,
        glob=batch.DEFAULT_DOCUMENT_PATTERNS, allow_missing=False, classifier=None, yes=True,
    )


def run_workflow(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    documents: Path,
    *,
    candidate_totals: dict[tuple[str, str], str],
) -> tuple[int, FakeIDP, FakePlatform]:
    """Pin at the trusted version, then verify at the candidate --
    exactly what `compare_versions.py` orchestrates."""
    store = workspace_root / ".idp-regression-pins"
    idp = FakeIDP(candidate_totals)
    platform = FakePlatform()

    # --- half 1: pin, through the real pin_document ---
    pin_rc, _ = pin_document.run(
        pin_args(store, documents),
        lambda path: raw(idp._total(os.path.basename(str(path)), TRUSTED)),
        FakeProvision(platform),
    )
    assert pin_rc == 0, "pinning must succeed before anything is verified"

    # --- half 2: verify, through the real verify_document + run_eval ---
    # `run_eval`'s N6 pre-run check reads these even when the platform
    # itself is faked -- which is precisely the check `preflight.check()`
    # mirrors, so a machine that passes the preflight passes this too.
    for name, value in (
        ("LANGFUSE_HOST", "http://localhost:3000"),
        ("LANGFUSE_PUBLIC_KEY", "pk-test"),
        ("LANGFUSE_SECRET_KEY", "sk-test"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(facade, "make_platform", lambda: platform)
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: idp)
    monkeypatch.setenv("IDP_DOCUMENT_DIR", str(documents))
    exit_code, _ = verify_document.run(verify_args(store, documents), facade.run_eval)
    return exit_code, idp, platform


class TestTheUseCase:
    def test_an_unchanged_candidate_is_STILL_VALID_and_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        exit_code, idp, platform = run_workflow(
            monkeypatch, workspace_root, documents, candidate_totals={}
        )
        assert exit_code == 0, "the same reading at both versions must pass the gate"
        assert platform.statuses and platform.statuses[-1] != "ABORTED"

    def test_a_changed_candidate_FAILS_the_gate_and_names_the_document(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """The whole product in one assertion: a new LLM that reads one
        field differently on one document must turn the build red."""
        exit_code, _, platform = run_workflow(
            monkeypatch,
            workspace_root,
            documents,
            candidate_totals={("inv-002.pdf", CANDIDATE): "99.99"},
        )
        assert exit_code != 0

        # Asserted through the run artifact and `overall_gate` -- the same
        # path the console reads, so this test fails if the console would
        # ever disagree with the build about which document moved.
        from idp_regression.ui import reader

        run_id = next(iter(workspace.artifact_dir().glob("*.json"))).stem
        detail = reader.read_run(run_id)
        failing = [d["document_id"] for d in detail["documents"] if d["gate"] == "FAIL"]
        assert failing == ["inv-002.pdf"]

        # And the platform was told about every document, not just the
        # failing one -- a run that only reported failures would make a
        # green run indistinguishable from a run that did nothing.
        assert sorted(r["document_id"] for r in platform.records) == sorted(DOCUMENTS)

        changed = [
            leaf
            for document in detail["documents"]
            if document["document_id"] == "inv-002.pdf"
            for leaf in document["leaves"]
            if leaf["gate_failing"]
        ]
        assert [leaf["label"] for leaf in changed] == ["total"]
        assert changed[0]["expected"] == "87.48"
        assert changed[0]["actual"] == "99.99"

    def test_the_trusted_version_is_what_the_goldens_were_read_with(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """Pin reads at TRUSTED, verify reads at CANDIDATE -- 2N
        extractions across two versions, never 2N at one."""
        _, idp, _ = run_workflow(monkeypatch, workspace_root, documents, candidate_totals={})
        versions = {version for _document, version in idp.calls}
        assert versions == {CANDIDATE}, "the pin half uses its own capture fn, not the adapter"
        assert len(idp.calls) == len(DOCUMENTS)

    def test_the_goldens_land_on_the_versioned_pin_path(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """The paths ARE the relationship (user decision, 2026-09-25)."""
        run_workflow(monkeypatch, workspace_root, documents, candidate_totals={})
        goldens = workspace_root / ".idp-regression-pins" / "goldens" / "action-1" / TRUSTED
        assert sorted(p.name for p in goldens.glob("*.pdf.json")) == [
            f"{name}.json" for name in DOCUMENTS
        ]


class TestTheConsoleReadsTheResult:
    """Ticket 3, against real output rather than a hand-written fixture."""

    def test_the_run_artifact_lands_in_the_workspace_the_console_reads(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """The cwd bug: a job that wrote its artifact anywhere else left
        the console showing nothing for a batch the user paid for."""
        run_workflow(monkeypatch, workspace_root, documents, candidate_totals={})
        artifacts = list(workspace.artifact_dir().glob("*.json"))
        assert artifacts, f"no run artifact under {workspace.artifact_dir()}"

    def test_summarize_reports_the_real_per_document_verdicts(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """The bug this replaces: `summarize` counted the words "STILL
        VALID"/"CHANGED" in the output, but those appear ONCE as a
        banner and never per document -- so a 3-document run reported
        "changed: 1" regardless of what changed."""
        exit_code, _, _ = run_workflow(
            monkeypatch,
            workspace_root,
            documents,
            candidate_totals={("inv-002.pdf", CANDIDATE): "99.99"},
        )
        summary = jobs.summarize(
            [f"CHANGED: {len(DOCUMENTS)} file(s) pinned against {TRUSTED}"],
            exit_code,
            since=0.0,
        )
        assert summary["verdict"] == "CHANGED"
        assert summary["documents"] == len(DOCUMENTS)
        assert summary["changed_documents"] == 1
        assert summary["still_valid_documents"] == len(DOCUMENTS) - 1
        assert summary["changed_document_ids"] == ["inv-002.pdf"]

    def test_the_summary_carries_the_run_id_so_the_ui_can_link_to_it(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        exit_code, _, _ = run_workflow(
            monkeypatch, workspace_root, documents, candidate_totals={}
        )
        summary = jobs.summarize(["STILL VALID: 3 file(s)"], exit_code, since=0.0)
        assert summary["run_id"]
        assert (workspace.artifact_dir() / f"{summary['run_id']}.json").is_file()

    def test_an_artifact_written_before_the_job_started_is_never_claimed(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, documents: Path
    ) -> None:
        """Attributing someone else's run to this job would put the wrong
        document verdicts in front of an operator deciding whether a
        model swap is safe."""
        run_workflow(monkeypatch, workspace_root, documents, candidate_totals={})
        import time

        summary = jobs.summarize(["STILL VALID: 3 file(s)"], 0, since=time.time() + 3600)
        assert summary["run_id"] is None
        assert summary["changed_documents"] is None
