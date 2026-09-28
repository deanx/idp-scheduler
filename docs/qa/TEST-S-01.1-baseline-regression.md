# /test stamp — SPEC-01 / S-01.1 (classifier & gate)

**Status:** ✅ PASSED WITH FINDINGS — Wave C re-stamp #3 (DEBT-46). Gate #2's G-1 is **closed on every data route**: a `parse_spec`-valid rule can no longer turn a gate FAIL into a PASS on any field type, name scope, prompt, table cell or base, and a spec file on disk that would is refused at load. One residual of the same class remains reachable **only from Python** (a directly built `ScorerSpec`, never JSON/HTTP/CLI) — H-1 below, High, non-blocking by the same scale gate #2 applied to its own directly-built probe. Stampable; H-1 must land in the next `src/` delta and that delta re-gates fresh (`## Rigor` row 3).
**Source:** /test re-stamp gate #3 (Atchim, fresh instance — audit + mutation matrix; no production code edited, no tests added)
**Files:** src/idp_regression/classifier/__init__.py, canonical.py, custom.py, gate.py, registry.py, scorers.py, scoring.py, types.py; src/idp_regression/orchestration/scorer_store.py — tests: tests/classifier/test_classify.py, test_classify_contract.py, test_custom_scorers.py, test_date_format.py, test_edge_matrix.py, test_gate.py, test_performance.py, test_registry.py, test_tables.py, test_validation.py; tests/orchestration/test_scorer_store.py
**Sequence:** git-verified (`git log --diff-filter=A`): `custom.py` + `test_custom_scorers.py` first added together in `87da307`; `scorer_store.py` + `test_scorer_store.py` together in `e569ad0` (ADR-0003 co-commit waiver, unchanged). The fix round `621d735` co-commits 12 new tests with the fix; **RED proven independently by this gate (P7)**: those test files run against the pre-fix sources (`c48c516`, `git archive` to a scratch `PYTHONPATH`, module path verified) → **12 failed / 52 passed**, every failure a test `621d735` added. 
**Date:** 2026-09-28
**Commit (reviewed code):** `621d735` (HEAD, unchanged for the whole session; `git status` at start and end shows only `docs/state/STATE.json` modified). Working tree `shasum -a 256 -c`-verified identical to HEAD for all nine files after every mutant. Delta from the previous stamp `c48c516..621d735` on the nine files + their tests = 4 files, +182/−17.
**Author:** alex@divinocosta.com.br  <!-- solo mode — raw git config user.email -->
**Atchim TDD gate:** ✅ PASS WITH FINDINGS (H-1 High non-blocking; M-1 Medium; L-1 Low; G-2/G-3/G-4/G-6 confirmed and carried; G-5 refuted — now killed)
**Independence (R3 / DEBT-44):** ✅ structural (different models) — implementer Dengoso (Opus 5.5, `791c58e` + `621d735`) reviewed by Atchim (Fable 5.1), a **new fresh instance**: not the instance that FAILED at `4cb7cc8`, not the instance that FAILED at `c48c516`, and not any instance that issued a code-review APPROVE on this diff. This instance issued no earlier verdict on any of it. SPEC-01 is `Risk level: high` → this stamp binds regardless of the `prototype` profile; the verdict path ran at `full` per `## Rigor` row 1.
**Static:** ✅ `.venv/bin/python -m pytest -q` → **1874 passed, 15 skipped** (integration opt-in) · bare `.venv/bin/mypy` → Success, 0 issues / 150 files (strict) · `.venv/bin/ruff check src tests scripts` → All checks passed. Secret scan: hook-gated, not re-run (no new files). `PYTHONDONTWRITEBYTECODE=1` throughout; no stray `__pycache__` outside `.venv`/`frontend` at the end. Never sourced `.env`; never called IDP or the platform.

## Falsifiable freshness check (P5 / DEBT-77 — an explicit FILE LIST, never a directory)

This stamp is fresh iff the following prints nothing:

```
git diff --stat 621d735 HEAD -- \
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
git diff --diff-filter=A --name-only 621d735 HEAD -- src/idp_regression/classifier/
```

(Both printed nothing at stamp time.)

## What `621d735` closed — verified, not taken on trust

