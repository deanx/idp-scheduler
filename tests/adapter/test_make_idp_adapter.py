"""/test Scenario B item 7 — make_idp_adapter() env parsing coverage gap.

Pins existing correct behavior: comma-separated IDP_TERMINAL_STATUSES /
IDP_SUCCESS_STATUSES parsing, and the ["SUCCEEDED"] default when unset.
"""

from __future__ import annotations

import pytest

from idp_regression.adapter.errors import IDPConfigurationError
from idp_regression.adapter.idp_client import make_idp_adapter

_REQUIRED_ENV = {
    "IDP_CLIENT_ID": "cid",
    "IDP_CLIENT_SECRET": "csecret",
    "IDP_REGION": "us-east-2",
    "IDP_ORG_ID": "org-1",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("IDP_TERMINAL_STATUSES", raising=False)
    monkeypatch.delenv("IDP_SUCCESS_STATUSES", raising=False)


def test_default_terminal_and_success_statuses_are_succeeded_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    adapter = make_idp_adapter()
    assert adapter._terminal_statuses == {"SUCCEEDED"}  # noqa: SLF001 - white-box pin
    assert adapter._success_statuses == {"SUCCEEDED"}  # noqa: SLF001


def test_comma_separated_terminal_statuses_are_parsed_into_a_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TERMINAL_STATUSES", "SUCCEEDED,FAILED,PARTIAL_SUCCESS")
    monkeypatch.setenv("IDP_SUCCESS_STATUSES", "SUCCEEDED,PARTIAL_SUCCESS")
    adapter = make_idp_adapter()
    assert adapter._terminal_statuses == {"SUCCEEDED", "FAILED", "PARTIAL_SUCCESS"}  # noqa: SLF001
    assert adapter._success_statuses == {"SUCCEEDED", "PARTIAL_SUCCESS"}  # noqa: SLF001


def test_whitespace_around_comma_separated_statuses_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TERMINAL_STATUSES", " SUCCEEDED , FAILED ")
    monkeypatch.setenv("IDP_SUCCESS_STATUSES", "SUCCEEDED")
    adapter = make_idp_adapter()
    assert adapter._terminal_statuses == {"SUCCEEDED", "FAILED"}  # noqa: SLF001


@pytest.mark.parametrize(
    "env_name",
    [
        "IDP_SUBMIT_TIMEOUT_SECONDS",
        "IDP_EXECUTION_TIMEOUT_SECONDS",
        "IDP_TOKEN_REFRESH_MARGIN_SECONDS",
    ],
)
@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf", "0", "-1", "banana"])
def test_invalid_timing_env_var_raises_typed_config_error(
    monkeypatch: pytest.MonkeyPatch, env_name: str, bad_value: str
) -> None:
    # QA F-1: IDP_EXECUTION_TIMEOUT_SECONDS=nan/inf must be rejected at env
    # parsing (and/or the constructor gate), never silently accepted —
    # float("nan")/float("inf") parse "successfully" so a bare float(...)
    # call doesn't catch these; token_refresh_margin's "0" is legitimately
    # valid (checked separately below), so it's excluded from that combo.
    if env_name == "IDP_TOKEN_REFRESH_MARGIN_SECONDS" and bad_value == "0":
        pytest.skip("0 is a valid token refresh margin")
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, bad_value)
    with pytest.raises(IDPConfigurationError):
        make_idp_adapter()


def test_zero_token_refresh_margin_env_var_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TOKEN_REFRESH_MARGIN_SECONDS", "0")
    make_idp_adapter()


def test_missing_required_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear real creds explicitly — a live .env sourced into the shell
    # (e.g. RUN_INTEGRATION_TESTS=1 runs) must not make this test flaky.
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="IDP_CLIENT_ID"):
        make_idp_adapter()
