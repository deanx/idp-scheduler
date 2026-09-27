# Data model — UC-01 (initial proposal)

No prior data-model file exists in `docs/init/`; this is the **initial** data model (the delta from nothing). It covers the in-app contracts and the platform-side schemas UC-01 produces/consumes. **PII rule:** `document_id` + expected fields only; document files are never stored on the platform (`## Domain`, BR4, INV-01).

## 1. Golden (expected output) — the curated reference

Lives on the evaluation platform as the `expected_output` of a dataset item. Stored behind API-key auth; contents are sensitive (BR5, INV-02). Normalised from the seed's top-level `line_items` to a keyed `tables` map so golden and actual (`NormalizedOutput`, ADR-0002) share one shape.

```json
{
  "document_id": "invoice-007.pdf",
  "fields": {
    "invoice_number": { "value": "INV-1",   "type": "id",     "critical": true },
    "invoice_date":   { "value": "2024-03-15", "type": "date",   "critical": true, "format_critical": true },
    "total":          { "value": "1250.00", "type": "number", "critical": true }
  },
  "tables": {
    "line_items": {
      "match_key": "description",
      "critical": true,
      "columns": { "qty": "number", "unit_price": "number" },
      "rows": [
        { "description": "Widget A", "qty": "10", "unit_price": "50.00" }
      ]
    }
  },
  "prompts": {
    "What is the vendor name?": { "answer": "Acme Corp", "critical": false }
  }
}
```

