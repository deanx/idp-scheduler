# Sequence — UC-01 baseline regression run

Mermaid sequence for the happy path + the abort-on-failure path (ASM-02) + the timeout path (A1/AC5).

```mermaid
sequenceDiagram
    autonumber
    actor PE as Prompt Engineer / CI
    participant ORC as Orchestrator (run_eval)
    participant ENV as env (load_dotenv)
    participant PLAT as PlatformAdapter (Langfuse)
    participant IDP as IDPAdapter (MuleSoft)
    participant NORM as normalize()
    participant CLS as classifier
    participant GATE as overall_gate

    PE->>ORC: run_eval --version <v> --run <name> [--action <id>]
    ORC->>ENV: load_dotenv()
    Note over ORC: read IDP_* , platform key,<br/>IDP_TERMINAL_STATUSES,<br/>IDP_SUCCESS_STATUSES,<br/>IDP_EXECUTION_TIMEOUT_SECONDS
    ORC->>PLAT: make_platform()
    ORC->>IDP: make_idp_adapter()

    ORC->>PLAT: get_dataset(name)
    PLAT-->>ORC: items[] (document_id + golden)
    Note over ORC: A4 empty-set guard:<br/>if items empty -> exit non-zero

    Note over ORC: golden_version = hash_dataset(items)<br/>(single fetch, content hash — ADR-0001/INV-04,<br/>TOCTOU-safe; no get_golden_version on the adapter)

    ORC->>IDP: ensure_token()  # OAuth client credentials, cached for run
    alt auth failure (A3)
        IDP-->>ORC: OAuthError
        Note over ORC: fail-closed -> exit non-zero<br/>(no retry, no fallback)
    end

    loop for each item in items  (sequential — no concurrency at MVP)
        ORC->>IDP: extract(item.document_id, version=<v>)
        IDP->>IDP: submit doc -> execution id
        loop until status in IDP_TERMINAL_STATUSES or timeout
            IDP->>IDP: poll executions/{id}
        end

        alt timeout (A1 / AC5)  — ASM-02 abort
            IDP-->>ORC: TimeoutError
            Note over ORC: abort ENTIRE run<br/>surface error for this document_id<br/>exit non-zero (no partial run)
            ORC-->>PE: exit non-zero  (stop, do not continue)
        end

        alt hard IDP failure (A2)  — ASM-02 abort
            IDP-->>ORC: status in terminal-failure set OR HTTP error
            Note over ORC: report IDP error detail<br/>abort ENTIRE run<br/>exit non-zero
            ORC-->>PE: exit non-zero  (stop)
        else transient transport error (5xx/429/reset)
            Note over IDP: bounded exponential backoff retry<br/>(max attempts from config);<br/>exhausted budget = hard failure -> abort
        else terminal success status
            IDP->>NORM: normalize(raw_body, success_statuses)
            NORM-->>IDP: NormalizedOutput {status, fields, tables, prompts}
            IDP-->>ORC: NormalizedOutput
            ORC->>CLS: classify(item.golden, actual)
            CLS-->>ORC: verdicts (per-field)
            ORC->>GATE: overall_gate(verdicts)
            GATE-->>ORC: PASS | FAIL
            ORC->>PLAT: write_scores(run, item, verdicts, gate, action_id, action_version, golden_version)
            alt platform-write failure
                PLAT-->>ORC: WriteError
                Note over ORC: abort run -> exit non-zero
            end
        end
    end

    ORC->>PLAT: flush()
    PLAT-->>ORC: ok

    alt any gate == FAIL
        ORC-->>PE: exit non-zero  (CI: block PR)
    else all gates PASS
        ORC-->>PE: exit 0  (CI: unblock PR)
    end
```

Key paths this diagram pins:
- **Happy path:** extract → normalize → classify → gate → write_scores, one score set per field + one `gate` per document, action version + golden version recorded.
- **ASM-02 abort (A1 timeout, A2 hard failure):** the loop stops at the failing document, the run is aborted, exit non-zero. No partial run is ever produced as a reference.
- **A3 auth failure:** fail-closed at run start; no retry on 401/403.
- **A4 empty-set guard:** before the loop, if the dataset is empty, exit non-zero without writing scores.
- **Containment guardrails (ADR-0004 § Containment):** bounded retry for transient transport errors only; platform-write failure aborts; sequential-only at MVP.