"""/test Scenario B item 7 — make_idp_adapter() env parsing coverage gap.

Pins existing correct behavior: comma-separated IDP_TERMINAL_STATUSES /
IDP_SUCCESS_STATUSES parsing, and the ["SUCCEEDED"] default when unset.

ADR-0004 A9 (2026-09-22): `org_id` is a required PARAMETER to
`make_idp_adapter(org_id)`, not an env var anymore -- `IDP_ORG_ID` is
read by no production code path (see `test_static_orchestration_checks.
py`'s codebase-wide pin). `_ORG_ID` below is a fixed test constant passed
explicitly to every call in this file; it is not part of `_REQUIRED_ENV`
because it is no longer sourced from the environment at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import (
    IDPConfigurationError,
    IDPExecutionFailedError,
    IDPPollTimeoutError,
)
from idp_regression.adapter.idp_client import DEFAULT_POLL_INTERVAL_SECONDS, make_idp_adapter
from idp_regression.adapter.types import NormalizedOutput

_ORG_ID = "org-1"

_REQUIRED_ENV = {
    "IDP_CLIENT_ID": "cid",
    "IDP_CLIENT_SECRET": "csecret",
    "IDP_REGION": "us-east-2",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("IDP_TERMINAL_STATUSES", raising=False)
    monkeypatch.delenv("IDP_SUCCESS_STATUSES", raising=False)
    # ADR-0004 A9: IDP_ORG_ID must NOT be read even if it happens to be
    # set in the ambient environment (e.g. a live .env sourced into the
    # shell) -- clearing it here makes that live-reproducible: if
    # make_idp_adapter() ever regresses to reading it again instead of
    # using its `org_id` parameter, these tests would otherwise pass by
    # accident against a real value.
    monkeypatch.delenv("IDP_ORG_ID", raising=False)


def _extract_against_a_single_poll_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> NormalizedOutput:
    """DEBT-25(d): drives `make_idp_adapter()`'s constructed adapter through
    a real `extract()` call and returns the normalized output -- a
    behavioural proof of how `IDP_TERMINAL_STATUSES`/`IDP_SUCCESS_STATUSES`
    parsed, replacing a direct read of the private `_terminal_statuses`/
    `_success_statuses` attributes. `IDP_EXECUTION_TIMEOUT_SECONDS` is
    pinned short so a status that the config does NOT treat as terminal
    fails fast (`IDPPollTimeoutError`) instead of hanging on the real
    `time.sleep` a factory-built adapter uses (no injectable clock)."""
    monkeypatch.setenv("IDP_EXECUTION_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        transport,
        "post_json",
        lambda *a, **kw: (200, {"access_token": "tok-1", "expires_in": 300}),
    )
    monkeypatch.setattr(
        transport, "post_multipart_file", lambda *a, **kw: (202, {"id": "exec-1"})
    )
    monkeypatch.setattr(
        transport,
        "get_json_with_headers",
        lambda *a, **kw: (200, {"status": status, "fields": {}, "tables": {}}, {}),
    )
    adapter = make_idp_adapter(_ORG_ID)
    doc = tmp_path / "invoice.pdf"
    doc.write_bytes(b"%PDF")
    return adapter.extract(str(doc), "action-1", "v1")


def test_default_terminal_and_success_statuses_are_succeeded_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    # SUCCEEDED is terminal and success by default -- extract() returns.
    out = _extract_against_a_single_poll_status(monkeypatch, tmp_path, "SUCCEEDED")
    assert out["status"] == "SUCCEEDED"


def test_default_terminal_statuses_include_failed_so_a_hard_failure_is_not_a_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DEBT-22 leg 2 / ASM-01 -- pinned against a LIVE capture, per SR-1.

    `FAILED` is a real IDP terminal status: probed live 2026-09-25 by
    submitting a truncated PDF, which reached `status: "FAILED"` in 3.2s.
    The captured body is `tests/fixtures/live/failed-execution.raw.json`.

    **This test previously asserted the opposite** (`IDPPollTimeoutError`,
    on the ground that FAILED "is not in the default terminal set") --
    describing the mechanism rather than arguing for it, and thereby
    cementing the defect: with `FAILED` absent from the default terminal
    set, a hard IDP failure is not terminal, so the poll loop waits out
    the ENTIRE execution budget and then aborts with the WRONG reason.
    ADR-0004 #5 calls a non-success terminal status a hard IDP failure;
    the orchestrator maps `IDPExecutionFailedError` to `hard_failure` and
    `IDPPollTimeoutError` to a timeout, so the operator was told "IDP was
    slow" for a document IDP had already rejected -- after burning the
    full budget (120s as configured) waiting for it.

    `success_statuses` is unchanged and stays `{"SUCCEEDED"}`: FAILED is
    terminal, never successful.
    """
    _set_required_env(monkeypatch)
    with pytest.raises(IDPExecutionFailedError) as excinfo:
        _extract_against_a_single_poll_status(monkeypatch, tmp_path, "FAILED")
    assert excinfo.value.status == "FAILED"


