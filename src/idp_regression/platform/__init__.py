"""Vendor-neutral entry points for the evaluation-platform package (NFR N24).

Code outside `platform/` must import through this package, never reach
into `platform.langfuse_adapter` (or any other vendor-specific module)
directly — that is what keeps the platform genuinely swappable (ADR-0005)
and keeps the N24 vendor-neutrality guard
(`tests/platform/test_module_boundary.py`) meaningful rather than
structurally blind to a qualified import (Atchim review R-1, 2026-09-21 —
`\\blangfuse\\b` never matched `platform.langfuse_adapter` because `_` is
a word character; the guard is now a bare, case-insensitive `langfuse`
substring match, so any vendor-specific reference outside this file must
go through the re-exports below).
"""

from __future__ import annotations

from idp_regression.platform.langfuse_adapter import make_platform

#: Env vars `make_platform()` requires (DEBT-30, NFR N6). Exposed here so
#: a consumer outside `platform/` (the orchestrator's fail-closed
#: credential check, T-01.4.1) can validate presence without hand-listing
#: vendor-specific var names itself, and without constructing a real
#: client just to find out (Atchim review R-3/C-1, 2026-09-21 — a client
#: constructed only to be discarded leaks a background export thread per
#: call, and is a non-zero client construction on a path HARDEN-01's
#: split-brain condition asserts must be zero).
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)

__all__ = ["make_platform", "REQUIRED_ENV_VARS"]
