# /test stamp — SPEC-01 / S-01.1 (classifier & gate)

**Status:** ❌ FAILED — re-stamp gate (Wave C, DEBT-46) found 1 Critical + 1 High on the verdict path. Not stampable until F-1 and F-2 are closed with RED-then-GREEN tests and a fresh instance re-gates.
**Source:** /test re-stamp gate (Atchim, fresh instance — audit + mutation matrix; no production code edited, no tests added)
**Files:** src/idp_regression/classifier/__init__.py, src/idp_regression/classifier/canonical.py, src/idp_regression/classifier/custom.py, src/idp_regression/classifier/gate.py, src/idp_regression/classifier/registry.py, src/idp_regression/classifier/scorers.py, src/idp_regression/classifier/scoring.py, src/idp_regression/classifier/types.py — tests: tests/classifier/test_classify.py, test_classify_contract.py, test_custom_scorers.py, test_date_format.py, test_edge_matrix.py, test_gate.py, test_performance.py, test_registry.py, test_tables.py, test_validation.py
**Sequence:** git-verified (`git log --diff-filter=A`): `test_registry.py` + `test_custom_scorers.py` land in `87da307` with `registry.py`/`custom.py`/`scoring.py`/`scorers.py`; `test_date_format.py` lands in `8f4de93` with D2b; `new_table` tests in `3dd9879`; DEBT-103 tests in `f808bcf`. Co-committed per slice — consistent with the ADR-0003 co-commit waiver recorded in the previous stamp. Not a TDD violation; but see F-1/F-2: the tests that landed with the code pin what the code does, not the invariant the module docstring claims.
**Date:** 2026-09-28
**Commit (reviewed code):** `4cb7cc8` (HEAD at stamp time; moved from `0acc0c9` during the session by a docs-only commit — `git diff --stat 0acc0c9 4cb7cc8 -- src/idp_regression/classifier tests/classifier` is empty, and the working tree was `shasum`-verified identical to HEAD before and after mutation). Previous pin `1428520`; delta `1428520..4cb7cc8` on the classifier package = 8 files, +1336/−65 (`87da307` registry/scorers/custom · `5c6063a` fail-open #6 · `3dd9879` D1 `new_table` · `8f4de93` D2 per-column types + `date_format` · `f808bcf` DEBT-103 validator · `08ccd66` DEBT-09/11/63 · `9bf9062` tooling).
**Author:** alex@divinocosta.com.br  <!-- solo mode — raw git config user.email -->
**Atchim TDD gate:** ❌ REQUEST CHANGES (F-1 Critical, F-2 High; F-3..F-8 non-blocking, listed for the fix round and DEBT)
**Independence:** ✅ structural (different models) — implementer Dengoso (Opus 5.5) reviewed by Atchim (Fable 5.1), a **fresh instance** that had issued no APPROVE on any diff in `1428520..4cb7cc8` (DEBT-44). SPEC-01 is `Risk level: high` → this stamp binds regardless of the `prototype` profile; the verdict path ran at `full` per `## Rigor` row 1.
**Static:** ✅ `.venv/bin/python -m pytest -q` → **1845 passed, 15 skipped** (integration opt-in) · bare `.venv/bin/mypy` → 0 issues / 150 files (strict) · `.venv/bin/ruff check src tests scripts` → clean. Classifier suite standalone: **194 passed** in ~0.9 s. Secret scan: not re-run here (hook-gated; unchanged since `ee9770e`).

## Falsifiable freshness check (DEBT-77 — an explicit FILE LIST, never a directory)

This stamp is fresh iff the following prints nothing:

```
git diff --stat 4cb7cc8 HEAD -- \
  src/idp_regression/classifier/__init__.py \
  src/idp_regression/classifier/canonical.py \
  src/idp_regression/classifier/custom.py \
  src/idp_regression/classifier/gate.py \
  src/idp_regression/classifier/registry.py \
  src/idp_regression/classifier/scorers.py \
  src/idp_regression/classifier/scoring.py \
  src/idp_regression/classifier/types.py
```

A **new** module under `src/idp_regression/classifier/` is *outside* this gate until a stamp names it — it must not turn this check red for a non-reason (DEBT-77's trap). Surface it separately, as a scope-grew signal, with:

```
git diff --diff-filter=A --name-only 4cb7cc8 HEAD -- src/idp_regression/classifier/
```

(Because this stamp is ❌, freshness is moot for `/qa` until a ✅ re-stamp re-pins both commands.)

## Findings

**F-1 — CRITICAL — `classifier/custom.py` `ACTIONABLE_VERDICTS` / `verify_monotone`: a browser-authored spec turns a critical regression into a GREEN build.** The module's contract is *"a spec can turn a match into a failure; it can never turn a failure into a match"* — but the gate does not fail on `match` alone; it fails on `critical ∧ (missing | wrong_value)` or `format_critical ∧ wrong_format`. `ACTIONABLE_VERDICTS` permits `new_field`, `new_line`, `new_table` and `wrong_format`, all of which `overall_gate` treats as informational. Reproduced at HEAD (no mutation):
```
golden total: critical, expected 100.00 · actual 999.00 → base regression gate: FAIL
spec {when: {verdict_is: [wrong_value, missing]}, then: {verdict: new_field}}  → verify_monotone: 0 problems · gate: PASS
      …then: {verdict: new_line}   → 0 problems · PASS
      …then: {verdict: new_table}  → 0 problems · PASS
      …then: {verdict: wrong_format} → 0 problems · PASS
```
`verify_monotone` checks only "relaxes to `match`" and "clears `critical`" — the latter leg is vacuous for both shipped bases (neither ever returns a `ScoreResult`), so it proves nothing. Reachable from `--classifier <name>` via `register_custom_classifiers()` and from the console's scorer form on the unauthenticated loopback port. This is the silently-wrong GREEN `## Rigor` names as this system's worst failure. **Fix:** restrict `then.verdict` to verdicts that are *at least as gate-severe* as any base verdict they can replace — in practice `{missing, wrong_value}` only (`wrong_format` allowed only together with `format_critical: true`); and make `verify_monotone` compare **gate outcomes** per enumerated context (`critical=True` contexts included): if the base would FAIL under `overall_gate`, the compiled scorer must too. Pin with the four probes above as RED tests in `test_custom_scorers.py::TestMonotonicity`. **Regression-worthiness: PIN** (defect class: escalation-only guarantee measured against the wrong predicate).

**F-2 — HIGH — `classifier/gate.py::overall_gate` row-level FO-5 check uses the 7-set, not the 6-set.** Line 721 tests `row["verdict"] not in _VALID_VERDICTS` (includes `new_table`) where `_VALID_ROW_VERDICTS` exists precisely to exclude it. A caller-supplied map with a `new_table` row inside a **critical** detail block returns `PASS` at HEAD:
```
{"items": {"verdict":"detail","critical":True,"rows":[{"column":"qty","verdict":"new_table",…}]}} → PASS
```
`_as_row_verdict` (the classify-time guard) is the only thing between a runtime scorer and this hole — and **mutants M05/M06 (disable that guard) SURVIVED**: nothing tests it. With M06 applied, a custom scorer returning `new_table` for a table cell writes a row the gate then accepts. **Fix:** `overall_gate` row site → `_VALID_ROW_VERDICTS`; add `test_gate.py` case "a `new_table` row inside a detail block raises" (mirror of `test_unknown_row_verdict_inside_detail_raises_instead_of_passing`); add a `test_registry.py`/`test_custom_scorers.py` case with a scorer returning `new_table` on `kind="table_column"` asserting `MalformedActualError`. **PIN.**

**F-3 — MEDIUM — `custom.py`: `new_table` is an actionable verdict and a `verdict_is` condition, but it is not a leaf verdict.** `then: {verdict: new_table}` on `kind: table_column` raises `MalformedActualError` **at classify time — after the extraction is paid for** — contradicting `SpecError`'s own promise ("never at classify time"). `verdict_is: [new_table]` never matches (no base scorer returns it): a dead condition the author believes configured. Docstrings still say "the six verdicts" while `VERDICTS` is seven. Fold into F-1's fix: `parse_spec` refuses `new_table` in both `then.verdict` and `when.verdict_is`.

**F-4 — MEDIUM — `canonical.py::_format_date_with` fallback is untested (mutant M24 SURVIVED).** The scenario its docstring names — golden declares `%d/%m/%Y`, the new Action version emits ISO — has no test. At HEAD `compare_value("date","03/04/2024","2024-04-03",date_format="%d/%m/%Y") == "wrong_format"` and vs `2024-03-04` → `wrong_value` (correct); remove the fallback and the correct reading becomes `wrong_value`. Add both assertions to `test_date_format.py`.

**F-5 — MEDIUM — `gate.py::_column_type` unknown-type degrade is untested (M11 SURVIVED).** `types: {qty: "integer"}` silently compares as text (documented fail-open, "schema constrains it"). Untested, and a golden reaching `classify` without provisioning is exactly the case `_validate_declared_date_format` (DEBT-103) was added for. Either validate `types` values in `_validate_golden` like `date_format` (consistent, recommended) or pin the degrade with a test. Surface as DEBT if the team keeps the degrade.

**F-6 — LOW — `new_table` entry's `critical=False` unpinned (M08 SURVIVED).** `test_critical_is_echoed_from_golden_and_false_for_new` covers `new_field` only; extend to `new_table`.

**F-7 — LOW — `custom.py::compile_spec` "OR-ed, never assigned" for `critical` is unpinned (M30 SURVIVED)** — unobservable while no base returns a `ScoreResult`; pin with a stub base or drop the claim from the comment.

**F-8 — LOW — `gate.py::_row_affinity` ignores declared column types when tie-breaking duplicate `match_key` candidates (M12 SURVIVED)** — affects DEBT-09 pairing only; one test with `types` + duplicate keys closes it.

## Mutation matrix (35 own mutants, each aimed at a named invariant; `tests/classifier` only, `-x`; every file restored byte-identically — `shasum -a 256 -c` OK; `PYTHONDONTWRITEBYTECODE=1`)

| # | Mutant (file · symbol · change) | Invariant | Result | Killed by |
|---|---|---|---|---|
| M01 | gate.py `overall_gate` drop top-level unknown-verdict raise | FO-5 | KILLED | test_gate::test_unknown_top_level_verdict_raises_instead_of_passing |
| M02 | gate.py `overall_gate` drop row-level unknown-verdict raise | FO-5 rows | KILLED | test_gate::test_unknown_row_verdict_inside_detail_raises_instead_of_passing |
| M03 | gate.py `overall_gate` gate FAILs on critical `new_table`/`new_field` | BR3 informational | KILLED | test_gate::test_all_legitimate_top_level_verdicts_still_gate_correctly[new_field] |
| M04 | gate.py `_VALID_VERDICTS` hand-written 6 (7th forgotten) | 7-verdict gate | KILLED | …still_gate_correctly[new_table] |
| M05 | gate.py `_VALID_ROW_VERDICTS` = all 7 | Row ≠ new_table | **SURVIVED** | — (F-2) |
| M06 | gate.py `_as_row_verdict` guard removed | Row ≠ new_table | **SURVIVED** | — (F-2) |
| M07 | gate.py new_table loop `not in gtables` guard removed | D1 overwrite | KILLED | test_classify_contract::test_every_leaf_verdict_is_one_of_the_six_literals |
| M08 | gate.py new_table emitted `critical=True` | new_* critical False | **SURVIVED** | — (F-6) |
| M09 | gate.py new_table loop deleted | D1 / DEBT-05 | KILLED | test_tables::test_a_table_the_golden_does_not_have_is_reported_as_new_table |
| M10 | gate.py `_column_type` always text | D2a | KILLED | test_tables::test_a_numeric_column_compares_by_value_not_by_text |
| M11 | gate.py `_column_type` no `FIELD_TYPES` check | D2a degrade | **SURVIVED** | — (F-5) |
| M12 | gate.py `_row_affinity` ignores column types | D2a + DEBT-09 | **SURVIVED** | — (F-8) |
| M13 | gate.py validator: non-date-type check removed | DEBT-103 | KILLED | test_date_format::…refuses_a_date_format_that_cannot_do_anything[spec0] |
| M14 | gate.py validator: value-parses check removed | DEBT-103 | KILLED | …cannot_do_anything[spec1] |
| M15 | gate.py validator: blank format accepted | DEBT-103 | KILLED | test_date_format::test_a_blank_date_format_is_refused_even_on_an_empty_value |
| M16 | gate.py validator never called | DEBT-103 | KILLED | …cannot_do_anything[spec0] |
| M17 | gate.py `_declared_date_format` always None | D2b | KILLED | test_date_format::test_a_declared_day_first_format_is_honoured |
| M18 | gate.py `_classify_field` scorer critical REPLACES golden's | escalation-only | KILLED | test_classify::test_tp02_all_match_every_field_match |
| M19 | gate.py cell escalation not propagated to block | escalation-only | KILLED | test_registry::test_a_scorer_escalating_a_line_item_escalates_its_block |
| M20 | gate.py `format_critical` branch removed | DEBT-80 | KILLED | test_gate::test_wrong_format_fails_the_gate_when_the_field_is_format_critical |
| M21 | gate.py name-collision check removed | fail-open #6 | KILLED | test_validation::test_golden_field_and_table_name_collision_raises_malformed_golden |
| M22 | canonical.py `compare_value` ignores `date_format` | D2b | KILLED | test_date_format::test_a_declared_day_first_format_is_honoured |
| M23 | canonical.py `date_format` applied to VALUE tier | D2b tiers | KILLED | same |
| M24 | canonical.py `_format_date_with` no fallback | D2b vs ISO actual | **SURVIVED** | — (F-4) |
| M25 | registry.py `resolve` unknown → default | registry contract | KILLED | test_registry::test_an_unknown_name_is_refused_and_names_the_alternatives |
| M26 | registry.py pinned-file row wired to `classify` | registry contract | KILLED | test_registry::test_pinned_file_calls_empty_against_empty_an_agreement |
| M27 | scorers.py pinned-file: empty actual always match | pinned-file rule | KILLED | test_registry::test_pinned_file_still_fails_a_genuinely_lost_value |
| M28 | scorers.py regression: empty/empty match | regression byte-for-byte | KILLED | test_registry::test_regression_keeps_treating_an_empty_actual_as_missing |
| M29 | custom.py `ACTIONABLE_VERDICTS` includes match | monotone | KILLED | test_custom_scorers::TestMonotonicity::test_a_spec_cannot_relax_a_verdict_to_match |
| M30 | custom.py `compile_spec` critical assigned not OR-ed | monotone | **SURVIVED** | — (F-7) |
| M31 | custom.py `verify_monotone` relax-to-match check removed | monotone | KILLED | …test_verify_monotone_catches_a_relaxation_the_key_check_would_miss |
| M32 | custom.py base scorer not consulted | base runs first | KILLED | …test_the_goldens_critical_survives_a_rule_that_says_nothing_about_it |
| M33 | types.py `RowVerdictLiteral` gains new_table | CT-02 / INV-03 | KILLED | test_classify_contract::test_a_row_sub_verdict_can_never_be_new_table |
| M34 | gate.py `_validate_actual` prompt per-cell check removed | N22 | KILLED | test_validation::test_actual_prompt_cell_non_mapping_raises_malformed_actual |
| M35 | custom.py `confidence_below` fires on missing confidence | spec semantics | KILLED | …TestCompilation::test_a_missing_confidence_is_not_below_the_floor |

**28 / 35 killed.** All 7 survivors map to F-2, F-4, F-5, F-6, F-7, F-8 above. F-1 needed no mutant — it reproduces at HEAD.

## AC coverage (S-01.1 items, unchanged from the previous stamp and re-verified by M01–M04, M18–M21, M34; new invariants added)

| AC / invariant | Tests | Status |
|---|---|---|
| AC2 / TP-02 · AC3 / TP-03 / TP-08 · AC4 / TP-04 / EX-A1-4 · AC6 / TP-06 / EX-A1-5 · TP-07 / EX-A1-1 · BR2 · BR3 · BR8 · TP-21 / N22 · CT-02 · N2 · four types · new_line / missing rows · purity · N28 alias | as listed in the 2026-09-21 stamp (test_classify, test_gate, test_tables, test_validation, test_edge_matrix, test_classify_contract, test_performance) | ✅ COVERED (mutants M01–M04, M18, M20, M21, M34 all killed) |
| FO-5 both sites | test_gate.py:177, :184 | ✅ COVERED — but row site checks the wrong set (F-2) |
| 7-verdict `overall_gate` incl. `new_table` informational | test_gate.py:200–229 (parametrized over `get_args(VerdictLiteral)`), test_tables.py:359, :395, :422 | ✅ COVERED |
| `RowVerdictLiteral` never `new_table` — type level | test_classify_contract.py:44, :77 | ✅ COVERED |
| `RowVerdictLiteral` never `new_table` — runtime guard `_as_row_verdict` | — | ❌ MISSING (M05/M06 survived; F-2) |
| D2a per-column types | test_tables.py:497, :517, :526, :541 | ✅ COVERED (M10 killed) — degrade path untested (F-5) |
| D2b `date_format` tiers | test_date_format.py:41, :57, :68, :97, :177 | ✅ COVERED (M17, M22, M23 killed) — fallback untested (F-4) |
| DEBT-103 validator | test_date_format.py:151 (×2), :161, :167 | ✅ COVERED (M13–M16 killed) |
| Registry contract | test_registry.py:53–188, :235 | ✅ COVERED (M25–M28 killed) |
| Escalation-only (scorer → golden) | test_registry.py:267, :285, :301, :337 | ✅ COVERED (M18, M19 killed) |
| Custom spec monotone w.r.t. **the gate** | — | ❌ MISSING — only "never `match`" is tested; gate-relaxing verdicts pass (F-1) |

## Scenario A bugs (quarantined repros)
(none — no `@bug-repro` tests; F-1 and F-2 are this delta's own defects, i.e. Scenario B: back to /implement.)

## Non-blocking debt surfaced for Dunga (`/debt add`)
- F-5 `_column_type` unknown-type degrade: decide validate-vs-degrade; either way, pin it.
- F-7 `verify_monotone`'s `base_critical` leg is vacuous for both shipped bases — either give it a stub base in tests or state that it guards future bases only.
- Docstring drift in `custom.py` ("six verdicts") and `gate.py::classify_pinned_file` ("same six verdicts") since D1 made it seven.
- `custom.py`'s `_enumerate_contexts` never varies `format_critical` or `match_key`; fine today, worth a comment.

## History
- `/test re-stamp gate (Atchim, fresh instance)` on 2026-09-28 at `4cb7cc8`: ❌ **FAILED** — F-1 Critical (custom spec relaxes a critical `wrong_value` to an informational verdict; gate PASS), F-2 High (`overall_gate` row site accepts `new_table`; classify-time guard untested). 28/35 mutants killed. Gates green.
- `/test gap-fill (Atchim TDD gate)` on 2026-09-21 (re-gated 2026-09-22) at `1428520`: ✅ PASSED — FO-5 re-stamp; 104 classifier tests; drift-pin four-scenario matrix; alias-as-wrapper mutant killed. Staled by `87da307..f808bcf` (DEBT-46).
- `/implement (Atchim code review TDD gate)` on 2026-09-18 at `f90a0915`: ✅ PASSED — original S-01.1 stamp (2-round review, 76 tests). Superseded by the 2026-09-21 /test stamp.
