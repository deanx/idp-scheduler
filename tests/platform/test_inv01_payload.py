"""INV-01 — document files never enter the evaluation platform.

Every payload the platform module sends over the wire (dataset fetch is
read-only; score writes and the run_status marker are the write paths)
must carry only ``document_id`` + golden-derived content — never file
bytes or a file-path blob.
"""

from __future__ import annotations

import re
from typing import Any

from idp_regression.platform.langfuse_adapter import LangfuseAdapter
from idp_regression.platform.scoring import build_score_inputs

_FILE_PATH_LIKE = re.compile(r"(/[\w.\-]+){2,}|\.pdf\b|\.png\b|\.jpg\b")


class RecordingHttpClient:
    def __init__(self) -> None:
        self.bodies: list[Any] = []

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.bodies.append(body)
        return 200, {"id": "x"}


def test_write_scores_payload_never_carries_a_file_path_or_bytes() -> None:
    client = RecordingHttpClient()
    adapter = LangfuseAdapter(client=client)
    scores = build_score_inputs(
        golden={
            "document_id": "invoice-007.pdf",
            "fields": {"total": {"value": "1250.00", "type": "number", "critical": True}},
        },
        verdicts={
            "total": {
                "verdict": "match",
                "expected": "1250.00",
                "actual": "1250.00",
                "confidence": 0.9,
                "critical": True,
                "type": "number",
            }
        },
        gate="PASS",
        run_id="run-1",
        document_id="invoice-007.pdf",
    )

    adapter.write_scores(run_id="run-1", document_id="invoice-007.pdf", scores=scores)

    for body in client.bodies:
        assert isinstance(body, dict)
        # only score fields — no file-path-like blob anywhere in the payload
        for value in body.values():
            if isinstance(value, str):
                assert not _FILE_PATH_LIKE.search(value), f"file-path-like content in {value!r}"
        assert "file" not in body
        assert "path" not in body
        assert "bytes" not in body


def test_mark_run_status_payload_never_carries_a_file_path_or_bytes() -> None:
    client = RecordingHttpClient()
    adapter = LangfuseAdapter(client=client)

    adapter.mark_run_status(
        "run-1",
        "complete",
        action_id="action-1",
        action_version="v1",
        golden_version="deadbeef",
    )

    for body in client.bodies:
        assert "file" not in body
        assert "path" not in body
        assert "bytes" not in body
