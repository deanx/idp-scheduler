# UC-01: Run a baseline regression over a golden set

**Parent PRD:** `docs/init/PRD-idp-regression.md`
> Note: this is the informal source doc in `docs/init/` — nothing has been promoted to `docs/prd/` yet.
> When a formal PRD is produced, update this link.

**Epic:** B — Classifier & gate · D — Run orchestration & platform integration
**Persona:** Prompt Engineer (primary trigger); CI Pipeline (automated trigger, same flow)
**Priority:** P0
**Business value:** Establishes the known-good reference that every future candidate run is measured against — without this, "better or worse?" has no answer.

**Intent-validated:** pending — human confirmation not yet given

> No wireframes/mockups provided — UI derived from text only. The core app is a Python library + CLI; no browser UI in this epic.

---

## Use case

- **Actor:** Prompt Engineer (or CI Pipeline acting on their behalf)
- **Goal:** Run the current baseline action version over an entire golden set, classify each extracted field against the golden, persist per-field scores and a gate to a named run on the evaluation platform, and record the action version and golden version used — producing the reproducible reference against which any candidate change is compared.
- **Preconditions:**
  1. A golden set exists on the evaluation platform (at least one dataset item: `document_id` + expected golden fields).
  2. The baseline action version (`IDP_ACTION_VERSION_BASE`) is set in env/secrets.
  3. IDP credentials (`IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID`, `IDP_ACTION_ID`) are set in env/secrets.
  4. The evaluation-platform API key is set in env/secrets and `load_dotenv()` has been called before any SDK client is constructed.
  5. The document files corresponding to the golden-set items are accessible at the paths referenced by each `document_id`.

- **Main flow:**
  1. Prompt Engineer (or CI) invokes `run_eval --version <baseline_version> --run <run_name>` (or equivalent CLI).
  2. The orchestrator reads credentials and config from env via `load_dotenv()`.
  3. For each dataset item in the golden set:
     a. The platform adapter fetches the expected golden (fields + criticality flags).
     b. The IDP adapter authenticates via OAuth2 client credentials (token cached for the run).
     c. The IDP adapter submits the document at the baseline action version and polls the executions API until a terminal status is reached or a timeout expires. **[ASM-01, open]** The only terminal status confirmed in the source docs is `SUCCEEDED`; the requirements-spec explicitly flags the full status enum as org-specific and unconfirmed.
     d. On a terminal success status, `normalize()` converts the raw IDP response to the internal normalized-output contract and processing continues to step 3e.
     e. The classifier calls `classify(golden, actual)` and produces a per-field verdict map.
     f. `overall_gate(verdicts)` computes `PASS` or `FAIL` for that document.
     g. The platform adapter writes one `field:<name>` score per field and a `gate` score to the named run on the platform.
  4. After all documents are processed, the run on the platform holds: all per-field scores, all gate scores, the action version, and the golden version used.
  5. The orchestrator exits zero if all gates are `PASS`; exits non-zero if any gate is `FAIL`.

- **Alternate flows:**
  - **A1 — IDP execution timeout:** `[ASM-02, open — needs human confirmation]` If step 3c exceeds the configured timeout before a terminal status arrives, the orchestrator records an error for that document and **continues** processing the remaining documents in the golden set (does not abort the whole run). If the correct behavior is instead to abort on any timeout, this flow, AC5, and EX-A1-3 need revision.
  - **A2 — IDP returns a hard-failure status:** If the executions API returns an error/failure status for a document, the orchestrator records that document's gate as `FAIL` (extraction error) and reports the IDP error detail; whether this aborts the run or continues is covered by the same open question as A1 (ASM-02).
  - **A3 — IDP authentication failure:** If the OAuth2 token request fails, the orchestrator aborts immediately and exits non-zero with a clear credential error message.
  - **A4 — Golden set is empty:** If the dataset has no items, the orchestrator exits non-zero with a "golden set is empty" error — it does not silently succeed.
  - **A5 — New field in actual output:** If the actual output contains a field not in the golden, the classifier emits verdict `new_field` for that field; the gate is not affected. The field is reported as informational.

- **Post-conditions:**
  - The evaluation platform holds a named run with one `field:<name>` score per field per document, and one `gate` score per document.
  - The run metadata records the action version and golden version used (reproducibility).
  - Exit code reflects the aggregate gate result (zero = all PASS; non-zero = at least one FAIL or error).

---

## Acceptance criteria

- **AC1 (happy path):** Given a golden set of N documents and the baseline action version configured, when the Prompt Engineer runs a baseline regression, then every document is extracted, classified, and scored; the named run on the platform holds one `field:<name>` score per golden field per document plus a `gate` score; and the run records the action version and golden version used.

- **AC2 (all-match gate):** Given a document whose normalized IDP output matches its golden exactly on every field (type-aware canonical comparison), when the run is scored, then every field verdict for that document is `match` and its `gate` score is `PASS`.

- **AC3 (critical missing field):** Given a document whose IDP output returns empty (or absent) for a field marked `critical: true` in the golden, when classified, then that field's verdict is `missing` and the document's `gate` score is `FAIL`.

