"""``MuleSoftIDPAdapter`` — the ``IDPAdapter`` Protocol implementation over
MuleSoft Anypoint IDP (ADR-0002 Option B / §Design patterns "Adapter").

``extract()`` is the only public method: authenticate (cached token) ->
submit (not retried, own timeout) -> poll (configurable terminal-status
allowlist, monotonic-clock budget, INV-07) -> ``normalize()``.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from collections.abc import Callable

from idp_regression.adapter import transport
from idp_regression.adapter.errors import (
    IDPAmbiguousStatusError,
    IDPAuthenticationError,
    IDPConfigurationError,
    IDPExecutionFailedError,
    IDPPollHardFailureError,
    IDPPollTimeoutError,
    IDPSubmitError,
    IDPTransportError,
)
from idp_regression.adapter.normalize import normalize
from idp_regression.adapter.token_cache import TokenCache
from idp_regression.adapter.types import NormalizedOutput

logger = logging.getLogger(__name__)

#: OAuth2 client-credentials token endpoint (ADR-0002 §Context).
TOKEN_URL = "https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token"

DEFAULT_SUBMIT_TIMEOUT_SECONDS = 30.0
DEFAULT_POLL_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS = 60.0

#: DEBT-24 / ADR-0004 #3 -- bounded poll retry for a transient 5xx/429 HTTP
#: response (distinct from a connection-level ``IDPTransportError``, which
#: already keeps polling within the deadline unchanged by this task). Pinned
#: literal values per the ADR ("Backoff spec: exponential full jitter, base
#: 1s, cap 8s, max-attempts from config (default 3)") -- only max-attempts
#: is config, base/cap are not placeholders awaiting the S-01.6 spike.
DEFAULT_POLL_RETRY_MAX_ATTEMPTS = 3
MAX_POLL_RETRY_MAX_ATTEMPTS = 20
_POLL_RETRY_BACKOFF_BASE_SECONDS = 1.0
_POLL_RETRY_BACKOFF_CAP_SECONDS = 8.0

#: A sane upper bound on an IDP-controlled ``Retry-After`` value (QA F-1
#: style) -- a huge or negative value is never "sane", so it falls back to
#: the exponential backoff instead of being honoured verbatim.
MAX_RETRY_AFTER_SECONDS = 3_600.0

#: A sane upper bound on a token's advertised lifetime (1 year) — anything
#: beyond this is treated as malformed, not "very long-lived" (/test
#: Scenario B item 3).
MAX_EXPIRES_IN_SECONDS = 86_400.0 * 365

#: Sane upper bounds for the adapter's own timing config (QA F-1) — a
#: NaN/inf submit or poll timeout must never reach a real timeout call
#: (`now >= deadline` is always False for a NaN deadline, so the poll
#: loop never terminates) or an `OverflowError` from a socket-timeout
#: call with `inf`. An hour is generous for any of these.
MAX_SUBMIT_TIMEOUT_SECONDS = 3_600.0
MAX_POLL_TIMEOUT_SECONDS = 3_600.0
MAX_POLL_INTERVAL_SECONDS = 3_600.0
MAX_TOKEN_REFRESH_MARGIN_SECONDS = 3_600.0


def _validate_timing(name: str, value: object, *, allow_zero: bool, max_value: float) -> float:
    """Fail closed on a non-numeric/non-finite/out-of-range timing config
    value (QA F-1) — raised as a typed ``IDPConfigurationError`` at
    construction, not discovered mid-poll."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IDPConfigurationError(f"{name} must be a number")
    try:
        fvalue = float(value)
    except (OverflowError, TypeError, ValueError):
        # An int too large to represent as a float (e.g. 10**400) raises a
        # raw OverflowError from float() — never let it escape, and never
        # echo the value itself (coverage-audit defect).
        raise IDPConfigurationError(f"{name} must be a representable number") from None
    if not math.isfinite(fvalue):
        raise IDPConfigurationError(f"{name} must be finite (not NaN/inf)")
    if allow_zero:
        if fvalue < 0:
            raise IDPConfigurationError(f"{name} must be >= 0")
    elif fvalue <= 0:
        raise IDPConfigurationError(f"{name} must be > 0")
    if fvalue > max_value:
        raise IDPConfigurationError(f"{name} exceeds the sane upper bound of {max_value}")
    return fvalue


