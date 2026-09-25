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
        with pytest.raises(cs.SpecError, match="never relax one to `match`"):
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
        assert all("relaxes" in p for p in problems)


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
            {"when": {"field_type": "number"}, "then": {"verdict": "wrong_format"}},
            {"when": {"field_type": "number"}, "then": {"verdict": "missing"}},
        ])))
        assert scorer(ctx()) == "wrong_format"

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
