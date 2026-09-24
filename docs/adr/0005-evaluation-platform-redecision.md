# ADR-0005: Evaluation platform re-decision after SPIKE-01

**Status:** Accepted (Atchim APPROVED (R1–R4 closed, re-checked 2026-09-19)) — **supersedes ADR-0001's decision rationale** (ADR-0001 → Superseded by ADR-0005)
**Date:** 2026-09-19
**Context (use case):** UC-01 (Run a baseline regression over a golden set); UC-C1 / EX-C1-1, EX-C1-2 (Curator edits a nested golden); S-01.5 close-out
**Risk:** High — the platform becomes the durable store for golden sets. Moving goldens off it after the first real load is a migration, and the Curator-UX consequence below changes what Epic E must deliver. **Atchim review: APPROVED (R1–R4 closed, re-checked 2026-09-19)**.
**Evidence:** `docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md` (Langfuse 4.38.0 OSS tested live, plus a source-level addendum on Opik 2.2.x); `docs/spikes/SPIKE-01-langfuse-form-mode-nested-json.md` (§Result, §Fallback decision).

## Context

ADR-0001 chose Langfuse **PROVISIONALLY**. Its deciding factor was an assumed capability: that Langfuse "form mode" renders a nested golden (`fields` + `tables[].rows[]` + `prompts`) as schema-validated, editable form fields, so the non-engineer **Golden Set Curator** could edit goldens without code. SPIKE-01 made that claim the one assumption that could flip the platform choice (ASM-05).

**What SPIKE-01 found:**

| Claim | Langfuse 4.38.0 OSS (live) | Opik 2.2.x OSS (source only) |
|---|---|---|
| Schema-generated, editable form fields for nested values (the ADR-0001 deciding factor) | ❌ **Refuted.** The whole item is edited as raw JSON in CodeMirror. | ❌ None. Top-level keys appear as accordion sections, but each nested value is still raw JSON in CodeMirror. |
| Server-side type/shape enforcement of goldens | ✅ `expectedOutputSchema` is enforced on API **and** UI writes. Invalid writes are rejected with 400 and the stored value stays unchanged. A schema cannot be added over items that don't conform to it. | ❌ None. Opik only checks that the input is valid JSON. |
| Editor error feedback | ❌ Save is blocked with no message. Item-level API errors carry no JSON path, and Ajv reports only the first error (`allErrors:false`). | ✅ Inline message, but only for JSON syntax errors |
| Versioning | Point-in-time read of an item plus UI history. No per-user author column. `createdAt` resets on upsert. | ✅ Dataset-level versions with diff, restore, and a change description |
| Idempotent score writes | ✅ Upsert keyed by a client-supplied score `id` (live-verified) | ⚠️ Upsert keyed by the natural key `(entity, author, name)`. No client id. |
| Footprint | web, worker, postgres, redis, clickhouse, minio (**already running locally**) | Java backend, Python backend, nginx, mysql, redis, clickhouse, zookeeper, minio (not stood up) |

**The honest reading:** the differentiator ADR-0001 relied on **does not exist on either platform**. Neither gives a non-engineer a no-code nested editor. The re-decision therefore cannot rest on Curator UX; that is a wash that we build ourselves (Epic E) whichever platform we pick. It must rest on what actually differs:

