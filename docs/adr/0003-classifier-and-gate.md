# ADR-0003: Classifier & gate design

**Status:** Proposed
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