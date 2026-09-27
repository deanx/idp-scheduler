"""DEBT-29 -- `scripts/doc_lint.py`: prose that names code the repo no longer has.

These pin the lint's *logic* against synthetic inputs. The run against the
real repo is a report (non-blocking by design, DEBT-29), so it is not
asserted to be empty here -- only that it runs and returns its findings.
Each test names the drift instance it stands for, and so the mutation it
kills: drop the rule and that instance goes unreported.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "doc_lint", REPO_ROOT / "scripts" / "doc_lint.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["doc_lint"] = module
    spec.loader.exec_module(module)
    return module


dl = _load()

SOURCE = """
from typing import Protocol, TypedDict

class Base:
    def inherited(self) -> None: ...

class PlatformAdapter(Protocol):
    def get_dataset(self) -> None: ...
    def record_run(self) -> None: ...

class Child(Base):
    field: int
    def own(self) -> None: ...

class Doc(TypedDict):
    document_id: str

def run_eval() -> int: ...
CEILING = 1000
"""


def _surface() -> object:
    return dl.collect_surface([("src/pkg/mod.py", SOURCE)])


def _kinds(text: str, retired: set[str] | None = None) -> list[str]:
    return [f.kind for f in dl.scan_prose("docs/x.md", text, _surface(), retired)]


# --- symbol drift -------------------------------------------------------------


def test_a_protocol_member_the_protocol_no_longer_has_is_reported() -> None:
    # The DEBT-29 instance itself: `PlatformAdapter.flush` after ADR-0005 #9.
    assert _kinds("The seam is `PlatformAdapter.flush()`.") == ["no-such-member"]
    assert _kinds("The seam is `PlatformAdapter.record_run`.") == []


def test_a_member_inherited_from_a_project_base_is_not_reported() -> None:
    assert _kinds("`Child.inherited()` and `Child.own` and `Child.field`") == []


def test_a_class_with_an_external_base_is_not_second_guessed() -> None:
    # TypedDict members come from the runtime; the lint cannot rule them out.
    assert _kinds("`Doc.keys()` and `Doc.__required_keys__`") == []


def test_a_call_to_a_name_defined_nowhere_is_reported() -> None:
    # ADR-0006's `watch_once()`: a facade the design named and the code never grew.
    assert _kinds("Call `watch_once()` each tick.") == ["unknown-call"]
    assert _kinds("Call `run_eval(...)` once.") == []


def test_builtins_and_module_qualified_externals_are_not_reported() -> None:
    assert _kinds("`sorted(x)`, `time.monotonic()`, `hashlib.sha256(b)`, `uuid5(ns, s)`") == []
    # A module prefix alone is what excuses `shutil.copyfileobj` -- the name is
    # neither a builtin nor on the EXTERNAL_CALLS list.
    assert _kinds("`shutil.copyfileobj(src, dst)`") == []
    assert _kinds("`copyfileobj(src, dst)`") == ["unknown-call"]


def test_a_retired_bare_name_is_reported_only_when_it_is_really_gone() -> None:
    # `write_scores`: once def-ed under src/, now not. A name that was retired
    # and later re-defined (here `run_eval`) is live and must not be reported.
    retired = {"write_scores", "run_eval"}
    assert _kinds("scores go through `write_scores`", retired) == ["retired-name"]
    assert _kinds("scores go through `run_eval`", retired) == []
    # Without git history the check does not run -- it does not guess.
    assert _kinds("scores go through `write_scores`", None) == []


def test_history_is_not_contract() -> None:
    retired = {"write_scores"}
    assert _kinds("`write_scores` left the Protocol (ADR-0005 #9)", retired) == []
    assert _kinds("~~`write_scores` is the seam~~ -- now `record_run`", retired) == []
    assert _kinds("| T-1 | `write_scores` | <!-- doc-lint: history -->", retired) == []
    region = "\n".join(
        [
            "<!-- doc-lint: history -->",
            "- [ ] `write_scores` with a deterministic id",
            "<!-- doc-lint: end -->",
            "- [ ] `write_scores` is still the seam",
        ]
    )
    found = dl.scan_prose("docs/x.md", region, _surface(), retired)
    assert [(f.line, f.kind) for f in found] == [(4, "retired-name")]


def test_a_superseded_adr_is_skipped_whole() -> None:
    text = "# ADR-0001\n\n**Status:** Superseded by ADR-0005\n\n`PlatformAdapter.flush()`"
    assert dl.scan_prose("docs/adr/0001.md", text, _surface(), None) == []


# --- register consistency -----------------------------------------------------

HEADER = (
    "| ID | What | Where | Type | Impact | Effort | Interest | Status | Origin |\n"
    "|----|------|-------|------|--------|--------|----------|--------|--------|\n"
)


def _row(row_id: str, status: str, origin: str = "audit") -> str:
    return f"| {row_id} | w | f | t | i | 1 | — | {status} | {origin} |\n"


def _register(*rows: str) -> list[tuple[str, str]]:
    found = dl.scan_register("DEBT.md", HEADER + "".join(rows))
    return [(f.kind, re.findall(r"DEBT-\d+", f.detail)[0]) for f in found]


def test_a_struck_row_whose_status_still_reads_open_is_reported() -> None:
    assert _register(_row("~~DEBT-73~~", "🔶 **open — NEW, filed")) == [
        ("struck-but-open", "DEBT-73")
    ]
    assert _register(_row("~~DEBT-80~~", "✅ **CLOSED 2026-09-24")) == []


def test_a_closed_status_on_an_unstruck_row_is_reported() -> None:
    assert _register(_row("DEBT-70", "✅ **CLOSED 2026-09-22")) == [
        ("closed-not-struck", "DEBT-70")
    ]
    assert _register(_row("DEBT-02", "closed 2026-09-18 (loop)")) == [
        ("closed-not-struck", "DEBT-02")
    ]
    assert _register(_row("DEBT-29", "🔶 **still open")) == []


def test_closure_evidence_in_the_origin_cell_while_status_reads_open_is_reported() -> None:
    # DEBT-58/60/45/46/55/56/61: closure text in the wrong column.
    found = _register(_row("DEBT-58", "open", "Atchim — ✅ CLOSED in abc123"))
    assert found == [("closure-in-origin", "DEBT-58")]


def test_a_duplicate_id_is_reported() -> None:
    found = _register(_row("DEBT-45", "open"), _row("DEBT-45", "open"))
    assert found == [("duplicate-id", "DEBT-45")]


def test_a_register_without_the_main_table_says_so_rather_than_passing() -> None:
    assert [f.kind for f in dl.scan_register("DEBT.md", "# nothing here\n")] == ["register-shape"]


# --- the real repo --------------------------------------------------------------


def test_the_lint_runs_against_the_repo_and_is_non_blocking_by_default() -> None:
    findings, notes = dl.run(REPO_ROOT)
    assert all(isinstance(f, dl.Finding) for f in findings)
    assert all(isinstance(n, str) for n in notes)
    assert dl.main(["--repo", str(REPO_ROOT)]) == 0
