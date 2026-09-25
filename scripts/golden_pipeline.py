#!/usr/bin/env python
"""ZIP in, provisioned golden dataset out. One command, one approval.

The five tools this repo grew for a model swap each do one step well and
each need their outputs wired to the next one's inputs by hand. This runs
them in order, in process, with the wiring done:

    unpack ─▶ noise floor ─▶ draft ─▶ calibrate ─▶ provision ─▶ [compare]
             (a sample)      (all)    (corpus-wide) (dataset)   (candidate)

and spends the quota only once you have said `--yes` to the total it
prints first.

What is automated, and what is not
----------------------------------
Automated: unpacking, sampling for the floor, drafting every document,
setting `critical` from measured stability and corpus presence, unifying
each table's `match_key` and each field's `type` across the corpus,
provisioning, and -- with `--candidate-version` -- running the candidate
and reading it against the floor.

NOT automated, and not automatable: **whether the extractor's answers are
right.** Every expected value came from the incumbent, so what this
produces is an *agreement baseline* -- a green run means the candidate
agrees with the incumbent, not that either is correct. Turning it into
ground truth means reading values against pages, and the only thing a
pipeline can do about that is refuse to hide it. This one prints it at
the end, every time, along with the gate's blind spots.

Resumable
---------
Each stage writes into `--work-dir` and records itself in
`pipeline.json`. `--resume` re-runs the same command and skips whatever
already landed -- including per-document captures, so a batch that died
at document 700 of 1,000 restarts at 701, not at 1.

Usage
-----
    # what would it cost? nothing is unpacked, nothing is spent
    .venv/bin/python scripts/golden_pipeline.py --zip ~/corpus.zip \\
        --dataset customer-baseline-v1 \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 --plan

    # the whole thing
    .venv/bin/python scripts/golden_pipeline.py --zip ~/corpus.zip \\
        --dataset customer-baseline-v1 \\
        --org $IDP_ORG_ID --action $IDP_ACTION_ID --version 1.0.0 \\
        --max-documents 1000 --yes

    # ... and compare a candidate version in the same run
    ... --candidate-version 2.0.0
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import sys
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
    confirm_cost,
    discover_documents,
    ensure_private_dir,
    extract_documents_from_zip,
    read_json_if_present,
    write_private_json,
)


def _load(name: str) -> Any:
    """`scripts/` is not a package. Each stage is the real script, loaded
    and called in process -- never a reimplementation of it, so the
    pipeline cannot drift from the tool an operator runs by hand."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise RuntimeError(f"scripts/{name}.py is missing or unloadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGES = ("noise", "draft", "calibrate", "provision", "compare")


def _state_path(work_dir: Path) -> Path:
    return work_dir / "pipeline.json"


def _record(work_dir: Path, state: dict[str, Any], stage: str, payload: dict[str, Any]) -> None:
    state.setdefault("stages", {})[stage] = {
        "at": dt.datetime.now(dt.UTC).isoformat(),
        **payload,
    }
    write_private_json(_state_path(work_dir), state)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="golden_pipeline",
        description="ZIP -> noise floor -> draft -> calibrate -> provision (-> compare).",
    )
    ap.add_argument("--zip", dest="zip_path", type=Path, help="archive of documents")
    ap.add_argument("--document-dir", type=Path, help="an already-unpacked directory instead")
    ap.add_argument("--dataset", required=True, help="Langfuse dataset name to provision")
    ap.add_argument("--org", required=True, help="organisation / business group id")
    ap.add_argument("--action", required=True, help="IDP action id")
    ap.add_argument("--version", required=True, help="the INCUMBENT action version")
    ap.add_argument(
        "--candidate-version",
        help="also run this version against the new dataset and read it against the floor",
    )
    ap.add_argument("--work-dir", type=Path, help="default: .idp-regression-pipeline/<dataset>")
    ap.add_argument(
        "--glob",
        default=DEFAULT_DOCUMENT_PATTERNS,
        help=f"comma-separated filename patterns (default: {DEFAULT_DOCUMENT_PATTERNS})",
    )
    ap.add_argument("--max-documents", type=int, default=1000, help="ceiling on the draft")
    ap.add_argument("--noise-sample", type=int, default=100, help="documents for the floor")
    ap.add_argument("--noise-repeats", type=int, default=2, help="extractions per sampled doc")
    ap.add_argument("--noise-floor", type=Path, help="reuse an existing noise-floor report")
    ap.add_argument(
        "--skip-noise-floor",
        action="store_true",
        help=(
            "do not measure stability. Every field stays critical on the evidence of "
            "one pass, so expect reds that are not regressions."
        ),
    )
    ap.add_argument("--noise-tolerance", type=float, default=None)
    ap.add_argument("--sparse-threshold", type=float, default=None)
    ap.add_argument("--stop-after", choices=STAGES, help="stop once this stage completes")
    ap.add_argument("--resume", action="store_true", help="skip stages that already landed")
    ap.add_argument("--plan", action="store_true", help="print the plan and the cost, then stop")
    ap.add_argument("--yes", action="store_true", help="approve the whole pipeline's quota")
    return ap.parse_args(argv)


