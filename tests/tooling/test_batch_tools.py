"""Hermetic tests for the two batch operator tools (2026-09-24):

    scripts/bootstrap_golden_set.py   PDFs -> a DRAFT golden set      (#1)
    scripts/noise_floor.py            PDFs -> a self-consistency report (#2)

Both spend real IDP quota in production, so every test here drives them
through their injected seam (`capture_fn` / `adapter`) and never touches
the network. Three properties are worth pinning, and they are the three
that would be expensive to discover live:

1. **The quota guard holds.** Without `--yes`, neither tool may make a
   single call -- the fakes assert this by raising if invoked.
2. **Nothing paid for is lost.** A failure at document 2 of 3 leaves
   document 1's capture and draft on disk.
3. **The noise-floor arithmetic is right.** A baseline that silently
   mis-states its own rate is worse than no baseline; `aggregate()` is
   pure and is tested directly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

import pytest

from idp_regression.orchestration.run_artifact import artifact_envelope

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(name: str) -> ModuleType:
    """`scripts/` is not a package; load by path, as the tools do."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


batch = _load("_batch")
bootstrap = _load("bootstrap_golden_set")
noise_floor = _load("noise_floor")


# ── fixtures: a raw capture and its normalized twin ───────────────────


def _raw(total: str = "87.48", sku: str = "SKU-1") -> dict[str, Any]:
    """The live wire shape (tests/fixtures/live/seed-001-clean.raw.json),
    trimmed. Recorded shape, not one authored to match the parser -- SR-1."""
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
                    "sku": {"value": sku, "confidenceScore": 99.0},
                    "amount": {"value": "65.00", "confidenceScore": 99.0},
                }
            ]
        },
    }


def _normalized(total: str = "87.48", sku: str = "SKU-1") -> dict[str, Any]:
    return {
        "status": "SUCCEEDED",
        "fields": {
            "invoice_number": {"value": "INV-1001", "confidence": 0.99},
            "total": {"value": total, "confidence": 0.99},
            "vendor_name": {"value": "Acme Office Supplies", "confidence": 0.99},
        },
        "tables": {
            "line_items": [
                {"sku": {"value": sku, "confidence": 0.99}, "amount": {"value": "65.00"}}
            ]
        },
    }


def _documents(tmp_path: Path, count: int) -> Path:
    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    for index in range(1, count + 1):
        (document_dir / f"doc-{index:03d}.pdf").write_bytes(b"%PDF-1.4 not a real pdf")
    return document_dir


# ── _batch: discovery, the quota guard, owner-only writes ─────────────


def test_discover_documents_is_sorted_and_capped(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 5)
    (document_dir / "subdir").mkdir()

    found = batch.discover_documents(document_dir, "*.pdf", 3)

    assert [p.name for p in found] == ["doc-001.pdf", "doc-002.pdf", "doc-003.pdf"]


def test_discover_documents_skips_a_symlink_out_of_the_directory(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 1)
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"%PDF")
    (document_dir / "linked.pdf").symlink_to(outside)

    found = batch.discover_documents(document_dir, "*.pdf", 10)

    assert [p.name for p in found] == ["doc-001.pdf"]


def test_confirm_cost_refuses_without_approval() -> None:
    with pytest.raises(batch.QuotaRefusedError) as excinfo:
        batch.confirm_cost(documents=1000, extractions_each=2, approved=False)
    # The number has to be IN the refusal: that is the whole point of it.
    assert "2000" in str(excinfo.value)


def test_confirm_cost_returns_the_total_when_approved() -> None:
    assert batch.confirm_cost(documents=100, extractions_each=3, approved=True) == 300


def test_write_private_json_is_owner_only_and_leaves_no_partial(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.json"

    batch.write_private_json(target, {"a": 1})

    assert json.loads(target.read_text()) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert list(target.parent.glob(".*partial")) == []


def test_ensure_private_dir_tightens_an_existing_world_readable_dir(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o777)

    batch.ensure_private_dir(loose)

    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


def test_extract_with_containment_never_leaks_the_exception_message(tmp_path: Path) -> None:
    class Boom:
        def extract(self, *_: object) -> dict[str, Any]:
            raise RuntimeError("Bearer sekrit-token-value")

    with pytest.raises(batch.DocumentFailedError) as excinfo:
        batch.extract_with_containment(
            Boom(), tmp_path / "d.pdf", action_id="a", version="1.0.0"
        )

    assert excinfo.value.error_type == "RuntimeError"
    assert "sekrit" not in str(excinfo.value)


# ── #1 bootstrap_golden_set ───────────────────────────────────────────


def _bootstrap_args(tmp_path: Path, document_dir: Path | None, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "out": tmp_path / "draft_golden_set.json",
        "document_dir": document_dir,
        "zip_path": None,
        "extract_to": None,
        "glob": "*.pdf",
        "org": "org",
        "action": "action",
        "version": "1.0.0",
        "captures_dir": None,
        "max_documents": 200,
        "resume": False,
        "from_captures": False,
        "plan": False,
        "review_sample": 25,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_bootstrap_drafts_every_document_and_writes_the_worklist(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    args = _bootstrap_args(tmp_path, document_dir)

    exit_code, summary = bootstrap.run(args, lambda document: _raw())

    assert exit_code == 0
    golden_set = json.loads(args.out.read_text())
    assert sorted(golden_set) == ["doc-001", "doc-002", "doc-003"]
    entry = golden_set["doc-001"]
    assert entry["document_id"] == "doc-001.pdf"
    assert entry["fields"]["total"] == {"value": "87.48", "type": "number", "critical": True}
    # The match_key guess is the one that silently mis-pairs rows, so it
    # must be the preferred column, not a fallback.
    assert entry["tables"]["line_items"]["match_key"] == "sku"

    review = Path(summary["review"]).read_text()
    assert "NOT a golden set" not in review  # phrasing lives in the terminal summary
    assert "reconciliation worklist" in review
    assert "doc-001" in review


def test_bootstrap_without_yes_makes_no_capture_call(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("quota spent without --yes")

    exit_code, summary = bootstrap.run(_bootstrap_args(tmp_path, document_dir, yes=False), never)

    assert exit_code == 2
    assert summary == {}


def test_bootstrap_keeps_what_it_paid_for_when_a_document_fails(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    args = _bootstrap_args(tmp_path, document_dir)

    def flaky(document: Path) -> dict[str, Any]:
        if document.name == "doc-002.pdf":
            raise TimeoutError("poll budget exhausted")
        return _raw()

    exit_code, summary = bootstrap.run(args, flaky)

    assert exit_code == 1
    assert [f["document_id"] for f in summary["failures"]] == ["doc-002.pdf"]
    # The two that succeeded are on disk, both drafted and captured.
    assert sorted(json.loads(args.out.read_text())) == ["doc-001", "doc-003"]
    captures = sorted(p.name for p in Path(summary["captures_dir"]).glob("*.raw.json"))
    assert captures == ["doc-001.pdf.raw.json", "doc-003.pdf.raw.json"]


def test_bootstrap_resume_skips_what_is_already_captured(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    args = _bootstrap_args(tmp_path, document_dir)
    bootstrap.run(args, lambda document: _raw())

    called: list[str] = []

    def counting(document: Path) -> dict[str, Any]:
        called.append(document.name)
        return _raw()

    exit_code, _ = bootstrap.run(_bootstrap_args(tmp_path, document_dir, resume=True), counting)

    assert exit_code == 0
    assert called == []


def test_bootstrap_from_captures_redrafts_without_spending_quota(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)
    args = _bootstrap_args(tmp_path, document_dir)
    bootstrap.run(args, lambda document: _raw())
    args.out.unlink()

    exit_code, summary = bootstrap.run(
        _bootstrap_args(tmp_path, None, from_captures=True, yes=False), None
    )

    assert exit_code == 0
    assert summary["drafted"] == 2


def test_bootstrap_capture_is_written_before_it_is_drafted(tmp_path: Path) -> None:
    """A capture that cannot be drafted (unknown envelope) must still be
    on disk: the 24-hour window does not reopen, and `--from-captures`
    is the only way to recover from a drafting-rule bug."""
    document_dir = _documents(tmp_path, 1)
    args = _bootstrap_args(tmp_path, document_dir)

    exit_code, summary = bootstrap.run(args, lambda document: {"status": "SUCCEEDED"})

    assert exit_code == 1
    assert summary["failures"][0]["document_id"] == "doc-001.pdf"
    assert (Path(summary["captures_dir"]) / "doc-001.pdf.raw.json").exists()


# ── #2 noise_floor ────────────────────────────────────────────────────


def _noise_args(tmp_path: Path, document_dir: Path, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "document_dir": document_dir,
        "zip_path": None,
        "extract_to": None,
        "glob": "*.pdf",
        "org": "org",
        "action": "action",
        "version": "1.0.0",
        "repeats": 2,
        "max_documents": 100,
        "out": tmp_path / "report.json",
        "include_values": False,
        "plan": False,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _ScriptedAdapter:
    """Returns a pre-scripted sequence of normalized outputs per document
    — one entry per repeat — so a known amount of self-disagreement can
    be injected and the reported rate checked against it."""

    def __init__(self, by_document: dict[str, list[dict[str, Any]]]) -> None:
        self._by_document = by_document
        self.calls = 0

    def extract(self, document_path: str, action_id: str, version: str) -> dict[str, Any]:
        self.calls += 1
        name = Path(document_path).name
        return self._by_document[name].pop(0)


def test_noise_floor_reports_a_stable_extractor_as_zero(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)
    adapter = _ScriptedAdapter(
        {
            "doc-001.pdf": [_normalized(), _normalized()],
            "doc-002.pdf": [_normalized(), _normalized()],
        }
    )

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 0
    assert report["summary"]["field_instability_rate"] == 0.0
    assert report["summary"]["gate_noise_rate"] == 0.0
    # A 0% floor over a small sample is explicitly called a weak result.
    assert any("weak result" in line for line in report["interpretation"])


def test_noise_floor_counts_one_differing_field_out_of_four_leaves(tmp_path: Path) -> None:
    """One document, one repeat. The reference has 3 fields plus a 1-row
    table whose `sku` column is the match_key and is therefore the join,
    not a comparison -- 4 comparable leaves, not 5. `total` comes back
    different: 1/4 = 25%, and `total` is critical in the drafted
    reference, so the repeat also fails the gate: 1/1 comparisons."""
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized(total="87.49")]})

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 0
    assert report["summary"]["field_observations"] == 4
    assert report["summary"]["unstable_observations"] == 1
    assert report["summary"]["field_instability_rate"] == 0.25
    assert report["by_field"]["total"]["verdicts"] == {"wrong_value": 1}
    assert report["summary"]["gate_noise_rate"] == 1.0


def test_noise_floor_flattens_table_rows_into_per_column_observations(tmp_path: Path) -> None:
    """A 40-line invoice's line-item noise must not hide behind a single
    table-level observation."""
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized(sku="SKU-2")]})

    _, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert any(field.startswith("line_items.") for field in report["by_field"])
    assert report["summary"]["unstable_observations"] >= 1


def test_noise_floor_writes_no_values_unless_asked(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized(total="99.99")]})
    args = _noise_args(tmp_path, document_dir)

    noise_floor.run(args, adapter)

    written = args.out.read_text()
    assert "99.99" not in written
    assert "87.48" not in written
    assert "total" in written  # names and verdicts, yes; values, no
    assert stat.S_IMODE(args.out.stat().st_mode) == 0o600


def test_noise_floor_include_values_records_the_difference(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized(total="99.99")]})
    args = _noise_args(tmp_path, document_dir, include_values=True)

    noise_floor.run(args, adapter)

    written = json.loads(args.out.read_text())
    assert written["includes_values"] is True
    assert written["comparisons"][0]["differences"]["total"] == {
        "first_run": "87.48",
        "repeat": "99.99",
    }


def test_noise_floor_without_yes_makes_no_extraction(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    adapter = _ScriptedAdapter({})

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir, yes=False), adapter)

    assert exit_code == 2
    assert report == {}
    assert adapter.calls == 0


def test_noise_floor_rejects_a_single_repeat() -> None:
    assert noise_floor.main(
        ["--document-dir", ".", "--org", "o", "--action", "a", "--version", "1.0.0",
         "--repeats", "1"]
    ) == 2


def test_noise_floor_survives_a_failed_repeat_and_still_reports(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)

    class HalfBroken(_ScriptedAdapter):
        def extract(self, document_path: str, action_id: str, version: str) -> dict[str, Any]:
            if Path(document_path).name == "doc-002.pdf":
                raise TimeoutError("poll budget exhausted")
            return super().extract(document_path, action_id, version)

    adapter = HalfBroken({"doc-001.pdf": [_normalized(), _normalized()]})

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 1
    assert report["summary"]["documents"] == 1
    assert report["failures"][0]["error_type"] == "TimeoutError"


# ── the arithmetic, directly ──────────────────────────────────────────


def test_aggregate_computes_both_rates_independently() -> None:
    comparisons = [
        {
            "document_id": "a.pdf",
            "gate": "FAIL",
            "observations": [("total", "wrong_value"), ("vendor", "match")],
        },
        {
            "document_id": "b.pdf",
            "gate": "PASS",
            "observations": [("total", "match"), ("vendor", "match")],
        },
    ]

    body = noise_floor.aggregate(comparisons, [])

    assert body["summary"]["field_observations"] == 4
    assert body["summary"]["unstable_observations"] == 1
    assert body["summary"]["field_instability_rate"] == 0.25
    # Gate noise is a DOCUMENT-level rate, not the field rate: one of two
    # comparisons failed, so 50% -- conflating the two would understate
    # how many red builds a drafted golden produces.
    assert body["summary"]["gate_noise_rate"] == 0.5
    assert body["by_document"][0]["unstable_fields"] == ["total"]


def test_aggregate_on_an_empty_batch_states_nothing_rather_than_zero() -> None:
    body = noise_floor.aggregate([], [])

    assert body["summary"]["field_instability_rate"] == 0.0
    assert noise_floor._interpretation(body["summary"]) == [
        "No comparable field observations. Nothing can be concluded."
    ]


def test_aggregate_orders_by_field_worst_first() -> None:
    comparisons = [
        {
            "document_id": "a.pdf",
            "gate": "FAIL",
            "observations": [("steady", "match"), ("flaky", "wrong_value")],
        },
    ]

    body = noise_floor.aggregate(comparisons, [])

    assert list(body["by_field"]) == ["flaky", "steady"]


# ── #3 batch provisioning (scripts/provision_golden_dataset.py --all) ──
#
# The step that turns a drafted golden set into a RUNNABLE dataset. The
# fake client below records every request, so these assert what actually
# reaches the platform -- including the two things that would otherwise
# only surface live: that the schema is provisioned once per batch and
# not once per item, and that nothing is written at all when the batch
# would exceed the ceiling above which `run_eval` aborts.

provision = _load("provision_golden_dataset")


class _RecordingClient:
    """Stands in for `UrllibHttpClient`, recording every request. A key
    in `reject` returns the HTTP status it maps to."""

    def __init__(self, reject: dict[str, int] | None = None, **_: object) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self._reject = reject or {}

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.requests.append((method, path, body))
        document_id = (body or {}).get("input", {}).get("document_id", "")
        for marker, status in self._reject.items():
            if marker in document_id:
                # A real Langfuse 400 echoes the offending golden value.
                return status, {"message": "expectedOutput.fields.total.value INV-1001 bad"}
        return 200, {"id": f"item-{len(self.requests)}"}

    @property
    def item_posts(self) -> list[Any]:
        return [b for _m, p, b in self.requests if p == "/api/public/dataset-items"]


def _golden_file(tmp_path: Path, entries: dict[str, Any]) -> Path:
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(entries))
    return path


def _entry(document_id: str, total: str = "87.48") -> dict[str, Any]:
    return {
        "document_id": document_id,
        "fields": {
            "invoice_number": {"value": "INV-1001", "type": "id", "critical": True},
            "total": {"value": total, "type": "number", "critical": True},
        },
    }


@pytest.fixture
def provisioned(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Wires the provisioning script to a recording client and a no-op
    schema provisioner, and supplies the platform env it requires."""
    client = _RecordingClient()
    monkeypatch.setattr(provision, "UrllibHttpClient", lambda **kwargs: client)
    monkeypatch.setattr(provision, "provision_golden_schema", lambda c, dataset_name: None)
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    return client


def test_provision_all_writes_every_entry(tmp_path: Path, provisioned: Any) -> None:
    golden = _golden_file(
        tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf"), "c": _entry("c.pdf")}
    )

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    assert rc == 0
    assert len(provisioned.item_posts) == 3
    assert [p["input"]["document_id"] for p in provisioned.item_posts] == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
    ]


def test_provision_all_provisions_the_schema_once_not_once_per_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provisioned: Any
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        provision, "provision_golden_schema", lambda c, dataset_name: calls.append(dataset_name)
    )
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf")})

    provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    assert calls == ["ds"]


def test_provision_all_item_ids_are_deterministic_so_a_re_run_upserts(
    tmp_path: Path, provisioned: Any
) -> None:
    """The property that makes "fix the bad entries and re-run the same
    command" safe: a second pass must not duplicate the first (DEBT-82)."""
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf")})
    argv = ["--dataset", "ds", "--all", "--golden-file", str(golden)]

    provision.main(argv)
    first = [p["id"] for p in provisioned.item_posts]
    provisioned.requests.clear()
    provision.main(argv)

    assert [p["id"] for p in provisioned.item_posts] == first


def test_provision_all_names_an_invalid_entry_and_never_prints_its_values(
    tmp_path: Path, provisioned: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reconciliation typo has to be reported HERE, by name -- the
    platform's own 400 for it is a body this script may never print."""
    broken = _entry("b.pdf")
    broken["fields"]["total"]["type"] = "currency"  # not one of the four field types
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": broken})

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "INVALID b" in err
    assert "87.48" not in err and "INV-1001" not in err
    # DEBT-92: validation is local and free, so one invalid entry refuses
    # the batch. Provisioning the rest left a partial dataset that a run
    # could pass against.
    assert provisioned.item_posts == []


def test_provision_all_skip_invalid_provisions_the_rest_when_asked(
    tmp_path: Path, provisioned: Any
) -> None:
    broken = _entry("b.pdf")
    broken["fields"]["total"]["type"] = "currency"
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": broken})

    rc = provision.main(
        ["--dataset", "ds", "--all", "--golden-file", str(golden), "--skip-invalid"]
    )

    assert rc == 1, "still non-zero: the batch is incomplete"
    assert [p["input"]["document_id"] for p in provisioned.item_posts] == ["a.pdf"]


def test_provision_all_stop_on_error_writes_nothing_when_an_entry_is_invalid(
    tmp_path: Path, provisioned: Any
) -> None:
    broken = _entry("b.pdf")
    del broken["fields"]["total"]["type"]
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": broken})

    rc = provision.main(
        ["--dataset", "ds", "--all", "--golden-file", str(golden), "--stop-on-error"]
    )

    assert rc == 1
    assert provisioned.item_posts == []


def test_provision_all_continues_past_a_rejected_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _RecordingClient(reject={"b.pdf": 400})
    monkeypatch.setattr(provision, "UrllibHttpClient", lambda **kwargs: client)
    monkeypatch.setattr(provision, "provision_golden_schema", lambda c, dataset_name: None)
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "x")
    golden = _golden_file(
        tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf"), "c": _entry("c.pdf")}
    )

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    err = capsys.readouterr().err
    assert rc == 1
    assert len(client.item_posts) == 3  # c was still attempted after b failed
    assert "HTTP 400" in err and "withheld" in err
    assert "INV-1001" not in err  # the 400 body is never echoed
    assert "upserted, not duplicated" in err


def test_provision_all_refuses_a_dataset_run_eval_could_never_run(
    tmp_path: Path, provisioned: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """`run_eval` ABORTS above its ceiling rather than truncating, so a
    batch past it must write nothing at all -- not most of it."""
    golden = _golden_file(
        tmp_path, {f"doc-{i}": _entry(f"doc-{i}.pdf") for i in range(5)}
    )

    rc = provision.main(
        ["--dataset", "ds", "--all", "--golden-file", str(golden), "--max-items", "4"]
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert provisioned.requests == []
    assert "--max-documents-per-run" in err


def test_provision_max_items_defaults_to_the_run_eval_ceiling() -> None:
    from idp_regression.orchestration.facade import DEFAULT_MAX_DOCUMENTS_PER_RUN

    assert provision.DEFAULT_MAX_DOCUMENTS_PER_RUN == DEFAULT_MAX_DOCUMENTS_PER_RUN


def test_provision_all_refuses_to_perturb_a_whole_set(tmp_path: Path, provisioned: Any) -> None:
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf")})

    rc = provision.main(
        [
            "--dataset", "ds", "--all", "--golden-file", str(golden),
            "--perturb-field", "total", "--perturb-value", "1.00",
        ]
    )

    assert rc == 2
    assert provisioned.requests == []


def test_provision_all_dry_run_lists_every_item_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run must never touch the network")

    monkeypatch.setattr(provision, "UrllibHttpClient", _explode)
    monkeypatch.setattr(provision, "provision_golden_schema", _explode)
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf")})

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden), "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("would POST /api/public/dataset-items") == 2
    assert "87.48" not in out and "INV-1001" not in out
    assert "payload_sha256=" in out


# ── the gaps a coverage pass over this file found (2026-09-24) ────────
#
# Measured with stdlib `trace` (the project carries no coverage dep):
# `main()`'s argparse/terminal-summary lines and the live IDP capture
# closure are deliberately left alone -- the first is presentation, the
# second is the seam these tests exist to bypass. What follows is the
# rest: the branches that decide what SURVIVES a partial failure, which
# is exactly where an untested line costs a batch.


def test_bootstrap_rejects_a_corrupt_out_file_without_printing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A truncated `--out` must fail with a message, not a traceback --
    and must not echo what it managed to read back."""
    document_dir = _documents(tmp_path, 1)
    args = _bootstrap_args(tmp_path, document_dir)
    args.out.write_text('{"a": {"document_id": "a.pdf", "fields": {"total": {"val')

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("must not spend quota against an unreadable golden set")

    exit_code, summary = bootstrap.run(args, never)

    err = capsys.readouterr().err
    assert (exit_code, summary) == (2, {})
    assert "not readable as a golden set" in err
    assert "contents withheld" in err
    assert "total" not in err
    assert "--from-captures" in err  # the recovery path is named


def test_bootstrap_main_requires_every_run_identity_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR-0004 A8/A9: no environment fallback. What a golden set was
    drafted FROM must be visible in the command that drafted it, so the
    flags are required even when the environment could supply them."""
    # `.env` may well carry IDP_ORG_ID / IDP_ACTION_ID /
    # IDP_TEST_ACTION_VERSION / IDP_DOCUMENT_DIR -- `load_dotenv()` runs
    # before this check, and the answer must still be "pass the flags".
    rc = bootstrap.main(["--out", "/dev/null"])

    err = capsys.readouterr().err
    assert rc == 2
    for flag in ("--document-dir", "--org", "--action", "--version"):
        assert flag in err


def test_noise_floor_keeps_a_document_whose_later_repeat_fails(tmp_path: Path) -> None:
    """The reference pass succeeded and was paid for. A failed repeat
    must cost that one comparison, not the document -- and must not stop
    the next document."""
    document_dir = _documents(tmp_path, 2)

    class FailsSecondPassOfDoc1(_ScriptedAdapter):
        def extract(self, document_path: str, action_id: str, version: str) -> dict[str, Any]:
            name = Path(document_path).name
            if name == "doc-001.pdf" and not self._by_document[name]:
                raise TimeoutError("poll budget exhausted")
            return super().extract(document_path, action_id, version)

    adapter = FailsSecondPassOfDoc1(
        {
            "doc-001.pdf": [_normalized()],  # reference only; the repeat raises
            "doc-002.pdf": [_normalized(), _normalized()],
        }
    )

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 1
    assert report["failures"] == [
        {"document_id": "doc-001.pdf", "attempt": 2, "error_type": "TimeoutError"}
    ]
    # doc-002 still produced its comparison; doc-001 contributed none.
    assert [d["document_id"] for d in report["by_document"]] == ["doc-002.pdf"]
    assert report["summary"]["comparisons"] == 1


def test_noise_floor_contains_a_classifier_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed repeat that `classify` rejects is a failed comparison,
    not a crashed batch."""
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized()]})

    def _raise(golden: Any, actual: Any) -> Any:
        raise ValueError("Acme Office Supplies is not a valid row")

    monkeypatch.setattr(noise_floor, "classify_pinned_file", _raise)

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 1
    assert report["failures"][0]["error_type"] == "ValueError"
    assert report["summary"]["comparisons"] == 0
    # INV-02: the classifier's message could quote a value; only its type
    # is recorded.
    assert "Acme" not in json.dumps(report)


def test_noise_floor_include_values_records_a_line_item_difference(tmp_path: Path) -> None:
    """Table drift is the noisiest part of these extractions, so the
    diagnostic path has to carry it -- keyed by column AND match_key, or
    a 40-line invoice's differences are unattributable."""
    document_dir = _documents(tmp_path, 1)
    drifted = _normalized()
    drifted["tables"]["line_items"][0]["amount"] = {"value": "65.01", "confidence": 0.9}
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), drifted]})
    args = _noise_args(tmp_path, document_dir, include_values=True)

    noise_floor.run(args, adapter)

    differences = json.loads(args.out.read_text())["comparisons"][0]["differences"]
    assert differences["line_items.amount[SKU-1]"] == {"first_run": "65.00", "repeat": "65.01"}


def test_provision_all_records_a_transport_failure_and_keeps_going(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dropped connection mid-batch is not a rejected item: it has its
    own branch, and it must be reported as a class, not a message."""

    class _FlakyClient(_RecordingClient):
        def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
            document_id = (body or {}).get("input", {}).get("document_id", "")
            if document_id == "b.pdf":
                self.requests.append((method, path, body))
                raise provision.TransportError("connection reset while sending INV-1001")
            return super().request(method, path, body)

    client = _FlakyClient()
    monkeypatch.setattr(provision, "UrllibHttpClient", lambda **kwargs: client)
    monkeypatch.setattr(provision, "provision_golden_schema", lambda c, dataset_name: None)
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "x")
    golden = _golden_file(
        tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf"), "c": _entry("c.pdf")}
    )

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "b: transport: TransportError" in err
    assert "INV-1001" not in err
    assert len(client.item_posts) == 3  # c was still attempted


def test_provision_all_stop_on_error_halts_at_a_rejected_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fail-fast posture applies to a PLATFORM rejection too, not
    only to a locally-invalid entry: `c` is never attempted."""
    client = _RecordingClient(reject={"b.pdf": 400})
    monkeypatch.setattr(provision, "UrllibHttpClient", lambda **kwargs: client)
    monkeypatch.setattr(provision, "provision_golden_schema", lambda c, dataset_name: None)
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "x")
    golden = _golden_file(
        tmp_path, {"a": _entry("a.pdf"), "b": _entry("b.pdf"), "c": _entry("c.pdf")}
    )

    rc = provision.main(
        ["--dataset", "ds", "--all", "--golden-file", str(golden), "--stop-on-error"]
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert [p["input"]["document_id"] for p in client.item_posts] == ["a.pdf", "b.pdf"]
    assert "aborting at 'b'" in err
    assert "1 item(s) already written" in err


def test_discover_documents_ignores_a_directory_that_matches_the_glob(tmp_path: Path) -> None:
    """`--glob '*'` over a directory tree would otherwise hand a
    directory to `extract()` and spend a failed extraction on it."""
    document_dir = _documents(tmp_path, 1)
    (document_dir / "archive.pdf").mkdir()

    assert [p.name for p in batch.discover_documents(document_dir, "*.pdf", 10)] == [
        "doc-001.pdf"
    ]


def test_read_json_if_present_rejects_a_json_array(tmp_path: Path) -> None:
    """Valid JSON, wrong shape -- a golden set is an object keyed by
    entry. Caught as a `ValueError`, which `bootstrap.run` turns into the
    named message above rather than a traceback."""
    path = tmp_path / "golden.json"
    path.write_text('[{"document_id": "a.pdf"}]')

    with pytest.raises(ValueError, match="not a JSON object"):
        batch.read_json_if_present(path)


def test_noise_floor_plan_prints_the_cost_without_extracting(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    adapter = _ScriptedAdapter({})

    exit_code, report = noise_floor.run(
        _noise_args(tmp_path, document_dir, plan=True, yes=False), adapter
    )

    assert (exit_code, report) == (0, {})
    assert adapter.calls == 0


def test_noise_floor_survives_an_undraftable_reference_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference extraction succeeded but could not be turned into a
    golden. That document is lost, the batch is not."""
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized()]})

    def _raise(normalized: Any, document_id: str) -> Any:
        raise KeyError("line_items")

    monkeypatch.setattr(noise_floor.draft_golden, "_draft_from_normalized", _raise)

    exit_code, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)

    assert exit_code == 1
    assert report["failures"] == [
        {"document_id": "doc-001.pdf", "attempt": 1, "error_type": "KeyError"}
    ]
    assert report["summary"]["comparisons"] == 0


def test_bootstrap_plan_prints_the_cost_without_capturing(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 4)

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("--plan must never spend quota")

    exit_code, summary = bootstrap.run(
        _bootstrap_args(tmp_path, document_dir, plan=True, yes=False), never
    )

    assert (exit_code, summary) == (0, {})
    # Not even the captures directory is created by a plan.
    assert not (tmp_path / "draft_golden_set.captures").exists()


def test_provision_all_rejects_a_golden_file_that_is_not_an_object(
    tmp_path: Path, provisioned: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "draft.json"
    golden.write_text('[{"document_id": "a.pdf"}]')

    with pytest.raises(SystemExit) as excinfo:
        provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    assert excinfo.value.code == 1
    assert "not a non-empty JSON object" in capsys.readouterr().err
    assert provisioned.requests == []


def test_provision_all_catches_a_structural_error_the_schema_cannot_see(
    tmp_path: Path, provisioned: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A field and a table sharing one name is valid JSON Schema and a
    fail-OPEN gate: they share the verdict map's namespace, so the
    table's `detail` entry silently overwrites the field's verdict.
    `draft_golden` can emit exactly this pair when a capture carries both.
    Caught by `validate_golden_structure`, reported by type only."""
    broken = _entry("b.pdf")
    broken["fields"]["line_items"] = {"value": "3", "type": "number", "critical": True}
    broken["tables"] = {
        "line_items": {"match_key": "sku", "critical": True, "rows": [{"sku": "SKU-1"}]}
    }
    golden = _golden_file(tmp_path, {"a": _entry("a.pdf"), "b": broken})

    rc = provision.main(["--dataset", "ds", "--all", "--golden-file", str(golden)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "INVALID b: structure:" in err
    assert "SKU-1" not in err
    assert provisioned.item_posts == []


# ── #4 reading a run against the floor (scripts/show_run.py --baseline) ─
#
# The step that makes a difference rate sayable. The arithmetic is what
# matters here: a field labelled `within floor` is a field nobody will
# investigate, so the rule that produces that label has to be wrong in
# the safe direction.

show_run = _load("show_run")


def test_field_key_joins_a_table_leaf_to_its_noise_floor_key() -> None:
    """`show_run` labels a table leaf by row; `noise_floor` aggregates by
    column. Get this wrong and every table column reports 'too few to
    tell' -- which looks like caution and is a broken join."""
    assert show_run._field_key("total") == "total"
    assert show_run._field_key("line_items[SKU-1].amount") == "line_items.amount"
    assert show_run._field_key("line_items[SKU-1]") == "line_items"


def test_compare_does_not_call_one_disagreement_a_regression() -> None:
    """The bug the first run of this tool had: 1/30 against a 2% floor is
    3% > 2%, which a bare rate comparison calls ABOVE floor while being
    exactly what a 2% floor produces."""
    comparison = show_run._compare({"vendor_name": (1, 30)}, {"vendor_name": (0.02, 50)})

    assert comparison["vendor_name"]["status"] == "within"


def test_compare_flags_a_field_that_exceeds_what_the_floor_predicts() -> None:
    comparison = show_run._compare({"total": (12, 30)}, {"total": (0.02, 50)})

    assert comparison["total"]["status"] == "above"


def test_compare_flags_any_disagreement_on_a_perfectly_stable_field() -> None:
    """A 0% floor has nothing to explain a disagreement with, so one is
    enough."""
    comparison = show_run._compare({"invoice_number": (1, 30)}, {"invoice_number": (0.0, 50)})

    assert comparison["invoice_number"]["status"] == "above"


def test_compare_refuses_to_judge_a_thin_sample() -> None:
    thin_run = show_run._compare({"total": (3, 5)}, {"total": (0.02, 50)})
    thin_floor = show_run._compare({"total": (3, 50)}, {"total": (0.02, 5)})

    assert thin_run["total"]["status"] == "unknown"
    assert thin_floor["total"]["status"] == "unknown"


def test_compare_marks_a_field_the_baseline_never_saw() -> None:
    comparison = show_run._compare({"new_field": (2, 30)}, {"total": (0.02, 50)})

    assert comparison["new_field"]["status"] == "no_floor"
    assert comparison["new_field"]["floor_rate"] is None


def _cell(verdict: str, *, critical: bool = True) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "expected": "10.00",
        "actual": "10.01" if verdict != "match" else "10.00",
        "confidence": 0.99,
        "critical": critical,
        "format_critical": False,
        "type": "number",
    }


def test_failure_resting_only_on_a_within_floor_field_is_named_as_noise() -> None:
    fields = {"total": _cell("wrong_value"), "vendor_name": _cell("match")}
    comparison = {"total": {"status": "within"}}

    assert show_run._failure_rests_on_noise(fields, comparison) is True


def test_a_failure_with_one_above_floor_field_is_still_real() -> None:
    fields = {"total": _cell("wrong_value"), "vendor_name": _cell("wrong_value")}
    comparison = {"total": {"status": "within"}, "vendor_name": {"status": "above"}}

    assert show_run._failure_rests_on_noise(fields, comparison) is False


def test_an_unknown_or_unmeasured_field_keeps_a_failure_real() -> None:
    """Fail-closed on ignorance: 'this red is noise' is the claim that
    stops an investigation, so it is only made where the floor supports
    it."""
    fields = {"total": _cell("wrong_value")}

    assert show_run._failure_rests_on_noise(fields, {"total": {"status": "unknown"}}) is False
    assert show_run._failure_rests_on_noise(fields, {"total": {"status": "no_floor"}}) is False
    assert show_run._failure_rests_on_noise(fields, {}) is False


def test_a_passing_document_never_rests_on_noise() -> None:
    fields = {"total": _cell("match"), "note": _cell("wrong_value", critical=False)}

    assert show_run._failure_rests_on_noise(fields, {"total": {"status": "within"}}) is False


def test_run_rates_count_the_same_leaves_the_noise_floor_counts() -> None:
    documents = {
        "a.pdf": {"total": _cell("wrong_value"), "vendor": _cell("match")},
        "b.pdf": {"total": _cell("match"), "vendor": _cell("match")},
    }

    assert show_run._run_rates(documents) == {"total": (1, 2), "vendor": (0, 2)}


def test_baseline_index_rejects_a_file_that_is_not_a_noise_floor_report() -> None:
    with pytest.raises(SystemExit, match="not a noise-floor report"):
        show_run._baseline_index({"summary": {}})


def _artifact(tmp_path: Path, documents: dict[str, Any]) -> Path:
    artifact_dir = tmp_path / ".idp-regression-run-artifacts"
    artifact_dir.mkdir()
    path = artifact_dir / "runid.json"
    path.write_text(json.dumps(artifact_envelope("runid", documents, status="complete")))
    return path


def _floor_report(tmp_path: Path, by_field: dict[str, Any], rate: float = 0.04) -> Path:
    path = tmp_path / "floor.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-24T12:00:00+00:00",
                "run_identity": {"org": "o", "action": "act-1", "version": "1.0.0"},
                "summary": {
                    "documents": 50,
                    "comparisons": 50,
                    "field_instability_rate": rate,
                },
                "by_field": by_field,
            }
        )
    )
    return path


