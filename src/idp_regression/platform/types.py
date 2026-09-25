"""PlatformAdapter Protocol + wire-level shapes.

ADR-0005 Decision #9 (record-after experiment linkage, closes Atchim
S-01.3 R6): the interface is a *recorder*, invoked once after every
per-document gate is already known. ``write_scores``, ``flush`` and
``run_dataset_experiment`` are no longer part of the public Protocol —
they are adapter-private internals behind ``record_run``.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict

from idp_regression.classifier.types import Golden, VerdictMap

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

    ⚠️ **DEBT-18 REVERSED 2026-09-25 (user decision).** Option B (2026-09-19)
    kept every extracted/expected/confidence value off the platform, so a
    run recorded verdict literals and nothing else. The user has reversed
    that: *"we need to have as much information as possible at Langfuse,
    as Langfuse is the information point here"*, with PII handled where it
    belongs — IDP can be configured not to parse it, and a filter or a
    later deletion pass can run over the platform.

    ``verdicts`` carries the full CT-02 map (verdict + expected + actual +
    confidence per leaf) when the run is recording values, and is ``None``
    when it is not (``--platform-values verdicts-only``, which reproduces
    option B's payload exactly). The orchestrator decides; the adapter only
    renders what it is given, so the policy lives in ONE place.

    What this changes: a self-hosted platform instance now holds the same
    sensitive financial values ``CLAUDE.md ## Domain`` names. The
    self-hosting obligations already recorded there (encryption at rest,
    DB access control, backup — ADR-0001/N25) stop being paperwork and
    become the control.
    """

    item_id: str
    document_id: str
    scores: list[ScoreInput]
    verdicts: NotRequired[VerdictMap | None]


class RunMetadata(TypedDict):
    """Run-level metadata recorded with every completed run (INV-04,
    widened to four fields by ADR-0004 amendment T-01.4.12 A6 / DEBT-48).

    ``golden_dataset_name`` is the named golden set this run was measured
    against -- ``golden_version`` is a content hash (proves *what* was
    compared) but cannot on its own tell a reader *which* named dataset
    it came from. INV-04 now requires all four fields on every zero-exit
    run."""

    action_id: str
    action_version: str
    golden_version: str
    golden_dataset_name: str


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
        golden_dataset_name: str,
    ) -> None:
        """Write the run_status metadata marker (ADR-0004 #14, widened to
        four fields by amendment A6 / DEBT-48)."""
        ...