def test_a_status_outside_the_default_terminal_set_still_polls_to_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The half of the old test that was worth keeping.

    Broadening the default to `{"SUCCEEDED", "FAILED"}` must not make the
    loop treat *any* status as terminal -- a genuinely unknown status is
    still non-terminal and still polls to the deadline (fail-closed).
    `PARTIAL_SUCCESS` is the realistic one: ASM-01 records that whether
    this org's IDP emits it is still unconfirmed, so it must NOT be
    silently assumed terminal.
    """
    _set_required_env(monkeypatch)
    with pytest.raises(IDPPollTimeoutError):
        _extract_against_a_single_poll_status(monkeypatch, tmp_path, "PARTIAL_SUCCESS")


def test_comma_separated_terminal_statuses_are_parsed_into_a_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TERMINAL_STATUSES", "SUCCEEDED,FAILED,PARTIAL_SUCCESS")
    monkeypatch.setenv("IDP_SUCCESS_STATUSES", "SUCCEEDED,PARTIAL_SUCCESS")
    # FAILED: parsed into terminal_statuses but not success_statuses -- a
    # hard failure raised immediately (proves both sets parsed correctly;
    # if FAILED had NOT been parsed into terminal_statuses, this would
    # time out instead of raising).
    with pytest.raises(IDPExecutionFailedError):
        _extract_against_a_single_poll_status(monkeypatch, tmp_path, "FAILED")
    # PARTIAL_SUCCESS: parsed into both sets -- extract() returns.
    out = _extract_against_a_single_poll_status(monkeypatch, tmp_path, "PARTIAL_SUCCESS")
    assert out["status"] == "PARTIAL_SUCCESS"


def test_whitespace_around_comma_separated_statuses_is_stripped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TERMINAL_STATUSES", " SUCCEEDED , FAILED ")
    monkeypatch.setenv("IDP_SUCCESS_STATUSES", "SUCCEEDED")
    # If the surrounding whitespace were NOT stripped, the set would
    # contain " FAILED" (with a leading space), not "FAILED" -- the raw
    # status the poll response carries would then never match, and the
    # loop would time out instead of raising IDPExecutionFailedError.
    with pytest.raises(IDPExecutionFailedError):
        _extract_against_a_single_poll_status(monkeypatch, tmp_path, "FAILED")


_TIMING_ENV_NAMES = [
    "IDP_SUBMIT_TIMEOUT_SECONDS",
    "IDP_EXECUTION_TIMEOUT_SECONDS",
    "IDP_TOKEN_REFRESH_MARGIN_SECONDS",
    "IDP_POLL_INTERVAL_SECONDS",
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
        make_idp_adapter(_ORG_ID)


def test_zero_token_refresh_margin_env_var_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_TOKEN_REFRESH_MARGIN_SECONDS", "0")
    make_idp_adapter(_ORG_ID)


def test_poll_interval_env_var_reaches_the_constructed_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DEBT-71: IDP_POLL_INTERVAL_SECONDS was the only adapter timing knob
    # with no env var at all — poll_interval_seconds was a constructor
    # parameter only, so an operator had no way to raise or lower it
    # (e.g. to spend less IDP quota, or respect a slow action) without
    # editing source.
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_POLL_INTERVAL_SECONDS", "15")
    adapter = make_idp_adapter(_ORG_ID)
    assert adapter._poll_interval_seconds == 15.0


def test_poll_interval_env_var_default_is_unchanged_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("IDP_POLL_INTERVAL_SECONDS", raising=False)
    adapter = make_idp_adapter(_ORG_ID)
    assert adapter._poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS


def test_poll_interval_env_var_below_the_rate_protection_floor_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MIN_POLL_INTERVAL_SECONDS (10.0, IDP-side rate protection) must keep
    # holding for a value that reaches the constructor via the env var, not
    # only for a caller passing the constructor parameter directly.
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_POLL_INTERVAL_SECONDS", "9.9")
    with pytest.raises(IDPConfigurationError):
        make_idp_adapter(_ORG_ID)


@pytest.mark.parametrize("env_name", _TIMING_ENV_NAMES)
def test_timing_env_var_at_the_3600_second_cap_is_accepted(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, "3600")
    make_idp_adapter(_ORG_ID)


@pytest.mark.parametrize("env_name", _TIMING_ENV_NAMES)
def test_timing_env_var_just_above_the_3600_second_cap_is_rejected(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv(env_name, "3600.1")
    with pytest.raises(IDPConfigurationError):
        make_idp_adapter(_ORG_ID)


def test_missing_required_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear real creds explicitly — a live .env sourced into the shell
    # (e.g. RUN_INTEGRATION_TESTS=1 runs) must not make this test flaky.
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="IDP_CLIENT_ID"):
        make_idp_adapter(_ORG_ID)


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
        make_idp_adapter(_ORG_ID)


@pytest.mark.parametrize("required_var", list(_REQUIRED_ENV))
def test_literal_zero_required_credential_is_not_falsely_rejected(
    required_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the whitespace-only fix: `"0".strip()` is
    still the truthy, non-empty string `"0"` and must be accepted."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(required_var, "0")
    make_idp_adapter(_ORG_ID)  # must not raise


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
    adapter = make_idp_adapter(_ORG_ID)
    attr_name = {
        "IDP_CLIENT_ID": "_client_id",
        "IDP_CLIENT_SECRET": "_client_secret",
        "IDP_REGION": "_region",
    }[required_var]
    assert getattr(adapter, attr_name) == "padded-credential-value"


# --- ADR-0004 A9 (2026-09-22): org_id is a PARAMETER, not an env var ----


def test_org_id_parameter_reaches_the_adapter_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most direct pin of A9's own decision: `make_idp_adapter(org_id)`
    passes its parameter straight through to the constructed adapter's
    `_org_id`, not something derived from `IDP_ORG_ID`."""
    _set_required_env(monkeypatch)
    adapter = make_idp_adapter("a-distinctive-org-id-4d9c")
    assert adapter._org_id == "a-distinctive-org-id-4d9c"


def test_idp_org_id_env_var_is_not_read_even_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation pin for the A9 fix itself: setting `IDP_ORG_ID` in the
    environment to a DIFFERENT value than the `org_id` parameter must
    NOT leak into the constructed adapter -- confirms the org id comes
    from the parameter, not a `_require("IDP_ORG_ID")` call that merely
    got its literal renamed. Reverting `org_id=org_id` back to
    `org_id=_require("IDP_ORG_ID")` in `make_idp_adapter` makes this
    fail."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("IDP_ORG_ID", "env-org-should-be-ignored")
    adapter = make_idp_adapter("param-org-should-win")
    assert adapter._org_id == "param-org-should-win"
