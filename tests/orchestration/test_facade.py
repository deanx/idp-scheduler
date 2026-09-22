"""T-01.4.1: `run_eval(action_id, version, run_name) -> int` facade.

This slice builds ONLY the pre-run entry checks (ADR-0004 kickoff,
S-01.4-KICKOFF.md "Do NOT build the run loop"): `load_dotenv()` first
(INV-05), fail-closed credential VALIDATION with no client construction
(NFR N6, DEBT-30 -- see `bootstrap.py`), and
run-id/experiment-name composition (T-01.4.13, DEBT-19). The per-document
run loop is T-01.4.2 onward and does not exist yet -- `run_eval` raises
`NotImplementedError` once the pre-run checks pass, which is this
slice's honest, explicit boundary.
"""

from __future__ import annotations

import json
import logging

import pytest

from idp_regression.orchestration import facade
from idp_regression.orchestration.bootstrap import MissingCredentialError
from idp_regression.orchestration.facade import run_eval


def _set_all_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "distinctive-pub-9f3a")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "distinctive-secret-NOT-A-REAL-KEY-2c71")
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.setenv("IDP_CLIENT_ID", "distinctive-client-id-7b1e")
    monkeypatch.setenv("IDP_CLIENT_SECRET", "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c")
    monkeypatch.setenv("IDP_REGION", "us-east")
    monkeypatch.setenv("IDP_ORG_ID", "org-123")


def _disable_dotenv_file_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Isolation: point cwd at an empty tmp dir so a real ambient `.env`
    (present in this dev checkout, gitignored, with real credentials)
    can never leak into these tests -- the tests own their env fully via
    monkeypatch.setenv/delenv."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]


# --- INV-05: load_dotenv() ordering -----------------------------------


def test_run_eval_calls_load_dotenv_before_validating_platform_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []
    monkeypatch.setattr(facade, "load_dotenv", lambda: call_order.append("load_dotenv"))

    def fake_validate_platform_credentials() -> None:
        call_order.append("validate_platform_credentials")
        raise MissingCredentialError("LANGFUSE_HOST")

    monkeypatch.setattr(
        facade, "validate_platform_credentials", fake_validate_platform_credentials
    )

    run_eval("action", "version", "run")

    assert call_order == ["load_dotenv", "validate_platform_credentials"]


# --- NFR N6 / DEBT-30: fail-closed on missing platform credential -----


def test_run_eval_returns_nonzero_and_names_the_missing_platform_var(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    def _fail_if_called() -> None:
        raise AssertionError("make_idp_adapter must not be called: zero network calls (N6)")

    monkeypatch.setattr(facade, "make_idp_adapter", _fail_if_called)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly"
        )

    assert exit_code != 0
    assert "LANGFUSE_HOST" in caplog.text
    assert "distinctive-pub-9f3a" not in caplog.text
    assert "distinctive-secret-NOT-A-REAL-KEY-2c71" not in caplog.text


def test_run_eval_returns_nonzero_when_a_platform_var_is_set_but_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Atchim review C-1 (2026-09-21, reproduced live): a required var
    set to the EMPTY string must be rejected exactly like an absent one
    -- N6 is defeated if only presence, not non-emptiness, is checked."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")

    def _fail_if_called() -> None:
        raise AssertionError("make_idp_adapter must not be called: zero network calls (N6)")

    monkeypatch.setattr(facade, "make_idp_adapter", _fail_if_called)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "LANGFUSE_PUBLIC_KEY" in caplog.text


# --- NFR N6: fail-closed on missing IDP credential ---------------------


def test_run_eval_returns_nonzero_and_names_the_missing_idp_var(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("IDP_CLIENT_ID", raising=False)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly"
        )

    assert exit_code != 0
    assert "IDP_CLIENT_ID" in caplog.text
    assert "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c" not in caplog.text


