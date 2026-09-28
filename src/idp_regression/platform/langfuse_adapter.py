"""LangfuseAdapter — the concrete PlatformAdapter (ADR-0001, ADR-0005 #9).

All Langfuse-specific knowledge (endpoints, auth, wire shapes) is confined
to this module (and ``transport.py``/``schema_provisioning.py``/
``tracing.py``) — NFR N24 swappability, enforced by
``tests/platform/test_module_boundary.py``.

Design note (flagged for Atchim): the ``run_status`` metadata marker
(ADR-0004 #14) is implemented here as a well-known score
(``name="run_status"``) rather than a dataset-run/trace attribute
(DEBT-15) — ``record_run``'s linkage is per-dataset-item, so there is no
single "run-level" trace to attach a marker to, and an aborted run may
have written no records at all.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import os
import random
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, NotRequired, cast, get_args, get_origin, get_type_hints

from idp_regression.classifier.types import VerdictMap
from idp_regression.platform.errors import (
    DatasetFetchFailedError,
    ExperimentRecordFailedError,
    PlatformConfigurationError,
    RunStatusWriteFailedError,
    ScoreWriteFailedError,
    TracingNotConfiguredError,
    TransportError,
)
from idp_regression.platform.scoring import RUN_LEVEL_TRACE_SENTINEL, score_id, trace_id
from idp_regression.platform.tracing import ExperimentItem, ExperimentRunner, record_experiment
from idp_regression.platform.transport import HttpClient, UrllibHttpClient, sanitize_for_log
from idp_regression.platform.types import (
    Dataset,
    DatasetItem,
    DocumentRecord,
    PlatformAdapter,
    RunMetadata,
    RunStatus,
    ScoreInput,
)

logger = logging.getLogger(__name__)


def _str_annotated_field_names(td: type) -> list[str]:
    """FO-2 (DEBT-48/40/43/47) — derive a TypedDict's REQUIRED,
    `str`-obligated field names from the DECLARED TYPE, never hand-listed.
    Production-side counterpart of
    ``tests/platform/_type_pins.py::_str_fields`` (kept as a separate
    copy, deliberately: production code must not import from ``tests/``).

    DEBT-53 (prose) fix: this now calls ``get_type_hints(td,
    include_extras=True)`` and EXCLUDES any field whose origin is
    ``typing.NotRequired`` — the OLD version called ``get_type_hints``
    with no ``include_extras``, which strips the ``NotRequired[...]``
    wrapper entirely, so a hypothetical future ``NotRequired[str]`` field
    (no ``| None``) would have resolved to plain ``str`` and been wrongly
    folded into this REQUIRED set — the identical
    presence-mis-derivation trap ``_required_field_names`` above was
    fixed for DEBT-49, reintroduced on the VALUE-TYPE axis by the very
    function meant to sidestep it. ``ScoreInput.comment``
    (``NotRequired[str | None]``) stays excluded either way, but now
    because it is explicitly ``NotRequired`` AND its inner type is
    ``str | None`` (not ``str``) — not by the lucky coincidence the old
    docstring described. See ``_optional_str_annotated_field_names``
    below for the ``NotRequired[str]`` companion: fields that are
    `str`-typed but legitimately absent, which must NEVER be folded into
    this presence+type combined set."""
    hints = get_type_hints(td, include_extras=True)
    result = []
    for name, hint in hints.items():
        if get_origin(hint) is NotRequired:
            continue
        if hint is str:
            result.append(name)
    return sorted(result)


def _optional_str_annotated_field_names(td: type) -> list[str]:
    """DEBT-53 (prose) companion to ``_str_annotated_field_names`` above:
    the ``NotRequired[str]`` fields of ``td`` — `str`-typed WHEN PRESENT,
    but legitimately absent. Deliberately a SEPARATE derivation, never
    folded into the required set: a guard that combines presence+type
    for a field that may legitimately be missing rejects every score/
    record that omits it, the exact shape of the landmine this row
    describes. ``NotRequired[str | None]`` (e.g. ``ScoreInput.comment``)
    is NOT included here — its inner type is ``str | None``, not ``str``,
    so it is never type-checked by either derivation, matching today's
    behaviour."""
    hints = get_type_hints(td, include_extras=True)
    result = []
    for name, hint in hints.items():
        if get_origin(hint) is not NotRequired:
            continue
        (inner,) = get_args(hint)
        if inner is str:
            result.append(name)
    return sorted(result)


def _required_field_names(td: type) -> list[str]:
    """DEBT-49 — sound PRESENCE derivation for a TypedDict, robust to
    ``NotRequired`` under postponed annotations. Production-side
    counterpart of ``tests/platform/_type_pins.py::_required_fields``
    (kept as a separate copy, deliberately, same reason as
    ``_str_annotated_field_names`` above: production code must not
    import from ``tests/``).

    ``TypedDict.__required_keys__`` is UNSOUND on this repo's Python
    (3.13.5) with ``from __future__ import annotations`` in effect — it
    mis-derives every ``NotRequired`` field as required, because the
    annotation is a bare *string* at class-creation time and
    ``NotRequired`` is therefore never detected by that mechanism
    (verified live: ``ScoreInput.__required_keys__ ==
    frozenset({'comment', 'id', 'name', 'value'})``, with
    ``__optional_keys__`` empty, even though ``comment`` is declared
    ``NotRequired[str | None]``). ``DocumentRecord`` (this function's
    first caller) has no ``NotRequired`` field today, which is exactly
    why the old ``sorted(DocumentRecord.__required_keys__)`` call one
    level down used to look correct — it was correct BY LUCK, and would
    have silently started rejecting a legitimately-absent field the
    moment one was added (DEBT-49). NEVER use ``__required_keys__`` for
    a presence check anywhere in this module while postponed annotations
    are in effect.

    The fix: resolve every field via ``get_type_hints(td,
    include_extras=True)`` — which, unlike ``_str_annotated_field_names``
    above, deliberately KEEPS the ``NotRequired[...]`` wrapper visible —
    and exclude any field whose resolved hint's origin is
    ``typing.NotRequired``. This is presence-only: it says nothing about
    a field's value TYPE (that remains ``_str_annotated_field_names``'s
    separate job for the ``str``-typed subset), so a non-``str`` required
    field — e.g. ``DocumentRecord.scores: list[ScoreInput]`` — still gets
    its presence checked here even though it would never appear in a
    ``str``-fields-only derivation. Collapsing the two into one loop
    would silently drop that presence check, which is exactly the "keep
    them separate" instruction this function exists to honour."""
    hints = get_type_hints(td, include_extras=True)
    return sorted(name for name, hint in hints.items() if get_origin(hint) is not NotRequired)


_RUN_STATUS_SCORE_NAME = "run_status"

#: FO-3 (DEBT-48): the recognised ``RunStatus`` marker values, derived
#: from the Literal itself (never hand-written) — mirrors FO-4's
#: ``_VALID_GATES`` in ``scoring.py`` verbatim. Widening ``RunStatus`` to
#: a third value automatically widens this set too.
_VALID_RUN_STATUSES: frozenset[str] = frozenset(get_args(RunStatus))

