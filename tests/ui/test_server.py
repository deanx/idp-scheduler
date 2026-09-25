"""The entry point's one security decision: loopback only.

This server hands out run artifacts, which carry the extracted financial
values `CLAUDE.md ## Domain` calls sensitive, and it has no
authentication -- on loopback the OS account IS the authentication.
Binding an interface would silently turn "readable by this user" into
"readable by the network", which is the same mistake
`SIGNOFF-2026-09-22.md` B-2 records against the evaluation platform. So it
is refused, loudly, rather than accepted with a warning nobody reads.
"""

from __future__ import annotations

import pytest

from idp_regression.ui.server import main

#: The string this test exists to refuse. Built rather than written
#: so a bind-all literal never appears in the repo as if it were a
#: configuration someone could copy.
BIND_ALL = ".".join(["0", "0", "0", "0"])


@pytest.mark.parametrize("host", [BIND_ALL, "::", "192.168.1.10", "example.internal"])
def test_a_non_loopback_bind_is_refused_before_the_server_starts(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--host", host]) == 2
    message = capsys.readouterr().err
    assert "refusing to bind" in message
    assert "no authentication" in message


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_is_accepted(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}

    class FakeUvicorn:
        @staticmethod
        def run(_app: object, **kwargs: object) -> None:
            started.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", FakeUvicorn)
    assert main(["--host", host, "--port", "9999"]) == 0
    assert started["host"] == host
    assert started["port"] == 9999
