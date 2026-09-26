"""The authoring API for custom scorers and gates.

**This module is the public surface.** Everything a scorer needs is here
or re-exported from here, and nothing here is internal machinery -- so
"import it from `scoring`" is the whole rule, and anything you find
elsewhere in `classifier/` is the harness and may change.

    from idp_regression.classifier.scoring import (
        ScoreContext, ScoreResult, compare_value, is_empty,
    )

    def my_scorer(ctx: ScoreContext):
        if is_empty(ctx.actual):
            return "missing"
        return compare_value(ctx.field_type, ctx.expected or "", ctx.actual or "")

Then register it (see `registry.py`): `make_classifier(my_scorer)` wraps
it in the shared fan-out -- which fields exist, prompts, pairing table
rows by ``match_key``, input validation -- and one row in `CLASSIFIERS`
names it for `--classifier`.

What a scorer may look at
-------------------------
Everything the comparison has at that point, carried by
:class:`ScoreContext` -- deliberately a NamedTuple rather than positional
arguments, so a later field can be added without breaking every scorer
that already exists:

``name``             the field name, prompt key, or table column
``kind``             ``"field"`` | ``"prompt"`` | ``"table_column"``
``field_type``       ``number`` | ``date`` | ``id`` | ``text``
``expected``         the golden value (``None``/``""`` when the golden has none)
``actual``           the extracted value (``None``/``""`` when nothing was read)
``confidence``       IDP's own confidence for this value, ``None`` if absent
``critical``         the golden's gate opt-in for this field
``format_critical``  the golden's ``wrong_format``-fails opt-in (DEBT-80)
``match_key``        for a table column, the row's key; ``None`` otherwise
``source``           for a prompt, IDP's ``source``; ``None`` otherwise

``confidence`` is the interesting one and is unused by both shipped
scorers: it reaches the verdict map either way, but no decision is made
on it. A scorer that failed a match below a confidence floor is a ~3-line
function and needs nothing else.

Making one field fail the whole document
----------------------------------------
Return a :class:`ScoreResult` instead of a bare verdict::

    def ids_are_always_mandatory(ctx: ScoreContext) -> VerdictLiteral | ScoreResult:
        verdict = regression_scorer(ctx)
        if ctx.field_type == "id":
            return ScoreResult(verdict, critical=True)   # gate on it regardless
        return verdict

`critical` is OR-ed with the golden's, never AND-ed: a scorer can make a
field mandatory, never optional. See :class:`ScoreResult`.

A rule about the DOCUMENT rather than a field -- fail on any
``wrong_format`` anywhere, fail above a count of differences -- is not a
scorer's job. That is the ``gate`` on the classifier's registry row
(:class:`GateFn`), which ``overall_gate`` is merely the default for.

A scorer must stay PURE -- no I/O, no clock, no randomness. The
classifier package is the CI gate and its output has to be a function of
(golden, actual) alone.
"""

from __future__ import annotations

from typing import Literal, NamedTuple, Protocol

from idp_regression.classifier.canonical import compare_value, is_empty
from idp_regression.classifier.types import (
    Golden,
    NormalizedOutput,
    VerdictLiteral,
    VerdictMap,
)

__all__ = [
    "ClassifyFn",
    "GateFn",
    "NormalizedOutput",
    "ScoreContext",
    "ScoreResult",
    "Scorer",
    "VerdictLiteral",
    "compare_value",
    "is_empty",
]


class ScoreContext(NamedTuple):
    """Everything one comparison can see. See the module docstring."""

    name: str
    kind: Literal["field", "prompt", "table_column"]
    field_type: str
    expected: str | None
    actual: str | None
    confidence: float | None = None
    critical: bool = False
    format_critical: bool = False
    match_key: str | None = None
    source: str | None = None
    #: D2b: the golden's declared `strptime` pattern for an ambiguous
    #: date, or None. Appended with a default -- this NamedTuple exists in
    #: that shape precisely so a later field breaks no existing scorer.
    #: A scorer may pass it to `compare_value` unconditionally; it is
    #: ignored for every type but `date`.
    date_format: str | None = None


class ScoreResult(NamedTuple):
    """A verdict, plus the one thing a scorer may say about the GATE.

    A scorer returning a bare `VerdictLiteral` is the common case and
    means "decide the gate from the golden, as always". Returning a
    `ScoreResult` lets it ESCALATE: `critical=True` makes this one
    field's `missing`/`wrong_value` fail the whole document even though
    the golden did not mark it critical, and `format_critical=True` does
    the same for `wrong_format` (DEBT-80's opt-in).

    ⚠️ **Escalation only, by design.** These flags are OR-ed with the
    golden's own: a scorer can make a field mandatory, it can NEVER make
    a mandatory field optional. The golden's `critical: true` is the
    Curator's explicit statement about what is gated, and a scorer able
    to quietly cancel it would turn a real regression into a green build
    -- the one failure `CLAUDE.md ## Rigor` calls this system's worst.
    So the guarantee stays monotone: critical in the golden ⇒ gated, and
    a scorer may only add.

    A scorer that wants to fail on something the gate does not fail on at
    all -- say any `wrong_format` anywhere, or a count of differences
    across the document -- is asking for a document-level rule, not a
    field-level one. That is the `gate` on the classifier's registry row,
    which `overall_gate` is merely the default for.
    """

    verdict: VerdictLiteral
    critical: bool = False
    format_critical: bool = False


class Scorer(Protocol):
    """One comparison. The unit a classifier is built from.

    Returns the verdict word, or a :class:`ScoreResult` when it also
    wants to escalate this field to gate-failing.
    """

    def __call__(self, ctx: ScoreContext) -> VerdictLiteral | ScoreResult: ...


class ClassifyFn(Protocol):
    """A scorer wrapped in the shared fan-out: the pinned CT-02 shape."""

    def __call__(self, golden: Golden, actual: NormalizedOutput) -> VerdictMap: ...


class GateFn(Protocol):
    """Aggregation of a verdict map to the run's PASS/FAIL."""

    def __call__(self, verdicts: VerdictMap) -> Literal["PASS", "FAIL"]: ...
