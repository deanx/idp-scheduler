"""T-01.4.14 (DEBT-23, NFR N5): `IDPExecutionFailedError.status` is an
untrusted, IDP-controlled string. Route it through
`idp_regression.adapter.transport.sanitize_for_log` before it reaches any
log line or telemetry field.

Note: the adapter already caps/strips non-printable chars at
construction (`_sanitize_status`, /test Scenario B item 6) — this is a
SEPARATE, additional layer at the orchestration log boundary. A quote
character IS printable, so it survives `_sanitize_status` unescaped; it
is `sanitize_for_log`'s json.dumps-style quoting that neutralizes it for
safe interpolation into a structured `key=value` log line (the same
pattern already used at `idp_regression.platform.transport.sanitize_for_log`
and `idp_regression.adapter.transport.sanitize_for_log`).
"""

from __future__ import annotations

import os

from idp_regression.adapter.errors import IDPExecutionFailedError
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.orchestration.log_sanitize import (
    format_execution_failed_status_for_log,
    frame_location,
)


def test_format_execution_failed_status_matches_sanitize_for_log() -> None:
    """DEBT-44 gate, fifth instance, suggestion (2026-09-21): pin against
    the SHARED `sanitize_for_log` function this module delegates to, not
    a hardcoded `json.dumps` call -- if the sanitizer ever gains a length
    cap or extra control-char stripping, a `json.dumps`-pinned test would
    silently drift out of sync with what `format_execution_failed_status_for_log`
    actually does, rather than following it."""
    exc = IDPExecutionFailedError("boom", status="FAILED")
    assert format_execution_failed_status_for_log(exc) == sanitize_for_log("FAILED")


def test_format_execution_failed_status_escapes_an_embedded_quote() -> None:
    """Mutation-catching: if the orchestrator interpolated `exc.status`
    raw instead of through `sanitize_for_log`, this quote would reach a
    log line unescaped and could forge a trailing `key="value"` pair."""
    exc = IDPExecutionFailedError("boom", status='FAILED" forged="1')
    rendered = format_execution_failed_status_for_log(exc)
    assert rendered == sanitize_for_log('FAILED" forged="1')
    assert '\\"' in rendered


def test_format_execution_failed_status_is_wrapped_in_quotes() -> None:
    """A raw (unsanitized) status would not be JSON-quoted -- this pins
    that the sanitizer, not a passthrough, produced the value."""
    exc = IDPExecutionFailedError("boom", status="RUNNING")
    rendered = format_execution_failed_status_for_log(exc)
    assert rendered.startswith('"') and rendered.endswith('"')
    assert rendered != exc.status


# --- Atchim gate suggestion (2026-09-21): frame_location must not
# disclose this deployment's absolute directory layout. Shared by
# facade.py and cli.py (previously duplicated as two private
# `_frame_location` functions, one per module).


def test_frame_location_never_returns_an_absolute_path() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        location = frame_location(exc)

    filename = location.rsplit(":", 2)[0]
    assert not os.path.isabs(filename)
    assert location.endswith(":RuntimeError") is False  # sanity: has a real frame name


def test_frame_location_is_package_relative_for_a_facade_frame() -> None:
    """A frame inside `idp_regression` itself renders as
    `idp_regression/<...>.py:<line>:<func>` -- never a machine-specific
    absolute prefix like `/Users/alex/...`."""
    from idp_regression.orchestration import facade

    try:
        facade._resolve_document_path("/documents", "\x00")
    except Exception as exc:  # noqa: BLE001 - deliberately triggering _PathContainmentViolation
        location = frame_location(exc)

    assert location.startswith("idp_regression" + os.sep)
    assert "/Users/" not in location