| Gate #2 finding | Fix | Verified by |
|---|---|---|
| G-1(a) Critical — `compile_spec` applied a verdict rewrite that passed a field the base failed | `_compile(enforce=True)`: rewrite reverted when `_fails_the_gate(base)` and not `_fails_the_gate(rewritten)` | P1: all four G-1 repros (date, text, `name_in`, `name_matches`) + `id`/`kind` + prompt, on **both** bases → gate FAIL; mutants B1, B2 killed |
| G-1(b) — `scorer_store.load_specs` never ran the proof | `verify_monotone` on every load; failure → `errors`, not registered | P6: five relaxing spec files reported, none registered; mutants D1, D2 killed |
| G-1(c) — enumerator blind to date/text and to names | `_FORMAT_ONLY_PAIRS` per type; `_scoped_names` adds every `name_in` name; `verify_monotone` checks the spec **as written** (`enforce=False`) | P1: `verify_monotone` non-empty for date/text/`name_in`/`kind`; `name_matches` correctly left to enforcement (P6 `re-fmt` loads, gate FAIL at run time); mutants B3, C1–C4 killed |

## Findings

**H-1 — HIGH (non-blocking: no data route) — `classifier/custom.py::_fails_the_gate` disagrees with `overall_gate` for `kind == "table_column"`, so a DIRECTLY BUILT `ScorerSpec` still turns a critical block's FAIL into PASS.** `_fails_the_gate` counts `wrong_format` + `format_critical` as failing for every kind; the row gate (`overall_gate`, `detail` branch) reads only `missing`/`wrong_value` on a critical block and `RowVerdict` carries no `format_critical` at all (gate #2's G-3). So the rewrite `wrong_value → wrong_format` with `format_critical: true` looks gate-failing to the enforcer and to the proof, and is neither. Reproduced at HEAD, no mutation (probe P2):
```
golden tables.items: match_key sku, critical true, rows [{sku A, qty 2}] · actual qty 3   → base regression gate: FAIL
ScorerSpec(rules=(Rule(when={kind: table_column, verdict_is: [wrong_value]}, then={verdict: wrong_format, format_critical: True}),))
  compile_spec (enforce=True): rewrite applied · verify_monotone: 0 problems · custom gate: PASS   <<< FAIL→PASS
same, unscoped `when={verdict_is: [wrong_value]}`                                  → PASS, 0 problems
same, `when={actual_empty: True}` on a cell the actual lacks (base `missing`)      → PASS, 0 problems
```
**Reachability:** `parse_spec` refuses `then.verdict: wrong_format` (`ACTIONABLE_VERDICTS`), and every production route — `scorer_store.load_specs`, `ui/api.py` validate/preview/save — goes through `parse_spec` (grepped: no other `ScorerSpec(` construction under `src/`). So this needs Python, which can already register any scorer; the monotone guarantee holds for its threat model (specs as data). **Why it is still High:** (1) `compile_spec`'s docstring and the commit message claim enforcement "whatever route the spec took, including a directly built spec" — false; (2) it is the **third** occurrence of the same class (an enforcement predicate narrower or different from the gate's), and one edit to `ACTIONABLE_VERDICTS` opens it to data; (3) the fix's own tests cannot see it: `test_compile_spec_is_monotone_by_construction_on_every_enumerated_context` uses `_fails_the_gate` as the oracle for code enforced by `_fails_the_gate` (circular — R1's "reviewing a guard by mutating the guard"), and `test_a_directly_built_relaxing_spec_cannot_pass_a_failing_gate` only exercises `kind: field` without `format_critical`. **Fix:** in `_fails_the_gate`, for `ctx.kind == "table_column"` return `verdict in ("missing","wrong_value") and critical` (no format leg — mirror the row gate, or make `RowVerdict` carry `format_critical` and gate on it, which also closes G-3); re-oracle the "by construction" test on `overall_gate(classify(...))` per kind with a real golden/actual, and add the P2 repro (direct spec, critical block, `wrong_format`+`format_critical`) asserting `FAIL`. **Regression-worthiness: PIN** (same class as the F-1/G-1 PIN, third instance).

**M-1 — MEDIUM — `gate.py::_classify` → `_classify_prompt(…, scorer)`: no test exercises a non-default scorer on a PROMPT (mutant E4 SURVIVED, unchanged line).** Dropping the `scorer` argument (prompts silently compared by `regression_scorer` whatever the run named) passes the whole suite. Consequence today: `pinned-file` on a prompt with empty answer/empty actual reads `missing` instead of `match` (fail-closed, but a false red on a pinned file), and a custom rule scoped `kind: prompt` would be silently not applied — "a gate quietly weaker than the author believes it configured", `custom.py`'s own words. Add: `pinned-file` prompt empty/empty → `match`; a spec `{kind: prompt} → {critical: true}` escalates a prompt.

