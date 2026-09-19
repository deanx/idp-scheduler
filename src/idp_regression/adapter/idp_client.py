"""``MuleSoftIDPAdapter`` — the ``IDPAdapter`` Protocol implementation over
MuleSoft Anypoint IDP (ADR-0002 Option B / §Design patterns "Adapter").

``extract()`` is the only public method: authenticate (cached token) ->
submit (not retried, own timeout) -> poll (configurable terminal-status
allowlist, monotonic-clock budget, INV-07) -> ``normalize()``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

from idp_regression.adapter import transport
from idp_regression.adapter.errors import (
    IDPAuthenticationError,
    IDPExecutionFailedError,
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
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._region = region
        self._org_id = org_id
        self._terminal_statuses = terminal_statuses
        self._success_statuses = success_statuses
        self._submit_timeout_seconds = submit_timeout_seconds
        self._poll_timeout_seconds = poll_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep
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
        except IDPTransportError as exc:
            raise IDPAuthenticationError("OAuth token request failed") from exc
        if status != 200 or not isinstance(body, dict):
            raise IDPAuthenticationError("OAuth token request was rejected")
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise IDPAuthenticationError("OAuth token response is missing access_token")
        expires_in_raw = body.get("expires_in", 300)
        try:
            expires_in = float(expires_in_raw)
        except (TypeError, ValueError):
            expires_in = 300.0
        return access_token, expires_in

    # -- Submit (not retried — ADR-0004 #1/#4) -----------------------------

    def _submit(self, document_path: str, action_id: str, version: str, token: str) -> str:
        url = _executions_base_url(self._region, self._org_id, action_id, version)
        try:
            status, body = transport.post_multipart_file(
                url,
                "file",
                document_path,
                timeout_seconds=self._submit_timeout_seconds,
                headers={"Authorization": f"Bearer {token}"},
            )
        except IDPTransportError as exc:
            raise IDPSubmitError("IDP submit call failed or timed out") from exc
        if status not in (200, 201, 202) or not isinstance(body, dict):
            raise IDPSubmitError(f"IDP submit call was rejected (status={status})")
        execution_id = body.get("id")
        if not isinstance(execution_id, str) or not execution_id:
            raise IDPSubmitError("IDP submit response is missing an execution id")
        return execution_id

    # -- Poll (configurable allowlist, monotonic budget — INV-07/BR9) -----

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
        while True:
            now = self._clock()
            if now >= deadline:
                raise IDPPollTimeoutError(
                    "IDP execution did not reach a terminal status within the poll budget",
                    last_status=last_status,
                )
            try:
                status_code, body = transport.get_json(
                    url,
                    timeout_seconds=self._poll_interval_seconds * 2,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except IDPTransportError:
                # Transient transport error — keep polling within the same budget.
                self._sleep(self._poll_interval_seconds)
                continue
            if status_code in (401, 403):
                raise IDPAuthenticationError("IDP rejected the request mid-run (401/403)")
            if not isinstance(body, dict):
                self._sleep(self._poll_interval_seconds)
                continue
            raw_status = body.get("status")
            if isinstance(raw_status, str) and raw_status:
                last_status = raw_status
                # ADR-0004 #17: fail-closed — never infer a terminal status,
                # never hard-code the success string (BR9). Both allowlists
                # are config, checked here as set membership, never a literal.
                if raw_status in self._terminal_statuses:
                    if raw_status in self._success_statuses:
                        return body
                    raise IDPExecutionFailedError(
                        "IDP execution ended in a non-success terminal status",
                        status=raw_status,
                    )
            self._sleep(self._poll_interval_seconds)


def make_idp_adapter() -> MuleSoftIDPAdapter:
    """Factory reading ``IDP_*`` env config (BR6, ADR-0002 §Context).

    Action id and version are **not** read here — they are per-run
    parameters passed to ``extract()`` (ADR-0002 amendment 2026-09-19).
    """

    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"missing required env var {name}")
        return value

    def _statuses(name: str, default: str) -> set[str]:
        raw = os.environ.get(name, default)
        return {item.strip() for item in raw.split(",") if item.strip()}

    return MuleSoftIDPAdapter(
        client_id=_require("IDP_CLIENT_ID"),
        client_secret=_require("IDP_CLIENT_SECRET"),
        region=_require("IDP_REGION"),
        org_id=_require("IDP_ORG_ID"),
        terminal_statuses=_statuses("IDP_TERMINAL_STATUSES", "SUCCEEDED"),
        success_statuses=_statuses("IDP_SUCCESS_STATUSES", "SUCCEEDED"),
        submit_timeout_seconds=float(
            os.environ.get("IDP_SUBMIT_TIMEOUT_SECONDS", DEFAULT_SUBMIT_TIMEOUT_SECONDS)
        ),
        poll_timeout_seconds=float(
            os.environ.get("IDP_EXECUTION_TIMEOUT_SECONDS", DEFAULT_POLL_TIMEOUT_SECONDS)
        ),
        token_refresh_margin_seconds=float(
            os.environ.get(
                "IDP_TOKEN_REFRESH_MARGIN_SECONDS", DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS
            )
        ),
    )
