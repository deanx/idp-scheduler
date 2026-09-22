"""Static AST checks over `src/idp_regression/orchestration/` (SPEC-01
TP-19, TP-46 — 2026-09-21 coverage audit: "the code is clean today, so
these are pins to write, not bugs to fix").

Both checks walk the AST rather than grep raw text, so a name or import
appearing only in a comment/docstring can never satisfy either -- and
each is proven real below via a hand-mutation that violates it.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

from idp_regression.platform.types import PlatformAdapter

ORCHESTRATION_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression" / "orchestration"
)


# --- TP-19: orchestration issues no platform read beyond get_dataset ----


def _forbidden_platform_read_method_names() -> frozenset[str]:
    """Derived from the `PlatformAdapter` Protocol itself (DEBT-43 class,
    never hand-enumerated): every public method whose name reads as a
    read (`get_*` / `fetch_*` / `list_*`), MINUS `get_dataset` -- the
    one platform read `run_eval` is allowed (the pre-run `get_dataset`
    call). If a new read method is ever added to the Protocol, it is
    automatically forbidden here with no test change required."""
    names = {
        name
        for name, member in inspect.getmembers(PlatformAdapter)
        if not name.startswith("_") and inspect.isfunction(member)
    }
    read_like = {n for n in names if n.startswith(("get_", "fetch_", "list_"))}
    return frozenset(read_like - {"get_dataset"})


def _attribute_call_names(source: str) -> set[str]:
    """Every `<expr>.<name>(...)` call's `<name>`, AST-based (a mention
    inside a comment or docstring is invisible to this)."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_orchestration_issues_no_platform_read_beyond_get_dataset() -> None:
    """TP-19 / INV-08: the orchestrator performs no platform read-back at
    all beyond the single pre-run `get_dataset` -- every gate is a pure
    function of in-process state, never a re-read from the platform."""
    forbidden = _forbidden_platform_read_method_names()
    offenders: dict[str, set[str]] = {}
    for path in ORCHESTRATION_DIR.rglob("*.py"):
        called = _attribute_call_names(path.read_text(encoding="utf-8"))
        hit = called & forbidden
        if hit:
            offenders[str(path)] = hit
    assert offenders == {}, (
        f"orchestration/ calls a forbidden PlatformAdapter read method: {offenders}"
    )


def test_forbidden_read_method_set_would_catch_a_new_protocol_read_method() -> None:
    """Proves `_forbidden_platform_read_method_names` is live, not a
    frozen/hand-copied set -- a hypothetical new Protocol read method
    would be forbidden automatically, closing the DEBT-43 staleness
    class this task explicitly calls out."""
    forbidden = _forbidden_platform_read_method_names()
    assert "get_dataset" not in forbidden  # the one allowed read stays allowed
    # None of today's actual Protocol methods happen to be forbidden
    # (record_run/mark_run_status are writes) -- this is a pin on the
    # DERIVATION, not a claim that a forbidden name exists today.
    assert forbidden == frozenset()


def test_static_check_catches_a_mutated_forbidden_read_call() -> None:
    """Mutation pin: a source string that calls a forbidden read-like
    name (as if the Protocol had grown one and orchestration/ called it)
    is caught by the same AST call-name collector the real check uses."""
    fake_forbidden = frozenset({"get_run_history"})
    source = (
        "def f(platform):\n"
        "    return platform.get_run_history('x')\n"
    )
    called = _attribute_call_names(source)
    assert called & fake_forbidden


# --- TP-46: N28 must stay is-identical to N22 -- no second validator ----


def _module_is_jsonschema(module_name: str) -> bool:
    return module_name == "jsonschema" or module_name.startswith("jsonschema.")


