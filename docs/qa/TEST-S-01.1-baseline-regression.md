# /test stamp — SPEC-01 / S-01.1 (classifier & gate)

**Status:** ❌ FAILED — Wave C re-stamp #2 (DEBT-46). The `791c58e` fix closes F-1..F-8 as stated, but the gate found 1 Critical residual of the F-1 class: a `parse_spec`-valid rule still turns a gate FAIL into PASS on a `format_critical` field, `verify_monotone` cannot see it when the rule is scoped by `field_type`/`name_in`/`name_matches`/`kind: prompt`, and the `--classifier` load path never runs `verify_monotone` at all. Not stampable until G-1 is closed with RED-then-GREEN tests and a fresh instance re-gates.
**Source:** /test re-stamp gate (Atchim, fresh instance — audit + mutation matrix; no production code edited, no tests added)
**Files:** src/idp_regression/classifier/__init__.py, canonical.py, custom.py, gate.py, registry.py, scorers.py, scoring.py, types.py; src/idp_regression/orchestration/scorer_store.py (the spec load path, in scope because G-1 runs through it) — tests: tests/classifier/test_classify.py, test_classify_contract.py, test_custom_scorers.py, test_date_format.py, test_edge_matrix.py, test_gate.py, test_performance.py, test_registry.py, test_tables.py, test_validation.py
**Sequence:** git-verified (`git log --diff-filter=A`): all ten classifier test files pre-date or co-commit with their implementation per slice (ADR-0003 co-commit waiver, unchanged). The fix round `791c58e` co-commits 14 new/changed tests with the fix; **RED proven independently by this gate**: the same test files run against the pre-fix classifier sources (`4cb7cc8`, copied to a scratch `PYTHONPATH`) → **14 failed / 83 passed**, every failure a test the fix added or corrected. The three pinning tests for F-4/F-6/F-8 pass on both (they pin surviving behaviour, not a defect) — as intended.
**Date:** 2026-09-28
**Commit (reviewed code):** `c48c516` (HEAD, unchanged for the whole session; `git status` at start showed only `docs/state/STATE.json` modified). Working tree `shasum -a 256`-verified identical to HEAD for all eight classifier files after every mutant. Delta from the previous stamp `4cb7cc8..c48c516` on the classifier package + its tests = 7 files, +230/−37 (`791c58e` fix; `c48c516` docs-only).
**Author:** alex@divinocosta.com.br  <!-- solo mode — raw git config user.email -->
**Atchim TDD gate:** ❌ REQUEST CHANGES (G-1 Critical; G-2..G-6 non-blocking, listed for the fix round and DEBT)
**Independence:** ✅ structural (different models) — implementer Dengoso (Opus 5.5, `791c58e`) reviewed by Atchim (Fable 5.1), a **new fresh instance** (DEBT-44): the previous gate instance FAILED at `4cb7cc8`; the implementer fixed its findings in `791c58e`; this instance issued no verdict on either and was not the instance that wrote the previous stamp. SPEC-01 is `Risk level: high` → this stamp binds regardless of the `prototype` profile; the verdict path ran at `full` per `## Rigor` row 1.
**Static:** ✅ `.venv/bin/python -m pytest -q` → **1862 passed, 15 skipped** (integration opt-in) · bare `.venv/bin/mypy` → 0 issues / 150 files (strict) · `.venv/bin/ruff check src tests scripts` → clean. Secret scan: hook-gated, not re-run here (no new files). Never sourced `.env`; never called IDP or the platform.

## Falsifiable freshness check (DEBT-77 — an explicit FILE LIST, never a directory)

This stamp is fresh iff the following prints nothing:

```
git diff --stat c48c516 HEAD -- \
  src/idp_regression/classifier/__init__.py \
  src/idp_regression/classifier/canonical.py \
  src/idp_regression/classifier/custom.py \
  src/idp_regression/classifier/gate.py \
  src/idp_regression/classifier/registry.py \
  src/idp_regression/classifier/scorers.py \
  src/idp_regression/classifier/scoring.py \
  src/idp_regression/classifier/types.py \
  src/idp_regression/orchestration/scorer_store.py
```

A **new** module under `src/idp_regression/classifier/` is *outside* this gate until a stamp names it — surface it separately, as a scope-grew signal, with:

```
git diff --diff-filter=A --name-only c48c516 HEAD -- src/idp_regression/classifier/
```

(Both printed nothing at stamp time. Because this stamp is ❌, freshness is moot for `/qa` until a ✅ re-stamp re-pins both commands.)

