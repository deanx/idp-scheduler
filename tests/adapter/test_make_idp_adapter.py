"""/test Scenario B item 7 — make_idp_adapter() env parsing coverage gap.

Pins existing correct behavior: comma-separated IDP_TERMINAL_STATUSES /
IDP_SUCCESS_STATUSES parsing, and the ["SUCCEEDED"] default when unset.
"""

from __future__ import annotations

import pytest

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


def test_missing_required_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear real creds explicitly — a live .env sourced into the shell
    # (e.g. RUN_INTEGRATION_TESTS=1 runs) must not make this test flaky.
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="IDP_CLIENT_ID"):
        make_idp_adapter()
