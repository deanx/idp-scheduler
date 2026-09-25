"""Named classifiers (user decision, 2026-09-25).

A *classifier* here is the pair a run compares with: a `classify`
function and the `gate` that aggregates its verdicts. The runner names
one; it never imports a specific implementation.

    run_eval(..., classifier="pinned-file")

Two ship today, and they exist because the two use cases genuinely
disagree about one rule:

* **`regression`** (default) — the watched-Action path, unchanged. A
  curated golden asserts a field should be there, so an empty actual is
  `missing` whatever the golden holds.
* **`pinned-file`** — "this file was already validated against the
  trusted version; is it still valid under a new version/LLM?" Here an
  empty expected value matched by an empty actual is agreement (neither
  version read anything), so it is a `match`. That lets a pinned golden
  mark EVERY field critical, including the empty ones, while invented
  content still fails as `wrong_value`.

Why a registry and not two forked modules
-----------------------------------------
Measured before choosing: `gate.py` is 510 lines and `canonical.py` 136,
with 1,809 lines of tests behind them, and the two cases differ in **one
branch** of `_classify_field`. Forking would copy ~500 lines to change
~8, and the copy would be the under-tested one — while being the copy
that answers "did the model swap break my file". So the comparison
engine (canonicalization by type, table row pairing on `match_key`,
input validation) is shared, and what differs is expressed as two
two-argument entry points behind this table.

Adding a third classifier
-------------------------
Write a **scorer** -- one pure function over a single expected/actual
pair, the same shape as an evaluation platform's custom scorer (`scoring.py` is the authoring API;
`scorers.py` shows the two shipped ones, each 3 lines of body) -- then::

    CLASSIFIERS["my-rule"] = Classifier(
        name="my-rule",
        classify=make_classifier(my_scorer),
        gate=overall_gate,
        description="...",
        scorer=my_scorer,
    )

The fan-out over fields, prompts and table rows, the input validation and
the gate are shared; a classifier does not restate them.
`tests/classifier/test_registry.py` then holds it to the contract every
classifier must satisfy — the pinned two-argument signature (CT-02), the
six verdict literals, and a `gate` returning only PASS/FAIL — so a new
entry cannot quietly introduce a vocabulary the score names (INV-03) and
the remediation UI do not know about.

⚠️ Registered NAMES only, never an importable path from a flag: a
`--classifier` that could load arbitrary code would turn a CLI argument
into a code-execution surface on a runner that holds IDP and platform
credentials.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from idp_regression.classifier.gate import classify, classify_pinned_file, overall_gate
from idp_regression.classifier.scorers import pinned_file_scorer, regression_scorer
from idp_regression.classifier.scoring import ClassifyFn, GateFn, Scorer


class Classifier(NamedTuple):
    """One named comparison strategy.

    `scorer` is where the strategy actually lives -- the small per-value
    function (`scorers.py`). `classify` is that scorer wrapped in the
    shared fan-out (`gate.make_classifier`), carried explicitly so the
    pinned two-argument signature is what the registry hands out.
    """

    name: str
    classify: ClassifyFn
    gate: GateFn
    description: str
    scorer: Scorer


class UnknownClassifierError(Exception):
    """`--classifier` named something not in the registry. Raised before
    any extraction so a typo costs nothing, and carries the valid names
    so the caller can print them."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.available = sorted(CLASSIFIERS)
        super().__init__(f"unknown classifier {name!r} (available: {', '.join(self.available)})")


#: The default is the behaviour every existing run already has. A run
#: that does not name a classifier must keep comparing exactly as it did
#: before this registry existed.
DEFAULT_CLASSIFIER: Final = "regression"

CLASSIFIERS: Final[dict[str, Classifier]] = {
    "regression": Classifier(
        name="regression",
        classify=classify,
        gate=overall_gate,
        description=(
            "Watched-Action regression against a curated golden set. An empty "
            "actual is `missing` whatever the golden holds."
        ),
        scorer=regression_scorer,
    ),
    "pinned-file": Classifier(
        name="pinned-file",
        classify=classify_pinned_file,
        gate=overall_gate,
        description=(
            "One file pinned to a trusted Action version, re-checked under a new "
            "one. Empty expected + empty actual is agreement (`match`); invented "
            "content is still `wrong_value`."
        ),
        scorer=pinned_file_scorer,
    ),
}


def resolve(name: str | None) -> Classifier:
    """The named classifier, or the default when `name` is `None`/empty.

    Fail-closed on an unknown name: a run that silently fell back to the
    default would report a gate computed by a comparison nobody asked
    for, and two runs would be incomparable without saying so.
    """
    if not name:
        return CLASSIFIERS[DEFAULT_CLASSIFIER]
    if name not in CLASSIFIERS:
        raise UnknownClassifierError(name)
    return CLASSIFIERS[name]
