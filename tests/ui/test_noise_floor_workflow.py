"""Ticket 8: making a `CHANGED` verdict interpretable.

Pin/verify compares **one** reading at the trusted version against
**one** reading at the candidate. If either version is nondeterministic,
`CHANGED` cannot be told from the extractor disagreeing with *itself* --
and a model swap is precisely when that is most likely. `CLAUDE.md`
already says so for the corpus path: *"Skipping the first step produces
a golden set with the incumbent's coin-flips baked in as 'expected', and
a red build rate nobody can interpret."* The per-file path had the same
hole; this closes it by making the floor a first-class step of the
validation workflow rather than a separate thing an operator is told
about in a failure hint.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="the `ui` extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from idp_regression.orchestration.run_artifact import artifact_envelope  # noqa: E402
from idp_regression.ui import jobs, preflight, workspace  # noqa: E402
from idp_regression.ui.api import create_app  # noqa: E402


@pytest.fixture
def runnable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A console that CAN run: credentials present, so the server-side
    preflight is not what these tests are measuring."""
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in preflight.IDP_VARIABLES + preflight.PLATFORM_VARIABLES:
        monkeypatch.setenv(name, "configured")
    try:
        yield TestClient(create_app(scorer_dir=tmp_path / "scorers"))
    finally:
        workspace.set_workspace(previous)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (directory / name).write_bytes(b"%PDF-1.4 fixture")
    return directory


def floor_body(corpus: Path, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_dir": str(corpus),
        "org": "org-1",
        "action": "action-1",
        "version": "1.0.0",
        "repeats": 2,
        "max_documents": 20,
    }
    body.update(overrides)
    return body


class TestArgv:
    def test_it_invokes_the_real_noise_floor_script(self, corpus: Path) -> None:
        argv = jobs.build_noise_floor_argv(
            document_dir=corpus, org="o", action="a", version="1.0.0", plan_only=True
        )
        assert argv[1].endswith("scripts/noise_floor.py")
        assert "--version" in argv and "1.0.0" in argv

    def test_one_repeat_is_refused(self, corpus: Path) -> None:
        """The first read is the reference; with only one there is
        nothing to compare it against and the "floor" would be 0% by
        construction -- a number that would then be used to dismiss real
        regressions."""
        with pytest.raises(jobs.JobRejectedError, match="at least 2"):
            jobs.build_noise_floor_argv(
                document_dir=corpus, org="o", action="a", version="1.0.0",
                repeats=1, plan_only=True,
            )

    @pytest.mark.parametrize("sample", [0, -5, 1001])
    def test_an_out_of_range_sample_is_refused(self, corpus: Path, sample: int) -> None:
        with pytest.raises(jobs.JobRejectedError, match="between 1 and 1000"):
            jobs.build_noise_floor_argv(
                document_dir=corpus, org="o", action="a", version="1.0.0",
                max_documents=sample, plan_only=True,
            )

    @pytest.mark.parametrize(
        "field,value",
        [("org", "o; rm -rf /"), ("action", "$(id)"), ("version", "1.0.0 --yes")],
    )
    def test_shell_metacharacters_are_refused_here_too(
        self, corpus: Path, field: str, value: str
    ) -> None:
        values = {"org": "o", "action": "a", "version": "1.0.0"} | {field: value}
        with pytest.raises(jobs.JobRejectedError, match="is not a valid value"):
            jobs.build_noise_floor_argv(
                document_dir=corpus,
                org=values["org"],
                action=values["action"],
                version=values["version"],
                plan_only=True,
            )

    def test_the_plan_and_the_run_differ_only_in_the_final_flag(self, corpus: Path) -> None:
        plan = jobs.build_noise_floor_argv(
            document_dir=corpus, org="o", action="a", version="1.0.0", plan_only=True
        )
        run = jobs.build_noise_floor_argv(
            document_dir=corpus, org="o", action="a", version="1.0.0", plan_only=False
        )
        assert plan[-1] == "--plan" and run[-1] == "--yes"
        assert plan[:-1] == run[:-1]


