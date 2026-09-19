# ⚠️ Spike — Can a non-engineer Curator edit a nested golden in Langfuse "form mode" with schema validation?

**Throwaway:** the code from this spike is exploratory and NOT shippable. No TDD stamp was written; the board was not advanced. To ship: `/design` (if an ADR is needed) then `/implement` (test-first).

**Date:** 2026-09-19  ·  **Branch:** spike/langfuse-form-mode  ·  **Linked spec:** S-01.5 (SPIKE-01, gates ADR-0001 / ASM-05)
**Method:** multi-agent — Dengoso (live API probe), research agent (Langfuse source @ v4.38.0 + changelog), top-level (live UI in browser). All data synthetic; no credentials written to any artifact.

## Question
ADR-0001 chose Langfuse PROVISIONALLY on the claim that its "form mode" renders a nested golden (`fields` of mixed types, `tables.line_items.rows[]`, `prompts[]`) as **schema-validated editable form fields**, so the Golden Set Curator (non-engineer) can edit it without code (EX-C1-1 / EX-C1-2). Is that true on our self-hosted Langfuse?

## Environment
Self-hosted Langfuse **4.38.0 OSS** (Docker: web, worker, postgres 17, redis 7, clickhouse 25.12, minio), `LANGFUSE_HOST=http://localhost:3000`. Server runs in v4 `events_only` ingestion mode.

## Approaches tried
- **API probe (Dengoso)** — created dataset `spike-01-form-mode` with `inputSchema` + `expectedOutputSchema` (JSON Schema, `additionalProperties:false`, `total.value: number`), loaded one synthetic nested item, then fired invalid writes, a valid nested update, a schema-over-nonconforming-items change, score-id upserts, and a point-in-time version read. Scripts: `spikes/langfuse-form-mode/{lf,golden,probe,probe2}.py`.
- **Source/changelog research** — sparse clone of `langfuse/langfuse` @ `ef0add7` (web `package.json` = 4.38.0); read `web/src/features/datasets/**` (`DatasetItemField(s).tsx`, `DatasetSchemaHoverCard.tsx`, `DatasetItemFieldSchemaErrors.tsx`, `hooks/useDatasetItemValidation.ts`) and `packages/shared/src/utils/jsonSchemaValidation.ts`, `.../server/repositories/dataset-items.ts`.
- **Live UI (top-level, Chrome)** — opened the item, used **Edit** (⋯ menu), typed an invalid value, clicked **Save changes**, read back via API.

## What we learned

