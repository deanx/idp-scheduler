"""Typed IDP-adapter errors — mappable to the orchestrator's abort-reason
taxonomy (ADR-0004). Never include a raw ``Authorization`` header, OAuth
token, or ``client_secret`` in a message (NFR N23, INV-02) — messages are
redacted at the transport boundary (``idp_regression.adapter.transport.redact``)
before they reach an exception.
"""

from __future__ import annotations

#: A poll-response status string is IDP-controlled — cap it and strip
#: control characters at construction so a long or control-char-laden
#: status can never inject into a downstream log line (/test Scenario B,
#: item 6; DEBT-23 becomes partly moot since the value is sanitized here,
#: not just at the log call site — truncation still loses information for
#: a legitimately long/odd status, so the debt isn't fully closed).
_MAX_STATUS_LENGTH = 64


def _sanitize_status(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isprintable() or ch == " ")
    return cleaned[:_MAX_STATUS_LENGTH]


class IDPAdapterError(Exception):
    """Base for IDP transport/auth/execution errors (not shape errors)."""


class IDPConfigurationError(IDPAdapterError):
    """A numeric timing config value (a timeout, interval, or margin) is
    non-finite, out of range, or non-numeric — QA F-1: a NaN/inf timeout
    must never silently produce an infinite poll or let a raw
    ``OverflowError`` escape."""


class IDPTransportError(IDPAdapterError):
    """The raw HTTP call failed before a status code was returned (timeout,
    connection error) — R7-style: never a raw ``urllib`` exception escapes."""


class IDPAuthenticationError(IDPAdapterError):
    """OAuth token fetch/refresh failed, or IDP rejected a request as
    unauthenticated mid-run — fail-closed, no retry (A3, ADR-0004 #7)."""


class IDPSubmitError(IDPAdapterError):
    """The submit call failed, timed out, or was rejected — never retried
    (the submit POST is not idempotent for a given document; ADR-0004 #1/#4)."""


class IDPPollTimeoutError(IDPAdapterError):
    """The poll-wall-clock budget expired before a terminal status arrived
    (ADR-0004 #2/#17)."""

    def __init__(self, message: str, *, last_status: str | None) -> None:
        super().__init__(message)
        self.last_status = _sanitize_status(last_status) if last_status is not None else None


class IDPExecutionFailedError(IDPAdapterError):
    """The execution reached a terminal status outside ``IDP_SUCCESS_STATUSES``
    (ADR-0004 #5 — a hard IDP failure)."""

    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = _sanitize_status(status)


class IDPAmbiguousStatusError(IDPAdapterError):
    """A poll response's ``status`` is missing, null, or not a non-empty
    string — ADR-0004 #17 requires an immediate abort, never inferring
    success by continuing to poll on an ambiguous status."""


class IDPPollHardFailureError(IDPAdapterError):
    """A poll request returned a non-2xx HTTP status other than 401/403 —
    ADR-0004 #5, a hard failure, aborted immediately. The response body's
    ``status`` field is never read on this path."""

    def __init__(self, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.http_status = http_status


class MalformedIDPOutputError(IDPAdapterError):
    """``normalize()`` rejected a malformed/untrusted raw IDP body (NFR N21).

    ``reason`` is a stable machine-readable tag (e.g. ``unsafe_field_name``,
    ``duplicate_prompt``, ``value_too_large``, ``table_too_large``) — never
    the offending value itself, so the message never echoes golden/actual
    content (INV-02).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
