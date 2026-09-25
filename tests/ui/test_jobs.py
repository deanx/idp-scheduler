"""The one thing in this console that spends money.

Every test here is about the two guards that make that acceptable:

1. **Nothing reaches a shell.** `argv` is a list, built from values that
   passed the same grammar the path-building code uses. A console that
   interpolated user text into a command line would be a
   command-injection surface on a host holding IDP and platform
   credentials -- the same class of mistake `registry.py` refuses for
   `--classifier`.
2. **The approval carries the number.** The client must echo back the
   extraction count this server computed from the REAL `--plan`. A stale
   page cannot approve a cost it never displayed.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from idp_regression.ui import jobs, workspace


@pytest.fixture
def document_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "documents"
    directory.mkdir()
    (directory / "inv-001.pdf").write_bytes(b"%PDF-1.4")
    return directory


def argv_for(document_dir: Path, **overrides: object) -> list[str]:
    kwargs: dict[str, object] = {
        "document_dir": document_dir,
        "dataset": "invoices-golden",
        "org": "org-1",
        "action": "action-1",
        "trusted_version": "1.0.0",
        "candidate_version": "2.0.0",
        "plan_only": True,
    }
    kwargs.update(overrides)
    return jobs.build_compare_argv(**kwargs)  # type: ignore[arg-type]


class TestArgvConstruction:
    def test_it_invokes_the_real_script_not_a_reimplementation(self, document_dir: Path) -> None:
        argv = argv_for(document_dir)
        assert argv[0] == sys.executable
        assert argv[1].endswith("scripts/compare_versions.py")

    def test_the_plan_and_the_run_differ_only_in_the_final_flag(self, document_dir: Path) -> None:
        plan = argv_for(document_dir, plan_only=True)
        run = argv_for(document_dir, plan_only=False)
        assert plan[-1] == "--plan"
        assert run[-1] == "--yes"
        assert plan[:-1] == run[:-1], (
            "the priced command and the executed command must be the same command"
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("org", "org-1; rm -rf /"),
            ("org", "$(whoami)"),
            ("action", "a`id`"),
            ("action", "../../etc/passwd"),
            ("trusted_version", "1.0.0 --yes"),
            ("candidate_version", "2.0.0\nmalicious"),
            ("dataset", "d&&curl evil.example"),
            ("org", ""),
            ("action", "x" * 129),
        ],
    )
    def test_a_value_that_is_not_the_expected_grammar_is_refused(
        self, document_dir: Path, field: str, value: str
    ) -> None:
        with pytest.raises(jobs.JobRejectedError, match="is not a valid value"):
            argv_for(document_dir, **{field: value})

    def test_a_dataset_may_carry_a_space_but_still_not_a_metacharacter(
        self, document_dir: Path
    ) -> None:
        assert "my invoices" in argv_for(document_dir, dataset="my invoices")
        with pytest.raises(jobs.JobRejectedError):
            argv_for(document_dir, dataset="my;invoices")

    def test_comparing_a_version_with_itself_is_refused(self, document_dir: Path) -> None:
        """That comparison cannot fail, so running it would spend 2N
        extractions to prove nothing."""
        with pytest.raises(jobs.JobRejectedError, match="cannot fail"):
            argv_for(document_dir, trusted_version="1.0.0", candidate_version="1.0.0")

    def test_a_missing_document_directory_is_refused_before_anything_starts(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(jobs.JobRejectedError, match="is not a directory"):
            argv_for(tmp_path / "absent")

    @pytest.mark.parametrize("bad", [0, -1, 5000])
    def test_an_out_of_range_document_ceiling_is_refused(
        self, document_dir: Path, bad: int
    ) -> None:
        with pytest.raises(jobs.JobRejectedError, match="between 1 and 1000"):
            argv_for(document_dir, max_documents=bad)


class TestCostApproval:
    def test_a_job_will_not_start_against_a_cost_the_client_did_not_echo(
        self, document_dir: Path
    ) -> None:
        registry = jobs.JobRegistry()
        argv = argv_for(document_dir, plan_only=False)
        with pytest.raises(jobs.JobRejectedError, match="re-plan and confirm"):
            registry.start(
                "compare-versions", argv, planned_extractions=40, approved_extractions=2
            )

    def test_a_job_will_not_start_from_a_plan_argv(self, document_dir: Path) -> None:
        """`--plan` spends nothing, so starting one as a job would
        produce a 'run' that silently did no work and reported success."""
        registry = jobs.JobRegistry()
        with pytest.raises(jobs.JobRejectedError, match="must end in --yes"):
            registry.start(
                "compare-versions",
                argv_for(document_dir, plan_only=True),
                planned_extractions=2,
                approved_extractions=2,
            )

    def test_plan_refuses_an_argv_that_is_not_a_plan(self, document_dir: Path) -> None:
        with pytest.raises(jobs.JobRejectedError, match="must be given a --plan argv"):
            jobs.plan(argv_for(document_dir, plan_only=False))


class TestSummary:
    """What the console reports when a job ends.

    The counts now come from the RUN ARTIFACT (see
    `test_validate_workflow_e2e.py`, which exercises that against a real
    run). These cover the part that must work when there is no artifact
    at all -- a job that died before `run_eval` ever started.
    """

    @pytest.fixture(autouse=True)
    def empty_workspace(self, tmp_path: Path) -> Iterator[None]:
        """An empty workspace. Without it these read the developer's real
        artifact directory and a job claims a run it never produced --
        which is the failure `since=` exists to prevent, so it must not
        be possible to write a passing test that depends on it."""
        previous = workspace.workspace_root()
        workspace.set_workspace(tmp_path)
        yield
        workspace.set_workspace(previous)

    def test_a_non_zero_exit_with_a_verdict_banner_is_a_REGRESSION_not_a_broken_run(
        self,
    ) -> None:
        """`compare_versions.py` exits 0 iff the verification passed, so
        a non-zero exit usually means the tool WORKED. Rendering that as
        an error teaches an operator to dismiss the one result this whole
        system exists to produce."""
        summary = jobs.summarize(
            ["CHANGED: 3 document(s) read with 1.0.0, re-read with 2.0.0"], 1, since=0.0
        )
        assert summary["verdict"] == "CHANGED"

    def test_a_non_zero_exit_with_no_banner_at_all_is_a_broken_run(self) -> None:
        assert jobs.summarize(["Traceback…"], 2, since=0.0)["verdict"] == "RUN FAILED"

    def test_a_zero_exit_is_still_valid(self) -> None:
        summary = jobs.summarize(["STILL VALID: 3 file(s) pinned"], 0, since=0.0)
        assert summary["verdict"] == "STILL VALID"

    def test_the_exit_code_is_carried_through_as_the_gate(self) -> None:
        summary = jobs.summarize([], 1, since=0.0)
        assert summary["exit_code"] == 1
        assert summary["gate_is_the_exit_code"] is True

    def test_the_banner_is_never_counted_as_a_document(self) -> None:
        """The bug this replaces. `STILL VALID` / `CHANGED` appear ONCE
        as a banner, from each of two scripts -- there is no per-document
        line anywhere. Counting them reported "changed: 1" for a
        forty-document run, every time."""
        lines = [
            "CHANGED: 40 document(s) read with 1.0.0, re-read with 2.0.0",
            "CHANGED: 40 file(s) pinned against 1.0.0",
        ]
        summary = jobs.summarize(lines, 1, since=0.0)
        assert summary["changed_documents"] is None, (
            "with no artifact there is no per-document truth, and guessing from text is "
            "what produced the wrong numbers before"
        )

    def test_no_artifact_means_no_counts_rather_than_zero_counts(self) -> None:
        """Zero changed documents and "we could not tell" are different
        answers; only one of them means the corpus is still valid."""
        summary = jobs.summarize(["STILL VALID: 1 file(s)"], 0, since=0.0)
        assert summary["run_id"] is None
        assert summary["documents"] is None
        assert summary["changed_documents"] is None


class TestRegistry:
    def test_an_unknown_job_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            jobs.JobRegistry().get("nope")

    def test_cancelling_a_job_that_is_not_running_is_refused(self, document_dir: Path) -> None:
        registry = jobs.JobRegistry()
        job = jobs.Job(id="abc", kind="compare-versions", argv=argv_for(document_dir))
        registry._jobs["abc"] = job
        with pytest.raises(jobs.JobRejectedError, match="not running"):
            registry.cancel("abc")

    def test_the_log_buffer_is_bounded(self) -> None:
        """One progress line per document over a large corpus; an
        unbounded buffer is a leak that only appears on the big run."""
        job = jobs.Job(id="x", kind="k", argv=[])
        for index in range(jobs.MAX_LOG_LINES + 500):
            job.append(f"line {index}")
        assert len(job.lines) == jobs.MAX_LOG_LINES
        assert job.lines[-1] == f"line {jobs.MAX_LOG_LINES + 499}"

    def test_the_listing_omits_the_log_lines(self, document_dir: Path) -> None:
        registry = jobs.JobRegistry()
        registry._jobs["a"] = jobs.Job(id="a", kind="k", argv=argv_for(document_dir))
        assert "lines" not in registry.summaries()[0]


class TestAgainstTheRealScript:
    """Runs `scripts/compare_versions.py --plan` as a real subprocess.

    Spends nothing -- `--plan` stops before any IDP call -- but it is the
    only test that proves the argv this module builds is one the script
    actually accepts, and that the cost the UI shows is the cost the
    script computed. A unit test with a fake process would pass while the
    two drifted apart, and the drift would surface as a charge.
    """

    @pytest.fixture
    def corpus(self, tmp_path: Path) -> Path:
        directory = tmp_path / "corpus"
        directory.mkdir()
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            (directory / name).write_bytes(b"%PDF-1.4 not a real document")
        return directory

    def test_the_plan_reports_two_extractions_per_document(self, corpus: Path) -> None:
        """2N, not N: the workflow pins every document at the trusted
        version AND verifies every one at the candidate."""
        extractions, lines = jobs.plan(
            argv_for(
                corpus,
                dataset="demo-golden",
                org="11111111-1111-4111-8111-111111111111",
                action="22222222-2222-4222-8222-222222222222",
            )
        )
        assert extractions == 6
        assert any("stopping before any IDP call" in line for line in lines)

    def test_a_plan_that_the_script_refuses_is_a_refusal_here(self, tmp_path: Path) -> None:
        """`--plan` is the archive's last validation before money is
        spent, so its failure must stop the workflow rather than warn."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(jobs.JobRejectedError):
            jobs.plan(argv_for(empty))


