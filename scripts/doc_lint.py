#!/usr/bin/env python
"""Doc-lint (DEBT-29): prose that names code the repo no longer has.

Two checks, one report:

1. **Symbol drift.** Every backticked span in `docs/specs/`, `docs/adr/` and
   `docs/design/` that is *shaped like code* is resolved against the names
   actually defined in `src/`, `scripts/` and `tests/` (by AST, never by
   grep):

   - a call, ``name()`` / ``module.name(...)`` -- flagged when ``name`` is
     defined nowhere in the repo and is not a builtin or a known external
     (stdlib / SDK) callable;
   - a member, ``Class.member`` -- flagged when ``Class`` is a project class
     and ``member`` is not on it (nor on a project base class). This is the
     ``PlatformAdapter.flush`` class of drift DEBT-29 was filed for;
   - a bare identifier that git history says was once ``def``/``class``-ed
     under ``src/`` and no longer is (``write_scores``) -- a *retired* name.

   Skipped, because they are history rather than contract: an ADR whose
   ``**Status:**`` says Superseded; text inside ``~~strike~~``; a region
   between a line that is exactly ``<!-- doc-lint: history -->`` and one that
   is exactly ``<!-- doc-lint: end -->`` (for a block kept verbatim as the
   record of what was accepted); a single line carrying that marker inline
   (for a table row, where a comment line would break the table); and any
   line that itself says the name is gone (``superseded``, ``no longer``,
   ``removed``, ``renamed``, ``retired``, ``replaced``, ``formerly``,
   ``left``/``leave(s) the``, ``remove the``, ``private internal``). That
   last rule is a heuristic and it is the lint's known blind spot: a stale
   name on a line that happens to say "superseded" about something else is
   not reported. Dunder members (``__required_keys__``)
   are never checked -- they come from the runtime, not from the repo.

2. **Register consistency** (`docs/state/DEBT.md`, the main table). The
   register marks a closed row by striking its ID; the Status cell carries
   the evidence. They are compared against each other, which is the lag
   DEBT-54 recorded four times by hand:

   - ID struck, Status cell leads with an *open* marker;
   - Status cell leads with a *closed* marker, ID not struck;
   - Origin cell announces a closure while Status still leads open;
   - the same ID on two rows.

**Non-blocking by design** (DEBT-29: "wired into /qa as a non-blocking
report"): exit 0 with findings printed, unless ``--strict``. The report is
the product; `/qa` and CI read it, nothing gates on it yet.

Usage
-----
    .venv/bin/python scripts/doc_lint.py            # report, exit 0
    .venv/bin/python scripts/doc_lint.py --strict   # exit 1 on any finding
"""

from __future__ import annotations

import argparse
import ast
import builtins
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CODE_ROOTS = ("src", "scripts", "tests")
PROSE_GLOBS = ("docs/specs/*.md", "docs/adr/*.md", "docs/design/*.md")
REGISTER = "docs/state/DEBT.md"

# Callables the docs legitimately name that live outside this repo. Kept
# short on purpose: a module-qualified call (`time.monotonic()`) is already
# skipped by its prefix, so this is only for the unqualified spellings.
EXTERNAL_CALLS = frozenset(
    {
        "uuid5",
        "sha256",
        "monotonic",
        "get_type_hints",
        "get_args",
        "get_headers",
        "gather",
        "raises",
        "Langfuse",
        "run_experiment",
        "urlopen",
        "safe_load",
        "load_dotenv",
        "_ExperimentItem",  # the langfuse SDK's own private item class
    }
)