- `document_id` — opaque string the adapter resolves to a local file path at run time. The file is **not** stored on the platform.
- `fields.<name>.value` — the expected value (string; canonicalisation is the classifier's job).
- `fields.<name>.type` — one of `number`, `date`, `id`, `text` (drives canonical comparison, ADR-0003).
- `fields.<name>.critical` — bool; if `true`, a `missing`/`wrong_value` verdict fails the gate (ADR-0003, BR2).
- `fields.<name>.format_critical` — bool, **optional, default `false`** (added 2026-09-24, DEBT-80). If `true`, a `wrong_format` verdict on this field fails the gate as well, the declared exception to BR3's "format is informational". Use it where the *format* is itself the contract — e.g. an `invoice_date` whose extraction prompt is required to emit ISO-8601. It is independent of `critical`: neither implies the other, and they gate different verdicts. **Fields only** for now; `tables`/`prompts` have no equivalent. **Migration:** golden items that opt in do change, and the committed schema's canonical hash changes either way, so the dataset's `expectedOutputSchema` must be re-provisioned in the same change (ADR-0005 #8) or every run aborts `schema_drift`.
- `tables.<name>.match_key` — the column used to pair actual rows to golden rows (BR8). Required for any table block.
- `tables.<name>.critical` — bool; if `true`, a `missing` row or `wrong_value` column fails the gate.
- `tables.<name>.columns` — **optional** `{column_name: type}` map, types `number|date|id|text` (added 2026-09-24, ADR-0003 Amendment A1, closes DEBT-04). Drives the classifier's per-column canonical comparison. **Absent, or a column absent from the map, means `text`** — byte-identical to the pre-amendment behaviour, so every existing golden stays valid and compares identically. Two fail-closed rules (`MalformedGoldenError`, abort `malformed_golden`): a key that is **not a column of any golden row** in that table is rejected (a typo must be loud, not an inert no-op that quietly restores the false FAIL), and a key equal to that table's **`match_key`** is rejected (row pairing uses `match_key_form`, so typing the key column would declare nothing). **The map drives comparison only — it adds NO per-type `value` pattern to cells.** The asymmetry with flat fields is deliberate: cell patterns would invalidate goldens storing `"$50.00"` and turn an additive change into an expand/contract migration, and the classifier canonicalizes anyway.
- `prompts.<key>` — the IDP `prompt` string **verbatim**: 1–200 chars, no control characters (ADR-0002 amendment 2026-09-19, ADR-0005 F10). The `[A-Za-z0-9_\-]` charset applies to field and table names only.
- `prompts.<key>.answer` — expected free-form prompt answer, a **plain string** (not the spike's `answer.value` object); `critical` optional.
- Presence: `fields` is required and must be non-empty; `tables` and `prompts` are optional; `document_id` is optional (it duplicates the item `input`). No other top-level keys are allowed. (Schema review 2026-09-19, C3.)

> **Schema limits (ADR-0005, 2026-09-19; guarded by CT-05):**
> - Per-type patterns apply to `fields.<name>.value` only. `number` = `^-?[0-9]+(\.[0-9]+)?$`, which forbids currency symbols and thousands separators (store `"1250.00"`, not `"$1,250.00"`). `date` = ISO `YYYY-MM-DD` and is **syntactic only** (`"2024-02-31"` passes).
> - Table cells and prompt answers are string-only (any string passes). **Amended 2026-09-24 (ADR-0003 A1, DEBT-04): they stay string-only — per-column `type` now exists but drives *comparison*, not *validation*.** The `columns` map is declared as `{"type":"object","propertyNames":{"type":"string","pattern":"^[A-Za-z0-9_-]{1,128}$"},"additionalProperties":{"type":"string","enum":["number","date","id","text"]}}` inside `tables.additionalProperties.properties`, beside `match_key`/`critical`/`rows` (the table entry keeps `additionalProperties: false`, so it must be declared to be accepted). Ajv `strict:true` clean: no new `if`/`then` (CT-05 rule 1), no new `required` key (rule 2), +175 minified chars → schema **2,073** chars (measured 2026-09-24), far under the 10,000 cap (rule 3). **Migration: the golden item itself does not change** (the key is optional); what changes is the committed schema's canonical hash, so the dataset's `expectedOutputSchema` must be **re-provisioned in the same change that commits the schema**, or every run aborts `schema_drift` (ADR-0005 #8). `idp-regression-seed001` needs no re-curation and the Curator is asked for nothing.
> - The platform's raw JSON editor collapses a duplicate key to one entry (last wins) without warning, so key uniqueness in a golden is JSON-object semantics, not a guarantee the Curator sees.
> - **F2 measurement (S-01.3, 2026-09-19):** committed schema `src/idp_regression/platform/schema/golden_schema_v1.json` (version `v1`), CT-05-guarded. Minified full-schema length = **1,898 (after the C1–C3 schema changes, 17a4f64; was 1,641) chars** (`fields`+`tables`+`prompts` blocks), well under the 10,000-char Langfuse cap; `fields`-only block = 961 chars, `tables`-only block = 298 chars — consistent with the spike's ~954-char measurement for the fields+prompts block alone (Addendum 2).
>
> **ADR-0005 note (2026-09-19, Atchim APPROVE WITH NOTES; F1 resolved by SPIKE Addendum 2):** shape above is **kept**. `value`s stay **strings** (the S-01.1 classifier canonicalizes `str`; JSON numbers would drop formatting `1150.00`→`1150`). Server-side type checks come from a committed generic `expectedOutputSchema` using per-`type` string **patterns** (not JSON numbers). `prompts` stays a **keyed map** (not the spike's array). Table cells stay flat strings. Pattern/`if`-`then` enforcement is live-verified (F1 resolved). The committed schema file reference is folded in here when S-01.3 commits it (F2/F5).

### Schema review (Soneca, 2026-09-19)

Reviewed `src/idp_regression/platform/schema/golden_schema_v1.json` against ADR-0005 Decisions #1–#3 and #9, the DEBT-18 option B rule (CLAUDE.md ## Domain), this §1, CT-05, and the ADR-0002 amendment of 2026-09-19. (Closes QA-01-S-01.3 F-1, SPEC-01 S-01.3 DoD line 4.)

**Verdict: CHANGES REQUIRED.** The fix is one small schema edit covering C1–C3 below; there is no redesign. The schema is **APPROVED** once exactly C1–C3 land with the CT-05 assertions listed below, and that diff needs no further review.

Why fix it now: nothing real is loaded yet. Tightening the schema after real goldens exist is an expand/contract migration (ADR-0005 #4). Right now it costs one hash bump and a re-provision of the synthetic datasets.

**Required changes (all in one edit, one hash bump):**
- **C1. Add a name charset for field and table names.** Add `"propertyNames": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}` to the `fields` object and to the `tables` object.
  - Why: a golden field name becomes a score key `field:<name>`. A golden-only field still gets scored (as `missing`).
  - Nothing validates golden names today. `gate.py:44` only checks that a name is non-empty, and the `normalize()` charset (ADR-0002) guards the *actual* side only, which S-01.2 has not built yet.
  - Result: `"a:b"` or `"x\ngate"` in a golden breaks INV-03.
  - S-01.2 must pin the same `{1,128}` cap in `normalize()`.
- **C2. Close the entry objects.** Add `"additionalProperties": false` to the field-entry, table-entry and prompt-entry objects. Leave the `rows` items open; they are column maps.
  - Why: a typo such as `"critcal": true` is accepted today. `critical` then defaults to `false`, so a regression in a critical field **passes the gate** (a false PASS).
  - This does not affect CT-05 rules (1)–(2).
- **C3. Match what the classifier accepts.** Add `"minProperties": 1` to `fields` and `"minLength": 1` to `match_key`.
  - Why: the schema accepts `fields: {}` and `match_key: ""`, but the classifier rejects both (`gate.py:41`, `gate.py:65`). Today the Curator's save succeeds and the next run aborts with `malformed_golden`.
  - The "Presence" bullet above now documents this.
- **CT-05 test update:** assert C1–C3 by walking the schema. Re-measure the F2 length; it will stay well under 10k.

**Notes (no change needed):**
- **N1. Field-type patterns.** Each pattern matches ADR-0005 #1 exactly. Every `if`/`then` pair declares `type`, as CT-05 requires.
  - `date` is syntactic only: `2024-02-31` passes.
  - `number` accepts leading zeros and `-0`. That is fine because the classifier canonicalizes.
  - `text` allows `""`. This is intentional: an empty string is distinct from a missing value.
- **N2. `$` differs between validators.** In Ajv (the server, which is authoritative) `$` matches only at the true end of the string. In Python `jsonschema`, which uses `re.search`, `$` also matches before a trailing `\n`. So local validation of `"1250.00\n"` is looser than the server's. Never use local validation as the golden gate.
- **N3. Tables** follow Decision #2. Cells are string-only — **still true after the 2026-09-24 `columns` amendment**, which types comparison, not validation (DEBT-04 closed by ADR-0003 A1). The rule "`match_key` must be a column" cannot be expressed in the schema, so it stays the classifier's job — as do the two `columns` validation rules. **Column names still never reach score keys**: the 2026-09-24 table score is the per-table roll-up `table:<name>`, not `table:<name>:<column>` (ADR-0005 A4; comments remain `None`).
- **N4. Prompts.** `propertyNames` matches the ADR-0002 amendment verbatim. `answer` is a plain string.
- **N5. Top-level keys.** Top-level `additionalProperties: false` is correct. The optional `document_id` duplicates the item's `input`; it is harmless.
- **N6. Draft and version.** Draft-07 is live-verified. The version lives only in the filename and `title`; the schema's identity is the canonical hash (Decision #8). Do not add `$id`: Ajv's per-instance id collisions are untested on Langfuse.
- **N7. DEBT-18 option B.** The schema describes only the golden in the dataset item. Nothing in it implies copying values elsewhere, which is consistent with option B.

## 2. NormalizedOutput (actual) — what `normalize()` emits (ADR-0002)

In-app only; not persisted to the platform as a blob (its *verdicts* are, via scores). Confidence is carried through but **ignored by the gate** (UC-01 Hand-off).

```json
{
  "status": "SUCCEEDED",
  "fields": {
    "invoice_number": { "value": "INV-1",        "confidence": 0.99 },
    "invoice_date":   { "value": "March 15, 2024", "confidence": 0.90 },
    "total":          { "value": "$1,250.00",     "confidence": 0.80 }
  },
  "tables": {
    "line_items": [
      { "description": { "value": "Widget A", "confidence": 0.95 },
        "qty":         { "value": "10",        "confidence": 0.95 },
        "unit_price":  { "value": "50.00",     "confidence": 0.95 } }
    ]
  },
  "prompts": {
    "What is the vendor name?": { "answer": "Acme Corp", "confidence": 0.88, "source": "..." }
  }
}
```

- `status` — the raw IDP terminal status string; one of `IDP_SUCCESS_STATUSES` when `extract()` returns (ADR-0002).
- `fields.<name>.confidence` — `float | None`; used locally by the classifier/orchestrator. Per DEBT-18 option B it is never written to the platform; the gate ignores it.
- `tables.<name>` — array of rows; each row is a `{column: {value, confidence}}` map. Rows are matched to golden rows by the golden's `match_key`.
- `prompts.<key>.source` — passthrough from IDP; not compared.

## 3. Verdicts (classifier output) — ADR-0003

Per-field map; one entry per (golden field ∪ actual field ∪ table ∪ prompt key).

```json
{
  "invoice_number": { "verdict": "match",        "expected": "INV-1", "actual": "INV-1", "confidence": 0.99, "critical": true,  "format_critical": false, "type": "id" },
  "total":          { "verdict": "wrong_value",  "expected": "1250.00", "actual": "1150.00", "confidence": 0.80, "critical": true, "format_critical": false, "type": "number" },
  "discount":       { "verdict": "new_field",    "expected": null, "actual": "5.00", "confidence": 0.70, "critical": false, "type": null },
  "line_items":     { "verdict": "detail", "critical": true,
                      "rows": [ { "match_key": "Widget A", "column": "unit_price", "verdict": "match" } ] }
}
```

In-app only: `expected`/`actual`/`confidence` never leave the process. Only the `verdict` literal (and the gate) is written, as a score value (DEBT-18 option B, 2026-09-19).

The verdicts: `match`, `missing`, `wrong_value`, `wrong_format`, `new_field`, `new_line` (glossary), and — **added 2026-09-24, ADR-0003 Amendment A2 (DEBT-05), a versioned seventh literal** — `new_table`. `new_field`/`new_line`/`new_table` are always `critical: false` and never fail the gate.

**`new_table`** — a table present in `actual.tables` with no golden counterpart. It is a **flat `Verdict`**, not a `TableVerdict` container (`TableVerdict.verdict` stays `Literal["detail"]` and keeps carrying `rows[]`):

```json
"shipping_charges": { "verdict": "new_table", "expected": null, "actual": null,
                      "confidence": null, "critical": false, "type": null }
```

`actual` is `null` deliberately — no cell content enters the entry for a table nobody expected; the row detail lives in ADR-0007's local `0600` run artifact. `overall_gate` needs no change (`_VALID_VERDICTS` derives from the Literal). **`new_table` is never written to the platform** (§4) — actual-only keys have never been scored; `new_field` is not either.

## 4. Run / score schema (on the evaluation platform) — ADR-0001

The platform persists, per named run:

- One **`field:<name>`** score per field per document (value = verdict string; comment carries **no values**: no expected, actual or confidence. It is `None` or value-free metadata only, per DEBT-18 option B, user decision 2026-09-19). These keys are the **stable contract** the remediation UI reads (BR11, INV-03).
- One **`table:<name>`** score per **golden-declared** table per document (added 2026-09-24, ADR-0005 Amendment A4, closes DEBT-14). Value = the **worst row-verdict in the block**, one verdict literal, under the fixed precedence `missing` > `wrong_value` > `wrong_format` > `new_line` > `match`; an empty golden `rows: []` → `match`, so the score never disagrees with the gate; an unrecognised row verdict **raises** rather than ranking lowest (the fail-open this roll-up invites — derive the precedence from `VerdictLiteral`). `dataType` `CATEGORICAL`; `id` = the existing `score_id(run_id|document_id|"table:<name>")`; **`comment: None` — no cell value, no column name, no `match_key`, no confidence, no row count (DEBT-18 option B: verdicts only).** The name is safe under INV-03 because golden table names already carry the §1 C1 charset. **Actual-only tables (`new_table`) are NOT scored**, so no IDP-controlled name can become a score key. Per-column (`table:<name>:<column>`) and per-cell shapes were considered and rejected — per-cell puts a `match_key` **value** into an immutable score name; per-column needs a column-name charset guard that does not exist yet. Diagnosis lives in ADR-0007's local artifact; the platform only has to make the regression **visible**.
- One **`gate`** score per document (value = `PASS` | `FAIL`).
- Run **metadata**: `action_id` (the IDP action exercised), `action_version` (the regression variable, BR1), `golden_version` (app-tracked for Langfuse, ADR-0001 — resolves ASM-03, INV-04) and, from 2026-09-21, **`golden_dataset_name`** (which named golden set the run was measured against — ADR-0004 Amendment A6, §5 note).

> **ADR-0005 follow-up (2026-09-19, Atchim APPROVE WITH NOTES):** platform stays Langfuse (ADR-0005 supersedes ADR-0001). Score writes carry a deterministic client `id` = `uuid5(NAMESPACE, run_id|document_id|score_name)`, where `NAMESPACE` is a committed, pinned UUID constant (`run_id` unique per invocation → retry-safe, no cross-run overwrite, N26). Langfuse v4 `events_only`: traces/run linkage via OTLP/v4 SDK, scores via `/api/public/scores`, reads via `/v3/scores`. `golden_version` stays the app-tracked content hash. Prompt-derived score names use a charset-safe derived id `prompt:<16-hex sha256 of the prompt key>`, never the raw prompt (ADR-0002 amendment, INV-03). At run start the dataset's `expectedOutputSchema` hash must equal the committed schema's hash or the run aborts `schema_drift` (ADR-0005 Decision #8). To be folded in after S-01.3 confirms (ADR-0005 F3/F5).
>
> **S-01.3 confirmation (2026-09-19, live-probed against Langfuse 4.38.0):** `POST /api/public/scores` requires exactly one of `traceId`/`sessionId`/`datasetRunId` on every score (a 400 without one — DEBT-16) and an explicit `dataType` (all scores here are `"CATEGORICAL"`); every score write carries a deterministic per-document `traceId = uuid5(NAMESPACE, run_id|document_id).hex` (`mark_run_status` uses a pinned `RUN_LEVEL_TRACE_SENTINEL` document_id) — a score may target a trace that was never ingested. Trace + dataset-run linkage (ADR-0005 #6/F3) is via the `langfuse` Python SDK's `Langfuse.run_experiment(...)`, the only mechanism confirmed to make a run appear under the dataset's Experiments tab (`GET /api/public/experiments`) — a manual OTel span + `POST /api/public/dataset-run-items` link does NOT achieve UI visibility (probed and rejected, see `src/idp_regression/platform/tracing.py`). `Langfuse.flush()` never raises on export failure (only logs); `record_experiment()` (`platform/tracing.py`) attaches a `_FailureWatcher` to the OTLP exporter's own logger as the sole observable failure signal and raises `FlushFailedError` (TP-43).

Mapping (from `requirements-spec.md`):
- Dataset item `input` = `document_id` (opaque); `expected_output` = the Golden above.
- Score name `field:<name>` — value is the verdict. *(Amended 2026-09-19, DEBT-18 option B: every score comment is `None`. Amended 2026-09-24, ADR-0005 A4: a table block now gets **one roll-up `table:<name>` score carrying a verdict literal only** — no row-level detail block, column name, `match_key` or cell value reaches the platform.)*
- Score name `gate` — value is the aggregate gate.

## 5. Config (env/secrets) — never stored on the platform

| Env var | Purpose | Source |
|---|---|---|
| `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET` | OAuth client credentials | secret |
| `IDP_REGION`, `IDP_ORG_ID` | IDP endpoint scoping | env |
| ~~`IDP_ACTION_ID`~~ | **Test-harness convenience only (ADR-0004 A8, 2026-09-22).** No longer a default for `--action`, which is now **required on the CLI with no env fallback**. Read by **no production code path**; may be read by integration tests / local wrappers to compose a command line | not config — test fixture |
| `IDP_TERMINAL_STATUSES` | Configurable terminal-status allowlist (ASM-01) | env, default `SUCCEEDED` |
| `IDP_SUCCESS_STATUSES` | Terminal-success subset (others = hard failure) | env, default `SUCCEEDED` |
| `IDP_EXECUTION_TIMEOUT_SECONDS` | Per-document poll timeout | env |
| `PLATFORM` | `langfuse` (chosen, ADR-0001) | env |
| `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` | Platform auth | secret |
| ~~`GOLDEN_DATASET_NAME`~~ | **Test-harness convenience only (ADR-0004 A8, 2026-09-22 — supersedes A6's env-fallback clause).** The golden-set pointer comes from **`--dataset`, required on the CLI, no env fallback**; missing/empty → clear message naming the **flag**, non-zero exit, zero network calls (N6). Read by **no production code path**. | not config — test fixture |
| `IDP_TEST_*` (e.g. `IDP_TEST_DOCUMENT_PATH`) | **Test-harness convenience only (A8)** — integration-test fixtures; read by no production code path | not config — test fixture |
| `IDP_DOCUMENT_DIR` | Local dir holding the documents under test; `document_id` is resolved under it with realpath containment (abort `path_containment_violation` on escape — ADR-0004 Amendment A2) | env, no default |

No credential is ever logged (BR6, INV-02). `load_dotenv()` is the first line of any script before any SDK client is constructed.

> **Run identity vs environment description (2026-09-22, ADR-0004 Amendment A8 — user decision).** This table describes **the machine and the account** only. Everything that defines **what a run measured** — `--action`, `--version`, `--dataset`, `--run` — is a **required CLI parameter with no env fallback**, so the full invocation lives in the CI workflow file and a change to what is being measured shows up in the PR diff. A8 supersedes A6's `GOLDEN_DATASET_NAME` fallback clause and the 2026-09-19 `--action`→`IDP_ACTION_ID` default; the three struck rows above survive as test fixtures, read by no production code path.

> **Golden-set pointer note (2026-09-21, ADR-0004 Amendment A6 / DEBT-48).** `GOLDEN_DATASET_NAME` is config, not a secret, but it is the **highest-consequence config value in the system**: a pointer that silently forks between CI and local produces a *green run against the wrong dataset*, the worst failure this product can produce and one no test here can detect. Hence three rules: (1) no default anywhere, fail-closed before any network call — **hardened 2026-09-22 (A8): `--dataset` is required and `GOLDEN_DATASET_NAME` is read by no production code path at all**; (2) CI must pass it explicitly as `--dataset` so a change to it is visible in the PR diff, never only in a runner's environment; (3) **every run records which dataset it used** — `RunMetadata` carries a fourth field `golden_dataset_name` alongside `action_id`/`action_version`/`golden_version` (§4), on both `record_run` and `mark_run_status`, and the run-start log line repeats it. `golden_version` is a content hash: it proves *what* was compared, never *which named set* it came from. **INV-04 widens from three fields to four.**

## Notes

- **ASM-04 (partially resolved 2026-09-24; Low for UC-01 / Med for Epic F):** the response contract can vary by action definition & API version, and `normalize()` is the single seam that absorbs this (ADR-0002). **Corrected:** the `pages[]` shape this line previously called "confirmed" was never confirmed — it came from a hand-authored reading of an early sample. Live responses carry `fields`/`tables` at the **top level** with **no `pages` key**, verified against a 3-page document (`tests/fixtures/live/inv-004-multipage.raw.json`). The `prompts` shape remains unverified (DEBT-69(a)): no action available to this project emits prompts. Multi-document-type routing (Epic F) will still need a spike before extending the golden/normalized contract.
- **Confidence** is a first-class field in `NormalizedOutput` and the verdict so the remediation UI (Epic E) and a future review-queue gate can use it; the UC-01 gate itself ignores it.

### D2 (user decision 2026-09-25) — per-column `type`, per-field `date_format`

**Table columns carry a declared type.** A table spec may hold
`types: {<column>: number|date|id|text}`, and a declared column canonicalizes
exactly as a field of that type. Before this, `_TABLE_COLUMN_TYPE` was hard-coded to
`text` because this document carried no per-column type (DEBT-04), so a `unit_price`
of `1,250.00` against a golden `1250.00` — the same number with a thousands separator
— read as a difference, and a real regression on that column could not be told from a
formatting change.

**The default stays `text`, and that is load-bearing.** Every golden written before D2
has no `types` key, and those goldens must keep meaning exactly what they meant.
Adopting this may not silently re-verdict an existing corpus.

**Fields may declare `date_format`** (a `strptime` pattern). `canonical.py` tries
`%m/%d/%Y` before `%d/%m/%Y`, so any `NN/NN/YYYY` whose day is 12 or less is read
American-first — silently (DEBT-81). A declared format resolves *which* calendar date
the golden's own value denotes.

It applies to the **format tier**, not the value tier: two spellings of one date remain
a `wrong_format` (ADR-0003's rule), and declaring a format decides which date is meant,
never whether the notation changed.

**Where it is valid (amended 2026-09-27, DEBT-103).** `date_format` is honoured on a
`type: "date"` field only, and such a field may hold its value **as the document writes
it** (`03/04/2024` with `%d/%m/%Y`). The ISO rule applies to a `date` WITHOUT a declared
format. Before this, the schema forced every `date` value to be ISO, and on a `text`
field `date_format` was accepted and ignored, so the golden D2b was designed for could not
be written validly at all. Both the committed schema and `_validate_golden` now refuse:
a `date_format` on a non-date field; a blank one; and one the golden's own value does not
parse under. The schema edit changes its hash, so an existing dataset aborts
`schema_drift` until re-provisioned (`provision_golden_dataset.py`, no quota).

**Undeclared remains a guess, and is recorded as one.** The default is pinned by
`tests/classifier/test_date_format.py` so that changing it requires changing a test and
saying why, and `scripts/draft_golden.py` flags every genuinely ambiguous slash-date in
its review worklist. Drafting **never infers** a `date_format`: a corpus of `01/02/2024`
through `05/06/2024` is indistinguishable under both readings, so a guessed format would
be wrong about half the time *and look declared* — worse than an undeclared default,
which at least reads as the known blind spot it is.
