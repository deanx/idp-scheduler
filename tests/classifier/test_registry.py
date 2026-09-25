"""The classifier registry (user decision, 2026-09-25): a run names a
comparison strategy, it never imports one.

Two things are tested here, and the second matters more than the first.

1. `pinned-file` differs from `regression` in exactly one rule, and that
   rule is the one the per-file scenario needs: an empty expected value
   matched by an empty actual is agreement, not a loss.
2. **Every registered classifier obeys one contract** — the pinned
   two-argument signature (CT-02), only the six verdict literals plus a
   table's `detail` container, and a gate returning only PASS/FAIL.
   These run over `CLASSIFIERS` itself, so a third classifier added
   later is held to them automatically and cannot quietly introduce a
   vocabulary the score names (INV-03) and the remediation UI do not
   know about.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from idp_regression.classifier.registry import (
    CLASSIFIERS,
    DEFAULT_CLASSIFIER,
    UnknownClassifierError,
    resolve,
)
from idp_regression.classifier.types import Golden, NormalizedOutput, VerdictLiteral

_VERDICTS = set(cast("Any", VerdictLiteral).__args__)


def _golden(value: str, *, critical: bool = True) -> Golden:
    return cast(
        "Golden",
        {"fields": {"po_number": {"value": value, "type": "text", "critical": critical}}},
    )


def _actual(value: str) -> NormalizedOutput:
    return cast(
        "NormalizedOutput",
        {"status": "SUCCEEDED", "fields": {"po_number": {"value": value, "confidence": 0.9}}},
    )


# ── the registry itself ───────────────────────────────────────────────


def test_the_default_is_the_behaviour_every_existing_run_already_has() -> None:
    from idp_regression.classifier.gate import classify, overall_gate

    default = resolve(None)

    assert default.name == DEFAULT_CLASSIFIER == "regression"
    assert default.classify is classify
    assert default.gate is overall_gate


def test_an_unknown_name_is_refused_and_names_the_alternatives() -> None:
    """Fail-closed: falling back to the default would report a gate
    computed by a comparison nobody asked for."""
    with pytest.raises(UnknownClassifierError) as excinfo:
        resolve("pinned_file")  # underscores, not the registered spelling

    assert excinfo.value.available == sorted(CLASSIFIERS)
    assert "pinned-file" in str(excinfo.value)


@pytest.mark.parametrize("name", sorted(CLASSIFIERS))
def test_every_registered_classifier_keeps_the_pinned_two_argument_contract(
    name: str,
) -> None:
    """CT-02 pins `classify(golden, actual)`. A registry entry that took
    a third argument would not be substitutable for it."""
    signature = inspect.signature(CLASSIFIERS[name].classify)

    assert list(signature.parameters) == ["golden", "actual"]
    assert CLASSIFIERS[name].name == name
    assert CLASSIFIERS[name].description


@pytest.mark.parametrize("name", sorted(CLASSIFIERS))
def test_every_registered_classifier_emits_only_the_known_vocabulary(name: str) -> None:
    classifier = CLASSIFIERS[name]
    golden = cast(
        "Golden",
        {
            "fields": {
                "total": {"value": "10.00", "type": "number", "critical": True},
                "po_number": {"value": "", "type": "text", "critical": True},
            },
            "tables": {
                "line_items": {
                    "match_key": "sku",
                    "critical": True,
                    "rows": [{"sku": "S-1", "amount": "1.00"}],
                }
            },
        },
    )
    actual = cast(
        "NormalizedOutput",
        {
            "status": "SUCCEEDED",
            "fields": {"total": {"value": "10.01"}, "surprise": {"value": "x"}},
            "tables": {"line_items": [{"sku": {"value": "S-1"}, "amount": {"value": "2.00"}}]},
        },
    )

    verdicts = classifier.classify(golden, actual)

    for entry in verdicts.values():
        if entry["verdict"] == "detail":
            for row in cast("Any", entry)["rows"]:
                assert row["verdict"] in _VERDICTS
            continue
        assert entry["verdict"] in _VERDICTS
    assert classifier.gate(verdicts) in ("PASS", "FAIL")


# ── the one rule the two disagree on ──────────────────────────────────


def test_regression_keeps_treating_an_empty_actual_as_missing() -> None:
    """The watched-Action path, unchanged: a curated golden asserts the
    field should be there, so an empty actual is a loss even when the
    golden's own value is empty."""
    regression = CLASSIFIERS["regression"]

    verdicts = regression.classify(_golden(""), _actual(""))

    assert verdicts["po_number"]["verdict"] == "missing"
    assert regression.gate(verdicts) == "FAIL"


