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
---

## Amendment 2026-09-21 (T-01.4.12) — S-01.4 as built

Additive. Nothing above is rewritten; where this section and an earlier one disagree, **this section wins for anything dated on or after 2026-09-21**. Written by Soneca against the code that landed on `feat/S-01.2-idp-adapter` (780 tests green), not against intent.

### A1. Pinned pre-run order (supersedes Flow §3–§4's looser wording)

The pre-run chain is an **ordered** chain, not a set of independent guards. As built (`orchestration/prerun.py`, wired in `facade.py`):

1. `platform.get_dataset(dataset_name)` → any network/404/auth failure aborts `dataset_fetch_failed`.
2. **`schema_drift`** — the dataset's `expectedOutputSchema` hash vs. the committed schema hash (ADR-0005 Decision #8).
3. **`empty_set`** — `items == []` (A4).
4. **`malformed_golden`** (N28) — structural validation over the whole set, via the `is`-identical alias of the classifier's own `_validate_golden` (ADR-0005 #8: no second `jsonschema` dialect).
5. `golden_version = hash_dataset(dataset["items"])` (same object, single fetch — the TOCTOU guard of Flow §4) and `run_id` / `experiment_name`.
6. **Only then** the first IDP call.

The order is load-bearing and is part of the contract, not an implementation detail: **when two guards would both fire, the earlier one is the reported reason.** Pinned at the `run_eval` seam by `test_run_eval_reports_schema_drift_not_empty_set_when_both_would_fire` — an isolated per-guard test does not pin an order. Rationale: drift is a statement about the *set's schema*, emptiness and malformation are statements about its *contents*; diagnosing a drifted schema as "empty set" sends the reader to the wrong place. Cost of a wrong order is a misleading abort reason, never a false green.

### A2. Abort taxonomy as built — 10 reasons

`AbortReason` (`orchestration/errors.py`) is the single authority; the list in §Containment's "Abort-reason taxonomy (NFR N10)" was 6 and is superseded by these 10:

`dataset_fetch_failed` · `schema_drift` · `empty_set` · `malformed_golden` · `auth_failure` · `unknown_status_timeout` · `hard_failure` · `malformed_actual` · `flush_failed` · **`path_containment_violation`**.

`path_containment_violation` (new 2026-09-21) fires when a `document_id` — **golden-set content, writable by a Curator or anyone with platform write access, therefore an untrusted string** — resolves outside `IDP_DOCUMENT_DIR`: an absolute path, a `..` traversal, or a symlink escape (realpath containment). Raised **before** the IDP call for that document, so the adapter never sees the escaped path. N28 validates JSON shape only; it never said anything about filesystem safety. Only the `document_id` is logged (via `sanitize_for_log`), never the candidate or resolved path.

**The exit-code namespace does not fragment.** All 10 map to a plain non-zero (CT-04: `0` iff every gate PASS and no run error). New reasons are added to the taxonomy, never to the exit-code space — the versioning rule of §API contract is unchanged and remains immutable. The CLI's former reserved exit code `3` (`NotImplementedError`) is **retired**; a catch-all now maps any escaping exception to a sanitized non-zero `1`.

### A3. #12/#13 retry ownership — corrected

Per ADR-0005 Decision #9 and the QA-01-S-01.3 F-2 ruling:

- **#12 (score-write retry) is adapter-private.** The retry, its idempotency key and its deadline live entirely inside the platform adapter. The orchestrator does not see attempts; it sees one raise or none.
- **#13's bounded `flush()` retry is dropped**, as already stated in the 2026-09-19 blockquote. `FlushFailedError` → abort `flush_failed`, no retry.
- **The orchestrator never retries `record_run`.** One call, one outcome: `FlushFailedError` → `flush_failed`; `ExperimentRecordFailedError` / `ScoreWriteFailedError` (or any other platform error) → `hard_failure`. Re-reading #11/#12/#13 as "the orchestrator retries" is the misreading this amendment exists to kill — a second `record_run` from the orchestrator would duplicate an experiment, and the honest signal is a failed run, not a re-sent one.

### A4. The `run_status=aborted` marker rule

