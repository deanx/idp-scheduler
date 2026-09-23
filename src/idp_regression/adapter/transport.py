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

import http.client
import json
import logging
import mimetypes
import re
import urllib.error
import urllib.request
import uuid
from typing import Any, NoReturn

from idp_regression.adapter.errors import IDPTransportError

logger = logging.getLogger(__name__)

_BEARER_PATTERN = re.compile(r"Bearer\s+\S+")
# JSON string values: `(?:[^"\\]|\\.)*` consumes an escaped quote (`\"`)
# instead of stopping at it — a naive `[^"]*` leaves the remainder of the
# secret (after the escaped quote) unredacted (/test Scenario B item 4).
_CLIENT_SECRET_JSON_PATTERN = re.compile(r'"client_secret"\s*:\s*"(?:[^"\\]|\\.)*"')
_ACCESS_TOKEN_JSON_PATTERN = re.compile(r'"access_token"\s*:\s*"(?:[^"\\]|\\.)*"')
# Form-encoded (application/x-www-form-urlencoded) bodies: value runs until
# the next `&`, whitespace, or end of string.
_CLIENT_SECRET_FORM_PATTERN = re.compile(r"client_secret=[^&\s]*")
_ACCESS_TOKEN_FORM_PATTERN = re.compile(r"access_token=[^&\s]*")

#: Bounded default — never block indefinitely on a hung connection (ADR-0004 #1).
DEFAULT_TIMEOUT_SECONDS = 30.0

#: A response beyond this many bytes is rejected rather than buffered whole
#: (Atchim suggestion — resp.read() was otherwise unbounded).
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

#: Transport-level failures that must never escape as a raw stdlib
#: exception (Atchim R3): connection drops, partial reads, generic
#: ``http.client``/``OSError`` failures. ``TimeoutError``/``URLError`` are
#: handled alongside these; ``ConnectionResetError`` is an ``OSError``
#: subclass and ``http.client.RemoteDisconnected``/``IncompleteRead`` are
#: ``HTTPException`` subclasses, both already covered by the tuple below —
#: listed explicitly anyway for readability.
_TRANSPORT_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionResetError,
    OSError,
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect (QA F-2) — the ``Authorization`` header (a
    Bearer token) must never be re-sent to a different host, even for a
    same-origin redirect (defense-in-depth) or an https->http downgrade.
    Returning ``None`` makes urllib raise ``HTTPError`` for the 3xx status
    instead of transparently following it, so a redirect becomes an
    ordinary non-2xx response handled (and redacted) like any other."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


#: A module-level opener with redirects disabled — built once, reused for
#: every request. Exposed as a call-through function (rather than used
#: directly as ``_opener.open``) so tests can monkeypatch a single seam.
_opener = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen(req: urllib.request.Request, timeout: float) -> Any:
    return _opener.open(req, timeout=timeout)


def redact(text: str) -> str:
    """Strip a Bearer token / ``client_secret`` / ``access_token`` value
    out of arbitrary text before it reaches a log line or an exception
    message — covers JSON bodies (incl. an escaped quote inside the
    value), form-encoded bodies, and Bearer header values (/test Scenario
    B item 4). When a whole body/response is available and its shape is
    untrusted, prefer not including it at all (see
    ``_log_and_raise_transport_error_without_detail``) rather than relying
    solely on these patterns."""
    text = _BEARER_PATTERN.sub("Bearer ***REDACTED***", text)
    text = _CLIENT_SECRET_JSON_PATTERN.sub('"client_secret":"***REDACTED***"', text)
    text = _ACCESS_TOKEN_JSON_PATTERN.sub('"access_token":"***REDACTED***"', text)
    text = _CLIENT_SECRET_FORM_PATTERN.sub("client_secret=***REDACTED***", text)
    return _ACCESS_TOKEN_FORM_PATTERN.sub("access_token=***REDACTED***", text)


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
    status, resp_body, _response_headers = _send(req, timeout_seconds)
    return status, resp_body


