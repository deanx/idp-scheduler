"""NFR N24 swappability — platform-vendor references confined to
``src/idp_regression/platform/`` (static grep, no live import needed)."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression"
PLATFORM_DIR = ROOT / "platform"
_VENDOR_PATTERN = re.compile(r"\blangfuse\b", re.IGNORECASE)


def test_no_vendor_reference_outside_platform_package() -> None:
    offenders = []
    for path in ROOT.rglob("*.py"):
        if PLATFORM_DIR in path.parents or path.parent == PLATFORM_DIR:
            continue
        text = path.read_text(encoding="utf-8")
        if _VENDOR_PATTERN.search(text):
            offenders.append(str(path))
    assert offenders == [], f"Langfuse references leaked outside platform/: {offenders}"