class TestAgainstTheRealScript:
    def test_the_plan_prices_repeats_times_sample(self, corpus: Path) -> None:
        """Runs `scripts/noise_floor.py --plan` as a real subprocess. It
        spends nothing, and it is the only check that the argv this
        module builds is one the script accepts and that the cost shown
        is the cost the script computed."""
        extractions, lines = jobs.plan(
            jobs.build_noise_floor_argv(
                document_dir=corpus, org="org-1", action="action-1",
                version="1.0.0", repeats=2, max_documents=20, plan_only=True,
            )
        )
        assert extractions == 6, "3 documents x 2 reads"
        assert any("stopping before any IDP call" in line for line in lines)

    def test_a_bigger_repeat_count_costs_more(self, corpus: Path) -> None:
        extractions, _ = jobs.plan(
            jobs.build_noise_floor_argv(
                document_dir=corpus, org="org-1", action="action-1",
                version="1.0.0", repeats=3, max_documents=20, plan_only=True,
            )
        )
        assert extractions == 9


class TestTheEndpoints:
    def test_planning_a_floor_spends_nothing_and_returns_the_count_to_echo(
        self, runnable: TestClient, corpus: Path
    ) -> None:
        body = runnable.post("/api/workflows/floor/plan", json=floor_body(corpus)).json()
        assert body["planned_extractions"] == 6
        assert body["confirm_with"]["approved_extractions"] == 6

    def test_starting_a_floor_without_the_approved_count_is_refused(
        self, runnable: TestClient, corpus: Path
    ) -> None:
        response = runnable.post("/api/workflows/floor/start", json=floor_body(corpus))
        assert response.status_code == 422
        assert "approved_extractions" in response.json()["detail"]

    def test_starting_a_floor_against_the_wrong_count_is_refused(
        self, runnable: TestClient, corpus: Path
    ) -> None:
        response = runnable.post(
            "/api/workflows/floor/start",
            json=floor_body(corpus, approved_extractions=2),
        )
        assert response.status_code == 409
        assert "re-plan and confirm" in response.json()["detail"]

    def test_a_bad_value_is_refused_before_any_process_starts(
        self, runnable: TestClient, corpus: Path
    ) -> None:
        response = runnable.post(
            "/api/workflows/floor/plan", json=floor_body(corpus, version="1.0.0; echo hi")
        )
        assert response.status_code == 422


class TestReadingTheFloorBack:
    def test_the_report_name_is_recovered_from_the_scripts_REAL_output(self) -> None:
        """Pinned against the line `noise_floor.py` actually prints
        (`report: <path>`, `scripts/noise_floor.py`), not a plausible
        invention. A fabricated fixture passes a verb-agnostic pattern
        while proving nothing about the format it has to parse (Zangado
        QA F-B)."""
        lines = [
            "  (none disagreed)",
            "",
            "report: .idp-regression-noise-floor/noise-floor-20260925T120000Z.json",
            "=" * 72,
        ]
        assert jobs.find_floor_report(lines) == "noise-floor-20260925T120000Z.json"

    def test_the_real_script_still_prints_a_line_this_can_parse(self) -> None:
        """Belt and braces on the same coupling: if that print statement
        is reworded, this fails rather than the console silently losing
        the ability to read a run against the floor it just measured."""
        source = (
            Path(__file__).resolve().parents[2] / "scripts" / "noise_floor.py"
        ).read_text(encoding="utf-8")
        assert 'print(f"\\nreport: {report[\'_out\']}"' in source

    def test_the_last_report_named_wins(self) -> None:
        lines = [
            "old .idp-regression-noise-floor/noise-floor-20260101T000000Z.json",
            "new .idp-regression-noise-floor/noise-floor-20260925T120000Z.json",
        ]
        assert jobs.find_floor_report(lines) == "noise-floor-20260925T120000Z.json"

    def test_no_report_is_none_not_a_guess(self) -> None:
        assert jobs.find_floor_report(["nothing here", "still nothing"]) is None

    def test_a_floor_job_summary_carries_the_report(self, tmp_path: Path) -> None:
        previous = workspace.workspace_root()
        workspace.set_workspace(tmp_path)
        try:
            line = (
                "noise_floor: wrote "
                ".idp-regression-noise-floor/noise-floor-20260925T120000Z.json"
            )
            summary = jobs.summarize([line], 0, since=0.0)
            assert summary["floor_report"] == "noise-floor-20260925T120000Z.json"
        finally:
            workspace.set_workspace(previous)


