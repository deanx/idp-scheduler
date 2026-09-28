# /test stamp — Epic E: custom-scorer wiring into the Validate (pin/verify) workflow

**Status:** ✅ PASSED WITH FINDINGS (re-gate #3) — nothing above Medium; the Medium is a narrower trigger than F-1/F-6 and is filed as debt, not a block.
**Source:** /test independent delta re-gate (Atchim, fresh instance, mutation-testing discipline) on the F-6/F-7/F-9 fix `bdedfbc`. Epic E has no SPEC file (`CLAUDE.md ## Rigor` known gap); reviewed at `full` rigor because the delta touches the classifier/orchestration seam SPEC-01 shares and the console's quota-spending route.
**Files:** scripts/verify_document.py, tests/tooling/test_batch_tools.py, docs/state/DEBT.md (this delta = `bdedfbc`); surrounding class re-mutated on unchanged lines in scripts/compare_versions.py, src/idp_regression/orchestration/scorer_store.py, src/idp_regression/classifier/custom.py (feature = six commits `5890018`, `d9b18fc`, `7bb414f`, `9f24233`, `41b7a5e`, `bdedfbc`)
**Sequence:** tests and implementation landed in the same commit (`bdedfbc`); no test-first ordering is provable from git. P7 is satisfied for the delta's new tests: M1 (revert `load_specs` → `register_custom_classifiers`) turns `test_compare_verifies_a_valid_custom_classifier_after_the_pin_half_ran` red; M3/M4 turn the F-7 parametrized test red; M2 turns `test_resolve_classifier_returns_a_valid_custom_name_unchanged` red.
**Date:** 2026-09-28
**Commit:** bdedfbcedf57b51695bb5e5a567825b41195eacc
**Author:** alex@divinocosta.com.br (solo)
**Atchim TDD gate:** PASSED — the fix closes the in-process double-registration class on every reachable path (harness `run()`, real `compare_versions.main()` in `--document-dir` AND `--zip` mode, which is what the console's `/start` subprocess runs). Two non-blocking findings below.
**Independence:** ✅ structural — implementer Dengoso on Claude Sonnet 5 (per the `bdedfbc` trailer); this gate on a **fresh Atchim instance, model Claude Fable 5.1 (`claude-fable-5-1`)**, which issued **no earlier verdict** on this feature — not gate #1's FAIL, not gate #2's FAIL, no code-review APPROVE (R3 satisfied).
**Static:** mypy strict `src tests scripts` — 0 errors (151 files) · ruff `src tests scripts` — clean · pytest FULL suite at HEAD, one process — **1970 passed / 15 skipped** (integration, opt-in), run before the first mutant and again after the last restore (same count).
**Review rules:** R1 (mutants from invariants; unchanged-line mutants M5, M6, M7, M8, M11, M15 — M5 and M11 sit on the exact invariant this fix rests on, "`load_specs` is pure / only `main()` registers") · R2 n/a (no TypedDict guard in the delta) · R3 above · P1 one gate at a time, 11 mutants applied and restored strictly sequentially, no commits · P2 restore by `cp`, all 6 copied files `shasum -a 256 -c` byte-identical against scratchpad copies taken before the first mutant · P4 `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, no `__pycache__` outside `.venv`/`frontend` · P5 `git diff --stat bdedfbc^..bdedfbc -- scripts/verify_document.py tests/tooling/test_batch_tools.py docs/state/DEBT.md` = 3 files, 114 insertions(+), 5 deletions(-) · P6 gate #2's FAILED stamp was committed (`9d632c6`) before the fix (`bdedfbc`) ✅.

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Full suite at HEAD (single process, `pytest -q`) | 1970 | 0 (15 skipped, integration) |
| Delta tests (new/changed in `bdedfbc`: F-6 test, F-7 ×4, F-9-amended F-1 test) | 6 | 0 |
| Gate probes (scratchpad, not committed — real `compare_versions.main()`, both source modes, bad name, TOCTOU) | 5 | 0 |

## The three questions this gate was asked, answered

1. **Is `load_specs()` genuinely pure?** Yes, with respect to `CLASSIFIERS` and every other module-level name. Read in full: `load_specs` → `parse_spec` (reads `CLASSIFIERS` for the shadow guard at `custom.py:266`, never writes; every other line builds a local `ScorerSpec`) → `verify_monotone` (reads `CLASSIFIERS[spec.base].scorer`, calls `_compile(spec, enforce=False)`; no cache, no `global`, no module-level dict — grepped `custom.py` for `lru_cache`/`functools`/`_cache`/`global`/module-level `= {}`: only `_FORMAT_ONLY_PAIRS`, a `Final` constant). The registry write exists in exactly one place, `scorer_store.py:115` inside `register_custom_classifiers`. **Proved by mutant, not only by reading:** M5 (make `load_specs` write the registry — the one way this fix could silently re-open F-6) is killed by the delta's F-6 test and 38 others.
2. **Does `--document-dir` mode exercise the same fixed path?** Yes — confirmed by probe, not by reading. `compare_versions.run` reaches `verify.resolve_classifier` after document discovery on a branch shared by both modes (`compare_versions.py:264`), and the real `compare_versions.main()` was driven in-process with fakes only at `idp_client.make_idp_adapter` / `facade.make_idp_adapter` / `facade.make_platform` / the pin half's `provision_golden_dataset`: `--document-dir` + `--classifier pin-rule` → **exit 0, 3 trusted + 3 candidate extractions, `custom_scorer_selected name=pin-rule`, `classifier=pin-rule` on `run_eval`'s pre-run line, 3 platform records**; identical for `--zip`. With M1 applied (the pre-fix body) the same probe fails on both modes with `SystemExit(2)` after the 3 pin-half extractions.
3. **Was the original F-6 reachable through the real subprocess `main()` path the console spawns, or only through the harness's `run(args, pin, verify)` injection?** Genuinely reachable through `main()`. `compare_versions.main` does `run(args, _load("pin_document"), _load("verify_document"))` — both scripts loaded into the SAME process, `verify.main()` called in-process after the pin half. The M1 result above (both real-`main()` probes red, `pin` calls = 3 before the crash) is the reproduction; the harness's `_FakeStage` was never the only route. So the console's `POST /api/workflows/compare/start` (→ `registry.start` → `subprocess.Popen([... compare_versions.py ...], cwd=workspace_root())`) *was* billing N and dying, and now completes. Weighted accordingly: gate #2's High was correct, and this delta closes it on the route that matters. (`/start` itself was not driven through a live server — hard limit; the argv it builds is pinned by `tests/ui/test_jobs.py` and the subprocess body is what the probe ran in-process.)

## Findings

### F-10 — Medium (non-blocking → debt). The scorer directory is read twice in one process, N extractions apart, with nothing holding the two reads to the same bytes.
- **Where:** `scripts/verify_document.py::resolve_classifier` (`load_specs()`, before the pin half) and `scripts/verify_document.py::main` (`register_custom_classifiers()` → `load_specs()` again, after the pin half) — both via `scripts/compare_versions.py::run`. The window between the two reads is the entire pin half (minutes, N extractions).
- **Scenario (reproduced by probe `test_toctou_spec_edited_between_resolve_and_verify_main`):** a valid `pin-rule` passes `resolve_classifier`; the pin half spends 3 extractions; the spec file is rewritten to `base: regression` (or deleted) before `verify.main()` runs; `_parse_args`'s `choices` are now `{pinned-file}` → `verify_document: error: argument --classifier: invalid choice: 'pin-rule'` → **uncaught `SystemExit(2)` after the spend**, no `RUN FAILED` banner. A same-name edit that keeps `base: pinned-file` does not crash but runs a spec the console never priced/approved (visible only via the `spec_digest` log line).
- **Who can hit it:** the console's `PUT /api/scorers` / `DELETE /api/scorers/{name}` have no `registry.is_busy()` guard, and the directory is hand-editable by design (it is meant to be committed). Operator-induced, bounded to N, and a different trigger from F-1/F-6 (which needed no interference) — hence Medium, not a block.
- **Suggested fix (either):** (a) thread the specs `resolve_classifier` already parsed into the registrar — e.g. `verify.main(argv, custom_specs=...)` or a `verify.register(specs)` that `compare_versions.run` calls once before `pin.main`, so `main()` registers what was validated rather than re-reading disk; or (b) have the scorer mutation routes refuse with 409 while `registry.is_busy()`. (a) also removes the double read that DEBT-136 describes.

### F-11 — Low (test gap, unchanged line). The suite cannot tell that `verify_document.main()` actually registers the named spec — only that it *offers* it.
- **Evidence:** M11 (replace `main()`'s `register_custom_classifiers()` with a `load_specs()`-based parse that never writes `CLASSIFIERS`) **survived** `tests/tooling + tests/orchestration + tests/classifier + tests/ui` (1131 passed) and is killed only by this gate's real-`main()` probe. `test_verify_main_offers_a_registered_pinned_file_custom_scorer_and_logs_its_selection` fakes `facade.run_eval`, so argparse accepts the name and the digest log fires whether or not the registry was written. Live consequence of such a regression: pin half spends N, then `run_eval` refuses `unknown_classifier` pre-run (`facade.py:553`, before any `extract`) — the F-1 class again, on the same route.
- **Fix:** in that test, assert `"pin-rule" in CLASSIFIERS` after `main()` returns (before the `finally` pop), or have the `run_eval` double call `registry.resolve(kwargs["classifier"])`.

### F-12 — Info (equivalent mutant, no action). M9 (drop the F-9 `monkeypatch.chdir(tmp_path)`) survives at HEAD because the repo root holds no `.idp-regression-scorers/`. The `chdir` is prophylactic for the day that directory is committed; correct to keep, nothing to fix.

### Confirmed correct in this delta
- `resolve_classifier` and `main()` read the same directory (`SCORER_DIR` relative to cwd; the console runs the job with `cwd=workspace_root()`), so the F-1 pre-spend check and the real registration cannot disagree on *which* files, only on *when* (F-10).
- `_pinned_file_classifier_names` remains the single source for `_parse_args`'s `choices` and `resolve_classifier`'s allowed set.
- The F-6 test's `CLASSIFIERS.pop` cleanup is load-bearing (M10 leaks `pin-rule` into the very next `verify.main` test in the same file).
- DEBT-136's correction is accurate to what gate #2 found; the underlying non-idempotency of `register_custom_classifiers` still stands for any future second caller — and F-10(a) would retire it.

## Mutation matrix

| # | Mutant (origin: invariant / declared contract) | File | Result | Killed by |
|---|---|---|---|---|
| M1 | Revert `load_specs()` → `register_custom_classifiers()` in `resolve_classifier` (INV: only `main()` registers; the F-6 fix) | scripts/verify_document.py | KILLED | test_batch_tools::test_compare_verifies_a_valid_custom_classifier_after_the_pin_half_ran + real-`main()` probes (`--document-dir`, `--zip`) |
| M2 | `custom_specs = {}` — loaded specs ignored (contract: a valid custom name resolves) | scripts/verify_document.py `resolve_classifier` | KILLED | test_resolve_classifier_returns_a_valid_custom_name_unchanged, F-6 test, probes |
| M3 | `if name and name not in allowed` → `if name not in allowed` (declared: `None` defaults) | scripts/verify_document.py `resolve_classifier` | KILLED | test_resolve_classifier_defaults_to_pinned_file[None], [""] (F-7) |
| M4 | `return name or PINNED_FILE_CLASSIFIER` → `return name` (declared default) | scripts/verify_document.py `resolve_classifier` | KILLED | same (F-7) |
| M5 | **Unchanged line (R1):** `load_specs` writes `CLASSIFIERS[parsed.name]` (INV: `load_specs` is pure — the axis this fix stands on) | src/idp_regression/orchestration/scorer_store.py:95 | KILLED | 39 failures incl. F-6 test, tests/ui TestScorers, probes |
| M6 | **Unchanged line (R1):** `register_custom_classifiers` no longer writes the registry (INV: a registered name resolves in `run_eval`) | scorer_store.py:115 | KILLED | tests/orchestration/test_cli_custom_scorers (×2) + probes |
| M7 | **Unchanged line (R1):** `parse_spec` shadow guard removed (INV: a spec may not shadow a shipped name) | src/idp_regression/classifier/custom.py:266 | KILLED | tests/classifier/test_custom_scorers::test_a_spec_may_not_shadow_a_shipped_classifier, test_cli_custom_scorers |
| M8 | **Unchanged line (R1):** pre-spend `resolve_classifier` block moved after `pin.main` (INV: refuse before any extraction; reverts F-1) | scripts/compare_versions.py `run` | KILLED | test_compare_refuses_a_bad_classifier_before_the_pin_half_spends_anything + bad-name probes |
| M9 | Drop the F-9 `monkeypatch.chdir(tmp_path)` from the F-1 test | tests/tooling/test_batch_tools.py | SURVIVED — **equivalent at HEAD** (F-12) | — |
| M10 | Drop the F-6 test's `CLASSIFIERS.pop("pin-rule")` (INV: no test leaks into the global registry) | tests/tooling/test_batch_tools.py | KILLED | test_verify_main_offers_a_registered_pinned_file_custom_scorer_and_logs_its_selection (same-process leak) |
| M11 | **Unchanged line (R1):** `main()` parses with `load_specs()` and never registers (INV: `main()` is the registrar) | scripts/verify_document.py:519 | **SURVIVED** the repo suite (1131 passed); killed only by this gate's probe | — (F-11) |
| M15 | **Unchanged line (R1):** `load_specs` skips `verify_monotone` (INV: a relaxing spec never loads via `--classifier`) | scorer_store.py:91 | KILLED | tests/orchestration/test_scorer_store::test_a_relaxing_spec_file_is_reported_not_registered |

**Equivalent mutants:** M9 only, and only at HEAD's cwd (no committed scorer directory) — reported as F-12, not counted against the delta. M11 is **not** equivalent (it changes whether `run_eval` can resolve the name; a live regression of that shape costs the pin half) — reported as F-11. No other mutant was dropped as equivalent. M13/M14 from the plan were not run: the `jobs.plan` returncode mutant is already DEBT-138 (F-8, unchanged by this delta) and re-running it adds nothing.

## Probes (not mutants) — what was actually exercised
- **Real `compare_versions.main()`**, both scripts `_load`ed and run in ONE process (the console's subprocess body), fakes only at `idp_client.make_idp_adapter` (pin half's capture), `facade.make_idp_adapter` / `facade.make_platform` (verify half's `run_eval`), and the pin half's `provision_golden_dataset` (a `FakeProvision` feeding the fake platform's dataset, borrowed from `tests/ui/test_validate_workflow_e2e.py`): valid `pin-rule` → exit 0 on `--document-dir` and `--zip`, 6 extractions in the right order, `custom_scorer_selected` + `classifier=pin-rule` logged, 3 platform records. `typo-rule` → exit 2, **zero** extractions, both modes.
- **TOCTOU** (F-10): spec rewritten to `base: regression` from inside the pin half's provision hook → `SystemExit(2)` after 3 extractions.
- **No live IDP or platform call; `.env` never sourced; no server started.**

## Debt to hand to Dunga (non-blocking)
- **F-10** (Medium): double read of the scorer directory across the pin half, no busy guard on scorer mutation routes → new DEBT row; fix (a) also closes DEBT-136.
- **F-11** (Low): suite blind to "`verify.main()` registers" (M11 survivor) → new DEBT row, or fold into DEBT-136's fix.
- Carried: DEBT-135 (`--scorer-dir` not forwarded), DEBT-137 (non-string payload → 500), DEBT-138 (F-8, `jobs.plan` returncode masked), no frontend test runner for `ValidateZipPage.tsx`.

## Freshness
`git status --short` at gate end: only `M docs/state/STATE.json` (pre-existing) plus this stamp. No `__pycache__` outside `.venv`/`frontend`. All 6 copied files (`scripts/verify_document.py`, `scripts/compare_versions.py`, `src/idp_regression/orchestration/scorer_store.py`, `src/idp_regression/classifier/custom.py`, `tests/tooling/test_batch_tools.py`, `src/idp_regression/ui/jobs.py`) restored by `cp` and `shasum -a 256 -c` verified byte-identical against the scratchpad copies taken before the first mutant; full suite re-run after the last restore: 1970 passed, same as before the first mutant.

## Next step
PASS WITH FINDINGS → `/qa` (Zangado audit) may proceed on this feature; Dunga records F-10/F-11 via `/debt add`. No fix round is required before QA — F-10 needs an operator to mutate the scorer directory during a running job, which neither prior FAILED gate's finding did.

## History
- /test independent delta re-gate #2 (Atchim, fresh instance) on 2026-09-28 at 41b7a5eabd6266c5082d37f026f5583316ee4793: ❌ FAILED — F-6 High (the F-1 fix made `resolve_classifier` a second in-process registrar; a VALID custom `--classifier` crashed with `SystemExit(2)` after the pin half spent), F-7/F-8/F-9 Low. Mutation matrix M1–M10: M6, M7, M8, M9 survived. Static clean, 1965 passed. F-6/F-7/F-9 fixed in `bdedfbc`; F-8 filed as DEBT-138.
- /test independent gate #1 (Atchim, fresh instance) on 2026-09-28 at 9f2423326126a7828cd7e548bb382c1136bbefe1: ❌ FAILED — F-1 High (bad `--classifier` refused only after the pin half spent; console `/plan` priced it as valid), F-2/F-3 Medium, F-4/F-5 Low. Mutation matrix M1–M14: M4, M5, M12 survived. Static clean, 1963 passed. Filed as DEBT-135..137 with F-1/F-5 fixed in `41b7a5e`.