- **AC4 (format-only difference):** Given a document where a date field is extracted as `"March 15, 2024"` when the golden holds `"2024-03-15"` (same content, different format), when classified with type `date`, then the verdict is `wrong_format` — not `wrong_value` — and the gate remains `PASS`.

- **AC5 (timeout — open, ASM-02):** Given one document whose IDP execution times out before reaching a terminal status, when the timeout is hit, then the run surfaces an error for that document and does not silently pass; **whether the run aborts entirely or continues processing the remaining documents is an open assumption pending human confirmation** (see ASM-02).

- **AC6 (new field is informational):** Given a document whose actual output contains a field `discount` that is absent from the golden, when classified, then `discount` receives verdict `new_field`, the document's gate is unaffected by this field, and the gate is `PASS` if no other critical field fails.

---

## Examples (EX-n table)

| ID | Scenario → expected outcome | Provenance |
|----|-----------------------------|------------|
| EX-A1-1 | Golden set of 5 invoices; all fields extract correctly → 5 gates `PASS`, all verdicts `match`. | [approved] |
| EX-A1-2 | One invoice's `total` field is empty in actual output; `total` is `critical: true` → verdict `missing`, gate `FAIL` for that document. | [approved] |
| EX-A1-3 | IDP execution times out for one document → run surfaces an error for that document, does not silently pass; overall exit code non-zero. **Open: abort-vs-continue behavior pending human confirmation (ASM-02).** | [approved] |
| EX-A1-4 | One invoice date extracted as `"March 15, 2024"` vs golden `"2024-03-15"` (type `date`) → verdict `wrong_format`, gate `PASS`. | [derived — not in seed; F5 coverage] |
| EX-A1-5 | Candidate output includes `discount` field not in the golden → verdict `new_field`, gate unaffected; remains `PASS` if no other critical miss. | [derived — not in seed; F7 coverage] |

---

## Business rules

1. **Version is the regression variable.** The action version must be passed as a parameter; it is never hard-coded. The run records which version was used.
2. **Criticality governs the gate.** `FAIL` is emitted only on a `missing` or `wrong_value` verdict where the field (or line-item block) is `critical: true` in the golden. Non-critical differences, `wrong_format`, `new_field`, and `new_line` do not fail the gate.
3. **`new_field` and `new_line` are informational, never failures.** Extending a prompt to extract extra fields must not turn a baseline red.
4. **Document files do not leave the app.** Only `document_id` and the expected golden fields are stored on the evaluation platform. Raw document files are accessed locally by the adapter and are never uploaded to the platform.
5. **Golden contents are sensitive.** The golden set may contain financial data and PII (invoice numbers, totals, dates, vendor IDs). Access must be credential-gated and the contents must not be logged in plain text.
6. **All credentials come from env/secrets.** `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID`, `IDP_ACTION_ID`, `IDP_ACTION_VERSION_BASE`, and the platform API key are never stored in code or committed to the repo. `load_dotenv()` is called before any SDK client is constructed.
7. **OAuth token is cached for the run.** A single token is obtained at run start and reused across all documents in the run; a new token is fetched only on expiry within the run.
8. **Line items are matched by `match_key`, not position.** Row reordering in the actual output is not a diff.
9. **IDP execution status enum — open (ASM-01).** Only `SUCCEEDED` is confirmed by the source docs as a terminal status; the full enum (including any partial/manual-review states) and the exact terminal-vs-non-terminal handling are org-specific and unconfirmed. Do not implement against an assumed enum without confirming against the live org.
10. **Timeout/failure handling — open (ASM-02).** Whether a per-document timeout or hard IDP failure aborts the entire run or is recorded as an error for that document while the run continues is an open assumption pending human confirmation.
11. **Score names are a stable contract.** `field:<name>` and `gate` score keys are used by the remediation UI and must not be renamed without a versioned migration.

---

## Compliance notes (regime: none formal)

No formal compliance regime applies. However, extracted fields from invoices, purchase orders, and identity documents can carry **financial data and PII** (totals, invoice numbers, vendor tax IDs, personal names). The following design rules apply:

- **Sensitive surfaces touched by this UC:** golden-set storage (expected values), IDP credentials (OAuth client secret), evaluation-platform API keys.
- **Document files never enter the evaluation platform** (out-of-scope rule from the PRD — enforced in the adapter layer).
- Golden-set contents (expected field values) must be stored only on the evaluation platform behind API-key auth, never in plain-text logs or unprotected files.
- IDP credentials and platform API keys must be sourced exclusively from env/secrets, never from code, config files committed to the repo, or log output.
- PII hygiene: if a golden field value contains personal data (e.g., a name from an ID document), it must not be emitted to stdout/stderr in debug output.

---

## Hand-off

**Intent validation is still pending.** This UC is not yet ready for `/design`. The human needs to confirm the EX-n examples and answer two open questions before Soneca picks this up:

1. **ASM-02:** On a per-document IDP timeout (or hard failure), should the orchestrator abort the entire run, or record an error for that document and continue?
2. **ASM-01:** What are the actual terminal status strings for your live IDP org (only `"SUCCEEDED"` appears as an example in the source docs — the full enum is unconfirmed)?

ASM-03 (golden versioning mechanism — Opik automatic vs. Langfuse app-tracked) is Low risk and can resolve later, at `/design`, once the platform ADR is decided.
