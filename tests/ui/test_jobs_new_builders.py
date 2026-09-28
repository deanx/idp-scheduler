"""Unit tests for the two new argv builders added for the two-stage
golden-review workflow (T-02.1.1 / T-02.1.2).

Each builder mirrors `build_compare_argv`'s validation pattern exactly:
- every value validated against `SAFE_VALUE` / `SAFE_DATASET` before it
  reaches an argv element;
- a resolved, existing directory for `document_dir`;
- `--plan` XOR `--yes` as the last element.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from idp_regression.ui.jobs import (
    SCRIPTS_DIR,
    JobRejectedError,
    build_pin_argv,
    build_verify_argv,
)


# ---------------------------------------------------------------------------
# build_pin_argv (T-02.1.1)
# ---------------------------------------------------------------------------


class TestBuildPinArgv:
    def test_plan_only_ends_in_plan(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            plan_only=True,
        )
        assert argv[-1] == "--plan"
        assert argv[-2] != "--yes"

    def test_run_mode_ends_in_yes(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            plan_only=False,
        )
        assert argv[-1] == "--yes"

    def test_targets_pin_document_script(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            plan_only=True,
        )
        assert str(SCRIPTS_DIR / "pin_document.py") in argv

    def test_all_flag_is_present(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            plan_only=True,
        )
        assert "--all" in argv

    def test_required_flags_present(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            plan_only=True,
        )
        joined = " ".join(argv)
        assert "--dataset" in joined
        assert "--org" in joined
        assert "--action" in joined
        assert "--version" in joined
        assert "--document-dir" in joined

    def test_invalid_org_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="org"):
            build_pin_argv(
                document_dir=tmp_path,
                dataset="d",
                org="bad/org!",
                action="act",
                trusted_version="1.0.0",
                plan_only=True,
            )

    def test_invalid_action_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="action"):
            build_pin_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="bad action!",
                trusted_version="1.0.0",
                plan_only=True,
            )

    def test_invalid_version_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="trusted_version"):
            build_pin_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="act",
                trusted_version="bad version!",
                plan_only=True,
            )

    def test_nonexistent_document_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="not a directory"):
            build_pin_argv(
                document_dir=tmp_path / "nonexistent",
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                plan_only=True,
            )

    def test_optional_max_documents(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            plan_only=True,
            max_documents=50,
        )
        assert "--max-documents" in argv
        assert "50" in argv

    def test_max_documents_out_of_range_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError):
            build_pin_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                plan_only=True,
                max_documents=2000,
            )

    def test_optional_glob(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            plan_only=True,
            glob="*.pdf",
        )
        assert "--glob" in argv
        assert "*.pdf" in argv

    def test_glob_with_path_separator_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError):
            build_pin_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                plan_only=True,
                glob="sub/dir/*.pdf",
            )

    def test_dataset_allows_space(self, tmp_path: Path) -> None:
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="my dataset name",
            org="org",
            action="act",
            trusted_version="1.0.0",
            plan_only=True,
        )
        assert "my dataset name" in argv

    def test_document_dir_is_resolved(self, tmp_path: Path) -> None:
        """The argv carries the RESOLVED absolute path."""
        argv = build_pin_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            plan_only=True,
        )
        idx = argv.index("--document-dir")
        assert Path(argv[idx + 1]).is_absolute()


# ---------------------------------------------------------------------------
# build_verify_argv (T-02.1.2)
# ---------------------------------------------------------------------------


class TestBuildVerifyArgv:
    def test_plan_only_ends_in_plan(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        assert argv[-1] == "--plan"

    def test_run_mode_ends_in_yes(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="my-dataset",
            org="org-001",
            action="act-001",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=False,
        )
        assert argv[-1] == "--yes"

    def test_targets_verify_document_script(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        assert str(SCRIPTS_DIR / "verify_document.py") in argv

    def test_all_flag_is_present(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        assert "--all" in argv

    def test_required_flags_present(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        joined = " ".join(argv)
        assert "--dataset" in joined
        assert "--version" in joined  # the new/candidate version
        assert "--document-dir" in joined

    def test_trusted_version_and_action_included(self, tmp_path: Path) -> None:
        """Both must be explicit so the script picks the right pin set."""
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act-id",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        assert "--trusted-version" in argv
        assert "--action" in argv

    def test_same_version_raises(self, tmp_path: Path) -> None:
        """Comparing a version to itself cannot detect a regression."""
        with pytest.raises(JobRejectedError, match="same"):
            build_verify_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                candidate_version="1.0.0",
                plan_only=True,
            )

    def test_invalid_candidate_version_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="candidate_version"):
            build_verify_argv(
                document_dir=tmp_path,
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                candidate_version="bad version!",
                plan_only=True,
            )

    def test_nonexistent_document_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobRejectedError, match="not a directory"):
            build_verify_argv(
                document_dir=tmp_path / "nonexistent",
                dataset="d",
                org="org",
                action="act",
                trusted_version="1.0.0",
                candidate_version="2.0.0",
                plan_only=True,
            )

    def test_optional_max_documents(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
            max_documents=100,
        )
        assert "--max-documents" in argv
        assert "100" in argv

    def test_document_dir_is_resolved(self, tmp_path: Path) -> None:
        argv = build_verify_argv(
            document_dir=tmp_path,
            dataset="d",
            org="org",
            action="act",
            trusted_version="1.0.0",
            candidate_version="2.0.0",
            plan_only=True,
        )
        idx = argv.index("--document-dir")
        assert Path(argv[idx + 1]).is_absolute()