#: Hard cap on dataset-items pages fetched per get_dataset() call (REG-04,
#: F-3) -- generous for any real golden set, but stops an untrusted/bogus
#: ``meta.totalPages`` from looping unbounded.
_MAX_DATASET_PAGES = 500

#: Bounded score-write retry defaults (REG-03, F-2; ADR-0005 #9: the
#: deterministic score_id makes a retry an upsert, so a 5xx/transport
#: failure is safe to retry). Backoff shape matches ADR-0004 #3's
#: transient-transport-retry spec: exponential, full jitter, base 1s,
#: cap 8s, default 3 attempts.
_DEFAULT_SCORE_WRITE_MAX_ATTEMPTS = 3
_DEFAULT_SCORE_WRITE_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_SCORE_WRITE_BACKOFF_CAP_SECONDS = 8.0

#: FO-2 (DEBT-48): the REQUIRED `str`-obligated fields of ``ScoreInput``
#: -- computed ONCE, from the declared type (see
#: ``_str_annotated_field_names``), and reused by
#: ``_require_record_shape``'s score-loop below. Verified today:
#: ``["id", "name", "value"]`` -- ``comment`` is excluded because it is
#: NOT `str` (it's `NotRequired[str | None]`, i.e. `str | None`), never
#: because of ``__required_keys__`` (see that function's docstring for why
#: that attribute is unsafe to use here).
_SCORE_STR_FIELDS = _str_annotated_field_names(ScoreInput)

#: DEBT-53 (prose): the presence derivation for ``ScoreInput`` -- SEPARATE
#: from ``_SCORE_STR_FIELDS`` even though both produce ``["id", "name",
#: "value"]`` today (every required ``ScoreInput`` field happens to be
#: ``str``), the same MY-25/DEBT-43 reason ``_RUN_METADATA_REQUIRED_
#: FIELDS`` is kept separate from ``_RUN_METADATA_STR_FIELDS`` below --
#: collapsing presence onto the `str`-only derivation would silently miss
#: a future required non-`str` field's presence check.
_SCORE_REQUIRED_FIELDS = _required_field_names(ScoreInput)

#: DEBT-53 (prose): the ``NotRequired[str]`` fields of ``ScoreInput`` --
#: `str`-typed WHEN PRESENT, but never required to be present. Empty
#: today (``comment`` is ``NotRequired[str | None]``, not
#: ``NotRequired[str]``) -- kept as its own derivation, never folded into
#: ``_SCORE_STR_FIELDS``, so the day a genuine ``NotRequired[str]`` field
#: is added it is type-checked ONLY when supplied, not rejected for being
#: absent.
_SCORE_OPTIONAL_STR_FIELDS = _optional_str_annotated_field_names(ScoreInput)

#: DEBT-49: the SOUND presence derivation for ``DocumentRecord`` — computed
#: ONCE, from the declared type (see ``_required_field_names``), and reused
#: by ``_require_record_shape``'s presence loop below. Verified today:
#: ``["document_id", "item_id", "scores"]`` (identical to
#: ``sorted(DocumentRecord.__required_keys__)`` for as long as
#: ``DocumentRecord`` carries no ``NotRequired`` field — the two derivations
#: diverge, correctly, the day one is added).
_DOCUMENT_RECORD_REQUIRED_FIELDS = _required_field_names(DocumentRecord)

#: FO-1 (DEBT-48): the `str`-obligated (VALUE-type) fields of
#: ``RunMetadata`` -- computed ONCE, reused by
#: ``_require_run_metadata_shape`` below for its VALUE-type loop only.
_RUN_METADATA_STR_FIELDS = _str_annotated_field_names(RunMetadata)

#: Required B (fix-round finding, MY-25): the SOUND presence derivation for
#: ``RunMetadata`` -- computed ONCE, reused by
#: ``_require_run_metadata_shape``'s PRESENCE loop below. Deliberately a
#: SEPARATE derivation from ``_RUN_METADATA_STR_FIELDS`` even though both
#: produce the same four names today (every ``RunMetadata`` field is
#: currently plain ``str``) -- collapsing presence onto the `str`-only
#: derivation is exactly the mutation (MY-25) that let a hypothetical
#: required non-`str` field (e.g. ``attempt: int``) go unchecked for
#: presence, the identical ``DocumentRecord.scores``-class trap DEBT-49
#: fixed one guard over.
_RUN_METADATA_REQUIRED_FIELDS = _required_field_names(RunMetadata)


