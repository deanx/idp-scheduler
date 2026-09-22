"""T-01.4.7 (CT-04, NFR N11): `run_eval` exit-code contract.

The contract (docs/design/CONTRACTS.md CT-04): `run_eval` exits `0` iff
every gate is `PASS` and no error occurred; every abort reason exits
non-zero. There are no per-reason exit codes -- the abort *reason* is
logged for observability and never fragments the exit-code namespace
(`orchestration/errors.py`'s own docstring).

This suite is parametrized over the WHOLE `AbortReason` taxonomy read
from `orchestration/errors.py` itself (not hardcoded). ⚠️ Corrected
2026-09-21 (S-1 gate finding): the parametrized tests below fabricate
`RunAborted(reason, ...)` through a monkeypatch of `check_schema_drift`,
so on their own they only re-prove the ONE generic
`except RunAborted` catch every abort funnels through -- adding a new
`AbortReason` member to the `Literal` with no real raise site anywhere
in `src/` still passes every test in this file, because `get_args`
would simply hand the new member to the same fabricated-raise
machinery. `test_every_abort_reason_has_a_real_raise_site` below is the
one that actually fails by construction on that gap: it greps
`src/idp_regression/**/*.py` (excluding `errors.py`'s own `Literal`
declaration) for each reason literal appearing at a real raise/log
site, independent of this file's own fakes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import get_args

import pytest

from idp_regression.orchestration import facade
from idp_regression.orchestration.errors import AbortReason, RunAborted
from idp_regression.orchestration.facade import run_eval

ALL_ABORT_REASONS: tuple[AbortReason, ...] = get_args(AbortReason)


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


def _disable_dotenv_file_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]


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


class _FakePlatform:
    def __init__(
        self,
        dataset: object,
        *,
        record_run_error: Exception | None = None,
    ) -> None:
        self._dataset = dataset
        self._record_run_error = record_run_error

    def get_dataset(self, name: str) -> object:
        return self._dataset

    def record_run(self, **kwargs: object) -> None:
        if self._record_run_error is not None:
            raise self._record_run_error

    def mark_run_status(self, *args: object, **kwargs: object) -> None:
        pass


class _FakeIDPAdapter:
    def __init__(self, outputs: dict[str, object], *, error: Exception | None = None) -> None:
        self._outputs = outputs
        self._error = error

    def extract(self, document_path: str, action_id: str, version: str) -> object:
        if self._error is not None:
            raise self._error
        return self._outputs[document_path]


def _matching_actual_for(
    document_dir: str, document_id: str, *, total: str = "1250.00"
) -> tuple[str, dict[str, object]]:
    import os

    path = os.path.join(document_dir, document_id)
    return path, {
        "status": "SUCCEEDED",
        "fields": {"total": {"value": total, "confidence": 0.99}},
    }


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    _disable_dotenv_file_loading(monkeypatch, tmp_path)
    _set_all_credential_env(monkeypatch)


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> int:
    _base_env(monkeypatch, tmp_path)
    return run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden"
    )


# --- Exhaustive reason -> non-zero mapping ------------------------------


@pytest.mark.parametrize("reason", ALL_ABORT_REASONS)
def test_every_abort_reason_exits_non_zero(
    reason: AbortReason,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """Force `RunAborted(reason, ...)` to be raised inside the pre-run
    `try: check_schema_drift(...) / check_empty_set(...) /
    validate_golden_set(...) except RunAborted` block -- the earliest
    point in `run_eval` that catches `RunAborted` generically, by any
    `.reason` -- by monkeypatching `check_schema_drift` to raise it for
    each taxonomy member in turn. `run_eval` never dispatches on
    `.reason` to decide 0 vs non-zero (CT-04: no per-reason exit codes;
    every `except RunAborted` block simply returns 1), so this exercises
    the SAME generic catch that every real abort site -- pre-run and
    in-loop alike -- ultimately funnels through. If a future refactor
    ever narrows that catch to a subset of reasons (e.g. an `if exc.reason
    in {...}` branch that silently falls through to 0 for anything else),
    a new/renamed reason would surface here as a wrong exit code, not an
    uncaught exception -- so the assertion is on the exit code, not on
    "did this raise", which is what actually protects CT-04.
    """
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        facade, "make_platform", lambda: _FakePlatform(_well_formed_dataset())
    )

    def _raise(dataset: object) -> None:
        raise RunAborted(reason, f"forced for reason={reason}")

    monkeypatch.setattr(facade, "check_schema_drift", _raise)

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden"
        )

    assert exit_code != 0
    assert reason in caplog.text


def test_taxonomy_is_read_from_errors_module_not_hardcoded() -> None:
    """Guards the parametrization itself: every reason this batch's
    Dengoso ticket named explicitly must still be present in the live
    taxonomy (a sanity floor, not a substitute for the exhaustive
    `get_args` parametrization above)."""
    expected_floor = {
        "unknown_status_timeout",
        "hard_failure",
        "auth_failure",
        "empty_set",
        "dataset_fetch_failed",
        "schema_drift",
        "malformed_golden",
        "malformed_actual",
        "flush_failed",
    }
    assert expected_floor.issubset(set(ALL_ABORT_REASONS))


def test_every_abort_reason_has_a_real_raise_site() -> None:
    """S-1: the ONLY test in this file that would fail if a new
    `AbortReason` member were added to the `Literal` with no real raise
    site under `src/idp_regression/` -- every other test here fabricates
    `RunAborted(reason, ...)` via a monkeypatch, so it re-proves nothing
    about whether `reason` is ever actually raised by production code.

    Scans every `.py` file under `src/idp_regression/` EXCEPT
    `orchestration/errors.py` itself (whose `Literal` definition and
    module docstring name every reason in prose -- that's the
    declaration, not evidence of a raise site, and including it would
    make this test pass vacuously for a brand-new, never-raised member).
    A reason must appear as a substring somewhere in the remaining
    corpus -- in practice that is always `raise _abort(...)`,
    `raise RunAborted(...)`, or (for `dataset_fetch_failed`, which
    `facade.py` handles with a direct `except .../return 1`, never
    through `RunAborted`) the `logger.error` call in that except block."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "idp_regression"
    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in src_root.rglob("*.py")
        if path.name != "errors.py"
    )
    for reason in ALL_ABORT_REASONS:
        pattern = re.compile(re.escape(reason))
        assert pattern.search(corpus), (
            f"AbortReason {reason!r} has no real raise/log site outside "
            "orchestration/errors.py -- the Literal and production code "
            "have drifted apart"
        )


