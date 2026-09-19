# SPEC-01: Baseline regression over a golden set

**Use case:** UC-01 (Run a baseline regression over a golden set)
**ADRs:** ADR-0005 (evaluation-platform re-decision — Langfuse, Accepted, Atchim APPROVED R1–R4; supersedes ADR-0001), ADR-0001 (Superseded by ADR-0005 — interface/STRIDE still referenced), ADR-0002 (IDP adapter & normalize() contract), ADR-0003 (classifier & gate), ADR-0004 (run orchestration & failure containment)
**NFRs:** NFR-01 (28 rows, all PENDING) — `Containment: REQUIRED`, `LLM-Evals: N/A`, `Observability: REQUIRED`
**Owner persona:** Prompt Engineer (primary), CI Pipeline (automated)
**Risk level:** high (driven by ADR-0002/0004/0005, all High; Atchim-approved)
**Re-scope 2026-09-19 (Dunga, /plan):** S-01.5 Done (QA-S-01.5, ADR-0005). S-01.3 re-scoped per ADR-0005 Decisions #1–#8 / F2–F4 + CT-05 + DEBT-13 (new T-01.3.0, T-01.3.9–T-01.3.11; changed T-01.3.1/3/5/8). S-01.4 re-scoped per ADR-0005 #5/#8 (new T-01.4.11; changed T-01.4.2/3/7). New test-plan rows TP-32–TP-46 (TP-42–46 from Atchim DoD gate 2026-09-19). New/changed tasks are `est: TBD` pending Dengoso. See `## Definition of Ready`.

## Summary

We build the P0 MVP flow for the IDP Regression Tester: run the current baseline IDP action version over an entire golden set, classify each extracted field against the golden, persist per-field `field:<name>` scores and a `gate` score per document to a named run on Langfuse (ADR-0005; golden sets guarded by a committed server-side `expectedOutputSchema`), record the action version and golden version used, and exit non-zero on any critical FAIL or run error. The classifier (Epic B) is pure and is the CI gate; the IDP adapter, Langfuse platform adapter, and orchestrator + CLI (Epic D) wire the pure core to the external services. Two prerequisite spike stories gate the uncertain parts before the implementation stories that depend on them (S-01.5 is Done; S-01.6 is open).

## Scope

