"""T-01.4.1/.11/.2/.5/.6, ADR-0004 A9: `run_eval(action_id, version, run_name,
dataset_name, org_id, max_documents_per_run=1000) -> int` facade -- the
FULL run (pre-run chain, per-document loop, post-loop record phase).

This slice builds the pre-run chain (ADR-0004/ADR-0005 #8,
S-01.4-KICKOFF.md pinned order): `load_dotenv()` first (INV-05),
fail-closed credential VALIDATION (NFR N6, DEBT-30 -- see `bootstrap.py`),
`IDP_DOCUMENT_DIR` validation, platform construction, `get_dataset`
(dataset_fetch_failed), schema-drift (schema_drift), empty-set
(empty_set), N28 structural validation (malformed_golden), and
golden_version/run-id/experiment-name composition (T-01.4.6, T-01.4.13,
DEBT-19) -- AND, new in this batch, the per-document loop (`extract` ->
`classify` -> `overall_gate` -> `build_score_inputs`, ADR-0005 #9 step
2/3, INV-06/INV-08) plus the single post-loop `record_run` (#9 step 4)
and `mark_run_status` (#9 step 5).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast

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
    monkeypatch.setenv("IDP_DOCUMENT_DIR", "/documents")


class _FakePlatform:
    """A minimal `PlatformAdapter` double for the PRE-RUN-chain-only
    tests below -- `get_dataset` plus no-op `record_run`/`mark_run_status`
    (so a test that reaches the loop boundary doesn't crash on a missing
    attribute; the loop-boundary tests below assert nothing about their
    arguments). Tests that need to assert on `record_run`/
    `mark_run_status` arguments use `_RecordingPlatform` instead."""

    def __init__(self, dataset: object) -> None:
        self._dataset = dataset

    def get_dataset(self, name: str) -> object:
        return self._dataset

    def record_run(self, **kwargs: object) -> None:
        pass

    def mark_run_status(self, *args: object, **kwargs: object) -> None:
        pass


class _RecordingPlatform:
    """A `PlatformAdapter` double that records every `record_run` /
    `mark_run_status` call for assertion, and can be told to raise a
    given exception from either."""

    def __init__(
        self,
        dataset: object,
        *,
        record_run_error: Exception | None = None,
        # BaseException, not Exception: production's `_mark_run_status_
        # best_effort` catches `(Exception, asyncio.CancelledError)`
        # (facade.py GAP-5) -- asyncio.CancelledError is a BaseException,
        # not an Exception, so this fixture must be able to raise one to
        # prove that widened catch clause actually holds.
        mark_run_status_error: BaseException | None = None,
    ) -> None:
        self._dataset = dataset
        self._record_run_error = record_run_error
        self._mark_run_status_error = mark_run_status_error
        self.record_run_calls: list[dict[str, object]] = []
        self.mark_run_status_calls: list[dict[str, object]] = []

    def get_dataset(self, name: str) -> object:
        return self._dataset

    def record_run(self, **kwargs: object) -> None:
        self.record_run_calls.append(kwargs)
        if self._record_run_error is not None:
            raise self._record_run_error

    def mark_run_status(self, run_id: str, status: str, **kwargs: object) -> None:
        self.mark_run_status_calls.append({"run_id": run_id, "status": status, **kwargs})
        if self._mark_run_status_error is not None:
            raise self._mark_run_status_error


class _FakeIDPAdapter:
    """An `.extract()` double. `outputs` maps a resolved document PATH to
    the `NormalizedOutput` to return; `error` (if set) is raised on every
    call instead, after recording it."""

    def __init__(
        self,
        outputs: dict[str, object] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._outputs = outputs or {}
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def extract(self, document_path: str, action_id: str, version: str) -> object:
        self.calls.append((document_path, action_id, version))
        if self._error is not None:
            raise self._error
        return self._outputs[document_path]


def _matching_actual_for(document_dir: str, document_id: str) -> tuple[str, dict[str, object]]:
    """A `NormalizedOutput` that exactly matches `_well_formed_dataset()`'s
    single golden field (`total` = `"1250.00"`, critical), producing a
    `PASS` gate -- for tests that only care about the success path
    reaching the loop/record phase, not about classification itself."""
    path = os.path.join(document_dir, document_id)
    return path, {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "1250.00", "confidence": 0.99}},
    }


def _stub_make_idp_adapter_success(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document_dir: str = "/documents",
    document_id: str = "doc-1",
) -> _FakeIDPAdapter:
    """Stub `facade.make_idp_adapter` to return a fake whose `.extract()`
    matches `_well_formed_dataset()`'s single item, producing a `PASS`
    gate -- for tests that only care about reaching the record phase."""
    path, actual = _matching_actual_for(document_dir, document_id)
    fake = _FakeIDPAdapter({path: actual})
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake)
    return fake


def _well_formed_dataset() -> dict[str, object]:
    from idp_regression.platform.schema import load_golden_schema

    return {
        "items": [
            {
                "item_id": "item-1",
                "document_id": "doc-1",
                "golden": {
                    "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}}
                },
            }
        ],
        "expected_output_schema": load_golden_schema(),
    }


def _stub_make_platform_with_a_well_formed_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(_well_formed_dataset()))


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

    run_eval("action", "version", "run", "idp-regression-golden", "org-t")

    assert call_order == ["load_dotenv", "validate_platform_credentials"]


def test_run_eval_never_escapes_when_load_dotenv_itself_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """`load_dotenv()` used to sit OUTSIDE the pre-run try-block (it starts
    one line later, at `validate_platform_credentials()`) -- an unreadable
    or undecodable `.env` (e.g. `OSError` on a permission-denied path)
    escaped `run_eval` raw: no catch, no `run_end`, no abort marker (no
    `run_id` exists yet, correctly), and the exception's own message --
    which can carry the `.env` path -- was never sanitized. Live-reproduced
    with a sentinel in the message; none of it may escape or reach the
    logs, and the `run_end` line must still be emitted (N10) with a
    non-zero exit."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    sentinel = "SEKRIT-dotenv-path-should-never-be-logged/etc/secret/.env"

    def _boom() -> None:
        raise OSError(f"[Errno 13] Permission denied: '{sentinel}'")

    monkeypatch.setattr(facade, "load_dotenv", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval("action", "version", "run", "idp-regression-golden", "org-t")

    assert exit_code != 0
    assert "run_end" in caplog.text
    assert sentinel not in caplog.text
    assert "OSError" in caplog.text


def test_run_eval_has_nothing_outside_the_pre_run_try_between_load_dotenv_and_the_loop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """The merged pre-run try used to end right after
    `compose_experiment_name()`, leaving the `pre-run checks passed`
    log line's own `len(dataset["items"])` call sitting OUTSIDE any
    catch-all (with `_abort`'s definition next to it). A dataset whose
    `"items"` raises on `len()` (a pathological but real object, e.g. one
    whose `__len__` itself blows up) must not escape `run_eval` -- the
    fix extends the try to cover that log line too."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    class _ExplodingLenList(list):  # type: ignore[type-arg]
        """`check_empty_set`'s `not dataset["items"]` already calls
        `__len__` once (that pinned behavior must keep working) -- this
        only explodes from the SECOND call onward, isolating the
        `logger.info("pre-run checks passed", ..., len(dataset["items"]))`
        call itself as the thing under test."""

        def __init__(self, *args: object) -> None:
            super().__init__(*args)
            self._calls = 0

        def __len__(self) -> int:
            self._calls += 1
            if self._calls > 1:
                raise RuntimeError("len() blew up")
            return list.__len__(self)

    dataset = _well_formed_dataset()
    dataset["items"] = _ExplodingLenList(dataset["items"])
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text
    # No run_id exists yet at this point in the pre-run chain, so no
    # aborted marker is expected -- same class as the other pre-run
    # untyped-exception tests above.


# --- ADR-0004 A10 (2026-09-22): pre-flight IDP quota ceiling ------------
# `_two_item_dataset()` is defined later in this file (T-01.4.6 section)
# and reused here -- forward reference is fine, module-level function
# lookup happens at call time, not at def time.


def test_run_eval_aborts_quota_ceiling_exceeded_before_any_idp_call(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """A10: the dataset's item count (2) exceeds `max_documents_per_run`
    (1) -- refused PRE-FLIGHT, zero IDP calls, no run_status marker (no
    `run_id` exists yet, per A7)."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(_two_item_dataset())
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    class _FailIfExtractCalled:
        def extract(self, document_path: str, action_id: str, version: str) -> object:
            raise AssertionError("extract() must not be called: zero quota spent (A10)")

    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: _FailIfExtractCalled())

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
            max_documents_per_run=1,
        )

    assert exit_code == 1
    assert "quota_ceiling_exceeded" in caplog.text
    assert recording_platform.mark_run_status_calls == []


