"""Item 1 (2026-09-23, live-reproduced): NO logging handler was ever
configured anywhere in this codebase -- `logging.basicConfig()` was never
called in `cli.py` or `facade.py`, so every `logger.info(...)`/
`logger.error(...)` line (run-start, run-end, per-document, every abort)
went to `logging.lastResort` -- i.e. nowhere. Two live end-to-end runs
today produced NO log output at all beyond the wrapper's own echo and the
exit code, despite `docs/qa/NFR-01.md` marking `Observability: REQUIRED`
(a marker `CLAUDE.md ## Rigor` states is NOT waived by the `prototype`
profile).

These tests deliberately do NOT use `caplog` -- `caplog` attaches its own
handler directly to the root logger, which is why every existing test in
this suite that asserts on `caplog.text` passed even with ZERO handlers
configured in production. That is exactly the blind spot this bug lived
in. These tests instead assert on `capsys` (real `sys.stderr`), which is
what an actual CI log reader sees.

`cli.configure_logging()` attaches ONE `StreamHandler(sys.stderr)` to the
`idp_regression` PACKAGE logger (never the root logger -- `run_eval` is
also an importable library facade, and a library must not hijack its
host's root logger). It does not set `propagate = False`, so `caplog`
(which listens at the root) keeps working for every other test in this
codebase unchanged -- confirmed by the whole suite staying green.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from idp_regression.orchestration import cli

_PACKAGE_LOGGER_NAME = "idp_regression"


@pytest.fixture(autouse=True)
def _restore_package_logger_state() -> Iterator[None]:
    """Isolation: `configure_logging()` mutates process-global logging
    state (handlers + level on the `idp_regression` logger). Snapshot and
    restore around every test in this file so no state leaks into other
    test modules relying on `caplog` (943+ of them)."""
    pkg_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    original_handlers = list(pkg_logger.handlers)
    original_level = pkg_logger.level
    yield
    pkg_logger.handlers[:] = original_handlers
    pkg_logger.setLevel(original_level)


def test_configure_logging_attaches_a_formatted_stderr_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.configure_logging()

    logging.getLogger(f"{_PACKAGE_LOGGER_NAME}.orchestration.test_probe").info(
        "distinctive-run-start-marker-7a3f"
    )

    captured = capsys.readouterr()
    assert "distinctive-run-start-marker-7a3f" in captured.err
    # format includes the logger name and a level (CI logs read days later
    # need to know WHERE a line came from and how severe it was)
    assert "idp_regression.orchestration.test_probe" in captured.err
    assert "INFO" in captured.err
    assert captured.out == "", "logs must go to stderr, not stdout (a future --json contract)"


def test_configure_logging_never_touches_the_root_logger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`run_eval` is a public function other callers may import directly
    (bypassing the CLI) -- configuring logging at the CLI boundary must
    not hijack the root logger of whatever process embeds it."""
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    level_before = root_logger.level

    cli.configure_logging()

    assert list(root_logger.handlers) == handlers_before
    assert root_logger.level == level_before


def test_configure_logging_is_idempotent_not_accumulating_handlers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Calling it twice (e.g. two `main()` invocations in one process,
    as this whole test file does) must not double-emit every line."""
    cli.configure_logging()
    cli.configure_logging()

    logging.getLogger(f"{_PACKAGE_LOGGER_NAME}.orchestration.test_probe").info(
        "distinctive-once-only-marker-2b91"
    )

    captured = capsys.readouterr()
    assert captured.err.count("distinctive-once-only-marker-2b91") == 1


def test_log_level_env_var_raises_the_floor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The env var adjusts WHERE the floor sits, never WHAT gets
    sanitized/redacted before it reaches the logger -- every existing
    INV-02 sanitize_for_log call site is upstream of this, untouched."""
    monkeypatch.setenv("IDP_REGRESSION_LOG_LEVEL", "ERROR")

    cli.configure_logging()

    probe = logging.getLogger(f"{_PACKAGE_LOGGER_NAME}.orchestration.test_probe")
    probe.info("distinctive-suppressed-info-marker-5c10")
    probe.error("distinctive-visible-error-marker-9e44")

    captured = capsys.readouterr()
    assert "distinctive-suppressed-info-marker-5c10" not in captured.err
    assert "distinctive-visible-error-marker-9e44" in captured.err


def test_default_level_is_info_without_the_env_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("IDP_REGRESSION_LOG_LEVEL", raising=False)

    cli.configure_logging()

    logging.getLogger(f"{_PACKAGE_LOGGER_NAME}.orchestration.test_probe").info(
        "distinctive-default-info-marker-4d02"
    )

    assert "distinctive-default-info-marker-4d02" in capsys.readouterr().err


