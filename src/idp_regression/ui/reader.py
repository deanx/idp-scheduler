"""Read-only views over what a run leaves on local disk.

Everything here answers a question the evaluation platform structurally
cannot, which is the whole reason this UI exists:

* **the noise floor** -- is this red above the extractor's own variance,
  within it, or is there too little evidence to say? The floor is a
  corpus-level statistic measured against ONE version; the platform has
  no place to hold it and no way to join it to a run.
* **gate blind spots** -- calibration demotes unstable and sparse fields
  from `critical`, fail-open by design. A green run on the platform
  cannot say "green, and here are the fields the gate is blind to".
* **pins** -- `STILL VALID` / `CHANGED` per document per (action,
  version). Datasets are flat-named; that axis lives in the pin store's
  paths.

Nothing here writes, spends quota, or reaches the network. The floor
comparison is `scripts/show_run.py`'s own, called in process -- see
`scripts_bridge`.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any

from idp_regression.classifier.gate import overall_gate
from idp_regression.orchestration.run_artifact import (
    RunArtifact,
    parse_run_artifact,
    run_level_gate,
)
from idp_regression.ui import workspace
from idp_regression.ui.scripts_bridge import load

# Resolved against the WORKSPACE, never the process's working directory:
# a job writes these same directories from a child process, and the two
# sides disagreeing is how a paid-for run becomes invisible. See
# `workspace.py` for the bug this closes.


def ARTIFACT_DIR() -> Path:  # noqa: N802 - kept call-shaped; see workspace.py
    return workspace.artifact_dir()


def NOISE_FLOOR_DIR() -> Path:  # noqa: N802
    return workspace.noise_floor_dir()


def PIN_STORE_DIR() -> Path:  # noqa: N802
    return workspace.pin_store_dir()


def _show_run() -> Any:
    return load("show_run")


def _mtime(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).isoformat()


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

def _artifact_payload(path: Path) -> RunArtifact:
    """One artifact file, through `run_artifact.parse_run_artifact` -- the
    single reader of both envelopes, so completeness (DEBT-91) cannot be
    read one way here and another way in `show_run`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return parse_run_artifact(data)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from None


def _document_gate(fields: dict[str, Any]) -> str:
    """PASS/FAIL for one document, from the AUTHORITATIVE gate.

    `show_run` keeps a `_BAD` tuple for display grouping and says in its
    own comment that it is never the decision. So the decision is made
    here by `overall_gate`, the same function the run itself used -- a
    UI that re-derived pass/fail from verdict words would eventually
    disagree with the build, and the UI would be the one that is wrong.
    """
    try:
        return overall_gate(fields)
    except (KeyError, TypeError, ValueError):
        # A malformed artifact must not 500 the whole list. Narrow on
        # purpose: these are what a hand-edited or truncated verdict map
        # raises, and anything else is a bug worth seeing.
        return "UNKNOWN"


