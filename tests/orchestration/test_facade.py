"""T-01.4.1: `run_eval(action_id, version, run_name) -> int` facade.

This slice builds ONLY the pre-run entry checks (ADR-0004 kickoff,
S-01.4-KICKOFF.md "Do NOT build the run loop"): `load_dotenv()` first
(INV-05), fail-closed credential construction (NFR N6, DEBT-30), and
run-id/experiment-name composition (T-01.4.13, DEBT-19). The per-document
run loop is T-01.4.2 onward and does not exist yet -- `run_eval` raises
`NotImplementedError` once the pre-run checks pass, which is this
slice's honest, explicit boundary.
"""

from __future__ import annotations

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


def test_run_eval_calls_load_dotenv_before_constructing_the_platform_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []
    monkeypatch.setattr(facade, "load_dotenv", lambda: call_order.append("load_dotenv"))

    def fake_construct_platform() -> None:
        call_order.append("construct_platform")
        raise MissingCredentialError("LANGFUSE_HOST")

    monkeypatch.setattr(facade, "construct_platform", fake_construct_platform)

    run_eval("action", "version", "run")

    assert call_order == ["load_dotenv", "construct_platform"]


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
    """Mutation-catching: `run_name` reaches the pre-run-checks-passed log
    line. If it were interpolated raw (%s) instead of through
    `sanitize_for_log`, an embedded quote would survive unescaped --
    `sanitize_for_log` (json.dumps) always escapes it."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    with caplog.at_level(logging.INFO), pytest.raises(NotImplementedError):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            'nightly" forged="1',
        )

    assert '\\"' in caplog.text
