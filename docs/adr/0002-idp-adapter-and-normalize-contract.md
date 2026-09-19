# ADR-0002: IDP adapter & normalize() contract

**Status:** Proposed
**Date:** 2026-09-17
**Context (use case):** UC-01 (Run a baseline regression over a golden set)
**Risk:** High — the normalized-output contract is what the classifier, the run history, and (later) the remediation UI consume. Changing it after runs are scored on the platform invalidates historical `field:<name>` score semantics. **Atchim review: APPROVED (top-level gate, 2026-09-18 — opus; reviewer-independent, all five axes PASS).**

## Context

The IDP adapter is the only component that knows IDP's HTTP shape. Three calls (`docs/init/requirements-spec.md` Layer 1):
1. **Auth** — OAuth2 client credentials, `POST .../accounts/api/v2/oauth2/token`.
2. **Submit** — `POST {base}/organizations/{org}/actions/{action}/versions/{version}/executions` with the document as a file upload → execution `id`. Base host is region-specific.
3. **Poll** — `GET {base}/.../executions/{id}` until status is terminal → extraction body.

Confirmed from a live response sample (UC-01 Hand-off, 2026-09-17): the raw execution response is nested under `pages[]`, each page carrying:
- `fields` — `<name>.value` (and a per-value `confidence` in fuller examples).
- `tables` — a keyed map of arrays of row-objects: `tables.<table>[].<column>.value`.
- `prompts` — `prompt`, `source`, `answer.value`.

`normalize()` must walk into `pages[].fields.<name>.value` (and `pages[].tables.<table>[].<col>.value`, `pages[].prompts[].answer.value`) — **not** a flat top-level map.

Constraints that shape the adapter:
- **Action and version are per-run parameters** (BR1; amended 2026-09-19) — `action_id` and `version` are passed to `extract()` on every call, never hard-coded and never read from env inside the adapter. The version is the regression variable; the action is per-run so one environment can exercise several actions (Epic F routing). Both are validated at the CLI boundary before they reach the URL path: `action_id` must be a UUID, `version` must match `^[A-Za-z0-9._-]{1,64}$` — anything else exits non-zero before any IDP call (path-injection guard).
- **Terminal status is a configurable allowlist** (ASM-01, BR9) — read from config/env, never `== "SUCCEEDED"`. `SUCCEEDED` is confirmed real; the full enum (whether `PARTIAL_SUCCESS` exists in this org / appears in callbacks) is unconfirmed and is pinned by a `/spike` against the live org before implementation.
- **OAuth token cached for the run** (BR7) — one token at run start, reused across all documents; refreshed only on expiry within the run.
- **Per-document timeout, abort the run on timeout or hard failure** (ASM-02, BR10, AC5) — no partial runs.
- **All credentials from env/secrets** (BR6) — `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`, `IDP_ORG_ID`. Run parameters (`action_id`, `version`) are not environment config; `IDP_ACTION_ID` survives only as an optional CLI default (ADR-0004), and there is no env var for the version.
- **Confidence is kept available to the classifier but is NOT part of the golden-set comparison** (UC-01 Hand-off) — it flows through `normalize()` into the verdict/score so the remediation UI can use it, but the gate ignores it.
- **Shape variance across actions** (ASM-04) — the confirmed shape is from invoice/field-report actions; `normalize()` must not assume one fixed shape across all document types. For UC-01 (single baseline action) this is Low risk; it becomes Med for Epic F routing.

## Threat model

Trust boundary: **IDP** (external OAuth-authenticated HTTP service). IDP is a trusted *internal* service, but its output is still untrusted input to us — the adapter must not pass raw IDP JSON straight to the classifier or platform without normalization.

- **Spoofing:** OAuth client credentials mitigate; fail-closed on auth failure (A3) — never fall back to anonymous.
- **Tampering:** TLS to IDP; the execution `id` is opaque to us. Mitigation: we treat the polled body as data, never execute it. A hallucinated/malformed field name from IDP is a tampering-shaped input — mitigated by field-name sanitization at the `normalize()` boundary (see § Normalized-output contract / Field-name sanitization).
- **Information disclosure:** IDP credentials (client secret) are the sensitive surface. Mitigation: env/secrets only (BR6); token cached in memory, never logged; redact `Authorization` headers in any debug/observability output. `normalize()` uses structured (non-f-string) logging so a golden/actual value containing `\n` or `"` cannot break a log line or inject a fake telemetry field.
- **Denial of service:** IDP rate limits unknown (NFR). Mitigation: sequential-only at MVP (no concurrent submissions); per-document timeout; bounded retry for transient errors only, immediate abort on hard failure (Containment section in ADR-0004).
- **Repudiation:** the adapter returns the IDP execution status alongside the normalized output so the orchestrator records it in run metadata.

## Options considered

### Option A — Adapter returns the raw `pages[]` body; classifier walks pages
**Pros:** less adapter code.
**Cons:** pushes IDP's shape into the classifier, breaking classifier purity (ADR-0003) and coupling the pure logic to a volatile external contract. Shape variance (ASM-04) would leak into the classifier.

### Option B — Adapter `normalize()` flattens `pages[]` into the internal contract; classifier stays pure
**Pros:** the classifier depends only on the stable internal contract; IDP shape changes are absorbed in one place. Matches the seed (`design-inputs.md` ADR-2: classifier is pure and platform-agnostic) and UC-01 step 3d.
**Cons:** the normalized-output contract is now load-bearing — changing it is a cross-feature break (score semantics, remediation UI). This is the reversal cost; it's real but manageable with contract tests.

## Decision

We choose **Option B**. `normalize()` is the single seam between IDP's volatile shape and the stable internal contract. The adapter owns: OAuth token handling, polling with timeout, the configurable terminal-status allowlist, and `normalize()`.

### Adapter interface (contract-first)

```python
class IDPAdapter(Protocol):
    def extract(self, document_path: str, action_id: str, version: str) -> NormalizedOutput: ...
```

`extract()` is the only public method. It:
1. Ensures a cached OAuth token (fetch at first call, refresh on expiry).
2. POSTs the document to `actions/{action_id}/versions/{version}/executions` → execution `id`.
3. Polls until status ∈ `IDP_TERMINAL_STATUSES` (configurable allowlist) or timeout.
4. On terminal success: `normalize()` the raw body → `NormalizedOutput`.
5. On timeout / hard failure: raise (the orchestrator aborts the run — ADR-0004).

### Normalized-output contract (the internal shape `normalize()` emits)

```python
class NormalizedOutput(TypedDict):
    status: str                                   # the IDP terminal status string (e.g. "SUCCEEDED")
    fields: dict[str, FieldValue]                 # name -> {value, confidence}
    tables: dict[str, list[dict[str, FieldValue]]]  # table_name -> [{col_name -> FieldValue}]
    prompts: dict[str, PromptValue]               # prompt key -> {answer, confidence, source}

class FieldValue(TypedDict):
    value: str | None
    confidence: float | None                       # kept available; NOT part of golden comparison

> **Amended 2026-09-19 (QA S-01.2 F-3, typing only).** In code, the adapter's TypedDicts mirror the classifier's (`src/idp_regression/classifier/types.py`) so that `NormalizedOutput` is assignable to `classify()` under mypy (CT-01 key-parity test). They mark `tables`, `prompts`, `FieldValue.confidence`, `PromptValue.confidence` and `PromptValue.source` as `NotRequired`. At runtime `normalize()` always emits every key: the contract above describes the emitted shape, and `NotRequired` only marks keys a *consumer* must tolerate as absent.

# A row is a dict keyed by column name (NOT a TypedDict with wildcard keys —
# TypedDict cannot express `column_name -> FieldValue` for arbitrary columns,
# and `dict[str, list[Row]]` would not be mypy-typeable. A plain
# dict[str, FieldValue] per row is the expressible, mypy-checkable form.)
# A contract test mypy-checks these types.

class PromptValue(TypedDict):
    answer: str | None
    confidence: float | None
    source: str | None
```

Merge semantics across `pages[]`:
- `fields`: later pages win on name collision (last-wins); single field-name namespace across the document. (`pages[].fields.<name>.value` → `fields[<name>].value`.)
- `tables`: concatenate rows across pages per table name (`pages[].tables.<table>[]` → `tables[<table>] + [...]`).
- `prompts`: keyed by `prompt` string. **Key collision rule:** if two prompt entries share the same `prompt` string, `normalize()` raises `MalformedIDPOutputError` (duplicate-prompt) rather than silently dropping one — last-wins would hide a real IDP duplication bug. The Curator-visible prompt key is the `prompt` string; uniqueness is asserted at the trust boundary. (If a future action emits legitimately duplicate `prompt` strings with different `source`s, re-open this with an index-qualified key — out of scope for UC-01.)
  - *Amendment 2026-09-19 (ADR-0005 F10, R4):* the prompt key is the IDP `prompt` string **verbatim** (no trimming or case-folding): 1–200 chars, no control characters (`^[^\u0000-\u001F\u007F]{1,200}$`), matching the golden schema's `propertyNames` (CT-05). A prompt that violates this raises `MalformedIDPOutputError(reason="unsafe_prompt_key")`. The `[A-Za-z0-9_\-]` charset below does **not** apply to prompt keys.
- Confidence is preserved wherever the raw response carries it; absent confidence → `None`. The classifier and gate **ignore** confidence. **Amended 2026-09-19 (DEBT-18 option B, user decision):** confidence is never written to the platform, and the score comment is always `None`. Any remediation-UI use of confidence must come from the local run, not from Langfuse.

### Field-name sanitization at the trust boundary
Field names from IDP become platform score keys `field:<name>` (INV-03, BR11). An LLM-hallucinated field name containing `:`, a newline, or the literal `gate` would break score-key parsing downstream (INV-03). `normalize()` therefore **validates field names at the boundary** against a safe charset `[A-Za-z0-9_\-]` (and a non-empty length cap). A field name that fails this check is not silently dropped and is not passed through — it maps to a typed `MalformedIDPOutputError(field="…", reason="unsafe_field_name")` (the orchestrator aborts the run — ADR-0004). This is part of the untrusted-input contract (see §Threat model). A contract test feeds a malicious field name (`"total\ngate"`, `"a:b"`) and asserts the typed error. The same charset applies to `tables` keys. *(Amended 2026-09-19, ADR-0005 F10: previously also `prompts` keys; prompt keys now follow the verbatim rule above. A prompt-derived score name never uses the raw prompt: it uses a charset-safe derived id, `prompt:<first 16 hex chars of sha256(UTF-8 prompt key)>`, with the verbatim prompt in the score comment (INV-03). **Amended 2026-09-19 (DEBT-18 option B):** score comments are `None`. The verbatim prompt is golden content and never leaves the dataset item; the score name alone identifies the prompt. The exact score-name family is pinned by CT-03 in S-01.3.)*

### Mapping to the golden (consumed by ADR-0003)
- `golden.fields` (name → `{value, type, critical}`) compares against `actual.fields[name].value`.
- `golden.tables[<table>]` (with `match_key`, `critical`, `rows`) compares against `actual.tables[<table>]` by `match_key`. Each actual row is a `dict[str, FieldValue]` keyed by column name (post-revision ADR-0002 shape); the seed's top-level `line_items` block is normalised to `tables.line_items` in the golden schema (DATA-MODEL-01) so golden and actual share one keyed-map shape.
- `golden.prompts` (if present) compares against `actual.prompts[key].answer`.

### Configurable terminal-status allowlist (ASM-01 directive)
- `IDP_TERMINAL_STATUSES` read from env/config as a comma-separated list (e.g. `"SUCCEEDED,PARTIAL_SUCCESS,FAILED"`). Default: `["SUCCEEDED"]` only — fail-closed until a `/spike` pins the live-org enum.
- The "terminal success" set (statuses that should produce a `NormalizedOutput` rather than abort) is a separate, smaller allowlist: `IDP_SUCCESS_STATUSES` (default `["SUCCEEDED"]`). A status in `IDP_TERMINAL_STATUSES` but not in `IDP_SUCCESS_STATUSES` is a hard failure → orchestrator aborts (ADR-0004).
- Never `== "SUCCEEDED"` in code. Both allowlists are config; the spike pins the values.

## Design patterns

- **Adapter (GoF)** — `IDPAdapter` Protocol with one concrete implementation talking to IDP. Idiomatic: a `Protocol` + `MuleSoftIDPAdapter` class; `extract()` is the seam. The classifier never imports the adapter.
- **Strategy (injectable function)** — `normalize()` is a pure function `normalize(raw: dict, success_statuses: set[str]) -> NormalizedOutput`, injectable for testing with sample raw bodies. Idiomatic: a module-level function, not a class.
- **Repository (light)** — token caching is a small in-memory cache scoped to the run, held as a `TokenCache` **instance** on the adapter (not a module-level `_token` global). A module global is awkward for test isolation and any future concurrency; an instance is idiomatic, injectable in tests, and scoped to the adapter's lifetime. The Repository intent (a thin persistence facade over the token endpoint) fits; the form is a small class with `get()`/`refresh()`, not a module global.

