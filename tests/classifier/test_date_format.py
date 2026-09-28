"""D2b / DEBT-81 — an ambiguous slash-date is a declared decision, not a guess.

`canonical.py` tries `%m/%d/%Y` before `%d/%m/%Y`, so every `NN/NN/YYYY`
with a day of 12 or less is read American-first. Silently. A European
invoice dated `03/04/2024` (3 April) compares as 4 March, and the gate
reports a confident verdict about the wrong calendar date.

The user's decision (2026-09-25) was to make the format DECLARABLE per
field, leaving today's behaviour as the undeclared default. These tests
pin both halves, because the undeclared half is still a guess and has to
be visible as one.
"""

from __future__ import annotations

from typing import cast

import jsonschema
import pytest

from idp_regression.classifier import classify
from idp_regression.classifier.canonical import compare_value
from idp_regression.classifier.types import Golden, MalformedGoldenError, NormalizedOutput
from idp_regression.platform.schema import load_golden_schema


def _golden(date_format: str | None) -> dict[str, object]:
    spec: dict[str, object] = {"value": "03/04/2024", "type": "date", "critical": True}
    if date_format is not None:
        spec["date_format"] = date_format
    return {"fields": {"shipped": spec}}


def _actual(value: str) -> dict[str, object]:
    return {
        "status": "SUCCEEDED",
        "fields": {"shipped": {"value": value, "confidence": 0.99}},
    }


def test_a_declared_day_first_format_is_honoured() -> None:
    """`03/04/2024` declared as `%d/%m/%Y` is 3 April, so an ISO
    `2024-04-03` is the SAME DATE.

    The verdict is `wrong_format`, not `match`, and that is correct:
    ADR-0003's tiers say equal-by-value but different-by-format is a
    formatting difference, not a value regression. What matters here is
    that it is not `wrong_value` -- the gate must not report a changed
    date when only the notation changed.
    """
    verdicts = classify(
        cast(Golden, _golden("%d/%m/%Y")), cast(NormalizedOutput, _actual("2024-04-03"))
    )
    assert verdicts["shipped"]["verdict"] == "wrong_format"


def test_a_declared_day_first_format_rejects_the_american_reading() -> None:
    """The same golden must NOT match 4 March. Without this, a
    `date_format` that was silently ignored would still look correct on
    the test above, since the American reading is what the default does.
    """
    verdicts = classify(
        cast(Golden, _golden("%d/%m/%Y")), cast(NormalizedOutput, _actual("2024-03-04"))
    )
    assert verdicts["shipped"]["verdict"] == "wrong_value"


def test_an_undeclared_slash_date_still_reads_american_first() -> None:
    """DEBT-81's behaviour, pinned rather than fixed — deliberately.

    Changing the default would re-verdict every existing golden, which a
    regression tool may not do to its own baseline. So the guess stays,
    and this test is what makes it a RECORDED guess: anyone changing the
    order has to change this test and state why.

    This is the gate's blind spot, and `calibrate_golden.py` names it in
    its report for exactly that reason.
    """
    # Undeclared, `03/04/2024` is read as 4 March. So the March date is
    # value-equal (`wrong_format`) and the April date is a real
    # difference (`wrong_value`) -- the exact inversion of the declared
    # day-first case above, which is what makes the default a GUESS.
    assert (
        classify(cast(Golden, _golden(None)), cast(NormalizedOutput, _actual("2024-03-04")))[
            "shipped"
        ]["verdict"]
        == "wrong_format"
    ), "the undeclared default changed; that re-verdicts every committed golden"
    assert (
        classify(cast(Golden, _golden(None)), cast(NormalizedOutput, _actual("2024-04-03")))[
            "shipped"
        ]["verdict"]
        == "wrong_value"
    )


