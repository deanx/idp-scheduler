"""`oauth.fetch_access_token` -- the token fetch the version probe uses.

Its token-character guard mirrors `idp_client._fetch_token`'s, which is
tested in `test_idp_client.py`; this one had no test of its own.
"""

from __future__ import annotations

import pytest

from idp_regression.adapter import oauth, transport
from idp_regression.adapter.errors import IDPAuthenticationError


# "tok€en": printable, not ASCII -- RFC 6750 b64token is ASCII, and
# http.client would reject it with a message naming it (S-01.2 re-stamp F-2).
@pytest.mark.parametrize("bad_token", ["tok\r\ninjected", "tok\x00null", "tok€en"])
def test_a_token_outside_printable_ascii_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, bad_token: str
) -> None:
    monkeypatch.setattr(
        transport, "post_json",
        lambda *a, **k: (200, {"access_token": bad_token, "expires_in": 300}),
    )
    with pytest.raises(IDPAuthenticationError) as excinfo:
        oauth.fetch_access_token("id", "secret", 5.0)
    assert bad_token not in str(excinfo.value)
    assert excinfo.value.__context__ is None


def test_a_printable_ascii_token_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transport, "post_json",
        lambda *a, **k: (200, {"access_token": "abc.DEF-123_~+/=", "expires_in": 300}),
    )
    assert oauth.fetch_access_token("id", "secret", 5.0)[0] == "abc.DEF-123_~+/="
