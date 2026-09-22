"""T-01.4.1 CLI entry + TP-31 (ADR-0002/0004 amendment 2026-09-19;
tightened 2026-09-22 -- user decision):

`run_eval --version <v> --run <name> --action <id> --dataset <name>`.
- Missing `--version`, `--action` or `--dataset` -> exit non-zero (no env
  fallback for any of the three; argparse's own `required=True`).
- Malformed `action_id` (non-UUID) or `version` (e.g. `../x`, `1.0/../`)
  -> exit non-zero with ZERO IDP/platform calls (`run_eval` never called).
- A whitespace-only `--dataset` value is rejected the same way (`.strip()`
  fail-closed, mirroring `bootstrap.py`'s N6 credential guard).
- `load_dotenv()` is the first line of the script (INV-05) -- it must run
  before `parse_args`, so any credential a later step reads is present.
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


def test_missing_action_exits_via_the_argparse_required_path_not_calling_run_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tightened 2026-09-22 (user decision): `--action` is required, no
    env fallback -- omitting it is now an argparse `required=True`
    failure (exit 2), same shape as omitting `--version`. Pinned as
    EXACTLY 2, not merely nonzero: `action_id = None` would ALSO fail
    the downstream UUID check (exit 1) if `required=True` were quietly
    dropped, so a plain `!= 0` assertion can't tell `required=True` apart
    from `required=False` -- exact-code-2 is the mutation-sensitive
    assertion (confirmed live: reverting to `required=False` here still
    exits nonzero, via the UUID check, but never with code 2)."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.delenv("IDP_ACTION_ID", raising=False)
    monkeypatch.setenv("GOLDEN_DATASET_NAME", "idp-regression-golden")

    exit_code = cli.main(["--version", "1.0", "--run", "nightly", "--dataset", "d"])

    assert exit_code == 2


def test_idp_action_id_env_var_is_no_longer_read_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tightened 2026-09-22 (user decision) mutation pin: setting
    `IDP_ACTION_ID` in the environment must NOT make an omitted
    `--action` succeed -- the env fallback is deleted, not merely
    shadowed by the flag. Exact-code-2, see the docstring above."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.setenv("IDP_ACTION_ID", _VALID_UUID)
    monkeypatch.setenv("GOLDEN_DATASET_NAME", "idp-regression-golden")

    exit_code = cli.main(["--version", "1.0", "--run", "nightly"])

    assert exit_code == 2


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
    calls: list[tuple[str, str, str, str]] = []

    def _record(action_id: str, version: str, run_name: str, dataset_name: str) -> int:
        calls.append((action_id, version, run_name, dataset_name))
        return 7

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _record)

    exit_code = cli.main(
        [
            "--action",
            _VALID_UUID,
            "--version",
            "1.0.0",
            "--run",
            "nightly",
            "--dataset",
            "idp-regression-golden",
        ]
    )

    assert exit_code == 7
    assert calls == [(_VALID_UUID, "1.0.0", "nightly", "idp-regression-golden")]


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


def test_load_dotenv_is_called_before_parse_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEBT-44 gate finding 2 (2026-09-21; still applies after the
    2026-09-22 required-flags tightening, since `load_dotenv()` still
    must precede any credential read downstream of a successful parse):
    a mutant that moves the `load_dotenv()` call to AFTER
    `parser.parse_args(argv)` must be caught. This test pins the
    literal property the module docstring claims: `load_dotenv()` is
    called before `parse_args` is ever invoked, the same `call_order`
    shape already used for `run_eval` in `test_facade.py`."""
    call_order: list[str] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: call_order.append("load_dotenv"))

    class _RecordingParser:
        def parse_args(self, argv: object) -> argparse.Namespace:
            call_order.append("parse_args")
            return argparse.Namespace(
                version="1.0",
                run_name="nightly",
                action=_VALID_UUID,
                dataset="idp-regression-golden",
            )

    monkeypatch.setattr(cli, "_build_parser", lambda: _RecordingParser())
    monkeypatch.setattr(cli, "run_eval", lambda a, v, r, d: 0)

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

    exit_code = cli.main(
        [
            "--action",
            _VALID_UUID,
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "idp-regression-golden",
        ]
    )

    assert exit_code == 1