def _require_record_shape(record: DocumentRecord) -> None:
    """FU-01.3-B / QA-01 F-1 / REG-09 (widened by FU-01.3-D): validate a
    record's SHAPE before ANY subscript of it, so malformed caller input
    raises the typed ``ExperimentRecordFailedError`` (the Protocol
    docstring's promised contract, ``types.py:99-101``) instead of an
    untyped ``KeyError``/``TypeError``. Called from a loop at the very
    TOP of ``record_run``, before ``record_item_ids`` is even built --
    the seam where ``record["item_id"]`` used to be subscripted
    seventeen lines before this guard ran is now physically impossible,
    not merely patched at that call site. Runs before any SDK call, so
    ``run_experiment_calls == 0`` holds on every path here. Absorbs the
    ``.get("scores")`` tolerance debt from FU-01.3-A -- this replaces it.

    FU-01.3-D / QA-01 re-audit F-1 structural fix (Atchim's ruling,
    carried verbatim in substance -- "enumerating keys by hand is not
    the fix, it IS the defect"):
      1. ``isinstance(record, dict)`` is the FIRST statement --
         ``"x" not in record`` on a ``str`` silently does substring
         semantics, the same trap already patched one level down for
         non-dict scores (REG-09's seventh anchor case).
      2. Required keys are read from ``_required_field_names(DocumentRecord)``
         (DEBT-49's sound derivation — verified ``['document_id',
         'item_id', 'scores']`` — and NOT
         ``DocumentRecord.__required_keys__``, which is unsound under
         postponed annotations for any TypedDict carrying a
         ``NotRequired`` field; see ``_required_field_names``'s
         docstring), not hand-enumerated, so a new required field on
         the TypedDict auto-generates its own guard here -- three
         consecutive hand-written attempts enumerated a subset of that
         list (0 for 3).
      3. ``item_id``'s VALUE (not just its presence) is checked -- a
         non-string ``item_id`` would otherwise sail past the key check
         and later blow up ``set(record_item_ids)`` with an untyped
         ``TypeError: unhashable type``.

    INV-02: the message names ``document_id`` ONLY -- never a score id,
    name, or value. Cases with no document_id to name (record is not a
    dict at all, or ``document_id`` itself is the missing key) say so
    without inventing one.
    """
    if not isinstance(record, dict):
        raise ExperimentRecordFailedError("record_run: a record is not a dict (malformed input)")
    for key in _DOCUMENT_RECORD_REQUIRED_FIELDS:
        if key not in record:
            if key == "document_id":
                raise ExperimentRecordFailedError("record_run: a record is missing 'document_id'")
            raise ExperimentRecordFailedError(
                f"record_run: record for document_id={record.get('document_id')!r} "
                f"is missing {key!r}"
            )
    if not isinstance(record["document_id"], str):
        # FU-01.3-G / QA-01 re-audit #3 F-1 / REG-09 (reopened): the
        # first FAIL-OPEN member of this family. INV-02: name the FIELD
        # only, never interpolate the value -- document_id itself IS the
        # offending value here, unlike the item_id check below (which
        # can safely name a document_id already known to be a str).
        raise ExperimentRecordFailedError(
            "record_run: a record has a non-string 'document_id'"
        )
    document_id = record["document_id"]
    if not isinstance(record["item_id"], str):
        raise ExperimentRecordFailedError(
            f"record_run: record for document_id={document_id!r} has a non-string 'item_id'"
        )
    scores = record.get("scores")
    if not isinstance(scores, list):
        # Covers both `"scores": None` (TypeError today) and any other
        # non-list shape (e.g. a dict -- truthy and iterable, so a bare
        # `.get(..., [])` tolerance would NOT have caught it either).
        raise ExperimentRecordFailedError(
            f"record_run: record for document_id={document_id!r} has a non-list "
            "'scores' value (expected a list of score dicts)"
        )
    _require_verdicts_shape(record.get("verdicts"), document_id=document_id)
    for score in scores:
        # FO-2 (DEBT-48/40/43/47), split per DEBT-53 (prose): this used to
        # hand-enumerate "id" and "name" only, omitting "value" (declared
        # `str` on `ScoreInput`, four lines below the guard FU-01.3-G
        # fixed for `DocumentRecord`) -- the identical hand-enumeration
        # defect one level down. That was then folded into a SINGLE
        # presence+type loop over `_SCORE_STR_FIELDS`, "sound only
        # because every required field happens to be str-annotated" --
        # the same MY-25 coincidence `_require_run_metadata_shape` below
        # was already split to avoid, reintroduced here on a different
        # TypedDict. Now split identically: presence over
        # `_SCORE_REQUIRED_FIELDS` (every required field, `str`-obligated
        # or not), type over `_SCORE_STR_FIELDS` (the `str`-obligated
        # REQUIRED subset only -- presence already guaranteed by the loop
        # above), and a third pass over `_SCORE_OPTIONAL_STR_FIELDS`
        # (`NotRequired[str]` fields -- type-checked ONLY when supplied,
        # never required to be present).
        if not isinstance(score, dict):
            raise ExperimentRecordFailedError(
                f"record_run: a score for document_id={document_id!r} is not a dict "
                "(malformed input)"
            )
        score_as_dict = cast(dict[str, Any], score)
        for key in _SCORE_REQUIRED_FIELDS:
            # PRESENCE only -- covers every required field regardless of
            # value type (MY-25's fix, applied here).
            if key not in score_as_dict:
                raise ExperimentRecordFailedError(
                    f"record_run: a score for document_id={document_id!r} is missing {key!r}"
                )
        for key in _SCORE_STR_FIELDS:
            # VALUE TYPE only, for the REQUIRED `str`-obligated subset --
            # presence already guaranteed by the loop above, so this
            # subscript cannot raise KeyError. INV-02: name the FIELD
            # only, never interpolate the value -- a score value is a
            # verdict literal but is treated as sensitive here, mirroring
            # `document_id`'s guard above. (`cast` above: `key` is a
            # runtime str, not a literal, so ScoreInput's TypedDict
            # subscript restriction doesn't apply.)
            if not isinstance(score_as_dict[key], str):
                raise ExperimentRecordFailedError(
                    f"record_run: a score for document_id={document_id!r} has a "
                    f"non-string {key!r}"
                )
        for key in _SCORE_OPTIONAL_STR_FIELDS:
            # DEBT-53 (prose): VALUE TYPE only, and only IF SUPPLIED -- a
            # `NotRequired[str]` field's absence is legitimate and must
            # never raise here. This is the fix: the OLD single-loop
            # derivation would have folded a future field of this shape
            # into `_SCORE_STR_FIELDS` and rejected every score that
            # legitimately omitted it.
            if key in score_as_dict and not isinstance(score_as_dict[key], str):
                raise ExperimentRecordFailedError(
                    f"record_run: a score for document_id={document_id!r} has a "
                    f"non-string {key!r}"
                )


def _require_run_metadata_shape(metadata: RunMetadata) -> None:
    """FO-1 (DEBT-48): ``RunMetadata``'s three ``str`` fields
    (``action_id``/``action_version``/``golden_version``) used to be
    subscripted directly out of the caller-supplied dict while building
    the ``record_experiment(...)`` call's ``metadata={...}`` argument --
    no presence or type check anywhere before that point. A missing key
    raised a bare ``KeyError`` (not the Protocol's promised typed error,
    ``types.py`` ``PlatformAdapter.record_run`` docstring); a non-string
    value didn't raise at ALL -- it landed in the run's platform metadata
    and ``record_run`` returned success. INV-04 says every completed run
    records this metadata; a run with ``action_version=None`` was
    indistinguishable from a good one.

    Required B (fix-round finding, MY-25): this used to derive BOTH
    presence and value-type from ``_RUN_METADATA_STR_FIELDS`` /
    ``_str_annotated_field_names(RunMetadata)`` in one loop -- sound only
    by coincidence, because every ``RunMetadata`` field happens to be
    ``str`` today. A required NON-``str`` field (e.g. a future
    ``attempt: int``) would never appear in that ``str``-only derivation,
    so its PRESENCE would go unchecked entirely -- the identical
    ``DocumentRecord.scores`` trap ``_require_record_shape`` above is
    already structured to avoid, via its own two-derivation split. Same
    fix here, same structural reason: presence is derived from
    ``_RUN_METADATA_REQUIRED_FIELDS`` (``_required_field_names(
    RunMetadata)``, DEBT-49's sound derivation -- includes every required
    field regardless of value type), and value type is checked
    separately, only for the ``str``-obligated subset
    (``_RUN_METADATA_STR_FIELDS`` / ``_str_annotated_field_names(
    RunMetadata)``). ``isinstance(dict, ...)`` remains the guard's FIRST
    statement (a ``str``'s ``in`` does substring semantics), same as
    ``_require_record_shape``. Called at the top of ``record_run``,
    before any subscript of ``metadata`` and before any SDK/HTTP call.

    INV-02: the message names the FIELD only, never the offending value.
    """
    if not isinstance(metadata, dict):
        raise ExperimentRecordFailedError("record_run: metadata is not a dict (malformed input)")
    # `key` is a runtime str, not a literal, so RunMetadata's TypedDict
    # subscript restriction doesn't apply (same cast pattern as the score
    # loop above).
    metadata_as_dict = cast(dict[str, Any], metadata)
    for key in _RUN_METADATA_REQUIRED_FIELDS:
        # PRESENCE only -- covers every required field, `str`-obligated or
        # not (MY-25's fix: a required non-`str` field must be caught
        # here even though it never reaches the value-type loop below).
        if key not in metadata_as_dict:
            raise ExperimentRecordFailedError(f"record_run: metadata is missing {key!r}")
    for key in _RUN_METADATA_STR_FIELDS:
        # VALUE TYPE only, for the `str`-obligated subset -- presence for
        # these fields was already guaranteed by the loop above (every
        # `str`-obligated `RunMetadata` field is also required today), so
        # this subscript cannot raise `KeyError`.
        if not isinstance(metadata_as_dict[key], str):
            raise ExperimentRecordFailedError(
                f"record_run: metadata has a non-string {key!r}"
            )


