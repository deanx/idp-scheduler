"""T-01.4.1/.11/.2/.5/.6: `run_eval(action_id, version, run_name) -> int`
facade -- the pre-run chain.

This slice builds the ENTIRE pre-run chain (ADR-0004/ADR-0005 #8,
S-01.4-KICKOFF.md pinned order): `load_dotenv()` first (INV-05),
fail-closed credential VALIDATION (NFR N6, DEBT-30 -- see `bootstrap.py`),
platform construction, `get_dataset` (dataset_fetch_failed), schema-drift
(schema_drift), empty-set (empty_set), N28 structural validation
(malformed_golden), and golden_version/run-id/experiment-name composition
(T-01.4.6, T-01.4.13, DEBT-19). The per-document run loop is T-01.4.3a
onward and does not exist yet -- `run_eval` raises `NotImplementedError`
once every pre-run check passes, which is this slice's honest, explicit
boundary.
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
    monkeypatch.setenv("GOLDEN_DATASET_NAME", "idp-regression-golden")


class _FakePlatform:
    """A minimal `PlatformAdapter` double -- only `get_dataset` is used by
    the pre-run chain this batch builds. `record_run`/`mark_run_status`
    are deliberately absent: any test that reaches them is out of this
    batch's slice boundary and should fail loudly, not silently pass."""

    def __init__(self, dataset: object) -> None:
        self._dataset = dataset

    def get_dataset(self, name: str) -> object:
        return self._dataset


def _well_formed_dataset() -> dict[str, object]:
    from idp_regression.platform.schema import load_golden_schema

    return {
        "items": [
            {
                "item_id": "item-1",
                "document_id": "doc-1",
                "golden": {
                    "fields": {
                        "total": {"value": "1250.00", "type": "number", "critical": True}
                    }
                },
            }
        ],
        "expected_output_schema": load_golden_schema(),
    }


def _stub_make_platform_with_a_well_formed_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        facade, "make_platform", lambda: _FakePlatform(_well_formed_dataset())
    )


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


# --- T-01.4.11/.2/.5: get_dataset / schema-drift / empty-set / N28 ------


def test_run_eval_returns_nonzero_and_names_the_missing_dataset_name_var(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("GOLDEN_DATASET_NAME", raising=False)

    def _fail_if_called() -> None:
        raise AssertionError("make_platform must not be called: zero network calls (N6)")

    monkeypatch.setattr(facade, "make_platform", _fail_if_called)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "GOLDEN_DATASET_NAME" in caplog.text


def test_run_eval_returns_nonzero_on_dataset_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    from idp_regression.platform.errors import DatasetFetchFailedError

    class _FailingPlatform:
        def get_dataset(self, name: str) -> object:
            raise DatasetFetchFailedError("get_dataset failed with HTTP 500")

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FailingPlatform())

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "dataset_fetch_failed" in caplog.text


def test_run_eval_returns_nonzero_on_schema_drift(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    dataset = _well_formed_dataset()
    dataset["expected_output_schema"] = {"not": "the committed schema"}

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "schema_drift" in caplog.text


def test_run_eval_returns_nonzero_on_empty_golden_set(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    dataset = _well_formed_dataset()
    dataset["items"] = []

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "empty_set" in caplog.text


def test_run_eval_returns_nonzero_on_malformed_golden(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    dataset = _well_formed_dataset()
    dataset["items"] = [{"item_id": "i1", "document_id": "doc-42", "golden": {}}]

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert exit_code != 0
    assert "malformed_golden" in caplog.text
    assert "doc-42" in caplog.text


def test_run_eval_logs_the_golden_version_content_hash(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """T-01.4.6 / INV-04: `golden_version` is a content hash of the SAME
    `dataset["items"]` just validated -- no second fetch."""
    from idp_regression.platform.hashing import hash_dataset

    dataset = _well_formed_dataset()
    expected_golden_version = hash_dataset(dataset["items"])  # type: ignore[arg-type]

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.INFO), pytest.raises(NotImplementedError):
        run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")

    assert expected_golden_version in caplog.text


# --- pre-run checks pass -> the loop boundary (T-01.4.3a onward) --------


def test_run_eval_raises_not_implemented_once_prerun_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    with pytest.raises(NotImplementedError):
        run_eval("12345678-1234-1234-1234-123456789012", "1.0", "nightly")


def test_run_eval_composes_the_experiment_name_before_hitting_the_loop_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
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
    pre-run-checks-passed log line interpolates FIVE values
    (run/experiment/action/version/golden_version), each through its own
    `sanitize_for_log(...)` call. A single `'\\"' in caplog.text`
    assertion is satisfied if ANY ONE of the five is sanitized -- it does
    not catch a regression that un-sanitizes exactly one field while
    leaving the other four correct (verified: dropping ONLY the
    `run_name` wrapper survived that assertion). Assert each field's
    escaped form individually instead."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
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