def test_run_eval_quota_ceiling_boundary_equal_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The boundary case: item_count == max_documents_per_run must NOT
    abort -- only a strict excess does."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    document_dir = "/documents"
    dataset = _two_item_dataset()
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))
    idp_outputs: dict[str, object] = {
        f"{document_dir}/doc-1": _matching_actual_for(document_dir, "doc-1")[1],
        f"{document_dir}/doc-2": _matching_actual_for(document_dir, "doc-2")[1],
    }
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: _FakeIDPAdapter(idp_outputs))
    monkeypatch.setenv("IDP_DOCUMENT_DIR", document_dir)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012",
        "1.0",
        "nightly",
        "idp-regression-golden",
        "org-t",
        max_documents_per_run=2,
    )

    assert exit_code == 0


def test_run_eval_logs_the_effective_max_documents_per_run_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """The run-start log line always records the effective ceiling, so
    the arbitrary MVP default is never hidden."""
    _stub_make_idp_adapter_success(monkeypatch)
    _base_env_for_run_start(monkeypatch, tmp_path)

    with caplog.at_level(logging.INFO):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
            max_documents_per_run=42,
        )

    assert "max_documents_per_run=42" in caplog.text


def _base_env_for_run_start(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)


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
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "LANGFUSE_PUBLIC_KEY" in caplog.text


# --- NFR N6: fail-closed on missing IDP credential ---------------------


@pytest.mark.parametrize("missing_idp_var", ["IDP_CLIENT_ID", "IDP_CLIENT_SECRET", "IDP_REGION"])
def test_run_eval_returns_nonzero_and_names_the_missing_idp_var(
    missing_idp_var: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """⚠️ Updated 2026-09-21 (GAP-6 fix): this clause no longer logs
    `str(exc)` at all (see the module's GAP-6 comment on the narrow
    pre-run except-clause) -- a missing `IDP_CLIENT_ID` still exits
    non-zero and is still safe (the type name, never the message, is
    logged), but the variable NAME is no longer named in the log line as
    a side effect of that message-level fix. This test now pins the
    safe contract directly instead of relying on `_require`'s message
    happening to be value-free.

    Coverage audit gap 5 (2026-09-21): only `IDP_CLIENT_ID` was ever
    exercised for the IDP leg of N6 -- `IDP_CLIENT_SECRET`/`IDP_REGION`
    were never independently unset, unlike `test_bootstrap.py`'s
    exhaustive parametrization over the platform's three vars.
    Parametrized here the same way, over every ENV-sourced `_require`d
    IDP var `make_idp_adapter()` reads (`adapter/idp_client.py`).
    `IDP_ORG_ID` is deliberately excluded (ADR-0004 A9, 2026-09-22): it
    is no longer read from the environment at all -- `org_id` reaches
    `make_idp_adapter` as a caller-supplied parameter, validated at the
    CLI boundary (`test_cli.py`'s `--org` tests), not by this N6 path."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv(missing_idp_var, raising=False)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "RuntimeError" in caplog.text
    assert "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c" not in caplog.text
    assert "distinctive-client-id-7b1e" not in caplog.text


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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0


def test_run_eval_sanitizes_the_caught_idp_configuration_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Reviewer suggestion (2026-09-21): `facade.py`'s
    `make_idp_adapter()` except-block used to log `str(exc)` unsanitized
    -- the one untrusted-ish boundary in the batch not routed through
    `sanitize_for_log`. ⚠️ Superseded 2026-09-21 by the GAP-6 fix
    (Branca `/harden`): rather than sanitizing (escaping only, not
    redacting) the message, this clause now logs NO part of `str(exc)`
    at all -- only the type name -- since `sanitize_for_log` cannot tell
    a safe variable name from a leaked golden value or secret. Pin the
    stronger contract directly."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    hostile_message = 'boom" forged="1'

    def _raise_hostile(*args: object, **kwargs: object) -> None:
        raise RuntimeError(hostile_message)

    monkeypatch.setattr(facade, "make_idp_adapter", _raise_hostile)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert hostile_message not in caplog.text
    assert json.dumps(hostile_message) not in caplog.text
    assert "RuntimeError" in caplog.text


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
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert "distinctive-pub-9f3a" not in caplog.text
    assert "distinctive-client-id-7b1e" not in caplog.text
    assert "distinctive-client-secret-NOT-A-REAL-SECRET-4d9c" not in caplog.text


# --- T-01.4.11/.2/.5: get_dataset / schema-drift / empty-set / N28 ------


def test_run_eval_returns_nonzero_on_an_empty_dataset_name(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """A6 (ADR-0004 amendment T-01.4.12 / DEBT-48; A8, 2026-09-22 user
    decision): `dataset_name` is a required parameter -- `--dataset` is a
    required CLI flag with no environment fallback (same shape as
    `--action`/`--version`, `cli.py::main`), and `run_eval` itself never
    reads `GOLDEN_DATASET_NAME` from the environment. This function still
    fail-closes (N6 shape, zero network calls) on an empty/whitespace-only
    value passed directly by any caller, since a caller bypassing the CLI
    could still pass one."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    def _fail_if_called() -> None:
        raise AssertionError("make_platform must not be called: zero network calls (N6)")

    monkeypatch.setattr(facade, "make_platform", _fail_if_called)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "   ", "org-t"
        )

    assert exit_code != 0
    assert "dataset_name" in caplog.text


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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "schema_drift" in caplog.text