## What `791c58e` closed — verified, not taken on trust

| Prior finding | Fix | Verified by |
|---|---|---|
| F-1 Critical — spec rewrites `wrong_value` → informational verdict | `ACTIONABLE_VERDICTS = (missing, wrong_value)`; `verify_monotone` compares gate outcomes; enumerator adds a format-only actual and `format_critical` | probes: all four prior repros now `SpecError`; mutants M01–M06 killed |
| F-2 High — `overall_gate` row site used 7-set | `_VALID_ROW_VERDICTS` at the row site | probe: `new_table` row in a critical block → `MalformedActualError`; M13 killed |
| F-3 Medium — `verdict_is: [new_table]` dead | `SCORER_VERDICTS` (6) for `verdict_is`; console vocabulary follows | M07 killed |
| F-4 Medium — date fallback untested | `test_a_declared_format_falls_back_for_an_actual_in_another_notation` | M27 killed |
| F-5 Medium — unknown column type degraded to text | `_validate_golden` refuses `types` outside `FIELD_TYPES` | M15, M16 killed |
| F-6 Low — `new_table` critical=False unpinned | `test_a_new_table_entry_never_gates` | M19 killed |
| F-7 Low — OR-vs-assign unobservable | not addressed (acknowledged) | M08 SURVIVED — carried as debt (G-4) |
| F-8 Low — `_row_affinity` ignores types | `test_row_pairing_uses_the_declared_column_type` | M17, M18 killed |

## Findings