def test_compare_value_keeps_its_public_three_argument_form() -> None:
    """`compare_value` is the PUBLIC scorer-authoring API (CLAUDE.md).

    Every custom scorer calls it with three positional arguments, so
    `date_format` had to arrive as an optional keyword. A signature
    change here breaks scorers this project does not own.
    """
    assert compare_value("date", "2024-04-03", "2024-04-03") == "match"
    assert (
        compare_value("date", "03/04/2024", "2024-04-03", date_format="%d/%m/%Y")
        == "wrong_format"
    )
    assert (
        compare_value("date", "03/04/2024", "2024-03-04", date_format="%d/%m/%Y")
        == "wrong_value"
    )


# --- DEBT-103: a date_format must be able to do something -------------------


def _entry(spec: dict[str, object]) -> dict[str, object]:
    return {"document_id": "d.pdf", "fields": {"shipped": spec}}


def _schema_valid(spec: dict[str, object]) -> bool:
    return jsonschema.Draft7Validator(load_golden_schema()).is_valid(_entry(spec))


def test_the_schema_accepts_a_date_written_in_its_declared_format() -> None:
    """The golden D2b exists for: the value as the document writes it, plus
    the format that says which date it is. The ISO-only rule used to reject
    it, which left no valid way to write it at all."""
    assert _schema_valid({"value": "03/04/2024", "type": "date", "date_format": "%d/%m/%Y"})


def test_the_schema_still_requires_iso_for_an_undeclared_date() -> None:
    assert not _schema_valid({"value": "03/04/2024", "type": "date"})
    assert _schema_valid({"value": "2024-04-03", "type": "date"})


def test_the_schema_refuses_a_date_format_on_a_non_date_field() -> None:
    """On `text` it was saved, looked declared, and did nothing."""
    assert not _schema_valid({"value": "03/04/2024", "type": "text", "date_format": "%d/%m/%Y"})


@pytest.mark.parametrize(
    "spec",
    [
        {"value": "03/04/2024", "type": "text", "date_format": "%d/%m/%Y", "critical": True},
        {"value": "03/04/2024", "type": "date", "date_format": "%Y-%m-%d", "critical": True},
        {"value": "03/04/2024", "type": "date", "date_format": "  ", "critical": True},
    ],
)
def test_the_classifier_refuses_a_date_format_that_cannot_do_anything(
    spec: dict[str, object],
) -> None:
    """Enforced here as well as in the schema: a golden can reach the
    classifier without passing through provisioning."""
    with pytest.raises(MalformedGoldenError) as excinfo:
        classify(cast(Golden, {"fields": {"shipped": spec}}), cast(NormalizedOutput, _actual("x")))
    assert "03/04/2024" not in str(excinfo.value), "INV-02: never the value"


def test_an_empty_value_with_a_declared_format_is_allowed() -> None:
    golden = {"fields": {"shipped": {"value": "", "type": "date", "date_format": "%d/%m/%Y",
                                     "critical": False}}}
    classify(cast(Golden, golden), cast(NormalizedOutput, _actual("")))


def test_a_blank_date_format_is_refused_even_on_an_empty_value() -> None:
    """With a value present, a blank format also fails to parse it; with an
    empty value only the blank check itself stands between it and a
    declaration that means nothing."""
    golden = {"fields": {"shipped": {"value": "", "type": "date", "date_format": "  ",
                                     "critical": False}}}
    with pytest.raises(MalformedGoldenError):
        classify(cast(Golden, golden), cast(NormalizedOutput, _actual("")))


def test_a_padded_value_is_parsed_the_way_the_comparison_reads_it() -> None:
    """Gate F-1 (M11): `canonical` strips before parsing, so the validator
    must too, or it refuses a golden the comparison would accept."""
    golden = {"fields": {"shipped": {"value": " 03/04/2024 ", "type": "date",
                                     "date_format": "%d/%m/%Y", "critical": True}}}
    classify(cast(Golden, golden), cast(NormalizedOutput, _actual("2024-04-03")))


def test_a_declared_format_falls_back_for_an_actual_in_another_notation() -> None:
    """Gate F-4 (M24): the declared pattern is tried first, then the lenient
    parser. For an ISO actual a raw passthrough looks the same, so only a
    third notation shows the fallback is there."""
    assert compare_value("date", "03/04/2024", "April 3, 2024", date_format="%d/%m/%Y") == (
        "wrong_format"
    )