def test_a_run_read_against_a_floor_still_fails_the_gate(tmp_path: Path) -> None:
    """INV-08, restated for this flow: the floor EXPLAINS a red, it never
    clears one. A workflow that measured the floor and then reported
    "green after all" would be the silently-wrong GREEN build `## Rigor`
    names as this system's worst failure.
    """
    import json

    from idp_regression.ui import reader

    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        artifacts = workspace.artifact_dir()
        artifacts.mkdir(parents=True)
        run_id = "abcd" * 8
        documents = {
            f"d{i}.pdf": {
                "bill_to": {
                    "verdict": "match", "critical": True, "expected": "x",
                    "actual": "x", "type": "text",
                }
            }
            for i in range(40)
        }
        documents["d0.pdf"] = {
            "bill_to": {
                "verdict": "wrong_value", "critical": True, "expected": "x",
                "actual": "y", "type": "text",
            }
        }
        (artifacts / f"{run_id}.json").write_text(
            json.dumps(artifact_envelope(run_id, documents, status="complete")), encoding="utf-8"
        )

        floors = workspace.noise_floor_dir()
        floors.mkdir(parents=True)
        (floors / "floor.json").write_text(
            json.dumps({
                "summary": {"field_instability_rate": 0.2},
                "by_field": {
                    "bill_to": {"instability_rate": 0.2, "observations": 100, "unstable": 20}
                },
            }),
            encoding="utf-8",
        )

        detail = reader.read_run(run_id, baseline="floor.json")
        assert detail["field_comparison"]["bill_to"]["status"] == "within"
        failing = [d for d in detail["documents"] if d["gate"] == "FAIL"]
        assert failing and failing[0]["rests_on_noise"] is True
        assert detail["gate"] == "FAIL", (
            "the floor explains the red; it must never clear it"
        )
    finally:
        workspace.set_workspace(previous)


def test_a_floor_job_never_claims_a_run_artifact(tmp_path: Path) -> None:
    """A floor job runs `noise_floor.py`, which writes a floor report and
    NO run artifact. Letting the time-based fallback look anyway means a
    concurrent compare job's artifact could be attributed to it --
    document counts from a run this job never performed (Zangado QA
    F-C). There is nothing to find, so it must not look.
    """
    import json

    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        artifacts = workspace.artifact_dir()
        artifacts.mkdir(parents=True)
        # A neighbour's artifact, newer than the floor job, whose gate
        # agrees with the floor job's exit code -- so the F-3 cross-check
        # alone would NOT reject it.
        run_id = "9999" * 8
        (artifacts / f"{run_id}.json").write_text(
            json.dumps(
                artifact_envelope(
                    run_id,
                    {"someone-elses.pdf": {"total": {"verdict": "match", "critical": True}}},
                    status="complete",
                )
            ),
            encoding="utf-8",
        )
        summary = jobs.summarize(
            ["report: .idp-regression-noise-floor/noise-floor-20260925T120000Z.json"],
            0,
            since=0.0,
            kind="noise-floor",
        )
        assert summary["floor_report"] == "noise-floor-20260925T120000Z.json"
        assert summary["run_id"] is None
        assert summary["documents"] is None

        # The same output under a COMPARE job does resolve the artifact --
        # so the guard above is the `kind`, not an accident of the fixture.
        compare = jobs.summarize(["nothing"], 0, since=0.0, kind="compare-versions")
        assert compare["run_id"] == run_id
    finally:
        workspace.set_workspace(previous)