1. **Golden integrity at write time.** A golden is the reference every future run is judged against. A wrong type in a golden (for example `"twelve fifty"` in a `number` field) does not raise a malformed-golden error in our classifier. N22 checks structure, not value patterns, so the bad value turns into `wrong_value` verdicts and a **false FAIL on another team's prompt-change PR**. Rejecting the bad value at save time is worth more than catching it at run time.
2. **Retry-safe score writes (ADR-0004 #12, DEBT-03).** A client-supplied score id turns "no retry on 5xx" into "retry safely".
3. **Operational reality.** A Langfuse instance is already running and its integration facts are known. This is a tie-breaker, not a decider.

What Opik genuinely does better (inline syntax feedback, and version diff/restore that works as an undo) is real, and the options below weigh it.

### Forces unchanged from ADR-0001
- Swappability through the `PlatformAdapter` Protocol (ADR-0001 §API contract, NFR N24) keeps the *integration* reversible. The *data* is locked only by the first real golden load, and none has happened yet. That makes reversal cheap **now**.
- Golden versioning stays **app-tracked**: a single-fetch content hash (`golden_version = hash_dataset(dataset)`, INV-04). This holds on either platform. Opik's automatic versions would not replace it, because the run must record the exact content it classified. ASM-03 is unaffected.
- Compliance: document files never enter the platform (BR4, INV-01). Golden contents are sensitive (`## Domain`).

## Threat model (deltas vs ADR-0001)

ADR-0001's STRIDE still applies: the platform API key is the only auth, TLS, BR5/BR6, and INV-01/02. The deltas come from the Curator write path, which the spike showed is a raw-JSON write into the golden store:

- **Tampering / integrity (Curator UI → golden store).** A Curator typo is a *well-formed but wrong* golden.
  - **Mitigation (chosen):** a server-side `expectedOutputSchema` on every golden dataset (see §Decision, value typing). It enforces shape on the whole golden, and per-type value patterns on **`fields.<name>.value` only**, on API and UI writes.
  - **Residual (scope, R1):** table cells and prompt answers are **string-only** — any string passes — until per-column table types land (DEBT-04); prompt answers are free text by nature. A `"twelve fifty"` in a `qty` cell is therefore *not* rejected at write time.
  - **Residual:** a semantically wrong but schema-valid value (for example `1250.00` typed as `1205.00`) still passes. The schema is a guard-rail, not a review.
- **Repudiation (who changed a golden?).** Langfuse item history has **no per-user author column**, so it is not an audit trail.
  - **Mitigation:** every run records `golden_version` (content hash, INV-04), so a change between runs is always *detectable*, though not *attributable*.
  - **Residual:** attribution is deferred to Epic E. The Epic E form writes through our API layer and records the actor. Until then, access to Langfuse projects is restricted to named Curators via Langfuse project roles. **To confirm (owner: Mestre):** that Langfuse OSS 4.38.0 project roles are available and sufficient without an enterprise licence; if not, the interim control is instance-level access only. Logged in Consequences.
- **Information disclosure (platform error bodies).** Langfuse 400 validation errors may echo parts of the offending value. S-01.3's existing DoD already requires platform error messages to be redacted at the logging boundary (INV-02, N5). This ADR makes that line apply explicitly to schema-validation 400s.
- **Denial of service / integrity (schema evolution).** Langfuse rejects a schema change if *any* item fails to conform to it (all-or-nothing). Dropping the schema to migrate would open an unguarded window. **Mitigation:** the expand/contract migration rule below (policy), backed by a **run-start schema-drift check** (control, Decision #8): a run against a dataset whose schema was removed or changed aborts with `schema_drift`.
- **New trust boundary, deferred to Epic E:** browser → Curator UI → Langfuse API. The Langfuse **secret** key must never reach the browser. Epic E's ADR owns this boundary (STRIDE there, not here).

## Options considered (symmetric)

### Option A — Langfuse. Curator edits JSON with server-side schema guard-rails for MVP; schema-driven Curator form in Epic E.
**Pros:**
- **Golden shape is enforced by the platform on every write path**, including the platform's own UI. A Curator cannot save a mis-shaped golden, or a pattern-invalid `fields` value, through any door. Table cells and prompt answers are string-only until DEBT-04 (R1 scope, §Threat model).
- Client-id idempotent score upsert (live-verified). This closes DEBT-03 and makes the ADR-0004 #12 retry safe.
- The instance is already running. Spike findings (v4 `events_only`, `/v3/scores`, schema cap) are known, so S-01.3 can proceed now.
- The JSON Schema we write for the platform guard-rail is **the same artifact the Epic E form renders**, for example rjsf over `expectedOutputSchema`. One schema, two uses, and no throwaway work.
- Smaller self-hosted footprint (no Java/zookeeper tier).

**Cons:**
- MVP Curator UX is poor: the raw JSON editor **blocks Save silently** and gives no path-level error. A non-engineer will get stuck. This is a known gap that Epic E must fix (see Consequences).
- No per-user author on golden changes (repudiation residual above).
- Versioning is weaker than Opik's: no dataset-level diff or restore. "Undo" means a manual point-in-time read and re-write.
- Schema operational constraints: a 10,000-char cap and all-or-nothing evolution.

**Cost:** S-01.3 adapter (already estimated) + a committed golden JSON Schema + a schema-provisioning step (raw REST, because Python SDK schema support is unverified, issue #10688). The Epic E form is new scope on either platform.
**Risk:** Curators hit the silent-block wall before Epic E ships. Mitigated by the MVP interim below. Reversal before the first real golden load: low.

### Option B — Opik. Curator edits JSON; Curator form in Epic E.
**Pros:**
- Better inline editor feedback, though only for JSON *syntax*.
- Dataset-level version diff and restore with a change description: a real "undo" for Curators and a better change history.
- Apache-2.0, self-hostable.

**Cons:**
- **No schema or type enforcement at all.** Any valid JSON is accepted. Golden integrity would have to be enforced by *our* code. Opik's own UI writes bypass it, so a Curator can save `"twelve fifty"` into a `number` field and nobody finds out until the next run produces false `wrong_value` FAILs in CI. The classifier's N22 validation catches structural malformation, not value patterns.
- No client-supplied score id. The natural-key upsert `(entity, author, name)` is idempotent in effect only if trace/entity ids are made deterministic. That is workable but less direct than Langfuse's model.
- Heavier stack (Java backend, MySQL, **zookeeper**). Not stood up, so a live spike would be owed before S-01.3, and the spike facts were source-level only.
- The Epic E form still has to be built. Opik gives it no schema to render, so we would own the schema *and* its enforcement.

**Cost:** stand up and spike Opik (about half a day), an Opik adapter, app-side golden validation on every read, and Epic E. S-01.3 slips.
**Risk:** golden corruption through the platform's own UI, with no guard. That is exactly the failure a regression gate cannot afford.

### Option C — Defer / custom golden store (for example goldens as JSON files in git + JSON Schema in CI; platform used only for runs/scores)
**Pros:**
- Strongest integrity and audit: schema-checked in CI, diffable, author-attributed, reviewable by PR.
- No platform lock on golden data.

**Cons:**
- **Breaks the Curator persona outright for MVP.** Git and PRs are further from a non-engineer than a JSON editor with guard-rails.
- Splits the source of truth: goldens in git, runs and scores on the platform. The remediation UI (Epic E, BR11) then joins across two stores.
- Deferring the platform choice blocks S-01.3/S-01.4 and buys nothing the adapter doesn't already give us. ADR-0001 rejected this for the same reason.

**Cost:** a new golden-store design plus a CI validator. The Curator surface is unsolved until Epic E.
**Risk:** low technical risk, high product risk (the persona gets nothing).

## Decision

**Option A: Langfuse.** For MVP the Curator edits goldens as JSON under a server-side JSON Schema guard-rail. The schema-driven Curator form, with path-level errors and actor attribution, moves to Epic E.

**Why, stated honestly:** we are **not** choosing Langfuse for Curator UX. That claim is refuted, and on UX Opik is marginally *better* (inline feedback, restore). We choose Langfuse because it is the only candidate that **enforces golden shape and `fields` value patterns on every write path, including its own UI**, and because it supports **client-id idempotent score writes**. For a tool whose purpose is to be a trustworthy CI gate, an unguarded golden store is the worse defect. A clumsy editor is a UX gap with a planned fix. The already-running instance is a tie-breaker only. Opik's version diff/restore is the thing we give up, and we accept it: app-tracked content hashes make golden changes detectable per run (INV-04), and Epic E can add an undo on top of Langfuse's point-in-time reads.

### Decisions folded in from the spike risks

1. **Golden `value` typing: strings plus JSON Schema `pattern`, not JSON numbers.** DATA-MODEL-01's string typing **stays**.
   - *Why:* the implemented and full-rigor-QA'd classifier (S-01.1, ADR-0003) canonicalizes `str` values (`canonical.py` `_value_number(value: str)` → `re.sub` on the string). A JSON-number golden would raise inside the classifier, or need a type change across a Done story.
   - JSON numbers also **lose formatting** (`1150.00` → `1150`). That breaks the value tier (exact string) of the format-vs-value distinction (AC4).
   - The schema still rejects `"twelve fifty"` through a per-type pattern. The schema dispatches on the field's `type` discriminator (draft-07 `if`/`then`):
     - `number` → `^-?[0-9]+(\.[0-9]+)?$`
     - `date` → an ISO `YYYY-MM-DD` **pattern**. Do not rely on `format: date`, because Ajv format support in Langfuse is unverified.
     - `id` / `text` → `string` with `minLength` as appropriate.
   - *Verified (SPIKE Addendum 2, 2026-09-19):* string values with per-type patterns via typed `if`/`then` are enforced on writes (`"twelve fifty"` and `"15/01/2026"` → 400). Langfuse's Ajv runs with `strict: true`, so the schema must follow the authoring rules pinned as contract **CT-05** (`docs/design/CONTRACTS.md`).
   - *Pattern limits (stated so nobody over-reads them):* the `number` pattern forbids currency symbols and thousands separators (`"$1,250.00"` is rejected; a golden stores `"1250.00"`). The `date` pattern is **syntactic only**: `"2024-02-31"` passes.
   - *Scope (R1):* patterns apply to `fields.<name>.value` only. Table cells and prompt answers are string-only until DEBT-04.
2. **`prompts` shape: keyed map (DATA-MODEL-01, ADR-0002), not array.** The spike's array was a probe artifact. The schema expresses the map with `additionalProperties: {<prompt schema>}` plus `propertyNames`.
   - **Prompt key (F10, resolved):** the IDP `prompt` string **verbatim**, 1–200 chars, no control characters: `propertyNames: {"type":"string","pattern":"^[^\\u0000-\\u001F\\u007F]{1,200}$"}`. The `[A-Za-z0-9_\-]` charset applies to field and table names only (ADR-0002 amendment 2026-09-19). The spike live-verified both the length bounds and the control-char pattern on `propertyNames` (newline/tab → 400; spaces + non-ASCII → 200; SPIKE-2026-09-19 "Follow-up probes"); CT-05 pins it as a regression guard.
   - **Uniqueness is not structural.** JSON object keys are unique only after parsing: the raw JSON editor collapses a duplicate key to one entry (last wins) without warning, so a Curator can silently lose a prompt. Uniqueness is asserted where it matters, at the `normalize()` trust boundary on the actual side (ADR-0002 collision rule); the golden side relies on JSON-object semantics, and the Epic E form fixes the editor path.
   - **`answer` shape:** the committed schema follows DATA-MODEL-01: `answer` is a plain string (`{"answer": "Acme Corp", "critical": false}`). The spike's `answer.value` object was a probe artifact.
   - Table rows likewise keep DATA-MODEL-01's flat `{column: "string"}` cells, not the spike's `{column: {value}}`.
3. **Schema form: generic, not per-field-name.** One compact schema describes shape and per-type patterns (fields/tables/prompts maps with typed cells). It does **not** enumerate every field name. This keeps it well under the **10,000-char cap** (`jsonSchemaValidation.ts:42`) independent of how many fields an action has: the fields+prompts part measured **954 chars minified** (SPIKE Addendum 2); the tables block is still to be measured (F2). Tightening with per-action `required` lists is optional, and each such schema must be measured against the cap before it is applied. Per-column table types (DEBT-04) can ride the same mechanism later.
4. **Schema evolution: expand/contract, never drop.** Because Langfuse rejects a schema change when any item fails it, a golden-shape migration is:
   - (a) apply an *expanded* schema that accepts old ∪ new;
   - (b) migrate the items;
   - (c) apply the *contracted* new schema.
   Removing the schema to migrate is forbidden, because it opens an unguarded window. Schema changes are versioned with the committed schema file. This rule is **policy**; the enforcing **control** is Decision #8.
5. **Score id: deterministic per invocation.** `score.id = uuid5(NAMESPACE, f"{run_id}|{document_id}|{score_name}")`, where `run_id` is a unique id generated at run start (**not** `run_name` alone).
   - `NAMESPACE` is a **committed, pinned UUID constant** in `src/idp_regression/platform/`. It is never generated at runtime and never changed; changing it would orphan every existing score id.
   - Retries within one invocation are idempotent (ADR-0004 #12). This closes DEBT-03 on the design side.
   - Two invocations that reuse a `run_name` can never overwrite each other's scores (N26).
   - `write_scores` may now retry on 5xx within ADR-0004's absolute retry deadline.
6. **v4 `events_only` ingestion reshapes S-01.3.**
   - Traces and dataset-run linkage go through OTLP / a v4-capable SDK.
   - `/api/public/ingestion` accepts only score events.
   - Scores are read via `/v3/scores` (`GET /v2/scores` returns 404).
   - Public reads can lag. CT-03 / N26 integration assertions must poll with a bounded wait rather than read-after-write.
7. **`golden_version` stays app-tracked** (content hash, INV-04). Langfuse `createdAt` resets on upsert and item history has no author, so neither can serve as the version. This is unchanged from ADR-0001 and holds on any platform.
8. **Run-start schema-drift check (R2; the control behind #4).** At run start, after `get_dataset` and before any IDP call, the orchestrator compares `sha256(canonical JSON of the dataset's expectedOutputSchema)` against the same hash of the committed schema file (canonical = sorted keys, no whitespace). On mismatch, including an absent schema, the run aborts non-zero with reason **`schema_drift`** (ADR-0004 taxonomy, CT-04). No scores are written.
   - *Alternative considered:* validating every golden on fetch against the committed schema in Python. Rejected: it adds a second validator (Python `jsonschema`) whose dialect behaviour differs from Langfuse's Ajv `strict`, so the two could disagree. It costs a pass over every item on every run. And it would not notice a weakened or removed schema on the platform, which is the exact gap #4 leaves open. The hash check is one comparison, and it makes a removed or edited schema impossible to run against. Items stay guaranteed conformant because Langfuse refuses to (re)apply a schema over non-conforming items.
   - *Interface note:* `get_dataset`'s return value carries the dataset's `expectedOutputSchema`. This is a widening of the returned value, not a new `PlatformAdapter` method. Confirmed by the spike: `GET /api/public/v2/datasets/{name}` returns `expectedOutputSchema` verbatim (SPIKE-2026-09-19 "Follow-up probes").
   - Becomes an S-01.3 (adapter exposes schema) / S-01.4 (orchestrator abort) DoD item for Dunga.

9. **Experiment linkage is recorded after the run (amended 2026-09-19, Soneca; closes Atchim S-01.3 R6 and specifies the R5 fix).**
   *Forces:* live on Langfuse 4.38.0 (`events_only`), a run shows up under the dataset's Experiments tab **only** when it was created by `Langfuse.run_experiment(data=…, task=…)` (langfuse==4.15.4). REST `dataset-run-items` plus hand-built OTel spans did not surface a run, even after 80 s. `run_experiment` runs items through `asyncio.gather(return_exceptions=True)`, so it logs and swallows task exceptions. It also logs and swallows a failed `dataset_run_items.create` (`client.py` ~L3036), calls `flush()` internally, and writes `EXPECTED_OUTPUT`, `input`, `output`, item `metadata` and `str(exception)` into span attributes. It reads items by duck typing: `id`, `dataset_id`, `input`, `expected_output` and `metadata`. `ExperimentItemResult` carries `trace_id` and `dataset_run_id`, and `run_experiment` accepts `metadata: Dict[str, str]`.
   - **Options.**
     - **(a) Record-after (chosen).** The orchestrator runs its own loop exactly as ADR-0004 describes. After the loop completes, it replays the precomputed results through `run_experiment` with a pure, total `task`.
     - **(b) Orchestrator loop inside `task`, with a re-raise sentinel.** Rejected:
       - the SDK owns iteration, so items after a failure still execute (INV-06 says no document is processed after an abort);
       - the span holding `output` is exported before our gate and write decision (INV-08);
       - per-document deadlines and token refresh end up inside an SDK-managed event loop.
     - **(c) Drop Experiments-tab visibility for MVP.** Viable, but it abandons #6/F3 for the Curator and Prompt Engineer personas. It stays as the **fallback** if the (a) live test fails (see S-01.3 below).
   - **Decision: (a).** The platform is a *recorder* invoked once, after every gate is already known. ADR-0004 containment runs unchanged in-process.
   - **Changed flow (amends ADR-0004 Flow §5e/§6, Containment #11/#13/#15 and the "scores written incrementally" API bullet):**
     1. Pre-run is unchanged: `get_dataset`, `empty_set`/`dataset_fetch_failed`, `schema_drift`, malformed-golden, `golden_version`.
     2. Loop, sequentially per item: `extract` → `classify` → `overall_gate` → `build_score_inputs` → append a `DocumentRecord`. **There is no platform write inside the loop.**
     3. On any abort inside the loop:
        - call `mark_run_status("aborted", …)` (best-effort, DEBT-01);
        - exit non-zero;
        - **no experiment and no per-document scores are written.** Partial-run evidence moves to the local structured log (document_id and abort reason, INV-02-clean). This is stricter than ADR-0004 #15: **for an in-loop abort**, nothing partial exists that someone could mistake for a baseline. That property is scoped to this step — it is **not** a property of #9 as a whole. See the step-4 residual below.
     4. After the loop, call `platform.record_run(...)` once. If it raises:
        - call `mark_run_status("aborted")` best-effort;
        - exit non-zero with `FlushFailedError` → `flush_failed`, and any other platform error → `hard_failure`;
        - **do not retry.** `run_experiment` is not idempotent per `run_name` (duplicate run items). A failed OTLP export batch has already been retried by the exporter and then dropped, so re-calling `flush()` cannot resend it. This supersedes ADR-0004 #13's "bounded flush retry".
        - **Partial-record residual (amended 2026-09-20, Soneca; Atchim A2). Step 4 is not all-or-nothing, and #9 must not be read as claiming it is.** `record_experiment` and `_write_scores` are two phases, and scores are written sequentially per record (`langfuse_adapter.py`). If the experiment records successfully and a score write then fails on document 5 of 10, the platform holds: a visible experiment under `run_name`; all N traces, **every span already carrying its verdict map plus `gate`** — so the partial evidence exists even when *zero* scores were written; 4 documents' scores; and a `mark_run_status("aborted")` marker that is itself best-effort and may fail (DEBT-01). A consumer reading that sees a partial baseline, possibly with no abort marker at all. ADR-0004 #13 stated this honestly for its own flow; #9 supersedes #13 and carries the caveat forward rather than dropping it.
        - **The CI gate is unaffected** — the exit code is a pure function of the in-process gates (INV-08), never of what landed on the platform. This is a **consumer-contract** problem, not a gate-correctness problem. **Consumer contract (fail-closed read):** a run is a usable reference **only** if `run_status=complete` is present. Consumers must key on the *presence of `complete`*, never on the *absence of `aborted`* — the aborted marker is best-effort and may be missing from a run that genuinely failed. This obligation belongs to the Epic E remediation UI and is added to **F8**'s scope.
        - **`run_name` uniqueness (amended 2026-09-20, Soneca; DEBT-19 closed).** `run_name` is Langfuse's dataset-run/experiment merge key: two `record_run` calls with the same `run_name` and different `run_id` merge into one experiment (live-confirmed, TP-37). The orchestrator therefore passes a composed name, `f"{run_name}-{run_id[:8]}"`, preserving the operator's `--run` label as the prefix. Uniqueness for scores (`run_id`) and for experiments (`run_id[:8]`) rest on the same identifier rather than on two that can disagree. Rejected: a pre-run "does this name exist" read (a new fail-closed network dependency, and a TOCTOU race, to police a name), and reworking run-level identity (unjustified — DEBT-19 is a UI merge, not a data-safety defect).
     5. `mark_run_status("complete", …)`, then exit from the **in-process** gates (INV-08 holds trivially because no write precedes any gate).
     - The record phase sits outside the per-document poll budget (ADR-0004 #3). It is bounded by the OTel `force_flush` timeout (30 s default) plus the REST transport timeout (R7).
   - **Interface (`platform/types.py`, the `PlatformAdapter` Protocol):**
     ```python
     class DatasetItem(TypedDict):
         item_id: str            # NEW: opaque platform item id, kept from get_dataset
         document_id: str
         golden: Golden

     class DocumentRecord(TypedDict):
         item_id: str
         document_id: str
         actual: NormalizedOutput          # CT-01 shape only
         scores: list[ScoreInput]          # built after the gate (INV-08)

     class RunMetadata(TypedDict):
         action_id: str
         action_version: str
         golden_version: str

     class PlatformAdapter(Protocol):
         def get_dataset(self, name: str) -> Dataset: ...
         def record_run(self, *, dataset_name: str, run_name: str, run_id: str,
                        records: list[DocumentRecord], metadata: RunMetadata) -> None:
             """Record a complete run: an experiment visible in the Experiments tab,
             plus per-document scores on each item's trace. Raises
             ExperimentRecordFailedError | ScoreWriteFailedError | FlushFailedError."""
         def mark_run_status(...) -> None: ...   # unchanged
     ```
     - `write_scores`, `flush` and `run_dataset_experiment` **leave the Protocol**. They become private adapter internals (`_write_scores(trace_id, scores)`, and the watcher inside `record_run`).
     - No SDK type crosses the Protocol, so N24 holds.
     - *Amendment 2026-09-19 (Soneca, QA-01-S-01.3 F-4; DEBT-18 option B):* `DocumentRecord` as built has **no `actual` field**. It is `{item_id, document_id, scores}` only (`platform/types.py`). The `actual: NormalizedOutput` line above is superseded: the normalized actual stays in-process and is never passed to the platform adapter.
   - **Item mapping (INV-04: no second fetch).**
     - `get_dataset` fetches the schema via `GET /v2/datasets/{name}` and the items via paginated `GET /api/public/dataset-items?datasetName=` (R1). It keeps a private `item_id → (dataset_id, expectedOutput)` map from that same response.
     - `record_run` builds adapter-private frozen `_ExperimentItem(id, dataset_id, input={"document_id": …}, expected_output=<stored golden>, metadata=None)` duck-typed objects. *(Amended 2026-09-19, DEBT-18 option B: `expected_output={}` as built (`langfuse_adapter.py`); the stored golden is not copied into items or spans. See the allowlist below.)* It does not construct `DatasetItemClient` objects and does not re-fetch.
     - Precondition, checked before any SDK call: the `records` item_ids equal the fetched item_ids exactly (same set, no duplicates). Otherwise `record_run` raises `ExperimentRecordFailedError`.
     - **Second precondition — `run_id` is verified, not decorative (amended 2026-09-20, Soneca; Atchim A3).** As first shipped, `record_run`'s `run_id` parameter was never read in the adapter body: score ids are precomputed by the orchestrator through `build_score_inputs` (`platform/scoring.py`), so the adapter had no use for it. That is two defects, not one unused argument: (1) the Protocol advertises a parameter no implementation consumes, and a swapped adapter (N24) cannot tell whether it is obliged to use it; (2) it encodes an **unchecked invariant** — that every `DocumentRecord.scores[*].id` was derived from *this* `run_id` — on which N26's cross-invocation no-overwrite guarantee rests. Nothing verified it, so a run whose score ids belonged to a different invocation would record as correct.
       - **Ruling: keep `run_id` and verify it.** Dropping it was considered and rejected: the Protocol must stay implementable by a non-Langfuse adapter that derives ids itself. §Option B records that Opik's natural-key upsert is idempotent "only if trace/entity ids are made deterministic", which needs a run-scoped id at exactly this seam. Dropping `run_id` would make the runner-up platform un-implementable behind this Protocol and would silently raise the reversal cost §Reversal cost claims is still low.
       - **Protocol-level obligation (generic — `types.py` docstring, no code):** every `scores[*].id` MUST be derived from the `run_id` passed in the same call. An implementation MAY verify this, and MUST raise `ExperimentRecordFailedError` if it verifies and the check fails.
       - **Langfuse-adapter control (concrete — the existing precondition block, before any SDK call):** for every record and every score, assert `score["id"] == score_id(run_id=run_id, document_id=record["document_id"], score_name=score["name"])`. Any miss raises `ExperimentRecordFailedError`. A pure local loop over data already in hand: no new dependency, no extra call, no network. The assertion lives in `LangfuseAdapter` and **not** in the Protocol, because the id scheme is this adapter's — the Protocol states the obligation, each adapter enforces its own form of it. Fixing an N24 trap must not introduce another one.
       - **INV-02:** the raise names `document_id` + `score_name` only. Both are value-free by construction (`field:<name>` is charset-restricted; `prompt:<16-hex>` is a digest — REG-05/F-5). A golden value never appears in the message, and neither does the offending id pair beyond those two names.
       - This converts N26 from orchestrator discipline into a boundary-checked precondition.
   - **Span-attribute allowlist (INV-01/INV-02).** Spans may contain only:
     - `input = {"document_id"}`;
     - `expected_output` = `{}` (the SDK types it as a dict). **Amended 2026-09-19 (DEBT-18 option B, user decision):** the golden is not copied into spans; spans reference the dataset item by id.
     - `output` = the verdict map `{score_name: verdict}` plus `gate` only. **Amended 2026-09-19 (DEBT-18 option B):** the normalized actual is never sent to the platform, and neither are expected or confidence values;
     - trace metadata = the three `RunMetadata` strings;
     - item metadata = `None`.

     The `task` is a total dictionary lookup that cannot raise. As defense in depth, it catches everything, returns the constant `{"record_error": "task_failed"}` (no exception text), and sets a flag that makes `record_run` raise. As a result, `str(exception)` never reaches a span.
   - **Failure detection (R5).**
     - **Log watch.** One watcher is installed for the **whole** `run_experiment` call plus a trailing explicit `flush()`. It sits on `opentelemetry.exporter.otlp.proto.http.trace_exporter` and on `langfuse`, at ERROR. Handlers are process-wide, so this covers (a) the internal flush and (b) batches exported by the background thread. An exporter ERROR raises `FlushFailedError`; a `langfuse` ERROR (item failure, run-item create failure, case c) raises `ExperimentRecordFailedError`. The watcher records only the logger name and level, never the message, because messages may carry API error bodies.
     - **Log-watch failure envelope (amended 2026-09-20, Soneca; Atchim A1). The log watch is an accepted heuristic, not a contract.** The langfuse SDK offers no raising signal for a swallowed item failure or a dropped export batch — `run_experiment` logs and swallows, and the OTLP exporter drops after exhausting its own retries — so a log watch is the only observable signal available. It is adopted knowingly, with two residuals stated here rather than discovered later:
       - **False negative (detection goes dark).** Detection binds to two logger names, `opentelemetry.exporter.otlp.proto.http.trace_exporter` and `langfuse`, at ERROR. A `BatchSpanProcessor` queue drop logs on `opentelemetry.sdk.trace.export`, typically at WARNING, and is invisible to both watchers; a patch release that downgrades an ERROR to WARNING disables detection silently. **The R5 unit tests do not catch this** — they raise ERROR on those loggers themselves, so they test the watcher, not the coverage. *Blast radius, stated precisely:* INV-08 means the CI gate never reads the platform, so an undetected drop does **not** produce a false-green gate. It produces a run that exits 0 while its evidence did not land — an incomplete reference for INV-04 reproducibility and for the Epic E remediation UI, found long after the fact. *Mitigation:* the langfuse and OTel exporter versions are pinned (DEBT-17), and the watcher's logger names and levels must be re-probed against a live instance before any version bump — the same rule that already governs re-probing CT-05.
       - **False positive (a healthy run aborts).** Handlers are process-wide, and the property that makes the watch work is the same one that makes *any* ERROR on the `langfuse` logger inside the window abort the run, including one raised by unrelated SDK activity. That yields `flush_failed`/`hard_failure` on a healthy run: a **false FAIL on another team's prompt-change PR**, which §Context ranks as the worst outcome this product can produce. No correlation id is available to narrow the watch, so it is accepted. It is at least **fail-closed** — it blocks a good PR, it never passes a bad one — and the watcher records the logger name and level (never the message, INV-02) so an operator can tell which watcher fired.
       - *On ranking the two:* they sit on different axes and neither dominates. The false positive is worse per incident — it blocks correct work and spends the gate's credibility, which is the one thing a CI gate cannot rebuy. The false negative is worse over time — a baseline that cannot be reproduced, discovered late. Both are named so that neither is traded away silently in a later tightening.
       - **Fallback (c) stays live after S-01.3, not only at the S-01.3 live test.** If the watcher proves flaky in practice — a false abort on a healthy run, or a version bump that silences detection — fall back to (c): scores on deterministic `trace_id()` values, Experiments-tab linkage deferred to Epic E. Do not improvise a third path; tell Soneca.
     - **Structural check (positive, in-band).** `len(item_results) == len(records)`, every `trace_id` is set, and every `dataset_run_id` is non-null and identical. Any miss raises `ExperimentRecordFailedError`.
     - **Scores.** Written only after both checks pass, per record, against that item's returned `trace_id`, with the deterministic `score_id` (#5, unchanged). A score write is retried within the transport budget because it is idempotent.
   - **Fallback.** If the S-01.3 live test shows `run_experiment` does not return usable `trace_id`/`dataset_run_id` on 4.38.0, fall back to (c): scores go on deterministic `trace_id()` values, experiment linkage is deferred to Epic E, and Soneca is told. Do not improvise a third path.
   - **Split:**
     - **S-01.3 (Dengoso, now):** `types.py` Protocol + TypedDicts; `get_dataset` (R1, item_id + private map); `record_run` (precondition, private items, total task, watcher, structural check, scores on returned trace ids, `metadata=RunMetadata`); new `ExperimentRecordFailedError`; remove the public `run_dataset_experiment`/`flush`/`write_scores` from the Protocol. Tests:
       - TP-45: in-memory span exporter asserting the allowlist above, and that no `IDP_DOCUMENT_DIR`/path sentinel appears in any attribute;
       - R5 unit tests: exporter ERROR raised from a background thread mid-run; `langfuse` ERROR; missing `dataset_run_id`; short `item_results`;
       - live opt-in: 2 synthetic items → the run is visible in `GET /api/public/experiments` (bounded poll), and scores are readable on the returned trace ids.

       R2/R3/R4/R7 remain his.
     - **S-01.4 (orchestrator):** in-loop `DocumentRecord` accumulation; a single `record_run` after the loop; the abort path writes only the marker; the error → abort-reason mapping above; no flush retry. Tests: INV-06 (no `record_run` call after any abort), INV-08 (every gate computed before `record_run`), and INV-04 (`RunMetadata` passed on every zero-exit run).
   - **Accepted coupling:** duck-typed items and `ExperimentItemResult` fields depend on langfuse==4.15.4 internals (DEBT-17). The pinned version and the live test are the guard.

### MVP Curator interim (until Epic E)
Golden edits in MVP are low-volume. The Curator edits JSON in the Langfuse UI. Guard-rails:
- The server-side schema, which blocks invalid saves.
- A short Curator runbook: where the schema hover card is, what "Save does nothing" means, and the value patterns per type.
- Escalation to a Prompt Engineer when a save is blocked.

A local `validate-golden` helper (the same schema, `allErrors`, path-level messages) is a *candidate* for Epic C. That scope is Dunga's call, not decided here.

## Design patterns
- **Adapter (GoF)**: *pattern* unchanged from ADR-0001; the **interface is reshaped by Decision #9** — see §API contract (deltas). `PlatformAdapter` Protocol plus a `LangfuseAdapter` class; the SDK and raw REST stay confined to `src/idp_regression/platform/` (N24). The v4 ingestion split (OTLP for traces, REST for scores) is hidden behind the adapter.
- **Specification (schema as data)**: the golden JSON Schema is a committed, versioned data artifact. Langfuse enforces it at write time and the Epic E form renders it. Stack-idiomatic form: a JSON file plus a small provisioning function that upserts it via REST. It is **not** a Python class hierarchy.
- **Deterministic identity (ad-hoc, a pure function)**: `score_id(run_id, document_id, score_name) -> UUID` is a plain function. No GoF pattern fits better, and wrapping it would be over-application.

## API contract (deltas)
- `PlatformAdapter` interface: **reshaped by Decision #9** — `get_dataset` / `record_run` / `mark_run_status`, and still no `get_golden_version` (INV-04 stays a single-fetch content hash). `write_scores`, `flush` and `run_dataset_experiment` are adapter-private internals behind `record_run`; there is no public flush seam and no flush retry.
- New observable behaviours we commit to (Hyrum's Law):
  - Score ids are deterministic in `(run_id, document_id, score_name)`. Consumers may rely on "re-writing a score within a run replaces it".
  - Every golden dataset carries an `expectedOutputSchema`. Consumers (Epic E form, Epic C tooling) may assume stored goldens conform to the committed schema version. A run never proceeds against a drifted schema (Decision #8, abort `schema_drift`).
- Versioning: the golden schema file is versioned. Changes follow expand/contract (Decision #4). Score keys (`field:<name>`, `gate`) remain immutable (BR11).

## Consequences

- **Positive:**
  - Golden shape and `fields` value patterns are guarded on every write path (API and platform UI) from day one. Table cells and prompt answers are string-only until DEBT-04 (R1).
  - Score writes become retry-safe.
  - S-01.3 is unblocked on known integration facts.
  - The schema artifact is reused by Epic E, so there is no throwaway.
  - The classifier (S-01.1, Done at full rigor) is untouched: string typing stays.
- **Negative:**
  - **Curator UX consequence (explicit):** in MVP the non-engineer Curator edits **raw nested JSON** with schema guard-rails. Saves of invalid data are **blocked silently** (no inline message, no JSON path, first error only). This is a known UX gap. **Epic E must fix it** with a schema-driven form (rendering `expectedOutputSchema`), path-level `allErrors` validation messages, an actor-attributed write path, and an undo built on point-in-time reads. The EX-C1-1 / EX-C1-2 "no-code Curator" expectation is **not met in MVP**. Feliz should reflect this in UC-C1 when it is discovered.
  - No per-user audit trail of golden edits until Epic E. Change *detection* is via INV-04 content hash only.
  - We give up Opik's dataset diff/restore.
  - Self-hosting obligations from ADR-0001 (encryption at rest, DB access control, backup/restore; N25) carry over unchanged and still **block any real-golden load** (Mestre).
- **Open design follow-ups (owners):**

| # | Follow-up | Owner | Blocks |
|---|---|---|---|
| F1 | **RESOLVED 2026-09-19** (SPIKE Addendum 2): `fields` + `prompts` with string values and per-type patterns via typed `if`/`then` are enforced; `propertyNames` length bounds enforced; generic schema is ~954 chars minified. Ajv `strict: true` authoring rules captured as CT-05. Still open and moved to F2: measure the tables block and the full-schema length. | — | — |
| F2 | Commit the golden JSON Schema (versioned) and a REST provisioning step (raw REST; the Python SDK schema support, #10688, is unverified). Measure the tables block and the full minified schema against the 10k cap. Schema passes CT-05. | Dengoso (S-01.3) / Soneca reviews schema | golden load |
| F3 | S-01.3 adapter reshaped for v4 `events_only`: OTLP/v4 SDK for traces and dataset-run linkage, scores via `/api/public/scores` with deterministic `id` (Decision #5), reads via `/v3/scores`, bounded-poll integration assertions. Pin the SDK version. **SPEC-01 wording:** the S-01.3 DoD line (~114) still says idempotency key `(run_name, document_id, field_name)` / "else NO retry"; replace it with the Decision #5 deterministic `score.id` (`run_id`, not `run_name`) and retry-within-deadline. | Dunga (re-scope S-01.3 tasks/estimate) → Dengoso | S-01.3 |
| F4 | ADR-0004 #12 text: the retry-on-5xx branch is now "retry with deterministic score id". Close DEBT-03. | Soneca (ADR-0004 amendment after Atchim gate) / Dunga (DEBT-03) | S-01.4 |
| F5 | DATA-MODEL-01 §1/§4: "Pending ADR-0005 follow-up" notes added. Fold the schema reference and score-id rule in properly once F1 confirms. | Soneca | — |
| F6 | Expand/contract golden-schema migration procedure documented for Epic C. | Soneca (Epic C design) | first schema change |
| F7 | Curator runbook for the MVP JSON editor (schema hover card, silent-block meaning, per-type patterns, escalation). | Dunga to card (Epic C) | Curator onboarding |
| F8 | Epic E ADR: schema-driven Curator form, path-level errors, actor attribution, undo, browser trust boundary (secret key never client-side). **Plus the #9 step-4 consumer contract (A2):** the UI must treat a run as a usable reference only on the *presence* of `run_status=complete`, never on the absence of `aborted`, and must render a run with an experiment + traces but incomplete scores as *partial/unusable* rather than as a baseline. | Soneca (at Epic E `/design`) | Curator no-code editing |
| F9 | Candidate NFR-01 row: "a schema-invalid golden write is rejected on API and UI paths", verified at S-01.3 integration. | Soneca (next NFR pass) / Atchim confirms | — |
| F10 | **RESOLVED 2026-09-19** (R4): prompt key = IDP `prompt` string verbatim (1–200 chars, no control chars, matches `propertyNames`); `[A-Za-z0-9_\-]` applies to field/table names only; prompt-derived score names use a charset-safe derived id (INV-03). ADR-0002 amended; DATA-MODEL-01 example fixed. | — | — |

## Reversal cost

**Low now, High after the first real golden load.** Nothing real is loaded. The spike datasets are synthetic, and N25 obligations block real data anyway. The adapter confines the integration. Switching to Opik today costs an Opik spike, an adapter, and app-side golden validation. After real goldens and run history live on Langfuse, reversal is a migration of curated data plus history, which effectively locks the choice.

The part of this decision most likely to be revisited is **not** the platform. It is the MVP Curator interim, which Epic E replaces. If Epic E builds its own form and validation layer, the platform's UI stops being the Curator's surface, and golden storage rests purely on API-level properties. On those properties Langfuse's schema enforcement and client-id scores remain the deciding advantages.

Atchim review: **APPROVED (R1–R4 closed, re-checked 2026-09-19)**. Original verdict: APPROVE WITH NOTES (top-level gate, 2026-09-19). Decision A approved; 4 Required amendments (R1 integrity-claim scope, R2 run-start schema-drift check, R3 F1 resolved + Ajv strict authoring rules as contract, R4 F10 resolution) must land before S-01.3 schema provisioning (T-01.3.0 / F2), not before Dunga re-scopes.

Amendments applied (Soneca, 2026-09-19): R1 integrity claim narrowed to `fields` (§Threat model, Option A, Decision, Consequences); R2 run-start schema-drift abort (Decision #8); R3 F1 resolved, Ajv strict authoring rules as contract CT-05; R4 F10 resolved (ADR-0002 amendment). Suggestions folded: `answer` shape, duplicate-key collapse, pattern limits, pinned `NAMESPACE`, SPEC-01 idempotency wording (F3), project-roles assumption (to confirm, Mestre). Atchim confirmation: APPROVED (R1–R4 closed, re-checked 2026-09-19).

Amendment 2026-09-19 (Soneca): Decision #9 added (record-after experiment linkage; `PlatformAdapter.record_run`), in answer to Atchim S-01.3 R6 and specifying the R5 fix. **Atchim CONFIRMED 2026-09-20** — re-derived from the ADR text and the code independently of the fact that it shipped; record-after ruled correct against options (b) and (c). Three required doc-only amendments attached and applied 2026-09-20 (Soneca): **A3** `run_id` kept and verified at the `record_run` precondition (Item mapping); **A1** log-watch failure envelope, both residuals named, heuristic accepted, fallback (c) live after S-01.3 (Failure detection); **A2** step-3 claim scoped to in-loop aborts, step-4 partial-record residual and consumer contract stated (Changed flow, F8). L233/L238 consistency fixes applied in fe09898.

---

## Amendment A4 — the table score family (DEBT-14), 2026-09-24

**Status:** Accepted (Soneca, design pass). Amends CT-03 and DATA-MODEL-01 §4. **Does not amend
DEBT-18 option B, `## Domain`, INV-01 or ADR-0007** — all stand as written.

### The gap

The classifier computes a full per-row, per-column table verdict block and `build_score_inputs`
then throws it away: only `field:<name>`, `prompt:<16-hex>` and `gate` are written. A line-item
regression therefore exists in the platform only as `gate = FAIL` with no score that moved — in the
compare view, a table regression and an unexplained failure look identical. The information is
already computed; the cost here is deciding a *shape*, not building a feature.

### Options considered

- **A — per-cell `table:<name>:<row>:<col>`.** Rejected on two independent grounds. (1) Row identity
  is the `match_key` **value** — golden content (`"Widget A"`, and in a real invoice a part number
  or a customer reference). Putting it in a score *name* writes expected content onto the platform
  outside the dataset item, which is DEBT-18 option B violated in substance even if the same string
  already exists in the item; score names are also immutable (BR11) and un-deletable in practice.
  (2) The count is unbounded in rows × columns, against N9.
- **B — per-column `table:<name>:<column>`.** No value leak (column names are schema-ish), bounded
  by golden columns. Rejected as the *first* step, not as wrong: column names have **no charset
  guard** in the golden schema today (`rows` items are an open `{string: string}` map), so a column
  named `a:b` or with a newline would break INV-03 — it needs a `propertyNames` rule first, and that
  rule is a golden-invalidating tightening, not an additive one. Revisit for Epic E if per-table
  proves too coarse; do it as its own change with its own migration.
- **C — one `table:<name>` roll-up per golden table per document.** Chosen.

### Decision — Option C

**One `table:<name>` score per golden-declared table, per document, per run.**

- **Name:** `table:<name>`, where `<name>` is the golden table name — already charset-guarded by the
  §1 C1 `propertyNames` rule `^[A-Za-z0-9_-]{1,128}$`, so INV-03 holds by construction and no new
  guard is needed. Prefix `table:` cannot collide with `field:`/`prompt:`/`gate`.
- **Value:** a **single verdict literal**, the worst row-verdict in the block, under this fixed
  precedence: `missing` > `wrong_value` > `wrong_format` > `new_line` > `match`. `dataType`
  `CATEGORICAL`, like every other score here.
- **`comment: None`** — DEBT-18 option B. No cell value, no column name, no `match_key`, no
  confidence, no row count. **Verdicts only.** That is the whole reconciliation: the score answers
  *"did this table regress, and how badly"*, and nothing else.
- **Empty golden `rows: []` → `match`**, because `overall_gate` returns PASS for that block. The
  score and the gate must never disagree; a score that says "bad" while the gate says PASS is worse
  than no score.
- **An unrecognised row verdict must raise**, not fall through to `match`. This is the same
  fail-open shape as FO-4/FO-5 and it is the default a roll-up invites. Derive the precedence table
  from `VerdictLiteral` so a future eighth literal with no precedence entry fails loudly rather than
  being silently ranked lowest.
- **`id`:** the existing `score_id(run_id=…, document_id=…, score_name="table:<name>")` — no new
  derivation, retry-safe and run-scoped exactly like the others (N26).
- **Actual-only tables (`new_table`, ADR-0003 A2) are NOT scored.** `build_score_inputs` iterates
  the **golden**, and always has — `new_field` is not scored either. Keeping that rule means no
  IDP-controlled name can ever become a score key, and N9's formula stays bounded by the curated
  golden rather than by whatever an action decided to emit. The `new_table` signal lives in the
  verdict map and ADR-0007's local `0600` artifact.

### Why a roll-up is enough

ADR-0007 is accepted: the full verdict detail — every row, column, expected and actual — is written
to a local gitignored `0600` artifact. The platform's job is therefore only to make a regression
**visible and comparable across runs**, not diagnosable. A per-table verdict does that: the compare
view shows `table:line_items` moving `match → missing` between two runs, which is precisely the
"better or worse" question this product exists to answer. Anything finer is diagnosis, and diagnosis
has a home that does not require putting more data on an instance that (SIGNOFF B-1/B-2) still runs
on a published encryption key.

### NFR N9 — the score-count formula changes

From `N_documents × (N_fields + 1)` to
**`N_documents × (N_fields + N_prompts + N_golden_tables + 1)`**. Still linear in the *golden*, still
with no actual-derived term, so "no unbounded score write" holds — but the stated formula was
already stale (it omitted `prompt:` scores, which have shipped) and is now correct. CT-03's count
assertion must be updated in the same change, and it should assert the formula, not the number.

### Migration and reversal

**No migration.** Additive: new score names in new runs; every existing score keeps its name, id and
value. Old runs simply have no `table:*` scores, which reads correctly as "this run predates table
scoring". **Reversal cost: low but one-way in one respect** — score names are immutable (BR11) and
already-written `table:*` scores stay on the platform forever. Stopping is "stop emitting"; it is
not "undo".
