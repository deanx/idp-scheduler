# ADR-0003: Classifier & gate design

**Status:** Proposed — **amended 2026-09-24: A1 (per-column table types, DEBT-04) and A2 (seventh verdict `new_table`, DEBT-05), both at the end of this file.**
**Date:** 2026-09-17
**Context (use case):** UC-01 (Run a baseline regression over a golden set)
**Risk:** Low — the classifier is a pure function with no external dependencies; it is fully unit-testable and the reversal cost of changing verdict logic is low (a test change, not a data migration).

## Context

The classifier is the CI gate. Given a golden (`expected`) and a `NormalizedOutput` (`actual`, from ADR-0002), it produces a per-field verdict map and an aggregate gate. It is **pure**: no IDP, no platform, no I/O. The seed (`design-inputs.md` ADR-2) and the spike (11/11 tests) already prove this is the safe place to start.

Requirements that shape it:
- **Six verdicts** (BR, glossary): `match`, `missing`, `wrong_value`, `wrong_format`, `new_field`, `new_line`.
- **Type-aware canonical comparison** (F4) per field type: `number`, `date`, `id`, `text`.
- **Format-vs-value distinction** (F5, AC4): a looser alphanumeric normalization tier separates `wrong_format` (same content, different formatting — `INV 001` vs `INV-001`, `March 15, 2024` vs `2024-03-15`) from `wrong_value` (genuinely different content).
- **Line items matched by `match_key`, not position** (F6, BR8) — row reordering is not a diff. Unmatched actual rows are `new_line`.
- **`new_field` / `new_line` are informational, never failures** (F7, BR3) — extending a prompt to extract extra fields must not turn a baseline red.
- **Criticality-based gate** (F8, BR2) — `FAIL` only on a `missing` or `wrong_value` where the field (or line-item block) is `critical: true` in the golden. `wrong_format`, `new_field`, `new_line`, and any non-critical difference do not fail the gate.
- **Confidence is carried into the verdict/score but does not affect the gate** (UC-01 Hand-off; ADR-0002) — the remediation UI uses it; the gate ignores it.

## Threat model

No external trust boundary (the classifier is pure). The one threat is **trusted-input assumption**: the classifier must not assume the golden or `actual` is well-formed. Mitigation: validate shapes at the entry of `classify()` and raise a typed `ClassifierError` on malformed input rather than silently emitting a wrong verdict — a malformed golden is a Curator error and should be loud, not a silent `match`.

## Options considered

### Option A — One monolithic `classify()` that returns verdicts + gate together
**Pros:** one call.
**Cons:** the gate depends on verdicts but is independently testable and independently useful (the same verdict map can be re-gated under different criticality rules later). Coupling them makes "re-gate under a different rule" a refactor.

### Option B — `classify(golden, actual) -> verdicts` then `overall_gate(verdicts) -> PASS|FAIL`
**Pros:** matches the seed (`design-inputs.md`); two pure functions, each independently unit-testable; the gate rule can evolve without touching verdict logic. This is the structure the spike already validated.
**Cons:** two functions instead of one — trivial cost.

## Decision

We choose **Option B**. Two pure functions, both in `src/idp_regression/classifier/`.

### `classify(golden, actual) -> dict[str, Verdict]`

```python
class Verdict(TypedDict):
    verdict: Literal["match", "missing", "wrong_value", "wrong_format", "new_field", "new_line"]
    expected: str | None
    actual: str | None
    confidence: float | None     # carried from actual; None for golden-only fields
    critical: bool               # echoed from the golden for the gate's convenience
    type: str | None             # field type, echoed for the score comment / remediation UI

def classify(golden: Golden, actual: NormalizedOutput) -> dict[str, Verdict]: ...
```

Verdict logic per field:
1. Field in golden, not in actual (or `actual.fields[name].value` is empty/None) → `missing`.
2. Field in actual, not in golden → `new_field` (never fails the gate).
3. Field in both → type-aware canonical comparison:
   - Equal under canonical form → `match`.
   - Not equal canonically, but equal under the looser alphanumeric/format tier → `wrong_format`.
   - Otherwise → `wrong_value`.

Per-type canonical forms:
- `number` — numeric compare (strip currency symbols, thousands separators; `1250.00` == `$1,250.00`).
- `date` — **value tier** = exact string compare; **format tier** = parse to a date, so `2024-03-15` vs `March 15, 2024` → `wrong_format` (same date, different format — AC4, EX-A1-4), NOT `match`.
- `id` — alphanumeric-strip comparison (whitespace/punctuation-insensitive) is the *format* tier; exact string is the *value* tier.
- `text` — string compare; format tier is whitespace-normalised.