def get_json(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    status, body, _response_headers = _send(req, timeout_seconds)
    return status, body


def get_json_with_headers(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """Like ``get_json`` but also returns the response headers (lower-cased
    keys), needed only by the poll loop's bounded-retry logic (ADR-0004 #3)
    to read ``Retry-After`` on a 429. Kept as a separate function rather
    than widening ``get_json``'s return shape so every existing ``get_json``
    caller/test (submit-leg-adjacent, T-01.2) stays untouched."""
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req, timeout_seconds)


def _escape_multipart_filename(filename: str) -> str:
    """RFC 7578-style escaping for a multipart ``filename`` parameter:
    backslash and quote are backslash-escaped, and CR/LF are stripped so a
    malicious local filename can't inject an extra MIME header."""
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\r", "").replace("\n", "")


def post_multipart_file(
    url: str,
    field_name: str,
    file_path: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    try:
        with open(file_path, "rb") as fh:
            file_bytes = fh.read()
    except (OSError, ValueError):
        # ValueError: a NUL byte embedded in the path raises this, not
        # OSError (/test Scenario B item 2). Never echo the local path —
        # it can reveal filesystem layout (R3).
        raise IDPTransportError("failed to read the local document file") from None

    boundary = uuid.uuid4().hex
    filename = _escape_multipart_filename(file_path.rsplit("/", 1)[-1])
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
    status, resp_body, _response_headers = _send(req, timeout_seconds)
    return status, resp_body


def post_empty_multipart(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """A POST with a syntactically valid but semantically EMPTY multipart
    body (zero parts — just the closing boundary line, no ``file`` part).

    Used by the version-existence probe (ADR-0006 §A'.1/Addendum 3): the
    IDP host still routes the request (there IS a version segment in the
    URL path), so a 400 ``Invalid query parameter 'file'`` means the
    version exists and a 404 means it does not — no document bytes are
    ever transmitted, so this spends zero extraction quota. Routed
    through the shared ``_send`` (D14) — the same no-redirect opener
    ``post_multipart_file``/``get_json`` use, never a second transport."""
    boundary = uuid.uuid4().hex
    body = f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    status, resp_body, _response_headers = _send(req, timeout_seconds)
    return status, resp_body


def _log_and_raise_transport_error(req: urllib.request.Request, detail: str) -> NoReturn:
    message = redact(f"{req.get_method()} {req.full_url} failed: {detail}")
    logger.error(
        "idp_transport_failed method=%s detail=%s",
        sanitize_for_log(req.get_method()),
        sanitize_for_log(message),
    )
    raise IDPTransportError(message)


def _log_and_raise_transport_error_without_detail(
    req: urllib.request.Request, reason: str
) -> NoReturn:
    """Like ``_log_and_raise_transport_error`` but never includes the raw
    exception text at all — for failures (e.g. a CR/LF-carrying header
    value) where the underlying stdlib message can embed a secret in a
    form (a bytes repr) that ``redact()``'s regexes aren't guaranteed to
    catch (/test Scenario B item 1). A static, secret-free reason is
    safer than a clever-but-fallible redaction."""
    message = f"{req.get_method()} {req.full_url} failed: {reason}"
    logger.error(
        "idp_transport_failed method=%s detail=%s",
        sanitize_for_log(req.get_method()),
        sanitize_for_log(message),
    )
    raise IDPTransportError(message)


def _read_bounded(readable: Any) -> bytes:
    data = readable.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise IDPTransportError(f"response body exceeded the {MAX_RESPONSE_BYTES}-byte cap")
    return bytes(data)


def _response_headers(source: Any) -> dict[str, str]:
    """Lower-cased response headers, defensively -- a fake/stub response
    used by a unit test may not define ``.headers`` at all, and that must
    never raise (only the real poll-retry path reads this)."""
    raw_headers = getattr(source, "headers", None)
    if raw_headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in raw_headers.items()}


def _send(req: urllib.request.Request, timeout_seconds: float) -> tuple[int, Any, dict[str, str]]:
    # Deferred-raise: a header value containing CR/LF (e.g. a corrupted
    # token) makes http.client raise a raw ValueError whose message embeds
    # the value (as a bytes repr — Atchim R8's redact()-regex approach
    # isn't trusted to catch that form). Raising *inside* the `except`
    # would leak that ValueError into __context__ even under `from None`,
    # so the raise is deferred to after the try/except exits (same pattern
    # as idp_client.py's _fetch_token/_submit).
    invalid_header_value = False
    unexpected_redirect = False
    response_headers: dict[str, str] = {}
    try:
        with _urlopen(req, timeout_seconds) as resp:  # noqa: S310 - internal MuleSoft IDP host only
            raw_bytes = _read_bounded(resp)
            status = resp.status
            response_headers = _response_headers(resp)
    except ValueError:
        invalid_header_value = True
        raw_bytes = b""
        status = 0
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            # A 3xx should never happen for IDP's API; the no-redirect
            # opener (QA F-2) converts an actual redirect attempt into
            # this HTTPError instead of transparently following it and
            # re-sending the Authorization header to a different host. A
            # redirect is a transport-level anomaly here, not an
            # application response — treat it as a typed transport error
            # immediately, and never read/return its body. Close the
            # unread response explicitly (releases the socket/file) —
            # don't rely on addinfourl's __del__ finalizer, whose timing
            # is a CPython refcounting implementation detail, not a
            # language guarantee (Atchim suggestion).
            exc.close()
            unexpected_redirect = True
            raw_bytes = b""
            status = exc.code
        else:
            try:
                raw_bytes = _read_bounded(exc)
            except _TRANSPORT_FAILURE_TYPES as read_exc:
                _log_and_raise_transport_error(req, str(read_exc))
            status = exc.code
            response_headers = _response_headers(exc)
    except _TRANSPORT_FAILURE_TYPES as exc:
        _log_and_raise_transport_error(req, str(exc))
    if invalid_header_value:
        _log_and_raise_transport_error_without_detail(req, "request headers were rejected")
    if unexpected_redirect:
        _log_and_raise_transport_error_without_detail(req, "unexpected redirect response")

    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _log_and_raise_transport_error(req, "response body was not valid UTF-8")
    if not raw:
        return status, None, response_headers
    try:
        return status, json.loads(raw), response_headers
    except json.JSONDecodeError:
        return status, raw, response_headers
    except (RecursionError, ValueError):
        _log_and_raise_transport_error(req, "response body could not be parsed as JSON")
