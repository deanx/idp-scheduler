"""The one directory this console reads and writes.

Every tool in this repo names its output with a RELATIVE path --
`.idp-regression-pins/`, `.idp-regression-run-artifacts/`,
`.idp-regression-noise-floor/` -- which means "relative to the process's
working directory". That is fine for a CLI, where one shell has one
working directory. It is a trap for a console that both reads those
directories itself and starts child processes that write them.

**The bug this module exists to close.** The job runner used to launch
`compare_versions.py` with `cwd=<repo root>` (so the scripts could find
`.env`), while `reader.py` resolved its paths against the CONSOLE's
working directory. Start the console from the repo root and the two
coincide; start it anywhere else and a validation run writes its pins and
its run artifact somewhere the Pins and Runs pages never look. The user
pays for a batch and the console shows nothing -- with no error, because
nothing failed.

So there is now exactly one answer to "where does this console keep
things", it is resolved once, and both sides use it:

* `reader.py` and `uploads.py` resolve their directories under it;
* `jobs.py` runs every child process with it as the working directory,
  so a script's own relative default lands where the console will look.

Credentials are decoupled from it on purpose. The scripts' own
`load_dotenv()` reads `./.env` relative to the CURRENT directory, so
tying the child's `cwd` to the workspace would have broken credential
loading for any workspace that is not the repo. `load_environment()`
below loads `.env` into the console's own environment at startup -- from
the workspace first, then the repo root, BY PATH and never by `chdir` --
and the child inherits it. That is why the workspace can be any
directory and the job still authenticates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: Set once, at import, from the directory the console was started in.
#: `set_workspace()` overrides it (tests, and a future `--workspace`).
_ROOT: Path = Path.cwd().resolve()

REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: The directories the console owns, all relative to the workspace.
ARTIFACT_DIR_NAME: Final = ".idp-regression-run-artifacts"
NOISE_FLOOR_DIR_NAME: Final = ".idp-regression-noise-floor"
PIN_STORE_DIR_NAME: Final = ".idp-regression-pins"
UPLOAD_DIR_NAME: Final = ".idp-regression-uploads"
SCORER_DIR_NAME: Final = ".idp-regression-scorers"
ENV_FILE_NAME: Final = ".env"


def workspace_root() -> Path:
    return _ROOT


def set_workspace(root: Path) -> None:
    global _ROOT
    _ROOT = Path(root).resolve()


def artifact_dir() -> Path:
    return _ROOT / ARTIFACT_DIR_NAME


def noise_floor_dir() -> Path:
    return _ROOT / NOISE_FLOOR_DIR_NAME


def pin_store_dir() -> Path:
    return _ROOT / PIN_STORE_DIR_NAME


def upload_dir() -> Path:
    return _ROOT / UPLOAD_DIR_NAME


def scorer_dir() -> Path:
    return _ROOT / SCORER_DIR_NAME


def load_environment() -> dict[str, object]:
    """Load `.env` into this process, so child jobs inherit credentials.

    Searched in the workspace first, then the repo root. Reports which
    file was used (path only -- **never a value, never a key name that
    was found in it**), so the preflight can tell an operator "no .env
    anywhere" rather than letting them discover it after the first
    extraction is billed.

    Never overrides an existing environment variable: a credential
    exported in the shell wins over a file, which is the same rule
    `load_dotenv()` itself applies.
    """
    from idp_regression.orchestration.dotenv_support import load_dotenv_file

    searched = [_ROOT / ENV_FILE_NAME, REPO_ROOT / ENV_FILE_NAME]
    used: Path | None = None
    for candidate in searched:
        if not candidate.is_file():
            continue
        # Loaded BY PATH, never by chdir. This runs on a request thread
        # (`/api/preflight`), and `os.chdir` there is a process-global
        # race against any other request resolving a relative path in the
        # same window (Zangado QA F-4, 2026-09-25).
        load_dotenv_file(candidate)
        used = candidate
        break

    if used is None:
        logger.info("no_env_file searched=%s", ", ".join(str(p) for p in searched))
    else:
        logger.info("env_file_loaded path=%s", used)
    return {
        "env_file": str(used) if used else None,
        "searched": [str(p) for p in searched],
    }
