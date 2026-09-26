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

from idp_regression.classifier import classify
from idp_regression.classifier.canonical import compare_value
from idp_regression.classifier.types import Golden, NormalizedOutput


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
