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


_TIMING_ENV_NAMES = [
    "IDP_SUBMIT_TIMEOUT_SECONDS",
    "IDP_EXECUTION_TIMEOUT_SECONDS",
    "IDP_TOKEN_REFRESH_MARGIN_SECONDS",
]

# (env_name, bad_value) pairs — every combination of env var and bad value
# EXCEPT (IDP_TOKEN_REFRESH_MARGIN_SECONDS, "0"), which is legitimately
# valid and asserted separately by
# test_zero_token_refresh_margin_env_var_is_accepted below (Atchim
# suggestion: drop the redundant runtime skip, exclude it declaratively).
_INVALID_TIMING_ENV_CASES = [
    (env_name, bad_value)
    for env_name in _TIMING_ENV_NAMES
    for bad_value in ("nan", "inf", "-inf", "0", "-1", "banana")
    if not (env_name == "IDP_TOKEN_REFRESH_MARGIN_SECONDS" and bad_value == "0")
]


@pytest.mark.parametrize(("env_name", "bad_value"), _INVALID_TIMING_ENV_CASES)
def test_invalid_timing_env_var_raises_typed_config_error(
    monkeypatch: pytest.MonkeyPatch, env_name: str, bad_value: str
) -> None:
    # QA F-1: IDP_EXECUTION_TIMEOUT_SECONDS=nan/inf must be rejected at env
    # parsing (and/or the constructor gate), never silently accepted —
    # float("nan")/float("inf") parse "successfully" so a bare float(...)
    # call doesn't catch these.
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, bad_value)
    with pytest.raises(IDPConfigurationError):
        make_idp_adapter()


def test_zero_token_refresh_margin_env_var_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TOKEN_REFRESH_MARGIN_SECONDS", "0")
    make_idp_adapter()


@pytest.mark.parametrize("env_name", _TIMING_ENV_NAMES)
def test_timing_env_var_at_the_3600_second_cap_is_accepted(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, "3600")
    make_idp_adapter()


@pytest.mark.parametrize("env_name", _TIMING_ENV_NAMES)
def test_timing_env_var_just_above_the_3600_second_cap_is_rejected(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, "3600.1")
    with pytest.raises(IDPConfigurationError):
        make_idp_adapter()


def test_missing_required_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear real creds explicitly — a live .env sourced into the shell
    # (e.g. RUN_INTEGRATION_TESTS=1 runs) must not make this test flaky.
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="IDP_CLIENT_ID"):
        make_idp_adapter()


@pytest.mark.parametrize("whitespace_value", ["   ", "\t", "\n", "\t\n ", " \r\n\t "])
@pytest.mark.parametrize("required_var", list(_REQUIRED_ENV))
def test_whitespace_only_required_credential_raises(
    required_var: str, whitespace_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-44 gate, fourth instance, finding R-3: `_require`'s
    `if not value:` accepted a whitespace-only credential (reproduced
    live: `IDP_CLIENT_SECRET="   "` passed N6's pre-run checks) -- the
    same gap fixed for the platform's three vars in
    `idp_regression.orchestration.bootstrap` (Atchim review C-1) applied
    here too, on the other four."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(required_var, whitespace_value)
    with pytest.raises(RuntimeError, match=required_var):
        make_idp_adapter()


@pytest.mark.parametrize("required_var", list(_REQUIRED_ENV))
def test_literal_zero_required_credential_is_not_falsely_rejected(
    required_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the whitespace-only fix: `"0".strip()` is
    still the truthy, non-empty string `"0"` and must be accepted."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(required_var, "0")
    make_idp_adapter()  # must not raise


@pytest.mark.parametrize("required_var", list(_REQUIRED_ENV))
def test_required_credential_is_stripped_of_surrounding_whitespace(
    required_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-44 gate, fifth instance, suggestion (2026-09-21): a trailing
    newline in a credential (e.g. an env var sourced from a file with a
    trailing `\\n`, or a CI secret with a stray newline) previously
    passed validation unstripped and was sent WITH the newline into the
    OAuth token request body -- a confusing auth failure on a sensitive
    surface, since the credential LOOKS correct in every log/error
    message (which never echoes values, INV-02) but silently fails
    server-side. `_require` now returns the stripped value, not the raw
    one."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(required_var, "  padded-credential-value  \n")
    adapter = make_idp_adapter()
    attr_name = {
        "IDP_CLIENT_ID": "_client_id",
        "IDP_CLIENT_SECRET": "_client_secret",
        "IDP_REGION": "_region",
        "IDP_ORG_ID": "_org_id",
    }[required_var]
    assert getattr(adapter, attr_name) == "padded-credential-value"