**L-1 — LOW — a legitimate tightening spec has no test pinning that `verify_monotone` ACCEPTS it (mutant A2 SURVIVED).** With `_fails_the_gate` ignoring `result.critical`, the rule `{verdict_is: [wrong_format]} → {verdict: wrong_value, critical: true}` (the documented way to gate a format difference as a value difference) would be **refused** by `verify_monotone` and neutralized by `compile_spec`. Fail-closed, but the console would reject a correct spec with a misleading message. Pin: that spec → `verify_monotone == []` and gate FAIL.

### Gate #2's non-blocking G-2..G-6 — confirmed or refuted, not re-filed
| | Status | Evidence |
|---|---|---|
| G-2 `_as_row_verdict` runtime guard untested | **Confirmed** | mutant E2 (guard removed) SURVIVED; `grep _as_row_verdict tests/` empty |
| G-3 `format_critical` action is a no-op on table cells | **Confirmed** (and is the root of H-1) | P7: base PASS, custom PASS, 0 problems |
| G-4 OR-vs-assign unobservable | **Confirmed — equivalent mutant** | F1 SURVIVED; under the two shipped bases the pre-OR flag is always False. Same for G2 (`verify_monotone`'s clears-critical leg: dead code while bases return bare verdicts) |
| G-5 number format tier unpinned | **Refuted — now killed** | E7 (`_format_number` identity) KILLED by `test_rewriting_…[amount-number-1250.00-1.250,00]`; the `test_edge_matrix.py` ask is now cosmetic |
| G-6 `overall_gate` accepts non-bool `critical` | **Confirmed**, plus one more shape | P5: `critical: 0` / `None` → PASS; `detail` block with `critical: 0` and a `missing` row → PASS. `format_critical: "no"` → FAIL (truthy — fail-closed). Still unreachable from `classify` and from JSON bools |

## Mutation matrix (28 own mutants, built from the invariant per R1; `tests/classifier` + `tests/orchestration/test_scorer_store.py`, `-x`; every file restored from the scratchpad copy and `shasum -a 256 -c`-verified after each; `PYTHONDONTWRITEBYTECODE=1`)

| # | File · change | Invariant it came from (R1) | Result | Killed by |
|---|---|---|---|---|
| A1 | custom.py `_fails_the_gate` format leg → False | `_fails_the_gate` ≡ `overall_gate` per entry | KILLED | test_custom_scorers::test_verify_monotone_catches_relaxing_a_format_critical_field |
| A2 | `_fails_the_gate` ignores `result.critical` | same | **SURVIVED** | — (L-1; not equivalent: refuses a legitimate tightening spec) |
| A3 | `_fails_the_gate` ignores `ctx.critical` | same | KILLED | …test_verify_monotone_compares_gate_outcomes_not_just_match[new_field] |
| A4 | `_fails_the_gate` wrong_format keyed on `critical` | same | KILLED | …[wrong_format] |
| B1 | `_compile` `enforce and` → `False and` | monotone by construction | KILLED | test_rewriting_a_format_critical_wrong_format_into_wrong_value_still_fails[date] |
| B2 | `compile_spec` → `_compile(enforce=False)` | same | KILLED | same |
| B3 | `verify_monotone` compiles with `enforce=True` | proof checks the spec AS WRITTEN | KILLED | TestMonotonicity::test_verify_monotone_catches_a_relaxation_the_key_check_would_miss |
| B4 | revert then `continue` instead of `break` | first matching rule wins | KILLED | TestCompilation::test_first_matching_rule_wins |
| C1 | enumerator drops the format-only pair | proof covers every `field_type` | KILLED | test_rewriting_…[date] |
| C2 | date pair identical | same | KILLED | same |
| C3 | text pair identical | same | KILLED | test_rewriting_…[vendor-text] |
| C4 | `_scoped_names` returns only `total` | proof reaches `name_in` names | KILLED | test_rewriting_…[amount-name_in] |
| D1 | scorer_store: `problems = []` | load path runs the proof | KILLED | test_scorer_store::test_a_relaxing_spec_file_is_reported_not_registered |
| D2 | scorer_store: report but still register | load path refuses | KILLED | same |
| E1 | gate.py `critical or escalated` → `critical` (unchanged line) | cell escalation reaches the block | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| E2 | gate.py `_as_row_verdict` guard removed (unchanged line) | row vocabulary (6) at runtime | **SURVIVED** | — (G-2 confirmed) |
| E3 | gate.py `_classify_field` flags not OR-ed (unchanged line) | escalation-only | KILLED | TestMonotonicity::test_a_spec_can_gate_a_field_the_golden_left_ungated |
| E4 | gate.py `_classify_prompt` called without `scorer` (unchanged line) | one comparison policy per run | **SURVIVED** | — (M-1) |
| E5 | gate.py `_classify_table` called without `scorer` (unchanged line) | same | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| E6 | gate.py `overall_gate` DEBT-80 leg removed (unchanged line) | gate predicate | KILLED | test_rewriting_…[date] |
| E7 | canonical.py `_format_number` identity (unchanged line) | AC4 number format tier | KILLED | test_rewriting_…[amount-number] (G-5 refuted) |
| E8 | `ACTIONABLE_VERDICTS` + `wrong_format` | closed action vocabulary | KILLED | test_a_rule_can_not_rewrite_a_failure_into_an_informational_verdict[wrong_format] |
| E9 | `parse_spec` base check removed | base is a shipped classifier | KILLED | TestParsing::test_the_closed_vocabulary_is_enforced…[base must be one of] |
| F1 | `_compile` `critical` assigned not OR-ed | escalation-only | **SURVIVED — equivalent** under shipped bases (G-4) |
| F2 | `_compile` verdict action never applied | rule verdict applies | KILLED | TestMonotonicity::test_a_spec_can_gate_a_field_the_golden_left_ungated |
| G1 | `verify_monotone` relax-to-match leg removed | proof legs | KILLED | …the_key_check_would_miss |
| G2 | `verify_monotone` clears-critical leg removed | proof legs | **SURVIVED — equivalent** (dead while bases return bare verdicts) |

**23 / 28 killed.** Survivors: A2 (L-1), E2 (G-2), E4 (M-1) are real gaps; F1, G2 are equivalent. 8 mutants (E1–E7, and the E9 base check) sit on lines `621d735` did not change (R1). **H-1 needed no mutant — it reproduces at HEAD.**

## Probes run at HEAD (no mutation) — the break attempt

| Probe | Result |
|---|---|
| P1 G-1 repros via `parse_spec`: `wrong_format→wrong_value` scoped by `field_type: date|text`, `name_in`, `name_matches`, `kind: field`, on `regression` AND `pinned-file` | gate **FAIL** on all 10; `verify_monotone` non-empty on all but `name_matches` (regex not enumerable — enforcement covers it, P6) ✅ |
| P1b prompt `wrong_value(critical)→missing`; direct `→new_field` | FAIL / FAIL ✅ |
| P2 direct `ScorerSpec`, table cell, `wrong_value→wrong_format + format_critical` (scoped, unscoped, and `actual_empty`) | **PASS, 0 problems — H-1** |
| P2 same via `parse_spec` | refused (`then.verdict must be one of ('missing','wrong_value')`) ✅ |
| P3 direct `→match`; direct `critical: False`; direct `verdict: "detail"`; direct spec whose base is a registered custom (chain) | FAIL on all ✅ |
| P4 `confidence_below` tighten; pinned-file empty/empty → `missing`; multi-rule relax-then-tighten; `critical_in_golden: false` scope | tighten / tighten / FAIL / FAIL ✅ |
| P5 `overall_gate` malformed maps: `critical: 0/None` → PASS (G-6); missing `critical` → KeyError; `rows: None` → TypeError; row `detail`/`new_table`, top `DETAIL` → MalformedActualError; non-dict entry → TypeError | as listed |
| P6 six relaxing spec FILES via `register_custom_classifiers` | five refused with `relaxes its base classifier: …`; `re-fmt` (`name_matches`) loads and its gate is FAIL at run time ✅ |
| P7 `{kind: table_column, verdict_is:[wrong_format]} → {format_critical: true}` | no-op (G-3 confirmed); enumerator = 3072 contexts |

## AC coverage (S-01.1; unchanged from the previous stamp except the rows below)

| AC / invariant | Tests | Status |
|---|---|---|
| AC2–AC6, TP-02..08, TP-21, EX-A1-1/4/5, BR2, BR3, BR8, CT-02, N2, N22, N28, four types, purity, FO-5 both sites, 7-verdict gate, `RowVerdictLiteral` type-level | as listed in the 2026-09-21 stamp + test_gate.py:356 | ✅ COVERED (E1, E3, E5, E6, E8, E9 killed) |
| D2a / D2b / F-4 / F-5 / F-8 | test_gate.py:373, :386; test_tables.py:497–541; test_date_format.py:41–185 | ✅ COVERED (per gate #2, unchanged files) |
| AC4 number format tier (`wrong_format` on a number) | test_custom_scorers.py:335–353 (`amount` cases) | ✅ COVERED (E7 killed; G-5 closed incidentally) |
| Custom spec monotone against the GATE's predicate for date/text/`name_in`/`name_matches`; `verify_monotone` on the `--classifier` load path (**gate #2 G-1**) | test_custom_scorers.py:335–402; test_scorer_store.py:89–106 | ✅ COVERED — and proven RED on `c48c516` (12/12) |
| `_fails_the_gate` ≡ row gate for `kind: table_column` | — | ❌ MISSING — **H-1** (non-blocking: Python-only route) |
| Non-default scorer on prompts (`_classify_prompt` plumbing) | — | ❌ MISSING — M-1 |
| `verify_monotone` accepts a legitimate tightening rewrite | — | ❌ MISSING — L-1 |
| `_as_row_verdict` runtime guard | — | ❌ MISSING (G-2, carried) |

## Scenario A bugs (quarantined repros)
(none — H-1/M-1/L-1 are this delta's own residuals, recorded here and handed to Dunga; no `@bug-repro` test was written since this gate edits nothing but the stamp.)

## Non-blocking debt surfaced for Dunga (`/debt add`)
- **H-1** `_fails_the_gate` vs row gate on `table_column` — PIN (third instance of the F-1/G-1 class); the fix touches `src/` so its delta re-gates fresh, never `re-gate skipped`.
- **M-1** prompts never exercised with a non-default scorer (E4 survived).
- **L-1** legitimate tightening spec not pinned as accepted by `verify_monotone` (A2 survived).
- G-2 `_as_row_verdict` runtime guard untested (third ask).
- G-3 `format_critical` action is a no-op on table cells — closing it via `RowVerdict.format_critical` would close H-1 the other way round; pick one.
- G-4 / G2: OR-vs-assign and the clears-critical proof leg are dead code under the shipped bases — pin with a stub base returning `ScoreResult`, or drop the claims.
- G-6 `overall_gate` does not validate the `critical` flag's type (top level and `detail` block).
- Docstring: `compile_spec` / commit `621d735` claim enforcement for "a directly built spec" — false for `table_column` until H-1 lands.

## History
- `/test re-stamp gate #3 (Atchim, fresh instance)` on 2026-09-28 at `621d735`: ✅ **PASSED WITH FINDINGS** — G-1 closed on every data route (P1/P6, mutants B1–D2 killed, 12 fix tests proven RED on `c48c516`); H-1 High non-blocking (`_fails_the_gate` ≠ row gate for `table_column`, Python-only route); 23/28 mutants killed, 2 equivalent. Gates green (1874 passed / mypy 0 / ruff clean).
- `/test re-stamp gate #2 (Atchim, fresh instance)` on 2026-09-28 at `c48c516`: ❌ **FAILED** — G-1 Critical (residual F-1 class: `wrong_format`+`format_critical` → `wrong_value` on a non-critical field; `verify_monotone` blind to date/text/name-scoped rules; `scorer_store` never runs it). F-1..F-8 verified closed; 33/36 mutants killed; 14 fix tests proven RED on `4cb7cc8`. Gates green.
- `/test re-stamp gate (Atchim, fresh instance)` on 2026-09-28 at `4cb7cc8`: ❌ **FAILED** — F-1 Critical (custom spec relaxes a critical `wrong_value` to an informational verdict; gate PASS), F-2 High (`overall_gate` row site accepts `new_table`; classify-time guard untested). 28/35 mutants killed. Gates green.
- `/test gap-fill (Atchim TDD gate)` on 2026-09-21 (re-gated 2026-09-22) at `1428520`: ✅ PASSED — FO-5 re-stamp; 104 classifier tests; drift-pin four-scenario matrix; alias-as-wrapper mutant killed. Staled by `87da307..f808bcf` (DEBT-46).
- `/implement (Atchim code review TDD gate)` on 2026-09-18 at `f90a0915`: ✅ PASSED — original S-01.1 stamp (2-round review, 76 tests). Superseded by the 2026-09-21 /test stamp.
