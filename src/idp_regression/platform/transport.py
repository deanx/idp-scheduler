"""Raw-REST HTTP transport for the Langfuse public API (stdlib only).

ADR-0005 F6 (Python SDK schema support unverified) — dataset schema
provisioning goes over raw REST, verified live in
``docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md``. This module is the
*only* place that builds the Basic ``Authorization`` header; the header
value is never logged, never included in an exception message, and
``redact`` strips it out of any text that might otherwise carry it.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from typing import Any, Protocol

_AUTH_HEADER_PATTERN = re.compile(r"Basic\s+[A-Za-z0-9+/=]+")


def redact(text: str) -> str:
    """Strip a Basic auth header value out of arbitrary text before logging."""
    return _AUTH_HEADER_PATTERN.sub("Basic ***REDACTED***", text)


class HttpClient(Protocol):
    """The seam ``LangfuseAdapter`` depends on — mocked in unit tests."""

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]: ...


class UrllibHttpClient:
    """The real transport: stdlib ``urllib`` + Basic auth (no SDK dependency)."""

    def __init__(self, host: str, public_key: str, secret_key: str) -> None:
        self._host = host.rstrip("/")
        self._auth_header = "Basic " + base64.b64encode(
            f"{public_key}:{secret_key}".encode()
        ).decode("ascii")

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self._host + path, data=data, method=method)
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 - internal Langfuse host only
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            status = exc.code
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw
