"""User-authored scorers, as data.

The load-bearing test in this file is `TestMonotonicity`. A spec is
authored in a browser, which is further from review than Python is, so
the compiler's guarantee has to be stronger than the one
`scoring.py`'s `ScoreResult` gives a hand-written scorer: a spec may
tighten a verdict and may set `critical`, and it may do neither of the
opposites. That is what lets an operator run a spec they did not audit
-- the worst it can do is fail a build that would otherwise pass, never
pass a build that should have failed. `CLAUDE.md ## Rigor` names the
silently-wrong GREEN build as this system's worst failure; these tests
are what keep a web form from introducing one.
"""

from __future__ import annotations

from typing import cast

import pytest

from idp_regression.classifier import custom as cs
from idp_regression.classifier.registry import CLASSIFIERS
from idp_regression.classifier.scoring import ScoreContext, ScoreResult
from idp_regression.classifier.types import Golden, NormalizedOutput, VerdictMap

#: The commonest action in these tables, named so a parametrize row
#: fits on one line.
CRITICAL = {"critical": True}


def spec(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "confidence-floor",
        "description": "Fail a match IDP was not confident about.",
        "base": "regression",
        "rules": [
            {"when": {"confidence_below": 0.8, "verdict_is": ["match"]},
             "then": {"verdict": "wrong_value", "critical": True}},
        ],
    }
    base.update(overrides)
    return base


def ctx(**overrides: object) -> ScoreContext:
    fields: dict[str, object] = {
        "name": "total",
        "kind": "field",
        "field_type": "number",
        "expected": "10.00",
        "actual": "10.00",
        "confidence": 0.99,
    }
    fields.update(overrides)
    return ScoreContext(**fields)  # type: ignore[arg-type]


class TestParsing:
    def test_a_valid_spec_round_trips(self) -> None:
        parsed = cs.parse_spec(spec())
        assert parsed.name == "confidence-floor"
        assert cs.parse_spec(parsed.to_json()) == parsed

    @pytest.mark.parametrize(
        "bad,fragment",
        [
            ({"name": "Not Kebab"}, "kebab-case"),
            ({"name": "ab"}, "kebab-case"),
            ({"base": "made-up"}, "base must be one of"),
            ({"rules": []}, "non-empty list"),
            ({"rules": [{"when": {}, "then": CRITICAL}]}, "is empty"),
            ({"rules": [{"when": {"nope": 1}, "then": CRITICAL}]},
             "is not a condition"),
            ({"rules": [{"when": {"kind": "x"}, "then": CRITICAL}]},
             "kind must be one of"),
            ({"rules": [{"when": {"confidence_below": 2}, "then": CRITICAL}]},
             "in [0, 1]"),
            ({"rules": [{"when": {"verdict_is": ["nope"]}, "then": CRITICAL}]},
             "verdict_is"),
            ({"rules": [{"when": {"name_matches": "([a-z"}, "then": CRITICAL}]},
             "not a valid regex"),
            ({"rules": [{"when": {"kind": "field"}, "then": {"nope": 1}}]}, "is not an action"),
        ],
    )
    def test_the_closed_vocabulary_is_enforced_with_the_offending_key_named(
        self, bad: dict[str, object], fragment: str
    ) -> None:
        with pytest.raises(cs.SpecError, match=fragment.replace("[", r"\[").replace("]", r"\]")):
            cs.parse_spec(spec(**bad))

    def test_an_unknown_top_level_key_is_refused_rather_than_ignored(self) -> None:
        # A silently-ignored key is a gate that is not what its author
        # believes they configured.
        with pytest.raises(cs.SpecError, match="unknown key"):
            cs.parse_spec(spec(gate="always-pass"))

    def test_a_spec_may_not_shadow_a_shipped_classifier(self) -> None:
        # Two runs naming `regression` must be comparing the same way.
        with pytest.raises(cs.SpecError, match="already a shipped classifier"):
            cs.parse_spec(spec(name="regression"))

    def test_a_pathological_regex_is_capped_by_length(self) -> None:
        pattern = "(a+)+" * 60
        assert len(pattern) > cs.MAX_PATTERN_LENGTH
        with pytest.raises(cs.SpecError, match="longer than"):
            cs.parse_spec(
                spec(rules=[{"when": {"name_matches": pattern}, "then": CRITICAL}])
            )


class TestMonotonicity:
    """The guarantee that makes an unaudited spec safe to run."""

    def test_a_spec_cannot_relax_a_verdict_to_match(self) -> None:
        with pytest.raises(cs.SpecError, match="a verdict the gate fails on"):
            cs.parse_spec(
                spec(rules=[{"when": {"name_in": ["total"]}, "then": {"verdict": "match"}}])
            )

    def test_match_is_absent_from_the_actionable_verdicts_by_construction(self) -> None:
        assert "match" in cs.VERDICTS
        assert "match" not in cs.ACTIONABLE_VERDICTS

    @pytest.mark.parametrize("flag", ["critical", "format_critical"])
    def test_a_spec_cannot_clear_an_escalation_flag(self, flag: str) -> None:
        with pytest.raises(cs.SpecError, match="may only be set to true"):
            cs.parse_spec(spec(rules=[{"when": {"name_in": ["total"]}, "then": {flag: False}}]))

    def test_the_goldens_critical_survives_a_rule_that_says_nothing_about_it(self) -> None:
        """Asserted through the full classifier, which is where the OR
        lives: the scorer returns only its own escalation, and
        `_classify_field` ORs the golden's `critical` onto it. A spec
        that never mentions `critical` must leave a gated field gated.
        """
        classifier = cs.build_classifier(cs.parse_spec(spec(
            rules=[{"when": {"verdict_is": ["wrong_value"]}, "then": {"format_critical": True}}]
        )))
        verdicts = classifier.classify(
            cast(
                Golden,
                {"fields": {"total": {"value": "10.00", "type": "number", "critical": True}}},
            ),
            cast(
                NormalizedOutput,
                {"status": "SUCCEEDED",
                 "fields": {"total": {"value": "99.00", "confidence": 0.9}}},
            ),
        )
        assert verdicts["total"]["verdict"] == "wrong_value"
        assert verdicts["total"]["critical"] is True
        assert classifier.gate(verdicts) == "FAIL"

    def test_a_spec_can_gate_a_field_the_golden_left_ungated(self) -> None:
        """The other direction of the same rule, and the reason to write
        a spec at all."""
        classifier = cs.build_classifier(cs.parse_spec(spec()))
        verdicts = classifier.classify(
            cast(
                Golden,
                {"fields": {"total": {"value": "10.00", "type": "number", "critical": False}}},
            ),
            cast(
                NormalizedOutput,
                {"status": "SUCCEEDED",
                 "fields": {"total": {"value": "10.00", "confidence": 0.4}}},
            ),
        )
        assert verdicts["total"]["verdict"] == "wrong_value"
        assert verdicts["total"]["critical"] is True
        assert classifier.gate(verdicts) == "FAIL"

    def test_every_shipped_base_survives_verify_monotone(self) -> None:
        for base in cs.BASE_CLASSIFIERS:
            parsed = cs.parse_spec(spec(name=f"probe-{base}", base=base))
            assert cs.verify_monotone(parsed) == []

    def test_verify_monotone_catches_a_relaxation_the_key_check_would_miss(self) -> None:
        """Belt and braces, deliberately bypassing `parse_spec`.

        `_validate_action` is per-key; this is per-outcome. A future
        condition or action that opened a relaxing path has to fail
        somewhere, and it should fail here rather than in a green build.
        """
        sneaky = cs.ScorerSpec(
            name="sneaky",
            description="",
            base="regression",
            rules=(cs.Rule(when={"kind": "field"}, then={"verdict": "match"}),),
        )
        problems = cs.verify_monotone(sneaky)
        assert problems, "a spec forcing `match` must be reported as non-monotone"
        # Reported both ways: the old `match` check AND the per-outcome
        # gate check (Wave C F-1) each name it.
        assert any("relaxes" in p for p in problems)
        assert any("turns a gate FAILURE" in p for p in problems)


class TestCompilation:
    def test_the_base_scorer_runs_and_a_non_matching_rule_changes_nothing(self) -> None:
        scorer = cs.compile_spec(cs.parse_spec(spec()))
        assert scorer(ctx(confidence=0.99)) == "match"

    def test_a_matching_rule_tightens_the_verdict_and_gates_the_field(self) -> None:
        scorer = cs.compile_spec(cs.parse_spec(spec()))
        result = scorer(ctx(confidence=0.4))
        assert result == ScoreResult("wrong_value", critical=True, format_critical=False)

    def test_a_missing_confidence_is_not_below_the_floor(self) -> None:
        """An absence of evidence is not evidence of a low score.

        Failing on `confidence is None` would fail every field of a
        confidence-free extraction at once -- a broken gate, not a strict
        one. `confidence_missing` exists for that case and has to be
        asked for.
        """
        scorer = cs.compile_spec(cs.parse_spec(spec()))
        assert scorer(ctx(confidence=None)) == "match"

    def test_confidence_missing_is_its_own_condition(self) -> None:
        scorer = cs.compile_spec(cs.parse_spec(spec(
            rules=[{"when": {"confidence_missing": True}, "then": {"critical": True}}]
        )))
        assert isinstance(scorer(ctx(confidence=None)), ScoreResult)
        assert scorer(ctx(confidence=0.5)) == "match"

    def test_first_matching_rule_wins(self) -> None:
        scorer = cs.compile_spec(cs.parse_spec(spec(rules=[
            {"when": {"field_type": "number"}, "then": {"verdict": "wrong_value"}},
            {"when": {"field_type": "number"}, "then": {"verdict": "missing"}},
        ])))
        assert scorer(ctx()) == "wrong_value"

    def test_a_compiled_scorer_only_ever_returns_the_six_verdicts(self) -> None:
        """The same contract `tests/classifier/test_registry.py` holds a
        shipped classifier to (INV-03)."""
        scorer = cs.compile_spec(cs.parse_spec(spec()))
        for context in cs._enumerate_contexts():
            result = scorer(context)
            verdict = result.verdict if isinstance(result, ScoreResult) else result
            assert verdict in cs.VERDICTS

    def test_build_classifier_produces_a_registry_row_of_the_shipped_shape(self) -> None:
        classifier = cs.build_classifier(cs.parse_spec(spec()))
        assert classifier.name == "confidence-floor"
        passing = cast(VerdictMap, {"total": {"verdict": "match", "critical": True}})
        failing = cast(VerdictMap, {"total": {"verdict": "missing", "critical": True}})
        assert classifier.gate(passing) == "PASS"
        assert classifier.gate(failing) == "FAIL"

    def test_the_scorer_stays_pure_over_repeated_calls(self) -> None:
        scorer = cs.compile_spec(cs.parse_spec(spec()))
        context = ctx(confidence=0.4)
        assert scorer(context) == scorer(context) == scorer(context)


# --- Wave C S-01.1 re-stamp F-1: a spec may never turn a FAIL into a PASS ----
#
# The old guarantee forbade only relaxing to `match`. But `overall_gate` fails
# a field only on critical `missing`/`wrong_value` (or `wrong_format` with
# `format_critical`), so rewriting a real `wrong_value` into `new_field`,
# `new_line`, `new_table` or `wrong_format` turned a failing gate GREEN, and
# `verify_monotone` approved it.

_GOLDEN = cast(Golden, {"fields": {"total": {"value": "100.00", "type": "number",
                                             "critical": True}}})
_WRONG = cast(NormalizedOutput, {"status": "SUCCEEDED",
                                 "fields": {"total": {"value": "999.00", "confidence": 0.9}}})


@pytest.mark.parametrize("target", ["new_field", "new_line", "new_table", "wrong_format"])
def test_a_rule_can_not_rewrite_a_failure_into_an_informational_verdict(target: str) -> None:
    payload = {"name": "relax", "base": "regression",
               "rules": [{"when": {"verdict_is": ["wrong_value", "missing"]},
                          "then": {"verdict": target}}]}
    with pytest.raises(cs.SpecError):
        cs.parse_spec(payload)


def test_only_gate_failing_verdicts_are_actionable() -> None:
    assert set(cs.ACTIONABLE_VERDICTS) == {"missing", "wrong_value"}


@pytest.mark.parametrize("target", ["new_field", "new_line", "wrong_format"])
def test_verify_monotone_compares_gate_outcomes_not_just_match(target: str) -> None:
    """Belt and braces: even a spec that got past parsing (built directly)
    is caught by the per-OUTCOME check."""
    spec = cs.ScorerSpec(
        name="relax", description="", base="regression",
        rules=(cs.Rule(when={"verdict_is": ["wrong_value"]}, then={"verdict": target}),),
    )
    assert cs.verify_monotone(spec), "a spec that turns a FAIL into a PASS must be named"


def test_the_shipped_example_still_only_tightens() -> None:
    """The documented confidence-floor spec stays valid and monotone."""
    parsed = cs.parse_spec(spec())
    assert cs.verify_monotone(parsed) == []
    classifier = cs.build_classifier(parsed)
    assert classifier.gate(classifier.classify(_GOLDEN, _WRONG)) == "FAIL"


def test_verdict_is_refuses_new_table_a_scorer_never_sees() -> None:
    """F-3: `new_table` is a table-EXISTENCE verdict, never a scorer's base
    verdict, so a condition on it was dead."""
    with pytest.raises(cs.SpecError):
        cs.parse_spec({"name": "dead", "base": "regression",
                       "rules": [{"when": {"verdict_is": ["new_table"]},
                                  "then": {"critical": True}}]})


def test_verify_monotone_catches_relaxing_a_format_critical_field() -> None:
    """A `wrong_format` on a `format_critical` field fails the gate. A spec
    rewriting it into `new_field` must be named -- and only the per-outcome
    check can see that, since `wrong_format` was never `match`. The context
    enumerator must therefore produce a format-only difference at all."""
    spec = cs.ScorerSpec(
        name="relax-format", description="", base="regression",
        rules=(cs.Rule(when={"verdict_is": ["wrong_format"]}, then={"verdict": "new_field"}),),
    )
    assert any("turns a gate FAILURE ('wrong_format')" in p for p in cs.verify_monotone(spec))


# --- Wave C S-01.1 re-stamp #2 G-1: escalation-only against the GATE's predicate ---
#
# `wrong_value` fails only when `critical`; `wrong_format` fails when
# `format_critical`. On a legal golden with `critical: false,
# format_critical: true`, "treat a format difference as a value difference"
# rewrote a failing `wrong_format` into a passing `wrong_value` -- through
# `parse_spec`, with `verify_monotone` silent, because the enumerator had no
# date/text format pair and only ever used the name "total".

_FORMAT_ONLY_CASES = [
    ("invoice_date", "date", "2026-01-22", "22/01/2026", {"field_type": "date"}),
    ("vendor", "text", "Acme Corp", "Acme  Corp", {"field_type": "text"}),
    ("amount", "number", "1250.00", "1.250,00", {"name_in": ["amount"]}),
    ("amount", "number", "1250.00", "1.250,00", {"name_matches": "^amo"}),
]


def _format_critical_case(
    name: str, field_type: str, expected: str, actual: str
) -> tuple[Golden, NormalizedOutput]:
    golden = cast(Golden, {"fields": {name: {"value": expected, "type": field_type,
                                             "critical": False, "format_critical": True}}})
    output = cast(NormalizedOutput, {"status": "SUCCEEDED",
                                     "fields": {name: {"value": actual, "confidence": 0.9}}})
    return golden, output


@pytest.mark.parametrize(("name", "field_type", "expected", "actual", "when"), _FORMAT_ONLY_CASES)
def test_rewriting_a_format_critical_wrong_format_into_wrong_value_still_fails(
    name: str, field_type: str, expected: str, actual: str, when: dict[str, object]
) -> None:
    golden, output = _format_critical_case(name, field_type, expected, actual)
    base = CLASSIFIERS["regression"]
    assert base.gate(base.classify(golden, output)) == "FAIL", "precondition: base gate fails"
    parsed = cs.parse_spec({"name": "format-is-value", "base": "regression",
                            "rules": [{"when": {**when, "verdict_is": ["wrong_format"]},
                                       "then": {"verdict": "wrong_value"}}]})
    custom = cs.build_classifier(parsed)
    assert custom.gate(custom.classify(golden, output)) == "FAIL"
    if "name_matches" not in when:  # a regex cannot be enumerated; enforcement covers it
        assert cs.verify_monotone(parsed), "the spec as written relaxes the gate and must be named"