def _body_snippet_for_error(body: Any) -> str:
    """A logging-safe error summary — deliberately NEVER the raw response
    body. A 400 validation body can echo back golden-shaped values (NFR
    N5, INV-02), so only the body's *shape* is recorded, never its content.
    """
    if body is None:
        return "<empty body>"
    return f"<{type(body).__name__} body, {len(str(body))} chars, redacted>"


def _require_verdicts_shape(verdicts: object, *, document_id: str) -> None:
    """`DocumentRecord.verdicts` (`NotRequired[VerdictMap | None]`) has no
    presence/type derivation to ride: it is neither `str` nor
    `NotRequired[str]`, so `_DOCUMENT_RECORD_REQUIRED_FIELDS` /
    `_str_annotated_field_names` skip it entirely -- "hand-written with
    one automated column" (R2). `_expected_output` subscripts it building
    each `ExperimentItem`, OUTSIDE the total `task` closure's try/except,
    so a malformed map used to escape `record_run` as a raw
    TypeError/KeyError/AttributeError instead of this typed error
    (S-01.3 re-stamp #5 F-2, HARDEN-01 GAP-1 class).

    `None` (or the key absent) is the `--platform-values verdicts-only`
    shape and is always accepted. Otherwise: a dict, every entry a dict
    with a `"verdict"` key, and a `"detail"` entry's `"rows"` a list of
    dicts. INV-02: the message names `document_id` only.
    """
    if verdicts is None:
        return
    if not isinstance(verdicts, dict):
        raise ExperimentRecordFailedError(
            f"record_run: record for document_id={document_id!r} has a non-dict "
            "'verdicts' value (expected a mapping or null)"
        )
    for entry in verdicts.values():
        if not isinstance(entry, dict) or "verdict" not in entry:
            raise ExperimentRecordFailedError(
                f"record_run: record for document_id={document_id!r} has a malformed "
                "'verdicts' entry (expected a mapping with a 'verdict' key)"
            )
        if entry["verdict"] == "detail":
            rows = entry.get("rows")
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or "verdict" not in row for row in rows
            ):
                raise ExperimentRecordFailedError(
                    f"record_run: record for document_id={document_id!r} has a 'verdicts' "
                    "table entry whose 'rows' is not a list of row mappings"
                )


def _leaf_values(verdicts: VerdictMap) -> dict[str, Any]:
    """Flatten a verdict map to `{leaf: {verdict, expected, actual,
    confidence}}` for the trace span.

    Table rows are flattened `table[key].column`, the same labelling
    `scripts/show_run.py` prints, so a span and the local run artifact
    read the same way.
    """
    flat: dict[str, Any] = {}
    for name, entry in verdicts.items():
        if entry["verdict"] == "detail":
            for row in entry["rows"]:
                column = row.get("column")
                label = f"{name}[{row.get('match_key')}]" + (f".{column}" if column else "")
                flat[label] = {
                    "verdict": row["verdict"],
                    "expected": row.get("expected"),
                    "actual": row.get("actual"),
                    "confidence": row.get("confidence"),
                }
            continue
        flat[name] = {
            "verdict": entry["verdict"],
            "expected": entry.get("expected"),
            "actual": entry.get("actual"),
            "confidence": entry.get("confidence"),
        }
    return flat


def _expected_output(record: DocumentRecord) -> dict[str, Any]:
    """`{leaf: expected}` for the experiment item, or `{}` when the run is
    not recording values."""
    verdicts = record.get("verdicts")
    if not verdicts:
        return {}
    return {leaf: detail["expected"] for leaf, detail in _leaf_values(verdicts).items()}


