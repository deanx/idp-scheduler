# ADR-0001: Evaluation platform selection (Langfuse vs Opik)

> **Superseded by ADR-0005 (2026-09-19):** SPIKE-01 refuted this ADR's deciding factor (Langfuse form mode is a raw-JSON editor, not schema-generated form fields); the platform decision and its rationale now live in `docs/adr/0005-evaluation-platform-redecision.md`. Body kept for history.

**Status:** Superseded by ADR-0005 (was: Proposed — PROVISIONAL pending SPIKE-01)
**Date:** 2026-09-17
**Context (use case):** UC-01 (Run a baseline regression over a golden set)
**Risk:** High — the platform becomes the durable store for golden sets; migrating off it once goldens live on it is expensive (re-keying the curated reference). **Atchim review: APPROVED (top-level gate, 2026-09-18 — opus; provisional status unchanged pending SPIKE-01).**

## Context

The evaluation platform is *substrate*, not the system: it stores golden sets (dataset items = `document_id` + expected fields), records runs, persists per-field scores (`field:<name>`) and the `gate`, and gives the non-engineer **Curator** a UI to maintain goldens. The custom app — classifier, gate, orchestration — is identical on either platform; only the platform adapter differs.

Forces in play:

- **Curator persona (non-engineer).** Goldens are nested JSON (`fields` + `line_items`/`tables` with `match_key`, `critical`, `type`). The Curator must edit these through a UI without touching code or SDK scripts. This is the load-bearing differentiator — the seed (`docs/init/discovery-notes.md`, `docs/init/use-cases-seed.md` UC-C1 note) flags that on Opik nested-golden editing falls back to the SDK or a custom surface.
- **Golden versioning (ASM-03).** A run is only meaningful against a known golden version. Opik versions datasets automatically and immutably on every edit; Langfuse does not version automatically — the app must record the dataset version at run start.
- **Swappability.** `docs/init/design-inputs.md` ADR-1 already constrains all platform SDK calls to one module behind a `get_dataset / write_scores / flush / golden-CRUD` interface. This keeps the *integration* reversible; it does not keep the *data* reversible. Once goldens live on one platform, moving them is a migration, not a reconfigure. So the choice is reversible before the first golden is loaded, and expensive after.
- **Self-hosting.** Both are open-source and free to self-host (Langfuse MIT, Opik Apache-2.0). The platform server runs in Docker; the custom app does not (per `## Stack`).
- **Cost.** Langfuse bills scores as billable units on its cloud; a regression run emits N×(fields+1) scores per document, which compounds. Self-hosting sidesteps cloud billing; the cost NFR still tracks score volume.
- **Compliance.** No formal regime, but golden contents can carry PII/financial data (`## Domain`). Document files never enter the platform — only `document_id` + expected fields. Both platforms satisfy this since we control what we write.

### The load-bearing assumption and why this ADR is PROVISIONAL

The deciding factor below is an **assumed capability of Langfuse**, not a verified fact: that Langfuse **form mode** renders nested expected outputs (`fields` of mixed types + `tables[].rows[]` of column-keyed dicts + `prompts[]`) as schema-validated editable form fields for a non-engineer Curator. The seed itself labels EX-C1-1 an assumption ("EX-C1-1 assumes Langfuse form mode"). The Atchim review flagged this as uncited and load-bearing — if Langfuse form mode only handles flat/shallow JSON, the Curator-persona differentiator collapses and the decision could flip.

**This ADR is therefore PROVISIONAL on `docs/spikes/SPIKE-01-langfuse-form-mode-nested-json.md`.** SPIKE-01 stands up a live self-hosted Langfuse, loads a representative nested golden, and confirms or refutes the form-mode capability. If SPIKE-01 refutes it, this ADR reverts to Option C (defer) and the platform decision re-opens Langfuse vs Opik (see §Decision). No goldens are loaded before SPIKE-01 resolves.

## Threat model

Trust boundary: **evaluation platform** (external, API-key-authenticated store of sensitive golden contents) and, for self-hosted Langfuse, the **self-hosted DB we operate** (an additional trust boundary — see Self-hosting obligations below).

- **Spoofing:** platform API key is the only auth. Mitigation: key from env/secrets only, `load_dotenv()` before any SDK client (BR6). Fail-closed if absent.
- **Tampering:** score/golden writes over the platform API. Mitigation: platform auth + TLS; the platform is the source of truth for goldens, so tampering would be detected by the next run's mismatch.
- **Information disclosure:** golden contents contain PII/financial data. Mitigation: never logged in plain text (BR5); access credential-gated; document files never uploaded (BR4). Self-hosted DB adds at-rest exposure — see below.
- **Repudiation:** runs must be attributable to a version pair. Mitigation: run metadata records action ID + action version + golden version (BR1, F16).

## Options considered

### Option A — Langfuse
**Pros:**
- **Form mode (ASSUMED capability, to be confirmed by SPIKE-01):** JSON-schema-driven form mode is expected to generate validated form fields for nested expected outputs — the strongest match for the non-engineer Curator editing nested goldens (UC-C1). This is the deciding factor; it is **not yet verified** and the ADR is provisional on SPIKE-01.
- Directly queryable backing database (useful for ad-hoc run analysis).
- Mature Python SDK; datasets/experiments/custom evaluators/CI gating all present.
- MIT-licensed, self-hostable.

