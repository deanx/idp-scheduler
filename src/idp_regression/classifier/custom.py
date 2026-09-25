"""User-authored scorers, as DATA rather than code (Epic E).

`scoring.py` is the authoring API for a scorer written in Python by an
engineer and reviewed like any other code. This module is the other
half: a scorer a **Prompt Engineer authors in the browser**, expressed as
a closed-vocabulary JSON spec that compiles to exactly the same pure
`Scorer` the registry already takes.

Why not "upload a Python file"
------------------------------
`registry.py` states the rule this module obeys: *registered NAMES only,
never an importable path from a flag -- a `--classifier` that could load
arbitrary code would turn a CLI argument into a code-execution surface on
a runner that holds IDP and platform credentials.* A web form that posts
Python is that same surface with a nicer font and, unlike the CLI, it is
reachable by anyone who reaches the port. So a UI-authored scorer is a
**spec**: a list of `when -> then` rules over a fixed set of conditions
and actions. There is no `eval`, no `import`, no path, and nothing in a
spec can name a Python object.

Monotone by construction -- the rule that makes this safe
---------------------------------------------------------
`ScoreResult`'s flags are escalation-only because a scorer that could
clear `critical` "would turn a real regression into a green build -- the
one failure `CLAUDE.md ## Rigor` calls this system's worst". A spec
authored in a browser is further from review than Python is, so it is
held to the STRONGER form of the same rule:

* an action may set `critical`/`format_critical` only to **true**;
* an action may **not** produce `match`. A spec can turn a match into a
  failure; it can never turn a failure into a match.

Together those make every spec a pure tightening of its base classifier.
That is a real restriction -- a "this mismatch is acceptable" rule is not
expressible here, deliberately -- and it is what lets an operator run a
stranger's spec without auditing it: the worst a spec can do is fail a
build that would otherwise have passed.

Why this module is PURE, and where the files live
--------------------------------------------------
`classifier/` has no I/O (INV-02) and the classifier suite is the CI
gate, so this module only ever turns a dict into a `Scorer`. Reading and
writing spec FILES, and registering them, is
`orchestration/scorer_store.py` -- one package up, where I/O belongs.
That split is also what lets `orchestration/cli.py` register a spec
without the CLI importing the UI: the dependency direction stays
classifier <- orchestration <- ui, never back.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final, NamedTuple, get_args

from idp_regression.classifier.gate import make_classifier, overall_gate
from idp_regression.classifier.registry import CLASSIFIERS, Classifier
from idp_regression.classifier.scoring import (
    ScoreContext,
    Scorer,
    ScoreResult,
    VerdictLiteral,
)

#: The six verdicts (INV-03). A spec may not invent a seventh -- the
#: score names, the remediation UI and the platform all know these and
#: only these.
VERDICTS: Final[tuple[str, ...]] = get_args(VerdictLiteral)

#: What an action may produce. `match` is absent BY DESIGN: see the
#: module docstring. This tuple is the monotonicity guarantee's teeth.
ACTIONABLE_VERDICTS: Final[tuple[str, ...]] = tuple(v for v in VERDICTS if v != "match")

#: A spec may only build on a classifier that ships with the tool, so the
#: base of every custom gate is code that went through review.
BASE_CLASSIFIERS: Final[tuple[str, ...]] = ("regression", "pinned-file")

#: Closed vocabularies. Anything not named here is a validation error,
#: never a silently-ignored key -- a typo'd condition that matched
#: nothing would produce a gate quietly weaker than the author believes
#: it configured.
CONDITION_KEYS: Final[tuple[str, ...]] = (
    "kind",
    "field_type",
    "name_in",
    "name_matches",
    "verdict_is",
    "confidence_below",
    "confidence_missing",
    "expected_empty",
    "actual_empty",
    "critical_in_golden",
)
ACTION_KEYS: Final[tuple[str, ...]] = ("verdict", "critical", "format_critical")

KINDS: Final[tuple[str, ...]] = ("field", "prompt", "table_column")
FIELD_TYPES: Final[tuple[str, ...]] = ("number", "date", "id", "text")

#: A regex in a spec is compiled and then run against every field name of
#: every document. Capped so a pathological pattern cannot be pasted in
#: without someone noticing the length; the server is localhost-bound,
#: which is the other half of that mitigation.
MAX_PATTERN_LENGTH: Final = 200
MAX_RULES: Final = 50
NAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")


class SpecError(ValueError):
    """A spec that cannot be compiled, with a message naming the exact
    key. Raised at authoring time and again at load time -- never at
    classify time, where a raise would abort a paid-for run."""


class Rule(NamedTuple):
    """One `when -> then`. Conditions are ANDed; the first matching rule
    wins and no later rule is consulted, so order is meaningful and the
    UI shows it."""

    when: dict[str, Any]
    then: dict[str, Any]


class ScorerSpec(NamedTuple):
    name: str
    description: str
    base: str
    rules: tuple[Rule, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "base": self.base,
            "rules": [{"when": dict(r.when), "then": dict(r.then)} for r in self.rules],
        }

    def digest(self) -> str:
        """A short content hash of the rules this spec compares with.

        Run identity, the same job `golden_version` does for the golden
        set (INV-04). A spec file can be edited between two runs that both
        name `--classifier my-rule`, and without this nothing would say
        the two runs compared differently. `description` is excluded --
        it is prose and changing it must not look like a comparison
        change; `base` and `rules` are exactly what decides a verdict.
        """
        payload = json.dumps(
            {
                "base": self.base,
                "rules": [{"when": dict(r.when), "then": dict(r.then)} for r in self.rules],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecError(message)


def _validate_condition(index: int, when: dict[str, Any]) -> None:
    where = f"rules[{index}].when"
    _require(isinstance(when, dict), f"{where} is not an object")
    _require(
        bool(when),
        f"{where} is empty -- a rule matching everything is a base change, not a rule",
    )
    for key, value in when.items():
        _require(
            key in CONDITION_KEYS,
            f"{where}.{key} is not a condition ({', '.join(CONDITION_KEYS)})",
        )
        if key == "kind":
            _require(value in KINDS, f"{where}.kind must be one of {KINDS}")
        elif key == "field_type":
            _require(value in FIELD_TYPES, f"{where}.field_type must be one of {FIELD_TYPES}")
        elif key == "name_in":
            _require(
                isinstance(value, list)
                and bool(value)
                and all(isinstance(v, str) for v in value),
                f"{where}.name_in must be a non-empty list of field names",
            )
        elif key == "name_matches":
            _require(isinstance(value, str), f"{where}.name_matches must be a string")
            _require(
                len(value) <= MAX_PATTERN_LENGTH,
                f"{where}.name_matches is longer than {MAX_PATTERN_LENGTH} characters",
            )
            try:
                re.compile(value)
            except re.error as exc:
                raise SpecError(f"{where}.name_matches is not a valid regex: {exc}") from None
        elif key == "verdict_is":
            _require(
                isinstance(value, list)
                and bool(value)
                and all(v in VERDICTS for v in value),
                f"{where}.verdict_is must be a non-empty list drawn from {VERDICTS}",
            )
        elif key == "confidence_below":
            _require(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and 0.0 <= float(value) <= 1.0,
                f"{where}.confidence_below must be a number in [0, 1]",
            )
        else:  # the three booleans
            _require(isinstance(value, bool), f"{where}.{key} must be true or false")


def _validate_action(index: int, then: dict[str, Any]) -> None:
    where = f"rules[{index}].then"
    _require(isinstance(then, dict), f"{where} is not an object")
    _require(bool(then), f"{where} is empty -- a rule that does nothing is a mistake, not a no-op")
    for key, value in then.items():
        _require(key in ACTION_KEYS, f"{where}.{key} is not an action ({', '.join(ACTION_KEYS)})")
        if key == "verdict":
            _require(
                value in ACTIONABLE_VERDICTS,
                f"{where}.verdict must be one of {ACTIONABLE_VERDICTS}. "
                "A spec may tighten a verdict, never relax one to `match` -- "
                "see the module docstring.",
            )
        else:
            _require(
                value is True,
                f"{where}.{key} may only be set to true. Escalation is one-way: a spec can make a "
                "field gate-failing, it can never clear the golden's `critical`.",
            )


def parse_spec(payload: object) -> ScorerSpec:
    """Validate an untrusted spec and return it, or raise `SpecError`.

    Everything that reaches a compiled scorer passes through here, so
    this is the only place the closed vocabulary needs enforcing.
    """
    if not isinstance(payload, dict):
        raise SpecError("spec is not a JSON object")
    data: dict[str, Any] = dict(payload)

    unknown = set(data) - {"name", "description", "base", "rules"}
    _require(not unknown, f"unknown key(s): {', '.join(sorted(unknown))}")

    name = data.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise SpecError(
            "name must be lowercase kebab-case, 3-50 characters "
            "(it becomes a --classifier value and a score-name component)"
        )
    _require(
        name not in CLASSIFIERS,
        f"{name!r} is already a shipped classifier -- a spec may not shadow one, "
        "because two runs naming it would not be comparing the same way",
    )

    description = data.get("description", "")
    _require(
        isinstance(description, str) and len(description) <= 500,
        "description must be a string under 500 characters",
    )

    base = data.get("base", "regression")
    _require(base in BASE_CLASSIFIERS, f"base must be one of {BASE_CLASSIFIERS}")

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise SpecError("rules must be a non-empty list")
    _require(len(raw_rules) <= MAX_RULES, f"a spec may hold at most {MAX_RULES} rules")

    rules: list[Rule] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise SpecError(f"rules[{index}] is not an object")
        extra = set(raw) - {"when", "then"}
        _require(not extra, f"rules[{index}] has unknown key(s): {', '.join(sorted(extra))}")
        when = raw.get("when")
        then = raw.get("then")
        when_map: dict[str, Any] = dict(when) if isinstance(when, dict) else {}
        then_map: dict[str, Any] = dict(then) if isinstance(then, dict) else {}
        _validate_condition(index, when_map)
        _validate_action(index, then_map)
        rules.append(Rule(when=when_map, then=then_map))

    return ScorerSpec(name=name, description=description, base=base, rules=tuple(rules))


def _matches(rule: Rule, ctx: ScoreContext, base_verdict: str) -> bool:
    for key, value in rule.when.items():
        if key == "kind" and ctx.kind != value:
            return False
        if key == "field_type" and ctx.field_type != value:
            return False
        if key == "name_in" and ctx.name not in value:
            return False
        if key == "name_matches" and re.search(value, ctx.name) is None:
            return False
        if key == "verdict_is" and base_verdict not in value:
            return False
        # A missing confidence is NOT below the floor. IDP not reporting
        # one is an absence of evidence; failing a field on it would make
        # every field of a confidence-free extraction fail at once, which
        # is a broken gate rather than a strict one. `confidence_missing`
        # is the condition for that case, and it has to be asked for.
        if key == "confidence_below" and (
            ctx.confidence is None or ctx.confidence >= float(value)
        ):
            return False
        if key == "confidence_missing" and (ctx.confidence is None) is not value:
            return False
        if key == "expected_empty" and _empty(ctx.expected) is not value:
            return False
        if key == "actual_empty" and _empty(ctx.actual) is not value:
            return False
        if key == "critical_in_golden" and ctx.critical is not value:
            return False
    return True


def _empty(value: str | None) -> bool:
    return value is None or not value.strip()


def compile_spec(spec: ScorerSpec) -> Scorer:
    """Turn a validated spec into a pure `Scorer`.

    The base classifier's scorer runs first and always: a spec adds to a
    reviewed comparison, it never replaces one. The first matching rule
    then applies, and its action can only tighten the result.
    """
    base_scorer = CLASSIFIERS[spec.base].scorer
    rules = spec.rules

    def scorer(ctx: ScoreContext) -> VerdictLiteral | ScoreResult:
        outcome = base_scorer(ctx)
        if isinstance(outcome, ScoreResult):
            verdict, critical, format_critical = outcome
        else:
            verdict, critical, format_critical = outcome, False, False

        for rule in rules:
            if not _matches(rule, ctx, verdict):
                continue
            action = rule.then
            if "verdict" in action:
                verdict = action["verdict"]
            # OR-ed, never assigned: the golden's own critical survives a
            # spec that says nothing about it.
            critical = critical or bool(action.get("critical", False))
            format_critical = format_critical or bool(action.get("format_critical", False))
            break

        if not critical and not format_critical:
            return verdict
        return ScoreResult(verdict, critical=critical, format_critical=format_critical)

    scorer.__name__ = f"custom_{spec.name.replace('-', '_')}"
    scorer.__doc__ = spec.description or f"Custom scorer {spec.name!r} (base: {spec.base})."
    return scorer


def build_classifier(spec: ScorerSpec) -> Classifier:
    """A registry row for a spec: the compiled scorer in the shared
    fan-out, with the default gate. Identical in shape to a shipped
    row, so `tests/classifier/test_registry.py`'s contract applies."""
    scorer = compile_spec(spec)
    return Classifier(
        name=spec.name,
        classify=make_classifier(scorer),
        gate=overall_gate,
        description=spec.description or f"Custom scorer (base: {spec.base}).",
        scorer=scorer,
    )