def test_pinned_file_calls_empty_against_empty_an_agreement() -> None:
    """Neither the trusted version nor the version under test read
    anything. That is agreement, and it is what lets a pinned golden mark
    every field critical."""
    pinned = CLASSIFIERS["pinned-file"]

    verdicts = pinned.classify(_golden(""), _actual(""))

    assert verdicts["po_number"]["verdict"] == "match"
    assert pinned.gate(verdicts) == "PASS"


def test_pinned_file_still_fails_on_invented_content() -> None:
    """The gap that closes: a new model producing a value where the
    trusted version found none, on a field that can now be critical."""
    pinned = CLASSIFIERS["pinned-file"]

    verdicts = pinned.classify(_golden(""), _actual("PO-999"))

    assert verdicts["po_number"]["verdict"] == "wrong_value"
    assert pinned.gate(verdicts) == "FAIL"


def test_pinned_file_still_fails_a_genuinely_lost_value() -> None:
    """It only widens `match` where the EXPECTED is empty. A field the
    trusted version did read, gone, is still `missing` in both."""
    pinned = CLASSIFIERS["pinned-file"]

    verdicts = pinned.classify(_golden("PO-1"), _actual(""))

    assert verdicts["po_number"]["verdict"] == "missing"
    assert pinned.gate(verdicts) == "FAIL"


def test_the_two_classifiers_agree_on_everything_else() -> None:
    """One rule apart. If this ever fails, the divergence grew and the
    fork-versus-flag decision is worth revisiting."""
    golden = _golden("PO-1")
    for value in ("PO-1", "PO-2", "po 1"):
        assert (
            CLASSIFIERS["regression"].classify(golden, _actual(value))["po_number"]["verdict"]
            == CLASSIFIERS["pinned-file"].classify(golden, _actual(value))["po_number"]["verdict"]
        )


# ── a third classifier is a scorer plus one row ───────────────────────


def test_a_new_classifier_is_a_scorer_and_a_registry_row() -> None:
    """The claim the registry docstring makes, exercised: adding a
    comparison strategy means writing the small per-value function and
    wrapping it -- not restating the fan-out, the validation or the gate.

    The scorer below is also the worked example from `scorers.py`: it
    uses `confidence`, which reaches every scorer and which neither
    shipped classifier decides on today.
    """
    from idp_regression.classifier.canonical import compare_value
    from idp_regression.classifier.gate import make_classifier, overall_gate
    from idp_regression.classifier.registry import Classifier
    from idp_regression.classifier.scoring import ScoreContext, is_empty

    def low_confidence_is_not_a_match(ctx: ScoreContext) -> VerdictLiteral:
        if is_empty(ctx.actual):
            return "missing"
        if ctx.confidence is not None and ctx.confidence < 0.80:
            return "wrong_value"
        return compare_value(ctx.field_type, ctx.expected or "", ctx.actual or "")

    strict = Classifier(
        name="confidence-floor",
        classify=make_classifier(low_confidence_is_not_a_match),
        gate=overall_gate,
        description="A value IDP is unsure of is not a match.",
        scorer=low_confidence_is_not_a_match,
    )

    golden = _golden("PO-1")
    confident = cast(
        "NormalizedOutput",
        {"status": "SUCCEEDED", "fields": {"po_number": {"value": "PO-1", "confidence": 0.99}}},
    )
    unsure = cast(
        "NormalizedOutput",
        {"status": "SUCCEEDED", "fields": {"po_number": {"value": "PO-1", "confidence": 0.10}}},
    )

    # Same signature as the shipped ones, so it is substitutable.
    assert list(inspect.signature(strict.classify).parameters) == ["golden", "actual"]
    assert strict.classify(golden, confident)["po_number"]["verdict"] == "match"
    assert strict.classify(golden, unsure)["po_number"]["verdict"] == "wrong_value"
    # ...and the shipped classifiers are unaffected by confidence.
    assert CLASSIFIERS["regression"].classify(golden, unsure)["po_number"]["verdict"] == "match"


