"""Shared OAuth client-credentials token fetch.

``MuleSoftIDPAdapter._fetch_token`` (``idp_client.py``) already does this,
bound to that class. ``version_probe.py`` (ADR-0006 §A'.1) needs the exact
same validated fetch — same token endpoint, same failure semantics — but
must not import a private method off ``idp_client.MuleSoftIDPAdapter``,
and this task is explicitly scoped away from touching ``idp_client.py``
(a file with 886+ mutation-verified tests, out of this change's blast
radius). This module holds the validation logic standalone so both
callers can use it without a second, divergent implementation growing
next to it. The ~30-line duplication against ``_fetch_token`` is a named,
accepted cost — see ``docs/state/DEBT.md``.
"""

from __future__ import annotations

import math

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPAuthenticationError, IDPTransportError

#: OAuth2 client-credentials token endpoint — identical to
#: ``idp_client.TOKEN_URL`` (ADR-0002 §Context).
TOKEN_URL = "https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token"  # noqa: S105 - a public endpoint URL, not a credential

#: A sane upper bound on a token's advertised lifetime (1 year) — mirrors
#: ``idp_client.MAX_EXPIRES_IN_SECONDS``.
MAX_EXPIRES_IN_SECONDS = 86_400.0 * 365


def fetch_access_token(
    client_id: str, client_secret: str, timeout_seconds: float
) -> tuple[str, float]:
    """Returns ``(access_token, expires_in_seconds)``. Fail-closed: any
    rejection, malformed body, control-char-carrying token, or
    non-finite/out-of-range ``expires_in`` raises ``IDPAuthenticationError``
    — never a raw exception, never the token/secret echoed (INV-02)."""
    # Deferred-raise (mirrors idp_client._fetch_token) — never raise
    # while an IDPTransportError is the "currently handled" exception, or
    # it leaks into __context__ even under `from None`.
    transport_failed = False
    status: int = 0
    body: object = None
    try:
        status, body = transport.post_json(
            TOKEN_URL,
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout_seconds=timeout_seconds,
        )
    except IDPTransportError:
        transport_failed = True
    if transport_failed:
        raise IDPAuthenticationError("OAuth token request failed") from None
    if status != 200 or not isinstance(body, dict):
        raise IDPAuthenticationError("OAuth token request was rejected")
    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise IDPAuthenticationError("OAuth token response is missing access_token")
    if any(not ch.isprintable() for ch in access_token):
        raise IDPAuthenticationError(
            "OAuth token response's access_token contains invalid characters"
        )
    expires_in_raw = body.get("expires_in", 300)
    try:
        expires_in = float(expires_in_raw)
    except (TypeError, ValueError, OverflowError):
        raise IDPAuthenticationError(
            "OAuth token response has a non-numeric expires_in"
        ) from None
    if not math.isfinite(expires_in) or expires_in <= 0 or expires_in > MAX_EXPIRES_IN_SECONDS:
        raise IDPAuthenticationError("OAuth token response has an invalid expires_in")
    return access_token, expires_in
