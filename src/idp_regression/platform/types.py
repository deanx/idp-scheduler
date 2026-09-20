"""PlatformAdapter Protocol + wire-level shapes.

ADR-0005 Decision #9 (record-after experiment linkage, closes Atchim
S-01.3 R6): the interface is a *recorder*, invoked once after every
per-document gate is already known. ``write_scores``, ``flush`` and
``run_dataset_experiment`` are no longer part of the public Protocol —
they are adapter-private internals behind ``record_run``.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict

from idp_regression.classifier.types import Golden

RunStatus = Literal["aborted", "complete"]


class DatasetItem(TypedDict):
    """One golden dataset item — the platform's opaque ``item_id`` plus
    the ``document_id`` + curated ``Golden`` (ADR-0005 #9)."""

    item_id: str
    document_id: str
    golden: Golden


class Dataset(TypedDict):
    """The fetched dataset — items plus the platform-stored schema.

    ``expected_output_schema`` is returned verbatim; an absent schema is
    ``None``, not raised (the orchestrator turns ``None`` into
    ``schema_drift``, ADR-0005 Decision #8).
    """

    items: list[DatasetItem]
    expected_output_schema: NotRequired[dict[str, Any] | None]


class ScoreInput(TypedDict):
    """A single score write — ``id`` is the deterministic score_id (T-01.3.3)."""

    id: str
    name: str
    value: str
    comment: NotRequired[str | None]


class DocumentRecord(TypedDict):
    """One document's complete, already-gated result (ADR-0005 #9) — built
    by the orchestrator's in-process loop, with no platform write until
    ``record_run`` is called once after the loop.

    DEBT-18 (user decision, option B): no extracted (actual), expected, or
    confidence value is ever written by the adapter — the golden lives
    only in its Langfuse dataset item, which is where it must be.
    ``actual`` (the ``NormalizedOutput`` the classifier compared against
    the golden) is deliberately NOT carried here: nothing on the platform
    side reads it any more, so it's never even constructed as sensitive
    dead weight. ``scores`` (built by ``build_score_inputs``, verdict
    literals + no comment) is the only per-document platform-bound data.
    """

    item_id: str
    document_id: str
    scores: list[ScoreInput]


class RunMetadata(TypedDict):
    """Run-level metadata recorded with every completed run (INV-04)."""

    action_id: str
    action_version: str
    golden_version: str


class PlatformAdapter(Protocol):
    """The interface of record (ADR-0001, reshaped by ADR-0005 #9),
    swappable per NFR N24."""

    def get_dataset(self, name: str) -> Dataset:
        """Fetch the golden dataset. Raises ``DatasetFetchFailedError`` on
        network/404/auth failure. An absent items list is a *valid* empty
        dataset (the orchestrator maps that to ``empty_set``), not an error.
        """
        ...

    def record_run(
        self,
        *,
        dataset_name: str,
        run_name: str,
        run_id: str,
        records: list[DocumentRecord],
        metadata: RunMetadata,
    ) -> None:
        """Record a complete run once, after every gate is already known:
        an experiment visible in the Experiments tab, plus per-document
        scores on each item's real trace id. Raises
        ``ExperimentRecordFailedError`` | ``ScoreWriteFailedError`` |
        ``FlushFailedError``. Never retried by the adapter (ADR-0005 #9).

        Obligation on ``run_id`` (ADR-0005 #9 amendment A3, 2026-09-20):
        every ``scores[*].id`` MUST be derived from the ``run_id`` passed
        in this same call. That is what makes two invocations under the
        same ``run_name`` unable to overwrite each other's scores (N26) —
        score ids are the upsert key. The derivation itself is each
        adapter's own (this Protocol does not mandate a scheme, so an
        adapter that derives ids differently stays implementable, N24).
        An implementation MAY verify the obligation, and MUST raise
        ``ExperimentRecordFailedError`` if it verifies and the check fails.
        """
        ...

    def mark_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        action_id: str,
        action_version: str,
        golden_version: str,
    ) -> None:
        """Write the run_status metadata marker (ADR-0004 #14)."""
        ...
