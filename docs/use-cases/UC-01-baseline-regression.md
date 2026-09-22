# UC-01: Run a baseline regression over a golden set

**Parent PRD:** `docs/init/PRD-idp-regression.md`
> Note: this is the informal source doc in `docs/init/` — nothing has been promoted to `docs/prd/` yet.
> When a formal PRD is produced, update this link.

**Epic:** B — Classifier & gate · D — Run orchestration & platform integration
**Persona:** Prompt Engineer (primary trigger); CI Pipeline (automated trigger, same flow)
**Priority:** P0
**Business value:** Establishes the known-good reference that every future candidate run is measured against — without this, "better or worse?" has no answer.

**Intent-validated:** yes — 2026-09-17 (ASM-02 abort confirmed; ASM-01 `SUCCEEDED` confirmed + configurable-allowlist directive, full enum pinned by spike pre-implementation)

> No wireframes/mockups provided — UI derived from text only. The core app is a Python library + CLI; no browser UI in this epic.

---

## Use case

> **Narrative amendment 2026-09-22 (Soneca, ADR-0006) — no AC, flow step or business rule changes.**
>
> **1. A third trigger exists (in design, not yet built).** UC-01's flow is triggered by a Prompt Engineer or by CI on a prompt-change PR. ADR-0006 designs an **unattended version watcher** — a scheduled one-shot tick that detects a newly published action version and invokes this same flow with the discovered `--version`. It is a *new caller above* `run_eval`, not a change to it: **every step, AC, alternate flow and business rule below is unchanged under that trigger.** The watcher is a separate use case (UC-02, owed by Feliz) and a separate spec (SPEC-02); it is **not** in scope here.
>
> **2. Where the comparison lives, said out loud (user decision 2026-09-22).** The evaluation platform stores the golden-set dataset and the scores, and provides the cross-run/version-over-version view. **The comparison itself is ours**: the nested walk, type-aware canonical matching, `match_key` list pairing and per-leaf scoring are `classifier/` (ADR-0003) and are the CI gate (INV-08). This confirms ADR-0003/0005 and changes no code — it is written down because the docs never said which side of the seam owned it, and a reader could have assumed the platform did. Langfuse *custom evaluators* were considered as an alternative host for the same logic and **deferred**: they would move the pure classifier inside the platform's execution model, against INV-08 and ADR-0005 #9 (record-after). See ADR-0006 §Step 8.
>
> **3. Two honest limits of the platform's "regression view".** Per DEBT-18 option B it shows **verdicts and gates, not values** — no extracted, expected or confidence *value* crosses to the platform. And version-over-version comparison depends on the run name carrying the version (ADR-0006 §C3).

- **Actor:** Prompt Engineer (or CI Pipeline acting on their behalf; from SPEC-02 onward, also an unattended version watcher — see the amendment above)
- **Goal:** Run the current baseline action version over an entire golden set, classify each extracted field against the golden, persist per-field scores and a gate to a named run on the evaluation platform, and record the action version and golden version used — producing the reproducible reference against which any candidate change is compared.
- **Preconditions:**
  1. A golden set exists on the evaluation platform (at least one dataset item: `document_id` + expected golden fields).
  2. The Prompt Engineer knows the IDP action ID and the published baseline action version to run; both are supplied per run on the command line (`--action`, `--version`), **each required with no env fallback** (ADR-0004 A8, 2026-09-22 — `IDP_ACTION_ID` no longer supplies a default). The golden-set name is likewise supplied as `--dataset`.
  3. IDP credentials (`IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID`) are set in env/secrets.
  4. The evaluation-platform API key is set in env/secrets and `load_dotenv()` has been called before any SDK client is constructed.
  5. The document files corresponding to the golden-set items are accessible at the paths referenced by each `document_id`.