**Cons:**
- **No automatic immutable dataset versioning.** Golden versioning is **app-tracked**: the orchestrator records the dataset version at run start (a content hash — see §API contract). Extra app code; if the app forgets to record it, reproducibility (F16) is silently broken. Resolves ASM-03 as "app-tracked".
- Cloud bills scores as billable units — mitigated by self-hosting; still tracked in the cost NFR.
- **Self-hosting moves PII (golden contents) into a Docker DB we operate** — an added compliance surface (see Self-hosting obligations).

**Cost:** one thin adapter; plus an app-tracked-versioning module (~small).
**Risk:** (a) app-tracked versioning can be forgotten on a new orchestration path — mitigated by INV-04 + a `/qa` check; (b) the form-mode assumption is false — mitigated by the PROVISIONAL-on-SPIKE-01 status.

### Option B — Opik
**Pros:**
- **Automatic immutable dataset versioning** on every edit — audit-grade golden history for free. Resolves ASM-03 as "automatic".
- Apache-2.0, self-hostable; Python SDK; datasets/experiments/evaluators present.

**Cons:**
- **Opik's dataset UI does not generate schema-validated form fields for nested JSON (to be confirmed by SPIKE-01 if a comparison pass is desired).** The seed flags that structured/nested golden editing is pushed to the **SDK or a custom surface**, which breaks the non-engineer Curator persona (UC-C1 feasibility depends on this). We would have to build a custom editing UI on the platform API to give the Curator a usable surface, which is Epic E scope creep for an Epic C need.
- This is the load-bearing disadvantage: the Curator is a named persona in `## Domain`; forcing SDK editing violates the product's human-in-the-loop design.

**Cost:** one thin adapter; versioning is free; but a custom Curator editing surface would be needed (large, out of MVP scope).
**Risk:** building a custom Curator UI to compensate is a quarter-scale detour that duplicates platform UI work.

### Option C — Defer the decision; stay platform-neutral behind the adapter
**Pros:** keeps both viable; no commitment yet.
**Cons:** UC-01 must write scores and fetch goldens somewhere now; deferring blocks the MVP. The adapter interface (ADR-0002) already keeps the *integration* reversible; deferring the *data* commitment buys nothing for the MVP and postpones the Curator-feasibility question that decides it. **This is the fallback if SPIKE-01 refutes the Langfuse form-mode assumption** — re-decide Langfuse vs Opik with the Opik SDK/custom-surface editing path evaluated symmetrically.

## Decision

We choose **Langfuse, PROVISIONAL pending SPIKE-01.**

The deciding factor is the **Curator persona**: Langfuse's form mode (an **assumed capability, to be confirmed by SPIKE-01**) is expected to let a non-engineer edit nested expected outputs through generated, schema-validated form fields — UC-C1 is feasible as designed. On Opik, the same workload falls back to the SDK or a custom surface, which either breaks the persona or forces a custom UI build that is out of MVP scope. The seed's own UC-C1 note makes this the explicit pivot point.

**Provisional status:** SPIKE-01 (`docs/spikes/SPIKE-01-langfuse-form-mode-nested-json.md`) is a **pre-implementation prerequisite**. If SPIKE-01 refutes the nested-JSON form-mode capability (form mode only renders flat/shallow JSON), **revert to Option C (defer) and re-decide Langfuse vs Opik** with the Opik SDK/custom-surface editing path evaluated on the same nested golden. That re-decision supersedes this ADR and re-resolves ASM-03 / ASM-05. No goldens are loaded before SPIKE-01 resolves; the classifier (ADR-0003) and adapter contract (ADR-0002) are platform-independent and can proceed in parallel.

The cost we accept is **app-tracked golden versioning** (no Opik-style automatic immutable versioning). The orchestrator computes a **content hash from the `get_dataset` result** (single fetch — see §API contract) and records it in run metadata; an invariant (INV-04) and a `/qa` check guard reproducibility so the app-tracked step is never silently skipped. This resolves ASM-03 (still Low risk — the versioning mechanism is app-tracked regardless of which platform; SPIKE-01 does not change that).

The adapter (ADR-0002) keeps the platform SDK confined to `src/idp_regression/platform/`, so the *integration* stays reversible up until the first golden is loaded — the decision is locked by data, not by code, and that lock is the one we accept.

## Design patterns

- **Adapter (GoF)** — `PlatformAdapter` interface (`get_dataset / write_scores / flush / golden-CRUD`) with a `LangfuseAdapter` implementation; the rest of the app depends on the interface, never importing the Langfuse SDK. Stack-idiomatic form: a Python `Protocol` + a `LangfuseAdapter` class implementing it, constructed in `make_platform()`. Ad-hoc is unjustified here — two platforms were explicitly in scope.
- **Factory** — `make_platform()` reads `PLATFORM` from env and returns the right adapter. Idiomatic: a module-level function, not a class hierarchy.