def run(args: argparse.Namespace, stages: Any) -> tuple[int, dict[str, Any]]:
    """`stages` carries the four loaded scripts + the run_eval entry
    point, injected so the tests drive the whole pipeline without an IDP
    or a platform."""
    work_dir = args.work_dir or Path(".idp-regression-pipeline") / args.dataset
    document_dir = args.document_dir or (work_dir / "documents")
    golden = work_dir / "golden.json"
    calibrated = work_dir / "golden.calibrated.json"

    if not args.plan:
        ensure_private_dir(work_dir)
    state: dict[str, Any] = read_json_if_present(_state_path(work_dir)) if args.resume else {}
    done: dict[str, Any] = state.get("stages", {}) if args.resume else {}

    # ── unpack ────────────────────────────────────────────────────────
    if args.zip_path:
        try:
            unpacked, skipped = extract_documents_from_zip(
                args.zip_path, document_dir, patterns=args.glob, dry_run=args.plan
            )
        except ZipRejectedError as exc:
            print(f"golden_pipeline: {exc}", file=sys.stderr)
            return 2, {}
        print(
            f"golden_pipeline: {'would unpack' if args.plan else 'unpacked'} "
            f"{len(unpacked)} document(s) -> {document_dir}",
            file=sys.stderr,
        )
        for note in skipped[:10]:
            print(f"    skipped {note}", file=sys.stderr)
        if len(skipped) > 10:
            print(f"    ... and {len(skipped) - 10} more skipped", file=sys.stderr)
        found = unpacked
    else:
        found = discover_documents(document_dir, args.glob, args.max_documents)

    documents = min(len(found), args.max_documents)
    measuring_floor = not (args.skip_noise_floor or args.noise_floor)
    sample = min(documents, args.noise_sample) if measuring_floor else 0

    # ── the one approval ──────────────────────────────────────────────
    total = sample * args.noise_repeats + documents + (documents if args.candidate_version else 0)
    print(f"golden_pipeline: PLAN for dataset {args.dataset!r}", file=sys.stderr)
    print(f"  documents           {documents}", file=sys.stderr)
    print(
        f"  noise floor         {sample} document(s) x {args.noise_repeats} "
        f"= {sample * args.noise_repeats} extraction(s)",
        file=sys.stderr,
    )
    print(f"  draft               {documents} extraction(s)", file=sys.stderr)
    if args.candidate_version:
        print(
            f"  candidate run       {documents} extraction(s) (version "
            f"{args.candidate_version})",
            file=sys.stderr,
        )
    try:
        confirm_cost(documents=total, extractions_each=1, approved=args.yes or args.plan)
    except QuotaRefusedError as exc:
        print(f"golden_pipeline: refusing to start — {exc}", file=sys.stderr)
        return 2, {}
    if args.plan:
        print("golden_pipeline: --plan, stopping before any IDP call.", file=sys.stderr)
        return 0, {}

    common = ["--org", args.org, "--action", args.action, "--version", args.version]
    summary: dict[str, Any] = {"work_dir": str(work_dir), "document_dir": str(document_dir)}

    # ── 1. noise floor ────────────────────────────────────────────────
    floor_report = args.noise_floor
    if args.skip_noise_floor:
        print("golden_pipeline: [1/5] noise floor SKIPPED by request", file=sys.stderr)
    elif floor_report:
        print(f"golden_pipeline: [1/5] noise floor reused from {floor_report}", file=sys.stderr)
    elif args.resume and "noise" in done:
        floor_report = Path(done["noise"]["report"])
        print(
            f"golden_pipeline: [1/5] noise floor already landed ({floor_report})",
            file=sys.stderr,
        )
    else:
        print(f"golden_pipeline: [1/5] noise floor over {sample} document(s)", file=sys.stderr)
        floor_report = work_dir / "noise-floor.json"
        rc = stages.noise_floor.main(
            [
                *common,
                "--document-dir", str(document_dir),
                "--glob", args.glob,
                "--max-documents", str(sample),
                "--repeats", str(args.noise_repeats),
                "--out", str(floor_report),
                "--yes",
            ]
        )
        if rc == 2:
            return rc, summary
        _record(work_dir, state, "noise", {"report": str(floor_report), "exit_code": rc})
    summary["noise_floor"] = str(floor_report) if floor_report else None
    if args.stop_after == "noise":
        return 0, summary

    # ── 2. draft ──────────────────────────────────────────────────────
    print(f"golden_pipeline: [2/5] drafting {documents} document(s)", file=sys.stderr)
    draft_argv = [
        *common,
        "--document-dir", str(document_dir),
        "--glob", args.glob,
        "--out", str(golden),
        "--captures-dir", str(work_dir / "captures"),
        "--max-documents", str(args.max_documents),
        "--resume",  # always: a re-run must never re-pay for a capture
        "--yes",
    ]
    rc = stages.bootstrap_golden_set.main(draft_argv)
    _record(work_dir, state, "draft", {"golden": str(golden), "exit_code": rc})
    if not golden.exists():
        print("golden_pipeline: drafting produced no golden set — stopping", file=sys.stderr)
        return 1, summary
    summary["golden"] = str(golden)
    summary["draft_exit_code"] = rc
    if args.stop_after == "draft":
        return 0, summary

    # ── 3. calibrate ──────────────────────────────────────────────────
    print("golden_pipeline: [3/5] calibrating against the corpus and the floor", file=sys.stderr)
    calibrate_argv = ["--golden-file", str(golden), "--out", str(calibrated)]
    if floor_report:
        calibrate_argv += ["--noise-floor", str(floor_report)]
    if args.noise_tolerance is not None:
        calibrate_argv += ["--noise-tolerance", str(args.noise_tolerance)]
    if args.sparse_threshold is not None:
        calibrate_argv += ["--sparse-threshold", str(args.sparse_threshold)]
    rc = stages.calibrate_golden.main(calibrate_argv)
    if rc != 0:
        return rc, summary
    _record(work_dir, state, "calibrate", {"golden": str(calibrated)})
    summary["calibrated"] = str(calibrated)
    calibration = read_json_if_present(calibrated.with_suffix(".calibration.json"))
    summary["blind_spots"] = calibration.get("blind_spots", [])
    summary["still_human"] = calibration.get("still_human", [])
    if args.stop_after == "calibrate":
        return 0, summary

    # ── 4. provision ──────────────────────────────────────────────────
    print(f"golden_pipeline: [4/5] provisioning dataset {args.dataset!r}", file=sys.stderr)
    rc = stages.provision_golden_dataset.main(
        ["--dataset", args.dataset, "--all", "--golden-file", str(calibrated)]
    )
    _record(work_dir, state, "provision", {"dataset": args.dataset, "exit_code": rc})
    summary["dataset"] = args.dataset
    summary["provision_exit_code"] = rc
    if rc != 0:
        print(
            "golden_pipeline: provisioning reported failures — the dataset is "
            "INCOMPLETE. Fix the named entries and re-run with --resume.",
            file=sys.stderr,
        )
        return rc, summary
    if args.stop_after == "provision" or not args.candidate_version:
        return 0, summary

    # ── 5. compare ────────────────────────────────────────────────────
    print(
        f"golden_pipeline: [5/5] running candidate {args.candidate_version} "
        f"against {args.dataset!r}",
        file=sys.stderr,
    )
    # `run_eval` resolves every document_id against IDP_DOCUMENT_DIR; the
    # pipeline knows where it unpacked them, so the operator never has to.
    os.environ["IDP_DOCUMENT_DIR"] = str(document_dir.resolve())
    run_name = f"candidate-{args.candidate_version}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
    gate = stages.run_eval(
        [
            "--org", args.org,
            "--action", args.action,
            "--version", args.candidate_version,
            "--dataset", args.dataset,
            "--run", run_name,
            "--max-documents-per-run", str(args.max_documents),
        ]
    )
    _record(work_dir, state, "compare", {"run": run_name, "exit_code": gate})
    summary["candidate_run"] = run_name
    summary["gate_exit_code"] = gate

    show_argv = ["--failures-only"]
    if floor_report:
        show_argv += ["--baseline", str(floor_report)]
    stages.show_run.main(show_argv)
    return gate, summary


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if bool(args.zip_path) == bool(args.document_dir):
        print(
            "golden_pipeline: pass --zip OR --document-dir (exactly one)", file=sys.stderr
        )
        return 2

    from idp_regression.orchestration.cli import main as run_eval

    stages = argparse.Namespace(
        noise_floor=_load("noise_floor"),
        bootstrap_golden_set=_load("bootstrap_golden_set"),
        calibrate_golden=_load("calibrate_golden"),
        provision_golden_dataset=_load("provision_golden_dataset"),
        show_run=_load("show_run"),
        run_eval=run_eval,
    )

    exit_code, summary = run(args, stages)
    if not summary:
        return exit_code

    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print("GOLDEN PIPELINE — done", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"  dataset            {summary.get('dataset', '(not provisioned)')}", file=sys.stderr)
    print(f"  golden set         {summary.get('calibrated', summary.get('golden', '-'))}",
          file=sys.stderr)
    print(f"  noise floor        {summary.get('noise_floor') or '(not measured)'}",
          file=sys.stderr)
    print(
        f"  IDP_DOCUMENT_DIR   {Path(summary['document_dir']).resolve()}",
        file=sys.stderr,
    )
    if summary.get("candidate_run"):
        print(f"  candidate run      {summary['candidate_run']}", file=sys.stderr)
    blind = summary.get("blind_spots") or []
    print("", file=sys.stderr)
    if blind:
        print(
            f"  ⚠️  {len(blind)} GATE BLIND SPOT(S) — a regression in these will not "
            "fail a run:",
            file=sys.stderr,
        )
        for name in blind:
            print(f"        {name}", file=sys.stderr)
    else:
        print("  every field and table stayed critical.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  STILL A HUMAN'S JOB:", file=sys.stderr)
    for item in summary.get("still_human", []):
        print(f"    - {item}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
