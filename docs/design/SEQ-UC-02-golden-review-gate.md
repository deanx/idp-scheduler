# SEQ-UC-02: Two-stage console validation with a golden review/approve gate

```mermaid
sequenceDiagram
    actor Curator
    participant UI as Console UI
    participant API as ui/api.py
    participant Jobs as ui/jobs.py (JobRegistry)
    participant Pin as pin_document.py (subprocess)
    participant Verify as verify_document.py (subprocess)
    participant Platform as Evaluation platform (dataset)

    Curator->>UI: Upload corpus ZIP
    UI->>API: POST /api/uploads (validate + unpack)
    API-->>UI: document_dir, archive_sha256

    Curator->>UI: Plan stage 1 (draft)
    UI->>API: POST /api/workflows/draft-golden/plan
    API-->>UI: real N-extraction cost

    Curator->>UI: Approve stage 1 (echo N)
    UI->>API: POST /api/workflows/draft-golden/start {approved_extractions: N}
    API->>Jobs: acquire workspace lock, start job
    Jobs->>Pin: pin_document.py --document-dir ... --version trusted --yes
    Pin->>Platform: upsert dataset items (drafted golden)
    Pin-->>Jobs: exit 0
    Jobs->>Jobs: write ReviewSession{stage1_job_id, archive_sha256, document_dir}
    Jobs->>Jobs: release workspace lock (job terminal)

    Curator->>UI: Open review screen
    UI->>API: GET /api/reviews/{session_id}
    API->>Platform: read drafted dataset items
    API-->>UI: per-document drafted fields

    alt Curator edits a value
        Curator->>UI: Edit field
        UI->>API: PATCH /api/reviews/{session_id}/items/{document_id}
        API->>API: validate against golden schema
        API->>Platform: upsert (deterministic id)
    else Curator uploads own golden.json
        Curator->>UI: Upload golden.json
        UI->>API: POST /api/reviews/{session_id}/replace
        API->>API: schema-validate whole file (all-or-nothing)
        API->>Platform: upsert all items
    else Curator accepts as-is
        Note over Curator,UI: no write — drafted values stand
    end

    Curator->>UI: Mark review complete
    UI->>API: POST /api/reviews/{session_id}/complete
    API->>Platform: read current dataset items
    API->>API: compute approved_golden_hash, write to ReviewSession, state=reviewed
    Note over API: closes the R1 gap — binds stage 2 to THIS golden, not whatever's on the platform later

    Curator->>UI: Plan stage 2 (verify)
    UI->>API: POST /api/workflows/verify-candidate/plan {session_id}
    API->>API: re-check archive_sha256 + document_dir match session
    API-->>UI: real N-extraction cost (fresh plan, not stage 1's number)

    Curator->>UI: Approve stage 2 (echo N)
    UI->>API: POST /api/workflows/verify-candidate/start {session_id, approved_extractions: N}
    API->>Platform: re-read dataset items, re-hash
    API->>API: refuse (409) if mismatch, if session/archive stale, or if golden hash != approved_golden_hash (INV-09 a-d)
    API->>Jobs: acquire workspace lock, start job
    Jobs->>Verify: verify_document.py --document-dir ... --version candidate --yes
    Verify->>Platform: read goldens, run gate, record run
    Verify-->>Jobs: exit code (gate result)
    Jobs->>Jobs: release workspace lock

    UI->>Curator: STILL VALID / CHANGED / RUN FAILED, linked to run artifact
```

Notes:
- The review step (between the two `alt` blocks and the two approvals) holds **no** workspace lock and spends **no** quota — see ADR-0008 "Why does the review step hold no workspace lock?".
- `ReviewSession` (CT-06) is what lets stage 2 be started in a later console session — the diagram's gap between "release workspace lock" and "Curator opens review screen" may span a restart.
- INV-09 is enforced at the `verify-candidate/start` boundary, not earlier — a stale or tampered session is caught at the last possible moment, right before quota would be spent.
- The explicit "review complete" step and its `approved_golden_hash` capture were added after Atchim's round-1 review (R1): without it, an **accept-as-is** review writes nothing to the platform, so nothing would bind stage 2 to the specific golden the curator actually looked at — a second workspace or operator overwriting the same platform item during the pause would otherwise go undetected.
