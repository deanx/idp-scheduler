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
