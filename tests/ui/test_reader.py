"""The read side: runs, the noise floor, blind spots, pins.

Two properties carry the weight here, and both are about NOT lying to
the person reading a red build:

* the gate shown is `overall_gate`'s, never re-derived from verdict
  words. A console that disagreed with the build about pass/fail would
  be the one that is wrong, and nobody would know which.
* `within floor` never becomes a pass. The floor explains a red; it does
  not clear one, and a gate-failing field the floor cannot speak to keeps
  its failure real (INV-08, fail-closed on ignorance).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

from idp_regression.orchestration.run_artifact import artifact_envelope
from idp_regression.ui import reader


def write_artifact(
    root: Path,
    run_id: str,
    documents: dict[str, Any],
    *,
    status: Literal["complete", "aborted"] = "complete",
    abort_reason: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{run_id}.json").write_text(
        json.dumps(artifact_envelope(run_id, documents, status=status, abort_reason=abort_reason)),
        encoding="utf-8",
    )


def field(
    verdict: str,
    *,
    critical: bool = False,
    expected: str | None = "1.00",
    actual: str | None = "1.00",
    confidence: float | None = 0.9,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "expected": expected,
        "actual": actual,
        "confidence": confidence,
        "critical": critical,
        "format_critical": False,
        "type": "number",
    }


class TestRuns:
    def test_an_empty_or_absent_directory_is_no_runs_not_an_error(self, tmp_path: Path) -> None:
        assert reader.list_runs(tmp_path / "absent") == []
        (tmp_path / "empty").mkdir()
        assert reader.list_runs(tmp_path / "empty") == []

    def test_a_run_summary_carries_the_gate_and_the_verdict_counts(self, tmp_path: Path) -> None:
        write_artifact(tmp_path, "r1", {
            "a.pdf": {"total": field("match", critical=True)},
            "b.pdf": {"total": field("missing", critical=True, actual=None)},
        })
        [run] = reader.list_runs(tmp_path)
        assert run["run_id"] == "r1"
        assert run["documents"] == 2
        assert run["failing_documents"] == 1
        assert run["gate"] == "FAIL"
        assert run["verdicts"] == {"match": 1, "missing": 1}

    def test_the_gate_is_the_gates_not_a_count_of_bad_words(self, tmp_path: Path) -> None:
        """A `missing` on a field the golden did NOT mark critical does
        not fail the document. Counting bad verdicts would say it does.
        """
        write_artifact(
            tmp_path, "r1", {"a.pdf": {"note": field("missing", critical=False, actual=None)}}
        )
        [run] = reader.list_runs(tmp_path)
        assert run["verdicts"] == {"missing": 1}
        assert run["gate"] == "PASS", (
            "an ungated field's loss must not be reported as a failed run"
        )

    def test_a_malformed_artifact_is_reported_by_name_and_never_hides_the_others(
        self, tmp_path: Path
    ) -> None:
        write_artifact(tmp_path, "good", {"a.pdf": {"total": field("match")}})
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        runs = reader.list_runs(tmp_path)
        assert len(runs) == 2
        assert any(r.get("error") for r in runs)
        assert any(r.get("gate") == "PASS" for r in runs)

    def test_a_table_is_flattened_to_its_leaves(self, tmp_path: Path) -> None:
        write_artifact(tmp_path, "r1", {"a.pdf": {"line_items": {
            "critical": True,
            "verdict": "detail",
            "rows": [
                {"match_key": "SKU-1", "column": "amount", "verdict": "match",
                 "expected": "1", "actual": "1"},
                {"match_key": "SKU-1", "column": "qty", "verdict": "wrong_value",
                 "expected": "1", "actual": "2"},
            ],
        }}})
        detail = reader.read_run("r1", artifact_dir=tmp_path)
        labels = [leaf["label"] for leaf in detail["documents"][0]["leaves"]]
        assert labels == ["line_items[SKU-1].amount", "line_items[SKU-1].qty"]

    def test_an_unknown_run_is_a_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            reader.read_run("nope", artifact_dir=tmp_path)

    def test_a_renamed_artifact_is_still_reachable_by_its_run_id(self, tmp_path: Path) -> None:
        write_artifact(tmp_path, "r1", {"a.pdf": {"total": field("match")}})
        (tmp_path / "r1.json").rename(tmp_path / "renamed.json")
        assert reader.read_run("r1", artifact_dir=tmp_path)["run_id"] == "r1"


class TestNoiseFloor:
    @pytest.fixture
    def floor_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "floor"
        root.mkdir()
        (root / "report.json").write_text(json.dumps({
            "generated_at": "2026-09-25T00:00:00Z",
            "summary": {"field_instability_rate": 0.02, "field_observations": 1000},
            "by_field": {
                "total": {"instability_rate": 0.0, "observations": 100, "unstable": 0},
                "bill_to": {"instability_rate": 0.2, "observations": 100, "unstable": 20},
                "rare": {"instability_rate": 0.5, "observations": 2, "unstable": 1},
            },
        }), encoding="utf-8")
        return root

    def test_a_directory_of_unpacked_documents_is_not_listed_as_a_report(
        self, floor_dir: Path
    ) -> None:
        (floor_dir / "not-a-report.json").write_text(
            json.dumps({"something": 1}), encoding="utf-8"
        )
        listed = reader.list_noise_floors(floor_dir)
        assert [f["name"] for f in listed] == ["report.json"]

    def test_fields_are_ordered_worst_first(self, floor_dir: Path) -> None:
        detail = reader.read_noise_floor("report.json", floor_dir)
        assert [f["field"] for f in detail["fields"]][:2] == ["rare", "bill_to"]

    def test_a_field_with_a_zero_floor_is_above_it_the_moment_it_disagrees(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        """Nothing else could explain it. This is the case a bare rate
        comparison gets right by accident and the margin gets right on
        purpose."""
        documents = {f"d{i}.pdf": {"total": field("match")} for i in range(40)}
        documents["d0.pdf"] = {"total": field("wrong_value", actual="2.00")}
        write_artifact(tmp_path, "r1", documents)
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        assert detail["field_comparison"]["total"]["status"] == "above"

    def test_a_disagreement_inside_a_high_floor_is_within_it(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        documents = {f"d{i}.pdf": {"bill_to": field("match")} for i in range(40)}
        documents["d0.pdf"] = {"bill_to": field("wrong_value", actual="x")}
        write_artifact(tmp_path, "r1", documents)
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        assert detail["field_comparison"]["bill_to"]["status"] == "within"

    def test_too_few_observations_says_so_rather_than_ranking_them(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        write_artifact(tmp_path, "r1", {"d0.pdf": {"rare": field("wrong_value", actual="x")}})
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        assert detail["field_comparison"]["rare"]["status"] == "unknown"

    def test_a_field_absent_from_the_baseline_is_no_floor_not_a_pass(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        write_artifact(tmp_path, "r1", {"d0.pdf": {"brand_new": field("wrong_value", actual="x")}})
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        assert detail["field_comparison"]["brand_new"]["status"] == "no_floor"

    def test_the_floor_never_changes_the_gate(self, tmp_path: Path, floor_dir: Path) -> None:
        """INV-08. `rests_on_noise` is a label on a red, never a green.
        """
        documents = {f"d{i}.pdf": {"bill_to": field("match", critical=True)} for i in range(40)}
        documents["d0.pdf"] = {"bill_to": field("wrong_value", critical=True, actual="x")}
        write_artifact(tmp_path, "r1", documents)
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        failing = [d for d in detail["documents"] if d["gate"] == "FAIL"]
        assert len(failing) == 1
        assert failing[0]["rests_on_noise"] is True
        assert detail["gate"] == "FAIL", "the floor explains the red; it must never clear it"

    def test_a_failure_on_a_field_the_floor_cannot_speak_to_is_not_explained(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        """Fail-closed on ignorance: the claim 'this red is noise' is the
        one that stops an investigation."""
        documents = {
            f"d{i}.pdf": {
                "bill_to": field("match", critical=True),
                "brand_new": field("match", critical=True),
            }
            for i in range(40)
        }
        documents["d0.pdf"] = {
            "bill_to": field("wrong_value", critical=True, actual="x"),
            "brand_new": field("wrong_value", critical=True, actual="y"),
        }
        write_artifact(tmp_path, "r1", documents)
        detail = reader.read_run(
            "r1", baseline="report.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
        )
        failing = [d for d in detail["documents"] if d["gate"] == "FAIL"][0]
        assert failing["rests_on_noise"] is False

    def test_an_unknown_baseline_is_a_file_not_found(
        self, tmp_path: Path, floor_dir: Path
    ) -> None:
        write_artifact(tmp_path, "r1", {"a.pdf": {"total": field("match")}})
        with pytest.raises(FileNotFoundError):
            reader.read_run(
                "r1", baseline="absent.json", artifact_dir=tmp_path, noise_floor_dir=floor_dir
            )


class TestBlindSpots:
    def test_a_calibration_report_surfaces_its_demoted_fields(self, tmp_path: Path) -> None:
        (tmp_path / "golden.calibration.json").write_text(json.dumps({
            "generated_at": "2026-09-25T00:00:00Z",
            "corpus": {"documents": 30, "fields": 10},
            "noise_floor_applied": True,
            "thresholds": {"noise_tolerance": 0.02},
            "fields": {"bill_to": {"critical": False, "detail": "unstable: 20% > 2%"}},
            "tables": {},
            "blind_spots": ["bill_to"],
            "still_human": ["whether the extractor's answers are RIGHT"],
        }), encoding="utf-8")
        [report] = reader.list_calibrations([tmp_path])
        assert report["blind_spots"] == ["bill_to"]
        assert report["still_human"]
        assert report["noise_floor_applied"] is True

    def test_no_reports_is_an_empty_list(self, tmp_path: Path) -> None:
        assert reader.list_calibrations([tmp_path]) == []


class TestPins:
    def test_the_paths_are_read_as_the_relationship(self, tmp_path: Path) -> None:
        """The same document at two versions is two rows, which is
        exactly what a flat dataset name cannot say."""
        for version in ("v1", "v2"):
            directory = tmp_path / "goldens" / "action-1" / version
            directory.mkdir(parents=True)
            (directory / "inv-001.pdf.json").write_text(
                json.dumps({"inv-001.pdf": {"fields": {"total": {}}, "tables": {}}}),
                encoding="utf-8",
            )
            (directory / "_pins.json").write_text(
                json.dumps({"dataset": "golden", "document_dir": "/docs"}), encoding="utf-8"
            )
        capture = tmp_path / "captures" / "action-1" / "v1"
        capture.mkdir(parents=True)
        (capture / "inv-001.pdf.raw.json").write_text("{}", encoding="utf-8")

        groups = reader.list_pins(tmp_path)
        assert [(g["action_id"], g["action_version"]) for g in groups] == [
            ("action-1", "v1"), ("action-1", "v2")
        ]
        assert groups[0]["dataset"] == "golden"
        assert groups[0]["documents"][0]["has_raw_capture"] is True
        assert groups[1]["documents"][0]["has_raw_capture"] is False

    def test_the_pins_marker_is_never_listed_as_a_document(self, tmp_path: Path) -> None:
        directory = tmp_path / "goldens" / "a" / "v1"
        directory.mkdir(parents=True)
        (directory / "_pins.json").write_text(json.dumps({"dataset": "d"}), encoding="utf-8")
        [group] = reader.list_pins(tmp_path)
        assert group["documents"] == []

    def test_an_absent_store_is_empty(self, tmp_path: Path) -> None:
        assert reader.list_pins(tmp_path / "absent") == []


class TestCompleteness:
    """DEBT-91: the console read an aborted run's partial artifact as PASS."""

    def test_an_aborted_run_is_incomplete_in_the_list_and_the_detail(
        self, tmp_path: Path
    ) -> None:
        write_artifact(
            tmp_path, "r1", {"a.pdf": {"total": field("match", critical=True)}},
            status="aborted", abort_reason="timeout",
        )
        [run] = reader.list_runs(tmp_path)
        assert (run["gate"], run["status"], run["abort_reason"]) == (
            "INCOMPLETE", "aborted", "timeout",
        )
        detail = reader.read_run("r1", artifact_dir=tmp_path)
        assert detail["gate"] == "INCOMPLETE"
        assert detail["abort_reason"] == "timeout"

    def test_an_abort_before_the_first_document_is_not_an_empty_pass(
        self, tmp_path: Path
    ) -> None:
        write_artifact(tmp_path, "r1", {}, status="aborted", abort_reason="auth_failure")
        [run] = reader.list_runs(tmp_path)
        assert run["gate"] == "INCOMPLETE"

    def test_a_legacy_artifact_is_incomplete_because_it_cannot_say(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "r1.json").write_text(
            json.dumps({"r1": {"a.pdf": {"total": field("match", critical=True)}}}),
            encoding="utf-8",
        )
        [run] = reader.list_runs(tmp_path)
        assert (run["gate"], run["status"]) == ("INCOMPLETE", None)

    def test_a_known_failure_in_an_aborted_run_still_reads_fail(self, tmp_path: Path) -> None:
        write_artifact(
            tmp_path, "r1", {"a.pdf": {"total": field("missing", critical=True, actual=None)}},
            status="aborted", abort_reason="timeout",
        )
        [run] = reader.list_runs(tmp_path)
        assert run["gate"] == "FAIL"


def test_an_uncomputable_document_makes_a_complete_run_incomplete(tmp_path: Path) -> None:
    """/test gate F-3 / M13: a malformed verdict map inside a `complete`
    artifact must not render PASS."""
    write_artifact(tmp_path, "r1", {
        "a.pdf": {"total": field("match", critical=True)},
        "b.pdf": {"total": {"critical": True}},  # no verdict: overall_gate cannot read it
    })
    [run] = reader.list_runs(tmp_path)
    assert run["gate"] == "INCOMPLETE"
    assert reader.read_run("r1", artifact_dir=tmp_path)["gate"] == "INCOMPLETE"