| Capability | Result | Evidence |
|---|---|---|
| Nested golden stored & round-tripped (fields + tables rows + prompts, add row, edit cell) | ✅ Confirmed | API update + read-back deep-equal; numbers stay numbers |
| JSON Schema on dataset items (`inputSchema`, `expectedOutputSchema`) | ✅ Confirmed (OSS, since v3.128.0, 2025-11) | OpenAPI + source; `POST /api/public/v2/datasets` (upsert by name) |
| Server-side enforcement on create/update (API **and** UI) | ✅ Confirmed | 400 `InvalidRequestError` for string-in-number, missing required key, bad row cell, malformed prompt; stored value unchanged |
| Adding a schema over non-conforming items | ✅ Rejected with per-item JSON paths | 400, `path:/fields/total/value` |
| **UI renders the schema as editable form fields** | ❌ **Refuted** | Edit dialog = CodeMirror **raw JSON editor**; schema only shown in a hover card + example object; no rjsf/jsonforms dependency in source |
| **Curator sees why an edit is invalid** | ❌ Weak | UI silently blocks **Save changes** (dialog stays open, no message while editing); item-level API errors omit the JSON path; Ajv `allErrors:false` → first error only |
| Item version history | ✅ Exists (point-in-time `?version=<ts>` read; UI "Show version history") | No per-user author column → not an audit trail; `createdAt` resets on upsert |
| Idempotent score writes (ADR-0004 #12, S-01.3) | ✅ Confirmed | `POST /api/public/scores` with client `id` twice → one row, last write wins |

**Verdict: PARTIALLY CONFIRMED — the load-bearing Curator capability is REFUTED.** All nested shapes store, round-trip, and are **schema-validated server-side** (the integrity half holds, for every shape). But **no shape** renders as editable form fields: the Curator edits raw nested JSON in a code editor and gets no inline explanation when Save is blocked. Per SPIKE-01, "nested editing falls back to raw JSON in a text box" is the refutation condition for the *no-code Curator* differentiator — mitigated here only by the fact that validation *is* enforced.

## Recommendation
- **Do not remove ADR-0001's PROVISIONAL label.** Route to **Soneca `/design`** for the SPIKE-01 re-decision ADR (supersedes ADR-0001), evaluating Opik's editing path **symmetrically** on the same golden. The realistic options are now:
  1. **Langfuse + custom Curator form in Epic E** built on its API — Langfuse already supplies the hard parts (server-enforced schema, versioning, idempotent scores); Epic E adds a schema-driven form (e.g. rjsf) over `expectedOutputSchema`, with path-level errors.
  2. **Accept JSON editing for MVP** — the Curator edits JSON with schema guard-rails; revisit with Epic E.
  3. **Opik** — only if it offers a genuinely better no-code nested editor (to be checked; the research agent did not cover Opik).
- The spike's evidence leans to **option 1 or 2 on Langfuse** (the platform-side guarantees are strong; the gap is purely UI and lives in a planned epic anyway) — but that is Soneca's call in the ADR, not this spike's.
- **S-01.3 (Langfuse adapter) may proceed only if the re-decision keeps Langfuse**; its integration facts are now known (below).

## Risks / open unknowns (for /design and S-01.3)
1. **Golden `value` typing vs DATA-MODEL-01.** The data model stores `value` as string; the schema can only type-check if `number` values are JSON numbers — which drops formatting (`1150.00` → `1150`). Decide: typed numbers (schema-validated) vs strings + regex patterns. Also `prompts` is a keyed map in DATA-MODEL-01 but an array here. → ADR-0002/0003 + DATA-MODEL-01.
2. **Schema size cap 10,000 chars** (`jsonSchemaValidation.ts:42`) — measure the real golden schema (multi-table, `$defs`) against it.
3. **Schema evolution is all-or-nothing** — a schema change fails if any item doesn't conform; golden migrations must move data + schema together.
4. **v4 `events_only` mode** — traces/runs need OTLP or a v4 SDK; `/api/public/ingestion` accepts only score events; `GET /v2/scores` is 404 (use `/v3/scores`); public reads can lag. Reshapes S-01.3's adapter.
5. **`golden_version` stays app-tracked** — `createdAt` resets on upsert; the content-hash approach (ADR-0001/INV-04) remains correct.
6. **Python SDK schema support** (issue #10688) unverified — S-01.3 may need raw REST for dataset schemas.
7. **Self-hosting obligations** (encryption at rest, DB access control, backup — NFR N25) still owed to Mestre before real golden data.
8. **Opik not evaluated** in this spike.

## Addendum — symmetric Opik evaluation (2026-09-19, source-level)

Source: `comet-ml/opik` @ `0647a9c` (version.txt 2.2.71; latest release 2.2.70, 2026-09-18). Not stood up live — source inspection only.

| Criterion | Langfuse 4.38.0 OSS (live-verified) | Opik 2.2.x OSS (source) |
|---|---|---|
| Nested editor | Whole item as raw JSON (CodeMirror) | Top-level keys as accordion sections; each nested object/array (`fields`, `tables`, `prompts`) is raw JSON (CodeMirror) — `DatasetItemEditor/{DatasetItemEditorForm,JsonFieldEditor}.tsx` |
| Schema-generated form fields | ❌ none | ❌ none (no rjsf/jsonforms) |
| Schema / type enforcement | ✅ `expectedOutputSchema`, server-side, API + UI | ❌ none — `data: Map<String, JsonNode>`, only "valid JSON" |
| Editor error feedback | ❌ Save blocked silently | ✅ inline "Must be a valid JSON object or array" (syntax only) |
| Versioning | Item point-in-time + UI history | ✅ dataset-level versions with diff + restore + change description |
| Idempotent scores | ✅ client-supplied score `id` upsert | ⚠️ natural-key upsert `(entity, author, name)` — no client id |
| Item shape | `input` / `expectedOutput` / `metadata` | flat `data` keys (no separate expected output) |
| Footprint | web, worker, postgres, redis, clickhouse, minio | Java backend, Python backend, nginx frontend, mysql, redis, clickhouse, **zookeeper**, minio |
| Curator extras | Annotation queues (scores on traces only) | Annotation queues (traces/threads only); "add to test suite" from traces |

**Symmetric conclusion:** neither platform gives a non-engineer a no-code nested-editing surface — the ADR-0001 differentiator does not exist on either. Langfuse wins on **golden integrity** (server-enforced types) and client-id idempotent scores; Opik wins on **editor feedback** and **version diff/restore**. A safe non-engineer nested editor requires our own schema-driven UI (Epic E) on either platform.

## Addendum 2: ADR-0005 F1 string-typed golden schema, live-probed (2026-09-19)

Script: `spikes/langfuse-form-mode/probe_patterns.py`. Dataset: `spike-01-patterns`, with **string** `value`s as ADR-0005 decides.

| Case | Result |
|---|---|
| `type:number`, `value:"1150.00"` (string, pattern `^-?\d+(\.\d+)?$`) | ✅ 200, formatting preserved |
| `type:number`, `value:"twelve fifty"` | ✅ 400 `must match pattern` (per-type `if`/`then` enforced) |
| `type:date`, `value:"15/01/2026"` | ✅ 400 `must match pattern` |
| `type:text`, free-form value | ✅ 200 (pattern applies only to its type) |
| prompts keyed by prompt text with spaces (`"What is the vendor name?"`) | ✅ 200 |
| empty prompt key / key > 200 chars (`propertyNames`) | ✅ 400 `property name must be valid` |

**Langfuse's validator is Ajv with `strict: true`. It rejects a schema at write time with 400 `Must be a valid JSON Schema` unless:**
1. every `if`/`then` subschema declares `"type"` (strictTypes); and
2. every key listed in `required` is declared in `properties` (strictRequired).

The golden-schema generator (S-01.3) must follow both rules, and a contract test should pin them.

**Size:** the generic fields+prompts schema is 954 characters minified, against the 10,000 cap. Tables add a similar block, so the full generic schema should stay around 2k (estimated). A per-field, per-action schema is the only variant that could approach the cap.

**F1 is resolved:** string values with per-type patterns are enforceable in Langfuse 4.38.0. **F10 is informed:** keying prompts by prompt text with spaces works at the platform layer. The ADR-0002 `[A-Za-z0-9_\-]` field-name sanitisation is the only conflict left, and it is an app-side rule.

**Follow-up probes after ADR-0005 R4:**
- **Control characters in prompt keys.** Script: `probe_ctrl.py`; dataset: `spike-01-promptkeys`. The `propertyNames` pattern `^[^\u0000-\u001f\u007f]{1,200}$` is enforced:
  - a newline or tab in the key → 400;
  - spaces (`"What is the vendor name?"`) and non-ASCII (`"Qual é o fornecedor?"`) → 200.
- **Schema read-back endpoint.** `GET /api/public/v2/datasets/{name}` returns `expectedOutputSchema` exactly as stored. This was used above for the 954-character measurement, and it is the endpoint for the ADR-0005 #8 `schema_drift` hash check.

## Next step
- `/design` (Soneca): SPIKE-01 re-decision ADR (Langfuse+Epic-E form vs JSON-for-MVP vs Opik), update ASM-05 (→ partially-resolved) and ADR-0001 status; fold risks 1–5 into ADR-0001/0002 and DATA-MODEL-01.
- Atchim review of the verdict + ADR update (S-01.5 DoD).
- Cleanup (Langfuse UI only; the API cannot delete datasets): delete `spike-01-tmp-upsert`, `spike-01-bisect`, `spike-01-p-pattern`, `spike-01-p-ifthen_typed`, `spike-01-p-propertyNames_typed` and `spike-01-p-propertyNames_untyped`, `spike-01-promptkeys`. `spike-01-form-mode`, `spike-01-no-schema` and `spike-01-patterns` can stay as reference.
- Discard the `spike/langfuse-form-mode` branch (or keep it as reference only — never merge).
