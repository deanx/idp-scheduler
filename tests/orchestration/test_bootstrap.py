"""DEBT-30 / NFR N6: platform credential env vars must be validated
PRESENT and NON-EMPTY before any client is constructed and before any
network call. `validate_platform_credentials()` checks
`os.environ` directly, against `idp_regression.platform.REQUIRED_ENV_VARS`
(never hand-listed here) -- it never calls the platform factory itself
(Atchim review R-3, 2026-09-21: a client constructed only to be discarded
leaks a background export thread per call and is a non-zero client
construction HARDEN-01's split-brain condition asserts against).

Atchim review C-1 (2026-09-21, reproduced live): a required var that is
SET but EMPTY raises no `KeyError` from a bare `os.environ[...]`
subscript, so relying on that alone silently defeats N6. These tests
cover both the missing-key and the empty-string case for every required
var.
"""

from __future__ import annotations

import pytest

from idp_regression.orchestration.bootstrap import (
    MissingCredentialError,
    validate_platform_credentials,
)
from idp_regression.platform import REQUIRED_ENV_VARS


def _set_all_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "distinctive-pub-9f3a")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "distinctive-secret-NOT-A-REAL-KEY-2c71")


def test_required_env_vars_are_exactly_the_three_platform_credentials() -> None:
    assert set(REQUIRED_ENV_VARS) == {
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    }


def test_validate_platform_credentials_succeeds_when_every_var_is_present_and_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)

    validate_platform_credentials()  # must not raise


@pytest.mark.parametrize(
    "missing_var", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
)
def test_validate_platform_credentials_raises_when_a_var_is_absent(
    missing_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        validate_platform_credentials()

    assert excinfo.value.variable_name == missing_var


@pytest.mark.parametrize(
    "empty_var", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
)
def test_validate_platform_credentials_raises_when_a_var_is_set_but_empty(
    empty_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atchim review C-1: a SET-but-EMPTY var must be rejected exactly
    like an absent one -- `os.environ[...]` subscript succeeds on `""`
    and raises no `KeyError`, which is the exact gap C-1 found live."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.setenv(empty_var, "")

    with pytest.raises(MissingCredentialError) as excinfo:
        validate_platform_credentials()

    assert excinfo.value.variable_name == empty_var


@pytest.mark.parametrize(
    "whitespace_value", ["   ", "\t", "\n", "\t\n ", " \r\n\t "]
)
@pytest.mark.parametrize(
    "whitespace_var", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
)
def test_validate_platform_credentials_raises_when_a_var_is_whitespace_only(
    whitespace_var: str, whitespace_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-44 gate finding 1 (2026-09-21, reproduced live): a
    whitespace-only value (e.g. a trailing-space `.env` line, or a CI
    secret resolving to a blank line) is truthy in Python and previously
    passed `validate_platform_credentials` unrejected -- reaching a real
    client at T-01.4.2 to die at the first 401 instead of failing closed
    here, with a clear message, before any network call (N6)."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.setenv(whitespace_var, whitespace_value)

    with pytest.raises(MissingCredentialError) as excinfo:
        validate_platform_credentials()

    assert excinfo.value.variable_name == whitespace_var


@pytest.mark.parametrize(
    "falsy_looking_var", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
)
def test_validate_platform_credentials_does_not_reject_a_literal_zero(
    falsy_looking_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the finding-1 fix: `"0".strip()` is still the
    truthy, non-empty string `"0"` -- a credential that happens to be the
    single character `"0"` must NOT be treated as missing."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.setenv(falsy_looking_var, "0")

    validate_platform_credentials()  # must not raise


def test_validate_platform_credentials_error_message_names_only_the_variable_not_any_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-02 sentinel-absence pin: the OTHER two secret values are set to
    distinctive sentinels -- they must never appear in the raised
    message, only the missing variable's NAME does."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        validate_platform_credentials()

    message = str(excinfo.value)
    assert "LANGFUSE_HOST" in message
    assert "distinctive-pub-9f3a" not in message
    assert "distinctive-secret-NOT-A-REAL-KEY-2c71" not in message


def test_validate_platform_credentials_reports_the_first_missing_var_in_a_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        validate_platform_credentials()

    assert excinfo.value.variable_name == "LANGFUSE_HOST"