class TestCostParsing:
    """The number the UI shows must be the number the invoice shows.

    The first implementation of this read `3` from a plan whose real
    total was `6`, because the grand-total line spells it "6 real IDP
    extraction(s)" and the pattern only matched a number adjacent to the
    word. Underreporting is the dangerous direction, so these pin it.
    """

    def test_the_explicit_grand_total_wins_over_the_per_stage_counts(self) -> None:
        lines = [
            "  1. pin    @ 1.0.0        3 extraction(s)",
            "  2. verify @ 2.0.0        3 extraction(s)",
            "  6 real IDP extraction(s) to be spent",
        ]
        assert max(
            int(m.group(1)) for line in lines for m in [jobs._EXPLICIT_TOTAL.search(line)] if m
        ) == 6

    def test_the_fallback_pattern_still_sees_a_total_with_words_before_extraction(self) -> None:
        match = jobs._ANY_EXTRACTION_COUNT.search("  6 real IDP extraction(s) to be spent")
        assert match is not None and match.group(1) == "6"

    def test_the_fallback_takes_the_maximum_never_the_first(self) -> None:
        lines = ["3 extraction(s)", "6 real IDP extraction(s)"]
        counts = [
            int(m.group(1))
            for line in lines
            for m in [jobs._ANY_EXTRACTION_COUNT.search(line)]
            if m
        ]
        assert max(counts) == 6

    def test_a_plan_with_no_readable_count_is_refused_rather_than_assumed_free(
        self, monkeypatch: pytest.MonkeyPatch, document_dir: Path
    ) -> None:
        import subprocess

        class FakeCompleted:
            returncode = 0
            stdout = "all good\n"
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted())
        with pytest.raises(jobs.JobRejectedError, match="cost could not be read back"):
            jobs.plan(argv_for(document_dir))
