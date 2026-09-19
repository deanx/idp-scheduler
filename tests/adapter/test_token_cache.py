"""T-01.2.2 — instance-held TokenCache (get/refresh) with a refresh margin
(BR7, ADR-0004 #7)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from idp_regression.adapter.errors import IDPAuthenticationError
from idp_regression.adapter.token_cache import TokenCache


def _clock_from(seq: list[float]) -> Callable[[], float]:
    it = iter(seq)

    def clock() -> float:
        return next(it)

    return clock


def test_first_get_fetches_a_token() -> None:
    calls = []

    def fetch() -> tuple[str, float]:
        calls.append(1)
        return "token-1", 300.0

    cache = TokenCache(fetch=fetch, refresh_margin_seconds=60.0, clock=lambda: 0.0)
    assert cache.get() == "token-1"
    assert len(calls) == 1


def test_get_reuses_the_cached_token_within_the_run() -> None:
    calls = []

    def fetch() -> tuple[str, float]:
        calls.append(1)
        return "token-1", 300.0

    # get() #1: now=0.0 (needs refresh) -> _refresh() reads clock again for
    # expires_at=0.0+300=300.0. get() #2: now=10.0 (well within margin).
    cache = TokenCache(
        fetch=fetch, refresh_margin_seconds=60.0, clock=_clock_from([0.0, 0.0, 10.0])
    )
    assert cache.get() == "token-1"
    assert cache.get() == "token-1"
    assert len(calls) == 1  # not re-fetched


def test_get_refreshes_once_within_the_margin_of_expiry() -> None:
    tokens = iter([("token-1", 100.0), ("token-2", 100.0)])
    calls = []

    def fetch() -> tuple[str, float]:
        calls.append(1)
        return next(tokens)

    # expires_at = 0 + 100 = 100; margin 60 -> refresh trigger at t=40.
    cache = TokenCache(
        fetch=fetch, refresh_margin_seconds=60.0, clock=_clock_from([0.0, 0.0, 45.0, 45.0])
    )
    assert cache.get() == "token-1"
    assert cache.get() == "token-2"
    assert len(calls) == 2


def test_fetch_failure_raises_typed_error_fail_closed_no_retry() -> None:
    calls = []

    def fetch() -> tuple[str, float]:
        calls.append(1)
        raise RuntimeError("network exploded")

    cache = TokenCache(fetch=fetch, refresh_margin_seconds=60.0, clock=lambda: 0.0)
    with pytest.raises(IDPAuthenticationError):
        cache.get()
    assert len(calls) == 1  # no retry


def test_typed_auth_error_from_fetch_propagates_unwrapped() -> None:
    def fetch() -> tuple[str, float]:
        raise IDPAuthenticationError("OAuth token request was rejected")

    cache = TokenCache(fetch=fetch, refresh_margin_seconds=60.0, clock=lambda: 0.0)
    with pytest.raises(IDPAuthenticationError, match="rejected"):
        cache.get()


def test_invalidate_forces_a_refetch() -> None:
    calls = []

    def fetch() -> tuple[str, float]:
        calls.append(1)
        return "token-1", 300.0

    cache = TokenCache(fetch=fetch, refresh_margin_seconds=60.0, clock=lambda: 0.0)
    cache.get()
    cache.invalidate()
    cache.get()
    assert len(calls) == 2