def test_main_never_escapes_when_load_dotenv_itself_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`load_dotenv()` sits BEFORE `main()`'s own try/except (which only
    wraps the `run_eval(...)` call) -- an `OSError` from an unreadable or
    undecodable `.env` (a real trigger: a permission-denied or
    non-UTF-8 path) used to escape `main()` entirely. Worse than the
    facade-level gap: this is the outermost caller, so an uncaught
    exception here means Python prints a RAW TRACEBACK containing the
    `.env` path straight to stderr -- a live INV-02 path-disclosure, not
    merely a broken `-> int` contract. Reproduced with a sentinel `.env`
    path in the message; it must never escape and never appear in the
    logs."""
    sentinel = "/etc/secret/.env-SEKRIT-path-should-never-be-logged"

    def _boom() -> None:
        raise OSError(f"[Errno 13] Permission denied: '{sentinel}'")

    monkeypatch.setattr(cli, "load_dotenv", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert exit_code != 0
    assert sentinel not in caplog.text
    assert "OSError" in caplog.text


def test_main_gives_a_dotenv_load_failure_its_own_message_not_run_evals(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Atchim gate finding (2026-09-21): this used to log
    "run_eval: unexpected error" for a `.env`-loading failure -- the same
    text as a genuine `run_eval` failure below, misattributing the cause
    in triage. It must carry its own, accurate message instead."""

    def _boom() -> None:
        raise OSError("boom")

    monkeypatch.setattr(cli, "load_dotenv", _boom)

    with caplog.at_level(logging.INFO):
        cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert "cli: unexpected error loading .env" in caplog.text
    assert "run_eval: unexpected error" not in caplog.text


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

    def _boom(action_id: str, version: str, run_name: str, dataset_name: str) -> int:
        raise RuntimeError("unexpected\nfailure with embedded newline")

    monkeypatch.setattr(cli, "run_eval", _boom)

    exit_code = cli.main(
        [
            "--action",
            _VALID_UUID,
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "idp-regression-golden",
        ]
    )

    assert exit_code != 0


def test_main_never_logs_the_raw_message_of_an_unexpected_exception_from_run_eval(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C-1 follow-up (Atchim gate, 2026-09-21): `sanitize_for_log` only
    quotes/escapes (`json.dumps`) -- it does NOT redact. Routing an
    unexpected exception's `str(exc)` through it (as this fallback used
    to) still lets the raw message -- including anything sensitive it
    might carry, e.g. a platform response body -- reach the log verbatim
    aside from quoting. Now `main()`'s fallback logs only the exception's
    type name plus its last traceback frame's location (same shape as
    `run_eval`'s own catch-alls, R-2) -- never `str(exc)`."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    sentinel = "Bearer sk-lf-SEKRIT-should-never-be-logged"

    def _boom(action_id: str, version: str, run_name: str, dataset_name: str) -> int:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(cli, "run_eval", _boom)

    with caplog.at_level(logging.ERROR):
        exit_code = cli.main(
            [
                "--action",
                _VALID_UUID,
                "--version",
                "1.0",
                "--run",
                "nightly",
                "--dataset",
                "idp-regression-golden",
            ]
        )

    assert exit_code != 0
    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


def test_main_never_escapes_on_a_cancelled_error_from_run_eval(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-5 (Branca /harden, 2026-09-21): `asyncio.CancelledError` is a
    `BaseException` subclass, not an `Exception` subclass -- it slips
    through `except Exception` and escaped `main()`'s own catch-all raw."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    import asyncio

    def _boom(action_id: str, version: str, run_name: str, dataset_name: str) -> int:
        raise asyncio.CancelledError()

    monkeypatch.setattr(cli, "run_eval", _boom)

    with caplog.at_level(logging.ERROR):
        exit_code = cli.main(
            [
                "--action",
                _VALID_UUID,
                "--version",
                "1.0",
                "--run",
                "nightly",
                "--dataset",
                "idp-regression-golden",
            ]
        )

    assert exit_code != 0


def test_version_at_the_64_char_cap_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """C2 (DEBT-44 gate, fourth instance): `_VERSION_PATTERN`'s `{1,64}`
    cap (TP-31) was stated in both `cli.py`'s and this module's own
    docstrings but had no test -- a mutant widening the cap to
    unbounded (`{1,}`) survived all 665 tests. Boundary-pin both edges."""
    calls: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        cli,
        "run_eval",
        lambda a, v, r, d: calls.append((a, v, r, d)) or 0,  # type: ignore[func-returns-value]
    )
    version = "a" * 64

    exit_code = cli.main(
        [
            "--action",
            _VALID_UUID,
            "--version",
            version,
            "--run",
            "nightly",
            "--dataset",
            "idp-regression-golden",
        ]
    )

    assert exit_code == 0
    assert calls == [(_VALID_UUID, version, "nightly", "idp-regression-golden")]