#: Every context shape a rule can distinguish, used by
#: `verify_monotone`. Small by construction: the conditions are a closed
#: set over four field types, three kinds, two confidence states and the
#: emptiness of two values.
def _enumerate_contexts() -> list[ScoreContext]:
    contexts: list[ScoreContext] = []
    for kind in KINDS:
        for field_type in FIELD_TYPES:
            for expected in (None, "", "1.00"):
                for actual in (None, "", "1.00", "2.00"):
                    for confidence in (None, 0.0, 0.5, 1.0):
                        for critical in (False, True):
                            contexts.append(
                                ScoreContext(
                                    name="total",
                                    kind=kind,  # type: ignore[arg-type]
                                    field_type=field_type,
                                    expected=expected,
                                    actual=actual,
                                    confidence=confidence,
                                    critical=critical,
                                )
                            )
    return contexts


def verify_monotone(spec: ScorerSpec) -> list[str]:
    """Prove this spec can only tighten its base, or name where it does not.

    Returns an empty list when the spec is monotone. This is belt and
    braces over `_validate_action` -- that check is per-key and this one
    is per-outcome, so a future condition or action that accidentally
    opened a relaxing path fails here rather than in a green build. It is
    cheap (a few hundred pure calls) and the API runs it on every save.
    """
    base_scorer = CLASSIFIERS[spec.base].scorer
    scorer = compile_spec(spec)
    problems: list[str] = []

    for ctx in _enumerate_contexts():
        before = base_scorer(ctx)
        base_verdict = before.verdict if isinstance(before, ScoreResult) else before
        base_critical = before.critical if isinstance(before, ScoreResult) else False

        after = scorer(ctx)
        verdict = after.verdict if isinstance(after, ScoreResult) else after
        critical = after.critical if isinstance(after, ScoreResult) else False

        if base_verdict != "match" and verdict == "match":
            problems.append(
                f"{ctx.kind}/{ctx.field_type} expected={ctx.expected!r} actual={ctx.actual!r}: "
                f"relaxes {base_verdict!r} to 'match'"
            )
        if base_critical and not critical:
            problems.append(
                f"{ctx.kind}/{ctx.field_type} expected={ctx.expected!r} actual={ctx.actual!r}: "
                "clears the base classifier's `critical`"
            )
    return sorted(set(problems))


