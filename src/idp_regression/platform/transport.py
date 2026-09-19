"""Raw-REST HTTP transport for the Langfuse public API (stdlib only).

ADR-0005 F6 (Python SDK schema support unverified) — dataset schema
provisioning goes over raw REST, verified live in
``docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md``. This module is the
*only* place that builds the Basic ``Authorization`` header; the header
value is never logged, never included in an exception message, and
``redact`` strips it out of any text that might otherwise carry it.

R7: every request has a configurable, bounded socket timeout. A timeout
or connection error (``URLError``/``TimeoutError``) never escapes as a
raw stdlib exception — it is mapped to the typed ``TransportError``, with
the message redacted before it reaches the exception or a log line.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Protocol

from idp_regression.platform.errors import TransportError

logger = logging.getLogger(__name__)

_AUTH_HEADER_PATTERN = re.compile(r"Basic\s+[A-Za-z0-9+/=]+")

#: Bounded default — never block indefinitely on a hung connection.
DEFAULT_TIMEOUT_SECONDS = 30.0


def redact(text: str) -> str:
    """Strip a Basic auth header value out of arbitrary text before logging."""
    return _AUTH_HEADER_PATTERN.sub("Basic ***REDACTED***", text)


def sanitize_for_log(value: object) -> str:
    """Render a caller-controlled value (dataset name, document_id, score
    name, run_id, ...) safe to interpolate into a single log line.

    ``json.dumps`` both escapes control characters (``\\n``, ``\\r``,
    tabs, ...) AND escapes embedded ``"``/``\\`` (unlike ``repr()``,
    which only escapes the delimiter it happens to pick and leaves the
    other quote character bare) — Atchim: ``repr('x" status="200')`` ==
    ``'x" status="200'``, so a bare ``"`` could still forge a field past
    a logfmt/key=value parser. Double-quoting + full escaping closes
    that gap (NFR N5, /test Scenario B). Shared by every module in
    ``platform/`` that logs a caller-controlled string.
    """
    return json.dumps(str(value))


class HttpClient(Protocol):
    """The seam ``LangfuseAdapter`` depends on — mocked in unit tests."""

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]: ...


class UrllibHttpClient:
    """The real transport: stdlib ``urllib`` + Basic auth (no SDK dependency)."""

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host.rstrip("/")
        self._auth_header = "Basic " + base64.b64encode(
            f"{public_key}:{secret_key}".encode()
        ).decode("ascii")
        self._timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self._host + path, data=data, method=method)
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(  # noqa: S310 - internal Langfuse host only
                req, timeout=self._timeout_seconds
            ) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            status = exc.code
        except (TimeoutError, urllib.error.URLError) as exc:
            message = redact(f"{method} {path} failed: {exc}")
            logger.error(
                "transport_failed method=%s path=%s detail=%s",
                sanitize_for_log(method),
                sanitize_for_log(path),
                sanitize_for_log(message),
            )
            raise TransportError(message) from exc
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw
