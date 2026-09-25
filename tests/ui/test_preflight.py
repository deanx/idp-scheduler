"""Can this machine run a validation? Asked before anything is billed.

Without the preflight, the first thing that tells an operator
`IDP_REGION` is unset is `pin_document.py` failing -- after the upload,
after the plan was approved, and after the first extraction was charged.

The second test class is the one that matters most: **missing PLATFORM
credentials are reported just as loudly as missing IDP ones**, because
that failure mode is the expensive one. With IDP configured and the
platform not, a run reaches MuleSoft, spends quota on every document,
and only then fails when the goldens cannot be provisioned.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from idp_regression.ui import preflight, workspace

ALL_VARIABLES = preflight.IDP_VARIABLES + preflight.PLATFORM_VARIABLES


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """No credentials anywhere, and a workspace with no `.env` -- so a
    developer machine's real `.env` cannot make these pass."""
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    for name in ALL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path / "no-repo")
    yield tmp_path
    workspace.set_workspace(previous)


def set_all(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> None:
    for name in names:
        monkeypatch.setenv(name, "configured")


class TestBlocking:
    def test_nothing_configured_blocks_the_run(self, clean_env: Path) -> None:
        report = preflight.check()
        assert report["can_run_validation"] is False
        assert len(report["blockers"]) == 2

    def test_missing_idp_credentials_block(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_all(monkeypatch, preflight.PLATFORM_VARIABLES)
        report = preflight.check()
        assert report["can_run_validation"] is False
        assert "IDP credentials are missing" in report["blockers"][0]
        assert "IDP_REGION" in report["blockers"][0]

    def test_missing_platform_credentials_block_and_say_why_it_is_worse(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IDP present + platform absent is the expensive failure: the
        run extracts every document, THEN fails. The message has to say
        so, or an operator reads it as a warning and proceeds."""
        set_all(monkeypatch, preflight.IDP_VARIABLES)
        report = preflight.check()
        assert report["can_run_validation"] is False
        assert "SPEND" in report["blockers"][0]

    def test_everything_configured_clears_the_run(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_all(monkeypatch, ALL_VARIABLES)
        report = preflight.check()
        assert report["can_run_validation"] is True
        assert report["blockers"] == []

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_a_whitespace_only_credential_is_absent_not_present(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch, blank: str
    ) -> None:
        """`IDP_CLIENT_SECRET="   "` passing a presence check is a bug
        this repo has already fixed once elsewhere -- it fails at the
        API, not at the check."""
        set_all(monkeypatch, ALL_VARIABLES)
        monkeypatch.setenv("IDP_CLIENT_SECRET", blank)
        report = preflight.check()
        assert report["can_run_validation"] is False
        assert report["idp"]["IDP_CLIENT_SECRET"] is False


class TestDisclosure:
    def test_it_reports_presence_by_name_and_never_a_value(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This console binds to loopback and has no authentication. A
        page that echoed a client secret back would turn "readable by
        this OS account" into "readable by anything that can reach the
        port" (INV-02)."""
        for name in ALL_VARIABLES:
            monkeypatch.setenv(name, f"SECRET-VALUE-{name}")
        report = preflight.check()
        assert "SECRET-VALUE" not in str(report)
        assert set(report["idp"]) == set(preflight.IDP_VARIABLES)
        assert all(value is True for value in report["idp"].values())


class TestWhereThingsGo:
    def test_it_names_the_directories_a_job_will_write(
        self, clean_env: Path
    ) -> None:
        """So an operator can see they are the same directories the Runs
        and Pins pages read -- the cwd bug, made visible."""
        report = preflight.check()
        assert report["workspace"] == str(clean_env)
        assert report["writes"]["run_artifacts"] == str(workspace.artifact_dir())
        assert report["writes"]["pin_store"] == str(workspace.pin_store_dir())

    def test_it_says_where_it_looked_for_an_env_file(self, clean_env: Path) -> None:
        """"Nothing is set" and "set somewhere I did not look" are
        different problems with the same symptom."""
        report = preflight.check()
        assert report["env_file"] is None
        assert len(report["env_searched"]) == 2


def test_the_variable_lists_are_the_ones_a_run_actually_enforces() -> None:
    """Not a copy: the preflight imports both lists from the packages
    that enforce them. A rename there propagates here automatically
    instead of leaving a trusted check passing on a machine that cannot
    run."""
    from idp_regression import adapter, platform

    assert preflight.IDP_VARIABLES is adapter.REQUIRED_ENV_VARS
    assert preflight.PLATFORM_VARIABLES is platform.REQUIRED_ENV_VARS


def test_the_platform_list_is_what_the_orchestrators_own_check_uses() -> None:
    """`bootstrap.validate_platform_credentials` is what actually aborts
    a run; the preflight must be asking the same question. Asserted by
    BEHAVIOUR rather than by reading a constant: the check either
    refuses the same missing variable or it does not."""
    import pytest as _pytest

    from idp_regression.orchestration import bootstrap

    with _pytest.MonkeyPatch.context() as patch:
        for name in preflight.PLATFORM_VARIABLES:
            patch.setenv(name, "configured")
        bootstrap.validate_platform_credentials()  # all present: no raise

        patch.delenv(preflight.PLATFORM_VARIABLES[0])
        with _pytest.raises(bootstrap.MissingCredentialError):
            bootstrap.validate_platform_credentials()