Line items (`golden.tables[name]` vs `actual.tables[name]`, with `match_key`):
- Each actual row is a `dict[str, FieldValue]` keyed by column name (post-revision ADR-0002 `tables: dict[str, list[dict[str, FieldValue]]]` shape). Pair rows by `match_key` value. Matched pairs are compared column-by-column using the same per-type logic; each column emits a sub-verdict.
- A golden row with no matching actual row → `missing` for that row (fails the gate if the table is `critical`).
- An actual row with no matching golden row → `new_line` (informational; never fails the gate).

`prompts` (if the golden declares them) compare `golden.prompts[key]` vs `actual.prompts[key].answer` the same way fields do.

### `overall_gate(verdicts) -> Literal["PASS", "FAIL"]`

```python
def overall_gate(verdicts: dict[str, Verdict]) -> Literal["PASS", "FAIL"]: ...
```

Rule: `FAIL` iff any verdict is `missing` or `wrong_value` AND its `critical` is `True`. `wrong_format`, `new_field`, `new_line`, and any non-critical difference are `PASS`. The gate is the **only** output that becomes the `gate` score on the platform (ADR-0001).

## Design patterns

- **Strategy (injectable)** — per-type canonical comparison is a `dict[type, Canonicalizer]` where each `Canonicalizer` is a pair of pure functions `(canonical_form, format_form)`. Idiomatic: a module-level registry dict + small functions, not a class hierarchy. Adding a new type is "add a function and register it."
- **No pattern for the gate** — `overall_gate` is a single pure function over a dict. Ad-hoc; no pattern applies because there's no variation to strategy over. Forcing one would be the smell.

## API contract (observable behaviors — Hyrum's Law)

- The verdict keys are exactly the union of (golden field names ∪ actual field names ∪ table names ∪ prompt keys). Consumers may iterate verdicts and assume every golden-declared field has an entry.
- `Verdict.critical` is always echoed from the golden; for `new_field`/`new_line` (no golden entry) it is `False` by definition.
- `overall_gate` is a pure function of `verdicts`; consumers may re-run it on a stored verdict map and get the same result. The remediation UI may re-gate under hypothetical criticality rules without re-classifying.
- Verdict string values are the six literals in the glossary — adding a seventh is a versioned change (score consumers must handle it).

Versioning strategy: the verdict enum and `Verdict` TypedDict are versioned by Python type; a new verdict literal or shape change requires a new ADR and a score-consumer migration.

## Consequences