def test_run_eval_returns_nonzero_when_idp_statuses_are_configured_inconsistently(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Required finding (Atchim gate, live-reproduced 2026-09-21):
    `MuleSoftIDPAdapter.__init__` raises a raw `ValueError` when
    `success_statuses` is not a subset of `terminal_statuses`
    (`adapter/idp_client.py:109`) -- `facade.py`'s
    `except (RuntimeError, IDPConfigurationError)` did not catch it, so
    an uncaught traceback escaped `main()` instead of a controlled
    non-zero return, breaking the `-> int` / ADR-0004 exit-code
    contract. `IDP_SUCCESS_STATUSES=DONE` / `IDP_TERMINAL_STATUSES=SUCCEEDED`
    reproduces it through the REAL facade (no `make_idp_adapter`
    monkeypatch), the same way `test_cli.py`'s C5 test exercises the real
    facade rather than a stubbed one."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setenv("IDP_SUCCESS_STATUSES", "DONE")
    monkeypatch.setenv("IDP_TERMINAL_STATUSES", "SUCCEEDED")

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0


def test_run_eval_sanitizes_the_caught_idp_configuration_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Reviewer suggestion (2026-09-21): `facade.py`'s
    `make_idp_adapter()` except-block logs `str(exc)` unsanitized --
    the one untrusted-ish boundary in the batch not routed through
    `sanitize_for_log`. Today's messages only name a variable, but the
    boundary should not rely on that staying true. Pin it with a message
    containing a quote and an embedded fake log line; the raw text must
    never appear unescaped in the log."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    hostile_message = 'boom" forged="1'

    def _raise_hostile(*args: object, **kwargs: object) -> None:
        raise RuntimeError(hostile_message)

    monkeypatch.setattr(facade, "make_idp_adapter", _raise_hostile)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert hostile_message not in caplog.text
    assert json.dumps(hostile_message) in caplog.text


def test_run_eval_never_logs_the_idp_client_secret_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Sentinel-absence pin (INV-02): every credential value present in
    the environment is a distinctive sentinel -- none may ever appear in
    a log line emitted by `run_eval`, on ANY of its exit paths."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with caplog.at_level(logging.ERROR):
        run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert "distinctive-pub-9f3a" not in caplog.text
    assert "distinctive-client-id-7b1e" not in caplog.text
    assert "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c" not in caplog.text


# --- pre-run checks pass -> the loop boundary (T-01.4.2 onward) --------


def test_run_eval_raises_not_implemented_once_prerun_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    with pytest.raises(NotImplementedError):
        run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")


def test_run_eval_composes_the_experiment_name_before_hitting_the_loop_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")

    with caplog.at_level(logging.INFO), pytest.raises(NotImplementedError):
        run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert "nightly-01234567" in caplog.text


def test_run_eval_sanitizes_logged_values_not_just_names_them(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Mutation-catching, PER FIELD (Atchim review R-4, 2026-09-21): the
    pre-run-checks-passed log line interpolates FOUR values
    (run/experiment/action/version), each through its own
    `sanitize_for_log(...)` call. A single `'\\"' in caplog.text`
    assertion is satisfied if ANY ONE of the four is sanitized -- it does
    not catch a regression that un-sanitizes exactly one field while
    leaving the other three correct (verified: dropping ONLY the
    `run_name` wrapper survived that assertion). Assert each field's
    escaped form individually instead."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")

    action_id = "12345678-1234-1234-1234-123456789012"
    version = '1.0" forged="1'
    run_name = 'nightly" forged="1'
    experiment_name = run_name + "-01234567"

    with caplog.at_level(logging.INFO), pytest.raises(NotImplementedError):
        run_eval(action_id, version, run_name)

    assert f"run={json.dumps(run_name)}" in caplog.text
    assert f"experiment={json.dumps(experiment_name)}" in caplog.text
    assert f"action={json.dumps(action_id)}" in caplog.text
    assert f"version={json.dumps(version)}" in caplog.text