_HISTORY_WORDS = re.compile(
    r"supersed|no longer|removed|renamed|retired|replaced|formerly"
    r"|left the|leaves? the|remove the|private internal",
    re.IGNORECASE,
)
HISTORY_MARKER = "<!-- doc-lint: history -->"
HISTORY_END = "<!-- doc-lint: end -->"
_STRIKE = re.compile(r"~~.*?~~")
_BACKTICK = re.compile(r"`([^`\n]+)`")
_CALL = re.compile(r"^((?:[A-Za-z_]\w*\.)*)([A-Za-z_]\w*)\s*\(")
_MEMBER = re.compile(r"^([A-Z]\w*)\.([A-Za-z_]\w*)(?:\s*\(.*)?$")
_BARE = re.compile(r"^[A-Za-z_]\w*$")
_SUPERSEDED_STATUS = re.compile(r"^\*\*Status:\*\*\s*Superseded", re.IGNORECASE | re.MULTILINE)
_REMOVED_DEF = re.compile(r"^-\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.detail}"


@dataclass
class Surface:
    """What the repo actually defines: every name, and each class's members."""

    names: set[str] = field(default_factory=set)
    class_members: dict[str, set[str]] = field(default_factory=dict)
    class_bases: dict[str, set[str]] = field(default_factory=dict)
    modules: set[str] = field(default_factory=set)

    def members_of(self, cls: str) -> set[str] | None:
        """Members of a project class and its project bases; None if any base
        is external (an inherited member cannot be ruled out)."""
        seen: set[str] = set()
        out: set[str] = set()
        stack = [cls]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            out |= self.class_members.get(c, set())
            for base in self.class_bases.get(c, set()):
                if base in self.class_members:
                    stack.append(base)
                elif base not in {"object", "Protocol"}:
                    return None
        return out


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def collect_surface(sources: Iterable[tuple[str, str]]) -> Surface:
    """Build the surface from (module_path, source_text) pairs."""
    surface = Surface()
    for path, text in sources:
        stem = Path(path).stem
        surface.modules.add(stem)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                surface.names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                surface.names.add(node.name)
                members = surface.class_members.setdefault(node.name, set())
                surface.class_bases.setdefault(node.name, set()).update(
                    b for b in (_base_name(x) for x in node.bases) if b
                )
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.add(item.name)
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        members.add(item.target.id)
                    elif isinstance(item, ast.Assign):
                        members.update(t.id for t in item.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.Assign):
                surface.names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                surface.names.add(node.target.id)
    return surface


def retired_names_from_git(repo: Path) -> set[str] | None:
    """Names once `def`/`class`-ed under src/ per git history. None when the
    history is unavailable (not a repo, or a shallow clone) -- the caller
    reports that, rather than treating 'could not look' as 'nothing retired'."""
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],  # noqa: S607 - fixed argv, git from PATH as every dev tool here
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if shallow == "true":
            return None
        log = subprocess.run(
            ["git", "log", "-p", "--format=", "--", "src/"],  # noqa: S607 - fixed argv, no caller input
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {m.group(1) for m in map(_REMOVED_DEF.match, log.splitlines()) if m}


def scan_prose(
    path: str, text: str, surface: Surface, retired: set[str] | None = None
) -> list[Finding]:
    if _SUPERSEDED_STATUS.search("\n".join(text.splitlines()[:25])):
        return []
    builtin_names = set(dir(builtins))
    dead = (retired or set()) - surface.names
    findings: list[Finding] = []
    in_history = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if stripped == HISTORY_MARKER:
            in_history = True
            continue
        if stripped == HISTORY_END:
            in_history = False
            continue
        if in_history or HISTORY_MARKER in raw:
            continue
        line = _STRIKE.sub("", raw)
        if _HISTORY_WORDS.search(line):
            continue
        for span in _BACKTICK.findall(line):
            s = span.strip()
            member = _MEMBER.match(s)
            if member and member.group(1) in surface.class_members:
                cls, attr = member.groups()
                if attr.startswith("__") and attr.endswith("__"):
                    continue
                known = surface.members_of(cls)
                if known is not None and attr not in known:
                    findings.append(
                        Finding(
                            path,
                            lineno,
                            "no-such-member",
                            f"`{cls}.{attr}` -- {cls} has no {attr}",
                        )
                    )
                continue
            call = _CALL.match(s)
            if call:
                prefix, name = call.groups()
                root = prefix.split(".")[0] if prefix else ""
                if root and root not in surface.names and root not in surface.modules:
                    continue  # module-qualified external call: time.monotonic()
                if (
                    name not in surface.names
                    and name not in builtin_names
                    and name not in EXTERNAL_CALLS
                ):
                    findings.append(
                        Finding(
                            path,
                            lineno,
                            "unknown-call",
                            f"`{s}` -- `{name}` is defined nowhere in the repo",
                        )
                    )
                continue
            if _BARE.match(s) and s in dead:
                findings.append(
                    Finding(
                        path,
                        lineno,
                        "retired-name",
                        f"`{s}` -- was defined under src/, no longer is",
                    )
                )
    return findings


def _cells(row: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)[1:-1]]