def list_runs(artifact_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every run artifact, newest first, with its gate and verdict counts."""
    root = artifact_dir or ARTIFACT_DIR()
    if not root.is_dir():
        return []
    show_run = _show_run()
    runs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            artifact = _artifact_payload(path)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            runs.append({"run_id": path.stem, "error": str(exc), "recorded_at": _mtime(path)})
            continue
        counts: Counter[str] = Counter()
        gates: list[str] = []
        for fields in artifact.documents.values():
            counts.update(show_run._counts(fields))
            gates.append(_document_gate(fields))
        runs.append({
            "run_id": artifact.run_id,
            "recorded_at": _mtime(path),
            "documents": len(artifact.documents),
            "failing_documents": gates.count("FAIL"),
            "gate": run_level_gate(gates, artifact.status),
            "status": artifact.status,
            "abort_reason": artifact.abort_reason,
            "verdicts": dict(sorted(counts.items())),
        })
    return runs


def _resolve_artifact(run_id: str, artifact_dir: Path | None = None) -> Path:
    root = artifact_dir or ARTIFACT_DIR()
    candidate = root / f"{run_id}.json"
    if candidate.is_file() and candidate.resolve().parent == root.resolve():
        return candidate
    # The file is usually named for the run, but the run id inside the
    # envelope is what is authoritative -- fall back to a scan so a
    # renamed artifact is still reachable.
    for path in root.glob("*.json"):
        try:
            if _artifact_payload(path).run_id == run_id:
                return path
        except (ValueError, json.JSONDecodeError, OSError):
            continue
    raise FileNotFoundError(f"no run artifact for {run_id!r}")


def read_run(
    run_id: str,
    *,
    baseline: str | None = None,
    artifact_dir: Path | None = None,
    noise_floor_dir: Path | None = None,
) -> dict[str, Any]:
    """One run, optionally read against a noise floor.

    With `baseline`, every field carries the floor's verdict on it --
    `above`, `within`, `unknown`, `no_floor` -- and a failing document
    whose red rests ENTIRELY on fields the floor explains is flagged.

    The two fail-closed rules `show_run` documents hold here unchanged,
    because this calls `show_run`'s own functions: a gate-failing field
    the floor cannot speak to keeps its failure real, and the gate is
    never softened by the floor (INV-08). The floor explains a red; it
    never clears one.
    """
    show_run = _show_run()
    path = _resolve_artifact(run_id, artifact_dir)
    artifact = _artifact_payload(path)
    resolved_id, documents = artifact.run_id, artifact.documents

    comparison: dict[str, dict[str, Any]] = {}
    baseline_meta: dict[str, Any] | None = None
    if baseline:
        report = _read_floor_report(baseline, noise_floor_dir)
        index = show_run._baseline_index(report)
        comparison = show_run._compare(show_run._run_rates(documents), index)
        baseline_meta = {
            "name": baseline,
            "summary": report.get("summary"),
            "fields_with_floor": len(index),
        }

    out_documents: list[dict[str, Any]] = []
    for document_id, fields in sorted(documents.items()):
        leaves = []
        for label, cell in show_run._leaves(fields):
            key = show_run._field_key(label)
            leaves.append({
                "label": label,
                "field_key": key,
                "verdict": cell.get("verdict"),
                "expected": cell.get("expected"),
                "actual": cell.get("actual"),
                "confidence": cell.get("confidence"),
                "type": cell.get("type"),
                "critical": bool(cell.get("critical", False)),
                "gate_failing": bool(show_run._fails(cell)),
                "floor_status": comparison.get(key, {}).get("status") if comparison else None,
            })
        gate = _document_gate(fields)
        out_documents.append({
            "document_id": document_id,
            "gate": gate,
            "verdicts": dict(sorted(show_run._counts(fields).items())),
            "rests_on_noise": (
                bool(show_run._failure_rests_on_noise(fields, comparison))
                if comparison and gate == "FAIL"
                else False
            ),
            "leaves": leaves,
        })

    return {
        "run_id": resolved_id,
        "recorded_at": _mtime(path),
        "gate": run_level_gate((d["gate"] for d in out_documents), artifact.status),
        "status": artifact.status,
        "abort_reason": artifact.abort_reason,
        "baseline": baseline_meta,
        "field_comparison": comparison,
        "documents": out_documents,
    }


# --------------------------------------------------------------------------
# Noise floors
# --------------------------------------------------------------------------

def _read_floor_report(name: str, noise_floor_dir: Path | None = None) -> dict[str, Any]:
    root = noise_floor_dir or NOISE_FLOOR_DIR()
    candidate = Path(name)
    if not candidate.is_file():
        candidate = root / (name if name.endswith(".json") else f"{name}.json")
    if not candidate.is_file():
        raise FileNotFoundError(f"no noise-floor report {name!r}")
    if candidate.resolve().parent != root.resolve() and not Path(name).is_file():
        raise FileNotFoundError(f"{name!r} does not resolve inside {root}")
    report: dict[str, Any] = json.loads(candidate.read_text(encoding="utf-8"))
    return report


def list_noise_floors(noise_floor_dir: Path | None = None) -> list[dict[str, Any]]:
    root = noise_floor_dir or NOISE_FLOOR_DIR()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            out.append({"name": path.name, "error": str(exc)})
            continue
        if "by_field" not in report:
            continue  # not a floor report; the directory also holds unpacked documents
        summary = report.get("summary", {})
        out.append({
            "name": path.name,
            "generated_at": report.get("generated_at") or _mtime(path),
            "action_id": report.get("action_id"),
            "action_version": report.get("action_version"),
            "repeats": report.get("repeats"),
            "documents": report.get("documents"),
            "field_instability_rate": summary.get("field_instability_rate"),
            "field_observations": summary.get("field_observations"),
            "fields": len(report.get("by_field", {})),
        })
    return out


def read_noise_floor(name: str, noise_floor_dir: Path | None = None) -> dict[str, Any]:
    report = _read_floor_report(name, noise_floor_dir)
    by_field = report.get("by_field", {})
    fields = [
        {
            "field": field,
            "instability_rate": stats.get("instability_rate"),
            "observations": stats.get("observations"),
            "unstable": stats.get("unstable"),
        }
        for field, stats in sorted(
            by_field.items(),
            key=lambda kv: -float(kv[1].get("instability_rate", 0) or 0),
        )
    ]
    return {
        "name": name,
        "summary": report.get("summary"),
        "interpretation": report.get("interpretation"),
        "fields": fields,
    }


# --------------------------------------------------------------------------
# Calibration blind spots
# --------------------------------------------------------------------------

def list_calibrations(search_dirs: list[Path] | None = None) -> list[dict[str, Any]]:
    """Every `*.calibration.json` report, with its blind spots.

    A blind spot is a field calibration demoted from `critical` -- so a
    regression in it will NOT fail the build. Calibration's rules are
    fail-open by design, which is exactly why they have to be shown
    rather than logged once at generation time.
    """
    roots = search_dirs or [
        workspace.workspace_root(),
        workspace.workspace_root() / ".idp-regression-pipeline",
    ]
    seen: set[Path] = set()
    out: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/*.calibration.json"), key=lambda p: -p.stat().st_mtime):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                out.append({"name": str(path), "error": str(exc)})
                continue
            out.append({
                "name": str(path),
                "generated_at": report.get("generated_at") or _mtime(path),
                "corpus": report.get("corpus"),
                "noise_floor_applied": bool(report.get("noise_floor_applied")),
                "thresholds": report.get("thresholds"),
                "blind_spots": report.get("blind_spots", []),
                "still_human": report.get("still_human", []),
                "fields": report.get("fields", {}),
                "tables": report.get("tables", {}),
            })
    return out


# --------------------------------------------------------------------------
# Pin store
# --------------------------------------------------------------------------

def list_pins(store: Path | None = None) -> list[dict[str, Any]]:
    """The pin store as the tree it is: action -> version -> documents.

    The paths ARE the relationship (user decision, 2026-09-25), so this
    reads them rather than an index: `<store>/goldens/<action>/<version>/
    <document>.json`, with `_pins.json` beside them for the document
    directory and dataset.
    """
    root = store or PIN_STORE_DIR()
    goldens = root / "goldens"
    if not goldens.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for action_dir in sorted(p for p in goldens.iterdir() if p.is_dir()):
        for version_dir in sorted(p for p in action_dir.iterdir() if p.is_dir()):
            pins_meta: dict[str, Any] = {}
            pins_file = version_dir / "_pins.json"
            if pins_file.is_file():
                try:
                    pins_meta = json.loads(pins_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pins_meta = {}
            documents = []
            for golden in sorted(version_dir.glob("*.json")):
                if golden.name == "_pins.json":
                    continue
                capture = (
                    root / "captures" / action_dir.name / version_dir.name
                    / f"{golden.stem}.raw.json"
                )
                documents.append({
                    "document_id": golden.stem,
                    "pinned_at": _mtime(golden),
                    "has_raw_capture": capture.is_file(),
                    "fields": _golden_field_count(golden),
                })
            out.append({
                "action_id": action_dir.name,
                "action_version": version_dir.name,
                "dataset": pins_meta.get("dataset"),
                "document_dir": pins_meta.get("document_dir"),
                "documents": documents,
            })
    return out


def _golden_field_count(path: Path) -> int | None:
    try:
        golden = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # A pin is written as a one-entry golden set, so the count lives one
    # level in -- the same shape `provision_golden_dataset.py --seed`
    # already validates.
    entries = golden.values() if isinstance(golden, dict) else []
    for entry in entries:
        if isinstance(entry, dict) and "fields" in entry:
            return len(entry.get("fields", {})) + len(entry.get("tables", {}))
    return None
