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

import json
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
    # `not os.path.isabs(...)` alone passes on a `..`-traversal leak
    # (e.g. `../../../Users/alex/...`) -- pin the stronger property too.
    assert os.pardir not in filename.split(os.sep)
    assert os.path.expanduser("~") not in location
    assert location.endswith(":RuntimeError") is False  # sanity: has a real frame name


def test_frame_location_for_an_external_frame_never_traverses_up_to_the_home_dir() -> None:
    """Atchim gate (2026-09-21, live-reproduced): `os.path.relpath` does
    NOT clamp -- when the innermost frame lives outside `idp_regression`
    (the normal case for an unanticipated exception raised deep inside a
    third-party/stdlib call, e.g. the Langfuse SDK, httpx, or here
    `json`), it walks back up the tree with `..` segments and re-emits
    the full absolute path underneath them (username, home directory,
    interpreter layout) -- the exact INV-02 disclosure GAP-7 already
    closed for the package-internal case. An external frame must render
    as `<external>/{basename}` instead."""
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        location = frame_location(exc)

    assert ".." not in location.split(":")[0].split(os.sep)
    assert os.path.expanduser("~") not in location
    assert not os.path.isabs(location.split(":")[0])
    assert location.startswith("<external>" + os.sep)


def test_frame_location_returns_no_traceback_placeholder_when_traceback_is_none() -> None:
    """An exception instance whose `__traceback__` is `None` (e.g. built
    but never raised) must not crash `frame_location` or fall through to
    an indexing error."""
    exc = RuntimeError("never raised")
    assert exc.__traceback__ is None
    assert frame_location(exc) == "<no traceback>"


# --- GAP-8 (Branca /harden re-run #3, 2026-09-21, live-reproduced against
# 1e8e1aa): the <external>/ clamp above closed the disclosure for an
# out-of-tree ABSOLUTE frame, but a non-absolute frame filename (a
# stdlib synthetic frame, e.g. `<string>` from `exec`/`compile`, or
# `<frozen importlib._bootstrap>`) still went through `os.path.relpath`,
# which silently joins a non-absolute argument onto `os.getcwd()` --
# another absolute-path disclosure, and a `FileNotFoundError` (an
# `OSError`) from `getcwd()` itself when the cwd no longer exists,
# raised from INSIDE this exact catch-all-safety helper (the A-5 class).


def test_frame_location_for_a_non_absolute_filename_never_joins_the_cwd() -> None:
    """A synthetic frame (`exec`-compiled code defaults to the filename
    `<string>`) is never inside `idp_regression` and must never be
    resolved relative to the cwd."""
    try:
        exec("raise RuntimeError('boom')", {})  # noqa: S102 - deliberate synthetic frame
    except RuntimeError as exc:
        location = frame_location(exc)

    assert location.startswith("<external>/<string>:")
    assert os.getcwd() not in location
    assert not os.path.isabs(location.split(":")[0])


def test_frame_location_never_raises_when_the_cwd_no_longer_exists(
    tmp_path: object, monkeypatch: object
) -> None:
    """Direct pin for the raise half of GAP-8: `os.path.relpath`/
    `os.path.abspath` calls `os.getcwd()` for any non-absolute argument,
    which raises `FileNotFoundError` once the cwd has been deleted --
    `frame_location` must be total and never propagate that."""
    import os as os_module

    workdir = tmp_path / "gone"  # type: ignore[operator]
    workdir.mkdir()  # type: ignore[attr-defined]
    monkeypatch.chdir(workdir)  # type: ignore[attr-defined]
    os_module.rmdir(workdir)

    try:
        exec("raise RuntimeError('boom')", {})  # noqa: S102 - deliberate synthetic frame
    except RuntimeError as exc:
        location = frame_location(exc)  # must not raise

    assert location.startswith("<external>/<string>:")


def test_frame_location_is_total_against_an_arbitrary_relpath_failure(
    monkeypatch: object,
) -> None:
    """`frame_location` must never raise, no matter what `os.path.relpath`
    itself does -- not just `ValueError`/`OSError`, ANY exception."""
    import os as os_module

    def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("relpath exploded unexpectedly")

    monkeypatch.setattr(os_module.path, "relpath", _boom)  # type: ignore[attr-defined]

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        location = frame_location(exc)  # must not raise

    assert location  # some safe string, not a crash


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