def _validate_positive_int(name: str, value: object, *, max_value: int) -> int:
    """Fail closed on a non-int/non-positive/absurd retry-attempts config
    value, mirroring ``_validate_timing``'s posture for the numeric timing
    fields (QA F-1 precedent)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise IDPConfigurationError(f"{name} must be an integer")
    if value <= 0:
        raise IDPConfigurationError(f"{name} must be > 0")
    if value > max_value:
        raise IDPConfigurationError(f"{name} exceeds the sane upper bound of {max_value}")
    return value


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Only the delay-seconds form of ``Retry-After`` is honoured (ADR-0004
    #3: "honors Retry-After capped at the remaining poll budget"). The
    HTTP-date form, and any non-numeric/negative/absurd value, is treated
    as absent -- an IDP-controlled header must never be able to stall or
    crash the poll loop; it just falls back to exponential backoff."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds


def _executions_base_url(region: str, org_id: str, action_id: str, version: str) -> str:
    return (
        f"https://idp-rt.{region}.anypoint.mulesoft.com/api/v1"
        f"/organizations/{org_id}/actions/{action_id}/versions/{version}/executions"
    )


class MuleSoftIDPAdapter:
    """``IDPAdapter`` Protocol implementation (ADR-0002)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        region: str,
        org_id: str,
        terminal_statuses: set[str],
        success_statuses: set[str],
        submit_timeout_seconds: float = DEFAULT_SUBMIT_TIMEOUT_SECONDS,
        poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        token_refresh_margin_seconds: float = DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS,
        poll_retry_max_attempts: int = DEFAULT_POLL_RETRY_MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_func: Callable[[], float] = random.random,
    ) -> None:
        if not success_statuses <= terminal_statuses:
            raise ValueError("success_statuses must be a subset of terminal_statuses")
        submit_timeout_seconds = _validate_timing(
            "submit_timeout_seconds",
            submit_timeout_seconds,
            allow_zero=False,
            max_value=MAX_SUBMIT_TIMEOUT_SECONDS,
        )
        poll_timeout_seconds = _validate_timing(
            "poll_timeout_seconds",
            poll_timeout_seconds,
            allow_zero=False,
            max_value=MAX_POLL_TIMEOUT_SECONDS,
        )
        poll_interval_seconds = _validate_timing(
            "poll_interval_seconds",
            poll_interval_seconds,
            allow_zero=False,
            max_value=MAX_POLL_INTERVAL_SECONDS,
        )
        token_refresh_margin_seconds = _validate_timing(
            "token_refresh_margin_seconds",
            token_refresh_margin_seconds,
            allow_zero=True,
            max_value=MAX_TOKEN_REFRESH_MARGIN_SECONDS,
        )
        poll_retry_max_attempts = _validate_positive_int(
            "poll_retry_max_attempts",
            poll_retry_max_attempts,
            max_value=MAX_POLL_RETRY_MAX_ATTEMPTS,
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._region = region
        self._org_id = org_id
        self._terminal_statuses = terminal_statuses
        self._success_statuses = success_statuses
        self._submit_timeout_seconds = submit_timeout_seconds
        self._poll_timeout_seconds = poll_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_retry_max_attempts = poll_retry_max_attempts
        self._clock = clock
        self._sleep = sleep
        self._random = random_func
        self._token_cache = TokenCache(
            fetch=self._fetch_token,
            refresh_margin_seconds=token_refresh_margin_seconds,
            clock=clock,
        )

    def extract(self, document_path: str, action_id: str, version: str) -> NormalizedOutput:
        token = self._token_cache.get()
        start = self._clock()
        execution_id = self._submit(document_path, action_id, version, token)
        deadline = start + self._poll_timeout_seconds
        raw = self._poll(execution_id, action_id, version, token, deadline)
        elapsed_seconds = self._clock() - start
        logger.info(
            "idp_extraction_timing action_id=%s version=%s elapsed_seconds=%s",
            transport.sanitize_for_log(action_id),
            transport.sanitize_for_log(version),
            transport.sanitize_for_log(f"{elapsed_seconds:.3f}"),
        )
        return normalize(raw, self._success_statuses)

    # -- OAuth ------------------------------------------------------------

    def _fetch_token(self) -> tuple[str, float]:
        # The deferred-raise pattern (fail outside the `except` block) is
        # deliberate: raising *inside* an `except` clause always sets the
        # new exception's __context__ to the exception being handled, even
        # with `from None` (which only clears __cause__) — that would keep
        # a possibly-secret-bearing exception reachable via __context__
        # (Atchim R8).
        transport_failed = False
        status: int = 0
        body: object = None
        try:
            status, body = transport.post_json(
                TOKEN_URL,
                {
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout_seconds=self._submit_timeout_seconds,
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
            # /test Scenario B item 1 (primary defense): a token with CR/LF
            # or other control characters would otherwise be embedded
            # verbatim into an `Authorization: Bearer <token>` header,
            # which urllib rejects with a raw ValueError that echoes the
            # token — reject it here instead, before it ever reaches a
            # header. Never echo the token itself in the message.
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
        # Fail-closed on NaN/inf/non-positive/absurdly-large (/test Scenario
        # B item 3) — a NaN or inf expires_in would silently produce
        # expires_at = nan/inf, and NaN comparisons are always False, so the
        # cached token would never be treated as due for refresh again.
        if not math.isfinite(expires_in) or expires_in <= 0 or expires_in > MAX_EXPIRES_IN_SECONDS:
            raise IDPAuthenticationError("OAuth token response has an invalid expires_in")
        return access_token, expires_in

    # -- Submit (not retried — ADR-0004 #1/#4) -----------------------------

    def _submit(self, document_path: str, action_id: str, version: str, token: str) -> str:
        url = _executions_base_url(self._region, self._org_id, action_id, version)
        # Deferred-raise (see _fetch_token) — never raise while an
        # IDPTransportError is the "currently handled" exception, or it
        # would leak into __context__ even under `from None` (Atchim R8).
        transport_failed = False
        status: int = 0
        body: object = None
        try:
            status, body = transport.post_multipart_file(
                url,
                "file",
                document_path,
                timeout_seconds=self._submit_timeout_seconds,
                headers={"Authorization": f"Bearer {token}"},
            )
        except IDPTransportError:
            transport_failed = True
        if transport_failed:
            raise IDPSubmitError("IDP submit call failed or timed out") from None
        if status not in (200, 201, 202) or not isinstance(body, dict):
            raise IDPSubmitError(f"IDP submit call was rejected (status={status})")
        execution_id = body.get("id")
        if not isinstance(execution_id, str) or not execution_id:
            raise IDPSubmitError("IDP submit response is missing an execution id")
        return execution_id

    # -- Poll (configurable allowlist, monotonic budget — INV-07/BR9) -----

    def _poll_get_with_auth_retry(
        self, url: str, token: str, timeout_seconds: float
    ) -> tuple[str, int, object, dict[str, str]]:
        """A single poll GET, with the mid-poll 401/403 refresh-then-retry
        (DEBT-21, ADR-0004 #7). Once ``extract()`` has started polling, the
        loop can no longer re-submit to recover from an expired token — the
        POST executions call is not idempotent for the same document — so
        the refresh-and-retry has to live here, in the adapter's poll loop,
        not in the orchestrator (which only sees ``extract()`` raise or
        return). This does NOT touch A3 (the *initial* token fetch in
        ``_fetch_token``/``TokenCache._refresh`` before the first poll,
        called from ``extract()``) — that path stays fail-closed, no retry.
        """
        status_code, body, headers = transport.get_json_with_headers(
            url, timeout_seconds=timeout_seconds, headers={"Authorization": f"Bearer {token}"}
        )
        if status_code not in (401, 403):
            return token, status_code, body, headers
        # Refresh triggers unconditionally here (not gated on the token's
        # advertised expiry) -- IDP told us the token is no longer good, so
        # there's nothing to gain by trusting the cached expires_at. One
        # refresh, one retry of the SAME GET, then fail closed (ADR-0004
        # #7) -- `TokenCache.get()` raises `IDPAuthenticationError` itself
        # (fail-closed, no retry inside the cache) if the refresh is
        # rejected, and that propagates here unchanged.
        self._token_cache.invalidate()
        token = self._token_cache.get()
        status_code, body, headers = transport.get_json_with_headers(
            url, timeout_seconds=timeout_seconds, headers={"Authorization": f"Bearer {token}"}
        )
        if status_code in (401, 403):
            raise IDPAuthenticationError(
                "IDP rejected the request mid-run (401/403) after a token refresh"
            )
        return token, status_code, body, headers

    def _poll_retry_sleep_seconds(
        self,
        attempt: int,
        status_code: int,
        headers: dict[str, str],
        remaining_budget: float,
    ) -> float:
        """Exponential full-jitter backoff (ADR-0004 #3: base 1s, cap 8s),
        or the response's ``Retry-After`` on a 429 when present and sane —
        either way, never more than what's left of the absolute poll
        deadline (the retry budget is INCLUDED in, never extends, the
        per-document timeout)."""
        retry_after = (
            _parse_retry_after_seconds(headers.get("retry-after"))
            if status_code == 429
            else None
        )
        if retry_after is not None:
            sleep_seconds = retry_after
        else:
            cap = min(
                _POLL_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                _POLL_RETRY_BACKOFF_CAP_SECONDS,
            )
            sleep_seconds = self._random() * cap
        return min(sleep_seconds, remaining_budget)

    def _poll(
        self,
        execution_id: str,
        action_id: str,
        version: str,
        token: str,
        deadline: float,
    ) -> dict[str, object]:
        base_url = _executions_base_url(self._region, self._org_id, action_id, version)
        url = f"{base_url}/{execution_id}"
        last_status: str | None = None
        poll_retry_count = 0
        while True:
            now = self._clock()
            if now >= deadline:
                raise IDPPollTimeoutError(
                    "IDP execution did not reach a terminal status within the poll budget",
                    last_status=last_status,
                )
            remaining = max(deadline - now, 0.0)
            # Clamp the per-GET timeout and the inter-poll sleep to what's
            # left of the budget — a poll can no longer overshoot the
            # deadline waiting on a single slow request or a final sleep
            # (Atchim suggestion).
            per_call_timeout = min(self._poll_interval_seconds * 2, remaining) or remaining
            try:
                token, status_code, body, headers = self._poll_get_with_auth_retry(
                    url, token, per_call_timeout
                )
            except IDPTransportError:
                # Transient transport error — keep polling within the same budget.
                self._sleep(min(self._poll_interval_seconds, max(deadline - self._clock(), 0.0)))
                continue
            if status_code == 429 or 500 <= status_code < 600:
                # DEBT-24 / ADR-0004 #6: a transient 5xx/429 is retried,
                # bounded (never a bare "abort immediately") -- but a
                # non-429 4xx (400, 404, ...) still falls through to the
                # immediate-hard-failure branch below, unchanged.
                poll_retry_count += 1
                if poll_retry_count > self._poll_retry_max_attempts:
                    raise IDPPollHardFailureError(
                        "IDP poll retry budget exhausted for a transient failure",
                        http_status=status_code,
                    )
                remaining_budget = max(deadline - self._clock(), 0.0)
                sleep_seconds = self._poll_retry_sleep_seconds(
                    poll_retry_count, status_code, headers, remaining_budget
                )
                self._sleep(sleep_seconds)
                continue
            if not (200 <= status_code < 300):
                # ADR-0004 #5: any other non-2xx is a hard failure, aborted
                # immediately — the body's "status" is never read on this
                # path (Atchim R5).
                raise IDPPollHardFailureError(
                    "IDP poll request returned a non-success HTTP status",
                    http_status=status_code,
                )
            if not isinstance(body, dict):
                raise IDPAmbiguousStatusError(
                    "IDP poll response body was not a JSON object"
                )
            raw_status = body.get("status")
            # ADR-0004 #17: a missing/null/non-string status must abort
            # immediately — never inferred as "unknown, keep polling"
            # (Atchim R4). An unknown-but-present status string is NOT
            # ambiguous: it may just not be terminal yet, so keep polling.
            if raw_status is None or not isinstance(raw_status, str) or not raw_status:
                raise IDPAmbiguousStatusError(
                    "IDP poll response 'status' is missing, null, or not a non-empty string"
                )
            last_status = raw_status
            # Fail-closed: never hard-code the success string (BR9). Both
            # allowlists are config, checked here as set membership, never
            # a literal.
            if raw_status in self._terminal_statuses:
                if raw_status in self._success_statuses:
                    return body
                raise IDPExecutionFailedError(
                    "IDP execution ended in a non-success terminal status",
                    status=raw_status,
                )
            self._sleep(min(self._poll_interval_seconds, max(deadline - self._clock(), 0.0)))


def make_idp_adapter() -> MuleSoftIDPAdapter:
    """Factory reading ``IDP_*`` env config (BR6, ADR-0002 §Context).

    Action id and version are **not** read here — they are per-run
    parameters passed to ``extract()`` (ADR-0002 amendment 2026-09-19).
    """

    def _require(name: str) -> str:
        # DEBT-44 gate, fourth instance, finding R-3 (2026-09-21): a bare
        # `if not value:` accepts a whitespace-only value ("   " is
        # truthy) -- reproduced live, `IDP_CLIENT_SECRET="   "` passed
        # every N6 pre-run check. `.strip()` before the truthiness check
        # closes it without over-rejecting: a literal `"0"` credential is
        # still non-empty after stripping and stays accepted. Mirrors
        # `idp_regression.orchestration.bootstrap.validate_platform_credentials`'s
        # identical fix for the platform's three vars (Atchim review C-1).
        value = os.environ.get(name)
        if value is None or not value.strip():
            raise RuntimeError(f"missing required env var {name}")
        # DEBT-44 gate, fifth instance, suggestion (2026-09-21): return the
        # STRIPPED value, not the raw one -- a trailing newline (e.g. from
        # a file-sourced env var or a CI secret) previously passed
        # validation and was sent WITH the newline into the OAuth token
        # request body, a confusing auth failure on a sensitive surface
        # (the credential looks correct everywhere it's ever logged,
        # since no log/error message ever echoes a value -- INV-02).
        return value.strip()

    def _statuses(name: str, default: str) -> set[str]:
        raw = os.environ.get(name, default)
        return {item.strip() for item in raw.split(",") if item.strip()}

    def _timing_env(name: str, default: float) -> float:
        # A bare float(...) on an env string raises a raw ValueError for
        # "nan"/"inf" parse *successfully* (float("nan") is valid!) but
        # would otherwise escape unvalidated — MuleSoftIDPAdapter.__init__
        # is the authoritative gate (_validate_timing), but a non-numeric
        # env string ("banana") should fail with the same typed error
        # here too, not a raw ValueError (QA F-1).
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            raise IDPConfigurationError(f"{name} env var is not numeric") from None

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise IDPConfigurationError(f"{name} env var is not an integer") from None

    return MuleSoftIDPAdapter(
        client_id=_require("IDP_CLIENT_ID"),
        client_secret=_require("IDP_CLIENT_SECRET"),
        region=_require("IDP_REGION"),
        org_id=_require("IDP_ORG_ID"),
        terminal_statuses=_statuses("IDP_TERMINAL_STATUSES", "SUCCEEDED"),
        success_statuses=_statuses("IDP_SUCCESS_STATUSES", "SUCCEEDED"),
        submit_timeout_seconds=_timing_env(
            "IDP_SUBMIT_TIMEOUT_SECONDS", DEFAULT_SUBMIT_TIMEOUT_SECONDS
        ),
        poll_timeout_seconds=_timing_env(
            "IDP_EXECUTION_TIMEOUT_SECONDS", DEFAULT_POLL_TIMEOUT_SECONDS
        ),
        token_refresh_margin_seconds=_timing_env(
            "IDP_TOKEN_REFRESH_MARGIN_SECONDS", DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS
        ),
        poll_retry_max_attempts=_int_env(
            "IDP_POLL_RETRY_MAX_ATTEMPTS", DEFAULT_POLL_RETRY_MAX_ATTEMPTS
        ),
    )
