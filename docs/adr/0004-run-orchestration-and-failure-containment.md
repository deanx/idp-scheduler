# ADR-0004: Run orchestration & failure containment

**Status:** Proposed
**Date:** 2026-09-17
**Context (use case):** UC-01 (Run a baseline regression over a golden set)
**Risk:** High — the orchestrator owns the failure-containment contract (ASM-02: abort the entire run on timeout or hard IDP failure). A wrong containment decision makes the CI gate silently pass on a broken run — a Definition-of-Done concern. **Atchim review: APPROVED (top-level gate, 2026-09-18 — opus; reviewer-independent, all five axes PASS).**

> **Amended 2026-09-19 by ADR-0005 Decision #9 (record-after).** No platform write happens inside the per-document loop. Scores, the experiment and the flush all happen in a single `platform.record_run(...)` call after the loop. On abort, only the `run_status=aborted` marker is written; no partial scores. The bounded flush retry in #13 is dropped (a flush that failed cannot be re-sent). Flow §5e/§6, #11, #13, #15 and the "scores written incrementally" API bullet are to be read through Decision #9.

## Context

The orchestrator is the top-level component: it reads env via `load_dotenv()`, iterates the golden set, calls the IDP adapter (ADR-0002), classifies (ADR-0003), writes scores + gate to the platform (ADR-0001), records action version + golden version, and exits with a code that becomes the CI gate (F18).

Decided constraints that shape it:
- **ASM-02 (refuted → abort, 2026-09-17):** the orchestrator **aborts the entire run** on a per-document IDP timeout or hard IDP failure (exit non-zero, no partial run). The original "continue" guess was refuted by the human — a partial run with a silent gap would be confusing to interpret. This is decided; we design to abort, not to continue.
- **ASM-01 (mitigated by configurable allowlist):** terminal status is a configurable allowlist, never `== "SUCCEEDED"`.
- **OAuth token cached for the run** (BR7) — one token at run start, reused across documents, refreshed on expiry.
- **All credentials from env/secrets** (BR6) — `load_dotenv()` before any SDK client.
- **Empty golden set guard (A4)** — exit non-zero with a clear error, never silently succeed.
- **Auth failure fail-closed (A3)** — abort immediately on OAuth failure.
- **Version is the regression variable** (BR1) — passed as `--version` (required, no env fallback), never hard-coded. The action is passed as `--action` (optional, defaults to `IDP_ACTION_ID`). Amended 2026-09-19.
- **Reproducibility (F16, BR10)** — the run records action version + golden version.
- **Score names are stable** (BR11) — `field:<name>` and `gate`.

## Threat model

Trust boundaries: **IDP** (external OAuth), **evaluation platform** (external API-key, sensitive golden contents), **local document files** (PII-bearing).

- **Spoofing:** OAuth for IDP, API key for platform — both from env. Fail-closed on either credential missing.
- **Information disclosure:** local document files contain PII; they are read by the adapter for IDP submission and never uploaded to the platform (BR4, INV-01). Golden contents are sensitive; never logged in plain text (BR5, INV-02).
- **Denial of service:** IDP rate limit unknown; sequential-only at MVP. Per-document timeout. No retry on hard failure.
- **Repudiation:** run metadata records action version + golden version; the exit code is the CI gate's truth.
- **Tampering:** the orchestrator treats IDP output and platform reads as untrusted data; the classifier validates shapes (ADR-0003).

## Containment (guardrails — the load-bearing section)

This is the section Atchim reviews. The orchestrator must never silently pass a broken run. Branca co-designed the guardrails below; they are mirrored as NFR rows (N1, N5, N10, N14, N16, N19, N21, N22, N23) and as new invariants (INV-07, INV-08) at `docs/design/INVARIANTS.md`.