Best-effort, **one attempt**, never retried, and **only once a run exists** — i.e. only after step 5 of A1 has generated `run_id`. **A pre-run abort writes no marker at all** (there is no run to mark, and inventing one would put a phantom aborted run on the platform). If the marker write itself fails, the run still exits non-zero — the exit code is the CI gate's truth regardless of whether the marker landed (this promotes the 2026-09-18 Atchim non-blocking note to the contract). Pinned by `test_run_eval_writes_no_marker_on_a_pre_run_abort` (mutation-verified). `mark_run_status("complete", ...)` on the success path obeys the same best-effort/one-attempt rule.

### A5. NFR-01 N10/N26 wording aligned to the telemetry actually emitted

N10's row is restated to match what fires (`facade.py`, T-01.4.8) rather than what was imagined in 2026-09-17:

- **run-start** — one line after the pre-run chain passes: `run`, `experiment`, `action`, `version`, `golden_version`, **`items=<count>`**.
- **run-end** — one line at **every** exit point, including every pre-run abort: `outcome` (`success` / `gate_failed` / `aborted`), `exit_code`, `pass_count`, `fail_count`, `elapsed_seconds` (monotonic).
- **per-document** — `document_id`, `gate`, `elapsed_seconds` on **the same line**; a timing line untethered from the document it describes is not acceptable telemetry.
- **abort** — `reason` (one of A2's 10), `document_id` (or `<none>` for run-level aborts), sanitized `detail`.

"Per-document start/end" in the original N10 row is satisfied by the single per-document completion line carrying elapsed; two lines are not required, and the run-start item count plus run-end counts give the reader the set-level picture. N26 (concurrency / run-name idempotency) is restated: uniqueness is carried by the per-invocation `run_id` and the composed `experiment_name` — **not** by the caller's `--run` name, which may legitimately repeat. Score ids are `uuid5(NAMESPACE, run_id|document_id|score_name)`, so a repeated `--run` cannot overwrite a previous run's scores.

### A6. `GOLDEN_DATASET_NAME` — the golden-set pointer (DEBT-48)

Escalated by the Atchim gate: the implementation introduced the single pointer to the golden set with no ADR behind it. Fail-closed plus a docstring was enough to merge and is not enough to stand, because **a pointer that silently forks between CI and local produces a green run against the wrong dataset** — the worst failure this product can produce, and one no test in this repo can detect.

**Decision.**
1. **Env var name `GOLDEN_DATASET_NAME`** is kept (unprefixed and platform-neutral: the golden set is a domain concept, not a Langfuse one — N24).
2. **No default, ever — not in code, not in `.env.example` as a live value.** Missing or empty → clear message naming the variable, non-zero exit, **zero network calls** (N6 shape). A default is precisely the mechanism by which CI and local fork silently.
3. **The CLI accepts it explicitly: `--dataset <name>`**, with the same precedence shape as `--action`/`IDP_ACTION_ID` — flag wins, env is the fallback, neither set is a pre-network non-zero exit. CI pipelines **must** pass `--dataset` explicitly on the command line next to `--version`, so the pointer is visible in the PR diff that changes it rather than buried in a runner's environment. Local convenience keeps the env fallback.
4. **Every run records which dataset it used.** `RunMetadata` gains a fourth field, `golden_dataset_name`, carried on `record_run` and on `mark_run_status` alongside `action_id` / `action_version` / `golden_version`; the run-start log line carries it too. **INV-04 is widened from three fields to four**: no zero-exit run exists without action id, action version, golden version *and* golden dataset name. `golden_version` is a content hash — it proves *what* was compared and cannot tell a reader *which* named set it came from. After the fact, a reader must be able to answer "which golden set was this green build measured against?" from the run itself.

**Consequence for S-01.4's DoD.** This is a scope addition, honestly labelled: `--dataset` on the CLI, the `RunMetadata` fourth field through `record_run` / `mark_run_status` / the adapter, the run-start log line, `.env.example` and the CI invocation. INV-04's check and CT-03's metadata assertion both widen to four fields, so **the existing INV-04/CT-03 tests must be updated, not merely added to** — S-01.4 cannot be Done on a three-field `RunMetadata`. Carded from DEBT-48; if it does not fit this story, it is a blocking follow-up, not a silent deferral, and until it lands the gate's provenance claim is weaker than this ADR states.

### A7. `RunAborted` is the *post-run* abort shape; the pre-run guards return non-zero directly (2026-09-21)

Raised by an independent S-01.4 coverage audit: SPEC-01 S-01.4's DoD said *"every abort path raises `RunAborted`"*, and three paths do not — **a missing credential** (`MissingCredentialError`, caught and logged in `run_eval`'s pre-run `except` chain), an **empty/whitespace `dataset_name`**, and a **missing `IDP_DOCUMENT_DIR`**. All three `return 1` directly (`orchestration/facade.py`). The exit code is tested; the raise shape is not.

