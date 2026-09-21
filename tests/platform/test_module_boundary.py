"""NFR N24 swappability — platform-vendor references confined to
``src/idp_regression/platform/`` (static grep, no live import needed)."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression"
PLATFORM_DIR = ROOT / "platform"
#: No `\b` boundaries: `_` is a word character, so a bounded pattern
#: never matches a qualified reference like `platform.langfuse_adapter`
#: or an env-var literal like `LANGFUSE_HOST` — the exact blind spot
#: Atchim review R-1 (2026-09-21) found. A bare, case-insensitive
#: substring match closes it: anything outside `platform/` that spells
#: the vendor's name, in any form, is a finding.
_VENDOR_PATTERN = re.compile(r"langfuse", re.IGNORECASE)


def test_no_vendor_reference_outside_platform_package() -> None:
    offenders = []
    for path in ROOT.rglob("*.py"):
        if PLATFORM_DIR in path.parents or path.parent == PLATFORM_DIR:
            continue
        text = path.read_text(encoding="utf-8")
        if _VENDOR_PATTERN.search(text):
            offenders.append(str(path))
    assert offenders == [], f"Langfuse references leaked outside platform/: {offenders}"
