# /test stamp — Epic E: custom-scorer wiring into the Validate (pin/verify) workflow

**Status:** ❌ FAILED (re-gate #2)
**Source:** /test independent delta re-gate (Atchim, fresh instance, mutation-testing discipline) on the F-1/F-5 fix `41b7a5e`. Epic E has no SPEC file (`CLAUDE.md ## Rigor` known gap); reviewed at `full` rigor because the delta touches the classifier/orchestration seam SPEC-01 shares and the console's quota-spending route.
**Files:** scripts/verify_document.py, scripts/compare_versions.py, src/idp_regression/ui/server.py, src/idp_regression/ui/jobs.py, src/idp_regression/ui/api.py, frontend/src/api.ts, frontend/src/pages/ValidateZipPage.tsx, tests/tooling/test_batch_tools.py, tests/ui/test_validate_workflow_e2e.py, tests/ui/test_server.py, tests/ui/test_api.py, tests/ui/test_jobs.py, docs/state/DEBT.md (feature = five commits `5890018`, `d9b18fc`, `7bb414f`, `9f24233`, `41b7a5e`)
**Sequence:** tests and implementation landed in the same commit for all five commits; no test-first ordering is provable from git. P7 is satisfied for the two new tests in `41b7a5e`: M1 (revert the pre-spend check) and M4/M5 (main wiring) each turn the corresponding new test red.
**Date:** 2026-09-28
**Commit:** 41b7a5eabd6266c5082d37f026f5583316ee4793
**Author:** alex@deanx.com.br (solo)
**Atchim TDD gate:** FAILED — F-6 High (blocking): the F-1 fix regresses the feature's own valid-name path into the same "spend, then fail" class F-1 named.
**Independence:** ✅ structural — implementer Dengoso on Claude Sonnet 5 (per the `41b7a5e` trailer); this gate on a **fresh Atchim instance, model Claude Fable 5.1 (`claude-fable-5-1`)**, which issued **no earlier verdict** on this diff — neither the first gate's FAIL nor any code-review APPROVE (R3 satisfied).
**Static:** mypy strict `src tests scripts` — 0 errors (151 files) · ruff `src tests scripts` — clean · pytest FULL suite at HEAD, one process — **1965 passed / 15 skipped** (integration, opt-in).
**Review rules:** R1 (mutants from invariants; unchanged-line mutants M8, M9 — and M8 is the one that exposes the blind spot behind F-6) · R2 n/a (no TypedDict guard in the delta) · R3 above · P1 one gate at a time, mutants applied and restored sequentially · P2 restore by `cp`, all 7 mutated files shasum-verified byte-identical against scratchpad copies · P4 `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, no stray `__pycache__` outside `.venv`/`frontend` · P5 file list above, `git diff --stat 5890018^..HEAD -- <files>` = 13 files changed, 455 insertions(+), 10 deletions(-) · P6 the first FAILED stamp was committed (`2de3f86`) before the fix (`41b7a5e`) ✅.

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Full suite at HEAD (single process, `pytest -q`) | 1965 | 0 (15 skipped, integration) |
| Feature tests (new in the five commits) | 14 | 0 |

The full suite passes together. The previous gate's cross-file leak (`pin-rule` into `tests/ui/test_api.py::TestScorers`) is closed by the `CLASSIFIERS.pop` cleanup — and M10 proves that cleanup is load-bearing, not decorative.

## Findings

### F-6 — High (blocking). The F-1 fix regresses the VALID custom-scorer path: through `compare_versions.py` (and therefore the console), a correctly registered `pinned-file`-based scorer now passes the pre-spend check, the pin half spends N extractions, and then the verify half refuses the same name with an uncaught `SystemExit(2)`.
- **Where:** `scripts/verify_document.py::resolve_classifier` (calls `register_custom_classifiers()`, which mutates the global `CLASSIFIERS`) → `scripts/compare_versions.py::run` (`pin.main()` spends) → `scripts/verify_document.py::main` (calls `register_custom_classifiers()` a SECOND time in the same process: `compare_versions.main` loads both scripts with `_load()` and calls `verify.main(verify_argv)` in-process) → `scorer_store.load_specs` → `custom.parse_spec` refuses `'pin-rule' is already a shipped classifier` because the first call already put it in `CLASSIFIERS` → `custom_specs == {}` → `_parse_args` builds `--classifier {pinned-file}` only → argparse `error: invalid choice: 'pin-rule'` → `SystemExit(2)` propagates out of `run()` (nothing catches it), so no `RUN FAILED` banner, no summary.
- **Reproduced (real `verify_document` module, recording pin, spec on disk in cwd, `facade.run_eval` patched to return 0, `--document-dir` mode = the console's mode):**
  - at `41b7a5e`: `custom_scorer_not_loaded detail="pin-rule.json: 'pin-rule' is already a shipped classifier …"` → `verify_document: error: argument --classifier: invalid choice: 'pin-rule' (choose from pinned-file)` → **`SystemExit 2`, pin calls (extractions already spent): 1**.
  - at `41b7a5e^` (same probe, parent's two scripts): **exit 0, pin calls 1, `STILL VALID`** — the valid path worked before this commit. This is a regression introduced by the fix, not a pre-existing gap.
  - subprocess `compare_versions.py --plan --classifier pin-rule` (both `--zip` and `--document-dir`): **exit 0** — so the console's `POST /api/workflows/compare/plan` prices a valid custom scorer at 2N and `/start` bills N and dies. The bad names (`bad`, `reg-rule`) are correctly refused at `--plan` with exit 2 on both source modes (F-1 is fixed for the bad-name case; the console's `/plan` becomes a 422 through `jobs.plan`'s non-zero-returncode refusal).
- **Why this is worse than F-1:** F-1 spent N on a typo. F-6 spends N on the feature working as designed — the only classifier name the Validate wizard's `<select>` can offer is exactly the one that crashes after the spend. `DEBT-136`'s text ("Not a live defect today — both calls land the same specs the second time") is factually wrong: the first gate's own F-3 reproduction recorded that the second call returns `[]` with errors. F-3 was a live gate defect the moment `resolve_classifier` became a second caller in the same process.
- **Fix (either closes it; (a) is the cleaner):** (a) `resolve_classifier` must not mutate the registry — a pre-spend *check* should read `scorer_store.load_specs()` (pure, no `CLASSIFIERS` write) to compute the allowed names, leaving `verify.main()` as the single registrar; or (b) make `register_custom_classifiers` idempotent (DEBT-136's fix: treat a name already registered from the same spec digest as a re-register, or have `parse_spec`'s shadow guard check `BASE_CLASSIFIERS`/the shipped set rather than the mutated `CLASSIFIERS` — note M8 below shows the suite is currently blind to either choice). **Test first (P7):** the probe above as a test in `tests/tooling/test_batch_tools.py` — real `verify_document` module, a `pinned-file` spec on disk, recording pin, `facade.run_eval` patched to capture `classifier=`; assert `exit == 0`, `pin.calls == [one argv]`, and `run_eval` received `classifier="pin-rule"`. It is red at HEAD. Pop `pin-rule` in `finally`.

### F-7 — Low (test gap, from surviving mutants). `resolve_classifier`'s return value and its `None` leg are unexercised.
- **M6** (`return name or PINNED_FILE_CLASSIFIER` → `return name`) and **M7** (`if name and name not in allowed` → `if name not in allowed`) both survive `tests/tooling`: `compare_versions.run` guards the call with `if args.classifier:` and discards the return, and `_FakeStage.resolve_classifier` is a stub. Not a live defect (no caller reaches either leg); the docstring promises a default that nothing pins. Either drop the return value and the `None` acceptance from the contract, or test them.

### F-8 — Low (test gap, unchanged line). `jobs.plan`'s non-zero-returncode refusal is untested (M9 survived `tests/ui`).
- **Where:** `src/idp_regression/ui/jobs.py::plan` — `if completed.returncode != 0: raise JobRejectedError(...)`. Deleting it fails nothing. For THIS delta's refusal it is masked (the classifier refusal is printed before any `extraction(s)` line, so the "did not report an extraction count" branch still refuses), but the console's `/plan` → 422 contract for a refused plan is carried by an untested branch. `tests/ui/test_jobs.py` fakes `subprocess.run` with returncode 0 only.

### F-9 — Low (test hygiene). Two state leaks adjacent to the one this delta fixed.
- `test_compare_refuses_a_bad_classifier_before_the_pin_half_spends_anything` calls the REAL `resolve_classifier` with cwd = the pytest cwd (repo root) and no `CLASSIFIERS` cleanup. Today the repo root has no `.idp-regression-scorers/`, so nothing registers — but `CLAUDE.md` says that directory is *meant to be committed*; the day it is, this test registers every real spec into the global registry with no pop, the exact leak M10 reproduces. `monkeypatch.chdir(tmp_path)` closes it.
- `test_verify_main_offers_a_registered_pinned_file_custom_scorer_and_logs_its_selection` reaches `verify.run`, which sets `os.environ["IDP_DOCUMENT_DIR"]` with no `monkeypatch.delenv` — the same shape the neighbouring `verify_document.main` tests (lines ~3579/3585) already have, so pre-existing class, not new; `sys.modules` is untouched (scripts are loaded via `importlib` under their own names) and `scorer_store` holds no module-level mutable state beyond `CLASSIFIERS` itself.

### Confirmed correct in this delta
- The bad-name refusal is reachable before any spend on every path that sets `args.classifier`: `compare_versions.run` places the check after document discovery and before the PLAN print, identically for `--zip` and `--document-dir`, so `--plan` (and therefore the console's `/plan`, which runs the real `--plan` as a subprocess and maps a non-zero exit to 422) refuses with exit 2 and `/start` never reaches `registry.start`. `_pinned_file_classifier_names` is the single source for both `_parse_args`'s `choices` and `resolve_classifier` (M3 kills through the parser test; the shared helper cannot drift).
- The `CLASSIFIERS.pop` cleanup in the F-5 test is proven load-bearing (M10: removing it fails `tests/ui/test_api.py::TestScorers::test_the_shipped_classifiers_are_listed_with_the_vocabulary` in a full-suite run).

## Mutation matrix

| # | Mutant (origin: invariant / declared contract) | File | Result | Killed by |
|---|---|---|---|---|
| M1 | Delete the pre-spend `resolve_classifier` block (INV: refuse before any extraction; reverts the F-1 fix) | scripts/compare_versions.py `run` | KILLED | test_batch_tools::test_compare_refuses_a_bad_classifier_before_the_pin_half_spends_anything |
| M2 | `resolve_classifier` never raises (`if False:`) | scripts/verify_document.py `resolve_classifier` | KILLED | same |
| M3 | `_pinned_file_classifier_names` drops the `base == "pinned-file"` filter (INV: a regression-based spec is never offered to verify) | verify_document.py | KILLED | test_verify_parser_only_offers_pinned_file_based_custom_classifiers |
| M4 | `main` builds the parser without registered specs | verify_document.py `main` | KILLED | test_verify_main_offers_a_registered_pinned_file_custom_scorer_and_logs_its_selection |
| M5 | `custom_scorer_selected` run-identity log never fires (INV-04 reasoning) | verify_document.py `main` | KILLED | same |
| M6 | `return name or PINNED_FILE_CLASSIFIER` → `return name` (declared default) | verify_document.py `resolve_classifier` | **SURVIVED** | — (F-7) |
| M7 | `None` refused instead of defaulted (declared contract) | verify_document.py `resolve_classifier` | **SURVIVED** | — (F-7) |
| M8 | **Unchanged line (R1):** `parse_spec` shadow guard checks `BASE_CLASSIFIERS` instead of the mutated `CLASSIFIERS` (INV: `--classifier my-rule` resolves the same way on the second registration in one process — the axis F-6 lives on) | src/idp_regression/classifier/custom.py | **SURVIVED** (1179 tests across classifier/orchestration/tooling/ui) | — the suite cannot distinguish the broken second-registration from a fixed one, in either direction |
| M9 | **Unchanged line (R1):** `jobs.plan` ignores a non-zero `--plan` exit (INV: a refused plan is a 422, never a priced job) | src/idp_regression/ui/jobs.py `plan` | **SURVIVED** | — (F-8) |
| M10 | Remove the F-5 test's `CLASSIFIERS.pop("pin-rule")` (INV: no test leaks into the global registry) | tests/tooling/test_batch_tools.py | KILLED (full suite) | tests/ui/test_api.py::TestScorers::test_the_shipped_classifiers_are_listed_with_the_vocabulary |

**Equivalent mutants:** M6 and M7 are equivalent *with respect to every current caller* (the return value is discarded and `None` is never passed), but not equivalent to the function's own documented contract — reported as F-7 rather than counted as killed. M8 is **not** equivalent: it changes what `verify.main()` offers after `resolve_classifier` has run (it is, in fact, one of the two candidate fixes for F-6), and the suite is blind to it. M9 is not equivalent (it changes `/plan`'s refusal contract). No mutant was dropped as equivalent.

## Probes (not mutants) — what was actually exercised
- **Valid name through `compare_versions.run` with the real verify module:** HEAD → spend-then-`SystemExit(2)`; parent commit → exit 0 (F-6, above).
- **Subprocess `compare_versions.py --plan`** in a tmp cwd holding `pin-rule` (`base: pinned-file`) and `reg-rule` (`base: regression`): `bad` → exit 2, `reg-rule` → exit 2, `pin-rule` → exit 0; identical for `--document-dir` and `--zip`. Every refusal names the allowed set (`pin-rule, pinned-file`).
- **Console reachability:** `/api/workflows/compare/plan` and `/start` both go through `_compare_argv` → `jobs.build_compare_argv` (grammar only) → `jobs.plan` (real `--plan` subprocess, `cwd=workspace_root()`), so the pre-spend check is reached on both routes for a bad name. Neither route was driven through a live server (hard limit); `jobs.plan`'s refusal mapping was read, not tested (F-8).
- **No live IDP or platform call; `.env` never sourced; no server started.**

## Debt to hand to Dunga (non-blocking)
- **Correct DEBT-136's text**: it is a live gate defect, not "fragile shape" — fold it into the F-6 fix or re-open it as High.
- F-7 (M6/M7 contract gap), F-8 (M9 `jobs.plan` refusal untested), F-9 (two test-hygiene leaks), and — carried from the first gate — DEBT-135 (`--scorer-dir` not forwarded; M12 there), DEBT-137 (non-string payload → 500), no frontend test runner for `ValidateZipPage.tsx`.

## Freshness
`git status --short` at gate end: only `M docs/state/STATE.json` (pre-existing) plus this stamp. No `__pycache__` outside `.venv`/`frontend`. All 7 mutated files (`verify_document.py`, `compare_versions.py`, `scorer_store.py`, `custom.py`, `api.py`, `jobs.py`, `test_batch_tools.py`) restored by `cp` and `shasum -c` verified byte-identical against the scratchpad copies taken before the first mutant.

## Next step
FAIL → back to Dengoso: write the valid-name-through-`compare_versions` test first and see it red (P7), fix F-6 by making the pre-spend check read-only (`load_specs`) or the registration idempotent, commit this FAILED stamp before the fix (P6), then re-run this gate on a fresh instance (R3).

## History
- /test independent gate (Atchim, fresh instance) on 2026-09-28 at 9f2423326126a7828cd7e548bb382c1136bbefe1: ❌ FAILED — F-1 High (bad `--classifier` refused only after the pin half spent; console `/plan` priced it as valid), F-2/F-3 Medium, F-4/F-5 Low. Mutation matrix M1–M14: M4, M5, M12 survived. Static clean, 1963 passed. Filed as DEBT-135..137 with F-1/F-5 fixed in `41b7a5e`.