def test_run_eval_reports_schema_drift_not_empty_set_when_both_would_fire(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """SPEC-01:342 / TP-40's actual AC, pinned at the `run_eval` seam
    (fresh Atchim gate finding on 98b336d..fd8c9c5): the pinned pre-run
    ORDER is drift, THEN empty-set -- a dataset that is BOTH empty AND
    drifted must report `schema_drift`, not `empty_set`, because
    `facade.py` calls `check_schema_drift` first. Unlike
    `test_prerun.py`'s same-named-in-spirit test (which calls
    `check_schema_drift` alone and can't distinguish ordering from
    single-guard behavior), this test drives `run_eval` itself: swapping
    the `check_schema_drift`/`check_empty_set` call order in `facade.py`
    makes this test go red, because an empty, non-drifted-looking-first
    dataset would then report `empty_set` before drift is ever checked."""
    dataset = _well_formed_dataset()
    dataset["items"] = []
    dataset["expected_output_schema"] = {"not": "the committed schema"}

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "schema_drift" in caplog.text
    assert "empty_set" not in caplog.text


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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "empty_set" in caplog.text


def test_run_eval_writes_no_marker_on_a_pre_run_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """ADR-0004 #14 / prerun.py's own docstring: 'a pre-run abort writes
    no run_status marker, because no run exists yet' -- `run_id` is only
    generated AFTER the pre-run chain passes (T-01.4.6), so there is
    nothing to mark. Pinned here at the `run_eval` seam (not just
    `prerun.py`'s unit tests) since `mark_run_status_best_effort` lives
    in `facade.py` and it is `facade.py`'s job never to call it before
    `run_id` exists."""
    dataset = _well_formed_dataset()
    dataset["items"] = []  # empty_set: a pre-run abort, no run_id yet

    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code != 0
    assert recording_platform.mark_run_status_calls == []


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
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

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
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.INFO):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert expected_golden_version in caplog.text


# --- pre-run checks pass -> the loop and record phase run ---------------


def test_run_eval_returns_zero_once_the_whole_run_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0


def test_run_eval_composes_the_experiment_name_before_hitting_the_loop_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")

    with caplog.at_level(logging.INFO):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

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
    _stub_make_idp_adapter_success(monkeypatch)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")

    action_id = "12345678-1234-1234-1234-123456789012"
    version = '1.0" forged="1'
    run_name = 'nightly" forged="1'
    experiment_name = run_name + "-01234567"

    with caplog.at_level(logging.INFO):
        run_eval(action_id, version, run_name, "idp-regression-golden", "org-t")

    assert f"run={json.dumps(run_name)}" in caplog.text
    assert f"experiment={json.dumps(experiment_name)}" in caplog.text
    assert f"action={json.dumps(action_id)}" in caplog.text
    assert f"version={json.dumps(version)}" in caplog.text


# --- T-01.4.6: the per-document loop + post-loop record_run -------------


def _two_item_dataset() -> dict[str, object]:
    from idp_regression.platform.schema import load_golden_schema

    golden = {"fields": {"total": {"value": "1250.00", "type": "number", "critical": True}}}
    return {
        "items": [
            {"item_id": "item-1", "document_id": "doc-1", "golden": golden},
            {"item_id": "item-2", "document_id": "doc-2", "golden": golden},
        ],
        "expected_output_schema": load_golden_schema(),
    }


def test_run_eval_returns_nonzero_when_idp_document_dir_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("IDP_DOCUMENT_DIR", raising=False)

    def _fail_if_called() -> None:
        raise AssertionError("make_platform must not be called: zero network calls (N6)")

    monkeypatch.setattr(facade, "make_platform", _fail_if_called)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "IDP_DOCUMENT_DIR" in caplog.text


def test_run_eval_resolves_document_id_to_a_path_under_idp_document_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setenv("IDP_DOCUMENT_DIR", "/custom-dir")
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    fake_idp = _stub_make_idp_adapter_success(monkeypatch, document_dir="/custom-dir")

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    assert fake_idp.calls == [("/custom-dir/doc-1", "12345678-1234-1234-1234-123456789012", "1.0")]