- In scope (UC-01):
  - Pure `classify()` + `overall_gate()` over the six verdicts and four field types (Epic B).
  - IDP adapter: OAuth2 client-credentials + polling with a configurable terminal-status allowlist + `normalize()` walking `pages[].fields/tables/prompts` + untrusted-input validation (Epic A).
  - Langfuse platform adapter: `get_dataset` (single-fetch content-hash golden version + the dataset's `expectedOutputSchema`), `write_scores` (deterministic score id, retry-safe — ADR-0005 #5), `flush`, `run_status=aborted` marker, committed golden JSON Schema + REST provisioning (ADR-0005 #1–#3, CT-05), v4 `events_only` ingestion (ADR-0005 #6) (Epic D).
  - Orchestrator + CLI: `run_eval`, abort-on-failure (ASM-02), two distinct timeouts, monotonic clock, 401 mid-run refresh-then-fail-closed, `document_id→path` resolution, exit-code contract (Epic D).
  - Two prerequisite spike stories (SPIKE-01 Langfuse form-mode — **Done**; IDP terminal-status/timeout/retry pinning).
- Out of scope:
  - Multi-document-type routing & structural novelty (Epic F — needs ASM-04 spike).
  - Remediation UI (Epic E).
  - Golden-set CRUD/curator workflow (UC-C1 — MVP Curator edits raw JSON under the schema guard-rail; schema-driven form is Epic E per ADR-0005; Curator runbook F7 is an Epic C card).
  - Candidate (non-baseline) runs and A/B comparison (later UCs).
  - Human disposition (F19) and golden promotion (F20) — deferred to post-MVP UCs.
  - Release/deploy/changelog/version tagging (CI/CD concern, not the board).

## Implementation order & dependencies

```
S-01.5 (SPIKE-01 Langfuse form-mode) ✅ DONE ──┐
                                        ├──▶ S-01.3 (Langfuse adapter) ──┐
S-01.6 (IDP status/timeout/retry spike) ┤                                ├──▶ S-01.4 (Orchestrator+CLI) ──▶ /harden ──▶ /qa
                                        ├──▶ S-01.2 (IDP adapter)  ──────┘
S-01.1 (Classifier & gate)  ────────────┘ (no deps; start here)
```

- **S-01.1** has no dependencies and no external credentials for its unit tests — start here (the spike already has 11 tests).
- **S-01.5** is **Done** (QA-S-01.5 ⚠️ Pass with follow-ups; ADR-0005 supersedes ADR-0001). **S-01.6** is the remaining prerequisite spike; it ungates S-01.2/S-01.4 live work.
- **S-01.2** unit tests can use a placeholder terminal-status allowlist (`["SUCCEEDED"]`); the spike S-01.6 pins the live-org values before integration.
- **S-01.3** — S-01.5 dependency **resolved** (ADR-0005, platform final); Langfuse credentials **configured** (gitignored `.env`, Mestre handoff 2026-09-19). Integration tests gated on F-1 (Langfuse images pinned to 4.38.0 — user/Mestre). Real-golden load (not build) gated on N25 self-hosting obligations (Mestre).
- **S-01.4** depends on S-01.1, S-01.2, S-01.3 (incl. `get_dataset` exposing `expectedOutputSchema` for the Decision #8 drift check), and a passing `/harden` (HARDEN-01.md) before Done.

## Stories

### Story S-01.1 — Classifier & gate (Epic B)

- **As a** Prompt Engineer, **I want** a pure per-field classifier and aggregate gate over the six verdicts and four field types **so that** a baseline run can be judged PASS/FAIL deterministically with no external dependencies, and the suite becomes the CI gate.
- **Acceptance criteria (from UC-01):** AC2 (all-match gate), AC3 (critical missing field → `missing`, gate FAIL), AC4 (format-only difference → `wrong_format`, gate PASS), AC6 (new field is informational → `new_field`, gate unaffected). Plus BR2 (criticality governs the gate), BR3 (`new_field`/`new_line` never fail), BR8 (line items matched by `match_key`, not position).
- **Definition of Done:**
  - [ ] All AC met (AC2, AC3, AC4, AC6)
  - [ ] Unit tests passing: the spike's 11 tests + new edge cases (per-type canonical forms for `number`/`date`/`id`/`text`; format-vs-value tier; line-item `match_key` pairing; `new_line` on unmatched actual row; `missing` on unmatched golden row; non-critical difference → PASS)
  - [ ] Contract test CT-02 (`tests/classifier/test_classify_contract.py`) pins the `Verdict` TypedDict and the six verdict literals
  - [ ] Classifier purity: no IDP/platform/I/O imports in `src/idp_regression/classifier/` (static grep)
  - [ ] Input shape validation: `classify()` raises typed `ClassifierError` on malformed golden/`NormalizedOutput` (NFR N22) — a malformed golden is loud, not a silent `match`
  - [ ] Performance: `classify()` + `overall_gate()` for ≤50 fields, ≤500 line-item rows < 100 ms p95 on M1 (NFR N2)
  - [ ] Compliance: no golden-content value logged in plain text in the classifier path (INV-02 — the classifier is pure so this is mostly N/A, but the verdict `expected`/`actual` strings must not be emitted to stdout in any debug helper)
  - [ ] Observability: the verdict map is structured data the orchestrator will emit; no logging required inside the pure module (telemetry is the orchestrator's job per NFR N10)
  - [ ] Docs updated: glossary verdicts unchanged (they are the stable contract); DATA-MODEL-01 §3 already describes the shape — no edit needed unless a verdict detail block shape changed
  - [ ] Reviewed by Atchim (code review — ADR-0003 is risk:Low, no TDD gate beyond the unit suite which IS the gate)
  - [ ] Zangado audit signed off at `/qa`
- **Tasks:**
  - T-01.1.1 — Implement `classify(golden, actual) -> dict[str, Verdict]` per ADR-0003 (six verdicts, four field types, per-type canonical + format tiers) — est: TBD (Dengoso) — owner: Dengoso
  - T-01.1.2 — Implement `overall_gate(verdicts) -> PASS|FAIL` (FAIL iff `missing`/`wrong_value` AND `critical: true`) — est: TBD — owner: Dengoso
  - T-01.1.3 — Line-item comparison: pair rows by `match_key`, per-column sub-verdicts, `new_line` on unmatched actual, `missing` on unmatched golden (BR8) — est: TBD — owner: Dengoso
  - T-01.1.4 — Input shape validation + typed `ClassifierError` (NFR N22) — est: TBD — owner: Dengoso
  - T-01.1.5 — Port the spike's 11 tests into `tests/classifier/` and add edge-case coverage (canonical-form matrix, format-vs-value, criticality gate) — est: TBD — owner: Dengoso
  - T-01.1.6 — Contract test CT-02 (`test_classify_contract.py`) pinning `Verdict` TypedDict + six literals + verdict-key union (golden ∪ actual ∪ tables ∪ prompts) — est: TBD — owner: Dengoso
  - T-01.1.7 — Performance test (NFR N2): pytest benchmark on ≤50 fields / ≤500 rows < 100 ms p95 — est: TBD — owner: Dengoso

### Story S-01.2 — IDP adapter + normalize() (Epic A)

- **As a** Prompt Engineer, **I want** the IDP adapter to authenticate, submit, poll, and normalize the raw `pages[]` response into the stable internal contract **so that** the classifier never touches IDP's volatile shape and a changed extraction can be compared against the golden.
- **Acceptance criteria (from UC-01):** step 3c/3d (OAuth2 client-credentials + polling to a configurable terminal-status allowlist; `normalize()` walks `pages[].fields/tables/prompts`), A3 (auth failure → abort), BR7 (token cached for the run), BR9 (configurable allowlist, never `== "SUCCEEDED"`).
- **Definition of Done:**
  - [ ] All AC met (step 3c/3d, A3, BR7, BR9)
  - [ ] Unit tests passing: `normalize()` against captured raw-IDP fixtures (mocked; no live IDP needed for unit tests); OAuth token cache (instance-held `TokenCache`, refreshed on expiry); polling loop with a fake executions API; OAuth token request fails at run start → fail-closed, no retry (A3)
  - [ ] Contract test CT-01 (`tests/adapter/test_normalize_contract.py`) pins `NormalizedOutput` / `FieldValue` / `PromptValue` / the `tables: dict[str, list[dict[str, FieldValue]]]` shape and the adapter returning the IDP status alongside `NormalizedOutput` (run metadata), incl. mypy type-check of the TypedDicts
  - [ ] Untrusted-input contract (NFR N21): `normalize()` raises typed `MalformedIDPOutputError` on malformed body — no `KeyError`/`AttributeError` escapes; malicious field name (`"total\ngate"`, `"a:b"`) → `unsafe_field_name`; duplicate `prompt` string → collision error; >64 KB field value → `value_too_large`; >10_000-row table → `table_too_large`; NaN/out-of-range confidence → `None` (NOT clamped); three-state missing/empty/null preserved
  - [ ] Field-name sanitization at the trust boundary (safe charset `[A-Za-z0-9_\-]`, non-empty length cap) applies to `fields`, `tables` keys, and `prompts` keys
  - [ ] Configurable allowlist: `IDP_TERMINAL_STATUSES` + `IDP_SUCCESS_STATUSES` from env/config (default `["SUCCEEDED"]` — fail-closed); never hard-coded `== "SUCCEEDED"` (static grep)
  - [ ] Monotonic clock for submit/poll budgets (INV-07): `time.monotonic()`, never `time.time()` for the deadline math; S-01.2 owns the clock MECHANISM + the budget-math unit test (T-01.2.7, TP-18) — the timeout VALUE/budget TARGETS (submit ≤30s not retried; poll-wall-clock budget absolute from the first attempt; no-retry-on-poll-timeout, per NFR N1) are gated by **S-01.4's** DoD (line 145) so exactly one story gates the values
  - [ ] Merge semantics: `fields` last-wins across pages; `tables` concatenate rows per table; `prompts` keyed by `prompt` string with duplicate-prompt raise
  - [ ] Compliance: token/`Authorization`/`client_secret` never logged (NFR N23, INV-02); structured (non-f-string) logging so a value with `\n`/`"` cannot break a log line or inject a telemetry field (NFR N5); normalized/extracted field values materialized into `NormalizedOutput` (PII/financial per `## Domain`) are never emitted to stdout/stderr/logs in plain text — only secrets are not enough; the adapter's actual values are redacted at the logging boundary
  - [ ] Containment: S-01.2's § Containment guardrails (normalize untrusted-input contract, token redaction, monotonic-clock budget math, fail-closed configurable allowlist) are verified by the S-01.4 `/harden` containment report (`docs/qa/HARDEN-01.md`, Branca) before the epic is Done (NFR-01 `Containment: REQUIRED` — red-teams token redaction + the untrusted-input contract at the adapter boundary)
  - [ ] Observability: per-document extraction timing metric emitted (monotonic clock) for NFR N1; adapter returns the IDP status alongside `NormalizedOutput` for run metadata
  - [ ] Integration tests marked `@pytest.mark.integration` and skipped in CI without IDP credentials — IDP creds now configured locally (gitignored `.env`); live runs still need a real IDP action id + published version passed as `--action`/`--version` (ADR-0002 amendment 2026-09-19)
  - [ ] Docs updated: ADR-0002 unchanged; DATA-MODEL-01 §2 already describes `NormalizedOutput` — no edit unless the contract test surfaced a drift
  - [ ] Reviewed by Atchim (ADR-0002 is High — full review)
  - [ ] Zangado audit signed off at `/qa`
- **Tasks:**
  - T-01.2.1 — `IDPAdapter` Protocol + `MuleSoftIDPAdapter` with `extract(document_path, version)` (ADR-0002) — est: TBD — owner: Dengoso
  - T-01.2.2 — OAuth2 client-credentials flow + instance-held `TokenCache` (get/refresh); `expires_at - IDP_TOKEN_REFRESH_MARGIN_SECONDS` refresh margin — est: TBD — owner: Dengoso
  - T-01.2.3 — Submit (`POST .../executions`, file upload, submit-call timeout `IDP_SUBMIT_TIMEOUT_SECONDS`, NOT retried) + Poll loop with configurable `IDP_TERMINAL_STATUSES` allowlist — est: TBD — owner: Dengoso
  - T-01.2.4 — `normalize(raw, success_statuses) -> NormalizedOutput` walking `pages[].fields.<name>.value` / `tables.<table>[].<col>.value` / `prompts[].answer.value`; merge semantics (fields last-wins, tables concat, prompts keyed with duplicate-prompt raise) — est: TBD — owner: Dengoso
  - T-01.2.5 — Untrusted-input validation: typed `MalformedIDPOutputError` family (`unsafe_field_name`, `duplicate_prompt`, `value_too_large`, `table_too_large`), confidence NaN→`None` (no clamp), three-state missing/empty/null — est: TBD — owner: Dengoso
  - T-01.2.6 — Contract test CT-01 against captured raw-IDP fixtures (from S-01.6 spike) incl. mypy type-check of the TypedDicts — est: TBD — owner: Dengoso
  - T-01.2.7 — Monotonic-clock budget math (INV-07) + per-document timing metric (NFR N1) — est: TBD — owner: Dengoso
  - T-01.2.8 — Structured logging + token redaction (NFR N5, NFR N23, INV-02) — est: TBD — owner: Dengoso
  - T-01.2.9 — Integration tests (`@pytest.mark.integration`) against live IDP — skipped in CI until IDP creds configured — est: TBD — owner: Dengoso

### Story S-01.3 — Langfuse platform adapter (Epic D)

- **As a** Prompt Engineer, **I want** the Langfuse adapter to fetch the golden set, write per-field + gate scores, flush, and mark aborted runs **so that** the run is recorded on the platform for the remediation UI and reproducibility, with no document-file bytes ever uploaded.
- **Acceptance criteria (from UC-01):** step 3a/3g (fetch expected golden; write `field:<name>` per field + `gate` per document), post-condition (run holds all scores + action version + golden version), BR4 (document files never enter the platform), BR11 (score keys are a stable contract).
- **Definition of Done:**
  - [ ] All AC met (step 3a/3g, post-condition, BR4, BR11)
  - [ ] `PlatformAdapter` Protocol + `LangfuseAdapter` implementing `get_dataset` / `write_scores` / `flush` (ADR-0001 interface, unchanged by ADR-0005); `make_platform()` factory reads `PLATFORM` env
  - [ ] `get_dataset` return value carries the dataset's `expectedOutputSchema`, read via `GET /api/public/v2/datasets/{name}` (returned verbatim; an **absent schema is returned as `None`, not raised** — the orchestrator turns `None` into `schema_drift`) — a widening of the returned value, not a new Protocol method; feeds the S-01.4 run-start schema-drift check (ADR-0005 Decision #8, TP-35)
  - [ ] Golden-schema provisioning (T-01.3.0, ADR-0005 #1–#3, F2): committed, versioned **generic** golden JSON Schema (string-valued; per-`type` value patterns on `fields.<name>.value` via typed `if`/`then` — `number` `^-?[0-9]+(\.[0-9]+)?$`, `date` ISO `YYYY-MM-DD` pattern (no `format: date`), `id`/`text` string + `minLength`; `prompts` keyed map via `additionalProperties` + `propertyNames`; `answer` plain string; table cells flat strings) + raw-REST provisioning step (Python SDK schema support #10688 unverified); **F2: tables block measured and full minified schema length recorded < 10,000 chars** — both numbers recorded in `docs/design/DATA-MODEL-01.md` §1 "Schema limits" note (with the schema file version) and the < 10k bound asserted by CT-05; live write of the tables block under the committed shape confirmed (QA-S-01.5 F-6); Soneca reviews the schema (TP-32, TP-33, TP-34)
  - [ ] Contract test CT-05 (`tests/platform/test_golden_schema_contract.py`) — static walk of the committed schema enforcing Ajv `strict: true` authoring rules: every `if`/`then` subschema declares `"type"`; every `required` key ⊆ that schema's `properties`; minified length < 10,000; prompts `propertyNames` = `{"type":"string","pattern":"^[^\\u0000-\\u001F\\u007F]{1,200}$"}` (1–200 chars, control-char exclusion); accepts the DATA-MODEL-01 §1 example; rejects `"twelve fifty"` in a `number` field (CT-05, TP-32)
  - [ ] Schema evolution is expand/contract, never drop (ADR-0005 #4) — provisioning step never deletes a dataset schema and never sends an empty/null schema (asserted by a unit test on the provisioning function against a mock REST client — TP-42)
  - [ ] v4 `events_only` ingestion (ADR-0005 #6, F3): traces + dataset-run linkage via OTLP / a v4-capable SDK; scores via `/api/public/scores` (score events only on `/api/public/ingestion`); score reads via `/v3/scores` (`GET /v2/scores` is 404); SDK version pinned in the lock file; integration assertions use a bounded poll (30 s max wait, 1 s poll interval), never read-after-write (TP-38); **OTLP export failure is NOT best-effort** — traces + dataset-run linkage are part of the run record the remediation UI reads, so an OTLP export error at `flush()` surfaces as a typed error the orchestrator maps to `flush_failed` (unit test with a failing exporter — TP-43)
  - [ ] Unit tests passing: `hash_dataset` encoding (stable across runs), `make_platform()` factory env dispatch, `run_status=aborted`/`complete` marker write path, the `write_scores` idempotency-vs-no-retry decision, `get_dataset` against a mock/stub platform asserting it returns the dataset items (document_id + golden fields) and feeds the single-fetch content-hash path, and `write_scores`/`flush` failures raising typed structured errors mappable to the abort-reason taxonomy (`dataset_fetch_failed` / `flush_failed`), deterministic `score_id()` (TP-36), `get_dataset` returning `expectedOutputSchema` (TP-35) — all against a mock/stub platform (no Langfuse creds required)
  - [ ] Golden versioning is **single-fetch content hash** (ADR-0001): orchestrator computes `golden_version = hash_dataset(dataset)` over the same `dataset` it iterates — NO `get_golden_version` on the interface (TOCTOU guard, INV-04); `hash_dataset` encoding pinned by a contract test (stable across runs)
  - [ ] DEBT-13: INV-03 (`docs/design/INVARIANTS.md`) and CT-03 (`docs/design/CONTRACTS.md`) rows widened to list the prompt-derived score family `prompt:<16-hex sha256 of the prompt key>` (ADR-0002 amendment 2026-09-19 / ADR-0005 F10) alongside `field:<name>` and `gate`; the family is pinned in the CT-03 test (`^prompt:[0-9a-f]{16}$` = first 16 hex chars of `sha256(prompt_key.encode("utf-8"))` — **UTF-8, verbatim key, no normalization**; raw prompt never appears in a score name) — DEBT-13 closed (TP-39)
  - [ ] Contract test CT-03 (`tests/platform/test_score_contract.py`): one `field:<name>` per golden field + one `prompt:<16-hex>` per golden prompt + exactly one `gate` per document; score-key format pinned for all three families (DEBT-13); run metadata carries `action_id` + `action_version` + `golden_version` (INV-03, INV-04) (NFR N9 — exactly one `field:<name>` per golden field + one gate per document, no unbounded score write)
  - [ ] INV-01: no platform-write payload contains file bytes or a file-path blob beyond `document_id` — covers score payloads, run metadata, **and OTLP span attributes/events** (asserted with an in-memory span exporter — TP-15, TP-45)
  - [ ] Deterministic score id (ADR-0005 Decision #5, F3; replaces the superseded `(run_name, document_id, field_name)` idempotency-key / no-retry wording; DEBT-03 design-resolved → closed on implementation): `score.id = uuid5(NAMESPACE, f"{run_id}|{document_id}|{score_name}")` where `run_id` is a unique id generated at run start (**not** `run_name`) and `NAMESPACE` is a **committed, pinned UUID constant** in `src/idp_regression/platform/` (never generated at runtime, never changed); re-writing a score within an invocation upserts (Langfuse client-id upsert, live-verified), so `write_scores` may retry on 5xx within the ADR-0004 absolute retry deadline (TP-36, TP-37)
  - [ ] `flush()` is the bounded-retry seam (the orchestrator owns the retry budget — adapter exposes idempotent flush)
  - [ ] `run_status=aborted` / `run_status=complete` metadata marker writable on the run (ADR-0004 #14)
  - [ ] Swappability (NFR N24): all Langfuse SDK imports confined to `src/idp_regression/platform/` — static grep (ruff/import-linter)
  - [ ] Compliance: platform API key from env only; `load_dotenv()` before client construction (INV-05); no golden-content value logged in plain text (INV-02); structured (non-f-string) logging so a value with `\n`/`"` cannot break a log line or inject a telemetry field (NFR N5) — `dataset_fetch_failed`/`flush_failed` structured-error messages are redacted at the logging boundary so golden-shaped values from `get_dataset` are not leaked through the error path; the Langfuse Basic `Authorization` header (public:secret, base64) used by the raw-REST provisioning step and the OTLP exporter is never logged — N5/N23 redaction unit test captures log output from both paths and asserts neither the header value nor the secret key appears (TP-44); integration-test fixtures use scrubbed golden values — no real invoice/ID/PII/financial values in committed test fixtures beyond captured-scrubbed samples (NFR N19 — S-01.3 touches the golden-set storage surface via `get_dataset` and its integration fixtures)
  - [ ] Observability: platform-write failures surface structured errors the orchestrator maps to abort reasons (NFR N10) — verified by the `write_scores`/`flush` structured-error unit test in the line-above enumeration
  - [ ] Containment: S-01.3's § Containment pieces — `write_scores` deterministic-id retry safety (ADR-0004 #12 as amended by ADR-0005 #5) and `run_status=aborted` marker (ADR-0004 #14) — are verified by the S-01.4 `/harden` containment report (`docs/qa/HARDEN-01.md`, Branca) before the epic is Done (NFR-01 `Containment: REQUIRED`)
  - [ ] NFR N26 (run-name idempotency): two runs under the same `run_name` over the same golden set produce no score collisions/overwrites — guaranteed by distinct `run_id` in the score id (CT-03 extension; TP-22 contract, TP-37 integration)
  - [ ] Integration tests `@pytest.mark.integration`, skipped in CI without Langfuse credentials (configured locally in gitignored `.env`); run against self-hosted Langfuse **pinned to 4.38.0** (QA-S-01.5 F-1 — dependency, user/Mestre owns the pin; CT-05 re-probed before any version bump); schema-invalid golden write rejected 400 on the API path with stored value unchanged (ADR-0005 F9 candidate NFR, TP-34); platform 400 validation bodies redacted at the logging boundary (ADR-0005 §Threat model, INV-02, N5)
  - [ ] Docs updated: ADR-0001 already Superseded by ADR-0005 (done at S-01.5); DATA-MODEL-01 §1/§4 'ADR-0005 follow-up' notes folded in with the committed schema file path + score-id rule (ADR-0005 F5, Soneca); CT-05 status → ✅ once the test lands; SEQ-UC-01 `get_golden_version` drift already fixed (DEBT-07 closed 2026-09-18)
  - [ ] Reviewed by Atchim (ADR-0005 is High — full review, incl. the committed golden schema against CT-05)
  - [ ] Zangado audit signed off at `/qa`
- **Tasks:**
  - T-01.3.0 — **NEW (ADR-0005 #1–#3, F2):** committed generic string-valued golden JSON Schema file (versioned) + raw-REST provisioning step (upsert, never drop — ADR-0005 #4); measure the tables block + full minified length < 10k; live-write the tables block under the committed shape — est: TBD — owner: Dengoso (Soneca reviews schema)
  - T-01.3.1 — **CHANGED:** `PlatformAdapter` Protocol + `LangfuseAdapter` (`get_dataset`, `write_scores`, `flush`) + `make_platform()` factory (ADR-0001 interface); `get_dataset` also returns the dataset's `expectedOutputSchema` via `GET /api/public/v2/datasets/{name}` (ADR-0005 #8) — est: TBD — owner: Dengoso
  - T-01.3.2 — `hash_dataset(dataset)` content-hash function (SHA-256 over canonical JSON encoding of `document_id` + `golden`); encoding pinned by contract test — est: TBD — owner: Dengoso
  - T-01.3.3 — **CHANGED (ADR-0005 #5, F3, DEBT-03):** `write_scores` with deterministic `score.id = uuid5(NAMESPACE, run_id|document_id|score_name)`; committed pinned `NAMESPACE` constant; `run_id` generated per invocation; retry-safe on 5xx — est: TBD — owner: Dengoso
  - T-01.3.4 — `run_status` metadata marker (`aborted`/`complete`) write path (ADR-0004 #14) — est: TBD — owner: Dengoso
  - T-01.3.5 — **CHANGED (DEBT-13):** Contract test CT-03 (`test_score_contract.py`): score-name shape for `field:<name>` / `prompt:<16-hex sha256>` / `gate`, metadata fields (`action_id` + `action_version` + `golden_version`), N9 score-count formula, N26 run-name idempotency — est: TBD — owner: Dengoso
  - T-01.3.6 — INV-01 payload assertion (no file bytes/path blob beyond `document_id`) — est: TBD — owner: Dengoso
  - T-01.3.7 — Module-boundary grep (NFR N24): Langfuse SDK imports only in `src/idp_regression/platform/` — est: TBD — owner: Dengoso
  - T-01.3.8 — **CHANGED:** Integration tests against live self-hosted Langfuse pinned to 4.38.0 (`@pytest.mark.integration`, skipped without creds): bounded-poll reads via `/v3/scores`, schema-invalid write rejected (F9), N26 cross-invocation no-overwrite — est: TBD — owner: Dengoso
  - T-01.3.9 — **NEW (CT-05):** `tests/platform/test_golden_schema_contract.py` — Ajv-strict authoring rules (typed `if`/`then`, `required` ⊆ `properties`), minified length < 10k, prompts `propertyNames` incl. control-char pattern, accepts DATA-MODEL-01 §1 example, rejects `"twelve fifty"` — est: TBD — owner: Dengoso
  - T-01.3.10 (split 2026-09-19 per Dengoso: **10a**, 5 pts: OTLP / v4 SDK traces + dataset-run linkage + export-failure → `flush_failed` + INV-01 on spans + OTLP auth redaction; **10b**, 3 pts: scores via `/api/public/scores` + `/v3/scores` reads + SDK pin) — **NEW (ADR-0005 #6, F3):** v4 `events_only` ingestion split behind the adapter — traces + dataset-run linkage via OTLP / v4 SDK, scores via `/api/public/scores`, reads via `/v3/scores`; pin the SDK version in the lock — est: TBD — owner: Dengoso
  - T-01.3.11 — **NEW (DEBT-13, docs):** widen INV-03 + CT-03 rows to include the `prompt:<16-hex sha256>` score family — est: TBD (docs, not pointed) — owner: Soneca

### Story S-01.4 — Orchestrator + CLI (Epic D)

- **As a** Prompt Engineer (or CI Pipeline), **I want** to run `run_eval --version <v> --run <name> [--action <id>]` over the golden set and get a deterministic exit code **so that** a prompt-change PR is blocked when a baseline regression fails or the run is not a usable reference.
- **Acceptance criteria (from UC-01):** AC1 (happy path — all docs extracted/classified/scored; run records action + golden version), AC5 (timeout → abort entire run, exit non-zero), A1/A2 (timeout/hard failure abort), A3 (auth failure abort), A4 (empty golden set → exit non-zero), post-condition (exit code = aggregate gate).
- **Definition of Done:**
  - [ ] All AC met (AC1, AC5, A1, A2, A3, A4, post-condition)
  - [ ] `run_eval(action_id, version, run_name) -> int` facade (ADR-0004); CLI `run_eval --version <v> --run <name> [--action <id>]`; `load_dotenv()` is the first line before any SDK client (INV-05)
  - [ ] `--version` required with no env fallback; `--action` defaults to `IDP_ACTION_ID`, exit non-zero if neither is set; both validated at the CLI boundary (`action_id` UUID, `version` `^[A-Za-z0-9._-]{1,64}$`) before any network call (ADR-0002/0004 amendment 2026-09-19, TP-31)
  - [ ] Abort-on-failure (INV-06, NFR N7): every abort path raises `RunAborted` and the CLI exits non-zero; no remaining documents processed after an abort — paths: timeout, hard failure, auth failure, empty golden set, dataset fetch failure, golden-schema drift (`schema_drift`, ADR-0005 #8), malformed golden (`malformed_golden`, N28), platform-write failure, flush failure, unknown-status timeout, credential missing
  - [ ] Fail-closed on missing credential (NFR N6): if any required `IDP_*` / platform env var is missing, `run_eval` exits non-zero with a clear message before any network call (unit test unsets each key and asserts non-zero exit + no outbound call)
  - [ ] Two distinct timeouts (NFR N1): submit-call `IDP_SUBMIT_TIMEOUT_SECONDS` (≤30s, NOT retried) + poll-wall-clock `IDP_EXECUTION_TIMEOUT_SECONDS` (default 120s placeholder, pinned by S-01.6); retry budget INCLUDED in the poll budget (absolute from first attempt); backoff exponential full-jitter base 1s cap 8s max-attempts from config (default 3)
  - [ ] No-retry on poll timeout (non-idempotent POST) — abort reason `hard_failure`
  - [ ] 401 mid-run refresh-then-fail-closed (ADR-0004 #7): initial auth fail → `auth_failure` no retry; mid-run 401/403 → one refresh, retry the triggering request, refresh-fail → abort `auth_failure`
  - [ ] **Pre-run order (pinned):** (1) `get_dataset` — network/404/auth → `dataset_fetch_failed`; (2) schema-drift check → `schema_drift`; (3) empty items → `empty_set`; (4) N28 structural validation → `malformed_golden` abort; (5) `golden_version = hash_dataset(dataset)` + generate `run_id`; (6) first IDP call. Each step aborts before the next; an empty dataset whose schema also drifted aborts `schema_drift`, not `empty_set` (TP-40 case)
  - [ ] Empty-set vs fetch-failure split (ADR-0004 #8): `get_dataset == []` → `empty_set`; network/404/auth on `get_dataset` → `dataset_fetch_failed`
  - [ ] Run-start schema-drift check (ADR-0005 Decision #8): after `get_dataset` and before any IDP call, compare `sha256(canonical JSON of the dataset's expectedOutputSchema)` with the same hash of the committed schema file (canonical = sorted keys, no whitespace); mismatch **or absent schema** → `RunAborted(reason="schema_drift")`, non-zero exit, zero IDP calls, zero scores written (CT-04 case; TP-40)
  - [ ] Pre-run golden-set validation (NFR N28): fail-fast over the entire dataset before any IDP call — **structural checks only, reusing the classifier's N22 golden validation** (`_validate_golden` in `classifier/gate.py`, via a public alias) — NOT a Python `jsonschema` pass (ADR-0005 #8 rejected a second validator dialect). Note: exposing the alias touches S-01.1's module — do it as an additive re-export. Ownership: Dengoso adds the alias plus an identity test (`tests/classifier/test_validation.py`: the public alias `is` `_validate_golden`, so N28 can never drift from N22), and then **re-runs `/test SPEC-01` for S-01.1** to re-stamp `docs/qa/TEST-S-01.1-baseline-regression.md`, because the alias touches a Files-set path and stales the stamp
  - [ ] `document_id → IDP_DOCUMENT_DIR/{id}` path resolution owned by the orchestrator (adapter takes a resolved path) — INV-01
  - [ ] Gate computed BEFORE the platform write (INV-08); exit-code aggregation reads the in-process gate, never a platform read-back
  - [ ] `run_status=aborted` + failing `document_id` written to the platform before non-zero exit (ADR-0004 #14); `run_status=complete` on zero exit. **Pre-run aborts (`dataset_fetch_failed`, `schema_drift`, `empty_set`, N28 `malformed_golden`, credential missing, CLI arg validation) write NO marker** — no run exists yet (asserted: zero platform writes on those paths)
  - [ ] run metadata records `action_id` + `action_version` + `golden_version` (INV-04, TP-16 — every zero-exit run; load-bearing for reproducibility; `action_id` already present per the 2026-09-19 amendment — confirmed in re-scope, no change)
  - [ ] `write_scores` 5xx retried within the ADR-0004 absolute retry deadline (safe because score ids are deterministic — ADR-0005 #5, F4); exhaustion → abort `hard_failure` (TP-41)
  - [ ] `flush()` bounded retry (3, exponential, full jitter); exhaustion → `flush_failed`
  - [ ] Exit-code contract CT-04 (`tests/orchestration/test_exit_code_contract.py`): `0` ⟺ all gates PASS + no run error; each abort reason (incl. `schema_drift`, `malformed_golden`) → non-zero (no exit-code namespace fragmentation) (NFR N11)
  - [ ] Unit tests passing: abort-path state machine, retry-budget math (absolute deadline, full-jitter backoff), 401 mid-run refresh-then-fail-closed state machine, empty-set vs fetch-failure split, gate-before-write ordering (INV-08), flush bounded-retry, monotonic-clock budget math, `document_id→path` resolution, pinned pre-run order (TP-40), and N28 structural validation (TP-46) — all against mock IDP + mock platform (no external deps)
  - [ ] Observability (NFR N10, REQUIRED): structured log emits run name, action version, golden version, per-document start/end + gate, and on abort the failing `document_id` + abort reason from the taxonomy (`hard_failure` / `unknown_status_timeout` / `auth_failure` / `dataset_fetch_failed` / `empty_set` / `schema_drift` / `malformed_golden` / `flush_failed`); the `schema_drift` log line carries both hashes (`expected_schema_sha256`, `actual_schema_sha256` or `"absent"`); the N28 `malformed_golden` error carries `document_id` + JSON path only, never the golden value (INV-02)
  - [ ] Compliance: credentials from env only (NFR N4, INV-05); no plaintext sensitive logging (NFR N5, INV-02); gitleaks clean; document files resolved locally and never uploaded to the evaluation platform (NFR N12/N20, INV-01)
  - [ ] **Passing `/harden` containment report (`docs/qa/HARDEN-01.md`, Branca) — story not Done until containment verified** (NFR-01 `Containment: REQUIRED`); /harden injects a hung POST and asserts abort-not-hang, red-teams the 401 refresh path, token redaction, and the no-retry-on-poll-timeout contract
  - [ ] CLI exit latency < 2 s after `flush()` (NFR N3) — e2e test against mock IDP + mock platform
  - [ ] Integration tests `@pytest.mark.integration`, skipped in CI without IDP + Langfuse credentials (both configured locally in gitignored `.env`) — covering TP-01 live happy path; needs a real IDP action id + published version (`--action`/`--version`)
  - [ ] Performance/scalability: 50-doc sequential run completes within the run-timeout budget (NFR N8 — sequential-only, no concurrency claim; scope=feature, measured at `/qa`)
  - [ ] Dependency pinning (NFR N17): `pip freeze` lock file committed; install from lock
  - [ ] Docs updated (Soneca): ADR-0004 #12 amended — retry-on-5xx branch becomes "retry with deterministic score id" (ADR-0005 F4); ADR-0004 abort taxonomy + SEQ-UC-01 show the pinned pre-run order incl. `schema_drift`/`malformed_golden`; NFR-01 N10 wording lists `schema_drift` + `malformed_golden`; NFR-01 N26 wording "scores dedupe by `(run_name, document_id, field_name)`" → deterministic score id over `run_id`
  - [ ] Reviewed by Atchim (ADR-0004 is High — full review)
  - [ ] Zangado audit signed off at `/qa`; QA report line `Observability: ✅ VERIFIED`
- **Tasks:**
  - T-01.4.1 — `run_eval(action_id, version, run_name) -> int` facade + CLI entry (`run_eval --version --run [--action]`, arg validation); `load_dotenv()` first (ADR-0004) — est: TBD — owner: Dengoso
  - T-01.4.2 — **CHANGED:** `RunAborted` exception + abort-reason taxonomy; per-path abort (timeout/hard-failure/auth/empty-set/fetch-failed/`schema_drift`/`malformed_golden`/write-failed/flush-failed/unknown-status); no `run_status` marker on pre-run aborts — est: TBD — owner: Dengoso
  - T-01.4.3 — **CHANGED (ADR-0005 #5, F4):** Two-timeout + retry-budget implementation (submit timeout not retried; poll budget absolute; full-jitter backoff); `write_scores` 5xx retry within the absolute deadline — est: TBD — owner: Dengoso
  - T-01.4.4 — 401 mid-run refresh-then-fail-closed (initial vs mid-run split) — est: TBD — owner: Dengoso
  - T-01.4.5 — **CHANGED:** `document_id → path` resolution (`IDP_DOCUMENT_DIR`); pre-run N28 structural validation reusing the classifier's N22 golden validator (public alias), `malformed_golden` abort with document_id + JSON path only; pinned pre-run order — est: TBD — owner: Dengoso
  - T-01.4.6 — Gate-before-write (INV-08); `hash_dataset` golden-version recording (INV-04); `run_status` marker writes — est: TBD — owner: Dengoso
  - T-01.4.7 — **CHANGED:** Exit-code contract test CT-04 (each abort reason incl. `schema_drift` and `malformed_golden` → non-zero; 0 ⟺ all PASS + no error) (NFR N11) — est: TBD — owner: Dengoso
  - T-01.4.8 — Observability: structured telemetry for every run + abort reason (NFR N10) — est: TBD — owner: Dengoso
  - T-01.4.9 — e2e orchestration test against mock IDP + mock platform (NFR N3 exit latency; INV-06 abort paths) — est: TBD — owner: Dengoso
  - T-01.4.10 — Credential-hygiene + gitleaks + lock-file pin (NFR N4, N5, N17, INV-02, INV-05) — est: TBD — owner: Dengoso
  - T-01.4.11 — **NEW (ADR-0005 Decision #8):** run-start schema-drift check — canonical-JSON sha256 of dataset `expectedOutputSchema` vs committed schema file; mismatch/absent → abort `schema_drift` before any IDP call — est: TBD — owner: Dengoso
  - T-01.4.12 — **NEW (docs, Atchim DoD re-check):** Soneca amends ADR-0004 (abort taxonomy + pinned pre-run order incl. `schema_drift`/`malformed_golden`; #12 retry with deterministic score id per ADR-0005 F4), SEQ-UC-01, NFR-01 N10/N26 wording. CT-04 row already lists `malformed_golden` (updated 2026-09-19) — est: TBD — owner: Soneca

### Story S-01.5 — SPIKE-01 prerequisite: Langfuse form-mode nested-JSON (gates ADR-0001)

**Status: ✅ DONE (2026-09-19)** — Zangado `/qa` ⚠️ Pass with follow-ups (`docs/qa/QA-S-01.5-langfuse-form-mode-spike.md`); platform decision final per `docs/adr/0005-evaluation-platform-redecision.md` (Accepted, Atchim APPROVED R1–R4). Open follow-ups carried elsewhere: F-1 image pin (S-01.3 DoR), F-2 commit durable docs (user), F-8 spike-dataset cleanup (user).

> **DoD branch note (per Zangado, QA-S-01.5 #8):** the per-verdict branch text below predates ADR-0005. The load-bearing capability was refuted, so the "refuted" branch applied — ADR-0005 superseding ADR-0001 (Atchim-approved) satisfies the branch intent, which is stricter than a retained PROVISIONAL label.

- **As a** Soneca/Dengoso pair, **I want** to confirm or refute on a live self-hosted Langfuse that form mode renders a representative nested golden as schema-validated editable form fields **so that** ADR-0001's PROVISIONAL platform decision is resolved (Langfuse confirmed, or the decision re-opens Langfuse vs Opik) before any golden is loaded.
- **Acceptance criteria (from SPIKE-01):** stand up a live self-hosted Langfuse (Docker); load a representative nested golden (`fields` of mixed types + `tables[].rows[]` + `prompts[]`); enable form mode; perform the EX-C1-1/EX-C1-2 Curator interactions (edit a nested value, edit a table-cell, type an invalid value and confirm schema rejection, add a row); record Langfuse version + UI mechanism + verdict.
- **Definition of Done:**
  - [x] Live self-hosted Langfuse stood up (Docker); version pinned and recorded in the spike result — ⚠️ met with deviation: 4.38.0 recorded, image not pinned (QA-S-01.5 #1, F-1)
  - [x] Representative nested golden loaded as a dataset item `expected_output` covering `fields` (number/date/id/text), `tables` (≥2 rows, column-keyed dict shape), `prompts` (≥1 entry) — using **synthetic/scrubbed values only** (no real invoice/ID/PII/financial data; `## Domain` marks golden-set contents sensitive, NFR N19 forbids golden values outside the platform beyond scrubbed samples) — ⚠️ met with deviation: probe shape diverged; fields+prompts re-probed in Addendum 2, tables block → ADR-0005 F2 (QA-S-01.5 #2)
  - [x] Form mode enabled; the exact UI mechanism (schema upload vs inference) recorded — ✅ (QA-S-01.5 #3)
  - [x] EX-C1-1/EX-C1-2 interactions performed (interaction ids sourced from `docs/init/use-cases-seed.md` UC-C1, which is out of scope for UC-01 but defines the Curator edit/invalid-value/new-row interactions reused here): nested-field edit + save + read-back; table-cell edit; invalid-value schema rejection; new-row add — ⚠️ met with deviation: edits via API, invalid-value rejection via live UI (QA-S-01.5 #4)
  - [x] Verdict recorded (confirmed / partially confirmed / refuted) with the specific shapes that did/did not render as schema-validated form fields — appended to `docs/spikes/SPIKE-01-langfuse-form-mode-nested-json.md`; screenshots/descriptions redacted of any PII/golden-value content (INV-02, NFR N19 — spike artifacts must not leak sensitive golden values) — ✅ PARTIALLY CONFIRMED / no-code Curator REFUTED (QA-S-01.5 #5)
  - [x] Observability: N/A (manual spike — no runtime telemetry; pinned UI-mechanism + verdict feed ADR-0001, not S-01.4's runtime observability) — ✅ N/A
  - [x] Performance: N/A (manual spike — no runtime surface; NFR N1/N2/N3 apply to S-01.1/S-01.4, not this manual exploration) — ✅ N/A
  - [x] ADR-0001 PROVISIONAL label updated by Soneca and ASM-05 status updated — branch per verdict: **confirmed** → PROVISIONAL label removed, ASM-05 → resolved; **refuted** → superseding ADR opened (PROVISIONAL retained on ADR-0001 until superseded), ASM-05 → open/blocked; **partially confirmed** → PROVISIONAL label retained with a note on the unconfirmed shapes, ASM-05 → partially-resolved, S-01.3 proceeds only against the confirmed shapes (golden load stays blocked for the unconfirmed shapes) — ✅ intent satisfied: ADR-0005 supersedes ADR-0001; ASM-05 resolved (QA-S-01.5 #8; see branch note above)
  - [x] If refuted: the re-decision ADR (Langfuse vs Opik, symmetric evaluation) is opened — blocks S-01.3 and any golden load — ✅ ADR-0005 (options A/B/C, symmetric) (QA-S-01.5 #9)
  - [x] Self-hosting obligations from ADR-0001 handed to Mestre (encryption at rest, DB access control, backup/restore — NFR N25) — ✅ Mestre handoff 2026-09-19 (QA-S-01.5 #10)
  - [x] Langfuse credentials registered with Mestre (`LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST`) — MISSING at planning time, now configured; credentials sourced from env only and never hard-coded into committed spike artifacts, Docker config (e.g. a committed `docker-compose` `.env`), spike notes, or screenshots (NFR N4, NFR N5 — Langfuse API keys are a Sensitive surface per `## Domain`) — ✅ configured in gitignored `.env`; secret grep clean (QA-S-01.5 #11)
  - [x] Time-box: half a day — ✅ (QA-S-01.5 #12)
  - [x] Reviewed by Atchim (the spike result flips a High-risk ADR — review the verdict + ADR update) — ✅ ADR-0005 APPROVED R1–R4 (QA-S-01.5 #13)
  - [x] Zangado audit at `/qa` (verifies the spike result is recorded and the ADR/ASM-05 status is consistent) — ✅ QA-S-01.5 ⚠️ Pass with follow-ups
- **Tasks:**
  - T-01.5.1 — Stand up self-hosted Langfuse (Docker); pin + record version — est: TBD — owner: Dengoso — ✅ done
  - T-01.5.2 — Load representative nested golden (fields/tables/prompts per DATA-MODEL-01) — est: TBD — owner: Dengoso — ✅ done
  - T-01.5.3 — Enable form mode; perform EX-C1-1/EX-C1-2 interactions; capture screenshots/description — est: TBD — owner: Dengoso — ✅ done
  - T-01.5.4 — Record verdict in `docs/spikes/SPIKE-01-*.md`; Soneca updates ADR-0001 PROVISIONAL label + ASM-05 — est: TBD — owner: Soneca (ADR) / Dengoso (result) — ✅ done
  - T-01.5.5 — Hand self-hosting obligations + Langfuse credential registration to Mestre — est: TBD — owner: Dunga (coordination) — ✅ done

### Story S-01.6 — IDP terminal-status/timeout/retry spike prerequisite (gates S-01.2/S-01.4 implementation, ASM-01)

- **As a** Soneca/Dengoso pair, **I want** to pin `IDP_TERMINAL_STATUSES` / `IDP_SUCCESS_STATUSES` / poll timeout / retry budget against the live IDP org **so that** the adapter's configurable allowlist and the orchestrator's timeout/retry budget are set from observation, not guesses, before S-01.2/S-01.4 integration.
- **Acceptance criteria:** against the live IDP org, observe the full terminal-status enum (does `PARTIAL_SUCCESS` exist? does it appear in callbacks?); confirm `SUCCEEDED` is terminal-success; observe a representative execution's wall-clock duration to inform `IDP_EXECUTION_TIMEOUT_SECONDS`; pin the retry budget defaults; capture a redacted raw-IDP fixture for the S-01.2 contract tests.
- **Definition of Done:**
  - [ ] Live IDP org access available (IDP credentials configured locally in gitignored `.env` per Mestre handoff 2026-09-19; still needs a real IDP action id + published version to execute against)
  - [ ] Full terminal-status enum observed and recorded: terminal set, success subset, whether `PARTIAL_SUCCESS` exists / appears in callbacks
  - [ ] `IDP_TERMINAL_STATUSES` and `IDP_SUCCESS_STATUSES` default values pinned (written into the config spec / DATA-MODEL-01 §5)
  - [ ] `IDP_EXECUTION_TIMEOUT_SECONDS` default pinned from observed wall-clock (replaces the 120s placeholder) — written into DATA-MODEL-01 §5; methodology: N ≥ 10 observed executions across the golden set, pin the p95 wall-clock as the default (a single-sample value is not defensible for the NFR N1 ≥99%-within-budget target)
  - [ ] Retry budget defaults (max-attempts, base, cap) pinned to concrete numbers written into DATA-MODEL-01 §5 (not "reasonable" — testable: values are present, numeric, and match the orchestrator config defaults); transient-error retry/backoff per NFR N14, quota/rate-limit concern per NFR N27
  - [ ] Redacted raw-IDP fixture captured for S-01.2 contract test CT-01 (no PII; golden-shaped values scrubbed per NFR N19)
  - [ ] Compliance: no IDP credentials / OAuth `access_token` / `Authorization` headers captured into the fixture, spike notes, or any committed artifact; credentials sourced from env only (NFR N4, NFR N23 — IDP credentials are a Sensitive surface per `## Domain`)
  - [ ] Observability: N/A (spike — no runtime component; pinned timeout/retry/status values feed S-01.4's runtime telemetry)
  - [ ] ASM-01 status updated (allowlist values now pinned, not just "mitigated by configurable mechanism")
  - [ ] Spike result recorded (a section in ASSUMPTIONS.md or a short spike note referenced from ASM-01)
  - [ ] Time-box: half a day
  - [ ] Reviewed by Atchim (the pinned values feed a High-risk adapter)
  - [ ] Zangado audit at `/qa` (verifies the fixture is scrubbed and the allowlist is config-driven)
- **Tasks:**
  - T-01.6.1 — Confirm IDP credentials configured (Mestre); gain live-org access — est: TBD — owner: Mestre (creds) / Dengoso (access)
  - T-01.6.2 — Observe terminal-status enum across several executions; record terminal vs success sets — est: TBD — owner: Dengoso
  - T-01.6.3 — Measure representative execution wall-clock; pin `IDP_EXECUTION_TIMEOUT_SECONDS` default — est: TBD — owner: Dengoso
  - T-01.6.4 — Capture redacted raw-IDP fixture for CT-01 (scrubbed per NFR N19) — est: TBD — owner: Dengoso
  - T-01.6.5 — Update ASM-01 + DATA-MODEL-01 §5 config defaults — est: TBD — owner: Soneca (ASM-01) / Dengoso (observations)

## Definition of Ready (re-scope 2026-09-19)

| Story | Ready? | Reason |
|---|---|---|
| S-01.1 | n/a — ✅ DONE | Full-rigor QA ✅ Pass (QA-01) |
| S-01.2 | **Ready for unit build**; **not Ready for live/integration** | Unit + CT-01 work runs on mocks/fixtures (placeholder allowlist). Live work needs a real IDP action id + published version, and CT-01 fixtures come from S-01.6 |
| S-01.3 | **Ready for build** (unit, CT-03, CT-05, T-01.3.0 schema authoring) — **not Ready for integration** | S-01.5 dependency resolved; creds configured. Integration (T-01.3.8, TP-33/34/37/38) needs F-1 (Langfuse images pinned to 4.38.0 — user/Mestre). N25 blocks real-golden load only, not build. New/changed tasks need Dengoso estimates first |
| S-01.4 | **Not Ready** | Depends on S-01.2 + S-01.3; live e2e needs a real IDP action id + published version; `/harden` before Done; new/changed tasks unestimated |
| S-01.5 | n/a — ✅ DONE | QA-S-01.5 ⚠️ Pass with follow-ups; ADR-0005 |
| S-01.6 | **Not Ready** | IDP creds configured, but needs a real IDP action id + published version to observe executions |

## Test plan

One row per AC, plus a row per EX-n tagged with its EX id. Integration/e2e rows note the external services/credentials required (both MISSING at original planning time; both configured locally as of 2026-09-19 — see DoR). Rows TP-32–TP-46 added by the 2026-09-19 re-scope (TP-42–46 after the Atchim DoD gate), each tagged with its source.

| Row | AC / EX | Scenario | Level | Story | External deps |
|-----|---------|----------|-------|-------|---------------|
| TP-01 | AC1 (happy path) | N-doc golden set, baseline version: every doc extracted, classified, scored; run holds `field:<name>` per field + `gate` per doc + action/golden version | e2e | S-01.4 | IDP creds + Langfuse creds (both MISSING) |
| TP-02 | AC2 (all-match gate) | Normalized output matches golden exactly (type-aware canonical) → all `match`, gate `PASS` | unit | S-01.1 | none |
| TP-03 | AC3 (critical missing) | IDP returns empty for a `critical: true` field → `missing`, gate `FAIL` | unit | S-01.1 | none |
| TP-04 | AC4 (format-only) | Date `"March 15, 2024"` vs golden `"2024-03-15"` (type `date`) → `wrong_format`, gate `PASS` | unit | S-01.1 | none |
| TP-05 | AC5 (timeout abort) | One doc's IDP execution times out → run aborts, exits non-zero, remaining docs not processed | e2e | S-01.4 | mock IDP (or live IDP creds) |
| TP-06 | AC6 (new field informational) | Actual has `discount` absent from golden → `new_field`, gate unaffected, `PASS` if no other critical miss | unit | S-01.1 | none |
| TP-07 | EX-A1-1 (5 invoices all match) | 5 invoices, all fields correct → 5 gates `PASS`, all `match` | unit/integration | S-01.1 (+S-01.4 e2e) | none for unit; IDP+Langfuse for e2e |
| TP-08 | EX-A1-2 (critical total empty) | One invoice `total` empty, `critical: true` → `missing`, gate `FAIL` for that doc | unit | S-01.1 | none |
| TP-09 | EX-A1-3 (timeout aborts run) | IDP times out for one doc → abort entire run, exit non-zero, no partial run, remaining docs not processed | e2e | S-01.4 | mock/live IDP |
| TP-10 | EX-A1-4 (date wrong_format) | Date `"March 15, 2024"` vs `"2024-03-15"` (type `date`) → `wrong_format`, gate `PASS` | unit | S-01.1 | none |
| TP-11 | EX-A1-5 (new_field discount) | Candidate output has `discount` not in golden → `new_field`, gate unaffected, stays `PASS` | unit | S-01.1 | none |
| TP-12 | A3 (auth failure abort) | OAuth token request fails at run start → fail-closed, exit non-zero, no retry | unit + e2e | S-01.2 (unit) / S-01.4 (e2e) | mock IDP |
| TP-13 | A4 (empty golden set) | `get_dataset` returns `[]` → exit non-zero `empty_set`, never silent zero | unit | S-01.4 | mock platform |
| TP-14 | A5 (new field, classifier) | Covered by AC6/EX-A1-5 | unit | S-01.1 | none |
| TP-15 | INV-01 (no file bytes to platform) | `write_scores` payload + run metadata contain no file bytes / no path blob beyond `document_id` (OTLP spans: TP-45) | contract | S-01.3 | none |
| TP-16 | INV-04 (version recording) | Every exit-0 run records `action_id` + `action_version` + `golden_version` (single-fetch hash) | contract | S-01.4 | mock platform |
| TP-17 | INV-06 (abort paths) | Each abort path → `RunAborted` + non-zero exit + no further docs processed | unit + e2e | S-01.4 | mock IDP + mock platform |
| TP-18 | INV-07 (monotonic clock) | Poll/submit deadline math uses `time.monotonic()`, not `time.time()` | unit + static grep | S-01.2 | none |
| TP-19 | INV-08 (gate before write) | `overall_gate()` called and captured before any `write_scores` for that doc; exit aggregation reads in-process gate | contract | S-01.4 | mock platform |
| TP-20 | NFR N21 (normalize untrusted input) | Malformed raw body → typed `MalformedIDPOutputError`; malicious field name; duplicate prompt; oversized value/table; NaN confidence → `None` | unit | S-01.2 | none |
| TP-21 | NFR N22 (classifier validates input) | Malformed golden/actual → `ClassifierError`, not silent `match` | unit | S-01.1 | none |
| TP-22 | NFR N26 (run-name idempotency) | Two runs same `run_name` over same golden → no score collisions/overwrites | contract | S-01.3 | none (contract test against mock) |
| TP-23 | NFR N28 (pre-run schema validation) | Golden set with one malformed item → non-zero exit before any IDP submit (detail pinned by TP-46: `malformed_golden`, N22 validator reuse, no value leak) | unit | S-01.4 | none |
| TP-24 | BR7 (token cached for the run) | `TokenCache` instance-held — single token reused across documents in a run, refreshed on expiry (not re-requested per doc) | unit | S-01.2 | none |
| TP-25 | BR9 (configurable terminal-status allowlist) | `IDP_TERMINAL_STATUSES` / `IDP_SUCCESS_STATUSES` read from config, default `["SUCCEEDED"]` fail-closed; static grep confirms no hard-coded `== "SUCCEEDED"` in the adapter | unit + static grep | S-01.2 | none |
| TP-26 | EX-C1-1 (Curator nested-field/table-cell edit) | On self-hosted Langfuse form mode: edit a nested field value + save + read-back; edit a table-cell; confirm schema-validated editable form fields | manual/spike | S-01.5 | ✅ DONE — executed at S-01.5 (edits via API; no-code form refuted — QA-S-01.5 #4, ADR-0005) |
| TP-27 | EX-C1-2 (Curator invalid-value rejection + new row) | On self-hosted Langfuse form mode: type an invalid value and confirm schema rejection; add a new row | manual/spike | S-01.5 | ✅ DONE — executed at S-01.5 (invalid-value rejection via live UI, add-row via API — QA-S-01.5 #4) |
| TP-28 | S-01.6 AC (terminal-status enum observed) | Against the live IDP org: full terminal-status enum observed and recorded (terminal set, success subset, whether `PARTIAL_SUCCESS` exists / appears in callbacks); `IDP_TERMINAL_STATUSES`/`IDP_SUCCESS_STATUSES` defaults written into DATA-MODEL-01 §5 | manual/spike | S-01.6 | live IDP org + IDP creds (MISSING) |
| TP-29 | S-01.6 AC (wall-clock → timeout default) | ≥10 observed executions across the golden set; p95 wall-clock pinned as `IDP_EXECUTION_TIMEOUT_SECONDS` default (written into DATA-MODEL-01 §5, replaces 120s placeholder) | manual/spike | S-01.6 | live IDP org + IDP creds (MISSING) |
| TP-30 | S-01.6 AC (redacted raw-IDP fixture) | Redacted raw-IDP fixture captured for CT-01 — no PII, no credentials/`access_token`/`Authorization`, golden-shaped values scrubbed per NFR N19 | manual/spike | S-01.6 | live IDP org + IDP creds (MISSING) |
| TP-31 | Run parameters (ADR-0002/0004 amendment 2026-09-19) | Missing `--version` → exit non-zero (no env fallback); `--action` omitted → `IDP_ACTION_ID` used, neither set → exit non-zero; malformed `action_id` (non-UUID) or `version` (e.g. `../x`, `1.0/../`) → exit non-zero with **zero** IDP/platform calls | unit | S-01.4 | none |
| TP-32 | CT-05 / ADR-0005 #1–#3 | Static walk of the committed golden schema: every `if`/`then` subschema declares `type`; every `required` key ⊆ `properties`; minified length < 10,000; prompts `propertyNames` pattern (1–200 chars, rejects `\n`/`\t`/`\u007F`); accepts DATA-MODEL-01 §1 example; rejects `"twelve fifty"` in a `number` field | contract | S-01.3 | none |
| TP-33 | ADR-0005 F2 / QA-S-01.5 F-6 | Tables block measured and full minified schema length recorded < 10k; provisioning the committed schema on a dataset succeeds; a golden with a flat `{column: "string"}` tables block writes 200 | integration | S-01.3 | self-hosted Langfuse pinned 4.38.0 (F-1) + Langfuse creds |
| TP-34 | ADR-0005 #1 / F9 | Schema-invalid golden write (`"twelve fifty"` in `number`, `"15/01/2026"` in `date`) → 400, stored value unchanged; 400 body not logged in plaintext (INV-02) | integration | S-01.3 | self-hosted Langfuse pinned 4.38.0 + creds |
| TP-35 | ADR-0005 #8 (adapter side) | `get_dataset` returns the dataset's `expectedOutputSchema` verbatim from `GET /api/public/v2/datasets/{name}` (mock asserts endpoint + passthrough; absent schema surfaced as `None`, not an error) | unit + integration | S-01.3 | mock platform; live Langfuse for integration |
| TP-36 | ADR-0005 #5 / F3 / DEBT-03 | `score_id(run_id, document_id, score_name)` = `uuid5(NAMESPACE, "run_id|document_id|score_name")`; same inputs → same id; different `run_id` → different id; `NAMESPACE` is a module-level literal UUID constant (static check, not generated); a retried `write_scores` in one invocation sends identical ids | unit + contract | S-01.3 | none |
| TP-37 | ADR-0005 #5 / N26 | Two invocations under the same `run_name` → distinct `run_id`s → no score overwrite; verified via `/v3/scores` with bounded poll (30 s max wait, 1 s poll interval) | integration | S-01.3 | self-hosted Langfuse pinned 4.38.0 + creds |
| TP-38 | ADR-0005 #6 / F3 | v4 `events_only`: traces + dataset-run linkage emitted via OTLP / v4 SDK, scores via `/api/public/scores` (no trace events on `/api/public/ingestion`), reads via `/v3/scores`; assertions poll with a bounded wait (30 s max wait, 1 s poll interval; no read-after-write) | integration | S-01.3 | self-hosted Langfuse pinned 4.38.0 + creds |
| TP-39 | DEBT-13 / INV-03 / CT-03 | One `prompt:<16-hex>` score per golden prompt; name matches `^prompt:[0-9a-f]{16}$` = first 16 hex of `sha256(prompt_key.encode("utf-8"))` (verbatim key; a non-ASCII prompt pins the encoding); deterministic across runs; raw prompt text never appears in any score name | contract | S-01.3 | none |
| TP-40 | ADR-0005 #8 / CT-04 | Dataset schema hash ≠ committed schema hash, or schema absent → `RunAborted(schema_drift)`, non-zero exit, **zero** IDP calls, **zero** scores written; key-order/whitespace differences alone do NOT trigger drift (canonical JSON); **empty dataset + drifted schema → `schema_drift` (not `empty_set`)**; pre-run order fetch → drift → empty → N28 asserted; no `run_status` marker written; log carries both hashes (or `absent`) | unit + contract | S-01.4 | mock platform |
| TP-41 | ADR-0005 #5 / F4 | `write_scores` 5xx retried within the ADR-0004 absolute deadline with identical score ids; deadline exhausted → abort `hard_failure`, non-zero exit | unit | S-01.4 | mock platform |
| TP-42 | ADR-0005 #4 (never drop) | Provisioning function against a mock REST client: expanded/contracted schema upserts succeed; no call path issues a schema delete or sends an empty/`null` schema; a provisioning attempt that would remove the schema raises | unit | S-01.3 | none |
| TP-43 | ADR-0005 #6 / N10 (OTLP failure) | Failing OTLP exporter at `flush()` → typed platform error → orchestrator abort `flush_failed`, non-zero exit (not swallowed as best-effort) | unit | S-01.3 (error) / S-01.4 (mapping) | none |
| TP-44 | NFR N5 / N23 / INV-02 (Basic-auth redaction) | Captured log output from the raw-REST provisioning step and the OTLP exporter (incl. on 4xx/5xx) contains neither the `Authorization: Basic …` value nor `LANGFUSE_SECRET_KEY` | unit | S-01.3 | none |
| TP-45 | INV-01 (OTLP span attributes) | In-memory span exporter: no span attribute/event carries file bytes, a file path, or `IDP_DOCUMENT_DIR`-resolved path — only `document_id` | contract | S-01.3 | none |
| TP-46 | NFR N28 (pre-run structural validation) | One malformed golden item → abort `malformed_golden` before any IDP call; error/log carries `document_id` + JSON path only, never the golden value; validator is the classifier's N22 golden validation (no `jsonschema` import in orchestration — static grep) | unit | S-01.4 | none |

**EX-n coverage assertion:** every EX-n in this epic's scope is covered — EX-A1-1 (TP-07), EX-A1-2 (TP-08), EX-A1-3 (TP-09), EX-A1-4 (TP-10), EX-A1-5 (TP-11) from UC-01; EX-C1-1 (TP-26), EX-C1-2 (TP-27) reused from `docs/init/use-cases-seed.md` UC-C1 for the S-01.5 spike. All in-scope EX ids map to test-plan rows tagged with their EX id.

**Credentials required for integration/e2e (MISSING at original planning time; both sets configured locally in gitignored `.env` as of 2026-09-19 — Mestre handoff):**
- IDP: `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID` (plus a real action ID + published version, passed per run as `--action`/`--version`) — creds configured; the real action id + published version is what still blocks S-01.2 / S-01.4 / S-01.6 live work.
- Langfuse: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` + a self-hosted instance — configured and running (4.38.0); images must be pinned to 4.38.0 (F-1) before S-01.3/S-01.4 integration tests.

## Risks / open questions

- ~~Atchim re-review of ADR-0001/0002/0004 PENDING~~ — resolved (approved; ADR-0005 APPROVED R1–R4 2026-09-19).
- ~~ADR-0001 PROVISIONAL on SPIKE-01~~ — resolved: SPIKE-01 refuted the no-code form-mode premise; ADR-0005 (Langfuse, write-time golden integrity + client-id scores) supersedes ADR-0001.
- **Credentials:** IDP + Langfuse creds configured locally (2026-09-19). Remaining live-work blocker: a real IDP action id + published version (S-01.2/S-01.4/S-01.6).
- **Langfuse image unpinned (QA-S-01.5 F-1, Major):** `../langfuse/docker-compose.yml` uses floating `:4`; ADR-0005 relies on 4.38.0-specific behaviour (Ajv strict, 10k cap, `events_only`, `/v3/scores`). Pin before S-01.3 integration — user/Mestre.
- **N25 self-hosting obligations** (encryption at rest, DB access control, tested backup/restore) block any **real** golden load — not S-01.3 build/tests with synthetic data (Mestre).
- **MVP Curator UX gap (ADR-0005 Consequences):** raw-JSON editor blocks invalid saves silently; Epic E fixes; Curator runbook F7 to be carded under Epic C.
- **ASM-04 (shape variance) open, Low for UC-01 / Med for Epic F** — does not block UC-01 (single baseline action); spike before Epic F routing.
- **ASM-05 (Langfuse form-mode)** — resolved by S-01.5 (split verdict: form-mode refuted, integrity confirmed).
- ~~Doc drift: SEQ-UC-01 `get_golden_version`~~ — fixed 2026-09-18 (DEBT-07 closed).
- **Estimates:** Dengoso batch estimate 2026-09-18 covers the original tasks — see `## Estimates`. **Re-scope 2026-09-19:** new tasks (T-01.3.0, T-01.3.9, T-01.3.10, T-01.4.11) and changed tasks (T-01.3.1, T-01.3.3, T-01.3.5, T-01.3.8, T-01.4.2, T-01.4.3, T-01.4.5, T-01.4.7) are `est: TBD` — Dengoso re-estimates next; the table values for changed tasks are stale until then. T-01.3.11 is a Soneca docs task (not pointed). No card ships to a board without an estimate (boardless here, but the discipline holds).

## Estimates

Dengoso batch estimate, 2026-09-18. Story-points on the Fibonacci scale (1/2/3/5/8). Supersedes the per-task `est: TBD` inline markers **for unchanged tasks only**. Re-scope 2026-09-19: rows marked † are changed and pending re-estimate; new tasks T-01.3.0 / T-01.3.9 / T-01.3.10 / T-01.4.11 are not in the table yet (TBD). Totals NOT recomputed.

| Task | Pts | Justification |
|---|---:|---|
| **S-01.1** | **22** | |
| T-01.1.1 | 5 | Core `classify()` over six verdicts and four field types with per-type canonical forms and format-vs-value tiers — the heaviest pure-logic piece; TDD matrix is substantial |
| T-01.1.2 | 2 | `overall_gate()` is a small aggregation: FAIL iff missing/wrong_value AND critical — simple once `classify()` exists |
| T-01.1.3 | 5 | Line-item `match_key` pairing with per-column sub-verdicts plus new_line/missing unmatched-row handling — non-trivial set-matching with many edge cases |
| T-01.1.4 | 3 | Typed `ClassifierError` family + malformed golden/actual shape validation — bounded validation surface |
| T-01.1.5 | 3 | Port 11 spike tests + canonical-form/format/criticality edge-case matrix — test authoring, no new production logic |
| T-01.1.6 | 2 | Contract test CT-02 pinning `Verdict` TypedDict, six literals, verdict-key union |
| T-01.1.7 | 2 | pytest-benchmark gate for <100ms p95 over ≤50 fields / ≤500 rows — small once `classify` is correct |
| **S-01.2** | **31** | |
| T-01.2.1 | 3 | `IDPAdapter` Protocol + `MuleSoftIDPAdapter.extract()` skeleton (ADR-0002) — defines the seam |
| T-01.2.2 | 3 | OAuth2 client-credentials + instance-held `TokenCache` with refresh margin — standard but security-sensitive |
| T-01.2.3 | 5 | Submit (non-retried, bounded timeout) + poll loop with configurable terminal-status allowlist + fail-closed — substantial I/O control flow |
| T-01.2.4 | 5 | `normalize()` walking pages[].fields/tables/prompts with three merge semantics + duplicate-prompt raise — core transform, many branches |
| T-01.2.5 | 5 | Untrusted-input validation family (unsafe_field_name, duplicate_prompt, value/table_too_large, NaN confidence, three-state) — broad, security-critical |
| T-01.2.6 | 3 | Contract test CT-01 against captured fixtures incl. mypy TypedDict checks — depends on S-01.6 fixture |
| T-01.2.7 | 2 | Monotonic-clock budget math + per-document timing metric — small, contained |
| T-01.2.8 | 2 | Structured logging + token redaction (NFR N5, N23, INV-02) — small but compliance-load-bearing |
| T-01.2.9 | 3 | Integration tests against live IDP, skipped in CI — blocked on MISSING creds |
| **S-01.3** | **35** | re-estimated 2026-09-19 (Dengoso) after ADR-0005 re-scope; was 18 |
| T-01.3.0 | 5 | NEW: committed generic string-valued schema (typed if/then, keyed prompts via propertyNames), raw-REST upsert provisioning with never-drop/never-null guard (TP-42) + Basic-auth redaction (TP-44), live tables-block measurement (TP-33) + DATA-MODEL-01 "Schema limits" |
| T-01.3.1 † | 3 | Schema pass-through from `GET /api/public/v2/datasets/{name}`; absent → `None`. Ingestion transport is in T-01.3.10, not here |
| T-01.3.2 | 2 | `hash_dataset` SHA-256 over canonical JSON of document_id+golden, encoding pinned by contract test |
| T-01.3.3 † | 2 | Down from 3: pure `score_id()` uuid5 + pinned literal NAMESPACE + per-invocation `run_id` + typed retryable 5xx error. The retry loop lives in T-01.4.3 |
| T-01.3.4 | 2 | `run_status` aborted/complete metadata marker write path — small, contained |
| T-01.3.5 † | 3 | CT-03 grows by the `prompt:<16-hex>` family (UTF-8 pinned via a non-ASCII key; raw prompt never in a name); N26 rests on distinct `run_id` |
| T-01.3.6 | 1 | INV-01 payload assertion on `write_scores` (the OTLP-span half is counted in T-01.3.10a) |
| T-01.3.7 | 1 | Module-boundary grep/ruff config asserting Langfuse SDK imports stay in platform/ |
| T-01.3.8 † | 5 | Up from 3: bounded `/v3/scores` poll helper (30 s / 1 s), live TP-33/34/35/37/38. Blocked on F-1 (images pinned to 4.38.0) |
| T-01.3.9 | 3 | NEW, CT-05: recursive schema walker (typed if/then, required ⊆ properties), minified length < 10k, exact propertyNames pattern, accept/reject on DATA-MODEL-01 §1 example vs "twelve fifty" |
| T-01.3.10a | 5 | NEW, split per Dengoso: OTLP / v4 SDK traces + dataset-run linkage; own exporter wrapper so export failure surfaces at `flush()` → `flush_failed` (TP-43; BatchSpanProcessor swallows errors by default); INV-01 on spans (TP-45); OTLP auth redaction. Least-known piece: the v4-capable SDK is unconfirmed |
| T-01.3.10b | 3 | NEW: scores via `/api/public/scores` with the deterministic id, `/v3/scores` reads, SDK version pinned in the lock |
| T-01.3.11 | — | NEW, Soneca docs (DEBT-13): not pointed |
| **S-01.4** | **37** | re-estimated 2026-09-19 (Dengoso); was 34 |
| T-01.4.1 | 3 | `run_eval(action_id, version, run_name)` facade + CLI with `--version` required / `--action` default + format validation (TP-31); load_dotenv() first |
| T-01.4.2 † | 5 | Taxonomy grows to ~11 reasons (`schema_drift`, `malformed_golden`); pre-run aborts write no marker. More enumerated paths, same structure |
| T-01.4.3 † | 5 | `write_scores` 5xx retry (TP-41, exhaustion → `hard_failure`) reuses the absolute-deadline full-jitter helper — a second consumer, not new math |
| T-01.4.4 | 3 | 401 mid-run refresh-then-fail-closed state machine with initial-vs-mid-run split — security-sensitive |
| T-01.4.5 † | 3 | Up from 2: pinned five-step pre-run order (TP-40 incl. empty + drifted → `schema_drift`), TP-46, alias + identity test + S-01.1 `/test` re-stamp. **Risk:** `MalformedGoldenError` carries only a message, and some messages echo golden content (`{ftype!r}`, the verbatim prompt key). `malformed_golden` needs a structured JSON-path attribute (an additive S-01.1 change) or an orchestration-side wrapper, so no golden value reaches logs |
| T-01.4.6 | 3 | Gate-before-write ordering, golden-version recording via `hash_dataset`, run_status marker writes — orchestration glue |
| T-01.4.7 † | 3 | CT-04 parametrized over the taxonomy: two new reasons plus the "empty + drift" ordering case |
| T-01.4.8 | 3 | Structured telemetry for every run and abort reason per NFR N10 — moderate emit surface |
| T-01.4.9 | 5 | e2e orchestration test against mock IDP+platform covering exit latency + all abort paths — substantial harness |
| T-01.4.10 | 2 | Credential-hygiene checks, gitleaks clean, pip freeze lock-file pin — small config/hygiene |
| T-01.4.11 | 2 | NEW: canonical-JSON (sorted keys, no whitespace) sha256 of the **parsed** schema vs the committed file; absent → drift; log both hashes; assert zero IDP calls/writes. Never hash raw bytes: Langfuse likely stores the schema as JSONB, which reorders keys |
| T-01.4.12 | — | NEW, Soneca docs: not pointed |
| **S-01.5** | **11** | |
| T-01.5.1 | 3 | Stand up self-hosted Langfuse via Docker + pin/record version — moderate ops, time-boxed |
| T-01.5.2 | 2 | Load representative nested golden (fields/tables/prompts) with scrubbed values — fixture authoring |
| T-01.5.3 | 3 | Enable form mode, perform EX-C1-1/EX-C1-2 interactions, capture screenshots — moderate manual exploration |
| T-01.5.4 | 2 | Record verdict in spike doc (Dengoso result capture; Soneca owns ADR update) — small |
| T-01.5.5 | 1 | Hand self-hosting obligations + Langfuse credential registration to Mestre — coordination hand-off |
| **S-01.6** | **12** | |
| T-01.6.1 | 2 | Confirm IDP credentials configured by Mestre + gain live-org access — mostly a blocker gate |
| T-01.6.2 | 3 | Observe terminal-status enum across several executions + record terminal vs success sets — moderate empirical |
| T-01.6.3 | 2 | Measure representative execution wall-clock + pin `IDP_EXECUTION_TIMEOUT_SECONDS` default — small observation |
| T-01.6.4 | 3 | Capture redacted raw-IDP fixture for CT-01 scrubbed per NFR N19 — care required to strip PII/creds |
| T-01.6.5 | 2 | Feed observations into ASM-01 + DATA-MODEL-01 §5 config defaults (Soneca owns ASM-01) — small for Dengoso's part |
| **Total** | **148** | 48 tasks (46 pointed; T-01.3.11 and T-01.4.12 are unpointed Soneca docs tasks). Re-estimated 2026-09-19; was 128 / 42 |