@pytest.mark.parametrize("target", ["wrong_value", "missing"])
def test_compile_spec_is_monotone_by_construction_on_every_enumerated_context(target: str) -> None:
    """Built directly, bypassing `parse_spec`: whatever the rule rewrites a
    failing verdict into, the compiled result still fails the gate."""
    spec_ = cs.ScorerSpec(
        name="rewrite", description="", base="regression",
        rules=(cs.Rule(when={}, then={"verdict": target}),),
    )
    scorer = cs.compile_spec(spec_)
    base = CLASSIFIERS["regression"].scorer
    for context in cs._enumerate_contexts():
        if cs._fails_the_gate(context, base(context)):
            assert cs._fails_the_gate(context, scorer(context)), context


def test_the_enumerator_holds_a_format_only_difference_for_every_field_type() -> None:
    base = CLASSIFIERS["regression"].scorer
    types_with_one = {c.field_type for c in cs._enumerate_contexts()
                      if base(c) == "wrong_format"}
    assert types_with_one == set(cs.FIELD_TYPES)


def test_the_enumerator_reaches_names_a_spec_scopes_itself_to() -> None:
    parsed = cs.parse_spec({"name": "scoped", "base": "regression",
                            "rules": [{"when": {"name_in": ["amount"]},
                                       "then": {"critical": True}}]})
    assert "amount" in {c.name for c in cs._enumerate_contexts(parsed)}


@pytest.mark.parametrize("target", ["new_field", "new_line", "wrong_format"])
def test_a_directly_built_relaxing_spec_cannot_pass_a_failing_gate(target: str) -> None:
    """`verify_monotone` names it; `compile_spec` neutralizes it anyway."""
    spec_ = cs.ScorerSpec(
        name="relax", description="", base="regression",
        rules=(cs.Rule(when={"verdict_is": ["wrong_value"]}, then={"verdict": target}),),
    )
    custom = cs.build_classifier(spec_)
    assert custom.gate(custom.classify(_GOLDEN, _WRONG)) == "FAIL"


# --- Wave C S-01.1 re-stamp #3 H-1: `_fails_the_gate` must BE the gate ------
#
# The row gate reads only `missing`/`wrong_value` on a critical block; a row
# carries no `format_critical`. `_fails_the_gate` counted a table cell's
# `wrong_format` + `format_critical` as failing, so a directly built spec
# rewriting a critical cell's `wrong_value` into that turned the gate GREEN
# while the proof -- and the construction test, which used `_fails_the_gate`
# as its own oracle -- saw nothing. The oracle below is `overall_gate`.

_ALL_RESULTS: list[object] = [
    *cs.SCORER_VERDICTS,
    *(ScoreResult(v, critical=c, format_critical=f)  # type: ignore[arg-type]
      for v in cs.SCORER_VERDICTS for c in (False, True) for f in (False, True)),
]


def _gate_of_one_leaf(context: ScoreContext, result: object) -> bool:
    """What `overall_gate` says about a map holding just this leaf, built the
    way `gate.py` builds it: flags OR-ed, a cell's escalation on its block."""
    from idp_regression.classifier.gate import overall_gate

    verdict = result.verdict if isinstance(result, ScoreResult) else result
    critical = context.critical or (isinstance(result, ScoreResult) and result.critical)
    fmt = context.format_critical or (isinstance(result, ScoreResult) and result.format_critical)
    if context.kind == "table_column":
        leaf: object = {"verdict": "detail", "critical": critical, "type": None,
                        "rows": [{"match_key": "A", "column": context.name, "verdict": verdict,
                                  "expected": None, "actual": None, "confidence": None}]}
    else:
        leaf = {"verdict": verdict, "expected": None, "actual": None, "confidence": None,
                "critical": critical, "format_critical": fmt, "type": None}
    return overall_gate(cast(VerdictMap, {context.name: leaf})) == "FAIL"