def test_run_eval_calls_record_run_exactly_once_with_every_document_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    assert len(recording_platform.record_run_calls) == 1
    call = recording_platform.record_run_calls[0]
    assert call["dataset_name"] == "idp-regression-golden"
    assert call["run_name"] == "nightly-01234567"
    assert call["run_id"] == "0123456789abcdef0123456789abcdef"
    records = call["records"]
    assert isinstance(records, list)
    assert len(records) == 1
    assert records[0]["item_id"] == "item-1"
    assert records[0]["document_id"] == "doc-1"
    assert {score["name"] for score in records[0]["scores"]} == {"field:total", "gate"}


def test_run_eval_passes_run_metadata_to_record_run_on_every_zero_exit_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """INV-04."""
    from idp_regression.platform.hashing import hash_dataset

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "9.9", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    metadata = recording_platform.record_run_calls[0]["metadata"]
    assert metadata == {
        "action_id": "12345678-1234-1234-1234-123456789012",
        "action_version": "9.9",
        "golden_version": hash_dataset(dataset["items"]),  # type: ignore[arg-type]
        "golden_dataset_name": "idp-regression-golden",
    }


def test_run_eval_marks_run_status_complete_after_a_successful_record_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    assert recording_platform.mark_run_status_calls == [
        {
            "run_id": "0123456789abcdef0123456789abcdef",
            "status": "complete",
            "action_id": "12345678-1234-1234-1234-123456789012",
            "action_version": "1.0",
            "golden_version": recording_platform.record_run_calls[0]["metadata"]["golden_version"],  # type: ignore[index]
            "golden_dataset_name": "idp-regression-golden",
        }
    ]
    # record_run happened strictly before the complete marker (ADR-0005 #9 step 4/5).
    assert recording_platform.record_run_calls
    assert recording_platform.mark_run_status_calls


def test_run_eval_never_calls_record_run_before_every_gate_is_computed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """INV-08: the gate result is computed BEFORE the platform write. Pin
    it with a mutation-style probe: `record_run` asserts every record it
    receives already carries a `gate` score -- if a future change moved
    `record_run` to be called per-document, mid-loop, before the LAST
    document's gate was computed, this would still pass per-call but the
    call-count/record-count assertion below would catch a mid-loop call
    directly: `record_run` must be called exactly once, with ALL records
    already built."""
    dataset = _two_item_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    path1, actual1 = _matching_actual_for("/documents", "doc-1")
    path2, actual2 = _matching_actual_for("/documents", "doc-2")
    fake_idp = _FakeIDPAdapter({path1: actual1, path2: actual2})
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    assert len(fake_idp.calls) == 2
    assert len(recording_platform.record_run_calls) == 1
    records = recording_platform.record_run_calls[0]["records"]
    assert isinstance(records, list)
    assert len(records) == 2
    for record in records:
        assert any(score["name"] == "gate" for score in record["scores"])


def test_run_eval_aborts_the_whole_run_and_stops_after_the_first_document_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """INV-06: no remaining document is processed after an abort, and
    `record_run` is never called (no partial run)."""
    from idp_regression.adapter.errors import IDPAuthenticationError

    dataset = _two_item_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    fake_idp = _FakeIDPAdapter(error=IDPAuthenticationError("token rejected"))
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert len(fake_idp.calls) == 1  # doc-2 never attempted
    assert recording_platform.record_run_calls == []
    assert "auth_failure" in caplog.text
    assert "doc-1" in caplog.text


@pytest.mark.parametrize(
    ("exception_factory", "expected_reason"),
    [
        (
            lambda: __import__(
                "idp_regression.adapter.errors", fromlist=["IDPAuthenticationError"]
            ).IDPAuthenticationError("bad creds"),
            "auth_failure",
        ),
        (
            lambda: __import__(
                "idp_regression.adapter.errors", fromlist=["IDPPollTimeoutError"]
            ).IDPPollTimeoutError("poll budget expired", last_status=None),
            "unknown_status_timeout",
        ),
        (
            lambda: __import__(
                "idp_regression.adapter.errors", fromlist=["IDPSubmitError"]
            ).IDPSubmitError("submit rejected"),
            "hard_failure",
        ),
        (
            lambda: __import__(
                "idp_regression.adapter.errors", fromlist=["IDPExecutionFailedError"]
            ).IDPExecutionFailedError("terminal failure", status="FAILED"),
            "hard_failure",
        ),
        (
            lambda: __import__(
                "idp_regression.adapter.errors", fromlist=["MalformedIDPOutputError"]
            ).MalformedIDPOutputError("unsafe_field_name", "rejected"),
            "malformed_actual",
        ),
    ],
)
def test_run_eval_maps_each_typed_idp_error_to_its_abort_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
    exception_factory: object,
    expected_reason: str,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    fake_idp = _FakeIDPAdapter(error=exception_factory())  # type: ignore[operator]
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert expected_reason in caplog.text


def test_run_eval_aborts_malformed_actual_from_classify(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Coverage audit gap 2 (2026-09-21): the `except MalformedActualError`
    raise site at `facade.py`'s per-document loop (classify/gate step) had
    no test -- unlike its `except MalformedGoldenError` neighbour right
    above it (provably unreachable, see that except-clause's own comment
    in `facade.py`: N28 already validates every golden item with the same
    validator `classify()` calls, before the loop even starts), this one
    IS reachable: `actual` comes from a live per-document `idp_adapter.
    extract()` call, and nothing pre-validates it the way N28
    pre-validates `golden` -- the `IDPAdapter` Protocol only promises the
    method returns a `NormalizedOutput`, so a non-conforming adapter (or,
    as here, a test double standing in for one) can still hand `classify()`
    a structurally malformed `actual`, which its own `_validate_actual`
    (N22-adjacent defense-in-depth) must catch. `normalize()` itself would
    never produce this shape (it always emits a dict `fields`) -- this
    fake `.extract()` bypasses `normalize()` entirely, exactly the way a
    non-conforming adapter implementation could."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    fake_idp = _FakeIDPAdapter(
        {
            os.path.join("/documents", "doc-1"): {
                "status": "SUCCEEDED",
                "fields": ["not", "a", "mapping"],
            }
        }
    )
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "malformed_actual" in caplog.text


