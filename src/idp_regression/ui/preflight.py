"""Is this machine able to run a validation at all? Asked BEFORE paying.

Without this, the first thing that tells an operator `IDP_REGION` is
unset is `pin_document.py` failing -- after the archive was uploaded,
after the plan was approved, and after the first extraction was billed.
The check costs nothing and takes no network call.

**Presence, never values.** Every credential here is reported as
present/absent by NAME only. This console binds to loopback and has no
authentication, and a page that echoed a client secret back would turn
"readable by this OS account" into "readable by anything that can reach
the port and render HTML" -- the same INV-02 rule the adapter's error
messages follow, applied to a dashboard.

Absent is not always fatal, and the difference matters:

* **IDP credentials** are required for any extraction. Without them a
  validation run cannot start, so the UI refuses rather than charging
  for a failure.
* **Platform credentials** are required for the goldens to be
  provisioned and the scores recorded. Without them the run reaches IDP,
  spends quota, and then fails -- the WORST outcome of the three, so it
  is reported just as loudly.
* **A missing `.env`** is not itself an error: CI and a shell export
  supply the same variables. It is reported so "nothing is set" can be
  told apart from "set somewhere I did not look".
"""

from __future__ import annotations

import os
from typing import Any, Final

from idp_regression.adapter import REQUIRED_ENV_VARS as _IDP_REQUIRED
from idp_regression.platform import REQUIRED_ENV_VARS as _PLATFORM_REQUIRED
from idp_regression.ui import workspace

#: **Imported, never re-typed.** Each list lives beside the code that
#: enforces it, so a variable renamed there cannot leave this check
#: passing on a machine that cannot run -- which would be worse than no
#: preflight, because this one is trusted. Importing the platform's list
#: also keeps the vendor's variable names inside `platform/` (N24).
IDP_VARIABLES: Final = _IDP_REQUIRED
PLATFORM_VARIABLES: Final = _PLATFORM_REQUIRED


def _present(name: str) -> bool:
    """Set AND non-blank.

    `IDP_CLIENT_SECRET="   "` passing a presence check is a real bug this
    repo has already fixed once (`bootstrap.py`'s `_require`); a
    whitespace-only credential fails at the API, not at the check.
    """
    return bool(os.environ.get(name, "").strip())


def check() -> dict[str, Any]:
    env = workspace.load_environment()
    idp = {name: _present(name) for name in IDP_VARIABLES}
    platform = {name: _present(name) for name in PLATFORM_VARIABLES}
    missing_idp = sorted(name for name, ok in idp.items() if not ok)
    missing_platform = sorted(name for name, ok in platform.items() if not ok)

    blockers: list[str] = []
    if missing_idp:
        blockers.append(
            "IDP credentials are missing (" + ", ".join(missing_idp) + ") -- a validation "
            "run cannot extract anything, so it would fail on the first document."
        )
    if missing_platform:
        blockers.append(
            "Evaluation-platform credentials are missing (" + ", ".join(missing_platform)
            + ") -- the goldens cannot be provisioned. A run would reach IDP, SPEND "
            "quota, and then fail: the most expensive way to find this out."
        )

    return {
        "can_run_validation": not blockers,
        "blockers": blockers,
        "idp": idp,
        "platform": platform,
        "env_file": env["env_file"],
        "env_searched": env["searched"],
        "workspace": str(workspace.workspace_root()),
        # Where a job will write, stated so an operator can see it is the
        # same place the Runs and Pins pages read.
        "writes": {
            "run_artifacts": str(workspace.artifact_dir()),
            "pin_store": str(workspace.pin_store_dir()),
            "uploads": str(workspace.upload_dir()),
        },
    }