**G-1 — CRITICAL — `classifier/custom.py` (`compile_spec` / `_enumerate_contexts`) + `orchestration/scorer_store.py::load_specs`: a `parse_spec`-valid rule still turns a gate FAIL into a PASS.** `ACTIONABLE_VERDICTS` now admits only `missing`/`wrong_value` — but those fail the gate only when `critical`, while a base `wrong_format` fails it when `format_critical`. So on a field with `critical: false, format_critical: true` (a legal golden; `format_critical` is DEBT-80's own opt-in and its docstring does not require `critical`), the natural-sounding rule *"treat a format difference as a value difference"* relaxes the gate. Reproduced at HEAD, no mutation:
```
golden invoice_date: type date, critical false, format_critical true, expected 2026-01-22 · actual 22/01/2026
  base regression gate: FAIL
  spec {when: {field_type: date, verdict_is: [wrong_format]}, then: {verdict: wrong_value}}
    parse_spec: accepted · verify_monotone: 0 problems · custom gate: PASS      <<< FAIL→PASS
golden bill_to: type text, format_critical true, expected "ACME Corp" · actual "ACME   Corp"
  spec {when: {field_type: text, verdict_is: [wrong_format]}, then: {verdict: wrong_value}}
    parse_spec: accepted · verify_monotone: 0 problems · custom gate: PASS      <<< FAIL→PASS
same rule scoped by name_in: ["amount"] or name_matches: "^amo": verify_monotone 0 problems (enumerator only ever uses name="total")
```
Three layers, each of which alone would have stopped it, all miss:
1. **Not monotone by construction.** `compile_spec` applies the verdict rewrite and keeps the flags as they were; nothing at runtime checks that the rewritten result still fails when the base did. The module docstring's guarantee ("can never turn a failure into anything the gate passes") is enforced only by a proof, not by the compiled scorer.
2. **The proof has blind spots it cannot close by enumeration.** `_enumerate_contexts` yields a format-only difference for `number`/`id` only (`"1.00"` vs `"1,00"`); for `date` and `text` it never produces a `wrong_format`, so any rule scoped `field_type: date|text` (or `kind: prompt`, which is always text) is unverifiable. It uses the single name `total`, so any `name_in`/`name_matches` rule is unverifiable. Names and notations are open sets — this is why layer 1 is the real fix, not a bigger enumerator.
3. **The `--classifier` path never runs `verify_monotone` at all.** `scorer_store.load_specs` → `parse_spec` → `build_classifier`; only `ui/api.py` calls `verify_monotone`. `.idp-regression-scorers/` is documented as *meant to be committed* and hand-editable, so the UNSCOPED variant — which the console does refuse (6 problems) — loads through the CLI with no check: reproduced via `register_custom_classifiers(dir)` → `resolve("date-format-is-value")` → base FAIL, custom PASS.

**Fix (all three):** (a) in `compile_spec`, after applying a rule: `if _fails_the_gate(ctx, outcome) and not _fails_the_gate(ctx, new): critical = True` — a verdict rewrite may never lose the gate, by construction; (b) `scorer_store.load_specs` runs `verify_monotone` and reports a non-empty result as a load error like any `SpecError` (so `custom_scorer_not_loaded` names it and a run naming that spec is refused pre-quota); (c) extend `_enumerate_contexts` with a date pair (`"2026-01-22"` vs `"22/01/2026"`) and a text pair (`"a b"` vs `"a  b"`) so the proof at least covers the shipped canonicalizers. RED tests: the two probes above in `test_custom_scorers.py` (asserting `gate == "FAIL"` **and** `verify_monotone` non-empty), plus a `tests/orchestration` test that a relaxing spec file is reported, not registered. **Regression-worthiness: PIN** (same class as the F-1 PIN: escalation-only guarantee measured against a predicate narrower than the gate's; second occurrence, which is itself the argument for enforcing it at compile time).

**G-2 — MEDIUM — `gate.py::_as_row_verdict` runtime guard still has no test (M14 SURVIVED, carried from F-2).** The previous stamp asked for two tests; only the `overall_gate` half landed. Defense in depth only now — `overall_gate` catches the row (M13 killed) and no spec can produce `new_table` — so non-blocking; but a Python scorer returning `new_table` for a `kind="table_column"` context should be pinned to `MalformedActualError` at classify time.

**G-3 — LOW — `then: {format_critical: true}` on `kind: table_column` is a silent no-op.** `RowVerdict` carries no `format_critical`, and `_classify_table` propagates only `cell_result.critical` to the block; the author believes format is gated on those cells and it is not. Refuse at parse (a `format_critical` action under a `kind: table_column` condition) or state it in the console vocabulary. Not a relaxation — the base outcome is unchanged.

**G-4 — LOW (DEBT, carried F-7) — `compile_spec` "OR-ed, never assigned" is unobservable (M08 SURVIVED)** while neither shipped base returns a `ScoreResult`. Pin with a stub base in tests or drop the comment's claim.

**G-5 — LOW — the number format tier is unpinned (M29 SURVIVED).** `_format_number` replaced by identity: `"1.250,00"` vs `"1250.00"` (European thousands) stops being `wrong_format` and becomes `wrong_value`, and nothing in `tests/classifier` notices. AC4 names four types; add the number `wrong_format` case to `test_edge_matrix.py`.

**G-6 — LOW — `overall_gate` accepts a non-bool `critical` on a caller-supplied map** (`{"verdict": "wrong_value", "critical": 0}` / `None` → PASS). `classify` always writes a bool and the artifact reader feeds JSON bools, so not reachable today; FO-5 validates the verdict word but not the flag. Either validate or note it as out of contract.

## Mutation matrix (36 own mutants; `tests/classifier` only, `-x`; every file restored byte-identically — `shasum -a 256 -c` OK; `PYTHONDONTWRITEBYTECODE=1`)

| # | Mutant (file · symbol · change) | Invariant | Result | Killed by |
|---|---|---|---|---|
| M01 | custom.py `ACTIONABLE_VERDICTS` + `new_field` | F-1 fix | KILLED | test_custom_scorers::test_a_rule_can_not_rewrite_a_failure_into_an_informational_verdict[new_field] |
| M02 | custom.py `ACTIONABLE_VERDICTS` + `wrong_format` | F-1 fix | KILLED | …[wrong_format] |
| M03 | custom.py `_fails_the_gate` format leg dropped | F-1 fix | KILLED | …test_verify_monotone_catches_relaxing_a_format_critical_field |
| M04 | custom.py `verify_monotone` gate-outcome check removed | F-1 fix | KILLED | TestMonotonicity::test_verify_monotone_catches_a_relaxation_the_key_check_would_miss |
| M05 | custom.py enumerator: no format-only actual | F-1 fix | KILLED | …catches_relaxing_a_format_critical_field |
| M06 | custom.py enumerator: `format_critical` never True | F-1 fix | KILLED | same |
| M07 | custom.py `verdict_is` accepts `new_table` | F-3 fix | KILLED | test_verdict_is_refuses_new_table_a_scorer_never_sees |
| M08 | custom.py `compile_spec` critical assigned not OR-ed | monotone | **SURVIVED** | — (G-4) |
| M09 | custom.py `_validate_action` flag may be False | escalation-only | KILLED | TestMonotonicity::test_a_spec_cannot_clear_an_escalation_flag[critical] |
| M10 | custom.py `confidence_below` fires on missing confidence | spec semantics | KILLED | TestCompilation::test_a_missing_confidence_is_not_below_the_floor |
| M11 | custom.py `verify_monotone` relax-to-match check removed | monotone | KILLED | …the_key_check_would_miss |
| M12 | custom.py `compile_spec` base scorer ignored | base runs first | KILLED | …test_the_goldens_critical_survives_a_rule_that_says_nothing_about_it |
| M13 | gate.py `overall_gate` row site back to 7-set | F-2 fix | KILLED | test_gate::test_a_new_table_row_inside_a_detail_block_is_refused |
| M14 | gate.py `_as_row_verdict` guard removed | Row ≠ new_table (runtime) | **SURVIVED** | — (G-2) |
| M15 | gate.py `_validate_golden` `types` check removed | F-5 fix | KILLED | test_gate::test_an_unknown_declared_column_type_is_refused |
| M16 | gate.py `types` check accepts any string | F-5 fix | KILLED | same |
| M17 | gate.py `_column_type` always text | D2a | KILLED | test_gate::test_row_pairing_uses_the_declared_column_type |
| M18 | gate.py `_row_affinity` ignores column types | F-8 fix | KILLED | same |
| M19 | gate.py `new_table` entry `critical=True` | F-6 fix | KILLED | test_gate::test_a_new_table_entry_never_gates |
| M20 | gate.py `format_critical` branch removed | DEBT-80 | KILLED | test_gate::test_wrong_format_fails_the_gate_when_the_field_is_format_critical |
| M21 | gate.py top-level unknown verdict → PASS | FO-5 | KILLED | test_gate::test_unknown_top_level_verdict_raises_instead_of_passing |
| M22 | gate.py non-critical block with `missing` row FAILs | BR2 | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| M23 | gate.py `_classify_field` critical REPLACES golden's | escalation-only | KILLED | test_classify::test_tp02_all_match_every_field_match |
| M24 | gate.py cell escalation not propagated | escalation-only | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| M25 | gate.py date_format validator never called | DEBT-103 | KILLED | test_date_format::…refuses_a_date_format_that_cannot_do_anything[spec0] |
| M26 | gate.py `_declared_date_format` always None | D2b | KILLED | test_date_format::test_a_declared_day_first_format_is_honoured |
| M27 | canonical.py `_format_date_with` no fallback | F-4 fix | KILLED | test_date_format::test_a_declared_format_falls_back_for_an_actual_in_another_notation |
| M28 | canonical.py `compare_value` ignores `date_format` | D2b | KILLED | …test_a_declared_day_first_format_is_honoured |
| M29 | canonical.py `_format_number` identity | AC4 number format tier | **SURVIVED** | — (G-5) |
| M30 | registry.py `resolve` unknown → default | registry contract | KILLED | test_registry::test_an_unknown_name_is_refused_and_names_the_alternatives |
| M31 | registry.py pinned-file row wired to `classify` | registry contract | KILLED | test_registry::test_pinned_file_calls_empty_against_empty_an_agreement |
| M32 | scorers.py pinned-file: empty actual always match | pinned-file rule | KILLED | test_registry::test_pinned_file_still_fails_a_genuinely_lost_value |
| M33 | scorers.py regression: empty/empty → match | regression byte-for-byte | KILLED | test_registry::test_regression_keeps_treating_an_empty_actual_as_missing |
| M34 | types.py `RowVerdictLiteral` gains `new_table` | CT-02 / INV-03 | KILLED | test_classify_contract::test_a_row_sub_verdict_can_never_be_new_table |
| M35 | gate.py `_validate_actual` prompt per-cell check removed | N22 | KILLED | test_validation::test_actual_prompt_cell_non_mapping_raises_malformed_actual |
| M36 | gate.py name-collision check removed | fail-open #6 | KILLED | test_validation::test_golden_field_and_table_name_collision_raises_malformed_golden |

**33 / 36 killed.** The 3 survivors map to G-2, G-4, G-5. **G-1 needed no mutant — it reproduces at HEAD.**

## Probes run at HEAD (no mutation) — the break attempt

| Probe | Result |
|---|---|
| `then.verdict` ∈ {new_field, new_line, new_table, wrong_format, match} via `parse_spec` | refused (SpecError) ✅ |
| directly-built `ScorerSpec` with `then: {verdict: match}` scoped `name_in: [amount]` | `verify_monotone` 0 problems; gate on a critical `amount` PASS — bypasses parse, so belt-and-braces only, but the same name blind spot as G-1 |
| `wrong_format → wrong_value` on a `format_critical` **number** field, unscoped | `verify_monotone` 6 problems ✅ (console refuses) — but loads via `--classifier` unchecked (G-1 layer 3) |
| same, scoped `field_type: date` / `field_type: text` / `name_in` / `name_matches` | accepted, 0 problems, **FAIL→PASS** (G-1) |
| `pinned-file` base, same class | same behaviour as `regression` |
| critical `wrong_value → missing`; prompt `wrong_value → missing`; table cell rewrites | gate stays FAIL ✅ |
| `then: {format_critical: true}` on `kind: table_column` | no-op (G-3) |
| `overall_gate` malformed maps: `DETAIL`, row verdict `detail`, row `new_table` | raise ✅; `critical: 0/None` → PASS (G-6) |

## AC coverage (S-01.1; unchanged from the previous stamp except the rows below)

| AC / invariant | Tests | Status |
|---|---|---|
| AC2–AC6, TP-02..08, TP-21, EX-A1-1/4/5, BR2, BR3, BR8, CT-02, N2, N22, N28, four types, purity, FO-5 both sites, 7-verdict gate, `RowVerdictLiteral` type-level | as listed in the 2026-09-21 stamp + test_gate.py:356 (row site) | ✅ COVERED (M13, M20–M24, M30–M36 killed) |
| `_as_row_verdict` runtime guard | — | ❌ MISSING (M14; G-2, non-blocking) |
| D2a per-column types + F-5 refusal + F-8 type-aware pairing | test_gate.py:373, :386; test_tables.py:497–541 | ✅ COVERED (M15–M18 killed) |
| D2b `date_format` tiers + F-4 fallback + DEBT-103 validator | test_date_format.py:41–177, :185 | ✅ COVERED (M25–M28 killed) |
| AC4 number format tier (`wrong_format` on a number) | — | ❌ MISSING (M29; G-5, non-blocking) |
| Registry contract + escalation-only | test_registry.py:53–337 | ✅ COVERED |
| Custom spec: only gate-failing verdicts actionable; `verify_monotone` compares gate outcomes for number/id | test_custom_scorers.py:270–318 | ✅ COVERED (M01–M07, M09–M12 killed) |
| **Custom spec monotone w.r.t. the gate for date/text/prompt/name-scoped rules; `verify_monotone` on the `--classifier` load path** | — | ❌ MISSING — **G-1 (blocking)** |

## Scenario A bugs (quarantined repros)
(none — G-1 is this delta's own residual, i.e. Scenario B: back to /implement.)

## Non-blocking debt surfaced for Dunga (`/debt add`)
- G-2 `_as_row_verdict` runtime guard untested (carried from the previous stamp's F-2, second ask).
- G-3 `format_critical` action is a no-op on table cells — refuse or propagate.
- G-4 (= F-7) OR-vs-assign in `compile_spec` unobservable without a stub base.
- G-5 number format tier has no test.
- G-6 `overall_gate` does not validate the `critical` flag's type.
- `verify_monotone`'s docstring says "belt and braces over `_validate_action`" — true on the console path only; after G-1(b) it will be true on both, and the docstring should say where it runs.
- Foreign to this gate but observed in `git status` at the end: `docs/state/DEBT.md` modified and `docs/process/REVIEW-RULES.md` untracked — not touched by this instance; the orchestrator should own them.

## History
- `/test re-stamp gate #2 (Atchim, fresh instance)` on 2026-09-28 at `c48c516`: ❌ **FAILED** — G-1 Critical (residual F-1 class: `wrong_format`+`format_critical` → `wrong_value` on a non-critical field; `verify_monotone` blind to date/text/name-scoped rules; `scorer_store` never runs it). F-1..F-8 verified closed; 33/36 mutants killed; 14 fix tests proven RED on `4cb7cc8`. Gates green.
- `/test re-stamp gate (Atchim, fresh instance)` on 2026-09-28 at `4cb7cc8`: ❌ **FAILED** — F-1 Critical (custom spec relaxes a critical `wrong_value` to an informational verdict; gate PASS), F-2 High (`overall_gate` row site accepts `new_table`; classify-time guard untested). 28/35 mutants killed. Gates green.
- `/test gap-fill (Atchim TDD gate)` on 2026-09-21 (re-gated 2026-09-22) at `1428520`: ✅ PASSED — FO-5 re-stamp; 104 classifier tests; drift-pin four-scenario matrix; alias-as-wrapper mutant killed. Staled by `87da307..f808bcf` (DEBT-46).
- `/implement (Atchim code review TDD gate)` on 2026-09-18 at `f90a0915`: ✅ PASSED — original S-01.1 stamp (2-round review, 76 tests). Superseded by the 2026-09-21 /test stamp.
