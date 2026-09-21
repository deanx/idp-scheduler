"""INV-05: `load_dotenv()` must run before any credential is read.

`python-dotenv` is not (yet) a declared runtime dependency of this
project (checked: absent from `pyproject.toml` and `uv.lock`, and
`uv run python -c "import dotenv"` fails). Adding it is a `pyproject.toml`
edit, and this session's scope wall reserves that file for the
concurrent T-01.4.10 agent — see DEBT-56. `load_dotenv()` here is
therefore a minimal, dependency-free `.env` parser that reproduces the
one behaviour this codebase actually relies on: read `KEY=VALUE` lines
from a `.env` file and set `os.environ[KEY]` for any key not already
present, WITHOUT overriding an already-set var (matches python-dotenv's
`override=False` default, and the ADR-0004 flow's ordering intent: real
env/CI-injected vars win over `.env` file contents).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from idp_regression.orchestration.dotenv_support import load_dotenv


def test_load_dotenv_sets_a_var_from_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOTENV_SUPPORT_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DOTENV_SUPPORT_TEST_VAR=hello\n")
    monkeypatch.chdir(tmp_path)

    load_dotenv()

    assert os.environ["DOTENV_SUPPORT_TEST_VAR"] == "hello"


def test_load_dotenv_never_overrides_an_already_set_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOTENV_SUPPORT_TEST_VAR", "from-real-env")
    env_file = tmp_path / ".env"
    env_file.write_text("DOTENV_SUPPORT_TEST_VAR=from-dotenv-file\n")
    monkeypatch.chdir(tmp_path)

    load_dotenv()

    assert os.environ["DOTENV_SUPPORT_TEST_VAR"] == "from-real-env"


def test_load_dotenv_skips_blank_lines_and_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOTENV_SUPPORT_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n"
        "# a comment line\n"
        "  \n"
        "# DOTENV_SUPPORT_TEST_VAR=commented-out\n"
        "DOTENV_SUPPORT_TEST_VAR=real-value\n"
    )
    monkeypatch.chdir(tmp_path)

    load_dotenv()

    assert os.environ["DOTENV_SUPPORT_TEST_VAR"] == "real-value"


def test_load_dotenv_strips_surrounding_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOTENV_SUPPORT_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DOTENV_SUPPORT_TEST_VAR="quoted value"\n')
    monkeypatch.chdir(tmp_path)

    load_dotenv()

    assert os.environ["DOTENV_SUPPORT_TEST_VAR"] == "quoted value"


def test_load_dotenv_is_not_an_error_when_no_env_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    load_dotenv()  # must not raise


def test_load_dotenv_ignores_a_malformed_line_without_an_equals_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOTENV_SUPPORT_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("this line has no equals sign\nDOTENV_SUPPORT_TEST_VAR=ok\n")
    monkeypatch.chdir(tmp_path)

    load_dotenv()  # must not raise

    assert os.environ["DOTENV_SUPPORT_TEST_VAR"] == "ok"