def test_every_registered_classifier_exposes_its_scorer() -> None:
    """`scorer` is where a strategy lives; `classify` is it wrapped. A row
    whose two disagreed would be a strategy nobody could read."""
    from idp_regression.classifier.gate import make_classifier

    for name, classifier in CLASSIFIERS.items():
        rebuilt = make_classifier(classifier.scorer)
        golden = _golden("")
        assert (
            rebuilt(golden, _actual(""))["po_number"]["verdict"]
            == classifier.classify(golden, _actual(""))["po_number"]["verdict"]
        ), name


# ── a scorer escalating one field to gate-failing ─────────────────────


def _escalating_scorer(field_type: str) -> Any:
    """A scorer that makes every field of `field_type` mandatory,
    whatever the golden says."""
    from idp_regression.classifier.scorers import regression_scorer
    from idp_regression.classifier.scoring import ScoreResult

    def scorer(ctx: Any) -> Any:
        verdict = regression_scorer(ctx)
        if ctx.field_type == field_type:
            return ScoreResult(verdict, critical=True)
        return verdict

    return scorer


def test_a_scorer_can_make_one_field_fail_the_whole_document() -> None:
    """The golden says this field is not gated; the scorer says it is.
    The document fails."""
    from idp_regression.classifier.gate import make_classifier, overall_gate

    classify_strict = make_classifier(_escalating_scorer("text"))
    golden = _golden("PO-1", critical=False)  # NOT gated by the golden

    verdicts = classify_strict(golden, _actual("PO-2"))

    assert verdicts["po_number"]["verdict"] == "wrong_value"
    assert verdicts["po_number"]["critical"] is True
    assert overall_gate(verdicts) == "FAIL"
    # The default classifier, on the same inputs, does not fail: the
    # escalation is the scorer's, not a change to the gate.
    assert overall_gate(CLASSIFIERS["regression"].classify(golden, _actual("PO-2"))) == "PASS"


def test_a_scorer_can_never_cancel_the_goldens_own_critical() -> None:
    """Escalation only: `critical` is OR-ed, never replaced. A scorer
    able to quietly un-gate a field would turn a real regression into a
    green build."""
    from idp_regression.classifier.gate import make_classifier, overall_gate
    from idp_regression.classifier.scoring import ScoreResult

    def tries_to_demote(ctx: Any) -> Any:
        return ScoreResult("wrong_value", critical=False)

    verdicts = make_classifier(tries_to_demote)(_golden("PO-1"), _actual("PO-2"))

    assert verdicts["po_number"]["critical"] is True
    assert overall_gate(verdicts) == "FAIL"


def test_a_scorer_escalating_a_line_item_escalates_its_block() -> None:
    """A row verdict carries no `critical` of its own -- the gate reads
    the block's -- so a cell escalation has to reach the block or it
    would be silently inert."""
    from idp_regression.classifier.gate import make_classifier, overall_gate

    golden = cast(
        "Golden",
        {
            "fields": {"total": {"value": "10.00", "type": "number", "critical": False}},
            "tables": {
                "line_items": {
                    "match_key": "sku",
                    "critical": False,  # NOT gated by the golden
                    "rows": [{"sku": "S-1", "amount": "1.00"}],
                }
            },
        },
    )
    actual = cast(
        "NormalizedOutput",
        {
            "status": "SUCCEEDED",
            "fields": {"total": {"value": "10.00"}},
            "tables": {"line_items": [{"sku": {"value": "S-1"}, "amount": {"value": "9.99"}}]},
        },
    )

    lenient = CLASSIFIERS["regression"].classify(golden, actual)
    strict = make_classifier(_escalating_scorer("text"))(golden, actual)

    assert overall_gate(lenient) == "PASS"
    assert cast("Any", strict)["line_items"]["critical"] is True
    assert overall_gate(strict) == "FAIL"


def test_a_bare_verdict_return_keeps_the_goldens_decision() -> None:
    """The common case is unchanged: a scorer returning just the word
    leaves the gate reading the golden, exactly as before `ScoreResult`
    existed."""
    golden = _golden("PO-1", critical=False)

    verdicts = CLASSIFIERS["regression"].classify(golden, _actual("PO-2"))

    assert verdicts["po_number"]["critical"] is False
    assert CLASSIFIERS["regression"].gate(verdicts) == "PASS"
