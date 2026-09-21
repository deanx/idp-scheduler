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
``load_dotenv()`` resolves the file by walking **up** from the calling
module, so it would silently pick up an unrelated *parent* directory's
`.env` — a credential file the operator never chose, which is the
REG-07/REG-10 family (*a credential reaching a place the caller did not
pick*) in a new dress. The contract ADR-0004, `.env.example` and the
tests all assume is **read `./.env` from the current working
directory**; that is what this does. Swapping to the bare call silently
changes which file is read — it was caught here only because the tests
pin the cwd behaviour.

`override=False` is explicit rather than implied: a variable already
present in the real environment **wins** over the file. That ordering is
what lets CI inject credentials without a `.env`, and what makes the
double call from `cli.main()` and `run_eval()` idempotent — `cli.main()`
needs it before `--action`'s `IDP_ACTION_ID` fallback is resolved, and
`run_eval()` needs it because it is the direct caller of
`make_platform()` / `make_idp_adapter()`.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_ENV_FILE_NAME = ".env"

__all__ = ["load_dotenv"]


def load_dotenv() -> None:
    """Load `./.env` into `os.environ` without overriding existing vars.

    A missing `.env` is not an error — CI supplies credentials directly.
    """
    _load_dotenv(dotenv_path=Path.cwd() / _ENV_FILE_NAME, override=False)
