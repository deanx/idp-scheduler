"""`.env` loading for the orchestrator entry points (INV-05).

Thin delegation to `python-dotenv`, which ADR-0004 and every
adapter/platform module's docstring already assume by name — they say
"the caller has already called ``load_dotenv()``", and this module is
that caller's implementation.

**DEBT-56, resolved 2026-09-21.** `python-dotenv` was *not* a declared
dependency when T-01.4.1 landed — the first real call site in the
project. The batch shipped a small stdlib parser as a scope-respecting
workaround (the concurrent agent owned `pyproject.toml`) and disclosed it
as debt rather than absorbing it silently. The dependency has since been
added, so the workaround is gone: keeping a hand-rolled parser that its
own docstring described as *"not feature-complete — no ``export`` prefix,
no ``${VAR}`` interpolation, no multiline values"* would have left a
latent bug waiting for the first `.env` that used any of them.

⚠️ **The path is passed explicitly, and that is load-bearing.** A bare
``load_dotenv()`` calls ``find_dotenv()``, which walks up from **the
calling frame's file** — i.e. from *this module's location in the source
tree* — and only falls back to the cwd when the caller has no real
filename (a ``-c`` string, a REPL). So the bare form resolves relative to
**wherever the code is installed**, ignoring the operator's working
directory entirely: an installed package would read a `.env` sitting next
to its own source, and a CI job's working directory would be disregarded.
That is the same REG-07/REG-10 family (*a credential reaching a place the
caller did not pick*), and it is what pulled this checkout's real
credential file into the test process — nine tests broke on the swap,
which is how it was caught.

⚠️ **Correction, 2026-09-21 (Atchim review R-… / orchestrator's error).**
An earlier version of this docstring said the bare call would read "an
unrelated *parent directory's* `.env`". That describes only the cwd
**fallback** path, not the actual mechanism, and it understated the
problem: resolution keys off the *source tree*, not the cwd's ancestry.
Recorded rather than quietly reworded, because a wrong mechanism in a
docstring is how the next reader reintroduces the bug while believing
they understand it.

`override=False` is explicit rather than implied: a variable already
present in the real environment **wins** over the file. That ordering is
what lets CI inject credentials without a `.env`, and what makes the
double call from `cli.main()` and `run_eval()` idempotent — `cli.main()`
needs it before any credential is read (INV-05), and `run_eval()` needs
it because it is the direct caller of `make_platform()` /
`make_idp_adapter()`. (`--action` and `--dataset` are required CLI flags
with no environment fallback as of 2026-09-22 — user decision — this
call no longer resolves either of them; it still gates every credential
read.)
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_ENV_FILE_NAME = ".env"

__all__ = ["load_dotenv", "load_dotenv_file"]


def load_dotenv() -> None:
    """Load `./.env` into `os.environ` without overriding existing vars.

    A missing `.env` is not an error — CI supplies credentials directly.
    """
    load_dotenv_file(Path.cwd() / _ENV_FILE_NAME)


def load_dotenv_file(path: Path) -> None:
    """Load one named `.env` file, without touching the process's
    working directory.

    `load_dotenv()` above is the CLI's shape: one shell, one directory,
    `./.env`. A SERVER has neither — it serves concurrent requests from a
    threadpool, and `os.chdir` there is a process-global race against any
    other request resolving a relative path in the same window. So a
    caller that already knows which file it wants says so, and nothing
    chdirs (Zangado QA F-4, 2026-09-25).
    """
    _load_dotenv(dotenv_path=path, override=False)