def test_run_eval_writes_the_aborted_marker_on_a_per_document_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    from idp_regression.adapter.errors import IDPAuthenticationError

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    monkeypatch.setattr(facade, "generate_run_id", lambda: "0123456789abcdef0123456789abcdef")

    fake_idp = _FakeIDPAdapter(error=IDPAuthenticationError("token rejected"))
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code != 0
    assert len(recording_platform.mark_run_status_calls) == 1
    assert recording_platform.mark_run_status_calls[0]["status"] == "aborted"
    assert recording_platform.mark_run_status_calls[0]["run_id"] == (
        "0123456789abcdef0123456789abcdef"
    )


def test_run_eval_mark_run_status_failure_is_best_effort_and_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """ADR-0004 #14 non-blocking debt: the marker write is best-effort,
    one attempt, never retried -- a failure here must not itself crash
    `run_eval` or flip a would-be non-zero exit into something worse."""
    from idp_regression.adapter.errors import IDPAuthenticationError

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(
        dataset, mark_run_status_error=RuntimeError("platform unreachable")
    )
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    fake_idp = _FakeIDPAdapter(error=IDPAuthenticationError("token rejected"))
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert len(recording_platform.mark_run_status_calls) == 1  # exactly one attempt, no retry


def test_run_eval_returns_nonzero_when_record_run_raises_flush_failed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    from idp_regression.platform.errors import FlushFailedError

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(
        dataset, record_run_error=FlushFailedError("flush budget exhausted")
    )
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "flush_failed" in caplog.text
    assert len(recording_platform.mark_run_status_calls) == 1
    assert recording_platform.mark_run_status_calls[0]["status"] == "aborted"


def test_run_eval_returns_nonzero_when_record_run_raises_experiment_record_failed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    from idp_regression.platform.errors import ExperimentRecordFailedError

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(
        dataset, record_run_error=ExperimentRecordFailedError("structural check failed")
    )
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "hard_failure" in caplog.text


def test_run_eval_returns_nonzero_on_a_failing_gate_but_still_records_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """A gate FAIL is not an abort (ADR-0004 exit-code contract): the run
    still completes, `record_run` still runs with the FAIL score, and the
    `complete` marker is still written -- only the exit code goes
    non-zero."""
    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    recording_platform = _RecordingPlatform(dataset)
    monkeypatch.setattr(facade, "make_platform", lambda: recording_platform)

    path = os.path.join("/documents", "doc-1")
    mismatched_actual = {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": "9999.99", "confidence": 0.5}},
    }
    fake_idp = _FakeIDPAdapter({path: mismatched_actual})
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code != 0
    assert len(recording_platform.record_run_calls) == 1
    scores = recording_platform.record_run_calls[0]["records"][0]["scores"]  # type: ignore[index]
    gate_score = next(score for score in scores if score["name"] == "gate")
    assert gate_score["value"] == "FAIL"
    assert recording_platform.mark_run_status_calls == [
        {
            "run_id": recording_platform.record_run_calls[0]["run_id"],
            "status": "complete",
            "action_id": "12345678-1234-1234-1234-123456789012",
            "action_version": "1.0",
            "golden_version": recording_platform.record_run_calls[0]["metadata"]["golden_version"],  # type: ignore[index]
            "golden_dataset_name": "idp-regression-golden",
        }
    ]


# --- Path containment (security fix, N28 is JSON-shape only) -----------


def _dataset_with_document_id(document_id: str) -> dict[str, object]:
    from idp_regression.platform.schema import load_golden_schema

    return {
        "items": [
            {
                "item_id": "item-1",
                "document_id": document_id,
                "golden": {
                    "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}}
                },
            }
        ],
        "expected_output_schema": load_golden_schema(),
    }


@pytest.mark.parametrize(
    "hostile_document_id",
    [
        "../../etc/passwd",
        "../outside.json",
        "a/../../outside.json",
        "/etc/passwd",
    ],
)
def test_resolve_document_path_rejects_traversal_and_absolute_ids(
    hostile_document_id: str,
) -> None:
    with pytest.raises(facade._PathContainmentViolation):
        facade._resolve_document_path("/documents", hostile_document_id)


def test_resolve_document_path_rejects_a_symlink_escaping_the_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    escape_link = root / "escape.json"
    escape_link.symlink_to(outside)

    with pytest.raises(facade._PathContainmentViolation):
        facade._resolve_document_path(str(root), "escape.json")


def test_resolve_document_path_rejects_a_nul_byte(tmp_path: Path) -> None:
    """R-1: `os.path.realpath` raises a raw `ValueError` on an embedded
    NUL -- must surface as `_PathContainmentViolation`, not escape."""
    root = tmp_path / "documents"
    root.mkdir()
    with pytest.raises(facade._PathContainmentViolation):
        facade._resolve_document_path(str(root), "a\x00.pdf")


def test_resolve_document_path_rejects_empty_string() -> None:
    with pytest.raises(facade._PathContainmentViolation):
        facade._resolve_document_path("/documents", "")


def test_resolve_document_path_rejects_non_str() -> None:
    """R-1: a non-`str` `document_id` (e.g. `5`) raises a raw `TypeError`
    from `os.path.isabs` -- must surface as `_PathContainmentViolation`."""
    with pytest.raises(facade._PathContainmentViolation):
        facade._resolve_document_path("/documents", cast(str, 5))