# --- Ordering case: empty set AND drifted schema -> schema_drift -------


def test_empty_set_and_drifted_schema_reports_schema_drift(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """S-01.4-KICKOFF.md pinned order: `check_schema_drift` runs BEFORE
    `check_empty_set` and never looks at `items` -- an empty dataset
    whose schema also drifted reports `schema_drift`, not `empty_set`."""
    _base_env(monkeypatch, tmp_path)
    dataset: dict[str, object] = {"items": [], "expected_output_schema": None}
    monkeypatch.setattr(facade, "make_platform", lambda: _FakePlatform(dataset))

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden"
        )

    assert exit_code != 0
    assert "schema_drift" in caplog.text
    assert "empty_set" not in caplog.text


# --- The two exit-0/non-zero happy-path cases --------------------------


def test_all_gates_pass_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _base_env(monkeypatch, tmp_path)
    document_dir = "/documents"
    document_id = "doc-1"
    path, actual = _matching_actual_for(document_dir, document_id)
    monkeypatch.setattr(
        facade, "make_idp_adapter", lambda: _FakeIDPAdapter({path: actual})
    )
    monkeypatch.setattr(
        facade, "make_platform", lambda: _FakePlatform(_well_formed_dataset())
    )

    exit_code = run_eval(
        "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden"
    )

    assert exit_code == 0


def test_any_gate_fail_exits_non_zero_with_no_error_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    """A `FAIL` gate is a normal, error-free completion of the run --
    `record_run`/`mark_run_status` are still reached, and no ERROR line
    is logged (only the in-process gate result flips the exit code,
    INV-08)."""
    _base_env(monkeypatch, tmp_path)
    document_dir = "/documents"
    document_id = "doc-1"
    # A mismatching total makes the classifier gate FAIL, not error.
    path, actual = _matching_actual_for(document_dir, document_id, total="0.01")
    monkeypatch.setattr(
        facade, "make_idp_adapter", lambda: _FakeIDPAdapter({path: actual})
    )
    monkeypatch.setattr(
        facade, "make_platform", lambda: _FakePlatform(_well_formed_dataset())
    )

    with caplog.at_level(logging.ERROR):
        exit_code = run_eval(
            "12345678-1234-1234-1234-123456789012", "1.0", "nightly", "idp-regression-golden"
        )

    assert exit_code != 0
    assert caplog.text == ""
