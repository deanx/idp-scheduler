# /test stamp — Epic E: custom-scorer wiring into the Validate (pin/verify) workflow

**Status:** ❌ FAILED
**Source:** /test independent gate (Atchim, fresh instance, mutation-testing discipline) — **first stamp for this feature; no prior stamp exists** (Epic E has no SPEC file, see `CLAUDE.md ## Rigor` known gap; reviewed at `full` rigor because the delta touches the classifier/orchestration seam SPEC-01 shares and the console's quota-spending route).
**Files:** scripts/verify_document.py, scripts/compare_versions.py, src/idp_regression/ui/server.py, src/idp_regression/ui/jobs.py, src/idp_regression/ui/api.py, frontend/src/api.ts, frontend/src/pages/ValidateZipPage.tsx, tests/tooling/test_batch_tools.py, tests/ui/test_validate_workflow_e2e.py, tests/ui/test_server.py, tests/ui/test_api.py, tests/ui/test_jobs.py
**Sequence:** tests and implementation landed in the same commit for each of the four commits (`5890018`, `d9b18fc`, `7bb414f`, `9f24233`); no test-first ordering is provable from git. Each commit message reports its own mutants killed; this gate re-ran them and added its own (below).
**Date:** 2026-09-28
**Commit:** 9f2423326126a7828cd7e548bb382c1136bbefe1
**Author:** alex@deanx.com.br (solo)
**Atchim TDD gate:** FAILED (F-1 High, blocking on this repo's own "refuse before any extraction" rule)
**Independence:** ✅ structural — implementer Dengoso on Claude Sonnet 5 (per the four commit trailers); this gate on a **fresh Atchim instance, model Claude Fable 5.1 (`claude-fable-5-1`)**, which issued **no earlier verdict** on this diff (R3 satisfied).
**Static:** mypy strict `src tests scripts` — 0 errors (151 files) · ruff `src tests scripts` — clean · `frontend: npm run build` (tsc + vite) — clean · pytest — **1963 passed / 15 skipped** (integration, opt-in).
**Review rules:** R1 (mutants from invariants, ≥1 on an unchanged line: M13, M14) · R2 n/a (no TypedDict guard in the delta) · R3 above · P1 one gate at a time · P2 restore by `cp`, shasum-verified byte-identical for all 7 mutated files · P4 `PYTHONDONTWRITEBYTECODE=1`, no stray `__pycache__` · P5 file list above, `git diff --stat 5890018^..HEAD -- <files>` = 12 files changed, 317 insertions(+), 10 deletions(-).

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Full suite at HEAD | 1963 | 0 (15 skipped, integration) |
| Feature tests (new in the four commits) | 12 | 0 |

## Findings

### F-1 — High (blocking). A wrong `--classifier` is refused only AFTER the pin half has spent N extractions; `/api/workflows/compare/plan` prices it as valid.
- **Where:** `scripts/compare_versions.py::run` (pin half at `pin.main(pin_argv)` runs before `verify.main(verify_argv)` parses `--classifier`); `src/idp_regression/ui/api.py::post_compare_plan` / `post_compare_start` (plan validates nothing about the name beyond `jobs.SAFE_VALUE` grammar).
- **Scenario (reproduced, fake pin + REAL `verify_document` module, `run_eval` patched to assert-never):** `compare_versions.run(classifier="reg-rule")` (a loaded spec with `base: regression`) and `classifier="not-registered"` → **pin half called once (N extractions spent)**, then `verify_document: error: argument --classifier: invalid choice` → `SystemExit(2)` → RUN FAILED. Through the console: `POST /api/workflows/compare/plan` with `classifier: "reg-rule"` or `"not-registered"` → **200, planned_extractions=2**; `/start` echoing that count would bill the pin half and then fail.
- **Why it matters here:** the commit message for `5890018` states the wrong-base/misspelled name is "refused by argparse itself, before any extraction". True for `verify_document.py` invoked directly; **false for `compare_versions.py` and therefore for the console**, the one surface a non-engineer uses. This is the "reach IDP, spend on every document, only then fail" class that `/api/preflight` (T4) was BLOCKED for on 2026-09-25. Fail-closed on the verdict (never a wrong GREEN), but a paid-for RUN FAILED on a route whose whole design is "the number on screen is the cost".
- **Fix:** `compare_versions.py` must validate the verify argv **at plan time and before the pin half** — e.g. call `register_custom_classifiers()` and `verify._parse_args(verify_argv, custom_specs=…)` (or a shared `resolve_pinned_file_classifier(name)` helper) before `confirm_cost`, so `--plan` refuses with exit 2 and the console's `/plan` becomes a 422. Test: `compare_versions.run(..., classifier="regression-based")` with a recording pin → **pin never called**, and `--plan` exits non-zero. Write the test first and see it red (P7).

### F-2 — Medium. `--scorer-dir` (console) is not forwarded to the job: an explicit `--scorer-dir` re-opens the mismatch `d9b18fc` closed.
- **Where:** `src/idp_regression/ui/server.py::main` (`scorer_dir=args.scorer_dir or workspace.scorer_dir()`), `src/idp_regression/ui/jobs.py::build_compare_argv` (no scorer-dir argument; the child's `register_custom_classifiers()` reads `<cwd=workspace>/.idp-regression-scorers`).
- **Scenario (reproduced):** `python -m idp_regression.ui --workspace <ws> --scorer-dir /elsewhere/scorers` → `create_app` receives `/elsewhere/scorers` (explicit flag wins, confirmed), the picker lists specs from `/elsewhere`, the job's `verify_document.py` lists only `<ws>/.idp-regression-scorers` → a picked name is refused at verify time — and, via F-1, after the pin half has been paid for.
- **Fix:** either drop `--scorer-dir` from the console (workspace is the single answer, per `workspace.py`'s own contract) or thread it through `build_compare_argv` as `--scorer-dir` on `compare_versions.py`/`verify_document.py` (which do not have such a flag today). Mutant **M12 survived**: `tests/ui/test_server.py` never passes `--scorer-dir`, so "explicit flag wins" is unproven by test.

### F-3 — Medium. `register_custom_classifiers()` is not idempotent; a second call in one process makes every custom spec disappear from `--classifier` choices with a misleading reason.
- **Where:** `src/idp_regression/orchestration/scorer_store.py::register_custom_classifiers` → `custom.parse_spec` (`name not in CLASSIFIERS`). Unchanged by this delta, but the delta adds a second caller (`verify_document.main`) alongside `cli.py`.
- **Scenario (reproduced):** call `register_custom_classifiers()` twice in one process → 1st: `['pin-rule','reg-rule']`; 2nd: `[]` with errors `"'pin-rule' is already a shipped classifier"`. Any in-process harness that invokes `verify_document.main` more than once (e.g. a future `golden_pipeline`-style stage, or a test that loads the real script twice) silently loses custom choices.
- **Fix:** `register_custom_classifiers` should treat a name already registered **from the same spec digest** as a re-register (or `load_specs` should check against shipped names only, not the mutated `CLASSIFIERS`). Surface as debt if not fixed in this round.

### F-4 — Low. Non-string `classifier` (and `glob`) in the compare payload is a 500, not a 422.
- **Where:** `src/idp_regression/ui/api.py::_compare_argv` — every other field is wrapped in `str(...)`; `classifier=payload.get("classifier") or None` is not, and `jobs._validate` does `pattern.match(123)` → `TypeError`. Reproduced: `classifier: 123` and `classifier: ["x"]` → **500**. Fail-closed (nothing runs), pre-existing class (`glob` has the same shape), but a loopback API answering 500 to a malformed body is noise in the one place the operator reads.
- **Fix:** `str(payload.get("classifier") or "") or None`, and the same for `glob`.

### F-5 — Low (test gaps, from surviving mutants). `verify_document.main()`'s wiring is untested.
- **M4 survived:** `args = _parse_args(argv, custom_specs=custom_specs)` → `_parse_args(argv)` — the parser is built with NO registered specs and every test still passes, because the tests call `_parse_args(custom_specs=…)` directly. A real custom name would be refused end to end. **M5 survived:** the `custom_scorer_selected name=… base=… spec_digest=…` run-identity line can be deleted with no test failing. This gate confirmed the line DOES fire for a disk-loaded spec (`custom_scorer_selected name=pin-rule base=pinned-file spec_digest=54892b247e2a`), but it is proved by a probe, not a test. Add one test driving `verify_document.main([...,"--classifier","<disk spec>"])` in a tmp cwd with `facade.run_eval` patched, asserting both the parser accepts the name and the log record.
- Also note: the run-identity line is logged **before** `run()` refuses (e.g. no pins) — a "selected" without a run. Cosmetic.

### Frontend
- `frontend/src/pages/ValidateZipPage.tsx` picker is a `<select>` filtered to `name === "pinned-file" || base === "pinned-file"`; `/api/scorers` was verified to return `base` on custom entries and no `base` on shipped ones, so `regression` is correctly excluded. No test runner covers this file; the filter is documented as courtesy, not control — **but per F-1 the control it defers to is `verify_document.py`'s parser, which sits after the spend.** Not mutated (no runner).

## Mutation matrix

| # | Mutant (origin: invariant / declared contract) | File | Result | Killed by |
|---|---|---|---|---|
| M1 | Widen `choices` to every custom base (INV: regression-based spec never offered to verify) | scripts/verify_document.py `_parse_args` | KILLED | test_batch_tools::test_verify_parser_only_offers_pinned_file_based_custom_classifiers |
| M2 | Drop forwarding: always shipped `pinned-file` | verify_document.py `run` | KILLED | test_verify_forwards_a_named_classifier_to_run_eval |
| M3 | Default flips to `regression` (INV: per-file pin reads empty-vs-empty as agreement) | verify_document.py `run` | KILLED | test_verify_uses_the_default_pinned_file_classifier_when_none_named |
| M4 | Parser built without registered specs (`main` wiring) | verify_document.py `main` | **SURVIVED** | — (F-5) |
| M5 | Run-identity log never fires (INV-04 reasoning) | verify_document.py `main` | **SURVIVED** | — (F-5) |
| M6 | Drop forwarding to the verify half | scripts/compare_versions.py `run` | KILLED | test_compare_forwards_classifier_to_the_verify_half_only |
| M7 | ALSO forward to the pin half (INV: pin never classifies) | compare_versions.py `run` | KILLED | same |
| M8 | `classifier` skips `SAFE_VALUE` grammar (INV: every argv value validated, ADR-0004 grammar) | src/idp_regression/ui/jobs.py `build_compare_argv` | KILLED | test_jobs::test_a_classifier_that_is_not_the_expected_grammar_is_refused ×3 |
| M9 | `""` appended as `--classifier ""` | jobs.py | KILLED | test_an_empty_classifier_is_treated_as_none_named |
| M10 | `_compare_argv` drops `payload["classifier"]` | src/idp_regression/ui/api.py | KILLED | test_api::test_the_classifier_field_reaches_build_compare_argv |
| M11 | Revert `d9b18fc` (bare-relative default) | src/idp_regression/ui/server.py | KILLED | test_server::test_the_default_scorer_dir_follows_the_workspace_not_the_servers_own_cwd |
| M12 | Explicit `--scorer-dir` ignored | server.py | **SURVIVED** | — (F-2; behaviour verified by probe, not test) |
| M13 | **Unchanged line (R1):** `load_specs` skips `verify_monotone` (INV: a spec can never turn a failure green) | src/idp_regression/orchestration/scorer_store.py | KILLED | test_scorer_store / test_cli_custom_scorers |
| M14 | **Unchanged line (R1):** `parse_spec` drops the shipped-name shadow guard (INV: `--classifier regression` always means the shipped one) | src/idp_regression/classifier/custom.py | KILLED | test_custom_scorers |

**Equivalent mutants:** none of the 14. M4 and M5 are behaviour-changing (a real custom name is refused end to end / the run-identity line disappears) and simply untested; M12 changes which directory the console reads when the flag is passed.

## Probes (not mutants) — what was actually exercised
- **Subprocess boundary:** `scripts/verify_document.py --help` run with `cwd=<ws>` lists `{pinned-file,pin-rule}` (a `base: regression` spec `reg-rule` in the same directory is NOT offered); `create_app(scorer_dir=workspace.scorer_dir())` after `set_workspace(<ws>)` reports `scorer_dir=<ws>/.idp-regression-scorers` and lists both `pin-rule (pinned-file)` and `reg-rule (regression)`. Same directory both sides — `d9b18fc` holds for the default path. `create_app()` with no argument still reports the bare-relative `.idp-regression-scorers` (only `server.main` applies the fix; acceptable since it is the sole production constructor).
- **Regression-based scorer reaching pin/verify by ANY path:** `--document-dir` vs `--zip` mode makes no difference (classifier is parsed identically); a spec named `pinned-file`/`regression` is refused by `parse_spec` (M14 killed); the console's `/plan` and `/start` run the same `_compare_argv` and neither validates the name; editing the spec between `/plan` and `/start` changes nothing because neither validates it (F-1). **Answer: it cannot reach the VERIFY classifier — `verify_document.py`'s parser is the last word and it holds — but it CAN reach the pin half's spend, and the console's plan prices it as valid.**
- **Network client bypassing the `<select>`:** protected against argv injection by `jobs.SAFE_VALUE` (M8 killed; `"a b"` → 422). Semantics are NOT protected until verify's parser (F-1). Non-string → 500 (F-4).

## Debt to hand to Dunga (non-blocking)
- F-3 (register idempotency), F-4 (non-string payload → 500), F-5 (M4/M5 test gaps), M12 test gap, and the absence of any frontend test runner for `ValidateZipPage.tsx`.

## Freshness
`git status --short` at gate end: only `M docs/state/STATE.json` (pre-existing) plus this stamp. `frontend/dist/` and `frontend/node_modules/` are gitignored and unstaged; `npm run build` rewrote `frontend/dist` only. No `__pycache__` outside `.venv`/`frontend`. All 7 mutated source files restored and shasum-verified against the scratchpad copies.

## Next step
FAIL → back to Dengoso: fix F-1 (test first, P7), decide F-2 (drop or thread `--scorer-dir`), commit this FAILED stamp before the fix (P6), then re-run this gate on a fresh instance.
