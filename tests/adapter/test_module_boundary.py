"""Static-grep gates for the adapter module.

1. BR9 / ADR-0004 #17: the terminal-status check is always a configurable
   allowlist membership test, never a hard-coded ``== "SUCCEEDED"`` (or
   similar literal) comparison.
2. N24-style boundary: adapter HTTP calls (``urllib``, the MuleSoft host,
   IDP credentials) are confined to ``src/idp_regression/adapter/`` — no
   other package builds an IDP request directly.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression"
ADAPTER_DIR = ROOT / "adapter"

_HARDCODED_SUCCEEDED_PATTERN = re.compile(r'==\s*"SUCCEEDED"|"SUCCEEDED"\s*==')
_VENDOR_HOST_PATTERN = re.compile(r"\bmulesoft\.com\b|\banypoint\b", re.IGNORECASE)
_URLLIB_PATTERN = re.compile(r"\burllib\b")


def _adapter_py_files() -> list[pathlib.Path]:
    return sorted(ADAPTER_DIR.rglob("*.py"))


def test_no_hardcoded_succeeded_comparison_in_adapter() -> None:
    offenders = []
    for path in _adapter_py_files():
        text = path.read_text(encoding="utf-8")
        if _HARDCODED_SUCCEEDED_PATTERN.search(text):
            offenders.append(str(path))
    assert offenders == [], (
        f"hard-coded '== \"SUCCEEDED\"' found (BR9 requires a configurable "
        f"allowlist): {offenders}"
    )


def test_mulesoft_host_reference_confined_to_adapter_package() -> None:
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ADAPTER_DIR in path.parents or path.parent == ADAPTER_DIR:
            continue
        text = path.read_text(encoding="utf-8")
        if _VENDOR_HOST_PATTERN.search(text):
            offenders.append(str(path))
    assert offenders == [], f"MuleSoft/IDP host references leaked outside adapter/: {offenders}"


def test_urllib_usage_confined_to_adapter_and_platform_packages() -> None:
    # platform/transport.py has its own independent urllib usage (raw-REST
    # Langfuse provisioning) — both packages own a raw HTTP seam; neither
    # depends on the other (N24 swappability for each vendor).
    platform_dir = ROOT / "platform"
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ADAPTER_DIR in path.parents or path.parent == ADAPTER_DIR:
            continue
        if platform_dir in path.parents or path.parent == platform_dir:
            continue
        text = path.read_text(encoding="utf-8")
        if _URLLIB_PATTERN.search(text):
            offenders.append(str(path))
    assert offenders == [], f"urllib usage leaked outside adapter/ and platform/: {offenders}"