def test_resolve_document_path_happy_path_still_resolves(tmp_path: Path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    (root / "doc-1.json").write_text("{}")

    resolved = facade._resolve_document_path(str(root), "doc-1.json")

    assert resolved == str((root / "doc-1.json").resolve())


def test_run_eval_aborts_on_path_containment_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hostile_document_id = "../../etc/passwd"
    document_dir = tmp_path / "documents"
    document_dir.mkdir()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setenv("IDP_DOCUMENT_DIR", str(document_dir))
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    monkeypatch.setattr(
        facade,
        "make_platform",
        lambda: _FakePlatform(_dataset_with_document_id(hostile_document_id)),
    )
    fake_idp = _FakeIDPAdapter({})
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "path_containment_violation" in caplog.text
    assert fake_idp.calls == []
    # The sanitized document_id is logged (INV-02-compliant identification)
    # but the resolved/candidate filesystem path never is.
    assert json.dumps(hostile_document_id) in caplog.text
    assert "/etc/passwd" not in caplog.text.replace(json.dumps(hostile_document_id), "")


# --- T-01.4.8: observability (NFR N10) ---------------------------------


def test_run_eval_logs_run_start_with_item_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code == 0
    assert "items=1" in caplog.text


def test_run_eval_logs_run_end_with_outcome_exit_code_counts_and_elapsed_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code == 0
    end_lines = [line for line in caplog.text.splitlines() if "run_end" in line]
    assert len(end_lines) == 1
    assert "outcome=success" in end_lines[0]
    assert "exit_code=0" in end_lines[0]
    assert "pass_count=1" in end_lines[0]
    assert "fail_count=0" in end_lines[0]
    assert "elapsed_seconds=" in end_lines[0]


def test_run_eval_logs_run_end_with_elapsed_on_a_pre_run_credential_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The elapsed clock and run_end line must be emitted even when the
    run never reaches the loop -- not only on a full success (Zangado's
    S-01.2 note)."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    end_lines = [line for line in caplog.text.splitlines() if "run_end" in line]
    assert len(end_lines) == 1
    assert "outcome=aborted" in end_lines[0]
    assert "exit_code=1" in end_lines[0]
    assert "elapsed_seconds=" in end_lines[0]


def test_run_eval_logs_run_end_with_elapsed_on_a_per_document_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    from idp_regression.adapter.errors import IDPAuthenticationError

    monkeypatch.setattr(
        facade,
        "make_idp_adapter",
        lambda org_id: _FakeIDPAdapter(error=IDPAuthenticationError("boom")),
    )

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    end_lines = [line for line in caplog.text.splitlines() if "run_end" in line]
    assert len(end_lines) == 1
    assert "outcome=aborted" in end_lines[0]
    assert "exit_code=1" in end_lines[0]
    assert "pass_count=0" in end_lines[0]
    assert "fail_count=0" in end_lines[0]
    assert "elapsed_seconds=" in end_lines[0]


def test_run_eval_logs_elapsed_for_each_document_linked_to_its_document_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.INFO):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    doc_lines = [
        line
        for line in caplog.text.splitlines()
        if "document processed" in line and "document_id=" in line
    ]
    assert len(doc_lines) == 1
    assert "elapsed_seconds=" in doc_lines[0]


def test_run_eval_never_logs_golden_or_actual_field_values_in_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sentinel golden/actual VALUES planted in the fixtures must never
    reach any telemetry line -- only document_id/gate/outcome/counts."""
    from idp_regression.platform.schema import load_golden_schema

    sentinel_golden_value = "SENTINEL-GOLDEN-VALUE-9f21"
    sentinel_actual_value = "SENTINEL-ACTUAL-VALUE-4b7e"
    dataset = {
        "items": [
            {
                "item_id": "item-1",
                "document_id": "doc-1",
                "golden": {
                    "fields": {
                        "total": {
                            "value": sentinel_golden_value,
                            "type": "text",
                            "critical": True,
                        }
                    }
                },
            }
        ],
        "expected_output_schema": load_golden_schema(),
    }
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))
    fake_idp = _FakeIDPAdapter(
        {
            "/documents/doc-1": {
                "status": "SUCCEEDED",
                "fields": {"total": {"value": sentinel_actual_value, "confidence": 0.9}},
            }
        }
    )
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    with caplog.at_level(logging.INFO):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert sentinel_golden_value not in caplog.text
    assert sentinel_actual_value not in caplog.text


def test_run_eval_never_logs_the_document_dir_path_in_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel_dir = "/sekret-document-root-9f21"
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setenv("IDP_DOCUMENT_DIR", sentinel_dir)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)
    _stub_make_idp_adapter_success(monkeypatch, document_dir=sentinel_dir)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code == 0
    assert sentinel_dir not in caplog.text


# --- HARDEN-01 GAP-1: no untyped exception may escape run_eval ---------


@pytest.mark.parametrize(
    "bad_dataset",
    [
        {"expected_output_schema": None},  # no "items" key at all -> KeyError
        {"items": "not-a-list", "expected_output_schema": None},
        {"items": 42, "expected_output_schema": None},
        {"items": [None], "expected_output_schema": None},
        # no document_id key on the item:
        {"items": [{"item_id": "i1", "golden": {}}], "expected_output_schema": None},
        {
            "items": [{"item_id": "i1", "document_id": 5, "golden": {}}],
            "expected_output_schema": None,
        },  # non-str document_id
        # Coverage audit gap 6 (2026-09-21): the item_id shape guard is
        # dead -- removing it left the suite green, though its
        # document_id sibling (above) was covered. Same three cases,
        # mirrored for item_id: missing key, non-str, empty string.
        {
            "items": [{"document_id": "doc-1", "golden": {}}],
            "expected_output_schema": None,
        },  # no item_id key on the item
        {
            "items": [{"item_id": 5, "document_id": "doc-1", "golden": {}}],
            "expected_output_schema": None,
        },  # non-str item_id
        {
            "items": [{"item_id": "", "document_id": "doc-1", "golden": {}}],
            "expected_output_schema": None,
        },  # empty-string item_id
    ],
)
def test_run_eval_never_escapes_on_a_malformed_dataset_shape(
    bad_dataset: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-1 (HARDEN-01): a `Dataset` whose shape drifts from what the
    orchestrator assumes used to raise a raw `KeyError`/`TypeError`
    (e.g. no `items` key, `items` not a list, an item missing
    `document_id`, or a non-`str` `document_id`) straight out of
    `run_eval`, breaking its own `-> int` contract. Now mapped to
    `dataset_fetch_failed` (the same reason `get_dataset` failures use)
    -- no marker (no run exists pre-run), a `run_end` line, exit 1."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(bad_dataset))

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "dataset_fetch_failed" in caplog.text
    assert "run_end" in caplog.text


def test_run_eval_never_escapes_on_an_untyped_pre_run_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-1: an untyped exception raised anywhere in the pre-run chain
    (before `run_id` exists) must not escape -- no marker (no run yet),
    but a `run_end` line and exit 1."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    def _boom(dataset: object) -> None:
        raise ValueError("unexpected pre-run failure")

    monkeypatch.setattr(facade, "check_schema_drift", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text


def test_run_eval_writes_the_aborted_marker_on_an_untyped_in_loop_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-1: an untyped exception raised AFTER `run_id` exists (in the
    per-document loop or the record phase) must not escape either -- and,
    unlike the pre-run case, a run DOES exist, so the best-effort
    `aborted` marker must be written exactly once (ADR-0004 #14)."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    platform = _RecordingPlatform(_well_formed_dataset())
    monkeypatch.setattr(facade, "make_platform", lambda: platform)
    _stub_make_idp_adapter_success(monkeypatch)

    def _boom(golden: object, actual: object) -> None:
        raise ValueError("unexpected classify failure")

    monkeypatch.setattr(facade, "classify", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text
    assert [c["status"] for c in platform.mark_run_status_calls] == ["aborted"]


def test_run_eval_never_escapes_when_get_dataset_raises_an_untyped_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C-1 (Atchim gate, 2026-09-21): `get_dataset`/`_validate_dataset_shape`
    used to sit under a try-block with ONLY `except DatasetFetchFailedError`
    -- an untyped exception raised by `get_dataset` itself (a transport
    timeout, say) escaped `run_eval` entirely, breaking its `-> int`
    contract, emitting no `run_end` line (N10), and -- via the CLI's own
    fallback -- reaching a log line built from `sanitize_for_log(str(exc))`,
    which quotes but does not redact (INV-02). Reproduced with a
    `TimeoutError` whose message carries a fake bearer token; none of it
    may reach the logs."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    sentinel = "SEKRIT-token-should-never-be-logged"

    class _RaisingGetDatasetPlatform:
        def get_dataset(self, name: str) -> object:
            raise TimeoutError(f"socket hung talking to lf-host token={sentinel}")

        def record_run(self, **kwargs: object) -> None:
            pass

        def mark_run_status(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(facade, "make_platform", lambda: _RaisingGetDatasetPlatform())

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text
    assert sentinel not in caplog.text
    assert "TimeoutError" in caplog.text


@pytest.mark.parametrize(
    "seam",
    ["validate_platform_credentials", "make_idp_adapter", "make_platform", "hash_dataset"],
)
def test_run_eval_never_escapes_on_an_untyped_exception_at_any_pre_run_seam(
    seam: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C-1 widened (Branca `/harden` re-run, 2026-09-21): the first merge
    closed only the `get_dataset` seam -- `make_platform`,
    `make_idp_adapter` and `validate_platform_credentials` each still sat
    under their OWN narrow tuple with no catch-all, and the window
    between the two former try-blocks (`hash_dataset`, `generate_run_id`,
    `compose_experiment_name`) had no try at all. One repro per
    surviving seam: each raises an untyped exception (a type NOT in that
    seam's own narrow tuple, so it must hit the shared catch-all, not the
    typed one) carrying a secret sentinel -- none of it may escape, be
    marked, or reach the logs."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    sentinel = "SEKRIT-token-should-never-be-logged"
    platform = _RecordingPlatform(_well_formed_dataset())

    def _boom(*args: object, **kwargs: object) -> object:
        raise TimeoutError(f"unexpected failure token={sentinel}")

    if seam == "make_platform":
        monkeypatch.setattr(facade, "make_platform", _boom)
    else:
        monkeypatch.setattr(facade, "make_platform", lambda: platform)
        monkeypatch.setattr(facade, seam, _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text
    assert sentinel not in caplog.text
    assert "TimeoutError" in caplog.text
    assert platform.mark_run_status_calls == []


def test_run_eval_still_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """GAP-1's catch-all must be `except Exception`, never a bare
    `except:`/`except BaseException:` -- a `KeyboardInterrupt` must still
    propagate out of `run_eval`, not be swallowed into a non-zero exit."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    def _boom(dataset: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(facade, "check_schema_drift", _boom)

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )


def test_run_eval_never_escapes_on_a_cancelled_error_from_the_tail_complete_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Atchim gate finding (2026-09-21, live-reproduced): GAP-5's fifth
    site. `_mark_run_status_best_effort` only guarded `except Exception`
    -- unlike every OTHER catch-all in this module (all widened to
    `(Exception, asyncio.CancelledError)` for GAP-5) -- and its tail
    `status="complete"` call sits OUTSIDE every try-block in `run_eval`,
    after a fully successful run. A `CancelledError` raised by the
    platform on that final best-effort marker write therefore escaped
    `run_eval` raw, unlike an identical error at any earlier site (all of
    which are covered above)."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    import asyncio

    platform = _RecordingPlatform(
        _well_formed_dataset(), mark_run_status_error=asyncio.CancelledError()
    )
    monkeypatch.setattr(facade, "make_platform", lambda: platform)
    _stub_make_idp_adapter_success(monkeypatch)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code == 0  # the run itself passed; the tail marker is best-effort only
    assert "run_end" in caplog.text
    assert [c["status"] for c in platform.mark_run_status_calls] == ["complete"]


# --- GAP-6 (Branca /harden, 2026-09-21): the widened
# `except (RuntimeError, IDPConfigurationError, ValueError,
# PlatformConfigurationError)` clause logs `sanitize_for_log(str(exc))` --
# that clause used to sit under only two VETTED constructors
# (make_idp_adapter/make_platform's own guards), but 7d7aed3 widened the
# surrounding try to span the WHOLE pre-run chain, including
# `check_schema_drift` and `validate_golden_set`, both of which handle
# golden content and can raise a bare `RuntimeError`/`ValueError` built
# from it. `sanitize_for_log` only escapes/quotes -- it does not redact --
# so a golden value or a credential sentinel embedded in one of those
# messages reaches the log verbatim (aside from quoting). Also:
# `RecursionError` is a `RuntimeError` subclass, so a stack-overflow deep
# in golden validation lands in THIS clause, not the safe
# type-name-only catch-all.


