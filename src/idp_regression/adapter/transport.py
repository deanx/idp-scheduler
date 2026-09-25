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
import os
import re
import urllib.error
import urllib.request
import uuid
from typing import Any, NoReturn

from idp_regression.adapter.errors import IDPTransportError

logger = logging.getLogger(__name__)

#: DEBT-25(a): case-insensitive -- "Bearer" is the canonical RFC 6750
#: casing, but a lowercase "bearer" (seen from some clients/proxies) must
#: be redacted identically; the scheme name's case carries no security
#: meaning, so an exact-case-only pattern is a redaction gap, not a
#: deliberate narrowing.
_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
# JSON string values: `(?:[^"\\]|\\.)*` consumes an escaped quote (`\"`)
# instead of stopping at it — a naive `[^"]*` leaves the remainder of the
# secret (after the escaped quote) unredacted (/test Scenario B item 4).
_CLIENT_SECRET_JSON_PATTERN = re.compile(r'"client_secret"\s*:\s*"(?:[^"\\]|\\.)*"')
_ACCESS_TOKEN_JSON_PATTERN = re.compile(r'"access_token"\s*:\s*"(?:[^"\\]|\\.)*"')
# Form-encoded (application/x-www-form-urlencoded) bodies: value runs until
# the next `&`, whitespace, or end of string. DEBT-25(a): also match the
# percent-encoded `=` (`%3D`) form -- an already-URL-encoded query string
# (e.g. echoed back inside a rejected-request error message) uses
# `client_secret%3D...`, not the literal `client_secret=...`, and the
# original patterns only covered the literal form.
_CLIENT_SECRET_FORM_PATTERN = re.compile(r"client_secret(?:=|%3D)[^&\s]*", re.IGNORECASE)
_ACCESS_TOKEN_FORM_PATTERN = re.compile(r"access_token(?:=|%3D)[^&\s]*", re.IGNORECASE)

#: DEBT-54 A-2: the largest local document this client will read into memory.
#: 100 MiB — comfortably above any real invoice or scanned corpus page (the
#: test pack's heaviest is a few hundred KiB) and far below the point where
#: the doubled copy in the multipart body threatens the process. A document
#: over this is refused as a typed `IDPTransportError`, never a `MemoryError`.
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024

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
    req = urllib.request.Request(  # noqa: S310 - configured host, no-redirect (ADR-0002)
        url, data=data, method="POST"
    )
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
    req = urllib.request.Request(url, method="GET")  # noqa: S310 - IDP host, no-redirect
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
    req = urllib.request.Request(url, method="GET")  # noqa: S310 - IDP host, no-redirect
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
        # DEBT-52 -- close the TOCTOU window at the point of USE.
        # `_resolve_document_path` proves the path is inside
        # `IDP_DOCUMENT_DIR` with `os.path.realpath`, but that proof is
        # about a PATH and this open happens later; anything able to
        # write to the document directory can swap the file for a
        # symlink in between, and the read would follow it out of the
        # containment just verified. `O_NOFOLLOW` makes a symlink in the
        # final position fail the open instead of being resolved, so the
        # check and the use are bound to the same file descriptor.
        #
        # Residual, stated rather than implied: `O_NOFOLLOW` guards only
        # the FINAL component. An attacker who can swap an intermediate
        # DIRECTORY for a symlink between realpath and open is still not
        # covered, so DEBT-52 is NARROWED here, not closed.
        #
        # ⚠️ Corrected 2026-09-25 (Zangado QA, F-5): an earlier version of
        # this comment said closing that residual "needs `openat`
        # directory-fd walking, which is Linux-specific and not portable
        # to the macOS target". **That was wrong**, and a wrong reason in
        # a security comment is worse than no reason — it retires a fix
        # that is actually available. DEBT-52's option B is portable:
        # `os.stat` the candidate at check time, `os.fstat` the descriptor
        # after opening, and compare `(st_dev, st_ino)`. Inode identity
        # does not care how the path was walked, so it catches an
        # intermediate-directory swap that `O_NOFOLLOW` cannot.
        #
        # Why option B is not shipped here: it needs the identity
        # established by `_resolve_document_path` (orchestration) to reach
        # this read (adapter), and the only seam between them is
        # `IDPAdapter.extract(document_path, action_id, version)` — a
        # pinned Protocol. Threading an expected `(st_dev, st_ino)` through
        # it is an architectural change to a contract other code depends
        # on, not a local hardening, so it is recorded on DEBT-52 as the
        # remaining work rather than smuggled in beside an unrelated fix.
        fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            # DEBT-54 A-2 -- bound the read.
            # `fh.read()` was unbounded, and the bytes are then COPIED
            # into the multipart body below. Peak memory is ~2x the file
            # size, and only because the `del` below drops the first copy
            # before `bytes(body)` makes the third -- without it, review
            # (Atchim, 2026-09-25) measured THREE live copies at the
            # `Request(...)` call: `file_bytes`, `body`, and `bytes(body)`.
            # A large or hostile document raised `MemoryError`
            # inside the escaping class GAP-1 hardened — the one
            # exception shape that does not behave like the typed errors
            # every caller here is written against.
            #
            # `os.fstat` on the OPEN descriptor, never `os.stat` on the
            # path: stat-then-open is the very TOCTOU pair the line above
            # closes, and measuring a different file than the one being
            # read would be a guard in name only.
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_DOCUMENT_BYTES:
                raise IDPTransportError(
                    "the local document file exceeds the maximum size "
                    f"({MAX_DOCUMENT_BYTES} bytes)"
                )
            file_bytes = fh.read()
    except (OSError, ValueError):
        # ValueError: a NUL byte embedded in the path raises this, not
        # OSError (/test Scenario B item 2). Never echo the local path —
        # it can reveal filesystem layout (R3). `OSError` now also covers
        # `ELOOP`/`EMLINK` from the `O_NOFOLLOW` refusal above.
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
    # Drop the first copy as soon as it is in the body: `bytes(body)` at the
    # `Request(...)` below copies again, and holding all three at once is
    # what made peak memory 3x the file size rather than the 2x the cap
    # above is chosen against.
    del file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(  # noqa: S310 - configured host, no-redirect (ADR-0002)
        url, data=bytes(body), method="POST"
    )
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
    req = urllib.request.Request(  # noqa: S310 - configured host, no-redirect (ADR-0002)
        url, data=body, method="POST"
    )
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
    other_value_error: str | None = None
    response_headers: dict[str, str] = {}
    try:
        with _urlopen(req, timeout_seconds) as resp:
            raw_bytes = _read_bounded(resp)
            status = resp.status
            response_headers = _response_headers(resp)
    except ValueError as exc:
        # DEBT-25(b): a bare ValueError from `_urlopen` is not always a
        # rejected header -- `http.client.HTTPConnection.putheader` raises
        # "Invalid header name/value %r" for that case, but urllib also
        # raises an unrelated bare ValueError ("unknown url type: ...")
        # for a malformed URL. Only the header-rejection message is
        # reported as "headers were rejected"; anything else is an
        # ordinary transport failure with its (redacted) message included,
        # same as any other transport-level error.
        if str(exc).startswith("Invalid header"):
            invalid_header_value = True
        else:
            other_value_error = str(exc)
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
    if other_value_error is not None:
        _log_and_raise_transport_error(req, other_value_error)
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