- **Main flow:**
  1. Prompt Engineer (or CI) invokes `run_eval --action <action_id> --version <baseline_version> --dataset <golden_set_name> --run <run_name>` — all four required (ADR-0004 A8).
  2. The orchestrator reads credentials and config from env via `load_dotenv()`.
  3. For each dataset item in the golden set:
     a. The platform adapter fetches the expected golden (fields + criticality flags).
     b. The IDP adapter authenticates via OAuth2 client credentials (token cached for the run).
     c. The IDP adapter submits the document at the baseline action version and polls the executions API until a terminal status is reached or a timeout expires. The terminal-status check is a **configurable allowlist** (read from config/env), not a hard-coded `== "SUCCEEDED"` — `SUCCEEDED` is confirmed as a real terminal status (seen in a live response sample), but the full enum (e.g. whether `PARTIAL_SUCCESS` exists in this org and appears in callbacks) is unconfirmed and is pinned by a `/spike` against the live org before implementation. `[ASM-01, confirmed-mitigated 2026-09-17]`
     d. On a terminal success status, `normalize()` converts the raw IDP response to the internal normalized-output contract. The raw response is nested under `pages[]`, each page carrying `fields` (`<name>.value`), `tables` (a keyed map of arrays of row-objects: `tables.<table>[].<column>.value`), and `prompts` (`answer.value`) — `normalize()` walks into `pages[].fields.<name>.value`, not a flat top-level map. Processing continues to step 3e. `[confirmed from live response sample 2026-09-17; shape variance across actions flagged in ASM-04]`
     e. The classifier calls `classify(golden, actual)` and produces a per-field verdict map.
     f. `overall_gate(verdicts)` computes `PASS` or `FAIL` for that document.
     g. The platform adapter writes one `field:<name>` score per field and a `gate` score to the named run on the platform.
  4. After all documents are processed, the run on the platform holds: all per-field scores, all gate scores, the action version, and the golden version used.
  5. The orchestrator exits zero if all gates are `PASS`; exits non-zero if any gate is `FAIL`.

- **Alternate flows:**
  - **A1 — IDP execution timeout:** If step 3c exceeds the configured timeout before a terminal status arrives, the orchestrator **aborts the entire run**, surfaces an error for the timed-out document, and exits non-zero. It does not continue to the remaining documents — a partial run with a silent gap would be confusing to interpret. `[ASM-02, confirmed 2026-09-17 — abort]`
  - **A2 — IDP returns a hard-failure status:** If the executions API returns an error/failure status for a document, the orchestrator reports the IDP error detail and **aborts the entire run** (exits non-zero), following the same abort-on-failure decision as A1. The document is not silently skipped and no partial run is produced. `[ASM-02, confirmed 2026-09-17 — abort]`
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

- **AC5 (timeout — abort, ASM-02 confirmed 2026-09-17):** Given one document whose IDP execution times out before reaching a terminal status, when the timeout is hit, then the run surfaces an error for that document and **aborts the entire run** (exits non-zero); it does not continue to the remaining documents and does not silently pass.

- **AC6 (new field is informational):** Given a document whose actual output contains a field `discount` that is absent from the golden, when classified, then `discount` receives verdict `new_field`, the document's gate is unaffected by this field, and the gate is `PASS` if no other critical field fails.

---

## Examples (EX-n table)

| ID | Scenario → expected outcome | Provenance |
|----|-----------------------------|------------|
| EX-A1-1 | Golden set of 5 invoices; all fields extract correctly → 5 gates `PASS`, all verdicts `match`. | [approved] |
| EX-A1-2 | One invoice's `total` field is empty in actual output; `total` is `critical: true` → verdict `missing`, gate `FAIL` for that document. | [approved] |
| EX-A1-3 | IDP execution times out for one document → run surfaces an error for that document, **aborts the entire run** (exits non-zero); remaining documents are not processed, no partial run is produced. | [confirmed 2026-09-17] |
| EX-A1-4 | One invoice date extracted as `"March 15, 2024"` vs golden `"2024-03-15"` (type `date`) → verdict `wrong_format`, gate `PASS`. | [derived — not in seed; F5 coverage] |
| EX-A1-5 | Candidate output includes `discount` field not in the golden → verdict `new_field`, gate unaffected; remains `PASS` if no other critical miss. | [derived — not in seed; F7 coverage] |

---

## Business rules