@pytest.mark.parametrize(
    "seam,exc_type",
    [
        ("check_schema_drift", RuntimeError),
        ("validate_golden_set", ValueError),
        ("hash_dataset", RuntimeError),
    ],
)
def test_run_eval_never_logs_a_planted_secret_from_the_narrow_pre_run_except_clause(
    seam: str,
    exc_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-6: a golden value / credential sentinel planted in the raised
    message must never reach any log line -- only the exception's type
    name (never `str(exc)`) may appear."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    sentinel = "SEKRIT-golden-value-and-credential-should-never-be-logged"

    def _boom(*args: object, **kwargs: object) -> None:
        raise exc_type(f"invalid golden field total={sentinel}")

    monkeypatch.setattr(facade, seam, _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert sentinel not in caplog.text
    assert exc_type.__name__ in caplog.text


def test_run_eval_never_logs_a_planted_secret_from_make_platforms_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-6, the originally-vetted seam: even `make_platform`'s own
    `PlatformConfigurationError` must not have its message logged
    verbatim -- a future raiser of this type is not guaranteed to keep
    its message value-free, so the fix must be structural (type name
    only), not seam-by-seam."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    from idp_regression.platform.errors import PlatformConfigurationError

    sentinel = "SEKRIT-host-and-secret-should-never-be-logged"

    def _boom() -> object:
        raise PlatformConfigurationError(f"LANGFUSE_HOST/LANGFUSE_BASE_URL disagree: {sentinel}")

    monkeypatch.setattr(facade, "make_platform", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert sentinel not in caplog.text
    assert "PlatformConfigurationError" in caplog.text


def test_run_eval_never_logs_a_planted_secret_from_make_idp_adapters_value_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAP-6, the other originally-vetted seam (`make_idp_adapter`'s
    `success_statuses`-not-a-subset `ValueError`)."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)

    sentinel = "SEKRIT-status-value-should-never-be-logged"

    def _boom(org_id: str) -> object:
        raise ValueError(f"success_statuses not a subset of terminal_statuses: {sentinel}")

    monkeypatch.setattr(facade, "make_idp_adapter", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert sentinel not in caplog.text
    assert "ValueError" in caplog.text


# --- GAP-5 (Branca /harden, 2026-09-21): `asyncio.CancelledError` is a
# `BaseException` subclass (Python 3.8+), NOT an `Exception` subclass --
# it slips straight through `except Exception`, escaping both of
# `run_eval`'s catch-alls raw. `KeyboardInterrupt`/`SystemExit` must still
# propagate (test above), so this must be an explicit addition, not a
# switch to a bare `except BaseException`.


def test_run_eval_never_escapes_on_a_cancelled_error_pre_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    import asyncio

    def _boom(dataset: object) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(facade, "check_schema_drift", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text


def test_run_eval_never_escapes_on_a_cancelled_error_in_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    platform = _RecordingPlatform(_well_formed_dataset())
    monkeypatch.setattr(facade, "make_platform", lambda: platform)
    _stub_make_idp_adapter_success(monkeypatch)

    import asyncio

    def _boom(golden: object, actual: object) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(facade, "classify", _boom)

    with caplog.at_level(logging.INFO):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )

    assert exit_code != 0
    assert "run_end" in caplog.text
    assert [c["status"] for c in platform.mark_run_status_calls] == ["aborted"]


def test_run_eval_still_propagates_asyncio_cancelled_error_is_not_the_goal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Sanity pin for the KeyboardInterrupt/SystemExit test above: adding
    `asyncio.CancelledError` to the catch-all tuple must NOT change that
    `KeyboardInterrupt` still propagates."""
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    _stub_make_platform_with_a_well_formed_dataset(monkeypatch)

    def _boom(dataset: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(facade, "check_schema_drift", _boom)

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            "12345678-1234-1234-1234-123456789012",
            "1.0",
            "nightly",
            "idp-regression-golden",
            "org-t",
        )


# --- ADR-0007 Option E: the local run artifact -------------------------


def test_run_eval_writes_the_full_verdict_map_to_the_local_run_artifact_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Persists what `classify()` already computed and `facade.py`
    otherwise discards -- no new extraction, no new fetch (ADR-0007)."""
    from idp_regression.orchestration import run_artifact

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))
    monkeypatch.setattr(facade, "generate_run_id", lambda: "artifact-run-id-1")
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
    written = json.loads(
        Path(run_artifact.artifact_path("artifact-run-id-1")).read_text(encoding="utf-8")
    )
    assert written["artifact-run-id-1"]["doc-1"]["total"] == {
        "verdict": "match",
        "expected": "1250.00",
        "actual": "1250.00",
        "confidence": 0.99,
        "critical": True,
        "type": "number",
    }


def test_run_eval_writes_a_partial_local_run_artifact_on_an_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The whole run aborts on the second document (INV-06), but the
    artifact still carries the first document's verdict map -- exactly
    the run a human is most likely to want "what did it extract instead"
    for."""
    from idp_regression.adapter.errors import IDPAuthenticationError
    from idp_regression.orchestration import run_artifact

    dataset = _two_item_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))
    monkeypatch.setattr(facade, "generate_run_id", lambda: "artifact-run-id-2")

    path1, actual1 = _matching_actual_for("/documents", "doc-1")
    fake_idp = _FakeIDPAdapter({path1: actual1})

    def _extract(document_path: str, action_id: str, version: str) -> object:
        fake_idp.calls.append((document_path, action_id, version))
        if document_path == path1:
            return actual1
        raise IDPAuthenticationError("token rejected")

    fake_idp.extract = _extract  # type: ignore[method-assign]
    monkeypatch.setattr(facade, "make_idp_adapter", lambda org_id: fake_idp)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code != 0
    written = json.loads(
        Path(run_artifact.artifact_path("artifact-run-id-2")).read_text(encoding="utf-8")
    )
    assert list(written["artifact-run-id-2"].keys()) == ["doc-1"]


def test_run_eval_exit_code_is_unaffected_by_a_run_artifact_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Binding obligation (ADR-0007): artifact writing must not be able
    to fail a run that otherwise passed -- the gate is the contract
    (INV-08, CT-04). Goes through the REAL `write_run_artifact` (its own
    internal best-effort try/except is the thing under test here, not a
    test double standing in for it)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "makedirs", _boom)

    dataset = _well_formed_dataset()
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))
    _stub_make_idp_adapter_success(monkeypatch)

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden", "org-t"
    )

    assert exit_code == 0