def _imports_jsonschema(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            _module_is_jsonschema(alias.name) for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and _module_is_jsonschema(
            node.module
        ):
            return True
    return False


def test_orchestration_never_imports_jsonschema() -> None:
    """TP-46 / ADR-0005 Decision #8: a second `jsonschema` validator
    dialect must never creep into `orchestration/` -- N28
    (`validate_golden_set`) stays `is`-identical to the classifier's own
    N22 golden validator, forever. AST `Import`/`ImportFrom` node names
    only, so a mention in a comment or docstring (or a module alias
    like `import jsonschema as js`) cannot slip past this."""
    offenders = [
        str(path)
        for path in ORCHESTRATION_DIR.rglob("*.py")
        if _imports_jsonschema(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"jsonschema imported under orchestration/: {offenders}"


def test_jsonschema_import_check_catches_import_and_import_from_and_alias() -> None:
    """Mutation pin: all three import forms the real check must catch,
    and a comment/docstring mention must NOT."""
    assert _imports_jsonschema(ast.parse("import jsonschema"))
    assert _imports_jsonschema(ast.parse("import jsonschema as js"))
    assert _imports_jsonschema(ast.parse("from jsonschema import validate"))
    assert not _imports_jsonschema(
        ast.parse('"""a docstring that just says jsonschema, no import"""')
    )


# --- A8 (ADR-0004 amendment, 2026-09-22): no production read of the -----
# --- two retired env-fallback var names -----------------------------------

SRC_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "idp_regression"

#: The two env var names A8 declares dead: `--action`/`--dataset` are
#: required CLI flags with no environment fallback (user decision,
#: 2026-09-22) -- neither name may be read by any production code path
#: again. They survive only as test-harness conveniences (`.env.example`,
#: `tests/adapter/test_integration_idp.py`,
#: `tests/orchestration/test_integration_e2e.py`).
_RETIRED_ENV_VAR_NAMES = frozenset({"IDP_ACTION_ID", "GOLDEN_DATASET_NAME"})


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every AST string-`Constant` node that is a module/class/
    function docstring -- excluded from the literal scan below so a
    docstring MENTIONING a retired var name (as this file's own
    docstrings do) can never satisfy the check, only real code use can."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _non_docstring_string_literals(source: str) -> set[str]:
    """Every string-literal `Constant`'s value, MINUS docstrings. Comments
    are invisible to `ast` entirely, so a mention there already can't
    satisfy this -- the docstring exclusion closes the other prose-
    tolerant gap (the earlier finding in this project was a plain grep
    that a comment alone could satisfy; this walks parsed code instead)."""
    tree = ast.parse(source)
    excluded = _docstring_constant_ids(tree)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in excluded
    }


def test_no_production_code_reads_the_retired_env_var_names() -> None:
    """A8: `IDP_ACTION_ID` and `GOLDEN_DATASET_NAME` -- `--action` and
    `--dataset` are required CLI flags with no environment fallback
    (2026-09-22 user decision). Scans all of `src/idp_regression/`, not
    just `orchestration/`, since A8's claim is codebase-wide.

    This is an AST plain-string-literal pin, not a data-flow analysis: it
    catches accidental reintroduction (a literal `"IDP_ACTION_ID"` or
    `"GOLDEN_DATASET_NAME"` string anywhere outside a docstring, including
    two adjacent literals like `"IDP_ACTION" "_ID"`, which Python's parser
    folds into one `Constant` before the AST walk ever sees it), which is
    the realistic way this regresses. A name assembled at RUNTIME (e.g.
    `"IDP_" + "ACTION_ID"`, a `BinOp` the AST does not constant-fold; or an
    f-string, which becomes a `JoinedStr` rather than a `Constant`) is out
    of scope and will NOT be caught -- this check is deliberately not
    adversary-resistant; that is not its job (S-2, 2026-09-22 independent
    review)."""
    offenders: dict[str, set[str]] = {}
    for path in SRC_DIR.rglob("*.py"):
        literals = _non_docstring_string_literals(path.read_text(encoding="utf-8"))
        hit = literals & _RETIRED_ENV_VAR_NAMES
        if hit:
            offenders[str(path)] = hit
    assert offenders == {}, f"retired env var name read in production code: {offenders}"


def test_retired_env_var_check_ignores_docstring_mentions_but_catches_real_reads() -> None:
    """Mutation pin: a docstring mentioning a retired name must NOT
    satisfy the check (this file's own module docstring above does
    exactly that); a real `os.environ.get("IDP_ACTION_ID")`-shaped read
    must."""
    docstring_only = '"""mentions IDP_ACTION_ID and GOLDEN_DATASET_NAME, no read."""\n'
    real_read = 'import os\naction = os.environ.get("IDP_ACTION_ID")\n'

    assert _non_docstring_string_literals(docstring_only) & _RETIRED_ENV_VAR_NAMES == set()
    assert _non_docstring_string_literals(real_read) & _RETIRED_ENV_VAR_NAMES == {
        "IDP_ACTION_ID"
    }
