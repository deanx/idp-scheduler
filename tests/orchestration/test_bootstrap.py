"""DEBT-30 / NFR N6: `make_platform()` raises a bare `KeyError` on a
missing env var, which is neither a clear message nor a controlled exit.
`construct_platform()` wraps it into a typed `MissingCredentialError`
naming only the missing variable (INV-02: never echo a value)."""

from __future__ import annotations

import pytest

from idp_regression.orchestration.bootstrap import MissingCredentialError, construct_platform
from idp_regression.platform.errors import PlatformConfigurationError


def _set_all_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "langfuse")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "distinctive-pub-9f3a")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "distinctive-secret-NOT-A-REAL-KEY-2c71")
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)


def test_construct_platform_succeeds_when_every_var_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)

    platform = construct_platform()

    assert platform is not None


def test_construct_platform_wraps_a_missing_host_into_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        construct_platform()

    assert excinfo.value.variable_name == "LANGFUSE_HOST"


def test_construct_platform_wraps_a_missing_public_key_into_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        construct_platform()

    assert excinfo.value.variable_name == "LANGFUSE_PUBLIC_KEY"


def test_construct_platform_wraps_a_missing_secret_key_into_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        construct_platform()

    assert excinfo.value.variable_name == "LANGFUSE_SECRET_KEY"


def test_construct_platform_error_message_names_only_the_variable_not_any_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-02 sentinel-absence pin: the OTHER two secret values are set to
    distinctive sentinels -- they must never appear in the raised
    message, only the missing variable's NAME does."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        construct_platform()

    message = str(excinfo.value)
    assert "LANGFUSE_HOST" in message
    assert "distinctive-pub-9f3a" not in message
    assert "distinctive-secret-NOT-A-REAL-KEY-2c71" not in message


def test_construct_platform_lets_platform_configuration_error_propagate_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base_url split-brain guard already raises a clear, typed
    `PlatformConfigurationError` -- `construct_platform` must not mask or
    re-wrap an error that is already correct (DEBT-30 is about the BARE
    `KeyError` specifically, not every platform error)."""
    _set_all_platform_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://elsewhere.invalid")

    with pytest.raises(PlatformConfigurationError):
        construct_platform()


def test_construct_platform_lets_unsupported_platform_value_error_propagate_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_platform_env(monkeypatch)
    monkeypatch.setenv("PLATFORM", "not-a-real-platform")

    with pytest.raises(ValueError):
        construct_platform()