**Decision: the DoD was over-broad, not the code.** The contract is and remains the **exit code** (§Exit-code contract / CT-04: `0` iff every document's gate is PASS and no run error occurred; every other outcome is a plain non-zero). `RunAborted` is the *internal* mechanism by which a failure that happens **once a run exists** carries its `AbortReason` back to the seam — it is not itself the invariant.

**The dividing line is A1 step 5 — does a `run_id` exist yet?**

- **Before it** (credential resolution, `dataset_name`, `IDP_DOCUMENT_DIR`): no run, no `run_id`, no `AbortReason` that would mean anything on the platform → log, `run_end outcome=aborted exit_code=1`, `return 1`. **No `run_status` marker**, exactly as A4 already requires of every pre-run abort.
- **After it** (and for the pre-run chain's own reasons — `dataset_fetch_failed`, `schema_drift`, `empty_set`, `malformed_golden` — which are *statements about a named dataset* and already carry a taxonomy reason): `RunAborted(reason=...)`, and from `run_id` onward the best-effort marker too.

**Why not force the three to raise.** It would buy a uniform type and cost the honest one: each would need an `AbortReason` invented for a run that does not exist, and A2's taxonomy — whose 10 members are all statements about a *run's* data or transport — would acquire members describing the *caller's environment*. Nothing downstream consumes the exception type (the CLI consumes the `int`), so the uniformity would be decorative. A caller who wants a typed pre-run failure should read the log line and the non-zero code, which are the contract.

**Consequence.** No code change for S-01.4. The obligation is a **test** one, and it already exists as an exit-code assertion per path (the N6 unit test unsets each key and asserts non-zero exit + no outbound call). If a future consumer ever needs to distinguish pre-run from in-run failure programmatically, the right move is a **distinct exit code**, not a widened `RunAborted` — and that is an API-contract version bump under §API contract, not a refactor.

### A8. Run-identity parameters are CLI-only — `--action` and `--dataset` become required, no env fallback (user decision, 2026-09-22)

**The rule.** Every parameter that *defines what a run measured* is supplied on the command line and is **required**; the environment supplies only what *describes the machine and the account*. There is no fallback across that line, in either direction.

| Run identity — CLI, required, no env fallback | Environment description — `.env`, fail-closed |
|---|---|
| `--action` (IDP action id) | `IDP_CLIENT_ID` / `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID` |
| `--version` (action version) | `IDP_DOCUMENT_DIR`, `PLATFORM`, `LANGFUSE_HOST` + keys |
| `--dataset` (golden-set name) | `IDP_*_TIMEOUT_SECONDS`, `IDP_TERMINAL_STATUSES`, `IDP_SUCCESS_STATUSES` |
| `--run` (run name) | — |

Missing or empty → the existing N6 shape: message naming the **flag**, non-zero exit, zero network calls. The user's words: *"the system must not depend on hardcoded IDP parameters to work — it will kill the dynamic proposal of the system."* A tool whose purpose is to compare one action/version/dataset triple against another cannot carry any member of that triple as ambient state.

**What this supersedes, named so no reader is left with two live rules.**
1. **A6 clause 3's env-fallback sentence** — *"flag wins, env is the fallback … Local convenience keeps the env fallback"* — is **superseded**. `--dataset` is required; `GOLDEN_DATASET_NAME` is read by no production code path. A6's clauses 1, 2 and 4 (the name, no-default/fail-closed, and the fourth `RunMetadata` field / four-field INV-04) stand unchanged.
2. **The 2026-09-19 `--action` default** — this ADR's own §Design constraints line (*"The action is passed as `--action` (optional, defaults to `IDP_ACTION_ID`)"*) and §API contract's *"Run parameters vs environment"* paragraph (*"`--action` falls back to `IDP_ACTION_ID` when omitted (local convenience)"*), mirrored in ADR-0002 §Decision, SPEC-01 and DATA-MODEL-01 §5 — is **superseded**. `--action` is required; `IDP_ACTION_ID` is no longer a default. Per this amendment's preamble those earlier lines are left in place, not rewritten; **A8 wins.** The same applies to every `[--action <id>]` bracketed-optional invocation example above and in SEQ-UC-01 — read them as `--action <id>`, required.

This is A6's own argument applied consistently. A6 ruled that a pointer which can silently fork between CI and a laptop yields a green run against the wrong target, then kept a fallback for convenience — which is exactly the forking mechanism it had just condemned, merely narrowed to one variable. The user closed the inconsistency in the direction A6's reasoning already pointed.

**Consequence for CI.** The full invocation — `run_eval --action <id> --version <v> --dataset <name> --run <name>` — appears in the workflow file, therefore in the PR diff. A change to *what is being measured* becomes a reviewable line in a diff rather than an ambient runner setting that no reviewer sees. This is the same provenance property as A6's `RunMetadata` fourth field, one step earlier: the metadata proves after the fact what a run measured; the diff shows beforehand that someone changed it.

**What the env vars are for now.** `IDP_ACTION_ID`, `GOLDEN_DATASET_NAME` and the `IDP_TEST_*` family (e.g. `IDP_TEST_DOCUMENT_PATH`) are **test-harness convenience only** — read by integration tests and by local `make`/shell wrappers to compose a command line, **read by no production code path**. `IDP_ACTION_ID` and `GOLDEN_DATASET_NAME` are therefore **demoted from config to test fixtures**; DATA-MODEL-01 §5 keeps them only with that label. Keeping the values somewhere to make testing easy is fine — the rule is that nothing in `src/` may read them. A cheap pin: a static test asserting the strings `IDP_ACTION_ID` and `GOLDEN_DATASET_NAME` appear nowhere under `src/idp_regression/`.

**Residual risk.** Two, both small and both accepted. (a) **Ergonomics** — a local run is now four flags and no bare `run_eval`; the mitigation is a wrapper script or `make` target that reads the `IDP_TEST_*` values, which keeps the convenience where it belongs (outside `src/`). (b) **The CI workflow becomes the single place the triple is written**, so a wrong value there is wrong everywhere — but it is wrong *visibly, in a reviewed file*, which is strictly better than wrong invisibly in a runner's environment. Note the rule removes ambiguity, it does not remove the need to read: a required flag with a wrong value is still a run against the wrong target, and only INV-04's recorded metadata catches that after the fact.

**Reversal cost: Low.** Re-adding a fallback is a few lines in the CLI layer plus the DoD/test rows. Nothing persisted, no contract consumer.

⚠️ **Correction (Atchim review R-1, 2026-09-22): this paragraph previously read "no exit code changes", which is false about the very change it describes.** Omitting `--action` or `--dataset` returned **1** before `e807f05` (the deleted "not set" branch in `cli.py`) and returns **2** after it, via argparse's required path. That is **intentional** — a usage error is argparse's code 2, matching `--version`'s long-standing behaviour — but it is a real contract change for a consumer that distinguishes 1 (config/run error) from 2 (usage error), and this tool's entire purpose is being read by CI. `--dataset "   "` still returns 1, unchanged. The A8 preamble's *"message naming the flag, non-zero exit, zero network calls"* was and remains accurate; only the reversal-cost sentence was wrong, and it was wrong in the paragraph a future reader consults to decide whether reversal is safe.

It is cheap to reverse and should not be — the cost of reversing is not technical but the return of the silent-fork failure mode A6 exists to prevent.