## API contract (platform adapter interface)

Contract-first. The interface the rest of the app depends on, regardless of platform:

```python
class PlatformAdapter(Protocol):
    def get_dataset(self, name: str) -> list[DatasetItem]: ...
    def write_scores(self, run: str, item: DatasetItem,
                     verdicts: dict, gate: str,
                     action_id: str, action_version: str,
                     golden_version: str) -> None: ...
    def flush(self) -> None: ...
```

**Golden versioning — single-fetch content hash (no TOCTOU).** The app-tracked golden version is a **content hash computed from the `get_dataset` result**, not a second platform call. There is no `get_golden_version` on the adapter interface — a second fetch would open a TOCTOU window against the `get_dataset` result used for classification. The orchestrator computes `golden_version = hash_dataset(dataset)` over the **same** `dataset` list it iterates, so the recorded version is provably the version classified against. This is load-bearing for INV-04. `hash_dataset` is a stable, version-pinned digest (e.g. SHA-256 over a canonical JSON encoding of the items — `document_id` + `golden`); the exact encoding is pinned by a contract test so a hash is comparable across runs.

Observable behaviors we are committing to (Hyrum's Law):
- `field:<name>` and `gate` score keys are a **stable contract** the remediation UI reads (BR11). Renaming them is a versioned migration, not a refactor.
- `golden_version` is derived from the same `dataset` object passed to the classifier — consumers may assume the recorded version is the version classified (no TOCTOU).
- `DatasetItem` carries `document_id` + `golden` (the expected-output dict) — nothing else. Consumers may assume no document file bytes are present.

Versioning strategy: the adapter interface is versioned by its Python type; breaking changes (score-key renames, `DatasetItem` shape changes) require a new ADR. Score keys themselves are immutable from day one — they are the cross-feature contract.

## Consequences

- **Positive:** UC-C1 (Curator edits nested goldens in UI) is feasible without custom UI work **if SPIKE-01 confirms form mode**. The Curator persona is served by the platform, not by us. The integration stays reversible through the adapter.
- **Negative:** Golden versioning is app-tracked — we own the discipline of recording it (guarded by INV-04 and a `/qa` check, single-fetch content hash so it cannot TOCTOU). Self-hosted Langfuse is one more server to run (Docker, separate from the custom app) and adds a self-hosted-DB compliance surface.
- **Self-hosting obligations (handed to Mestre / Dunga):** because self-hosted Langfuse stores golden contents (PII/financial) in a Docker DB we operate, the following are **requirements**, not optional:
  - **Encryption at rest** for the self-hosted Langfuse DB (DB-level or volume-level). Cross-reference INV-02 (no plaintext sensitive logging) — at-rest protection is the storage-side complement.
  - **DB access control** — the Langfuse DB is not an open local Postgres; network exposure is restricted to the Langfuse server, and DB credentials are from env/secrets (same BR6 discipline as the app).
  - **Backup & retention** — the golden set is the baseline; a DB loss means the baseline is gone. A backup/restore target is captured as a system-scope NFR row (Pass C absence-audit: DR/backup of the golden set). Backup contents inherit the same encryption-at-rest requirement.
  - Mestre registers the Langfuse credential set (`LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST`) at the top-level — we do not write `CLAUDE.md`/`## External services` ourselves.
- **Follow-up work:**
  - **SPIKE-01 (pre-implementation prerequisite):** resolve the form-mode assumption before any golden is loaded. This ADR's PROVISIONAL label is removed (confirmed) or the ADR is superseded (refuted).
  - Implement the app-tracked golden-version recording in the orchestrator as `hash_dataset(dataset)` (ADR-0004 step 3/4 mirrors this — no `get_golden_version` call).
  - Pin the Langfuse SDK version in the lock file once Dengoso implements.
  - Mestre registers the Langfuse credential set (top-level).

## Reversal cost

**High**, and rising over time. Before the first golden is loaded: low (swap the adapter implementation; the classifier and adapter contract are platform-independent). After goldens live on Langfuse: a migration of the curated golden set + run history — expensive enough that it effectively locks the choice for the product's lifetime. This is why the risk label is **High** and why Atchim review is required before we load goldens.

**Provisional caveat:** if SPIKE-01 refutes the form-mode assumption, reversal is **cheap** (we have not loaded goldens yet) — that is exactly why the PROVISIONAL gate sits *before* the first golden load. The expensive, effectively-irreversible lock only begins after SPIKE-01 confirms and goldens are loaded.

Atchim re-review: APPROVED (top-level gate, 2026-09-18 — opus; reviewer-independent, all five axes PASS). Non-blocking debt: SEQ-UC-01 diagram still shows a stale `get_golden_version(name)` call — Soneca to correct (single-fetch content hash per §API contract is the design of record); `write_scores` idempotency-key support deferred to SPIKE-01 (no-retry-on-5xx is the safe fallback until confirmed). Provisional-on-SPIKE-01 status is unchanged — approval covers the decision structure, not a confirmation of the form-mode assumption.