- **Positive:** the CI gate is a pure function — fully unit-testable, no mocks, the spike already proves it (11 tests). The gate rule can evolve without touching verdict logic. Adding a field type is one function + one registration.
- **Negative:** type-aware canonicalization has a long tail of edge cases (date locales, number formats, id separators). Mitigation: the test suite is the regression gate; every edge case becomes a unit test.
- **Follow-up work:**
  - Dengoso implements against this contract; the test suite (the spike's 11 + new cases) is the CI gate.
  - Contract tests pin `classify()` / `overall_gate()` against `NormalizedOutput` fixtures.

## Reversal cost

**Low.** Changing verdict logic is a test change, not a data migration — historical `field:<name>` scores stay valid because the *verdict names* are the stable contract (the six literals). Adding a seventh verdict is a versioned change with a score-consumer migration, but that is a deliberate extension, not a reversal. The classifier is the safest component to change.

Atchim review: not required (risk Low) — Zangado audits the test suite at `/qa`.
---

## Amendment A1 — per-column types for table blocks (DEBT-04), 2026-09-24

**Status:** Accepted (Soneca, design pass). **Versioned change: golden schema v1 → v1.1 (additive), classifier contract unchanged in shape.**

### The defect this closes

`gate.py`'s `_TABLE_COLUMN_TYPE = "text"` is consumed unconditionally, because the golden table
block carries no per-column type. A numeric column compared as text makes `"65.00"` vs `"65.0"` a
`wrong_value` (it survives neither the value tier nor the whitespace-only format tier), and if the
table is `critical: true` that is a **false FAIL**. A gate that fails on a correct extraction is
the second-worst failure this product can produce, after a false PASS — the difference is that the
false FAIL is *visible*, so it burns trust instead of hiding a regression. That is enough to act.

### Options considered

- **A — leave it text-only.** Zero cost today; the false FAIL lands the first time a golden carries
  a numeric or date column whose extraction formats differently. Rejected: the case is not
  hypothetical, table cells are exactly where currency and quantity live.
- **B — infer the type from the golden cell values** (e.g. all cells in a column match the number
  pattern → treat as `number`). Zero Curator burden, and **rejected outright**: an id column of
  digits (`"0012"`) would be inferred `number` and `"12"` would then **match**, i.e. a data-shape
  dependent **false PASS**. This project has produced five fail-opens; we do not add a sixth by
  guessing.
- **C — an optional per-column `type` map, Curator-declared.** Chosen.

### Decision — Option C

`golden.tables.<name>` gains an **optional** `columns` map:

```json
"tables": {
  "line_items": {
    "match_key": "description",
    "critical": true,
    "columns": { "qty": "number", "unit_price": "number", "delivered_on": "date" },
    "rows": [ { "description": "Widget A", "qty": "10", "unit_price": "50.00" } ]
  }
}
```

- **Absent `columns`, or a column absent from the map → `text`.** Byte-identical to today's
  behaviour, so every existing golden stays valid and compares identically. This is the property
  that makes the change safe to land mid-flight.
- The classifier passes `columns.get(col, "text")` to `compare_value` instead of the constant.
  `_TABLE_COLUMN_TYPE` survives only as the **default**, and its name should say so.
- **The map drives comparison only — it does NOT add value patterns to cells.** Flat fields carry
  per-type `value` patterns in the schema; table cells deliberately do not. Adding them would
  invalidate any existing golden whose numeric cell is stored as `"$50.00"` and would turn one
  additive change into an expand/contract migration (ADR-0005 #4). The classifier canonicalizes
  anyway, so the patterns buy little here. **This asymmetry with flat fields is deliberate and
  recorded** (DATA-MODEL-01 §1), not an oversight.

### Two validation rules — both fail-closed, both because "silently ignored" is the enemy

The Curator edits this JSON by hand; every key added is a key they can mistype. A mistyped entry
must therefore be **loud**, not inert — an inert typo restores exactly the false FAIL this
amendment exists to remove, with the added insult that the golden *looks* fixed.

1. **A `columns` key that is not a column of any golden row in that table → `MalformedGoldenError`**
   (abort `malformed_golden`, CT-04). Catches `"unit_prce": "number"`.
2. **A `columns` key equal to the table's `match_key` → `MalformedGoldenError`.** Row pairing uses
   `match_key_form`, not type-aware comparison, so typing the key column would be a declaration
   that has no effect. One rule, one clear message, no silent no-op.

A type value outside `number|date|id|text` is rejected by the schema server-side and by
`_validate_golden` locally (same enum as flat fields — derive it, do not re-enumerate it).

### Schema shape (normative; the file lives under `src/`, so an implementer lands it)

Inside `tables.additionalProperties.properties`, beside `match_key` / `critical` / `rows`:

```json
"columns": {
  "type": "object",
  "propertyNames": { "type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$" },
  "additionalProperties": { "type": "string", "enum": ["number", "date", "id", "text"] }
}
```

Ajv `strict: true` cleanliness (CT-05): this adds **no** `if`/`then` (rule 1 unaffected), adds **no**
`required` key (rule 2 unaffected), and costs ~190 minified chars — the full schema goes from **1,898 to 2,073** minified chars (measured, not estimated), far under the 10,000 cap (rule 3 unaffected). The table-entry object keeps
`additionalProperties: false`, so `columns` must be declared to be accepted — which is the point.

### Migration for `idp-regression-seed001`

The **golden item does not change at all** — `columns` is optional and absent means `text`. What
changes is the **committed schema's canonical hash**, and ADR-0005 Decision #8 aborts a run
`schema_drift` when the dataset's `expectedOutputSchema` hash differs from the committed one. So the
migration is exactly one step: **re-provision the dataset's `expectedOutputSchema` with the v1.1
schema in the same change that commits it.** No golden is rewritten, no value is re-entered, and the
Curator is not asked to do anything. If the seed golden later *wants* numeric columns, that is a
separate, per-golden, additive edit.

### Reversal cost

**Low.** Delete the schema key, drop the argument, restore the constant — and re-provision the
schema hash again. The only residue is a golden that carries a `columns` map the schema no longer
accepts; because `additionalProperties: false` is on, that golden would then fail validation, so a
reversal must either strip the key from existing goldens or keep it accepted-and-ignored for one
release. Say so now rather than discover it.

---

## Amendment A2 — a seventh verdict: `new_table` (DEBT-05), 2026-09-24

**Status:** Accepted (Soneca, design pass). **Versioned change: `VerdictLiteral` gains a seventh
member. This is the score-consumer migration the original ADR's versioning clause anticipated.**

### The asymmetry

An actual field with no golden counterpart is `new_field`. An actual **table** with no golden
counterpart is *nothing* — no key, no verdict, no signal. That asymmetry is an artefact of the
classify loop iterating `golden.tables` only; it was never a decision. For a product whose whole
job is "did this prompt change make the extraction better or worse", an action that starts emitting
a table nobody expected is one of the most informative events there is, and today it is invisible.

### Decision

Add `new_table` to `VerdictLiteral` (seventh member). `classify()` gains a second loop over
`actual.tables.keys() - golden.tables.keys()`, emitting a **flat `Verdict`** (not a `TableVerdict`
container):

```json
"shipping_charges": { "verdict": "new_table", "expected": null, "actual": null,
                      "confidence": null, "critical": false, "type": null }
```

- **Flat, not a container.** `TableVerdict.verdict` is `Literal["detail"]` and carries `rows[]`;
  widening it to hold `new_table` would mean a row detail block for a table the golden never
  described, which is detail nobody asked for. The signal is "this table appeared". The full row
  content is already available locally in ADR-0007's `0600` run artifact, which is where the
  diagnosis happens.
- **`actual: null` deliberately** — no cell content enters the verdict entry for an unexpected
  table. Cheap, and it keeps the entry trivially safe under any future disclosure question.
- **Informational, never critical, never fails the gate.** `critical: false`, exactly like
  `new_field` (F7/BR3: extending an extraction must not turn a baseline red). A prompt that now
  emits a table is *usually* an improvement; making it a FAIL would punish the improvement and,
  worse, would make the gate's red mean two different things.
- `overall_gate` needs **no change**: `_VALID_VERDICTS` derives from the Literal, and `new_table` is
  not in `("missing", "wrong_value")`. The FO-5 fail-loud guard keeps working by construction —
  verify that with a test that the derivation is live, not that the new literal happens to pass.
- **`new_table` is NOT written to the platform.** See ADR-0005 Amendment A4 (2026-09-24): actual-only keys
  have never reached a score (`new_field` does not either — `build_score_inputs` iterates the
  *golden*), and keeping that rule means no actual-derived, IDP-controlled name can ever become a
  score key (INV-03) and N9's score-count formula stays bounded by the golden.

### What it buys / what it costs

Buys: the one class of extraction change the gate was structurally blind to becomes a visible,
non-failing signal in the verdict map and the local run artifact. Costs: a seventh literal — every
consumer doing an exhaustive match must handle it. Today that is `overall_gate` (derives),
`build_score_inputs` (never sees it), and the Epic E UI (not built). The cost is at its minimum
right now, which is the argument for doing it now rather than during Epic E.

### Reversal cost

**Low, but not zero.** Remove the literal and the loop. Residue: any stored verdict map (the
ADR-0007 local artifact) containing `new_table` would then fail a strict re-parse. As those
artifacts are per-run and gitignored, this is a non-event; noted for honesty.

---

## Implementation owed by A1/A2 and ADR-0005 A4 — proposed deltas for Dunga to card (2026-09-24)

**No DoD is edited here.** These are proposals; Dunga shapes and sizes them.

1. **Golden schema v1.1** — add the `columns` key to `src/idp_regression/platform/schema/golden_schema_v1.json`,
   extend the CT-05 walk (charset + enum, and re-measure the minified length), and **re-provision the
   dataset `expectedOutputSchema` in the same change** (otherwise every run aborts `schema_drift`).
2. **Classifier A1** — rename `_TABLE_COLUMN_TYPE` to say "default", pass `columns.get(col, "text")`
   to `compare_value`, and add the two `MalformedGoldenError` validations. Tests must pin the
   *defaulting* (absent map ⇒ byte-identical to today) and both rejections, each mutation-verified.
3. **Classifier A2** — seventh `VerdictLiteral` member, second loop over
   `actual.tables.keys() - golden.tables.keys()`. A test must prove `_VALID_VERDICTS` is **derived**
   (not that `new_table` happens to pass), and that `overall_gate` still returns `PASS` for it.
4. **Platform A4** — `build_score_inputs` emits `table:<name>` with the worst-of roll-up; the
   precedence table is derived from `VerdictLiteral` and an unranked literal **raises**. CT-03's
   count assertion moves to the formula `N_fields + N_prompts + N_golden_tables + 1`; INV-01's
   payload test gains the new score name (`comment` still `None`); INV-03 gains the fourth family
   and the "no score name is ever derived from `actual`" property.
5. **Sequencing:** (2) is independent. (3) must land before (4) touches the precedence derivation,
   and (1) must land with (2) or the new `columns` key is rejected by the server.

### Residual flagged, not fixed here (report, don't absorb)

**The verdict map is a single namespace over field names ∪ table names ∪ prompt keys, and nothing
prevents a collision.** A golden declaring a field `line_items` *and* a table `line_items` makes
`classify()` overwrite the field's verdict with the table's (the table loop runs last) — a critical
`wrong_value` on that field then vanishes and the gate can go green: a **fail-open**. It also feeds
`build_score_inputs` a `TableVerdict` where it expects a `Verdict`, so `field:line_items` would be
scored with the literal `"detail"`. This predates all three amendments and is **out of scope for
this design pass**; it needs its own DEBT row and a fail-closed fix (reject the collision in
`_validate_golden`). Recorded here so it is not lost.