_OPEN_LEAD = re.compile(r"^(?:🔶|\**\s*open\b|\**\s*still open)", re.IGNORECASE)
_CLOSED_LEAD = re.compile(
    r"^(?:✅|⛔|🔁|\**\s*(?:closed|resolved|design-resolved|resolved-design|wontfix|"
    r"superseded|withdrawn|already-closed)\b)",
    re.IGNORECASE,
)
_ORIGIN_CLOSURE = re.compile(r"✅\s*\**\s*closed|\bCLOSED\b")


def scan_register(path: str, text: str) -> list[Finding]:
    lines = text.splitlines()
    try:
        header = next(
            i for i, ln in enumerate(lines) if ln.startswith("| ID |") and "| Status |" in ln
        )
    except StopIteration:
        return [
            Finding(
                path, 1, "register-shape", "main table header (`| ID | … | Status | …`) not found"
            )
        ]
    columns = _cells(lines[header])
    status_at, origin_at = columns.index("Status"), columns.index("Origin")
    findings: list[Finding] = []
    first_seen: dict[str, int] = {}
    for offset, row in enumerate(lines[header + 2 :], header + 3):
        if not row.startswith("|"):
            break
        cells = _cells(row)
        if len(cells) <= max(status_at, origin_at):
            findings.append(
                Finding(path, offset, "register-shape", "row has fewer cells than the header")
            )
            continue
        id_cell = cells[0]
        struck = id_cell.startswith("~~") and id_cell.endswith("~~")
        row_id = id_cell.strip("~ ")
        status, origin = cells[status_at], cells[origin_at]
        if row_id in first_seen:
            findings.append(
                Finding(
                    path, offset, "duplicate-id", f"{row_id} also on line {first_seen[row_id]}"
                )
            )
        else:
            first_seen[row_id] = offset
        leads_open = bool(_OPEN_LEAD.match(status))
        leads_closed = bool(_CLOSED_LEAD.match(status))
        if struck and leads_open:
            findings.append(
                Finding(
                    path,
                    offset,
                    "struck-but-open",
                    f"{row_id} is struck, its Status cell leads open",
                )
            )
        elif not struck and leads_closed:
            findings.append(
                Finding(
                    path,
                    offset,
                    "closed-not-struck",
                    f"{row_id}'s Status leads closed, its ID is not struck",
                )
            )
        if leads_open and _ORIGIN_CLOSURE.search(origin):
            findings.append(
                Finding(
                    path,
                    offset,
                    "closure-in-origin",
                    f"{row_id}'s Origin cell announces a closure, Status leads open",
                )
            )
    return findings


def _read_sources(repo: Path) -> list[tuple[str, str]]:
    out = []
    for root in CODE_ROOTS:
        for p in sorted((repo / root).rglob("*.py")):
            out.append((str(p.relative_to(repo)), p.read_text(encoding="utf-8")))
    return out


def run(repo: Path) -> tuple[list[Finding], list[str]]:
    notes: list[str] = []
    surface = collect_surface(_read_sources(repo))
    retired = retired_names_from_git(repo)
    if retired is None:
        notes.append("git history unavailable (shallow clone?) -- retired-name check NOT run")
    findings: list[Finding] = []
    for pattern in PROSE_GLOBS:
        for p in sorted(repo.glob(pattern)):
            findings += scan_prose(
                str(p.relative_to(repo)), p.read_text(encoding="utf-8"), surface, retired
            )
    register = repo / REGISTER
    if register.exists():
        findings += scan_register(REGISTER, register.read_text(encoding="utf-8"))
    else:
        notes.append(f"{REGISTER} not found -- register check NOT run")
    return findings, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    findings, notes = run(args.repo)
    for f in findings:
        print(f.render())
    for n in notes:
        print(f"note: {n}")
    print(
        f"doc-lint: {len(findings)} finding(s)"
        + ("" if findings else " -- prose matches the shipped surface")
    )
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
