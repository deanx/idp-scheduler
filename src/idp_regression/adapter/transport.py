"""Raw HTTP transport for MuleSoft Anypoint IDP (stdlib ``urllib`` only —
mirrors ``idp_regression.platform.transport``'s redaction/sanitization
pattern; not shared across packages so ``adapter``/``platform`` stay
independently swappable, N24-style).

All adapter HTTP calls are confined to this module and ``idp_client.py``
(both under ``src/idp_regression/adapter/``) — asserted by
``tests/adapter/test_module_boundary.py``.

A timeout or connection error (``TimeoutError``/``URLError``) never escapes
as a raw stdlib exception — it is mapped to the typed ``IDPTransportError``,
message redacted before it reaches the exception or a log line (NFR N5,
NFR N23, ADR-0002 §Threat model).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import urllib.error
import urllib.request
import uuid
from typing import Any

from idp_regression.adapter.errors import IDPTransportError

logger = logging.getLogger(__name__)

_BEARER_PATTERN = re.compile(r"Bearer\s+\S+")
_CLIENT_SECRET_PATTERN = re.compile(r'"client_secret"\s*:\s*"[^"]*"')

#: Bounded default — never block indefinitely on a hung connection (ADR-0004 #1).
DEFAULT_TIMEOUT_SECONDS = 30.0


def redact(text: str) -> str:
    """Strip a Bearer token / ``client_secret`` value out of arbitrary text
    before it reaches a log line or an exception message."""
    text = _BEARER_PATTERN.sub("Bearer ***REDACTED***", text)
    return _CLIENT_SECRET_PATTERN.sub('"client_secret":"***REDACTED***"', text)


def sanitize_for_log(value: object) -> str:
    """Render a caller-controlled value (document path, action id, version,
    status, ...) safe to interpolate into a single log line. ``json.dumps``
    escapes control characters AND embedded quotes (NFR N5) — see
    ``idp_regression.platform.transport.sanitize_for_log`` for the same
    pattern and its rationale."""
    return json.dumps(str(value))


def post_json(
    url: str,
    body: dict[str, Any],
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req, timeout_seconds)


def get_json(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req, timeout_seconds)


def post_multipart_file(
    url: str,
    field_name: str,
    file_path: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    boundary = uuid.uuid4().hex
    with open(file_path, "rb") as fh:
        file_bytes = fh.read()
    filename = file_path.rsplit("/", 1)[-1]
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
    ).encode()
    body += f"Content-Type: {content_type}\r\n\r\n".encode()
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req, timeout_seconds)


def _send(req: urllib.request.Request, timeout_seconds: float) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(  # noqa: S310 - internal MuleSoft IDP host only
            req, timeout=timeout_seconds
        ) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    except (TimeoutError, urllib.error.URLError) as exc:
        message = redact(f"{req.get_method()} {req.full_url} failed: {exc}")
        logger.error(
            "idp_transport_failed method=%s detail=%s",
            sanitize_for_log(req.get_method()),
            sanitize_for_log(message),
        )
        raise IDPTransportError(message) from exc
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw
