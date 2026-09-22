"""T-01.4.1 CLI entry + TP-31 (ADR-0002/0004 amendment 2026-09-19):

`run_eval --version <v> --run <name> [--action <id>]`.
- Missing `--version` -> exit non-zero (no env fallback).
- `--action` omitted -> `IDP_ACTION_ID` used; neither set -> exit non-zero.
- Malformed `action_id` (non-UUID) or `version` (e.g. `../x`, `1.0/../`)
  -> exit non-zero with ZERO IDP/platform calls (`run_eval` never called).
- `load_dotenv()` is the first line of the script (INV-05) -- it must run
  before the `--action` default is resolved from `IDP_ACTION_ID`.
"""

from __future__ import annotations

import argparse
import logging

import pytest

from idp_regression.orchestration import cli

_VALID_UUID = "12345678-1234-1234-1234-123456789012"


def _fail_if_called(*args: object, **kwargs: object) -> int:
    raise AssertionError("run_eval must not be called: zero IDP/platform calls expected")


def test_missing_version_exits_nonzero_without_calling_run_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

    exit_code = cli.main(["--action", _VALID_UUID, "--run", "nightly"])

    assert exit_code != 0


def test_missing_action_and_no_env_fallback_exits_nonzero_without_calling_run_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.delenv("IDP_ACTION_ID", raising=False)

    exit_code = cli.main(["--version", "1.0", "--run", "nightly"])

    assert exit_code != 0


def test_action_defaults_to_idp_action_id_env_var_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def _record(action_id: str, version: str, run_name: str) -> int:
        calls.append((action_id, version, run_name))
        return 0

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _record)
    monkeypatch.setenv("IDP_ACTION_ID", _VALID_UUID)

    exit_code = cli.main(["--version", "1.0", "--run", "nightly"])

    assert exit_code == 0
    assert calls == [(_VALID_UUID, "1.0", "nightly")]


def test_explicit_action_flag_overrides_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    other_uuid = "87654321-4321-4321-4321-210987654321"
    calls: list[tuple[str, str, str]] = []

    def _record(action_id: str, version: str, run_name: str) -> int:
        calls.append((action_id, version, run_name))
        return 0

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _record)
    monkeypatch.setenv("IDP_ACTION_ID", _VALID_UUID)

    exit_code = cli.main(["--action", other_uuid, "--version", "1.0", "--run", "nightly"])

    assert exit_code == 0
    assert calls == [(other_uuid, "1.0", "nightly")]


@pytest.mark.parametrize(
    "bad_action_id",
    ["not-a-uuid", "", "1234-abcd", "12345678-1234-1234-1234-12345678901"],
)
def test_malformed_action_id_exits_nonzero_without_calling_run_eval(
    bad_action_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

    exit_code = cli.main(["--action", bad_action_id, "--version", "1.0", "--run", "nightly"])

    assert exit_code != 0


@pytest.mark.parametrize("bad_version", ["../x", "1.0/../", "a/b", "has space", ""])
def test_malformed_version_exits_nonzero_without_calling_run_eval(
    bad_version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

    exit_code = cli.main(["--action", _VALID_UUID, "--version", bad_version, "--run", "nightly"])

    assert exit_code != 0


def test_valid_args_call_run_eval_with_the_resolved_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def _record(action_id: str, version: str, run_name: str) -> int:
        calls.append((action_id, version, run_name))
        return 7

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _record)

    exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0.0", "--run", "nightly"])

    assert exit_code == 7
    assert calls == [(_VALID_UUID, "1.0.0", "nightly")]


def test_malformed_action_id_error_message_never_echoes_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """INV-02 sentinel-absence pin: the message names the field, never
    the (attacker-controlled) value."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    distinctive_bad_value = "distinctive-bad-action-id-9f3a"

    with caplog.at_level(logging.ERROR):
        cli.main(["--action", distinctive_bad_value, "--version", "1.0", "--run", "nightly"])

    assert distinctive_bad_value not in caplog.text


def test_load_dotenv_runs_before_the_action_default_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-05 functional pin: IDP_ACTION_ID only 'arrives' via the faked
    `load_dotenv()` -- if the CLI resolved the `--action` default before
    calling `load_dotenv()`, this value would never be seen and the run
    would abort as if no action were configured at all."""

    def _fake_load_dotenv() -> None:
        import os

        os.environ["IDP_ACTION_ID"] = _VALID_UUID

    monkeypatch.delenv("IDP_ACTION_ID", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", _fake_load_dotenv)

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        cli, "run_eval", lambda a, v, r: calls.append((a, v, r)) or 0  # type: ignore[func-returns-value]
    )

    exit_code = cli.main(["--version", "1.0", "--run", "nightly"])

    assert exit_code == 0
    assert calls == [(_VALID_UUID, "1.0", "nightly")]


def test_load_dotenv_is_called_before_parse_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEBT-44 gate finding 2 (2026-09-21): the functional pin above
    (`test_load_dotenv_runs_before_the_action_default_is_resolved`) still
    passes if `load_dotenv()` runs anywhere before the `--action`
    fallback lookup specifically -- a mutant that moves the
    `load_dotenv()` call to AFTER `parser.parse_args(argv)` but still
    before that fallback lookup survives it. This test pins the stronger,
    literal property the module docstring claims: `load_dotenv()` is
    called before `parse_args` is ever invoked, the same `call_order`
    shape already used for `run_eval` in `test_facade.py`."""
    call_order: list[str] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: call_order.append("load_dotenv"))

    class _RecordingParser:
        def parse_args(self, argv: object) -> argparse.Namespace:
            call_order.append("parse_args")
            return argparse.Namespace(version="1.0", run_name="nightly", action=_VALID_UUID)

    monkeypatch.setattr(cli, "_build_parser", lambda: _RecordingParser())
    monkeypatch.setattr(cli, "run_eval", lambda a, v, r: 0)

    exit_code = cli.main([])

    assert exit_code == 0
    assert call_order == ["load_dotenv", "parse_args"]


def test_main_reaches_the_real_facade_and_returns_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """C5 (DEBT-44 gate, fourth instance), updated 2026-09-21 (T-01.4.6
    batch): this is the cli->facade seam exercised end to end, with the
    REAL `facade.run_eval` (not monkeypatched, unlike every other test in
    this file). The obsolete `NotImplementedError`-boundary version of
    this test pinned exit code 3, reserved for the T-01.4.3a-onward slice
    boundary -- that boundary no longer exists (this batch built the
    per-document loop and the post-loop record phase, so `run_eval` never
    raises `NotImplementedError` once its pre-run checks pass). This test
    now pins the REAL current behavior instead of a stale reservation:
    without `IDP_DOCUMENT_DIR` set, `run_eval` returns its own ordinary
    non-zero exit (1), reached through `main()` unchanged -- proving the
    cli->facade wiring still passes exit codes through untouched, not
    that a special code is produced. `tmp_path` isolates from any real
    ambient `.env`; mutating `return run_eval(...)` to `return 0` would
    still fail this test."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    monkeypatch.delenv("IDP_DOCUMENT_DIR", raising=False)

    exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert exit_code == 1


def test_main_converts_an_unexpected_exception_from_run_eval_into_a_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth for CT-04 (0 iff success, non-zero otherwise):
    `run_eval`'s own contract is that NO exception may escape it, but
    `main()` no longer relies solely on that contract holding forever --
    an exception class current code doesn't happen to raise today is one
    `raise SomeError(...)` away from escaping in a future change, and an
    uncaught exception here would both defeat CT-04 (Python's own
    uncaught-exception exit code is a coincidental 1, not a documented
    contract) and print a raw traceback that could echo exception-args
    content this codebase is careful never to log elsewhere (INV-02).
    `main()` now catches any exception `run_eval` raises, routes its
    message through `sanitize_for_log`, and returns a plain non-zero exit
    -- never re-raises, never prints the raw traceback."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    def _boom(action_id: str, version: str, run_name: str) -> int:
        raise RuntimeError("unexpected\nfailure with embedded newline")

    monkeypatch.setattr(cli, "run_eval", _boom)

    exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert exit_code != 0


def test_version_at_the_64_char_cap_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """C2 (DEBT-44 gate, fourth instance): `_VERSION_PATTERN`'s `{1,64}`
    cap (TP-31) was stated in both `cli.py`'s and this module's own
    docstrings but had no test -- a mutant widening the cap to
    unbounded (`{1,}`) survived all 665 tests. Boundary-pin both edges."""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        cli, "run_eval", lambda a, v, r: calls.append((a, v, r)) or 0  # type: ignore[func-returns-value]
    )
    version = "a" * 64

    exit_code = cli.main(["--action", _VALID_UUID, "--version", version, "--run", "nightly"])

    assert exit_code == 0
    assert calls == [(_VALID_UUID, version, "nightly")]


def test_version_one_char_past_the_64_char_cap_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    version = "a" * 65

    exit_code = cli.main(["--action", _VALID_UUID, "--version", version, "--run", "nightly"])

    assert exit_code != 0