1. **Two distinct timeouts (Branca).** (a) **Submit-call timeout** on `POST .../executions` — an HTTP-level timeout, `IDP_SUBMIT_TIMEOUT_SECONDS` (default ≤ 30s). (b) **Poll-wall-clock timeout** covering submit + all polls cumulatively — `IDP_EXECUTION_TIMEOUT_SECONDS` (default 120s placeholder, pinned by /spike). The submit timeout is NOT retried (a hung POST is a hard failure → abort; re-submitting risks a non-idempotent second execution for the same doc). The poll budget is absolute from the first attempt (see #3).
2. **Per-document poll timeout (A1, AC5).** On poll-wall-clock timeout: surface the failing `document_id`, **abort the entire run**, exit non-zero with abort reason `hard_failure` (distinct reasons below). Do not continue to the remaining documents.
3. **Retry budget is INCLUDED in the per-document timeout (Branca + Atchim).** The deadline is absolute from the first attempt — backoff/retries do not extend the poll budget. Backoff spec: exponential full jitter, base 1s, cap 8s, max-attempts from config (default 3). 429 honors `Retry-After` capped at the **remaining** poll budget (do not extend the budget). A retry budget that exhausts before terminal status is a hard failure → abort.
4. **No-retry on poll timeout (Branca).** A timed-out execution is a hard failure → abort. Do not re-submit — the POST executions call is not idempotent for the same document, and a second execution could double-charge quota or produce a conflicting result. Abort reason `hard_failure`.
5. **Hard IDP failure (A2).** If the polled status is a terminal failure (in `IDP_TERMINAL_STATUSES` but not in `IDP_SUCCESS_STATUSES`) or the executions API returns an error: report the IDP error detail, **abort the entire run**, exit non-zero. Same abort decision as #2.
6. **Transient transport retry (bounded).** Transient transport errors (5xx, connection reset, 429) on a single HTTP call are retried per #3's backoff. A retry that exhausts its budget is a hard failure → abort. Auth failure handling is split — see #7.
7. **Auth failure — initial vs mid-run split (Branca + Atchim).**
   - **Initial auth failure (A3):** the OAuth token request fails at run start → fail-closed, no retry, abort immediately with abort reason `auth_failure`.
   - **Mid-run 401/403 (token expiry, not credential rejection):** attempt **one** token refresh. Refresh triggers at `expires_at - IDP_TOKEN_REFRESH_MARGIN_SECONDS` (default 60s). If refresh succeeds, retry the triggering request. If refresh fails with 401/403 mid-run, the credential has been revoked/rotated → abort immediately (no fall-back to re-running the triggering document; the whole run is non-reference, matching ASM-02). Abort reason `auth_failure`.
   - A 401/403 on the *initial* auth is NOT a refresh candidate — it is #initial above.
8. **Empty-set vs fetch-failure split (Branca).** `get_dataset` returns `[]` → abort reason `empty_set` (A4). A network error / 404 / auth failure on `get_dataset` → abort reason `dataset_fetch_failed`. Distinct reasons; both exit non-zero.
9. **Empty-set guard (A4).** If the dataset has no items: exit non-zero with `empty_set`; never silently succeed (a zero-exit on an empty run would be a false-green CI gate).
10. **Malformed golden-item guard (suggestion).** A4 guards the empty set; a golden item missing its `golden` key (or otherwise malformed) is caught by the **pre-run golden-set schema validation** (NFR absence-audit row: golden-set schema validation at load / pre-run — fail-fast over the entire golden set before any IDP call, not mid-run after quota is spent). `normalize()`/`classify()` shape validation (ADR-0002/0003) is the per-document seam; the pre-run validation is the set-level seam.
11. **Platform-write failure (A2-shape).** If `write_scores` raises: abort the run, exit non-zero, surface which document's scores failed to persist. Do not continue — a run with missing scores on the platform is not a complete reference.
12. **`write_scores` idempotency-or-no-retry (Branca).** Retried score POSTs must carry an **idempotency key** derived from `(run_name, document_id, field_name)`. If Langfuse's score API does not support client-supplied idempotency keys (confirm at SPIKE-01 or a follow-up), do **NOT** retry `write_scores` — a 5xx on write is an abort (`hard_failure`), not a retry. **The gate result is computed BEFORE the write and never inferred from what landed on the platform** (pin as INV-08) — the platform is a record, not the source of truth for the gate.
13. **`flush()` failure (Branca + Atchim agree).** `flush()` is idempotent ("commit pending"), so it gets a bounded retry (3, exponential, full jitter). If the retry budget exhausts → exit non-zero with abort reason `flush_failed`. Per-document scores already written remain on the platform under the failed run name as evidence; consumers must not treat the run as a reference if flush did not complete. Mirrored in the exit-code contract below.
14. **Failed run is marked on the platform (Atchim).** On mid-run abort, only the exit code + local log say "aborted"; the platform would otherwise show k scores with no marker (misreadable as a small complete baseline). On abort, the orchestrator writes a `run_status=aborted` metadata flag (and the failed `document_id`) to the run on the platform **before** exiting non-zero. Observable behavior: a zero-exit run has `run_status=complete`; a non-zero run has `run_status=aborted` on the platform.
15. **No partial-run output as a reference.** Because we abort on the first hard failure, the platform may hold scores for documents processed before the failure. Mitigation: the run name is per-invocation; an aborted run is a *failed run*, not a reference, and is marked `run_status=aborted` (#14). CI treats the run as failed, not as a baseline.
16. **Monotonic clock for the poll budget (Branca).** The poll budget uses `time.monotonic()` (or an asyncio deadline), NOT `time.time()` — NTP-jump safety. Pinned as INV-07. The submit-call timeout is likewise monotonic.
17. **Fail-closed on ambiguous/missing status (Branca).** A status missing/null in the poll body → abort, do not infer "SUCCEEDED". An unknown status (not in any allowlist) → keep polling (it may not be terminal yet). If the poll budget expires on an unknown status, abort reason is `unknown_status_timeout` (distinct from `hard_failure`).
18. **Token redaction on the failure path (Branca).** No `access_token`, `Authorization` header, or `client_secret` in any log/exception/capsys — including the refresh-failure path (#7). Mirrored in NFR N23.
19. **Credential hygiene.** `load_dotenv()` is called before any SDK client is constructed (BR6, INV-05). No credential is ever logged; `Authorization` headers and platform API keys are redacted in any error/observability output.

**Abort-reason taxonomy (NFR N10):** `hard_failure` (timeout / terminal-failure status / transient-retry-exhausted / poll-timeout / platform-write non-retriable) · `unknown_status_timeout` (poll budget expired on an unknown status) · `auth_failure` (initial auth fail / mid-run refresh fail) · `dataset_fetch_failed` (network/404/auth on `get_dataset`) · `empty_set` (A4) · `flush_failed` (#13). Each maps to a non-zero exit; the reasons are distinct for observability, not distinct exit codes (the exit-code contract stays `0`/non-zero — see §Exit-code contract).

## Options considered

### Option A — Continue on per-document failure (the refuted ASM-02 guess)
**Pros:** produces *some* scores when one document is slow.
**Cons:** a partial run with a silent gap is confusing to interpret and can produce a false-green CI gate (the regressing document is the one that timed out). **Refuted by the human 2026-09-17.** We do not design this.

### Option B — Abort the entire run on first hard failure / timeout (ASM-02 decided)
**Pros:** the exit code is an honest signal — a non-zero exit means the run is not a usable reference, no silent gap. Matches the human's "abort on timeout to avoid confusion" decision.
**Cons:** one slow/broken document fails the whole run. Mitigation: bounded retry for transient errors; the per-document timeout is configurable; the orchestrator logs which document failed so the engineer can investigate.

### Option C — Abort, but keep partial scores already written as a "failed run"
**Pros:** preserves evidence for debugging.
**Cons:** a failed run is not a reference. We mark the run name as failed and exit non-zero; CI never treats a failed run as a baseline. This is what we do (it is B with a note on run naming).

## Decision

We choose **Option B/C** — abort the entire run on first hard failure or timeout, keep any already-written scores under a per-invocation run name marked failed, exit non-zero. This is ASM-02 decided; we do not design "continue".

### Orchestrator interface (contract-first)

```python
def run_eval(action_id: str, version: str, run_name: str) -> int:
    """
    Run the baseline regression for `action_id` at `version` over the configured golden set,
    writing per-field + gate scores to run `run_name` on the platform.
    Returns process exit code: 0 = all gates PASS; non-zero = any FAIL or run error.
    """
```

CLI (Epic D, F10): `run_eval --version <v> --run <name> [--action <id>]`. `load_dotenv()` is the first line of the script before any SDK client.

**Run parameters vs environment (amended 2026-09-19).** `--version` is **required with no env fallback** — a silent default could record a run against the wrong version, which INV-04 exists to prevent. `--action` falls back to `IDP_ACTION_ID` when omitted (local convenience); if neither is set the CLI exits non-zero before any network call. The CLI validates both (`action_id` UUID, `version` `^[A-Za-z0-9._-]{1,64}$`) before `run_eval` is entered. `.env` holds only environment facts: credentials, org/region, hosts, timeouts, `IDP_DOCUMENT_DIR`.

### Flow
1. `load_dotenv()`; read all credentials + config (`IDP_*`, platform key, `IDP_TERMINAL_STATUSES`, `IDP_SUCCESS_STATUSES`, `IDP_EXECUTION_TIMEOUT_SECONDS`, `IDP_SUBMIT_TIMEOUT_SECONDS`, `IDP_TOKEN_REFRESH_MARGIN_SECONDS`, retry budget).
2. Construct `platform = make_platform()` (ADR-0001) and `adapter = make_idp_adapter()`.
3. `dataset = platform.get_dataset(name)`. **Single-fetch split (Branca):** `dataset == []` → abort `empty_set` (A4); network/404/auth failure on `get_dataset` → abort `dataset_fetch_failed`. Both exit non-zero. **Pre-run golden-set schema validation** (NFR absence-audit row) runs over the entire `dataset` here, fail-fast, before any IDP call — a malformed golden item (missing `golden` key, etc.) aborts before quota is spent.
4. `golden_version = hash_dataset(dataset)` — **content hash computed from the `get_dataset` result** (single fetch, ADR-0001 §API contract). There is **no** `platform.get_golden_version` call; a second fetch would TOCTOU against the `dataset` used for classification. Load-bearing for INV-04.
5. For each `item` in `dataset` (sequential — no concurrency at MVP):
   a. **Resolve `document_id → path`:** the orchestrator resolves `IDP_DOCUMENT_DIR / {item.document_id}` to a local path (the document files live in a configured local dir; the platform stores only `document_id`, never a path blob — INV-01). The orchestrator owns the resolution (the adapter takes a resolved path); this keeps the adapter pure-HTTP and the path convention in one place. `IDP_DOCUMENT_DIR` from env. (INV-01 interaction: no file-path blob beyond `document_id` is stored on the platform; the path is reconstructed locally at run time.)
   b. `actual = adapter.extract(resolved_path, action_id, version)` — on timeout/hard-failure/auth-error: write `run_status=aborted` + failing `document_id` to the run on the platform (#14), then abort, exit non-zero (Containment #2–#7).
   c. `verdicts = classifier.classify(item.golden, actual)`.
   d. `gate = classifier.overall_gate(verdicts)` — **the gate is computed here, BEFORE the platform write** (INV-08); the platform is a record, not the source of truth for the gate.
   e. `platform.write_scores(run_name, item, verdicts, gate, action_id=action_id, action_version=version, golden_version=golden_version)` — with idempotency key `(run_name, document_id, field_name)` if supported, else no-retry on 5xx (Containment #12). On `write_scores` raise: write `run_status=aborted` (#14), then abort, exit non-zero (Containment #11).
6. `platform.flush()` — bounded retry (3, exponential, full jitter, idempotent); on budget exhaustion → write `run_status=aborted` if possible, exit non-zero with reason `flush_failed` (Containment #13).
7. Write `run_status=complete` to the run metadata on the platform.
8. Aggregate gates: if any document's gate is `FAIL` → exit non-zero. Else exit zero.

### Exit-code contract (F18 — CI gate)
- `0` — every document processed, every gate `PASS`, `flush()` completed, `run_status=complete` written.
- non-zero — any of: a gate `FAIL`; a timeout; a hard IDP failure; an auth failure; an empty golden set; a dataset fetch failure; a `write_scores` failure (non-retriable); a `flush` failure; an unknown-status timeout. The abort reason (`hard_failure` / `unknown_status_timeout` / `auth_failure` / `dataset_fetch_failed` / `empty_set` / `flush_failed`) is logged for observability; it does NOT fragment the exit-code namespace. CI treats non-zero as "the run is not a usable reference; block the PR."

## Design patterns

- **Facade** — `run_eval()` is a thin facade over `adapter + classifier + platform`; the CLI is one call. Idiomatic: a module-level function. The orchestration sequence is the only place these three collaborate.
- **Template Method (function)** — the per-document loop body (extract → classify → gate → write) is a fixed sequence; in Python this is a plain loop, not a class hierarchy. Ad-hoc is correct here — forcing a Template Method class would be needless abstraction.
- **Chain of Responsibility (light)** — the abort-on-failure path is a series of guards (timeout, hard failure, auth failure, empty set, platform-write) each raising `RunAborted`. Idiomatic: exceptions, not a chain object.

## API contract (observable behaviors — Hyrum's Law)

- The exit code is the CI gate's truth. Consumers (CI, the engineer) may assume `0` ⟺ all gates PASS and no run error.
- A non-zero exit means the run named `run_name` is **not a reference** — consumers must not compare future runs against an aborted run.
- Every successful run records `action_id`, `action_version` and `golden_version` in its platform metadata (INV-04). Consumers may assume a zero-exit run has all three.
- Scores are written incrementally (per document) and flushed once at the end; consumers may see partial scores for an aborted run under the failed run name — they are evidence, not a baseline.

Versioning strategy: the exit-code contract is immutable — `0`/non-zero semantics never change. New abort reasons map to non-zero, not to a new exit-code namespace (we do not fragment the CI signal).

## Consequences

- **Positive:** the CI gate is honest — a false green is impossible while the containment guards hold. The run is either a complete reference (exit 0) or a failed run (non-zero), never a partial reference.
- **Negative:** one broken document fails the whole run. This is the human's decision (ASM-02) and is correct for a regression gate — a partial run is worse than a clear failure. Bounded retry cushions transient errors.
- **Follow-up work:**
  - `/spike` to pin the timeout value, retry budget, and terminal-status allowlists against the live org.
  - `/harden` (Branca) must verify the Containment section before Done — the harden gate (`docs/qa/HARDEN-01.md`) is required because the orchestrator has a failure-prone external integration.
  - Telemetry: the observability contract (NFR-01) requires per-document timing, the abort reason, and the failed `document_id` to be logged structurally.

## Reversal cost

**Medium-High.** The containment contract (abort, not continue) is the CI gate's honesty — reversing it to "continue" would silently break the gate's meaning. The exit-code contract is effectively immutable (consumers in CI depend on it). Individual guardrails (timeout value, retry budget) are config and reversible; the *policy* (abort on hard failure, fail-closed on auth, empty-set guard) is not.

Atchim review: APPROVED (top-level gate, 2026-09-18 — opus; reviewer-independent, all five axes PASS). Non-blocking debt (item #14 semantics clarification): on the platform-write-failure path, the `run_status=aborted` marker write is **best-effort** — if writing the marker itself fails, the orchestrator still exits non-zero (the exit code is the CI gate's truth regardless of whether the platform marker landed); do not retry the marker write past one attempt. Other deferred items: SEQ-UC-01 diagram drift (stale `get_golden_version` — Soneca) and `write_scores` idempotency-key support deferred to SPIKE-01.