def test_show_run_with_a_baseline_names_the_failures_worth_ignoring(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    documents = {
        f"doc-{i:03d}.pdf": {
            "total": _cell("wrong_value" if i <= 4 else "match"),
            "invoice_number": _cell("match"),
        }
        for i in range(1, 31)
    }
    artifact = _artifact(tmp_path, documents)
    floor = _floor_report(
        tmp_path,
        {
            "total": {"observations": 50, "unstable": 7, "instability_rate": 0.14},
            "invoice_number": {"observations": 50, "unstable": 0, "instability_rate": 0.0},
        },
    )

    rc = show_run.main([str(artifact), "--failures-only", "--baseline", str(floor)])

    out = capsys.readouterr().out
    # The gate is untouched by the floor: a run that fails still fails.
    assert rc == 1
    assert "NOISE FLOOR COMPARISON" in out
    assert "total" in out and "within floor" in out
    assert "4 of those 4 failure(s) rest ENTIRELY on fields the floor explains" in out
    assert "it does not clear it" in out


def test_show_run_without_a_baseline_prints_no_floor_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The unannotated output must be unchanged -- `--baseline` is
    additive, and this script is read on screen shares."""
    artifact = _artifact(tmp_path, {"a.pdf": {"total": _cell("wrong_value")}})

    rc = show_run.main([str(artifact)])

    out = capsys.readouterr().out
    assert rc == 1
    assert "NOISE FLOOR COMPARISON" not in out
    assert "[floor" not in out


def test_show_run_baseline_reports_a_real_regression_as_real(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    documents = {
        f"doc-{i:03d}.pdf": {"total": _cell("wrong_value" if i <= 20 else "match")}
        for i in range(1, 31)
    }
    artifact = _artifact(tmp_path, documents)
    floor = _floor_report(
        tmp_path, {"total": {"observations": 50, "unstable": 1, "instability_rate": 0.02}}
    )

    rc = show_run.main([str(artifact), "--baseline", str(floor)])

    out = capsys.readouterr().out
    assert rc == 1
    assert "ABOVE floor" in out
    assert "every failing document fails on at least one field ABOVE the floor" in out


def test_floor_note_distinguishes_no_baseline_from_no_floor_for_this_field() -> None:
    """Silence means "no baseline was given"; a field the baseline never
    saw says so out loud, so an unmeasured field is never read as a
    quiet pass."""
    assert show_run._floor_note("total", {}) == ""
    assert show_run._floor_note("total", {"total": (0.02, 50)}) == "   [floor 2% of 50]"
    assert "no floor" in show_run._floor_note("brand_new", {"total": (0.02, 50)})


# ── #5 zip input ──────────────────────────────────────────────────────
#
# A corpus arrives as a zip, not as a mounted folder. Unpacking one is
# therefore part of the tool -- and unpacking an archive from outside the
# trust boundary is a security surface. Every test below is about what
# must NOT land on disk.

import zipfile  # noqa: E402


def _zip(tmp_path: Path, entries: dict[str, bytes], name: str = "corpus.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member, payload in entries.items():
            zf.writestr(member, payload)
    return path


def test_zip_unpacks_nested_documents_flat_and_owner_only(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        {
            "invoices/2024/a.pdf": b"%PDF-a",
            "invoices/2025/b.png": b"\x89PNG-b",
            "c.tiff": b"II*\x00-c",
        },
    )
    destination = tmp_path / "docs"

    extracted, skipped = batch.extract_documents_from_zip(archive, destination)

    assert sorted(p.name for p in extracted) == ["a.pdf", "b.png", "c.tiff"]
    assert skipped == []
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in extracted)
    assert (destination / "a.pdf").read_bytes() == b"%PDF-a"


def test_zip_rejects_a_path_traversal_entry(tmp_path: Path) -> None:
    """Zip-slip: the classic. Nothing may be written outside the
    extraction directory, and the batch continues without it."""
    archive = _zip(tmp_path, {"../escaped.pdf": b"%PDF", "ok.pdf": b"%PDF"})
    destination = tmp_path / "docs"

    extracted, skipped = batch.extract_documents_from_zip(archive, destination)

    assert [p.name for p in extracted] == ["ok.pdf"]
    assert any("path traversal" in note for note in skipped)
    assert not (tmp_path / "escaped.pdf").exists()


def test_zip_never_extracts_a_symlink(tmp_path: Path) -> None:
    """A link is a way to make a later READ escape the directory even
    though the extraction itself did not."""
    path = tmp_path / "linky.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("link.pdf")
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
        zf.writestr("real.pdf", b"%PDF")

    extracted, skipped = batch.extract_documents_from_zip(path, tmp_path / "docs")

    assert [p.name for p in extracted] == ["real.pdf"]
    assert any("symlink" in note for note in skipped)


def test_zip_skips_archive_junk_and_non_documents(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        {
            "__MACOSX/._a.pdf": b"junk",
            ".DS_Store": b"junk",
            "notes.xlsx": b"spreadsheet",
            "a.pdf": b"%PDF",
        },
    )

    extracted, skipped = batch.extract_documents_from_zip(archive, tmp_path / "docs")

    assert [p.name for p in extracted] == ["a.pdf"]
    # The spreadsheet is REPORTED (it would have cost an extraction),
    # the macOS junk is not (it is noise, not a decision).
    assert any("notes.xlsx" in note for note in skipped)
    assert not any("MACOSX" in note or "DS_Store" in note for note in skipped)


def test_zip_disambiguates_two_documents_with_the_same_basename(tmp_path: Path) -> None:
    """Flattening must never let one document silently overwrite
    another -- that would drop a paid-for extraction and shrink the
    golden set without saying so."""
    archive = _zip(tmp_path, {"jan/a.pdf": b"%PDF-jan", "feb/a.pdf": b"%PDF-feb"})

    extracted, _ = batch.extract_documents_from_zip(archive, tmp_path / "docs")

    assert len(extracted) == 2
    assert {p.read_bytes() for p in extracted} == {b"%PDF-jan", b"%PDF-feb"}


def test_zip_refuses_a_decompression_bomb(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"bomb.pdf": b"0" * 5_000_000})

    with pytest.raises(batch.ZipRejectedError, match="decompression bomb"):
        batch.extract_documents_from_zip(archive, tmp_path / "docs")


def test_zip_refuses_too_many_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch, "ZIP_MAX_ENTRIES", 2)
    archive = _zip(tmp_path, {f"{i}.pdf": b"%PDF" for i in range(3)})

    with pytest.raises(batch.ZipRejectedError, match="over the 2 cap"):
        batch.extract_documents_from_zip(archive, tmp_path / "docs")


def test_zip_refuses_an_oversized_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch, "ZIP_MAX_TOTAL_BYTES", 10)
    archive = _zip(tmp_path, {"a.pdf": b"%PDF" * 100})

    with pytest.raises(batch.ZipRejectedError, match="uncompressed"):
        batch.extract_documents_from_zip(archive, tmp_path / "docs")


def test_zip_refuses_a_file_that_is_not_a_zip(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "corpus.zip"
    not_a_zip.write_bytes(b"this is a PDF, actually")

    with pytest.raises(batch.ZipRejectedError, match="not a readable zip"):
        batch.extract_documents_from_zip(not_a_zip, tmp_path / "docs")


def test_zip_dry_run_writes_nothing(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "b.pdf": b"%PDF"})
    destination = tmp_path / "docs"

    extracted, _ = batch.extract_documents_from_zip(archive, destination, dry_run=True)

    assert [p.name for p in extracted] == ["a.pdf", "b.pdf"]
    assert not destination.exists()


def test_discover_documents_matches_images_as_well_as_pdfs(tmp_path: Path) -> None:
    """A scanned-invoice corpus is TIFFs and JPEGs. Defaulting to `*.pdf`
    would process a handful of files out of a zip of thousands and report
    success."""
    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    for name in ("a.pdf", "b.jpg", "c.TIF", "d.txt"):
        (document_dir / name).write_bytes(b"x")

    found = batch.discover_documents(document_dir, batch.DEFAULT_DOCUMENT_PATTERNS, 10)

    # `d.txt` is not a document; `c.TIF` is (glob is case-insensitive on
    # a case-insensitive filesystem, and either way it must not crash).
    assert "a.pdf" in [p.name for p in found]
    assert "b.jpg" in [p.name for p in found]
    assert "d.txt" not in [p.name for p in found]


def test_bootstrap_drafts_straight_from_a_zip(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"batch/one.pdf": b"%PDF", "batch/two.pdf": b"%PDF"})
    args = _bootstrap_args(
        tmp_path, None, zip_path=archive, glob=batch.DEFAULT_DOCUMENT_PATTERNS
    )

    exit_code, summary = bootstrap.run(args, lambda document: _raw())

    assert exit_code == 0
    assert sorted(json.loads(args.out.read_text())) == ["one", "two"]
    # The unpacked directory is what IDP_DOCUMENT_DIR must point at, so
    # the summary has to name it.
    assert summary["document_dir"] == str(tmp_path / "draft_golden_set.documents")
    assert (Path(summary["document_dir"]) / "one.pdf").exists()


def test_bootstrap_zip_plan_unpacks_nothing(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"one.pdf": b"%PDF"})
    args = _bootstrap_args(
        tmp_path, None, zip_path=archive, plan=True, yes=False,
        glob=batch.DEFAULT_DOCUMENT_PATTERNS,
    )

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("--plan must never spend quota")

    exit_code, summary = bootstrap.run(args, never)

    assert (exit_code, summary) == (0, {})
    assert not (tmp_path / "draft_golden_set.documents").exists()


def test_bootstrap_zip_rejection_stops_before_any_quota(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "corpus.zip"
    not_a_zip.write_bytes(b"nope")
    args = _bootstrap_args(tmp_path, None, zip_path=not_a_zip)

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("a rejected archive must never reach the IDP")

    exit_code, summary = bootstrap.run(args, never)

    assert (exit_code, summary) == (2, {})


def test_bootstrap_main_refuses_both_a_dir_and_a_zip(capsys: pytest.CaptureFixture[str]) -> None:
    rc = bootstrap.main(
        [
            "--out", "/dev/null", "--document-dir", ".", "--zip", "c.zip",
            "--org", "o", "--action", "a", "--version", "1.0.0",
        ]
    )

    assert rc == 2
    assert "not both" in capsys.readouterr().err


def test_noise_floor_main_requires_exactly_one_document_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = ["--org", "o", "--action", "a", "--version", "1.0.0"]

    neither = noise_floor.main(common)
    both = noise_floor.main([*common, "--document-dir", ".", "--zip", "c.zip"])

    assert (neither, both) == (2, 2)
    assert capsys.readouterr().err.count("exactly one") == 2


# ── #6 calibration (scripts/calibrate_golden.py) ──────────────────────
#
# Every rule here DEMOTES a field from critical, which is a fail-OPEN
# change in a project whose stated worst outcome is a silently-wrong
# green build. So the tests are mostly about evidence: what it takes to
# demote, and that a demotion is always reported as a blind spot.

calibrate_golden = _load("calibrate_golden")


def _golden_entry(document_id: str, **fields: str) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "fields": {
            name: {"value": value, "type": "text", "critical": True}
            for name, value in fields.items()
        },
    }


def test_calibrate_demotes_a_field_the_floor_says_is_unstable() -> None:
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1", vendor="A") for i in range(10)}

    calibrated, report = calibrate_golden.calibrate(
        golden, {"total": (0.14, 50), "vendor": (0.0, 50)}
    )

    assert calibrated["d0"]["fields"]["total"]["critical"] is False
    assert calibrated["d0"]["fields"]["vendor"]["critical"] is True
    assert report["fields"]["total"]["reason"] == "unstable"
    assert report["blind_spots"] == ["total"]


def test_calibrate_will_not_demote_on_a_floor_it_cannot_support() -> None:
    """Few floor observations is not evidence of instability. Demoting
    on it would blind the gate for free."""
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1") for i in range(10)}

    calibrated, report = calibrate_golden.calibrate(golden, {"total": (0.9, 3)})

    assert calibrated["d0"]["fields"]["total"]["critical"] is True
    assert report["blind_spots"] == []


def test_calibrate_demotes_a_field_absent_from_much_of_the_corpus() -> None:
    """Worklist instruction #1, decided over the corpus: left critical,
    these documents fail forever."""
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1", po_number="") for i in range(10)}
    golden["d0"]["fields"]["po_number"]["value"] = "PO-1"

    calibrated, report = calibrate_golden.calibrate(golden, {})

    assert calibrated["d0"]["fields"]["po_number"]["critical"] is False
    assert report["fields"]["po_number"]["reason"] == "sparse"
    assert "90%" in report["fields"]["po_number"]["detail"]


def test_calibrate_keeps_a_present_stable_field_critical() -> None:
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1") for i in range(10)}

    calibrated, report = calibrate_golden.calibrate(golden, {"total": (0.0, 50)})

    assert calibrated["d0"]["fields"]["total"]["critical"] is True
    assert report["blind_spots"] == []


def test_calibrate_never_rewrites_an_expected_value() -> None:
    """Calibration flips `critical`, unifies `match_key` and `type`, and
    touches nothing else -- the expected values are what was drafted."""
    golden = {"d0": _golden_entry("d0.pdf", total="87.48")}

    calibrated, _ = calibrate_golden.calibrate(golden, {"total": (0.5, 50)})

    assert calibrated["d0"]["fields"]["total"]["value"] == "87.48"
    assert calibrated["d0"]["document_id"] == "d0.pdf"


def test_calibrate_unifies_a_match_key_that_drifted_across_documents() -> None:
    """`draft_golden` picks a match_key per document, from that
    document's rows alone. A join key that varies by document is not a
    contract."""
    golden = {}
    for i, key in enumerate(("sku", "description", "sku")):
        entry = _golden_entry(f"d{i}.pdf", total="1")
        entry["tables"] = {
            "line_items": {
                "match_key": key,
                "critical": True,
                "rows": [{"sku": "S-1", "description": "widget", "amount": "1.00"}],
            }
        }
        golden[f"d{i}"] = entry

    calibrated, report = calibrate_golden.calibrate(golden, {})

    assert {e["tables"]["line_items"]["match_key"] for e in calibrated.values()} == {"sku"}
    assert report["tables"]["line_items"]["match_key_agreed"] is False
    assert any("match_key was NOT unanimous" in item for item in report["still_human"])


def test_calibrate_demotes_a_table_with_an_unstable_column() -> None:
    golden = {}
    for i in range(10):
        entry = _golden_entry(f"d{i}.pdf", total="1")
        entry["tables"] = {
            "line_items": {
                "match_key": "sku",
                "critical": True,
                "rows": [{"sku": "S-1", "amount": "1.00"}],
            }
        }
        golden[f"d{i}"] = entry

    calibrated, report = calibrate_golden.calibrate(golden, {"line_items.amount": (0.3, 50)})

    assert calibrated["d0"]["tables"]["line_items"]["critical"] is False
    assert "line_items (table)" in report["blind_spots"]


def test_calibrate_unifies_a_type_that_disagreed_across_documents() -> None:
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1") for i in range(10)}
    golden["d9"]["fields"]["total"]["type"] = "number"
    for i in range(9):
        golden[f"d{i}"]["fields"]["total"]["type"] = "text"

    calibrated, report = calibrate_golden.calibrate(golden, {})

    assert {e["fields"]["total"]["type"] for e in calibrated.values()} == {"text"}
    assert report["types"]["total"]["counts"] == {"text": 9, "number": 1}


def test_calibrate_always_says_what_it_cannot_decide() -> None:
    """A pipeline that printed nothing here would read as 'nothing left
    to do', which is the one thing this output must never imply."""
    golden = {"d0": _golden_entry("d0.pdf", total="1")}

    _, with_floor = calibrate_golden.calibrate(golden, {"total": (0.0, 50)})
    _, without_floor = calibrate_golden.calibrate(golden, None)

    assert any("CORRECTNESS" in item for item in with_floor["still_human"])
    assert any("format_critical" in item for item in with_floor["still_human"])
    assert any("NO NOISE FLOOR" in item for item in without_floor["still_human"])
    assert not any("NO NOISE FLOOR" in item for item in with_floor["still_human"])


def test_calibrate_writes_a_set_that_still_validates_and_a_report(tmp_path: Path) -> None:
    """The calibrated file must still be provisionable -- and the report
    must NOT be a key inside it, or `provision --all` would try to
    provision the report as a document."""
    golden = {f"d{i}": _golden_entry(f"d{i}.pdf", total="1") for i in range(3)}
    source = tmp_path / "golden.json"
    source.write_text(json.dumps(golden))
    out = tmp_path / "calibrated.json"

    rc = calibrate_golden.main(["--golden-file", str(source), "--out", str(out)])

    written = json.loads(out.read_text())
    assert rc == 0
    assert sorted(written) == ["d0", "d1", "d2"]
    assert json.loads(out.with_suffix(".calibration.json").read_text())["corpus"]["documents"] == 3
    assert "Gate blind spots" in out.with_suffix(".calibration.md").read_text()
    for entry in written.values():
        assert provision._validation_error(entry) is None


# ── #7 the one-command pipeline (scripts/golden_pipeline.py) ──────────

golden_pipeline = _load("golden_pipeline")


class _FakeStage:
    """Records the argv a stage was called with, and can write the file
    the next stage expects."""

    def __init__(self, exit_code: int = 0, writes: dict[Path, Any] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.exit_code = exit_code
        self._writes = writes or {}

    def main(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        for path, payload in self._writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        return self.exit_code


def _pipeline_stages(tmp_path: Path, **overrides: Any) -> Any:
    work = tmp_path / "work"
    golden = {"d0": _golden_entry("d0.pdf", total="1")}
    defaults = {
        "noise_floor": _FakeStage(writes={work / "noise-floor.json": {"by_field": {}}}),
        "bootstrap_golden_set": _FakeStage(writes={work / "golden.json": golden}),
        "calibrate_golden": _FakeStage(
            writes={
                work / "golden.calibrated.json": golden,
                work / "golden.calibrated.calibration.json": {
                    "blind_spots": ["total"], "still_human": ["CORRECTNESS: ..."]
                },
            }
        ),
        "provision_golden_dataset": _FakeStage(),
        "show_run": _FakeStage(),
        "run_eval": _FakeStage(),
    }
    defaults.update(overrides)
    stages = argparse.Namespace(**defaults)
    # `run_eval` is a bare callable in the real wiring (the orchestration
    # CLI's `main`), so the namespace holds the function -- the recorder
    # stays reachable for assertions.
    stages.run_eval_stage = defaults["run_eval"]
    stages.run_eval = defaults["run_eval"].main
    return stages


def _pipeline_args(tmp_path: Path, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "zip_path": None,
        "document_dir": tmp_path / "docs",
        "dataset": "ds",
        "org": "o",
        "action": "a",
        "version": "1.0.0",
        "candidate_version": None,
        "work_dir": tmp_path / "work",
        "glob": batch.DEFAULT_DOCUMENT_PATTERNS,
        "max_documents": 1000,
        "noise_sample": 100,
        "noise_repeats": 2,
        "noise_floor": None,
        "skip_noise_floor": False,
        "noise_tolerance": None,
        "sparse_threshold": None,
        "stop_after": None,
        "resume": False,
        "plan": False,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_pipeline_runs_every_stage_in_order(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    for i in range(3):
        (tmp_path / "docs" / f"doc-{i}.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    exit_code, summary = golden_pipeline.run(_pipeline_args(tmp_path), stages)

    assert exit_code == 0
    assert stages.noise_floor.calls and stages.bootstrap_golden_set.calls
    assert stages.calibrate_golden.calls and stages.provision_golden_dataset.calls
    # The calibrated set is what gets provisioned, never the raw draft.
    assert "--all" in stages.provision_golden_dataset.calls[0]
    assert str(tmp_path / "work" / "golden.calibrated.json") in (
        stages.provision_golden_dataset.calls[0]
    )
    assert summary["blind_spots"] == ["total"]


def test_pipeline_spends_nothing_without_yes(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    exit_code, summary = golden_pipeline.run(_pipeline_args(tmp_path, yes=False), stages)

    assert (exit_code, summary) == (2, {})
    assert stages.noise_floor.calls == []


def test_pipeline_plan_prints_the_whole_cost_and_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One approval for the whole pipeline, so the number has to include
    every stage that spends."""
    (tmp_path / "docs").mkdir()
    for i in range(10):
        (tmp_path / "docs" / f"doc-{i}.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    exit_code, summary = golden_pipeline.run(
        _pipeline_args(
            tmp_path, plan=True, yes=False, noise_sample=5, candidate_version="2.0.0"
        ),
        stages,
    )

    err = capsys.readouterr().err
    assert (exit_code, summary) == (0, {})
    # 5 sampled x 2 repeats + 10 drafted + 10 candidate = 30
    assert "30 real IDP extraction(s) to be spent" in err
    assert stages.bootstrap_golden_set.calls == []


def test_pipeline_always_passes_resume_to_the_draft_stage(tmp_path: Path) -> None:
    """A re-run must never re-pay for a capture it already has."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    golden_pipeline.run(_pipeline_args(tmp_path), stages)

    assert "--resume" in stages.bootstrap_golden_set.calls[0]


def test_pipeline_skips_the_floor_when_one_is_supplied(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    existing = tmp_path / "floor.json"
    existing.write_text(json.dumps({"by_field": {}}))
    stages = _pipeline_stages(tmp_path)

    _, summary = golden_pipeline.run(_pipeline_args(tmp_path, noise_floor=existing), stages)

    assert stages.noise_floor.calls == []
    assert "--noise-floor" in stages.calibrate_golden.calls[0]
    assert summary["noise_floor"] == str(existing)


def test_pipeline_stops_before_provisioning_an_incomplete_draft(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path, bootstrap_golden_set=_FakeStage(exit_code=1))

    exit_code, _ = golden_pipeline.run(_pipeline_args(tmp_path), stages)

    assert exit_code == 1
    assert stages.provision_golden_dataset.calls == []


def test_pipeline_sets_the_document_dir_the_candidate_run_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_eval` resolves every document_id against IDP_DOCUMENT_DIR.
    The pipeline knows where it put them, so the operator never has to."""
    monkeypatch.delenv("IDP_DOCUMENT_DIR", raising=False)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    golden_pipeline.run(_pipeline_args(tmp_path, candidate_version="2.0.0"), stages)

    assert os.environ["IDP_DOCUMENT_DIR"] == str((tmp_path / "docs").resolve())
    assert "--version" in stages.run_eval_stage.calls[0]
    assert "2.0.0" in stages.run_eval_stage.calls[0]


def test_pipeline_reads_the_candidate_run_against_the_floor(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    golden_pipeline.run(_pipeline_args(tmp_path, candidate_version="2.0.0"), stages)

    assert "--baseline" in stages.show_run.calls[0]


def test_pipeline_stop_after_halts_where_told(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)

    _, summary = golden_pipeline.run(_pipeline_args(tmp_path, stop_after="calibrate"), stages)

    assert stages.provision_golden_dataset.calls == []
    assert summary["calibrated"]


def test_pipeline_main_requires_exactly_one_document_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = ["--dataset", "ds", "--org", "o", "--action", "a", "--version", "1.0.0"]

    neither = golden_pipeline.main(common)
    both = golden_pipeline.main([*common, "--zip", "c.zip", "--document-dir", "."])

    assert (neither, both) == (2, 2)
    assert capsys.readouterr().err.count("exactly one") == 2


# ── #8 the per-file scenario (pin_document / verify_document) ─────────
#
# "I have a file already validated against the current Action version. I
# change the LLM and create a new version. The file must still be valid."
# The pin is per document and nothing generalises across files.

pin_document = _load("pin_document")
verify_document = _load("verify_document")


def _pin_args(tmp_path: Path, document: Path | None, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "file": document,
        "zip_path": None,
        "document_dir": None,
        "all": False,
        "extract_to": None,
        "glob": batch.DEFAULT_DOCUMENT_PATTERNS,
        "max_documents": 200,
        "dataset": "ds",
        "org": "o",
        "action": "a",
        "version": "1.0.0",
        "store": tmp_path / "pins",
        "repin": False,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_pin_records_the_trusted_versions_reading_as_the_golden(tmp_path: Path) -> None:
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")
    provision = _FakeStage()

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _raw(), provision
    )

    assert exit_code == 0
    golden = json.loads(
        (tmp_path / "pins" / "goldens" / "a" / "1.0.0" / "inv-001.pdf.json").read_text()
    )
    assert list(golden) == ["inv-001.pdf"]
    assert golden["inv-001.pdf"]["fields"]["total"]["value"] == "87.48"
    # Every field the trusted version read is critical: values are compared,
    # and a change fails.
    assert golden["inv-001.pdf"]["fields"]["total"]["critical"] is True
    record = summary["pinned"][0]
    assert record["critical"] == record["fields"]
    # Provisioned as ONE item, by document_id.
    assert provision.calls[0] == [
        "--dataset", "ds", "--seed", "inv-001.pdf",
        "--golden-file",
        str(tmp_path / "pins" / "goldens" / "a" / "1.0.0" / "inv-001.pdf.json"),
        # provenance travels to the platform item too
        "--source-action", "a", "--source-version", "1.0.0",
    ]


def test_pin_gates_even_the_fields_the_trusted_version_read_as_empty(
    tmp_path: Path,
) -> None:
    """Every field is critical, including the empty ones: verification
    runs the `pinned-file` classifier, where empty-against-empty is a
    match, so criticality no longer traps -- and invented content on a
    field the trusted version left empty now FAILS."""
    document = tmp_path / "inv-002.pdf"
    document.write_bytes(b"%PDF")
    capture = _raw()
    capture["fields"]["po_number"] = {"value": "", "confidenceScore": 0.0}

    _, _ = pin_document.run(_pin_args(tmp_path, document), lambda path: capture, _FakeStage())

    fields = json.loads(
        (tmp_path / "pins" / "goldens" / "a" / "1.0.0" / "inv-002.pdf.json").read_text()
    )["inv-002.pdf"]["fields"]
    assert fields["po_number"]["value"] == ""
    assert fields["po_number"]["critical"] is True
    assert fields["total"]["critical"] is True


def test_pinning_a_second_file_does_not_touch_the_first(tmp_path: Path) -> None:
    """One golden per document; nothing is generalised across files."""
    store = tmp_path / "pins"
    for name, total in (("a.pdf", "10.00"), ("b.pdf", "20.00")):
        document = tmp_path / name
        document.write_bytes(b"%PDF")
        pin_document.run(
            _pin_args(tmp_path, document, store=store), lambda path, t=total: _raw(total=t),
            _FakeStage(),
        )

    versioned = store / "goldens" / "a" / "1.0.0"
    a = json.loads((versioned / "a.pdf.json").read_text())
    b = json.loads((versioned / "b.pdf.json").read_text())
    assert a["a.pdf"]["fields"]["total"]["value"] == "10.00"
    assert b["b.pdf"]["fields"]["total"]["value"] == "20.00"


def test_pin_refuses_to_respend_on_an_already_pinned_file(tmp_path: Path) -> None:
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF")
    pin_document.run(_pin_args(tmp_path, document), lambda path: _raw(), _FakeStage())

    def never(path: Path) -> dict[str, Any]:
        raise AssertionError("an already-pinned file must not be re-read without --repin")

    exit_code, summary = pin_document.run(_pin_args(tmp_path, document), never, _FakeStage())

    assert exit_code == 0
    assert summary["already_pinned"] is True
    assert summary["skipped"] == ["a.pdf"]


def test_pin_spends_nothing_without_yes(tmp_path: Path) -> None:
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF")

    def never(path: Path) -> dict[str, Any]:
        raise AssertionError("quota spent without --yes")

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document, yes=False), never, _FakeStage()
    )

    assert (exit_code, summary) == (2, {})


def test_pin_keeps_the_capture_when_drafting_fails(tmp_path: Path) -> None:
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF")

    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, document), lambda path: {"status": "SUCCEEDED"}, _FakeStage()
    )

    assert exit_code == 1
    assert (tmp_path / "pins" / "captures" / "a" / "1.0.0" / "a.pdf.raw.json").exists()


def _verify_args(tmp_path: Path, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "file": None,
        "all": False,
        "dataset": "ds",
        "version": "2.0.0",
        "org": None,
        "action": None,
        "store": tmp_path / "pins",
        "trusted_version": None,
        "run_name": None,
        "zip_path": None,
        "document_dir": None,
        "extract_to": None,
        "glob": batch.DEFAULT_DOCUMENT_PATTERNS,
        "allow_missing": False,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _RecordingRunEval:
    def __init__(self, gate: int = 0) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self._gate = gate

    def __call__(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append((args, kwargs))
        return self._gate


def _pinned(
    tmp_path: Path,
    *document_ids: str,
    document_dir: str | None = None,
    action: str = "a",
    version: str = "1.0.0",
    dataset: str = "ds",
) -> Path:
    """Build a pin store in the shipped layout:
    `goldens/<action>/<version>/<document>.json` + `_pins.json`."""
    goldens = tmp_path / "pins" / "goldens" / action / version
    goldens.mkdir(parents=True, exist_ok=True)
    for document_id in document_ids:
        (goldens / f"{document_id}.json").write_text(
            json.dumps({document_id: _golden_entry(document_id, total="1")})
        )
    (goldens / "_pins.json").write_text(
        json.dumps(
            {
                document_id: {
                    "document_dir": document_dir or str(tmp_path / "docs"),
                    "dataset": dataset,
                    "org": "o",
                }
                for document_id in document_ids
            }
        )
    )
    return goldens


def test_verify_narrows_the_run_to_the_one_pinned_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IDP_DOCUMENT_DIR", raising=False)
    _pinned(tmp_path, "inv-001.pdf", "inv-002.pdf")
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, file=Path("somewhere/inv-001.pdf")), run_eval
    )

    (action, version, _run, dataset, org, max_documents), kwargs = run_eval.calls[0]
    assert exit_code == 0
    assert (action, version, dataset, org) == ("a", "2.0.0", "ds", "o")
    assert kwargs["documents"] == ["inv-001.pdf"]  # not inv-002
    assert max_documents == 1
    # A pinned file is compared with the pinned-file classifier, never the
    # default regression one.
    assert kwargs["classifier"] == "pinned-file"
    # The pin recorded where the file lives, so this is not a manual step.
    assert os.environ["IDP_DOCUMENT_DIR"] == str(tmp_path / "docs")
    assert summary["trusted_version"] == "1.0.0"


def test_verify_all_runs_every_pinned_file_in_one_run(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf", "b.pdf")
    run_eval = _RecordingRunEval()

    verify_document.run(_verify_args(tmp_path, all=True), run_eval)

    assert run_eval.calls[0][1]["documents"] == ["a.pdf", "b.pdf"]


def test_verify_returns_the_gates_exit_code_unchanged(tmp_path: Path) -> None:
    """The gate is the contract (INV-08/CT-04): this wrapper adds
    convenience, never a second opinion."""
    _pinned(tmp_path, "a.pdf")

    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, file=Path("a.pdf")), _RecordingRunEval(gate=1)
    )

    assert exit_code == 1


def test_verify_refuses_a_file_that_was_never_pinned(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf")
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, file=Path("never-pinned.pdf")), run_eval
    )

    assert (exit_code, summary) == (2, {})
    assert run_eval.calls == []


def test_verify_refuses_pins_that_span_directories(tmp_path: Path) -> None:
    """`run_eval` resolves every document against ONE IDP_DOCUMENT_DIR, so
    this must fail before quota is spent, not as a containment abort
    mid-run."""
    store = tmp_path / "pins"
    store.mkdir(parents=True)
    (store / "ds.pins.json").write_text(
        json.dumps(
            {
                "a.pdf": {"document_dir": "/tmp/one", "trusted_version": "1.0.0",
                          "action": "a", "org": "o"},
                "b.pdf": {"document_dir": "/tmp/two", "trusted_version": "1.0.0",
                          "action": "a", "org": "o"},
            }
        )
    )
    run_eval = _RecordingRunEval()

    exit_code, _ = verify_document.run(_verify_args(tmp_path, all=True), run_eval)

    assert exit_code == 2
    assert run_eval.calls == []


def test_verify_spends_nothing_without_yes(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf")
    run_eval = _RecordingRunEval()

    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, file=Path("a.pdf"), yes=False), run_eval
    )

    assert (exit_code, run_eval.calls) == (2, [])


def test_verify_warns_when_asked_to_compare_the_trusted_version_with_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pinned(tmp_path, "a.pdf")

    verify_document.run(
        _verify_args(tmp_path, file=Path("a.pdf"), version="1.0.0"), _RecordingRunEval()
    )

    assert "compares the trusted version against itself" in capsys.readouterr().err


def test_pin_and_verify_agree_end_to_end_on_an_empty_field(tmp_path: Path) -> None:
    """The round trip the two pieces exist for, with the real classifier:
    pin a file whose trusted reading left `po_number` empty, then compare
    three candidate readings of it."""
    from idp_regression.classifier.registry import resolve

    document = tmp_path / "inv-003.pdf"
    document.write_bytes(b"%PDF")
    capture = _raw()
    capture["fields"]["po_number"] = {"value": "", "confidenceScore": 0.0}
    pin_document.run(_pin_args(tmp_path, document), lambda path: capture, _FakeStage())
    golden = json.loads(
        (tmp_path / "pins" / "goldens" / "a" / "1.0.0" / "inv-003.pdf.json").read_text()
    )["inv-003.pdf"]

    pinned = resolve("pinned-file")

    def gate_for(po_number: str, total: str = "87.48") -> str:
        actual = {
            "status": "SUCCEEDED",
            "fields": {
                "invoice_number": {"value": "INV-1001"},
                "total": {"value": total},
                "vendor_name": {"value": "Acme Office Supplies"},
                "po_number": {"value": po_number},
            },
            "tables": {"line_items": [{"sku": {"value": "SKU-1"}, "amount": {"value": "65.00"}}]},
        }
        return pinned.gate(pinned.classify(golden, cast("Any", actual)))

    # unchanged reading, including the empty field -> still valid
    assert gate_for("") == "PASS"
    # a value invented where the trusted version found none -> caught
    assert gate_for("PO-999") == "FAIL"
    # an ordinary changed value -> caught, as before
    assert gate_for("", total="99.99") == "FAIL"


def test_pin_all_from_a_zip_gives_each_file_its_own_golden(tmp_path: Path) -> None:
    """Ten files in, ten goldens out -- one per document, nothing shared
    or aggregated between them."""
    archive = _zip(
        tmp_path, {f"invoices/inv-{i:02d}.pdf": b"%PDF" for i in range(1, 11)}
    )
    provision = _FakeStage()
    args = _pin_args(tmp_path, None, zip_path=archive, all=True)

    exit_code, summary = pin_document.run(
        args, lambda document: _raw(total=f"{document.stem[-2:]}.00"), provision
    )

    assert exit_code == 0
    versioned = tmp_path / "pins" / "goldens" / "a" / "1.0.0"
    assert len(list(versioned.glob("*.pdf.json"))) == 10
    # Each file's golden is its own file, holding ITS OWN values.
    first = json.loads((versioned / "inv-01.pdf.json").read_text())
    last = json.loads((versioned / "inv-10.pdf.json").read_text())
    assert first["inv-01.pdf"]["fields"]["total"]["value"] == "01.00"
    assert last["inv-10.pdf"]["fields"]["total"]["value"] == "10.00"
    # One dataset item per document, each by its own document_id.
    assert len(provision.calls) == 10
    assert provision.calls[0][3] == "inv-01.pdf"
    # The unpack directory is what verification resolves against.
    assert summary["document_dir"] == str((tmp_path / "pins" / "documents").resolve())


def test_pin_all_keeps_what_it_paid_for_when_one_document_fails(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {f"inv-{i}.pdf": b"%PDF" for i in (1, 2, 3)})

    def flaky(document: Path) -> dict[str, Any]:
        if document.name == "inv-2.pdf":
            raise TimeoutError("poll budget exhausted")
        return _raw()

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, None, zip_path=archive, all=True), flaky, _FakeStage()
    )

    assert exit_code == 1
    assert [r["document_id"] for r in summary["failures"]] == ["inv-2.pdf"]
    versioned = tmp_path / "pins" / "goldens" / "a" / "1.0.0"
    assert sorted(p.name for p in versioned.glob("*.pdf.json")) == [
        "inv-1.pdf.json",
        "inv-3.pdf.json",
    ]


def test_pin_all_re_run_retries_only_what_failed(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {f"inv-{i}.pdf": b"%PDF" for i in (1, 2)})
    args = _pin_args(tmp_path, None, zip_path=archive, all=True)

    def first_pass(document: Path) -> dict[str, Any]:
        if document.name == "inv-2.pdf":
            raise TimeoutError("poll budget exhausted")
        return _raw()

    pin_document.run(args, first_pass, _FakeStage())

    read: list[str] = []

    def second_pass(document: Path) -> dict[str, Any]:
        read.append(document.name)
        return _raw()

    exit_code, _ = pin_document.run(args, second_pass, _FakeStage())

    assert exit_code == 0
    assert read == ["inv-2.pdf"]  # inv-1 was not re-paid for


def test_pin_all_spends_nothing_without_yes(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "b.pdf": b"%PDF"})

    def never(document: Path) -> dict[str, Any]:
        raise AssertionError("quota spent without --yes")

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, None, zip_path=archive, all=True, yes=False), never, _FakeStage()
    )

    assert (exit_code, summary) == (2, {})


def test_pin_main_requires_all_before_pinning_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pointing at a folder must never imply "and spend an extraction on
    every file in it"."""
    (tmp_path / "docs").mkdir()

    rc = pin_document.main(
        [
            "--document-dir", str(tmp_path / "docs"), "--dataset", "ds",
            "--org", "o", "--action", "a", "--version", "1.0.0",
        ]
    )

    assert rc == 2
    assert "Add --all to confirm" in capsys.readouterr().err


def test_pin_main_requires_exactly_one_source(capsys: pytest.CaptureFixture[str]) -> None:
    common = ["--dataset", "ds", "--org", "o", "--action", "a", "--version", "1.0.0"]

    neither = pin_document.main(common)
    both = pin_document.main([*common, "--file", "a.pdf", "--zip", "c.zip", "--all"])

    assert (neither, both) == (2, 2)
    assert capsys.readouterr().err.count("exactly one of --file") == 2


def test_verify_can_take_the_documents_from_a_new_zip(tmp_path: Path) -> None:
    """The files arrive again in a fresh archive -- a new checkout, another
    machine. The golden is still the source of truth; the archive only
    supplies the bytes to re-extract."""
    _pinned(tmp_path, "a.pdf", "b.pdf", document_dir="/gone/since/pinning")
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "b.pdf": b"%PDF"})
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, all=True, zip_path=archive), run_eval
    )

    assert exit_code == 0
    assert run_eval.calls[0][1]["documents"] == ["a.pdf", "b.pdf"]
    # The pinned-but-now-wrong path is NOT what the run resolves against.
    unpacked_to = (tmp_path / "pins" / "verify-documents").resolve()
    assert os.environ["IDP_DOCUMENT_DIR"] == str(unpacked_to)
    assert summary["document_dir"] != "/gone/since/pinning"


def test_verify_refuses_a_partial_validation_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pinned document missing from the archive means some files were
    never checked -- and exit 0 would read as 'every pinned file is still
    valid'."""
    _pinned(tmp_path, "a.pdf", "b.pdf", "c.pdf")
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, all=True, zip_path=archive), run_eval
    )

    err = capsys.readouterr().err
    assert (exit_code, summary) == (2, {})
    assert run_eval.calls == []  # refused before spending anything
    assert "pinned but missing: b.pdf" in err
    assert "PARTIAL validation" in err


def test_verify_allow_missing_checks_what_is_there(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf", "b.pdf")
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    run_eval = _RecordingRunEval()

    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, all=True, zip_path=archive, allow_missing=True), run_eval
    )

    assert exit_code == 0
    assert run_eval.calls[0][1]["documents"] == ["a.pdf"]


def test_verify_never_pins_a_document_the_archive_adds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file with no golden is reported and skipped. Creating one during
    a validation run would mean validating against something the run just
    invented."""
    _pinned(tmp_path, "a.pdf")
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "brand-new.pdf": b"%PDF"})
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, all=True, zip_path=archive), run_eval
    )

    assert exit_code == 0
    assert run_eval.calls[0][1]["documents"] == ["a.pdf"]
    assert summary["unpinned"] == ["brand-new.pdf"]
    assert "no golden, ignored: brand-new.pdf" in capsys.readouterr().err
    # ...and no golden was written for it (the pinned one is untouched).
    goldens = tmp_path / "pins" / "goldens" / "a" / "1.0.0"
    assert sorted(p.name for p in goldens.glob("*.pdf.json")) == ["a.pdf.json"]


def test_verify_can_take_the_documents_from_a_directory(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf", document_dir="/gone/since/pinning")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.pdf").write_bytes(b"%PDF")
    run_eval = _RecordingRunEval()

    exit_code, summary = verify_document.run(
        _verify_args(tmp_path, all=True, document_dir=elsewhere), run_eval
    )

    assert exit_code == 0
    assert summary["document_dir"] == str(elsewhere.resolve())


def test_verify_without_a_source_still_uses_the_pinned_path(tmp_path: Path) -> None:
    """Unchanged default: no --zip/--document-dir means the pins' own
    recorded directory, exactly as before."""
    _pinned(tmp_path, "a.pdf")
    run_eval = _RecordingRunEval()

    _, summary = verify_document.run(_verify_args(tmp_path, all=True), run_eval)

    assert summary["document_dir"] == str(tmp_path / "docs")


# ── #9 the two halves as one command (compare_versions.py) ────────────

compare_versions = _load("compare_versions")


def _compare_args(tmp_path: Path, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "zip_path": None,
        "document_dir": None,
        "dataset": "ds",
        "org": "o",
        "action": "a",
        "trusted_version": "1.0.0",
        "candidate_version": "2.0.0",
        "store": tmp_path / "pins",
        "extract_to": None,
        "glob": batch.DEFAULT_DOCUMENT_PATTERNS,
        "max_documents": 200,
        "repin": False,
        "allow_partial": False,
        "run_name": None,
        "plan": False,
        "yes": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_compare_pins_at_the_trusted_version_then_verifies_the_candidate(
    tmp_path: Path,
) -> None:
    archive = _zip(tmp_path, {f"inv-{i}.pdf": b"%PDF" for i in (1, 2, 3)})
    pin, verify = _FakeStage(), _FakeStage()

    exit_code, summary = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive), pin, verify
    )

    assert exit_code == 0
    pin_argv, verify_argv = pin.calls[0], verify.calls[0]
    # The two versions land on the right halves -- the one mix-up this
    # command exists to make impossible.
    assert pin_argv[pin_argv.index("--version") + 1] == "1.0.0"
    assert verify_argv[verify_argv.index("--version") + 1] == "2.0.0"
    assert "--all" in pin_argv and "--all" in verify_argv
    assert summary["documents"] == 3


def test_compare_unpacks_once_and_points_both_halves_at_it(tmp_path: Path) -> None:
    """The whole comparison rests on both halves seeing the SAME bytes.
    Two separate invocations would each unpack their own copy with
    nothing checking they matched."""
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    pin, verify = _FakeStage(), _FakeStage()

    _, summary = compare_versions.run(_compare_args(tmp_path, zip_path=archive), pin, verify)

    pin_dir = pin.calls[0][pin.calls[0].index("--document-dir") + 1]
    verify_dir = verify.calls[0][verify.calls[0].index("--document-dir") + 1]
    assert pin_dir == verify_dir == summary["document_dir"]
    assert "--zip" not in pin.calls[0] and "--zip" not in verify.calls[0]


def test_compare_asks_once_for_both_halves_quota(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _zip(tmp_path, {f"inv-{i}.pdf": b"%PDF" for i in range(5)})
    pin, verify = _FakeStage(), _FakeStage()

    exit_code, summary = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, plan=True, yes=False), pin, verify
    )

    err = capsys.readouterr().err
    assert (exit_code, summary) == (0, {})
    assert "10 real IDP extraction(s) to be spent" in err  # 5 documents x 2 halves
    assert pin.calls == [] and verify.calls == []


def test_compare_spends_nothing_without_yes(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    pin, verify = _FakeStage(), _FakeStage()

    exit_code, summary = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, yes=False), pin, verify
    )

    assert (exit_code, summary) == (2, {})
    assert pin.calls == [] and verify.calls == []


def test_compare_refuses_to_verify_after_a_partial_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file that failed to pin has no golden, so verification would
    report it as 'no golden, ignored' and exit 0 on the rest."""
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "b.pdf": b"%PDF"})
    pin, verify = _FakeStage(exit_code=1), _FakeStage()

    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive), pin, verify
    )

    assert exit_code == 1
    assert verify.calls == []
    assert "NO golden" in capsys.readouterr().err


def test_compare_allow_partial_verifies_what_pinned(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF", "b.pdf": b"%PDF"})
    pin, verify = _FakeStage(exit_code=1), _FakeStage()

    compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, allow_partial=True), pin, verify
    )

    assert verify.calls and "--allow-missing" in verify.calls[0]


def test_compare_returns_the_gates_exit_code(tmp_path: Path) -> None:
    """The verdict is the gate's, not this wrapper's (INV-08/CT-04)."""
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    pin, verify = _FakeStage(), _FakeStage(exit_code=1)

    exit_code, summary = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive), pin, verify
    )

    assert exit_code == 1
    assert summary["gate_exit_code"] == 1


def test_compare_warns_when_both_versions_are_the_same(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})

    compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, candidate_version="1.0.0"),
        _FakeStage(),
        _FakeStage(),
    )

    err = capsys.readouterr().err
    assert "compares a version against ITSELF" in err
    assert "noise_floor.py" in err


def test_compare_main_requires_exactly_one_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = [
        "--dataset", "ds", "--org", "o", "--action", "a",
        "--trusted-version", "1.0.0", "--candidate-version", "2.0.0",
    ]

    neither = compare_versions.main(common)
    both = compare_versions.main([*common, "--zip", "c.zip", "--document-dir", "."])

    assert (neither, both) == (2, 2)
    assert capsys.readouterr().err.count("exactly one") == 2


# ── the store layout carries the relationship ─────────────────────────


def test_the_same_document_can_be_pinned_at_two_versions(tmp_path: Path) -> None:
    """The point of `goldens/<action>/<version>/<document>.json`: two
    versions of the same action produce two goldens for one document, and
    neither overwrites the other -- on disk AND on the platform, which is
    why each version goes to its own dataset (DEBT-89)."""
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")

    pin_document.run(
        _pin_args(tmp_path, document, version="1.0.0", dataset="ds-v1"),
        lambda path: _raw(total="10.00"),
        _FakeStage(),
    )
    pin_document.run(
        _pin_args(tmp_path, document, version="2.0.0", dataset="ds-v2"),
        lambda path: _raw(total="20.00"),
        _FakeStage(),
    )

    goldens = tmp_path / "pins" / "goldens" / "a"
    v1 = json.loads((goldens / "1.0.0" / "inv-001.pdf.json").read_text())
    v2 = json.loads((goldens / "2.0.0" / "inv-001.pdf.json").read_text())
    assert v1["inv-001.pdf"]["fields"]["total"]["value"] == "10.00"
    assert v2["inv-001.pdf"]["fields"]["total"]["value"] == "20.00"
    # Captures sit on the same axis -- a raw response only means anything
    # against the version that produced it.
    assert (tmp_path / "pins" / "captures" / "a" / "1.0.0" / "inv-001.pdf.raw.json").exists()
    assert (tmp_path / "pins" / "captures" / "a" / "2.0.0" / "inv-001.pdf.raw.json").exists()


def test_verify_refuses_to_guess_between_two_pinned_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Guessing would verify against a different version's goldens than
    the operator meant, and the run would look entirely normal."""
    _pinned(tmp_path, "a.pdf", version="1.0.0")
    _pinned(tmp_path, "a.pdf", version="2.0.0")
    run_eval = _RecordingRunEval()

    exit_code, _ = verify_document.run(_verify_args(tmp_path, all=True), run_eval)

    err = capsys.readouterr().err
    assert exit_code == 2
    assert run_eval.calls == []
    assert "a/1.0.0" in err and "a/2.0.0" in err


def test_verify_takes_the_named_pin_set(tmp_path: Path) -> None:
    _pinned(tmp_path, "a.pdf", version="1.0.0", dataset="ds-v1")
    _pinned(tmp_path, "a.pdf", "b.pdf", version="2.0.0")
    run_eval = _RecordingRunEval()

    _, summary = verify_document.run(
        _verify_args(tmp_path, all=True, action="a", trusted_version="2.0.0"), run_eval
    )

    assert summary["trusted_version"] == "2.0.0"
    assert run_eval.calls[0][1]["documents"] == ["a.pdf", "b.pdf"]


def test_verify_names_the_legacy_layout_rather_than_saying_nothing_is_pinned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store written before 2026-09-25 has its goldens in one flat
    `<dataset>.golden.json`. Saying "nothing is pinned" would send the
    operator to re-pin without knowing why."""
    (tmp_path / "pins").mkdir()
    (tmp_path / "pins" / "ds.golden.json").write_text("{}")

    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, all=True), _RecordingRunEval()
    )

    assert exit_code == 2
    assert "pre-2026-09-25 flat layout" in capsys.readouterr().err


def test_pin_refuses_a_path_unsafe_action_or_version(tmp_path: Path) -> None:
    """`--action`/`--version` land in a filesystem path, so they are
    validated rather than trusted."""
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF")

    def never(path: Path) -> dict[str, Any]:
        raise AssertionError("must not extract with an unsafe path component")

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document, version="../../escaped"), never, _FakeStage()
    )

    assert (exit_code, summary) == (2, {})


# --- DEBT-91: show_run must not report an aborted run as a pass ------------


def _write_envelope(tmp_path: Path, envelope: dict[str, Any]) -> Path:
    artifact_dir = tmp_path / ".idp-regression-run-artifacts"
    artifact_dir.mkdir(exist_ok=True)
    path = artifact_dir / "runid.json"
    path.write_text(json.dumps(envelope))
    return path


@pytest.mark.parametrize(
    ("documents", "status"),
    [
        ({}, "aborted"),  # aborted before the first document
        ({"a.pdf": {"total": _cell("match")}}, "aborted"),  # aborted after a passing one
    ],
)
def test_show_run_never_reports_an_aborted_run_as_a_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    documents: dict[str, Any],
    status: Literal["complete", "aborted"],
) -> None:
    path = _write_envelope(
        tmp_path,
        artifact_envelope("runid", documents, status=status, abort_reason="auth_failure"),
    )
    rc = show_run.main([str(path)])
    out = capsys.readouterr().out
    assert rc == 1, "run_eval exited non-zero on this run; show_run must not say otherwise"
    assert "OVERALL  INCOMPLETE" in out
    assert "ABORTED (auth_failure)" in out
    assert "OVERALL  PASS" not in out


def test_show_run_cannot_vouch_for_a_legacy_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_envelope(tmp_path, {"runid": {"a.pdf": {"total": _cell("match")}}})
    assert show_run.main([str(path)]) == 1
    assert "OVERALL  INCOMPLETE" in capsys.readouterr().out


def test_show_run_still_passes_a_complete_passing_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_envelope(
        tmp_path,
        artifact_envelope("runid", {"a.pdf": {"total": _cell("match")}}, status="complete"),
    )
    assert show_run.main([str(path)]) == 0
    assert "OVERALL  PASS" in capsys.readouterr().out


# --- DEBT-86 / DEBT-114: a corpus over its ceiling is REFUSED, never truncated ---


def _record(calls: list[Path], path: Path) -> dict[str, Any]:
    calls.append(path)
    return _raw()


def test_compare_refuses_a_zip_larger_than_its_ceiling_before_spending(tmp_path: Path) -> None:
    """The primary use case once measured the first four of ten documents,
    printed STILL VALID and exited 0. Six were never read."""
    archive = _zip(tmp_path, {f"x{i:02d}.pdf": b"%PDF" for i in range(10)})
    pin, verify = _FakeStage(), _FakeStage()

    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, max_documents=4), pin, verify
    )

    assert exit_code == 2
    assert pin.calls == [] and verify.calls == [], "nothing may be spent on a truncated corpus"


def test_compare_refuses_a_directory_larger_than_its_ceiling(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 5)
    pin, verify = _FakeStage(), _FakeStage()
    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, document_dir=document_dir, max_documents=4), pin, verify
    )
    assert exit_code == 2 and pin.calls == []


def test_compare_at_exactly_the_ceiling_still_runs(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 4)
    pin, verify = _FakeStage(), _FakeStage()
    exit_code, summary = compare_versions.run(
        _compare_args(tmp_path, document_dir=document_dir, max_documents=4), pin, verify
    )
    assert exit_code == 0 and summary["documents"] == 4


def test_pin_refuses_a_batch_larger_than_its_ceiling(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 3)
    calls: list[Path] = []
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, None, document_dir=document_dir, all=True, max_documents=2),
        lambda path: _record(calls, path),
        _FakeStage(),
    )
    assert exit_code == 2 and calls == []


def test_bootstrap_refuses_over_the_ceiling_even_with_resume(tmp_path: Path) -> None:
    """DEBT-114: capping BEFORE the resume filter made --resume re-select
    the same first N forever and report "0 to go"."""
    document_dir = _documents(tmp_path, 3)
    calls: list[Path] = []
    for resume in (False, True):
        exit_code, _ = bootstrap.run(
            _bootstrap_args(tmp_path, document_dir, max_documents=2, resume=resume),
            lambda document: _record(calls, document),
        )
        assert exit_code == 2
    assert calls == []


def test_pipeline_refuses_over_the_ceiling_before_any_stage(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    for i in range(3):
        (tmp_path / "docs" / f"doc-{i}.pdf").write_bytes(b"%PDF")
    stages = _pipeline_stages(tmp_path)
    exit_code, _ = golden_pipeline.run(_pipeline_args(tmp_path, max_documents=2), stages)
    assert exit_code == 2
    assert stages.noise_floor.calls == [] and stages.bootstrap_golden_set.calls == []


# --- DEBT-87: flattening may never overwrite one document with another ------


def _unpacked_contents(paths: list[Path]) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in paths}


def test_a_folded_name_is_never_overwritten_by_a_real_entry_of_that_name(
    tmp_path: Path,
) -> None:
    """`b/x.pdf` folds to `b__x.pdf`; a real `b__x.pdf` entry then wrote
    over it and the returned list still counted both."""
    archive = _zip(
        tmp_path, {"a/x.pdf": b"AAA", "b/x.pdf": b"BBB", "b__x.pdf": b"CCC"}
    )
    with pytest.raises(batch.ZipRejectedError, match="overwriting"):
        batch.extract_documents_from_zip(archive, tmp_path / "out")
    with pytest.raises(batch.ZipRejectedError, match="overwriting"):
        batch.extract_documents_from_zip(archive, tmp_path / "plan", dry_run=True)


def test_names_differing_only_in_case_are_kept_apart(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"p/Inv.pdf": b"UPPER", "q/inv.pdf": b"lower"})
    paths, _ = batch.extract_documents_from_zip(archive, tmp_path / "out")
    contents = _unpacked_contents(paths)
    assert sorted(contents.values()) == [b"UPPER", b"lower"], "both documents survive"
    assert len({p.name.casefold() for p in paths}) == 2


def test_a_case_collision_in_one_folder_falls_back_to_the_folded_name(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a/Inv.pdf": b"UPPER", "a/inv.pdf": b"lower"})
    paths, _ = batch.extract_documents_from_zip(archive, tmp_path / "out")
    assert sorted(_unpacked_contents(paths).values()) == [b"UPPER", b"lower"]


def test_unicode_forms_of_one_name_are_the_same_name(tmp_path: Path) -> None:
    nfc, nfd = "fatura-é.pdf", "fatura-é.pdf"
    archive = _zip(tmp_path, {f"a/{nfc}": b"NFC", f"b/{nfd}": b"NFD"})
    paths, _ = batch.extract_documents_from_zip(archive, tmp_path / "out")
    assert sorted(_unpacked_contents(paths).values()) == [b"NFC", b"NFD"]


def test_every_returned_path_is_a_distinct_file_on_disk(tmp_path: Path) -> None:
    """The invariant behind all of the above: the list a caller prices and
    reports on is exactly the set of files that exist."""
    archive = _zip(
        tmp_path,
        {"x/a.pdf": b"1", "y/a.pdf": b"2", "z/a.pdf": b"3", "a.pdf": b"4", "x/b.pdf": b"5"},
    )
    paths, _ = batch.extract_documents_from_zip(archive, tmp_path / "out")
    on_disk = sorted((tmp_path / "out").iterdir())
    assert len(paths) == len(set(paths)) == len(on_disk) == 5
    assert sorted(p.read_bytes() for p in on_disk) == [b"1", b"2", b"3", b"4", b"5"]


# --- DEBT-88: empty-on-every-pass is agreement, not instability -------------


def _with_field(value: str, name: str = "po_number") -> dict[str, Any]:
    out = _normalized()
    out["fields"][name] = {"value": value, "confidence": 0.99}
    return out


def test_noise_floor_counts_a_field_empty_on_every_pass_as_stable(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)
    adapter = _ScriptedAdapter(
        {
            "doc-001.pdf": [_with_field(""), _with_field("")],
            "doc-002.pdf": [_with_field(""), _with_field("")],
        }
    )
    _, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)
    po = report["by_field"]["po_number"]
    assert po["instability_rate"] == 0.0, "the extractor read nothing, twice: that is agreement"
    assert (po["observations"], po["unstable"]) == (2, 0)
    assert report["summary"]["field_instability_rate"] == 0.0


def test_noise_floor_still_counts_invented_content_as_unstable(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_with_field(""), _with_field("PO-7")]})
    _, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)
    assert report["by_field"]["po_number"]["instability_rate"] == 1.0
    assert report["by_field"]["po_number"]["verdicts"] == {"wrong_value": 1}


# --- DEBT-90 / DEBT-93: spend what was priced, measure what was sent --------


def _pins_file(tmp_path: Path, records: dict[str, dict[str, str]]) -> None:
    goldens = tmp_path / "pins" / "goldens" / "a" / "1.0.0"
    goldens.mkdir(parents=True, exist_ok=True)
    for name in records:
        (goldens / f"{name}.json").write_text(json.dumps({name: _golden_entry(name, total="1")}))
    (goldens / "_pins.json").write_text(json.dumps(records))


def test_compare_unpacks_each_archive_into_its_own_directory(tmp_path: Path) -> None:
    """A plan of 4 once spent 9: every archive shared `<store>/documents/`,
    and the pin half, pointed at the directory, pinned all of them."""
    first = _zip(tmp_path, {"a1.pdf": b"%PDF-1", "a2.pdf": b"%PDF-2"}, name="first.zip")
    second = _zip(tmp_path, {"b1.pdf": b"%PDF-3"}, name="second.zip")
    dirs = []
    for archive in (first, second):
        pin = _FakeStage()
        exit_code, summary = compare_versions.run(
            _compare_args(tmp_path, zip_path=archive, dataset=archive.stem), pin, _FakeStage()
        )
        assert exit_code == 0
        dirs.append(Path(summary["document_dir"]))
    assert dirs[0] != dirs[1]
    assert sorted(p.name for p in dirs[1].iterdir()) == ["b1.pdf"]


def test_compare_refuses_an_extract_to_that_already_holds_other_documents(
    tmp_path: Path,
) -> None:
    target = tmp_path / "unpack"
    target.mkdir()
    (target / "stale.pdf").write_bytes(b"%PDF-old")
    archive = _zip(tmp_path, {"new.pdf": b"%PDF-new"})
    pin = _FakeStage()
    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, extract_to=target), pin, _FakeStage()
    )
    assert exit_code == 2 and pin.calls == []


def test_compare_refuses_when_the_dataset_already_holds_pins_of_another_corpus(
    tmp_path: Path,
) -> None:
    _pins_file(tmp_path, {"old.pdf": {"dataset": "ds", "document_dir": "/elsewhere"}})
    archive = _zip(tmp_path, {"new.pdf": b"%PDF"})
    pin, verify = _FakeStage(), _FakeStage()
    exit_code, _ = compare_versions.run(_compare_args(tmp_path, zip_path=archive), pin, verify)
    assert exit_code == 2
    assert pin.calls == [] and verify.calls == [], "refused before the pin half spends"


def test_compare_refuses_a_document_already_pinned_for_another_dataset(tmp_path: Path) -> None:
    _pins_file(tmp_path, {"new.pdf": {"dataset": "other", "document_dir": "/elsewhere"}})
    archive = _zip(tmp_path, {"new.pdf": b"%PDF"})
    pin = _FakeStage()
    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive), pin, _FakeStage()
    )
    assert exit_code == 2 and pin.calls == []
    # --repin re-reads it into THIS dataset, so it is no longer a conflict.
    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, repin=True), pin, _FakeStage()
    )
    assert exit_code == 0


def test_verify_all_selects_only_this_datasets_pins(tmp_path: Path) -> None:
    docs = str(tmp_path / "docs")
    _pins_file(
        tmp_path,
        {
            "mine.pdf": {"dataset": "ds", "document_dir": docs, "org": "o"},
            "theirs.pdf": {"dataset": "other", "document_dir": docs, "org": "o"},
        },
    )
    run_eval = _RecordingRunEval()
    exit_code, _ = verify_document.run(_verify_args(tmp_path, all=True), run_eval)
    assert exit_code == 0
    (_, kwargs), = run_eval.calls
    assert kwargs["documents"] == ["mine.pdf"]


def test_noise_floor_samples_only_the_archive_it_unpacked(tmp_path: Path) -> None:
    """A plan of 2 once spent 14: the shared unpack directory still held
    earlier corpora, and the run re-listed it."""
    shared = tmp_path / "shared"
    shared.mkdir()
    for i in range(6):
        (shared / f"old-{i}.pdf").write_bytes(b"%PDF-old")
    archive = _zip(tmp_path, {"doc-001.pdf": b"%PDF"})
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized()]})
    _, report = noise_floor.run(
        _noise_args(tmp_path, tmp_path / "unused", zip_path=archive, extract_to=shared),
        adapter,
    )
    assert adapter.calls == 2, "exactly the priced 1 document x 2 repeats"
    assert report["summary"]["documents"] == 1


# --- DEBT-89: one (dataset, document) is pinned at one version --------------


def test_pinning_a_document_again_into_the_same_dataset_at_another_version_is_refused(
    tmp_path: Path,
) -> None:
    """The platform item id is `uuid5(dataset|document_id)` -- no version --
    so the second pin would overwrite the first ON THE PLATFORM while both
    survived on disk, and a verify "against 1.0.0" would compare against
    2.0.0's reading."""
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")
    pin_document.run(
        _pin_args(tmp_path, document, version="1.0.0"), lambda path: _raw(), _FakeStage()
    )
    calls: list[Path] = []
    provision = _FakeStage()
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, document, version="2.0.0"),
        lambda path: _record(calls, path),
        provision,
    )
    assert exit_code == 2
    assert calls == [] and provision.calls == [], "refused before any extraction"


def test_verify_refuses_a_pin_set_whose_documents_are_pinned_at_another_version_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store already in that state (written before the pin-time check)
    must not verify: the platform item is whichever was pinned last."""
    _pinned(tmp_path, "a.pdf", version="1.0.0")
    _pinned(tmp_path, "a.pdf", version="2.0.0")
    run_eval = _RecordingRunEval()
    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, all=True, action="a", trusted_version="1.0.0", version="3.0.0"),
        run_eval,
    )
    assert exit_code == 2 and run_eval.calls == []
    assert "DEBT-89" in capsys.readouterr().err


# --- DEBT-92: calibration never writes an entry the schema rejects ----------



def test_calibrate_keeps_a_value_out_of_a_type_it_cannot_satisfy(tmp_path: Path) -> None:
    """Majority `number`, but one document drafted the field empty and one
    as `1,250.00` text. Forcing `number` on them fails the committed
    schema, and provisioning then left a partial dataset."""
    golden: dict[str, Any] = {}
    for i in range(18):
        golden[f"d{i}"] = _entry(f"d{i}.pdf")
    blank = _entry("blank.pdf")
    blank["fields"]["total"] = {"value": "", "type": "text", "critical": False}
    comma = _entry("comma.pdf")
    comma["fields"]["total"] = {"value": "1,250.00", "type": "text", "critical": True}
    golden["blank"], golden["comma"] = blank, comma

    calibrated, report = calibrate_golden.calibrate(golden, None)

    for key, entry in calibrated.items():
        assert provision._validation_error(entry) is None, key
    assert calibrated["d0"]["fields"]["total"]["type"] == "number"
    assert calibrated["blank"]["fields"]["total"]["type"] == "text"
    assert calibrated["comma"]["fields"]["total"]["type"] == "text"
    assert sorted(report["types"]["total"]["kept_drafted"]) == ["blank", "comma"]


# --- /test gate F-1..F-5 (Lane G High, 2026-09-27): each kills a survivor ---


def test_pin_refuses_a_zip_larger_than_its_ceiling(tmp_path: Path) -> None:
    """F-1 / M19: the --zip leg, not only --document-dir."""
    archive = _zip(tmp_path, {f"x{i}.pdf": b"%PDF" for i in range(3)})
    calls: list[Path] = []
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, None, zip_path=archive, all=True, max_documents=2),
        lambda path: _record(calls, path),
        _FakeStage(),
    )
    assert exit_code == 2 and calls == []


def test_noise_floor_counts_a_field_that_drops_out_as_unstable(tmp_path: Path) -> None:
    """F-2 / M31: value on the first pass, empty on the repeat is the
    extractor forgetting a field -- instability calibration must see."""
    document_dir = _documents(tmp_path, 1)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_with_field("PO-7"), _with_field("")]})
    _, report = noise_floor.run(_noise_args(tmp_path, document_dir), adapter)
    assert report["by_field"]["po_number"]["instability_rate"] == 1.0


def test_pinned_elsewhere_sees_another_action_too(tmp_path: Path) -> None:
    """F-4 / M43: the platform item id carries neither version nor action."""
    _pinned(tmp_path, "a.pdf", action="other-action", version="9.9.9")
    own = tmp_path / "pins" / "goldens" / "a" / "1.0.0"
    assert batch.pinned_elsewhere(tmp_path / "pins", "ds", {"a.pdf"}, own) == {
        "a.pdf": "other-action/9.9.9"
    }


def test_compare_admits_a_second_corpus_under_a_second_dataset(tmp_path: Path) -> None:
    """F-5 / M37: another dataset's pins in the same store are not a conflict."""
    _pins_file(tmp_path, {"old.pdf": {"dataset": "first", "document_dir": "/elsewhere"}})
    archive = _zip(tmp_path, {"new.pdf": b"%PDF"})
    pin = _FakeStage()
    exit_code, _ = compare_versions.run(
        _compare_args(tmp_path, zip_path=archive, dataset="second"), pin, _FakeStage()
    )
    assert exit_code == 0 and pin.calls


# --- DEBT-94: a partial draft never reaches calibrate / provision -----------


def test_pipeline_stops_on_a_partial_draft_that_still_wrote_a_golden(tmp_path: Path) -> None:
    """The real shape: bootstrap flushes after every document, so rc 1
    ALWAYS leaves a golden behind. The older test's fake wrote none."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF")
    partial = _FakeStage(
        exit_code=1,
        writes={tmp_path / "work" / "golden.json": {"d0": _golden_entry("d0.pdf", total="1")}},
    )
    stages = _pipeline_stages(tmp_path, bootstrap_golden_set=partial)

    exit_code, summary = golden_pipeline.run(_pipeline_args(tmp_path), stages)

    assert exit_code == 1
    assert summary["draft_exit_code"] == 1
    assert stages.calibrate_golden.calls == []
    assert stages.provision_golden_dataset.calls == []
    assert stages.run_eval_stage.calls == []


def test_bootstrap_resume_redrafts_a_failed_draft_from_its_capture_for_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_dir = _documents(tmp_path, 2)
    real_draft = bootstrap._draft_one

    def _flaky(capture: Any, document_id: str) -> Any:
        if document_id == "doc-002.pdf":
            raise ValueError("draft failed")
        return real_draft(capture, document_id)

    monkeypatch.setattr(bootstrap, "_draft_one", _flaky)
    exit_code, _ = bootstrap.run(_bootstrap_args(tmp_path, document_dir), lambda d: _raw())
    assert exit_code == 1

    monkeypatch.setattr(bootstrap, "_draft_one", real_draft)
    calls: list[Path] = []
    args = _bootstrap_args(tmp_path, document_dir, resume=True)
    exit_code, summary = bootstrap.run(args, lambda d: _record(calls, d))

    assert exit_code == 0
    assert calls == [], "the capture was already paid for"
    assert sorted(json.loads(args.out.read_text())) == ["doc-001", "doc-002"]
    assert summary["failures"] == []


# --- DEBT-95: calibration only ever demotes ---------------------------------


def test_calibrate_never_marks_a_documents_empty_value_critical() -> None:
    """`total` is stable and present corpus-wide, so it stays critical --
    but not on the one document that read it empty: a critical empty
    expected fails every run under the default classifier."""
    golden: dict[str, Any] = {f"d{i}": _entry(f"d{i}.pdf") for i in range(19)}
    blank = _entry("blank.pdf")
    blank["fields"]["total"] = {"value": "", "type": "number", "critical": False}
    golden["blank"] = blank

    calibrated, _ = calibrate_golden.calibrate(golden, None)

    assert calibrated["d0"]["fields"]["total"]["critical"] is True
    assert calibrated["blank"]["fields"]["total"]["critical"] is False


def test_calibrate_keeps_a_human_demotion_on_re_run() -> None:
    golden: dict[str, Any] = {f"d{i}": _entry(f"d{i}.pdf") for i in range(20)}
    golden["d3"]["fields"]["invoice_number"]["critical"] = False  # reconciled by hand

    calibrated, _ = calibrate_golden.calibrate(golden, None)

    assert calibrated["d3"]["fields"]["invoice_number"]["critical"] is False
    assert calibrated["d4"]["fields"]["invoice_number"]["critical"] is True


def test_calibrate_keeps_a_human_demotion_of_a_table() -> None:
    golden: dict[str, Any] = {}
    for i in range(10):
        entry = _golden_entry(f"d{i}.pdf", total="1")
        entry["tables"] = {
            "line_items": {"match_key": "sku", "critical": True, "rows": [{"sku": f"S{i}"}]}
        }
        golden[f"d{i}"] = entry
    golden["d2"]["tables"]["line_items"]["critical"] = False  # reconciled by hand

    calibrated, _ = calibrate_golden.calibrate(golden, None)

    assert calibrated["d2"]["tables"]["line_items"]["critical"] is False
    assert calibrated["d3"]["tables"]["line_items"]["critical"] is True


# --- DEBT-96: show_run sees a table failure ---------------------------------


def _table(verdict: str, *, critical: bool = True) -> dict[str, Any]:
    return {
        "verdict": "detail",
        "critical": critical,
        "rows": [
            {"match_key": "A", "column": "amount", "verdict": verdict,
             "expected": "1.00", "actual": "2.00" if verdict != "match" else "1.00"},
        ],
    }


def test_show_run_never_calls_a_table_regression_noise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """d0 fails on `line_items.amount` (floor 0% -> ABOVE) and on `total`
    (within the floor). The table failure is real, so d0 must NOT be named
    as resting entirely on noise."""
    documents = {
        f"doc-{i:03d}.pdf": {
            "total": _cell("wrong_value" if i <= 4 else "match"),
            "line_items": _table("wrong_value" if i == 1 else "match"),
        }
        for i in range(1, 31)
    }
    artifact = _artifact(tmp_path, documents)
    floor = _floor_report(
        tmp_path,
        {
            "total": {"observations": 50, "unstable": 7, "instability_rate": 0.14},
            "line_items.amount": {"observations": 50, "unstable": 0, "instability_rate": 0.0},
        },
    )

    rc = show_run.main([str(artifact), "--baseline", str(floor)])

    out = capsys.readouterr().out
    assert rc == 1
    noise_block = out.split("rest ENTIRELY", 1)[1] if "rest ENTIRELY" in out else ""
    assert "doc-001.pdf" not in noise_block
    assert "doc-002.pdf" in noise_block, "a total-only failure is still explained by the floor"
    assert "line_items[A].amount" in out and "FAILS THE GATE" in out


def test_a_non_critical_tables_difference_is_not_flagged_as_failing() -> None:
    fields = {"line_items": _table("wrong_value", critical=False)}
    assert not any(show_run._fails(cell) for _, cell in show_run._leaves(fields))


# --- DEBT-97: a failed provisioning is a failure, and a re-run retries it --


def test_a_failed_provisioning_is_reported_and_retried_without_re_extracting(
    tmp_path: Path,
) -> None:
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")
    calls: list[Path] = []

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _record(calls, path), _FakeStage(exit_code=1)
    )
    assert exit_code == 1
    assert summary["pinned"] == []
    assert [f["document_id"] for f in summary["failures"]] == ["inv-001.pdf"]

    provision = _FakeStage()
    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _record(calls, path), provision
    )
    assert exit_code == 0
    assert len(calls) == 1, "the retry re-provisions; it never re-extracts"
    assert len(provision.calls) == 1 and summary["reprovisioned"] == ["inv-001.pdf"]

    # Once recorded as landed, a third run touches nothing at all.
    again = _FakeStage()
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _record(calls, path), again
    )
    assert exit_code == 0 and again.calls == [] and len(calls) == 1


def test_a_failing_re_provisioning_keeps_the_run_red(tmp_path: Path) -> None:
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")
    pin_document.run(_pin_args(tmp_path, document), lambda path: _raw(), _FakeStage(exit_code=1))
    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _raw(), _FakeStage(exit_code=1)
    )
    assert exit_code == 1 and summary["failures"]


# --- DEBT-98: CHANGED means a document changed, nothing else ----------------


def _outcome_artifact(
    directory: Path, documents: dict[str, Any], status: Literal["complete", "aborted"]
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "r.json").write_text(
        json.dumps(artifact_envelope("r", documents, status=status, abort_reason="timeout"))
    )


def test_run_outcome_calls_only_a_recorded_failure_changed(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    assert batch.run_outcome(0, since=0.0, artifact_dir=arts)[0] == "STILL VALID"
    # A refusal before run_eval (rc 2), or an abort before any artifact.
    verdict, reason = batch.run_outcome(2, since=0.0, artifact_dir=arts)
    assert verdict == "RUN FAILED" and "before writing a result" in reason

    _outcome_artifact(arts, {"a.pdf": {"total": _cell("wrong_value")}}, "complete")
    assert batch.run_outcome(1, since=0.0, artifact_dir=arts)[0] == "CHANGED"

    _outcome_artifact(arts, {"a.pdf": {"total": _cell("match")}}, "aborted")
    verdict, reason = batch.run_outcome(1, since=0.0, artifact_dir=arts)
    assert verdict == "RUN FAILED" and "aborted (timeout)" in reason


def test_run_outcome_ignores_an_artifact_older_than_this_run(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    _outcome_artifact(arts, {"a.pdf": {"total": _cell("wrong_value")}}, "complete")
    later = (arts / "r.json").stat().st_mtime + 60
    assert batch.run_outcome(1, since=later, artifact_dir=arts)[0] == "RUN FAILED"


def test_run_outcome_names_an_abort_after_a_real_failure(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    _outcome_artifact(arts, {"a.pdf": {"total": _cell("wrong_value")}}, "aborted")
    verdict, reason = batch.run_outcome(1, since=0.0, artifact_dir=arts)
    assert verdict == "CHANGED" and "ABORTED (timeout)" in reason


# --- /test gate (Med-High wave) F-1..F-6 ------------------------------------


def _write_failing_artifact(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(
        json.dumps(
            artifact_envelope("run", {"a.pdf": {"total": _cell("wrong_value")}}, status="complete")
        )
    )


def test_verify_main_banner_follows_the_artifact_not_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-1 (DEBT-98's own symptom location): `main`'s wiring, including
    that `started` is stamped BEFORE the run writes its artifact."""
    from idp_regression.orchestration import dotenv_support, facade

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dotenv_support, "load_dotenv", lambda *a, **k: None)
    _pinned(tmp_path, "a.pdf")
    argv = ["--all", "--dataset", "ds", "--version", "2.0.0", "--store",
            str(tmp_path / "pins"), "--yes"]

    def _failing_run(*args: Any, **kwargs: Any) -> int:
        _write_failing_artifact(tmp_path / ".idp-regression-run-artifacts")
        return 1

    monkeypatch.setattr(facade, "run_eval", _failing_run)
    assert verify_document.main(argv) == 1
    assert "CHANGED:" in capsys.readouterr().err

    monkeypatch.setattr(facade, "run_eval", lambda *a, **k: 1)  # aborts, writes nothing new
    for stale in (tmp_path / ".idp-regression-run-artifacts").glob("*.json"):
        stale.unlink()
    assert verify_document.main(argv) == 1
    err = capsys.readouterr().err
    assert "RUN FAILED:" in err and "CHANGED:" not in err


def test_compare_main_banner_follows_the_artifact_not_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    document_dir = _documents(tmp_path, 1)

    class _Verify:
        def __init__(self, writes: bool) -> None:
            self.writes = writes

        def main(self, argv: list[str]) -> int:
            if self.writes:
                _write_failing_artifact(tmp_path / ".idp-regression-run-artifacts")
            return 1

    argv = ["--document-dir", str(document_dir), "--dataset", "ds", "--org", "o",
            "--action", "a", "--trusted-version", "1.0.0", "--candidate-version", "2.0.0",
            "--store", str(tmp_path / "pins"), "--yes"]
    for writes, banner in ((True, "CHANGED:"), (False, "RUN FAILED:")):
        verify = _Verify(writes)
        monkeypatch.setattr(
            compare_versions,
            "_load",
            lambda name, v=verify: _FakeStage() if name == "pin_document" else v,
        )
        for stale in (tmp_path / ".idp-regression-run-artifacts").glob("*.json"):
            stale.unlink()
        assert compare_versions.main(argv) == 1
        assert banner in capsys.readouterr().err


def test_a_legacy_pin_without_a_provisioned_record_is_re_sent(tmp_path: Path) -> None:
    """F-2 / M20."""
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF")
    _pinned(tmp_path, "inv-001.pdf")  # pre-DEBT-97 record: no `provisioned` key
    provision = _FakeStage()
    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _raw(), provision
    )
    assert exit_code == 0 and summary["reprovisioned"] == ["inv-001.pdf"]


def test_a_failed_reprovisioning_is_not_lost_in_a_batch_with_new_documents(
    tmp_path: Path,
) -> None:
    """F-2 / M22: DEBT-97's "re-run reports success" in a mixed batch."""
    docs = _documents(tmp_path, 2)
    _pinned(tmp_path, "doc-001.pdf", document_dir=str(docs))  # legacy, unprovisioned

    class _FailOld:
        calls: list[list[str]] = []

        def main(self, argv: list[str]) -> int:
            return 1 if "doc-001.pdf" in argv else 0

    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, None, document_dir=docs, all=True), lambda path: _raw(), _FailOld()
    )
    assert exit_code == 1
    assert [f["document_id"] for f in summary["failures"]] == ["doc-001.pdf"]


def test_bootstrap_resume_never_rewrites_an_entry_it_already_drafted(tmp_path: Path) -> None:
    """F-3 / M07: a hand-reconciled entry survives --resume."""
    document_dir = _documents(tmp_path, 2)
    args = _bootstrap_args(tmp_path, document_dir)
    bootstrap.run(args, lambda d: _raw())
    golden = json.loads(args.out.read_text())
    golden["doc-001"]["fields"]["total"]["value"] = "HAND-EDITED"
    args.out.write_text(json.dumps(golden))

    bootstrap.run(_bootstrap_args(tmp_path, document_dir, resume=True), lambda d: _raw())

    assert json.loads(args.out.read_text())["doc-001"]["fields"]["total"]["value"] == (
        "HAND-EDITED"
    )


def test_a_table_wrong_format_never_reads_as_gate_failing() -> None:
    """F-4 / M14, M16: overall_gate ignores a table's wrong_format, even
    on a block that carries format_critical."""
    block = _table("wrong_format")
    block["format_critical"] = True
    assert not any(show_run._fails(cell) for _, cell in show_run._leaves({"t": block}))


def test_calibrate_reads_an_absent_critical_as_false() -> None:
    """F-5 / M12: `critical` is schema-optional and the gate reads it absent
    as False; calibration must not promote it."""
    golden: dict[str, Any] = {f"d{i}": _entry(f"d{i}.pdf") for i in range(20)}
    del golden["d0"]["fields"]["total"]["critical"]
    calibrated, _ = calibrate_golden.calibrate(golden, None)
    assert calibrated["d0"]["fields"]["total"]["critical"] is False


def test_run_outcome_calls_an_unreadable_artifact_a_failed_run(tmp_path: Path) -> None:
    """F-6 / M30."""
    arts = tmp_path / "arts"
    arts.mkdir()
    (arts / "bad.json").write_text("{not json")
    assert batch.run_outcome(1, since=0.0, artifact_dir=arts)[0] == "RUN FAILED"


# --- DEBT-99: an output file never chmods the operator's directory ----------


def test_writing_an_output_leaves_an_existing_parent_alone(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    batch.write_private_json(shared / "golden.json", {"a": 1})
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755, "the operator's directory is theirs"
    assert stat.S_IMODE((shared / "golden.json").stat().st_mode) == 0o600


def test_writing_an_output_creates_missing_parents_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "new" / "deeper" / "report.json"
    batch.write_private_json(target, {})
    assert stat.S_IMODE((tmp_path / "new").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "new" / "deeper").stat().st_mode) == 0o700


def test_a_stale_partial_file_is_rewritten_owner_only(tmp_path: Path) -> None:
    stale = tmp_path / ".report.json.partial"
    stale.write_text("old")
    os.chmod(stale, 0o644)
    batch.write_private_json(tmp_path / "report.json", {})
    assert stat.S_IMODE((tmp_path / "report.json").stat().st_mode) == 0o600


def test_noise_floor_refuses_an_unwritable_report_path_before_spending(tmp_path: Path) -> None:
    """A report written once, at the end, to a path that cannot take it
    used to lose every extraction the run had paid for."""
    document_dir = _documents(tmp_path, 1)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    adapter = _ScriptedAdapter({"doc-001.pdf": [_normalized(), _normalized()]})
    try:
        exit_code, _ = noise_floor.run(
            _noise_args(tmp_path, document_dir, out=locked / "floor.json"), adapter
        )
    finally:
        os.chmod(locked, 0o700)
    assert exit_code == 2
    assert adapter.calls == 0


# --- DEBT-100: a pin is the BYTES it was read from, not the filename --------


def test_pin_refuses_a_same_named_file_with_different_bytes(tmp_path: Path) -> None:
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF-original")
    pin_document.run(_pin_args(tmp_path, document), lambda path: _raw(), _FakeStage())

    document.write_bytes(b"%PDF-a-different-invoice")
    calls: list[Path] = []
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _record(calls, path), _FakeStage()
    )
    assert exit_code == 2 and calls == [], "refused before spending"

    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, document, repin=True), lambda path: _record(calls, path), _FakeStage()
    )
    assert exit_code == 0 and len(calls) == 1, "--repin re-reads the new bytes"


def test_pin_still_skips_the_same_bytes(tmp_path: Path) -> None:
    document = tmp_path / "inv-001.pdf"
    document.write_bytes(b"%PDF-original")
    pin_document.run(_pin_args(tmp_path, document), lambda path: _raw(), _FakeStage())
    calls: list[Path] = []
    exit_code, summary = pin_document.run(
        _pin_args(tmp_path, document), lambda path: _record(calls, path), _FakeStage()
    )
    assert exit_code == 0 and calls == [] and summary.get("already_pinned")


def test_verify_refuses_to_compare_different_bytes_against_a_golden(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"%PDF-now")
    _pins_file(
        tmp_path,
        {"a.pdf": {"dataset": "ds", "document_dir": str(docs), "org": "o",
                   "sha256": hashlib.sha256(b"%PDF-when-pinned").hexdigest()}},
    )
    run_eval = _RecordingRunEval()
    exit_code, _ = verify_document.run(_verify_args(tmp_path, all=True), run_eval)
    assert exit_code == 2 and run_eval.calls == []


def test_bootstrap_resume_refuses_a_document_whose_bytes_changed(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)
    bootstrap.run(_bootstrap_args(tmp_path, document_dir), lambda d: _raw())
    (document_dir / "doc-001.pdf").write_bytes(b"%PDF-a-different-invoice")
    calls: list[Path] = []
    exit_code, _ = bootstrap.run(
        _bootstrap_args(tmp_path, document_dir, resume=True), lambda d: _record(calls, d)
    )
    assert exit_code == 2 and calls == []


# --- DEBT-102: documents sharing a stem are never collapsed -----------------


def test_bootstrap_keeps_both_documents_that_share_a_stem(tmp_path: Path) -> None:
    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    (document_dir / "inv.pdf").write_bytes(b"%PDF-invoice")
    (document_dir / "inv.png").write_bytes(b"\x89PNG-scan")
    args = _bootstrap_args(tmp_path, document_dir, glob="*.pdf,*.png")

    exit_code, _ = bootstrap.run(args, lambda d: _raw())

    assert exit_code == 0
    golden = json.loads(args.out.read_text())
    assert sorted(e["document_id"] for e in golden.values()) == ["inv.pdf", "inv.png"]
    assert len(golden) == 2


def test_a_redraft_keeps_the_key_a_document_was_first_drafted_under(tmp_path: Path) -> None:
    golden = {"inv": {"document_id": "inv.png"}}
    assert bootstrap._golden_key("inv.png", golden) == "inv"
    assert bootstrap._golden_key("inv.pdf", golden) == "inv.pdf"
    assert bootstrap._golden_key("other.pdf", golden) == "other"


def test_verify_asks_run_eval_for_exact_document_matches(tmp_path: Path) -> None:
    """DEBT-117: the ids verify passes are resolved already; the substring
    fallback must not be able to substitute another item for one of them."""
    _pinned(tmp_path, "a.pdf")
    run_eval = _RecordingRunEval()
    verify_document.run(_verify_args(tmp_path, all=True), run_eval)
    (_, kwargs), = run_eval.calls
    assert kwargs["exact_documents"] is True


# --- DEBT-100/99 review round: --from-captures and the worklist -------------


def test_from_captures_refuses_a_document_whose_bytes_changed(tmp_path: Path) -> None:
    document_dir = _documents(tmp_path, 2)
    bootstrap.run(_bootstrap_args(tmp_path, document_dir), lambda d: _raw())
    (document_dir / "doc-001.pdf").write_bytes(b"%PDF-a-different-invoice")
    exit_code, _ = bootstrap.run(
        _bootstrap_args(tmp_path, document_dir, from_captures=True), lambda d: _raw()
    )
    assert exit_code == 2


def test_from_captures_without_a_document_dir_says_it_cannot_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document_dir = _documents(tmp_path, 1)
    bootstrap.run(_bootstrap_args(tmp_path, document_dir), lambda d: _raw())
    capsys.readouterr()
    exit_code, _ = bootstrap.run(
        _bootstrap_args(tmp_path, None, from_captures=True), lambda d: _raw()
    )
    assert exit_code == 0
    assert "NOT checked" in capsys.readouterr().err


def test_the_review_worklist_is_owner_only_in_an_existing_open_directory(
    tmp_path: Path,
) -> None:
    """It carries every drafted value. Once DEBT-99 stopped chmodding an
    existing parent, nothing else was keeping it private."""
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    os.chmod(open_dir, 0o755)
    document_dir = _documents(tmp_path, 1)
    args = _bootstrap_args(tmp_path, document_dir, out=open_dir / "draft.json")

    _, summary = bootstrap.run(args, lambda d: _raw())

    assert stat.S_IMODE(Path(summary["review"]).stat().st_mode) == 0o600


def test_a_stale_partial_worklist_is_rewritten_owner_only(tmp_path: Path) -> None:
    stale = tmp_path / ".review.md.partial"
    stale.write_text("old")
    os.chmod(stale, 0o644)
    batch.write_private_text(tmp_path / "review.md", "values")
    assert stat.S_IMODE((tmp_path / "review.md").stat().st_mode) == 0o600


# --- Wave A1 /test gate findings F-1..F-4, and two write-then-chmod sites ---


def test_verify_checks_the_bytes_at_the_NEW_source_not_the_pinned_directory(
    tmp_path: Path,
) -> None:
    """F-1 / M17: the row's own scenario, and the path compare_versions and
    the console drive. Pinned from dir A; verified from dir B, where a file
    of the same name holds different bytes."""
    pinned_dir = tmp_path / "pinned"
    pinned_dir.mkdir()
    (pinned_dir / "a.pdf").write_bytes(b"%PDF-original")
    new_source = tmp_path / "new-source"
    new_source.mkdir()
    (new_source / "a.pdf").write_bytes(b"%PDF-a-different-invoice")
    _pins_file(
        tmp_path,
        {"a.pdf": {"dataset": "ds", "document_dir": str(pinned_dir), "org": "o",
                   "sha256": hashlib.sha256(b"%PDF-original").hexdigest()}},
    )
    run_eval = _RecordingRunEval()
    exit_code, _ = verify_document.run(
        _verify_args(tmp_path, all=True, document_dir=new_source), run_eval
    )
    assert exit_code == 2 and run_eval.calls == []


def test_pin_refuses_a_changed_file_before_spending_on_the_new_ones(tmp_path: Path) -> None:
    """F-2 / M15: a mixed batch -- one already-pinned file rewritten, one new."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "old.pdf").write_bytes(b"%PDF-old")
    pin_document.run(
        _pin_args(tmp_path, None, document_dir=docs, all=True), lambda path: _raw(), _FakeStage()
    )
    (docs / "old.pdf").write_bytes(b"%PDF-old-but-replaced")
    (docs / "new.pdf").write_bytes(b"%PDF-new")
    calls: list[Path] = []
    exit_code, _ = pin_document.run(
        _pin_args(tmp_path, None, document_dir=docs, all=True),
        lambda path: _record(calls, path),
        _FakeStage(),
    )
    assert exit_code == 2 and calls == [], "nothing spent, not even on the new file"


def test_compare_passes_repin_to_the_pin_half_only_when_asked(tmp_path: Path) -> None:
    """F-3 / M25: always passing --repin would bypass the byte check and
    re-pay every pinned document on each re-run."""
    for repin in (False, True):
        document_dir = tmp_path / f"docs-{repin}"
        document_dir.mkdir()
        (document_dir / "a.pdf").write_bytes(b"%PDF")
        pin = _FakeStage()
        compare_versions.run(
            _compare_args(tmp_path, document_dir=document_dir, repin=repin, dataset=f"d{repin}"),
            pin,
            _FakeStage(),
        )
        assert ("--repin" in pin.calls[0]) is repin


def test_a_missing_file_is_not_reported_as_changed(tmp_path: Path) -> None:
    """F-4 / M12b: absence is the MISSING check's business (refused unless
    --allow-missing); calling it a byte change would mislabel it."""
    recorded = {"gone.pdf": {"sha256": "0" * 64}}
    assert batch.changed_since_recorded([tmp_path / "gone.pdf"], recorded) == []


def test_unpacked_documents_are_owner_only(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"a.pdf": b"%PDF"})
    paths, _ = batch.extract_documents_from_zip(archive, tmp_path / "out")
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600


def test_the_calibration_report_markdown_is_owner_only(tmp_path: Path) -> None:
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({f"d{i}": _entry(f"d{i}.pdf") for i in range(5)}))
    out = tmp_path / "calibrated.json"
    assert calibrate_golden.main(["--golden-file", str(golden), "--out", str(out)]) == 0
    assert stat.S_IMODE(out.with_suffix(".calibration.md").stat().st_mode) == 0o600


def test_draft_golden_writes_its_golden_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-105: `draft_golden --out` used a plain `open(..., "w")`."""
    draft_golden = _load("draft_golden")
    capture = tmp_path / "doc.raw.json"
    capture.write_text(json.dumps(_raw()))
    out = tmp_path / "open" / "golden.json"
    out.parent.mkdir()
    os.chmod(out.parent, 0o755)

    monkeypatch.setattr(
        sys, "argv",
        ["draft_golden", "--capture", str(capture), "--document-id", "doc.pdf", "--out", str(out)],
    )
    rc = draft_golden.main()

    assert rc == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert "doc" in json.loads(out.read_text())


# --- DEBT-104: one type per table column across the corpus ------------------


def _with_table(document_id: str, qty_type: str | None) -> dict[str, Any]:
    entry = _golden_entry(document_id, total="1")
    block: dict[str, Any] = {"match_key": "sku", "critical": True,
                             "rows": [{"sku": "S1", "qty": "3"}]}
    if qty_type is not None:
        block["types"] = {"qty": qty_type}
    entry["tables"] = {"line_items": block}
    return entry


def test_calibrate_unifies_a_table_columns_type_across_the_corpus() -> None:
    # The majority (`text`) deliberately does NOT sort first, so a rule that
    # picked alphabetically instead of by count would be caught.
    golden = {
        "d0": _with_table("d0.pdf", "number"),
        "d1": _with_table("d1.pdf", "text"),
        "d2": _with_table("d2.pdf", "text"),
        "d3": _with_table("d3.pdf", None),  # no value seen: no vote, still unified
    }
    calibrated, report = calibrate_golden.calibrate(golden, None)
    assert {calibrated[k]["tables"]["line_items"]["types"]["qty"] for k in golden} == {"text"}
    assert report["column_types"]["line_items.qty"]["counts"] == {"number": 1, "text": 2}


def test_an_agreed_column_type_is_not_reported() -> None:
    golden = {f"d{i}": _with_table(f"d{i}.pdf", "number") for i in range(3)}
    _, report = calibrate_golden.calibrate(golden, None)
    assert report["column_types"] == {}


# --- DEBT-101: no pooled "within the floor" above a real red ----------------


def test_show_run_never_prints_a_pooled_reassurance_above_an_above_floor_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`total` is noisy (50% floor) and dominates the pooled rate; `amount`
    has a 0% floor and fails once, so it is ABOVE. The pooled comparison
    used to conclude "within the floor" directly above that red."""
    documents = {
        f"doc-{i:03d}.pdf": {
            "total": _cell("wrong_value" if i % 2 else "match", critical=False),
            "amount": _cell("wrong_value" if i == 1 else "match"),
        }
        for i in range(1, 31)
    }
    artifact = _artifact(tmp_path, documents)
    floor = _floor_report(
        tmp_path,
        {
            "total": {"observations": 50, "unstable": 25, "instability_rate": 0.5},
            "amount": {"observations": 50, "unstable": 0, "instability_rate": 0.0},
        },
        rate=0.5,
    )
    show_run.main([str(artifact), "--baseline", str(floor)])
    out = capsys.readouterr().out
    assert "within the floor" not in out
    assert "1 field(s) disagree MORE than the floor predicts" in out


def test_show_run_says_when_nothing_is_above_the_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    documents = {f"doc-{i:03d}.pdf": {"total": _cell("match")} for i in range(1, 31)}
    artifact = _artifact(tmp_path, documents)
    floor = _floor_report(
        tmp_path, {"total": {"observations": 50, "unstable": 1, "instability_rate": 0.02}}
    )
    show_run.main([str(artifact), "--baseline", str(floor)])
    assert "No field disagrees more than the floor predicts." in capsys.readouterr().out


# --- DEBT-108 / DEBT-112 ----------------------------------------------------


def test_capture_raw_spends_nothing_without_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_raw = _load("capture_raw")
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF")

    def _never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no adapter may be built without --yes")

    monkeypatch.setattr(capture_raw, "make_idp_adapter", _never)
    rc = capture_raw.main(["--org", "o", "--action", "a", "--version", "1.0.0",
                           "--document", str(document)])
    assert rc == 2


def test_show_run_counts_a_new_table_as_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    documents = {"a.pdf": {"total": _cell("match"),
                           "freight": {"verdict": "new_table", "actual": 2, "critical": False}}}
    show_run.main([str(_artifact(tmp_path, documents))])
    out = capsys.readouterr().out
    assert "new=1" in out