def test_golden_dataset_name_env_var_is_no_longer_read_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tightened 2026-09-22 (user decision) mutation pin: setting
    `GOLDEN_DATASET_NAME` in the environment must NOT make an omitted
    `--dataset` succeed -- the A6/DEBT-48 env fallback is deleted, not
    merely shadowed by the flag (mirrors the equivalent `--action` pin
    above). Exact-code-2: a bare `!= 0` can't distinguish `required=True`
    from `required=False`, since an omitted `--dataset` also fails the
    downstream blank-after-`.strip()` check (exit 1) either way -- code 2
    is only reachable via the argparse `required=True` path."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.setenv("GOLDEN_DATASET_NAME", "env-dataset")

    exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert exit_code == 2


def test_missing_dataset_exits_via_the_argparse_required_path_not_calling_run_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tightened 2026-09-22 (user decision): `--dataset` is required, no
    env fallback -- omitting it is now an argparse `required=True`
    failure (exit 2), same shape as omitting `--action`/`--version`. See
    the exact-code-2 rationale in the test above."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)
    monkeypatch.delenv("GOLDEN_DATASET_NAME", raising=False)

    exit_code = cli.main(["--action", _VALID_UUID, "--version", "1.0", "--run", "nightly"])

    assert exit_code == 2


@pytest.mark.parametrize("blank_dataset_flag", ["", "   "])
def test_blank_dataset_flag_value_is_rejected_including_whitespace_only(
    blank_dataset_flag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N6 shape, carried over from the env-fallback guard: a
    whitespace-only `--dataset` VALUE (not env var -- there is no env
    fallback anymore) must be rejected exactly like an empty one,
    mirroring bootstrap.py's credential guard."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

    exit_code = cli.main(
        [
            "--action",
            _VALID_UUID,
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            blank_dataset_flag,
        ]
    )

    assert exit_code != 0


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
# CAN still appear (safely quoted/escaped), unlike the other two paths'
# fixed, field-name-only messages, which never include a value at all.
# These are two different invariants and get two different assertion
# shapes below -- asserting "value never appears anywhere" against the
# argparse path would be a false claim about the actual, correct fix.
#
# ⚠️ Tightened 2026-09-22 (user decision): the THIRD field-name-only
# scenario that used to live here ("missing_action_no_env_fallback") is
# retired -- `--action` is now argparse `required=True`, so omitting it
# is the argparse-failure path (covered by the tests below), not a
# manually-written field-name-only message anymore.

_FIELD_NAME_ONLY_INV02_SCENARIOS = [
    pytest.param(
        "distinctive-bad-action-sentinel-4d9c",
        lambda sentinel: [
            "--action",
            sentinel,
            "--version",
            "1.0",
            "--run",
            "nightly",
            "--dataset",
            "idp-regression-golden",
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
            "--dataset",
            "idp-regression-golden",
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
    """DEBT-44 gate, fifth instance, finding (b); tightened 2026-09-22
    (user decision): parametrized across the TWO of `main()`'s remaining
    non-network-call error-return paths whose messages are fixed strings
    that name only the field -- malformed `--action` and malformed
    `--version` (the third, "missing `--action`", is retired now that
    `--action` is argparse `required=True` -- see the module comment
    above `_FIELD_NAME_ONLY_INV02_SCENARIOS`) -- not just the one
    (`malformed_action_id`) that already had its own dedicated test
    above."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "run_eval", _fail_if_called)

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
                "--dataset",
                "idp-regression-golden",
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
                "--dataset",
                "idp-regression-golden",
            ]
        )

    # The forged text may still appear (sanitize_for_log escapes, it does
    # not redact) -- what must NEVER appear is a REAL newline immediately
    # followed by it, which is what would make it render as its own line.
    assert "\nERROR:root:run_eval: gate PASSED forged=1" not in caplog.text
