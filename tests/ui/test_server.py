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

from pathlib import Path

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


def test_the_default_scorer_dir_follows_the_workspace_not_the_servers_own_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The console's own scorer routes (list/save/delete) and a
    validation job's `register_custom_classifiers()` must agree on
    WHERE scorers live. A job runs with `cwd=workspace.workspace_root()`
    (`jobs.py`), so its own bare-relative default (`scorer_store.
    SCORER_DIR`) resolves against the workspace. `create_app`'s own
    default used to be that SAME bare-relative constant resolved
    against the SERVER PROCESS's cwd instead -- correct only when
    `--workspace` is never passed, i.e. only by coincidence -- exactly
    the class of bug `ui/workspace.py`'s own docstring describes for
    every other directory this console owns."""
    from idp_regression.ui import workspace as workspace_module

    started: dict[str, object] = {}
    captured_scorer_dir: dict[str, object] = {}

    class FakeUvicorn:
        @staticmethod
        def run(_app: object, **kwargs: object) -> None:
            started.update(kwargs)

    def _fake_create_app(*, dev_cors: bool, scorer_dir: object) -> object:
        captured_scorer_dir["value"] = scorer_dir
        return object()

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", FakeUvicorn)
    monkeypatch.setattr("idp_regression.ui.api.create_app", _fake_create_app)
    monkeypatch.setattr(workspace_module, "load_environment", lambda: None)
    monkeypatch.setattr(
        "idp_regression.ui.preflight.check",
        lambda: {
            "workspace": str(tmp_path), "env_file": None,
            "writes": {"run_artifacts": "ok"}, "blockers": [],
        },
    )

    main(["--workspace", str(tmp_path), "--port", "9999"])

    assert captured_scorer_dir["value"] == tmp_path / workspace_module.SCORER_DIR_NAME
