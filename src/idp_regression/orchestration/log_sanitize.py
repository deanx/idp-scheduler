"""Log-safety helpers for IDP-controlled values reaching the orchestrator
(T-01.4.14, DEBT-23, NFR N5).

`IDPExecutionFailedError.status` is a string the IDP itself controls (a
poll-response terminal status). The adapter already caps/strips
non-printable characters from it at construction
(`idp_regression.adapter.errors._sanitize_status`), but a quote character
is printable and survives that pass — this module adds the SAME
`sanitize_for_log` quoting already used at every other untrusted-value log
boundary in this codebase (`idp_regression.adapter.transport.sanitize_for_log`,
`idp_regression.platform.transport.sanitize_for_log`) before the status
ever reaches a structured `key=value` log line or a telemetry field.
"""

from __future__ import annotations

import os
import traceback

from idp_regression.adapter.errors import IDPExecutionFailedError
from idp_regression.adapter.transport import sanitize_for_log


def format_execution_failed_status_for_log(error: IDPExecutionFailedError) -> str:
    """Render `error.status` safe to interpolate into a single structured
    log line or telemetry field (DEBT-23, N5)."""
    return sanitize_for_log(error.status)


#: The directory one level above the `idp_regression` package (i.e. `src/`,
#: or this repo's checkout root when installed editable) -- used by
#: `frame_location` below to strip the absolute deployment path off a
#: traceback frame's filename.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def frame_location(exc: BaseException) -> str:
    """Shared by `facade.py` and `cli.py` (previously duplicated as a
    private `_frame_location` in each): the last traceback frame's
    `filename:lineno:name` carries no golden value, no extracted value,
    no credential and no platform response body -- it is pure
    code-location metadata, safe under INV-02, unlike `str(exc)`.

    ⚠️ Fixed 2026-09-21 (Atchim gate suggestion): `frame.filename` alone is
    an ABSOLUTE path (e.g. `/Users/alex/.../facade.py`), which discloses
    this deployment's directory layout on every unexpected-exception log
    line. Rendered package-relative (relative to the directory containing
    `idp_regression`) instead, so the value logged is e.g.
    `idp_regression/orchestration/facade.py:426:run_eval` -- never an
    absolute machine path.

    ⚠️ Fixed again 2026-09-21 (same-day re-gate, live-reproduced):
    `os.path.relpath` does not clamp to `_PACKAGE_PARENT` -- it happily
    TRAVERSES above it. Whenever the innermost frame lives OUTSIDE
    `idp_regression` (the normal case for the unanticipated-exception
    catch-alls this feeds: the evaluation-platform SDK, httpx, the
    stdlib), the result is a string like
    `../../../../../../Users/alex/.../json/decoder.py:361:raw_decode` --
    the exact absolute-path disclosure the first fix above was written to
    close, just re-opened one level up. Any frame whose relative path
    would start with `..`, or whose drive/root differs entirely from
    `_PACKAGE_PARENT` (`relpath` raises `ValueError` on Windows for
    that), is rendered as `<external>/{basename}` instead -- code-location
    metadata for a third-party frame is not worth an absolute-path leak.
    A package-internal frame keeps the existing package-relative form.

    ⚠️ Fixed a third time 2026-09-21 (Branca `/harden` re-run #3, GAP-8,
    live-reproduced against `1e8e1aa`): the `<external>/` clamp above
    only covered an out-of-tree ABSOLUTE frame filename. A synthetic
    stdlib frame (`<string>` from `exec`/`compile`, `<frozen
    importlib._bootstrap>`, ...) is NOT absolute -- `os.path.relpath`
    silently joins a non-absolute argument onto `os.getcwd()` first,
    which is (a) yet another absolute-path disclosure route and (b)
    raises `FileNotFoundError` (an `OSError`) from `getcwd()` itself if
    the current working directory has since been deleted -- an
    exception escaping from INSIDE this exact catch-all-safety helper.
    A non-absolute filename is now recognised and routed to
    `<external>/{basename}` directly, without ever calling `relpath`.
    `OSError` is now also caught alongside `ValueError` around the
    remaining (absolute-frame) `relpath` call, and the whole filename
    computation is wrapped in a final `except Exception` so this
    function is TOTAL -- no traceback, no filename shape and no
    unanticipated `relpath` failure may ever raise out of it; the
    fallback is `<unavailable>`, never a crash inside a catch-all whose
    entire job is to make an unanticipated failure safe to log."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "<no traceback>"
    frame = frames[-1]
    try:
        if not os.path.isabs(frame.filename):
            filename = f"<external>/{os.path.basename(frame.filename)}"
        else:
            try:
                filename = os.path.relpath(frame.filename, _PACKAGE_PARENT)
            except (ValueError, OSError):
                filename = f"<external>/{os.path.basename(frame.filename)}"
            else:
                if filename.split(os.sep, 1)[0] == os.pardir:
                    filename = f"<external>/{os.path.basename(frame.filename)}"
        return f"{filename}:{frame.lineno}:{frame.name}"
    except Exception:  # noqa: BLE001 - this IS the catch-all's own safety net; must never raise
        return "<unavailable>"
