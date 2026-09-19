"""PlatformAdapter Protocol + wire-level shapes (ADR-0001 interface, unchanged
by ADR-0005 except widening ``get_dataset``'s return value)."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict

from idp_regression.classifier.types import Golden

RunStatus = Literal["aborted", "complete"]


class DatasetItem(TypedDict):
    """One golden dataset item — ``document_id`` + the curated ``Golden``."""

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


class PlatformAdapter(Protocol):
    """The interface of record (ADR-0001), swappable per NFR N24."""

    def get_dataset(self, name: str) -> Dataset:
        """Fetch the golden dataset. Raises ``DatasetFetchFailedError`` on
        network/404/auth failure. An absent items list is a *valid* empty
        dataset (the orchestrator maps that to ``empty_set``), not an error.
        """
        ...

    def write_scores(self, run_id: str, document_id: str, scores: list[ScoreInput]) -> None:
        """Write scores for one document. Deterministic ids make this
        idempotent — the orchestrator may retry on 5xx within its budget.
        Raises ``ScoreWriteFailedError`` on failure.
        """
        ...

    def flush(self) -> None:
        """Flush any buffered platform writes (trace export). Never
        best-effort — raises ``FlushFailedError`` on failure so the
        orchestrator can map it to abort reason ``flush_failed``.
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
