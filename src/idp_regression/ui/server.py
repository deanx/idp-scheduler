"""Run the local console.

    .venv/bin/python -m idp_regression.ui --port 8000

Bound to 127.0.0.1 and **refuses to bind anywhere else**. This server
hands out run artifacts, which carry the extracted financial values
`CLAUDE.md ## Domain` calls sensitive, and it has no authentication
because on loopback the OS account IS the authentication. Binding it to
an interface would silently turn "readable by this user" into "readable
by the network", which is the same mistake `SIGNOFF-2026-09-22.md` B-2
records against the evaluation platform's own instance.

`--dev-cors` allows the Vite dev server's origin. It is off by default
and should stay off outside development, because with it on any page the
browser happens to load can read a run out of this port.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m idp_regression.ui",
        description=(
            "Local console for run artifacts, noise floors, blind spots, pins and scorers."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="loopback only (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--dev-cors",
        action="store_true",
        help="allow http://localhost:5173 (the Vite dev server). Development only.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=(
            "where this console reads and writes artifacts, and where validation jobs "
            "run (default: the current directory)"
        ),
    )
    parser.add_argument(
        "--scorer-dir",
        type=Path,
        default=None,
        help="where custom scorer specs live (default: .idp-regression-scorers/)",
    )
    args = parser.parse_args(argv)

    if args.host not in LOOPBACK:
        print(
            f"idp-regression-ui: refusing to bind {args.host!r}. This server serves run "
            "artifacts, which hold extracted financial values, and it has no "
            "authentication -- loopback is what makes that safe. Put it behind an "
            "authenticating reverse proxy if it genuinely has to leave this machine.",
            file=sys.stderr,
        )
        return 2

    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "idp-regression-ui: the UI extra is not installed. `uv sync --extra ui`.",
            file=sys.stderr,
        )
        return 2

    from idp_regression.ui import preflight, workspace
    from idp_regression.ui.api import FRONTEND_DIST, create_app

    if args.workspace is not None:
        workspace.set_workspace(args.workspace)

    # Load `.env` into THIS process so every child job inherits the
    # credentials, wherever the workspace happens to be. Without this a
    # job could only authenticate when the console ran from the repo
    # root -- see `workspace.py`.
    workspace.load_environment()

    app = create_app(dev_cors=args.dev_cors, scorer_dir=args.scorer_dir)

    status = preflight.check()
    print(f"idp-regression-ui: workspace {status['workspace']}")
    print(f"idp-regression-ui: run artifacts {status['writes']['run_artifacts']}")
    if status["env_file"]:
        print(f"idp-regression-ui: credentials from {status['env_file']}")
    for blocker in status["blockers"]:
        # Printed at startup as well as served at /api/preflight: an
        # operator who never opens the Validate page should still learn
        # that this machine cannot run one.
        print(f"idp-regression-ui: ⚠️  {blocker}", file=sys.stderr)
    if not FRONTEND_DIST.is_dir():
        print(
            f"idp-regression-ui: no built frontend at {FRONTEND_DIST} — serving the API only. "
            "Build it with `cd frontend && npm install && npm run build`, or run the Vite "
            "dev server and start this with --dev-cors.",
            file=sys.stderr,
        )
    print(
        f"idp-regression-ui: http://{args.host}:{args.port}  "
        "(reads local artifacts; spends no quota)"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