1. **Version is the regression variable.** The action version must be passed as a parameter; it is never hard-coded. The run records which version was used.
2. **Criticality governs the gate.** `FAIL` is emitted only on a `missing` or `wrong_value` verdict where the field (or line-item block) is `critical: true` in the golden. Non-critical differences, `wrong_format`, `new_field`, and `new_line` do not fail the gate.
3. **`new_field` and `new_line` are informational, never failures.** Extending a prompt to extract extra fields must not turn a baseline red.
4. **Document files do not leave the app.** Only `document_id` and the expected golden fields are stored on the evaluation platform. Raw document files are accessed locally by the adapter and are never uploaded to the platform.
5. **Golden contents are sensitive.** The golden set may contain financial data and PII (invoice numbers, totals, dates, vendor IDs). Access must be credential-gated and the contents must not be logged in plain text.
6. **All credentials come from env/secrets.** `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID`, and the platform API key are never stored in code or committed to the repo. The action ID and action version are run parameters, not secrets — they are passed per run and recorded in run metadata. `load_dotenv()` is called before any SDK client is constructed.
7. **OAuth token is cached for the run.** A single token is obtained at run start and reused across all documents in the run; a new token is fetched only on expiry within the run.
8. **Line items are matched by `match_key`, not position.** Row reordering in the actual output is not a diff.
9. **IDP execution status enum — configurable allowlist (ASM-01, confirmed-mitigated 2026-09-17).** `SUCCEEDED` is confirmed as a real terminal status (seen in a live response sample). The full enum — specifically whether `PARTIAL_SUCCESS` exists in this org's version and whether it appears in callbacks — remains unconfirmed and is pinned by a `/spike` against the live org before implementation. The polling adapter's "is this terminal?" check treats the status set as a small configurable allowlist read from config/env, never a hard-coded `== "SUCCEEDED"`, so an unconfirmed enum does not break the adapter.
10. **Timeout/failure handling — abort the run (ASM-02, confirmed 2026-09-17).** A per-document timeout or hard IDP failure aborts the entire run and exits non-zero. The orchestrator does not continue to the remaining documents — a partial run with a silent gap would be confusing to interpret. The error is always surfaced, never silently passed.
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

**Intent-validated: yes — 2026-09-17.** Both blocking assumptions resolved by the human:
- **ASM-02 (timeout/failure):** the orchestrator aborts the entire run on a per-document timeout or hard IDP failure (avoids a confusing partial run). Reflected in A1, A2, AC5, EX-A1-3, BR10.
- **ASM-01 (status enum):** `SUCCEEDED` confirmed as a real terminal status from a live response sample. Full enum (e.g. whether `PARTIAL_SUCCESS` exists in this org / appears in callbacks) remains unconfirmed — but the polling adapter will treat terminal status as a **configurable allowlist** (read from config/env), never hard-coded `== "SUCCEEDED"`, so the unconfirmed enum does not break the adapter. The exact allowlist is pinned by a `/spike` against the live org before implementation. Reflected in step 3c and BR9.

**Confirmed IDP response structure (hand to Soneca at /design):** the raw execution response is nested under `pages[]`, each page carrying `fields` (`<name>.value`), `tables` (a keyed map of arrays of row-objects: `tables.<table>[].<column>.value`), and `prompts` (`prompt`, `source`, `answer.value`). `normalize()` must walk into `pages[].fields.<name>.value`, not a flat top-level map. Fuller examples also carry a per-value **confidence score** — not part of the golden-set comparison itself, but Soneca should keep it available to the classifier (review-queue gating is a likely future UC, Epic E/F).

**Still open (non-blocking, resolve at /design or /spike):**
- **ASM-03** — golden versioning mechanism (Opik automatic vs. Langfuse app-tracked); Low risk; resolves when the platform ADR is decided.
- **ASM-04** — the tables/prompts shape above is from invoice/field-report actions; the response contract can vary by action definition and API version. `normalize()` and the classifier must not assume one fixed shape across all document types (relevant to Epic F routing). Low risk for UC-01 (single baseline action); spike needed before multi-document-type support.

**Ready for `/lemon-studio-sdd:design` (Soneca).**
