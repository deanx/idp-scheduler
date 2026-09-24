"""Instance-held OAuth token cache with a refresh margin (BR7, ADR-0002
§Design patterns "Repository (light)", ADR-0004 #7).

An **instance** attribute, never a module-level global — keeps the cache
scoped to one adapter's lifetime and trivially test-isolated (idiomatic per
ADR-0002). ``fetch`` and ``clock`` are injected so the HTTP call and the
budget math (INV-07, monotonic) are both mockable without a live IDP org.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from idp_regression.adapter.errors import IDPAuthenticationError


class TokenCache:
    """``get()`` returns a valid token, fetching or refreshing as needed.

    ``fetch()`` returns ``(access_token, expires_in_seconds)``. A token is
    refreshed when absent or when ``clock() >= expires_at - refresh_margin_seconds``
    (ADR-0004 #7's ``IDP_TOKEN_REFRESH_MARGIN_SECONDS``).
    """

    def __init__(
        self,
        fetch: Callable[[], tuple[str, float]],
        refresh_margin_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._refresh_margin_seconds = refresh_margin_seconds
        self._clock = clock
        self._token: str | None = None
        self._expires_at: float | None = None

    def get(self) -> str:
        now = self._clock()
        needs_refresh = (
            self._token is None
            or self._expires_at is None
            or now >= self._expires_at - self._refresh_margin_seconds
        )
        if needs_refresh:
            self._refresh()
        assert self._token is not None  # noqa: S101 - _refresh always sets it or raises
        return self._token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None

    def _refresh(self) -> None:
        try:
            token, expires_in = self._fetch()
        except IDPAuthenticationError:
            # Fail-closed, no retry (A3) — propagate the typed error as-is.
            raise
        except Exception as exc:
            raise IDPAuthenticationError("OAuth token request failed") from exc
        self._token = token
        self._expires_at = self._clock() + expires_in
