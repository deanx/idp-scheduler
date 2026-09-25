"""The classifiers this repo ships, as scorers.

Each is a worked example of the thing `scoring.py` documents: one pure
function over a single expected/actual pair. Adding your own does not
belong here -- write it wherever it lives and register it (see
`registry.py`); this module is what the two shipped `CLASSIFIERS` rows
point at.
"""

from __future__ import annotations

from idp_regression.classifier.scoring import (
    ScoreContext,
    VerdictLiteral,
    compare_value,
    is_empty,
)


def regression_scorer(ctx: ScoreContext) -> VerdictLiteral:
    """The default (`regression` classifier), unchanged behaviour.

    A curated golden asserts a field should be there, so an empty actual
    is a loss whatever the golden holds -- including when the golden's own
    value is empty, which a Curator expresses as ``critical: false``.
    """
    if is_empty(ctx.actual):
        return "missing"
    return compare_value(ctx.field_type, ctx.expected or "", ctx.actual or "")


def pinned_file_scorer(ctx: ScoreContext) -> VerdictLiteral:
    """The `pinned-file` classifier: one file, pinned to a trusted Action
    version, re-checked under a new one.

    One rule apart from :func:`regression_scorer`: when the trusted
    version read NOTHING and the version under test also reads nothing,
    that is agreement, not a loss. Which is what lets a pinned golden mark
    every field critical -- and so a value invented where the trusted
    version found none is ``wrong_value`` on a critical field, a failure
    rather than a footnote.
    """
    if is_empty(ctx.actual):
        return "match" if is_empty(ctx.expected) else "missing"
    return compare_value(ctx.field_type, ctx.expected or "", ctx.actual or "")