def test_fails_the_gate_agrees_with_overall_gate_on_every_leaf() -> None:
    disagreements = [
        (c.kind, c.critical, c.format_critical, r)
        for c in cs._enumerate_contexts()
        for r in _ALL_RESULTS
        if not (c.kind == "table_column"
                and (r.verdict if isinstance(r, ScoreResult) else r) == "new_table")
        and cs._fails_the_gate(c, r) != _gate_of_one_leaf(c, r)
    ]
    assert disagreements == []


#: `fields` must be non-empty; one non-critical field that matches.
_ONE_FIELD = {"total": {"value": "1", "type": "number", "critical": False}}
_ONE_ACTUAL = {"total": {"value": "1", "confidence": 0.99}}


def _critical_table() -> tuple[Golden, NormalizedOutput]:
    golden = cast(Golden, {"fields": _ONE_FIELD, "tables": {"line_items": {
        "match_key": "sku", "critical": True,
        "rows": [{"sku": "A", "amount": "100"}]}}})
    actual = cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": _ONE_ACTUAL, "tables": {
        "line_items": [{"sku": {"value": "A"}, "amount": {"value": "999"}}]}})
    return golden, actual


@pytest.mark.parametrize("when", [{}, {"kind": "table_column"}])
def test_a_directly_built_spec_cannot_pass_a_critical_table_cell(when: dict[str, object]) -> None:
    golden, actual = _critical_table()
    base = CLASSIFIERS["regression"]
    assert base.gate(base.classify(golden, actual)) == "FAIL"
    spec_ = cs.ScorerSpec(
        name="relax-cell", description="", base="regression",
        rules=(cs.Rule(when=when, then={"verdict": "wrong_format", "format_critical": True}),),
    )
    custom = cs.build_classifier(spec_)
    assert custom.gate(custom.classify(golden, actual)) == "FAIL"
    assert cs.verify_monotone(spec_), "the spec as written relaxes a table cell"


def test_verify_monotone_accepts_a_legitimate_format_tightening() -> None:
    """Re-stamp #3 L-1: the proof must not refuse a correct spec -- treating a
    format difference as a gated value difference only tightens."""
    parsed = cs.parse_spec({"name": "format-is-a-failure", "base": "regression",
                            "rules": [{"when": {"verdict_is": ["wrong_format"]},
                                       "then": {"verdict": "wrong_value", "critical": True}}]})
    assert cs.verify_monotone(parsed) == []


# --- Re-stamp #3 M-1: prompts get the run's scorer, not the default ---------

def _prompt_case(answer: str) -> tuple[Golden, NormalizedOutput]:
    golden = cast(Golden, {"fields": _ONE_FIELD, "prompts": {"vendor_name": {"answer": answer,
                                                                    "critical": True}}})
    actual = cast(NormalizedOutput, {"status": "SUCCEEDED", "fields": _ONE_ACTUAL, "prompts": {
        "vendor_name": {"answer": answer, "confidence": 0.1}}})
    return golden, actual


def test_pinned_file_reads_an_empty_prompt_against_an_empty_answer_as_agreement() -> None:
    golden, actual = _prompt_case("")
    pinned = CLASSIFIERS["pinned-file"]
    verdicts = pinned.classify(golden, actual)
    assert verdicts["vendor_name"]["verdict"] == "match"
    assert pinned.gate(verdicts) == "PASS"


def test_a_custom_rule_scoped_to_prompts_is_applied_to_prompts() -> None:
    golden, actual = _prompt_case("Acme Corp")
    parsed = cs.parse_spec({"name": "prompt-floor", "base": "regression",
                            "rules": [{"when": {"kind": "prompt", "confidence_below": 0.5,
                                                "verdict_is": ["match"]},
                                       "then": {"verdict": "wrong_value", "critical": True}}]})
    custom = cs.build_classifier(parsed)
    assert custom.gate(custom.classify(golden, actual)) == "FAIL"


# --- Re-stamp #2 G-2 / #3: the runtime row-vocabulary guard ------------------

def test_a_scorer_returning_new_table_for_a_cell_is_refused() -> None:
    from idp_regression.classifier.gate import make_classifier
    from idp_regression.classifier.types import MalformedActualError

    golden, actual = _critical_table()
    classify = make_classifier(lambda ctx: "new_table" if ctx.kind == "table_column" else "match")
    with pytest.raises(MalformedActualError, match="not valid for a table row"):
        classify(golden, actual)
