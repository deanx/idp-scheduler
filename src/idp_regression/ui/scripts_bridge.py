"""The one seam where the UI reaches the batch scripts.

`golden_pipeline.py` established the rule this follows: *each stage is
the real script, loaded and called in process -- never a
reimplementation*. The floor comparison, the zip trust boundary and the
pin-store layout are decisions that already exist, each with tests behind
them, and a second copy inside the UI would be the copy that drifts and
the copy nobody notices has drifted.

So the UI imports them, and every such import is confined to this module
-- the same containment `NFR N24` applies to the evaluation
platform's SDK. If
`scripts/` ever becomes a package, this file is the only one to change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(REPO_ROOT / "src") not in sys.path:  # pragma: no cover - import-time wiring
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(SCRIPTS_DIR) not in sys.path:  # pragma: no cover - `_batch` is imported by name
    sys.path.insert(0, str(SCRIPTS_DIR))

_CACHE: dict[str, Any] = {}


def load(name: str) -> Any:
    """Load `scripts/<name>.py` once, by path, and return the module."""
    if name in _CACHE:
        return _CACHE[name]
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_ui_bridge_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - only on a broken checkout
        raise RuntimeError(f"cannot load scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _CACHE[name] = module
    return module
