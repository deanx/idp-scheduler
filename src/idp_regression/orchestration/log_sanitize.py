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
    absolute machine path."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "<no traceback>"
    frame = frames[-1]
    filename = os.path.relpath(frame.filename, _PACKAGE_PARENT)
    return f"{filename}:{frame.lineno}:{frame.name}"
