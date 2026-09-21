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

from idp_regression.adapter.errors import IDPExecutionFailedError
from idp_regression.adapter.transport import sanitize_for_log


def format_execution_failed_status_for_log(error: IDPExecutionFailedError) -> str:
    """Render `error.status` safe to interpolate into a single structured
    log line or telemetry field (DEBT-23, N5)."""
    return sanitize_for_log(error.status)