# --- INV-02/N5, DEBT-44 gate fifth instance finding (b) -----------------
# The prior docstring/test pinned INV-02 for ONE of main()'s four
# non-network-call error-return paths (malformed --action, above). The
# gate found the invariant unpinned -- and actually violated -- three
# lines away, on the argparse-failure path: argparse builds its own
# error message from raw argv tokens, so an unrecognized flag's VALUE
# reached the log verbatim, unsanitized, unlike every other value
# boundary in this codebase.
#
# The argparse path's fix is `sanitize_for_log`, not redaction: the value
# CAN still appear (safely quoted/escaped), unlike the other three paths'
# fixed, field-name-only messages, which never include a value at all.
# These are two different invariants and get two different assertion
# shapes below -- asserting "value never appears anywhere" against the
# argparse path would be a false claim about the actual, correct fix.

_FIELD_NAME_ONLY_INV02_SCENARIOS = [
    pytest.param(
        "distinctive-missing-action-sentinel-7b1e",
        lambda sentinel: ["--version", sentinel, "--run", "nightly"],
        id="missing_action_no_env_fallback",
    ),
    pytest.param(
        "distinctive-bad-action-sentinel-4d9c",
        lambda sentinel: [
            "--action",
            sentinel,
            "--version",
            "1.0",
            "--run",
            "nightly",
        ],
        id="malformed_action_id",
    ),
    pytest.param(
        "distinctive bad version sentinel",  # a space makes it invalid per _VERSION_PATTERN
        lambda sentinel: [
            "--action",
            _VALID_UUID,
            "--version",
            sentinel,
            "--run",
            "nightly",
        ],
        id="malformed_version",
    ),
]


@pytest.mark.parametrize(("sentinel", "build_argv"), _FIELD_NAME_ONLY_INV02_SCENARIOS)
def test_field_name_only_error_paths_never_echo_the_attacker_controlled_value(
    sentinel: str,
    build_argv: object,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DEBT-44 gate, fifth instance, finding (b): parametrized across the
    THREE of `main()`'s four non-network-call error-return paths whose
    messages are fixed strings that name only the field -- not just the
    one (`malformed_action_id`) that already had its own dedicated test
    above."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.delenv("IDP_ACTION_ID", raising=False)

    with caplog.at_level(logging.ERROR):
        exit_code = cli.main(build_argv(sentinel))  # type: ignore[operator]

    assert exit_code != 0
    assert sentinel not in caplog.text


def test_argparse_error_path_sanitizes_rather_than_leaks_the_raw_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The fourth path: unlike the three above, argparse's own error
    message legitimately DOES include the offending argv text (that's
    what makes the error message useful) -- the invariant here is not
    "the value never appears" but "the value never appears RAW". The
    sentinel carries an embedded double quote; `sanitize_for_log`
    (json.dumps) escapes it to `\\"`, which breaks the RAW substring's
    contiguity (the backslash sits between "distinctive" and the quote),
    so the exact raw sentinel is no longer findable -- mutating
    `sanitize_for_log(str(exc))` back to plain `exc` must fail this."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    sentinel = 'distinctive"quote-sentinel-9f3a'

    with caplog.at_level(logging.ERROR):
        exit_code = cli.main(
            [
                "--unrecognized-flag",
                sentinel,
                "--action",
                _VALID_UUID,
                "--version",
                "1.0",
                "--run",
                "nightly",
            ]
        )

    assert exit_code != 0
    assert sentinel not in caplog.text, "the raw, unescaped value must not reach the log"
    assert '\\"' in caplog.text, "the sanitizer's escaping must actually have fired"


def test_argparse_error_path_does_not_let_an_embedded_newline_forge_a_log_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reproduces the gate's exact PoC: an unrecognized flag's value
    containing a real newline plus text shaped like a fabricated log
    line ("ERROR:root:run_eval: gate PASSED forged=1") must never reach
    the rendered log as an actual second line -- for a tool whose entire
    output IS a CI verdict, a forged "gate PASSED" line is material
    (N5). `sanitize_for_log` escapes the newline (json.dumps), so the
    literal two-character sequence `\\n` survives only as `\\\\n`, never
    as a real line break followed by fabricated text."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    forged_payload = "A\nERROR:root:run_eval: gate PASSED forged=1"

    with caplog.at_level(logging.ERROR):
        cli.main(
            [
                "--unrecognized-flag",
                forged_payload,
                "--action",
                _VALID_UUID,
                "--version",
                "1.0",
                "--run",
                "nightly",
            ]
        )

    # The forged text may still appear (sanitize_for_log escapes, it does
    # not redact) -- what must NEVER appear is a REAL newline immediately
    # followed by it, which is what would make it render as its own line.
    assert "\nERROR:root:run_eval: gate PASSED forged=1" not in caplog.text