## API contract (observable behaviors — Hyrum's Law)

What we commit to, that consumers may come to depend on:
- `extract()` returns a `NormalizedOutput` whose `fields` keys are exactly the field names IDP extracted for that document; `tables` keys are exactly the table names IDP returned. Adding a field to the IDP action does **not** remove existing keys (this is what makes `new_field` detection stable).
- `confidence` is always present as a key (possibly `None`); consumers may assume the field exists on every `FieldValue`.
- `status` is the raw IDP status string, passed through unchanged — consumers may assume it's one of `IDP_SUCCESS_STATUSES` when `extract()` returns rather than raising.
- `extract()` raises on timeout / hard failure / auth failure — it never returns a partial or "empty success" result. Consumers may assume a returned `NormalizedOutput` means a successful extraction.
- `normalize()` raises `MalformedIDPOutputError` on any malformed input (no `KeyError`/`AttributeError` escapes): schema validation at the boundary (top-level dict with `pages` list; each page dict with `fields`/`tables`/`prompts` or absent; every `FieldValue` is `{value: str|None, confidence: float|None}` after coercion); field/table/prompt names validated against `[A-Za-z0-9_\-]`; duplicate `prompt` strings raise; confidence outside `[0,1]` or NaN → `None` (NOT clamped — clamping hides a broken IDP action); three-state missing/empty/null preserved (absent key / `null` / `""` are distinct); bounded sizes (field value > 64 KB → typed error `value_too_large`; `tables.<t>` > 10_000 rows → typed error `table_too_large`). Consumers may assume a returned `NormalizedOutput` is well-formed and bounded.
- `extract()` and `normalize()` use structured (non-f-string) logging — a value containing `\n` or `"` cannot break a log line or inject a fake telemetry field (NFR N5/N19).

Versioning strategy: the `NormalizedOutput` TypedDict is versioned by Python type; shape changes require a new ADR. The `field:<name>` / `gate` score keys built on top of this contract are immutable (BR11).

## Consequences

- **Positive:** the classifier stays pure (ADR-0003) — it depends on `NormalizedOutput`, never on IDP. Shape variance across actions (ASM-04) is isolated to `normalize()`. Confidence flows to the remediation UI without touching the gate.
- **Negative:** the `NormalizedOutput` shape is now load-bearing. Changing it after runs are scored on the platform redefines historical `field:<name>` score semantics.
- **Follow-up work:**
  - `/spike` to pin `IDP_TERMINAL_STATUSES` and `IDP_SUCCESS_STATUSES` against the live org before implementation (ASM-01).
  - Contract tests for `normalize()` against captured raw IDP bodies (the spike should capture fixtures), including: malformed-body cases (typed errors, no raw exception escapes), a malicious field name (`"total\ngate"`, `"a:b"`) asserting the sanitization error, a duplicate `prompt` string asserting the collision error, a >64KB field value, a >10_000-row table, an invalid-confidence (NaN / 1.5) value asserting `None` (not clamp), and a three-state missing/empty/null case. A mypy contract test that type-checks `NormalizedOutput` / `FieldValue` / `PromptValue` / the `tables` dict-of-list-of-dict shape.
  - Register IDP credential set with Mestre (already listed in `## External services` as missing).

## Reversal cost

**High** and rising. Before the first run is scored on the platform: medium (rewrite `normalize()` + classifier together). After runs are scored: high — historical scores become ambiguous. Mitigation: the contract is small and the contract tests pin it; the risk is in *delaying* a needed change, not in *making* one. Locked once we score real runs.

Atchim review: APPROVED (top-level gate, 2026-09-18 — opus; reviewer-independent, all five axes PASS). Non-blocking debt: the IDP terminal-status / timeout / retry spike (ASM-01) must still pin `IDP_TERMINAL_STATUSES` / `IDP_SUCCESS_STATUSES` and the timeout value against the live org before S-01.2/S-01.4 implementation — this approval covers the contract structure, not the pinned values.