class LangfuseAdapter:
    """PlatformAdapter implementation over the Langfuse public REST API."""

    def __init__(
        self,
        client: HttpClient,
        tracing_client: ExperimentRunner | None = None,
        *,
        score_write_max_attempts: int = _DEFAULT_SCORE_WRITE_MAX_ATTEMPTS,
        score_write_backoff_base_seconds: float = _DEFAULT_SCORE_WRITE_BACKOFF_BASE_SECONDS,
        score_write_backoff_cap_seconds: float = _DEFAULT_SCORE_WRITE_BACKOFF_CAP_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        random_func: Callable[[], float] = random.random,
        record_deadline_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._tracing_client = tracing_client
        self._score_write_max_attempts = score_write_max_attempts
        self._score_write_backoff_base_seconds = score_write_backoff_base_seconds
        self._score_write_backoff_cap_seconds = score_write_backoff_cap_seconds
        self._sleep = sleep
        self._random = random_func
        #: DEBT-20: a whole-record-phase wall-clock cap (INV-07,
        #: monotonic). None = disabled -- the shipped default. This is
        #: the ADAPTER's own deadline (Soneca ruling: the orchestrator
        #: never sees the per-score loop under ADR-0005 #9, so it has
        #: nothing to time). PROVISIONAL: the real value needs S-01.6
        #: timings + N8's 50-doc arithmetic, neither of which exist yet
        #: -- a low guess would convert a slow-but-alive platform from
        #: *stretching* a run into *aborting* one that would otherwise
        #: have succeeded, on a CI gate whose whole job is to be trusted.
        self._record_deadline_seconds = record_deadline_seconds
        self._clock = clock
        #: item_id -> dataset_id from the most recent get_dataset call —
        #: record_run() reads this instead of re-fetching (INV-04,
        #: ADR-0005 #9 "no second fetch"). DEBT-18 (Atchim suggestion):
        #: this used to also cache the golden per item, but nothing reads
        #: it any more (expected_output is always {} — see record_run) —
        #: keeping it would be dead sensitive data sitting in memory for
        #: no reason, so only dataset_id is kept.
        self._item_cache: dict[str, str] = {}
        #: the dataset name that produced ``_item_cache`` — record_run
        #: checks its own ``dataset_name`` argument against this so a
        #: caller passing the wrong dataset (while reusing stale item ids
        #: from a previous, correctly-fetched dataset) is caught rather
        #: than silently recording against the wrong dataset.
        self._cached_dataset_name: str | None = None

    def get_dataset(self, name: str) -> Dataset:
        encoded_name = urllib.parse.quote(name, safe="")
        try:
            status, body = self._client.request(
                "GET", f"/api/public/v2/datasets/{encoded_name}"
            )
        except TransportError as exc:
            raise DatasetFetchFailedError(f"get_dataset transport failure: {exc}") from exc
        if status >= 400:
            logger.error(
                "dataset_fetch_failed status=%s dataset=%s detail=%s",
                status,
                sanitize_for_log(name),
                _body_snippet_for_error(body),
            )
            raise DatasetFetchFailedError(f"get_dataset failed with HTTP {status}")
        if not isinstance(body, dict):
            raise DatasetFetchFailedError("get_dataset returned an unexpected body shape")
        dataset_id = body.get("id")
        if not isinstance(dataset_id, str) or not dataset_id:
            # FO-8 (DEBT-48): `ExperimentItem.dataset_id` (tracing.py,
            # declared `Any` -- the SDK's real contract is a string id)
            # is cached from this value with NO check anywhere else in
            # this module. Unchecked, every cache entry (and every
            # ExperimentItem record_run later builds from it) could
            # carry `None` or an empty string -- an experiment whose
            # items carry no dataset linkage, while THIS call still
            # returns successfully and the structural check in
            # record_experiment never looks at dataset_id at all. That
            # is the ADR-0005 #9 failure mode (a run invisible/unlinked
            # in the Experiments tab) reported as a clean run. Raised
            # BEFORE the paginated dataset-items fetch -- no further
            # network call for a dataset response that is already
            # unusable.
            raise DatasetFetchFailedError("get_dataset: dataset response is missing a valid 'id'")
        schema = body.get("expectedOutputSchema")
        if schema is not None and not isinstance(schema, dict):
            # FO-6 (DEBT-48): returned verbatim, a string or list schema made
            # a `Dataset` violating its own declared type. Downstream it still
            # failed CLOSED -- `check_schema_drift` hashed it, the hash did not
            # match, and the run aborted `schema_drift` -- but told the
            # operator "the schema does not match" when the truth is "the
            # platform sent something that is not a schema". An ABSENT schema
            # stays `None`: that is its own, already-distinct abort.
            raise DatasetFetchFailedError(
                "get_dataset: the dataset's expectedOutputSchema is not a JSON object"
            )

        items = self._fetch_all_dataset_items(name, encoded_name, dataset_id)
        self._cached_dataset_name = name
        return {"items": items, "expected_output_schema": schema}

    def _fetch_all_dataset_items(
        self, name: str, encoded_name: str, dataset_id: Any
    ) -> list[DatasetItem]:
        """R1 (Atchim, critical): items come from the separate, paginated
        ``GET /api/public/dataset-items?datasetName=`` endpoint — the
        ``GET /api/public/v2/datasets/{name}`` response carries NO ``items``
        key on Langfuse 4.38.0 (live-probed 2026-09-19)."""
        items: list[DatasetItem] = []
        self._item_cache = {}
        page = 1
        total_pages = 1
        while page <= total_pages:
            if page > _MAX_DATASET_PAGES:
                raise DatasetFetchFailedError(
                    f"dataset-items pagination exceeded the page cap ({_MAX_DATASET_PAGES})"
                )
            path = f"/api/public/dataset-items?datasetName={encoded_name}&page={page}"
            try:
                status, body = self._client.request("GET", path)
            except TransportError as exc:
                raise DatasetFetchFailedError(
                    f"dataset-items fetch transport failure: {exc}"
                ) from exc
            if status >= 400:
                logger.error(
                    "dataset_fetch_failed status=%s dataset=%s detail=%s",
                    status,
                    sanitize_for_log(name),
                    _body_snippet_for_error(body),
                )
                raise DatasetFetchFailedError(f"dataset-items fetch failed with HTTP {status}")
            if not isinstance(body, dict):
                raise DatasetFetchFailedError("dataset-items returned an unexpected body shape")

            data = body.get("data")
            if not isinstance(data, list):
                raise DatasetFetchFailedError(
                    "dataset-items response 'data' is not a list (untrusted shape)"
                )
            meta = body.get("meta")
            if not isinstance(meta, dict):
                raise DatasetFetchFailedError("dataset-items response is missing 'meta'")
            raw_total_pages = meta.get("totalPages")
            if (
                not isinstance(raw_total_pages, int)
                or isinstance(raw_total_pages, bool)
                or raw_total_pages < 0
            ):
                # 0 is a legitimate shape (an empty dataset, live-confirmed
                # on Langfuse 4.38.0) -- only a negative or non-int value
                # is untrusted/malformed.
                raise DatasetFetchFailedError(
                    "dataset-items response 'meta.totalPages' is not a non-negative integer"
                )

            for raw_item in data:
                try:
                    item_id = raw_item["id"]
                    document_id = raw_item["input"]["document_id"]
                    golden = raw_item["expectedOutput"]
                except (KeyError, TypeError) as exc:
                    raise DatasetFetchFailedError(
                        f"malformed dataset item (missing {exc})"
                    ) from exc
                if not isinstance(item_id, str):
                    # FU-01.3-I / QA-01 re-audit #4 F-1: `DatasetItem`
                    # declares TWO `str` fields (`item_id`, `document_id`)
                    # -- FU-01.3-G guarded only `document_id` and left
                    # this one, the identical shape, unguarded one
                    # boundary over. Must raise BEFORE `items.append` and
                    # BEFORE `self._item_cache[item_id] = dataset_id`
                    # below, or a non-hashable `item_id` reaches the
                    # cache assignment and raises an untyped `TypeError:
                    # unhashable type` instead. INV-02: name the field
                    # only, never the offending value.
                    raise DatasetFetchFailedError(
                        "malformed dataset item: 'item_id' is not a string"
                    )
                if not isinstance(document_id, str):
                    # FU-01.3-G / QA-01 re-audit #3 F-1 / REG-09
                    # (reopened): the OTHER trust boundary -- the golden
                    # schema (CT-05) guards `expectedOutput`, not
                    # `input`, so a malformed platform item otherwise
                    # flows in here and back out untyped (REG-04
                    # family). INV-02: name the field only, never the
                    # offending value.
                    raise DatasetFetchFailedError(
                        "malformed dataset item: 'document_id' is not a string"
                    )
                if not isinstance(golden, dict):
                    # FO-7 third leg (DEBT-48): `golden` (`raw_item
                    # ["expectedOutput"]`) had NO isinstance/type check
                    # anywhere in this module -- the committed CT-05
                    # schema guards `expectedOutput` on WRITE, never on
                    # READ, so a malformed platform item flowed straight
                    # through into the DatasetItem and back out. Type-only
                    # guard, deliberately NOT schema validation on read
                    # (a larger design decision, out of scope here). Must
                    # raise BEFORE `items.append` and BEFORE
                    # `self._item_cache[item_id] = dataset_id` below, same
                    # placement as its `item_id`/`document_id` siblings.
                    # INV-02: name the field only, never the value.
                    raise DatasetFetchFailedError(
                        "malformed dataset item: 'golden' (expectedOutput) is not an object"
                    )
                golden = cast(Any, golden)  # narrowed to dict[Any, Any] by isinstance above
                items.append(
                    {"item_id": item_id, "document_id": document_id, "golden": golden}
                )
                self._item_cache[item_id] = dataset_id

            total_pages = raw_total_pages
            page += 1
        return items

    def _check_record_deadline(
        self, *, deadline: float | None, document_id: str, score_name: str
    ) -> None:
        """DEBT-20: the whole-record-phase deadline, checked between
        scores AND between retry attempts (INV-07 monotonic). A no-op
        when disabled (``deadline is None``)."""
        if deadline is not None and self._clock() >= deadline:
            raise ScoreWriteFailedError(
                "write_scores: record-phase deadline exceeded before writing "
                f"score {score_name!r} for document_id={document_id!r}"
            )

    def _write_scores(
        self,
        *,
        trace_id: str,
        document_id: str,
        scores: list[ScoreInput],
        deadline: float | None = None,
    ) -> None:
        """Adapter-private (ADR-0005 #9 — no longer on the Protocol). Called
        by ``record_run`` once a real, ingested ``trace_id`` is known for
        the document (never the deterministic pre-#9 ``trace_id()``, which
        ``mark_run_status`` still uses for its own sentinel trace).

        ``deadline`` (DEBT-20) is an absolute ``self._clock()``-scale
        value, shared across every record in the same ``record_run`` call
        -- computed once by the caller, not reset per record."""
        for score in scores:
            self._check_record_deadline(
                deadline=deadline, document_id=document_id, score_name=score["name"]
            )
            self._write_score_with_retry(
                trace_id=trace_id, document_id=document_id, score=score, deadline=deadline
            )

    def _write_score_with_retry(
        self,
        *,
        trace_id: str,
        document_id: str,
        score: ScoreInput,
        deadline: float | None = None,
    ) -> None:
        """REG-03/F-2: a bounded retry (ADR-0004 #3 backoff shape). The
        score_id is deterministic (ADR-0005 #5), so every retried attempt
        re-sends the exact same payload -- an upsert, never a duplicate.
        Retries only 5xx and TransportError (transient); a 4xx is a
        caller/contract bug and is never retried."""
        payload = {
            "id": score["id"],
            "name": score["name"],
            "value": score["value"],
            "comment": score.get("comment"),
            "traceId": trace_id,
            "dataType": "CATEGORICAL",
        }
        last_status: int | None = None
        last_body: Any = None
        for attempt in range(1, self._score_write_max_attempts + 1):
            self._check_record_deadline(
                deadline=deadline, document_id=document_id, score_name=score["name"]
            )
            try:
                status, body = self._client.request("POST", "/api/public/scores", payload)
            except TransportError as exc:
                if attempt >= self._score_write_max_attempts:
                    logger.error(
                        "score_write_failed status=transport document_id=%s score_name=%s "
                        "attempts=%s detail=%s",
                        sanitize_for_log(document_id),
                        sanitize_for_log(score["name"]),
                        attempt,
                        sanitize_for_log(str(exc)),
                    )
                    raise ScoreWriteFailedError(
                        f"write_scores transport failure for {score['name']!r} "
                        f"after {attempt} attempts"
                    ) from exc
                self._sleep(self._backoff_delay_seconds(attempt))
                continue

            if status < 400:
                return
            if status < 500:
                # 4xx is a caller/contract bug (e.g. malformed payload) --
                # never retried, exactly one attempt.
                logger.error(
                    "score_write_failed status=%s document_id=%s score_name=%s detail=%s",
                    status,
                    sanitize_for_log(document_id),
                    sanitize_for_log(score["name"]),
                    _body_snippet_for_error(body),
                )
                raise ScoreWriteFailedError(
                    f"write_scores failed for {score['name']!r} with HTTP {status}"
                )

            last_status, last_body = status, body
            if attempt >= self._score_write_max_attempts:
                break
            self._sleep(self._backoff_delay_seconds(attempt))

        logger.error(
            "score_write_failed status=%s document_id=%s score_name=%s attempts=%s detail=%s",
            last_status,
            sanitize_for_log(document_id),
            sanitize_for_log(score["name"]),
            self._score_write_max_attempts,
            _body_snippet_for_error(last_body),
        )
        raise ScoreWriteFailedError(
            f"write_scores failed for {score['name']!r} with HTTP {last_status} "
            f"after {self._score_write_max_attempts} attempts"
        )

    def _backoff_delay_seconds(self, attempt: int) -> float:
        """Exponential full jitter (ADR-0004 #3): uniform(0, min(cap, base * 2**(attempt-1)))."""
        ceiling: float = min(
            self._score_write_backoff_cap_seconds,
            self._score_write_backoff_base_seconds * (2 ** (attempt - 1)),
        )
        jitter: float = self._random()
        return jitter * ceiling

    def record_run(
        self,
        *,
        dataset_name: str,
        run_name: str,
        run_id: str,
        records: list[DocumentRecord],
        metadata: RunMetadata,
    ) -> None:
        """ADR-0005 Decision #9: record a complete run once, after every
        gate is already known. No retry — see the ADR for why (a failed
        OTLP batch has already exhausted the exporter's own retries; a
        failed run_experiment is not safely re-runnable per run_name).
        """
        if self._tracing_client is None:
            raise TracingNotConfiguredError(
                "record_run requires a tracing_client (OTLP/v4 SDK) — none configured"
            )
        if dataset_name != self._cached_dataset_name:
            raise ExperimentRecordFailedError(
                f"record_run: dataset_name {dataset_name!r} does not match the dataset "
                f"{self._cached_dataset_name!r} last fetched by get_dataset() — call "
                "get_dataset(dataset_name) first, in this same run"
            )

        # FO-1 (DEBT-48): validate metadata's shape here too, at the very
        # TOP of record_run alongside the record-shape loop below -- it
        # used to be subscripted only much later, while building the
        # record_experiment(...) call's metadata dict, deep past every
        # other precondition.
        _require_run_metadata_shape(metadata)

        # FU-01.3-D / QA-01 re-audit F-1 (REG-09 widened): validate EVERY
        # record's shape here, at the very TOP of record_run, before ANY
        # subscript of ANY record below -- the seam where
        # record["item_id"] used to be subscripted seventeen lines before
        # this guard ran is now physically impossible, not merely patched
        # at that call site.
        for record in records:
            _require_record_shape(record)

        record_item_ids = [record["item_id"] for record in records]
        if len(record_item_ids) != len(set(record_item_ids)):
            raise ExperimentRecordFailedError("record_run: duplicate item_id in records")
        if set(record_item_ids) != set(self._item_cache.keys()):
            raise ExperimentRecordFailedError(
                "record_run: records' item_ids do not exactly match the fetched dataset items "
                "(call get_dataset(dataset_name) first, in this same run)"
            )

        # ADR-0005 #9 amendment A3 (Soneca, 2026-09-20): run_id is verified,
        # not decorative. N26's cross-invocation no-overwrite guarantee rests
        # on every scores[*].id having been derived from THIS run_id — score
        # ids are the upsert key, so ids belonging to another invocation would
        # overwrite that run's scores and still record as correct. A pure
        # local loop over data already in hand: no extra call, no network.
        # INV-02: the raise names document_id + score_name only (both
        # value-free by construction) and never the offending id pair.
        # (Shape already validated above -- this loop only derives ids.)
        for record in records:
            document_id = record["document_id"]
            for score in record.get("scores", []):
                score_name = score["name"]
                if score["id"] != score_id(
                    run_id=run_id, document_id=document_id, score_name=score_name
                ):
                    raise ExperimentRecordFailedError(
                        "record_run: a score id was not derived from the run_id passed in "
                        f"this call (document_id={document_id!r}, "
                        f"score_name={score_name!r}) — every score id must be "
                        "score_id(run_id, document_id, score_name) for this same run (N26)"
                    )

        records_by_item_id = {record["item_id"]: record for record in records}
        task_failed = False

        def task(*, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
            # A total function that cannot raise (ADR-0005 #9 defense in
            # depth): str(exception) must never reach a span attribute.
            #
            # ⚠️ DEBT-18 option B REVERSED 2026-09-25 (user decision). The
            # span still carries the verdict map keyed by score name --
            # that part is the stable shape (INV-03) and is unchanged --
            # and, when the record carries `verdicts`, an additional
            # `detail` key with the expected/actual/confidence behind
            # every one of them. A record without `verdicts`
            # (`--platform-values verdicts-only`) reproduces option B's
            # payload exactly.
            nonlocal task_failed
            try:
                record = records_by_item_id[item.id]
                output: dict[str, Any] = {
                    score["name"]: score["value"] for score in record["scores"]
                }
                verdicts = record.get("verdicts")
                if verdicts:
                    output["detail"] = _leaf_values(verdicts)
                return output
            except Exception:  # noqa: BLE001 - intentional total catch, no exception text kept
                task_failed = True
                return {"record_error": "task_failed"}

        experiment_items = [
            ExperimentItem(
                id=item_id,
                dataset_id=self._item_cache[item_id],
                input={"document_id": records_by_item_id[item_id]["document_id"]},
                # DEBT-18 REVERSED 2026-09-25: the expected values are
                # carried here too when the record has them, so the
                # platform UI shows input / expected / output side by side
                # without a reader opening the dataset item. Still `{}`
                # under `--platform-values verdicts-only`.
                expected_output=_expected_output(records_by_item_id[item_id]),
            )
            for item_id in record_item_ids
        ]

        trace_ids = record_experiment(
            self._tracing_client,
            run_name=run_name,
            items=experiment_items,
            task=task,
            metadata={
                "action_id": metadata["action_id"],
                "action_version": metadata["action_version"],
                "golden_version": metadata["golden_version"],
                "golden_dataset_name": metadata["golden_dataset_name"],
            },
        )

        if task_failed:
            raise ExperimentRecordFailedError(
                "record_run: the total task caught an unexpected exception for at least one item"
            )

        # DEBT-20: one deadline for the WHOLE record phase (every record,
        # every score, every retry attempt below) -- computed once here,
        # not reset per record. Disabled (None) unless a caller opted in.
        deadline = (
            None
            if self._record_deadline_seconds is None
            else self._clock() + self._record_deadline_seconds
        )
        for record in records:
            trace_id = trace_ids[record["item_id"]]
            self._write_scores(
                trace_id=trace_id,
                document_id=record["document_id"],
                scores=record["scores"],
                deadline=deadline,
            )

    def mark_run_status(
        self,
        run_id: str,
        # DEBT-53 (prose) F-5: was a hand-written `Literal["aborted",
        # "complete"]`, four lines under a comment claiming "never
        # hand-written" -- `_VALID_RUN_STATUSES` already derives from
        # this same `RunStatus` alias (types.py), so the parameter now
        # names it instead of re-declaring its members. One token,
        # matches the Protocol's own signature (types.py:126).
        status: RunStatus,
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
        golden_dataset_name: str,
    ) -> None:
        # FO-3 (DEBT-48): `RunStatus` was enforced nowhere -- any string
        # POSTed verbatim as ADR-0004 #14's marker value, which a reader
        # keys on; an off-allowlist value read as "valid" forever.
        # Mirrors FO-4's fix exactly: the valid set is derived from the
        # Literal itself (`_VALID_RUN_STATUSES`, module level), never
        # hand-written, and this reuses the module's own existing typed
        # error for this call rather than introducing a second one.
        # Same sweep finding, same call: run_id/action_id/action_version/
        # golden_version/golden_dataset_name (A6/DEBT-48, all declared
        # `str`) are interpolated into `comment` below, which IS written
        # -- unchecked until now.
        # Derived from THIS method's own declared type hints
        # (`typing.get_type_hints`), not a hand-written parameter list,
        # so a future `str` parameter on this signature auto-extends the
        # check. `locals()` here captures exactly the bound parameters
        # (nothing else has been assigned yet).
        if status not in _VALID_RUN_STATUSES:
            # INV-02: name the parameter, never interpolate the value.
            raise RunStatusWriteFailedError(
                "mark_run_status: status must be one of the recognised run_status values"
            )
        local_values = locals()
        for name, hint in get_type_hints(LangfuseAdapter.mark_run_status).items():
            # Suggestion (fix-round finding): `get_type_hints` includes the
            # signature's `return` annotation -- today it resolves to
            # `NoneType`, so `hint is str` already filters it out, but a
            # future `-> str` on this signature would otherwise make
            # `local_values.get("return")` resolve to `None` and raise
            # unconditionally on every call. `name != "return"` guards
            # against that regardless of the return annotation.
            if name != "return" and hint is str and not isinstance(local_values.get(name), str):
                # INV-02: name the parameter, never interpolate the value.
                raise RunStatusWriteFailedError(f"mark_run_status: {name!r} must be a string")

        comment = (
            f"action_id={action_id} action_version={action_version} "
            f"golden_version={golden_version} golden_dataset_name={golden_dataset_name}"
        )
        run_status_id = score_id(
            run_id=run_id,
            document_id=RUN_LEVEL_TRACE_SENTINEL,
            score_name=_RUN_STATUS_SCORE_NAME,
        )
        run_status_trace_id = trace_id(run_id=run_id, document_id=RUN_LEVEL_TRACE_SENTINEL)
        resp_status, body = self._client.request(
            "POST",
            "/api/public/scores",
            {
                "id": run_status_id,
                "name": _RUN_STATUS_SCORE_NAME,
                "value": status,
                "comment": comment,
                "traceId": run_status_trace_id,
                "dataType": "CATEGORICAL",
            },
        )
        if resp_status >= 400:
            logger.error(
                "run_status_write_failed status=%s run_id=%s detail=%s",
                resp_status,
                sanitize_for_log(run_id),
                _body_snippet_for_error(body),
            )
            raise RunStatusWriteFailedError(f"mark_run_status failed with HTTP {resp_status}")


def _is_local_dev_host(hostname: str) -> bool:
    """True only for a host that names THIS machine -- ``localhost`` or a
    loopback address. The only case where an unencrypted ``http://``
    LANGFUSE_HOST is legitimate (this project runs a local, self-hosted
    Langfuse per CLAUDE.md External services)."""
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_host_url(var_name: str, value: str) -> None:
    """FO-9 (DEBT-48; QA-01 S-01.4 F-4 fix round, 2026-09-22) -- fail
    closed on a malformed/unscoped host env var, before it reaches either
    transport. Same threat family as the LANGFUSE_BASE_URL split-brain
    guard and both transports' cross-host redirect refusal: a credential
    reaching a host the caller did not choose (REG-07/REG-10).

    Requires:
    - an ``https://`` scheme, OR ``http://`` but ONLY for an explicit
      local-development host (``localhost`` or a loopback address) --
      any other ``http://`` host would send the platform API key
      unencrypted to a network-reachable host;
    - a non-empty host component;
    - NO embedded userinfo (a host string can itself carry a credential,
      e.g. ``https://user:pass@host`` -- a prior probe confirmed that
      exact shape reaches this path);
    - no leading/trailing whitespace, no control characters (incl. a
      trailing newline) anywhere in the raw value. This check runs
      BEFORE ``urlsplit`` deliberately: Python's ``urlsplit`` silently
      strips ``\\t``/``\\n``/``\\r`` from the input (bpo-43882), so a
      value like ``"http://evil.example\\n"`` would otherwise parse as
      the clean, acceptable-looking ``http://evil.example``.

    INV-02: the error names ``var_name`` only, never ``value`` -- a
    malformed host can be malformed *because* it embeds a credential.
    """

    def _reject() -> None:
        raise PlatformConfigurationError(
            f"make_platform: {var_name} is not a well-formed, safe host URL "
            "-- refusing to send credentials to it. It must have an "
            "https:// scheme (http:// only for an explicit localhost/"
            "loopback host), a non-empty host, no embedded userinfo, and "
            f"no whitespace or control characters (credentials do not "
            f"belong in {var_name})."
        )

    if value.strip() != value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        _reject()

    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname
    scheme_ok = parsed.scheme == "https" or (
        parsed.scheme == "http" and hostname is not None and _is_local_dev_host(hostname)
    )
    is_valid = (
        scheme_ok
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
    )
    if not is_valid:
        _reject()


def make_platform() -> PlatformAdapter:
    """Factory reading ``PLATFORM`` from env (ADR-0001). Assumes the caller
    already called ``load_dotenv()`` (INV-05 — this module never does).

    Constructs both the raw-REST ``HttpClient`` (datasets/schema/scores)
    and the ``langfuse`` SDK's OTel-based client (T-01.3.10a trace +
    dataset-run linkage) — the only Langfuse SDK import in this codebase,
    confined here per NFR N24.
    """
    platform = os.environ.get("PLATFORM", "langfuse")
    if platform != "langfuse":
        raise ValueError(f"unsupported PLATFORM: {platform!r}")
    host = os.environ["LANGFUSE_HOST"]
    _validate_host_url("LANGFUSE_HOST", host)
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ["LANGFUSE_SECRET_KEY"]

    # Atchim PIN 2026-09-20 (new REG): the langfuse SDK's own base_url
    # resolution prioritizes LANGFUSE_BASE_URL over the explicit `host=`
    # constructor arg below -- if it's set and disagrees with
    # LANGFUSE_HOST (the only variable CLAUDE.md's External services
    # table documents), the SDK client would silently authenticate
    # against a host the operator never named via LANGFUSE_HOST, split
    # traffic across two platform instances with no marker (DEBT-28-like),
    # and leak credentials to an undeclared host (REG-07's class, arriving
    # via env instead of a 3xx). Fail closed, before either client is
    # constructed. Names only in the message -- never the values, since a
    # URL can embed a credential (INV-02).
    base_url_override = os.environ.get("LANGFUSE_BASE_URL")
    if base_url_override is not None and base_url_override != host:
        raise PlatformConfigurationError(
            "make_platform: LANGFUSE_BASE_URL is set and disagrees with LANGFUSE_HOST -- "
            "refusing to construct a platform client that would silently split traffic "
            "and credentials across two hosts. Unset LANGFUSE_BASE_URL or make it match "
            "LANGFUSE_HOST."
        )

    client = UrllibHttpClient(host=host, public_key=public_key, secret_key=secret_key)

    from langfuse import Langfuse  # local import: confine the SDK to this factory

    # base_url=host, belt-and-braces alongside the fail-closed check above:
    # passing it explicitly makes the SDK resolve to `host` regardless of
    # LANGFUSE_BASE_URL's env precedence, for the (already-refused-if-
    # disagreeing) case where it's unset or equal.
    sdk_client = Langfuse(host=host, base_url=host, public_key=public_key, secret_key=secret_key)
    return cast(
        PlatformAdapter,
        LangfuseAdapter(
            client=client,
            tracing_client=cast(ExperimentRunner, sdk_client),
            record_deadline_seconds=_record_deadline_seconds_from_env(),
        ),
    )


#: DEBT-53 (table, HARDEN-01 GAP-3): DEBT-20's monotonic record-phase
#: deadline mechanism landed in the constructor but shipped disabled --
#: `make_platform()` never passed it, so a hung record phase had NO bound
#: in production even though the control existed in code. Wired here with
#: a GENEROUS default (well above Branca's ~63h worst-case bound at N8's
#: 50-document golden-set ceiling): a low guess would convert a
#: slow-but-alive platform from *stretching* a run into *aborting* one
#: that would otherwise have succeeded, on a CI gate whose entire value
#: is being trusted. Provisional, same as the mechanism itself -- pin the
#: real value once S-01.6 gives live timings.
_DEFAULT_RECORD_DEADLINE_SECONDS = 259_200.0  # 72h


def _record_deadline_seconds_from_env() -> float:
    """Reads ``LANGFUSE_RECORD_DEADLINE_SECONDS`` (optional -- not in
    ``REQUIRED_ENV_VARS``), falling back to the generous default above.
    An unparsable or non-positive value fails closed rather than silently
    disabling the deadline it was meant to arm."""
    raw = os.environ.get("LANGFUSE_RECORD_DEADLINE_SECONDS")
    if raw is None:
        return _DEFAULT_RECORD_DEADLINE_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise PlatformConfigurationError(
            "make_platform: LANGFUSE_RECORD_DEADLINE_SECONDS is set but is not a "
            "valid number"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise PlatformConfigurationError(
            "make_platform: LANGFUSE_RECORD_DEADLINE_SECONDS must be a finite, "
            "positive number of seconds"
        )
    return value
