"""INV-05: `load_dotenv()` must run before any credential is read, and it
must read **only** the `.env` in the caller's current working directory.

⚠️ **Corrected 2026-09-21 (gate finding #10 — overclaim). The previous
docstring here was factually false** and had been since `c449b65`: it
said `python-dotenv` "is not (yet) a declared runtime dependency
(checked: absent from `pyproject.toml` and `uv.lock`)" and that the
subject under test "is therefore a minimal, dependency-free `.env`
parser". Both were true when the batch shipped and stopped being true in
the same commit. `python-dotenv>=1.2.3` is declared and locked, the
hand-rolled parser is deleted, and `dotenv_support.load_dotenv()`
delegates to the library. **`fb0653b` corrected the source docstring and
left this one behind** — the same lagging-record class this project has
now corrected four times, and it is worse here because it mislabels live
tests.

**What these tests actually pin, stated honestly per category:**

* **Our contract** — the cwd resolution (`_load_dotenv` receives
  `Path.cwd()/".env"`, nothing else), `override=False` ordering, and a
  missing file being a no-op. These are the load-bearing ones.
* **`python-dotenv`'s parsing behaviour**, NOT our contract — blank
  lines, comments, surrounding quotes, malformed lines. They were
  written against the hand-rolled parser and now assert the vendor's
  behaviour. Kept deliberately as characterisation tests (they would
  catch a surprising change on a version bump), but they must not be read
  as pinning anything this project decided.

**Why the cwd pin is load-bearing and not defensive.** A bare
`load_dotenv()` calls `find_dotenv()`, which walks up from the *calling
frame's file* — i.e. the source tree — and never consults the operator's
cwd on the production path. Measured: with cwd in one tree and a `.env`
in its parent, a bare call still resolved to the **source tree's** `.env`,
reaching past both. That is the REG-07/REG-10 family (*a credential
reaching a place the caller did not pick*), and it is what pulled this
checkout's real credential file into the test process when the library
swap landed — nine tests broke, which is how it was caught.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from idp_regression.orchestration import dotenv_support
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


def test_only_the_cwd_env_file_is_read_never_an_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate finding #5: the `Path.cwd()/".env"` pin had NO test.

    A `usecwd=True` mutant — which walks *up* from the cwd — passed the
    whole suite green, silently reintroducing the ancestor-walk this
    module exists to remove. This test kills it: the `.env` sits in the
    PARENT of the cwd, so any implementation that walks up finds it and
    any implementation pinned to the cwd does not.
    """
    monkeypatch.delenv("ANCESTOR_LEAK_PROBE", raising=False)
    (tmp_path / ".env").write_text("ANCESTOR_LEAK_PROBE=leaked\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)

    load_dotenv()

    assert "ANCESTOR_LEAK_PROBE" not in os.environ


def test_load_dotenv_is_called_with_the_cwd_path_and_no_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half of the pin, and the true tripwire.

    The ancestor test above proves no *ancestor* is read; this proves the
    path handed to `python-dotenv` is exactly the cwd's `.env` and that no
    search was delegated to it at all. Together they kill both the
    bare-call and the `usecwd=True` variants.
    """
    monkeypatch.chdir(tmp_path)
    received: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> bool:
        received.update(kwargs)
        return True

    monkeypatch.setattr(dotenv_support, "_load_dotenv", _spy)
    load_dotenv()

    assert received["dotenv_path"] == tmp_path / ".env"
    assert received["override"] is False