def test_main_error_line_reaches_real_stderr_not_only_caplog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression, reproduced directly: before this fix, this
    assertion failed -- `captured.err` was empty even though `caplog`
    (which attaches its own handler to the root logger, bypassing
    whatever this codebase configures) saw the line fine. `main()` is the
    real CLI entry point; this is what a CI log reader actually sees."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    def _fail_if_called(*args: object, **kwargs: object) -> int:
        raise AssertionError("run_eval must not be called: malformed action id")

    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

    exit_code = cli.main(
        [
            "--org",
            "org-test-0000",
            "--action",
            "not-a-uuid",
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "d",
        ]
    )

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "run_eval: --action is not a valid UUID" in captured.err


def test_main_run_start_line_from_the_real_facade_logger_reaches_real_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proves the fix covers every module under the package, not just
    `cli.py`'s own logger -- `facade.py`'s logger (`idp_regression.
    orchestration.facade`) is a different, child logger, and must
    propagate up to the same handler `configure_logging()` installs on
    the `idp_regression` package logger."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    def _stub_run_eval(*args: object, **kwargs: object) -> int:
        logging.getLogger("idp_regression.orchestration.facade").info(
            "run_eval: pre-run checks passed run=nightly distinctive-marker-8f21"
        )
        return 0

    monkeypatch.setattr(cli, "run_eval", _stub_run_eval)

    exit_code = cli.main(
        [
            "--org",
            "org-test-0000",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "d",
        ]
    )

    assert exit_code == 0
    assert "distinctive-marker-8f21" in capsys.readouterr().err


def test_cli_logger_name_is_stable_under_python_dash_m_invocation() -> None:
    """`CLAUDE.md ## Commands` documents the real entry point as `python
    -m idp_regression.orchestration.cli ...` -- under `-m`, Python's
    `runpy` imports the target module as `__main__`, so a logger built
    from `logging.getLogger(__name__)` at MODULE level would be named
    `"__main__"`, not `"idp_regression.orchestration.cli"` -- orphaned
    from the `idp_regression` package hierarchy `configure_logging()`
    attaches its handler to, so every line this module logs would fall
    through to `logging.lastResort` again, silently, ONLY when run the
    documented way (a plain `import` from a test or another module keeps
    `__name__` correct, which is why this class of bug survives unit
    tests and is only caught by an end-to-end subprocess run like the one
    below)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "idp_regression.orchestration.cli",
            "--org",
            "org-demo",
            "--action",
            "not-a-uuid",
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "d",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "run_eval: --action is not a valid UUID" in result.stderr
    assert "idp_regression.orchestration.cli" in result.stderr, (
        "the logger name in the output must be the package-qualified name, "
        "not '__main__' -- otherwise this line never reached the handler "
        "configure_logging() installs, and only appeared here by the "
        "unrelated logging.lastResort fallback"
    )


def test_check_versions_module_logger_name_is_not___main__() -> None:
    """Cheap unit-level pin, alongside the subprocess test below: a plain
    `import` keeps `__name__` correct (which is why the `-m` bug survives
    unit tests and needs the subprocess reproduction), but the logger
    object's OWN name is hardcoded now and must never regress back to
    `logging.getLogger(__name__)`."""
    from idp_regression.orchestration import check_versions

    assert check_versions.logger.name == "idp_regression.orchestration.check_versions"


def test_watch_module_logger_name_is_not___main__() -> None:
    from idp_regression.orchestration import watch

    assert watch.logger.name == "idp_regression.orchestration.watch"


def test_check_versions_logger_name_is_stable_under_python_dash_m_invocation(
    tmp_path: Path,
) -> None:
    """Mirrors `test_cli_logger_name_is_stable_under_python_dash_m_invocation`
    above -- the identical `__main__`-orphaning bug, fixed the same way, in
    `check_versions.py`. Live-reproduced 2026-09-23: `python -m
    idp_regression.orchestration.check_versions` produced
    `INFO:__main__:...` instead of the configured format. Triggered via the
    state-file-inside-repo refusal so the subprocess never needs IDP
    credentials or network access."""
    repo_root = Path(__file__).resolve().parents[2]
    inside_repo_state_file = repo_root / "docs" / "state" / "check-versions-state.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "idp_regression.orchestration.check_versions",
            "--org",
            "org1",
            "--action",
            "12345678-1234-1234-1234-123456789012",
            "--dataset",
            "ds1",
            "--state-file",
            str(inside_repo_state_file),
            "--max-probes-per-tick",
            "20",
            "--max-probes-per-sweep",
            "20",
            "--patch-lookahead",
            "3",
            "--minor-lookahead",
            "3",
            "--major-lookahead",
            "1",
            "--sweep-every-n-ticks",
            "0",
            "--max-indeterminate-ticks",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "must be OUTSIDE the repository" in result.stderr
    assert "idp_regression.orchestration.check_versions" in result.stderr, (
        "the logger name in the output must be the package-qualified name, "
        "not '__main__'"
    )
