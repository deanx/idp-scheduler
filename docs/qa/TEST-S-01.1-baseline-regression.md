# /test stamp — SPEC-01 / S-01.1 (classifier & gate)

**Status:** ✅ PASSED WITH FINDINGS — Wave C re-stamp #4 (DEBT-46). Gate #3's **H-1 is closed**: `_fails_the_gate` now agrees with `overall_gate` on every leaf, kind, flag and verdict combination — verified against the **real fan-out** (`make_classifier → classify → overall_gate`, 240 cases, 0 disagreements), not against a hand-built map — and an exhaustive sweep of **51,264 directly built `ScorerSpec`s** (both bases × 9 failing scenarios × 16 scopes × every action combination × 1- and 2-rule specs) produced **0 FAIL→PASS** leaks. The F-1/G-1/H-1 class is closed on every route, Python included. Two non-blocking residuals (N-1 Medium, N-2 Low) plus one Python-only crash shape (N-3 Low) below; none is fail-open. Stampable.
**Source:** /test re-stamp gate #4 (Atchim, fresh instance — audit + mutation matrix; no production code edited, no tests added)
**Files:** src/idp_regression/classifier/__init__.py, canonical.py, custom.py, gate.py, registry.py, scorers.py, scoring.py, types.py; src/idp_regression/orchestration/scorer_store.py — tests: tests/classifier/test_classify.py, test_classify_contract.py, test_custom_scorers.py, test_date_format.py, test_edge_matrix.py, test_gate.py, test_performance.py, test_registry.py, test_tables.py, test_validation.py; tests/orchestration/test_scorer_store.py
**Sequence:** git-verified (`git log --diff-filter=A`), unchanged from gate #3 (`custom.py` + `test_custom_scorers.py` first added together in `87da307`; ADR-0003 co-commit waiver). The fix round `35a35dc` co-commits 6 new tests with a 5-line fix; **RED proven independently by this gate (P7)**: `test_custom_scorers.py` at HEAD run against the `621d735` sources (`git archive` to a scratch `PYTHONPATH`, module path verified) → **3 failed / 57 passed** — the three H-1 tests (`test_fails_the_gate_agrees_with_overall_gate_on_every_leaf`, `test_a_directly_built_spec_cannot_pass_a_critical_table_cell[when0|when1]`). The M-1 / L-1 / G-2 tests are gap-fill for pre-existing behaviour and are correctly GREEN there; their RED is against the mutant each was written to kill (M4, M14, M15 below — all KILLED, where gate #3's A2, E4, E2 SURVIVED).
**Date:** 2026-09-28
**Commit (reviewed code):** `9491657` (HEAD; the code delta is `621d735..35a35dc`, `9491657` is docs-only — DEBT-120..122). `git status` at start and end shows only `docs/state/STATE.json` modified (pre-existing) plus this stamp. Working tree `shasum -a 256 -c`-verified identical to HEAD for `custom.py`, `gate.py`, `test_custom_scorers.py` after every mutant. Delta from the previous stamp on the nine files: `custom.py` only, +7/−1.
**Author:** alex@divinocosta.com.br  <!-- solo mode — raw git config user.email -->
**Atchim TDD gate:** ✅ PASS WITH FINDINGS (N-1 Medium; N-2 Low; N-3 Low, Python-only; gate #3's H-1 CLOSED, M-1 CLOSED, L-1 CLOSED, G-2 CLOSED; G-3/G-4/G-6 filed as DEBT-120/122/121 and not re-argued)
**Independence (R3 / DEBT-44):** ✅ structural (different models) — implementer Dengoso (Opus 5.5, `35a35dc`) reviewed by Atchim (Fable 5.1), a **new fresh instance**: not the instance that FAILED at `4cb7cc8`, not the one that FAILED at `c48c516`, not the one that PASSED WITH FINDINGS at `621d735`, and not any instance that issued a code-review APPROVE on this diff. This instance issued no earlier verdict on any of it. SPEC-01 is `Risk level: high` → this stamp binds regardless of the `prototype` profile; the delta touches `src/`, so `re-gate skipped` was never available (`## Rigor` row 3); the verdict path ran at `full` (row 1).
**Static:** ✅ `.venv/bin/python -m pytest -q` → **1881 passed, 15 skipped** (integration opt-in) · bare `.venv/bin/mypy` → Success, 0 issues / 150 files (strict) · `.venv/bin/ruff check src tests scripts` → All checks passed. Secret scan: hook-gated, not re-run (no new files). `PYTHONDONTWRITEBYTECODE=1` throughout; no stray `__pycache__` outside `.venv`/`frontend` at the end. Never sourced `.env`; never called IDP or the platform.

## Falsifiable freshness check (P5 / DEBT-77 — an explicit FILE LIST, never a directory)

This stamp is fresh iff the following prints nothing:

```
git diff --stat 9491657 HEAD -- \
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
git diff --diff-filter=A --name-only 9491657 HEAD -- src/idp_regression/classifier/
```

(Both printed nothing at stamp time. `git diff --stat 621d735 HEAD -- <the nine>` printed `custom.py | 8 +++++++-` — the whole code delta under review.)

## What `35a35dc` closed — verified, not taken on trust

| Gate #3 finding | Fix | Verified by |
|---|---|---|
| H-1 High — `_fails_the_gate` ≠ row gate for `table_column` (`wrong_format`+`format_critical` counted as failing; a directly built spec turned a critical block's FAIL into PASS) | `custom.py::_fails_the_gate`: `table_column` returns False for anything but `missing`/`wrong_value` (mirrors `overall_gate`'s `detail` branch) | Probe A (real fan-out, 240 cases, 0 disagreements); Probe B (51,264 direct specs, 0 leaks); gate #3's P2 repro now FAIL; mutants M1, M2, M3 killed by the new agreement test |
| H-1 test oracle was `_fails_the_gate` itself (circular) | new oracle `_gate_of_one_leaf` calls **`overall_gate`** on a one-leaf map | **M9** (`overall_gate` row branch widened to `wrong_format`, an unchanged line) KILLED by the agreement test → the oracle is not a copy of the gate. But see N-1: the *map builder* is a copy of the fan-out's flag OR. |
| M-1 Medium — prompts never exercised with a non-default scorer | two tests (`pinned-file` empty/empty prompt → `match`; `kind: prompt` rule applied) | **M14** (`_classify_prompt` without `scorer`, unchanged line) KILLED — gate #3's E4 survived |
| L-1 Low — legitimate tightening not pinned as accepted | `test_verify_monotone_accepts_a_legitimate_format_tightening` | **M4** (`_fails_the_gate` ignores `result.critical`) KILLED — gate #3's A2 survived |
| G-2 — `_as_row_verdict` runtime guard untested (third ask) | `test_a_scorer_returning_new_table_for_a_cell_is_refused` | **M15** (guard removed, unchanged line) KILLED — gate #3's E2 survived |

## Findings

**N-1 — MEDIUM (non-blocking, not fail-open) — `gate.py::_classify_field`'s `format_critical=format_critical or result.format_critical` is unpinned (mutant M12 SURVIVED, unchanged line), and the new oracle cannot see it because `_gate_of_one_leaf` hand-builds the map's flags.** Witness, **data route** (`parse_spec`-valid): golden `d: {value: 2026-01-22, type: date}` (no `format_critical`), actual `22/01/2026`, spec `{when: {verdict_is: [wrong_format]}, then: {format_critical: true}}` → base PASS, custom **FAIL** at HEAD (correct: the documented way to gate a format difference). Under M12: custom **PASS**, `verify_monotone == []`, and the whole suite is green — the `format_critical: true` action becomes a silent no-op on *fields*, which is exactly DEBT-120's shape on the leaf that was supposed to work. Not fail-open (the base passes too), but "a gate quietly weaker than the author believes it configured" — `custom.py`'s own words. **Why it matters for the class:** `_gate_of_one_leaf` copies `_classify_field`/`_classify_table`'s flag-OR rule (`critical = context.critical or result.critical`, `fmt = …`) and drops a cell's `format_critical` because gate.py does. The test is non-circular against the **gate** (M9 killed) but circular against the **fan-out**: if gate.py ever changed how it builds a map (e.g. DEBT-120's `RowVerdict.format_critical` option), the oracle and `_fails_the_gate` would go stale together and the test would stay green. **Fix:** (a) pin the witness above through `build_classifier(...).classify` + gate; (b) re-oracle `_gate_of_one_leaf` on the real fan-out — `make_classifier(lambda ctx: result if ctx.name == target else "match")` over a one-leaf golden/actual per kind, then `overall_gate` (this gate's Probe A is that test, 240 cases, ~40 lines). Regression-worthiness: **PIN** as part of the existing F-1/G-1/H-1 PIN (fourth instance of "the oracle is a copy of the rule").

**N-2 — LOW — `custom.py::_matches` `kind` condition: the negative side is unpinned (mutant M17 SURVIVED).** Removing the `kind` check lets a `kind: prompt` rule fire on fields and cells; the suite is green because every `kind`-scoped test asserts the rule fires on its own kind, never that it does not fire elsewhere. Fail-closed direction (over-application only tightens), but a spec author's scope is silently ignored. Pin: `{kind: prompt, confidence_below: 0.5, verdict_is: [match]} → wrong_value/critical` on a low-confidence **field** → PASS.

**N-3 — LOW (Python-only route) — a directly built `then: {verdict: "detail"}` on a passing non-critical field crashes `overall_gate` with `KeyError: 'rows'` rather than `MalformedActualError`.** `_compile` does not validate a rewrite verdict against `VERDICTS` (that is `parse_spec`'s job, bypassed by direct construction); `_classify_field` writes `"detail"` into a leaf and `overall_gate`'s `detail` branch indexes `rows`. A crash, never a green build; same family as DEBT-121 (caller-supplied shapes). Suggest: `_compile` refuses a rewrite outside `VERDICTS` at compile time, or file with DEBT-121.

**Docstring nit (fold into N-1):** `_fails_the_gate`'s docstring says the test holds it "to `overall_gate` itself, never to a copy of this rule" — true of the gate predicate, not of the map builder.

### Gate #3's non-blocking items — status
| | Status |
|---|---|
| H-1 | **CLOSED** (Probe A/B; M1–M3 killed) |
| M-1 | **CLOSED** (M14 killed) |
| L-1 | **CLOSED** (M4 killed) |
| G-2 | **CLOSED** (M15 killed) |
| G-3 → DEBT-120, G-6 → DEBT-121, G-4/G2 equivalents → DEBT-122 | filed, not re-argued; DEBT-122's "equivalent today" claim stands (no mutant re-run — no base returns a `ScoreResult`) |
| Docstring claim on `compile_spec` ("directly built spec") | now **true** (Probe B) |

## Mutation matrix (18 own mutants, built from the invariant per R1; `tests/classifier` + `tests/orchestration/test_scorer_store.py`, `-x`; each file restored by `cp` from the scratchpad copy and `shasum -a 256 -c`-verified after every mutant; `PYTHONDONTWRITEBYTECODE=1`)

| # | File · change | Invariant it came from (R1) | Result | Killed by |
|---|---|---|---|---|
| M1 | custom.py `_fails_the_gate` table_column early-return removed (H-1 revert) | `_fails_the_gate` ≡ `overall_gate` per leaf | KILLED | test_custom_scorers::test_fails_the_gate_agrees_with_overall_gate_on_every_leaf |
| M2 | table_column check moved BEFORE the missing/wrong_value leg | same (a critical block's `missing`/`wrong_value` cell DOES fail) | KILLED | same |
| M3 | `"table_column"` → `"prompt"` | same for prompts (`wrong_format`+`format_critical` fails) | KILLED | same |
| M4 | `_fails_the_gate` ignores `result.critical` (gate #3 A2) | predicate ORs the scorer's flags | KILLED | same (L-1 closed) |
| M5 | `_fails_the_gate` ignores `result.format_critical` | same | KILLED | same |
| M6 | `_compile` enforcement (the revert line) deleted | monotone by construction | KILLED | test_rewriting_a_format_critical_wrong_format_into_wrong_value_still_fails[date] |
| M7 | `KINDS` drops `table_column` | proof enumerates every kind | KILLED | test_a_directly_built_spec_cannot_pass_a_critical_table_cell[when0] |
| M8 | `verify_monotone` gate-outcome leg → `if False` | proof legs | KILLED | TestMonotonicity::test_verify_monotone_catches_a_relaxation_the_key_check_would_miss |
| M9 | gate.py `overall_gate` row branch widened to `wrong_format` (**unchanged line**) | oracle is the gate, not a copy | KILLED | test_fails_the_gate_agrees_with_overall_gate_on_every_leaf |
| M10 | gate.py cell `ScoreContext(critical=False)` (**unchanged line**) | enforcement input: a cell's ctx carries its block's `critical` | KILLED | test_a_directly_built_spec_cannot_pass_a_critical_table_cell[when1] |
| M11 | gate.py `escalated or cell_result.critical` removed (**unchanged line**) | cell escalation reaches the block | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| M12 | gate.py `_classify_field` `format_critical or result.format_critical` → `format_critical` (**unchanged line**) | escalation-only OR on the leaf; fan-out ≡ oracle's map builder | **SURVIVED — not equivalent** (N-1; witnessed PASS-vs-FAIL on a data-route spec) | — |
| M13 | gate.py `_classify_field` `critical or result.critical` → `critical` (**unchanged line**) | same, `critical` leg | KILLED | TestMonotonicity::test_a_spec_can_gate_a_field_the_golden_left_ungated |
| M14 | gate.py `_classify_prompt` called without `scorer` (**unchanged line**, gate #3 E4) | one comparison policy per run | KILLED | test_pinned_file_reads_an_empty_prompt_against_an_empty_answer_as_agreement (M-1 closed) |
| M15 | gate.py `_as_row_verdict` guard → `if False` (**unchanged line**, gate #3 E2) | row vocabulary (6) at runtime | KILLED | test_a_scorer_returning_new_table_for_a_cell_is_refused (G-2 closed) |
| M16 | gate.py `overall_gate` DEBT-80 leg → `elif False` (**unchanged line**) | gate predicate | KILLED | test_rewriting_…[date] |
| M17 | custom.py `_matches` `kind` condition removed | rule scope | **SURVIVED — not equivalent** (N-2; fail-closed direction) | — |
| M18 | `ACTIONABLE_VERDICTS` + `wrong_format` (the one edit that would open H-1 to data) | closed action vocabulary | KILLED | test_a_rule_can_not_rewrite_a_failure_into_an_informational_verdict[wrong_format] |

**16 / 18 killed.** Survivors M12 (N-1) and M17 (N-2) are real gaps, neither equivalent, neither fail-open. **Equivalent mutants: none in this matrix** (DEBT-122's F1/G2 were not re-run; their equivalence claim stands as filed). 9 mutants (M9–M16, M12 among them) sit on lines `35a35dc` did not change (R1).

## Probes run at HEAD (no mutation) — the break attempt

| Probe | Result |
|---|---|
| **A** — `_fails_the_gate` vs the REAL fan-out: for every kind × golden `critical`/`format_critical` × all 30 scorer results (6 bare + 24 `ScoreResult` flag combos), a scorer returning that result for one target leaf, `make_classifier → classify → overall_gate`, compared with `_fails_the_gate(captured ctx, result)` | **240 cases, 0 disagreements, 0 raises** ✅ (this is the non-circular oracle N-1 asks the test to adopt) |
| **B** — exhaustive DIRECTLY BUILT `ScorerSpec`: both bases × 9 base-FAILing scenarios (field/prompt/cell × `wrong_value`/`missing`, field `format_critical` `wrong_format`, row `missing`) × 16 `when` scopes (incl. `{}`, `kind`, `name_in`, `name_matches`, `critical_in_golden` both values, `verdict_is` ×3, emptiness, confidence, `field_type` ×3) × every `then` combination over 9 verdicts (incl. `detail`, `bogus`) × `critical` ∈ {–, T, F} × `format_critical` ∈ {–, T, F} × 1- and 2-rule specs | **51,264 specs, 0 FAIL→PASS**; 3,528 raised (fail-closed: `MalformedActualError` for a row/leaf vocabulary breach, `KeyError` for N-3) ✅ |
| **C** — witnesses: M12 (data-route `format_critical` action on an ungated date field) → HEAD FAIL / M12 PASS; M17 (`kind: prompt` rule on a field) → HEAD PASS; direct `verdict: "detail"` on a passing field → `KeyError 'rows'` | N-1 / N-2 / N-3 |
| Gate #3's P2 repro (`{kind: table_column, verdict_is: [wrong_value]} → {verdict: wrong_format, format_critical: true}`, scoped, unscoped, `actual_empty`) | **FAIL** on all, `verify_monotone` non-empty ✅ (covered by Probe B and `test_a_directly_built_spec_cannot_pass_a_critical_table_cell`) |
| `ScorerSpec(` construction under `src/` | one site, `parse_spec` — every data route still goes through the closed vocabulary ✅ |

## AC coverage (S-01.1; unchanged from gate #3 except the rows below)

| AC / invariant | Tests | Status |
|---|---|---|
| AC2–AC6, TP-02..08, TP-21, EX-A1-1/4/5, BR2, BR3, BR8, CT-02, N2, N22, N28, four types, purity, FO-5 both sites, 7-verdict gate, `RowVerdictLiteral` type-level | as listed in the 2026-09-21 stamp + test_gate.py:356 | ✅ COVERED (M11, M13, M16, M18 killed) |
| D2a / D2b / F-4 / F-5 / F-8 | test_gate.py:373, :386; test_tables.py:497–541; test_date_format.py:41–185 | ✅ COVERED (per gate #2, unchanged files) |
| Custom spec monotone against the GATE's predicate on every data route (gate #2 G-1) | test_custom_scorers.py:335–402; test_scorer_store.py:89–106 | ✅ COVERED (M6, M8 killed) |
| `_fails_the_gate` ≡ `overall_gate` for every kind incl. `table_column` (**gate #3 H-1**) | test_custom_scorers.py:405–466 (`_gate_of_one_leaf`, `test_fails_the_gate_agrees_with_overall_gate_on_every_leaf`), :469–485 (`test_a_directly_built_spec_cannot_pass_a_critical_table_cell[when0,when1]`) | ✅ COVERED — proven RED on `621d735` (3/3); M1–M3, M7, M9, M10 killed |
| `verify_monotone` accepts a legitimate tightening (gate #3 L-1) | test_custom_scorers.py:488–495 | ✅ COVERED (M4 killed) |
| Non-default scorer on prompts (gate #3 M-1) | test_custom_scorers.py:499–524 | ✅ COVERED (M14 killed) |
| `_as_row_verdict` runtime guard (G-2) | test_custom_scorers.py:527–537 | ✅ COVERED (M15 killed) |
| Leaf `format_critical` OR through the fan-out (`format_critical: true` action on an ungated field) | — | ❌ MISSING — **N-1** (non-blocking, not fail-open) |
| `kind` scope does not fire on another kind | — | ❌ MISSING — N-2 |

## Scenario A bugs (quarantined repros)
(none — N-1/N-2/N-3 are residual test gaps and a Python-only crash shape, recorded here and handed to Dunga; this gate edits nothing but the stamp.)

## Non-blocking debt surfaced for Dunga (`/debt add`)
- **N-1** `_classify_field` leaf `format_critical` OR unpinned (M12 survived, unchanged line); `_gate_of_one_leaf` hand-builds the map — re-oracle on the real fan-out (Probe A shape). PIN, same class as F-1/G-1/H-1 (fourth instance of oracle-copies-rule).
- **N-2** `_matches` `kind` scope negative side unpinned (M17 survived).
- **N-3** direct `then: {verdict: "detail"}` → `KeyError 'rows'` in `overall_gate` (Python-only; fold into DEBT-121 or refuse at `_compile`).
- Docstring: `_fails_the_gate` "never to a copy of this rule" — qualify: the gate is called, the map builder is copied.

## History
- `/test re-stamp gate #4 (Atchim, fresh instance)` on 2026-09-28 at `9491657` (code `35a35dc`): ✅ **PASSED WITH FINDINGS** — H-1/M-1/L-1/G-2 closed (Probe A 240/0, Probe B 51,264/0; 3 H-1 tests proven RED on `621d735`); N-1 Medium (fan-out `format_critical` OR unpinned, oracle copies the map builder), N-2/N-3 Low; 16/18 mutants killed, 0 equivalent. Gates green (1881 passed / mypy 0 / ruff clean).
- `/test re-stamp gate #3 (Atchim, fresh instance)` on 2026-09-28 at `621d735`: ✅ **PASSED WITH FINDINGS** — G-1 closed on every data route (P1/P6, mutants B1–D2 killed, 12 fix tests proven RED on `c48c516`); H-1 High non-blocking (`_fails_the_gate` ≠ row gate for `table_column`, Python-only route); 23/28 mutants killed, 2 equivalent. Gates green (1874 passed / mypy 0 / ruff clean).
- `/test re-stamp gate #2 (Atchim, fresh instance)` on 2026-09-28 at `c48c516`: ❌ **FAILED** — G-1 Critical (residual F-1 class: `wrong_format`+`format_critical` → `wrong_value` on a non-critical field; `verify_monotone` blind to date/text/name-scoped rules; `scorer_store` never runs it). F-1..F-8 verified closed; 33/36 mutants killed; 14 fix tests proven RED on `4cb7cc8`. Gates green.
- `/test re-stamp gate (Atchim, fresh instance)` on 2026-09-28 at `4cb7cc8`: ❌ **FAILED** — F-1 Critical (custom spec relaxes a critical `wrong_value` to an informational verdict; gate PASS), F-2 High (`overall_gate` row site accepts `new_table`; classify-time guard untested). 28/35 mutants killed. Gates green.
- `/test gap-fill (Atchim TDD gate)` on 2026-09-21 (re-gated 2026-09-22) at `1428520`: ✅ PASSED — FO-5 re-stamp; 104 classifier tests; drift-pin four-scenario matrix; alias-as-wrapper mutant killed. Staled by `87da307..f808bcf` (DEBT-46).
- `/implement (Atchim code review TDD gate)` on 2026-09-18 at `f90a0915`: ✅ PASSED — original S-01.1 stamp (2-round review, 76 tests). Superseded by the 2026-09-21 /test stamp.
