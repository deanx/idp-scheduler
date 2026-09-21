"""Fail-closed client construction for the orchestrator entry point
(T-01.4.1, DEBT-30, NFR N6).

`make_platform()` (`idp_regression.platform.langfuse_adapter`) reads
`LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` by direct
subscript on `os.environ`, so an operator who forgets one gets a raw
`KeyError` traceback out of the factory. NFR N6 requires `run_eval` to
exit non-zero with a CLEAR message before any network call — a traceback
is neither clear nor a controlled exit. DEBT-30 verified this is
single-owned by T-01.4.1 (the adapter side alone cannot satisfy N6): this
module wraps or pre-validates at the entry point, per that ruling.

`make_idp_adapter()` (`idp_regression.adapter.idp_client`) already raises
a clear `RuntimeError(f"missing required env var {name}")` on its own —
no wrapping needed there; `run_eval` still catches it so a missing IDP
credential produces a controlled exit rather than an unhandled
traceback (see `facade.py`).
"""

from __future__ import annotations

from idp_regression.platform.langfuse_adapter import make_platform
from idp_regression.platform.types import PlatformAdapter


class MissingCredentialError(Exception):
    """A required `PLATFORM`/`LANGFUSE_*` env var is missing (DEBT-30).

    Names only the missing variable — never a value (INV-02)."""

    def __init__(self, variable_name: str) -> None:
        super().__init__(f"missing required env var {variable_name}")
        self.variable_name = variable_name


def construct_platform() -> PlatformAdapter:
    """`make_platform()`, with its bare `KeyError` on a missing env var
    converted into a typed, clear-message `MissingCredentialError`. Any
    other platform error (`PlatformConfigurationError`, the unsupported-
    `PLATFORM` `ValueError`) already carries a clear message and
    propagates unchanged."""
    try:
        return make_platform()
    except KeyError as exc:
        missing = exc.args[0] if exc.args else "<unknown>"
        raise MissingCredentialError(str(missing)) from exc
