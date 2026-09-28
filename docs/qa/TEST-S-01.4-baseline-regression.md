# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ❌ **FAILED at `34dfe14`** — re-stamp #6, fresh gate (DEBT-44/46/72/77, Wave C).
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Date:** 2026-09-28 · **Commit:** `34dfe1423016af21ed57ebb7c95617342ecc0a80` · **Author:** alex@divinocosta.com.br (solo)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of the `prototype` profile. A `src/` fix never qualifies for `re-gate skipped`. **Profile ≠ risk level.**
**Independence (R3/DEBT-44):** run by a **new Atchim instance on Claude Fable 5.1**. This instance issued **no APPROVE and no prior verdict** on any commit in this story's history; re-stamp #5 (`3abfb25`, FAILED at `1e931fe`) was a different instance. The delta `34dfe14` carries `Co-Authored-By: Claude Sonnet 5` — a different model from the gate. ✅ structural (different models, fresh instance).
**Static:** `pytest -q` **1936 passed / 15 skipped** · `mypy src tests scripts` (strict) **0 errors / 151 files** · `ruff check src tests scripts` **clean**. No live IDP/platform call; `.env` never sourced. `PYTHONDONTWRITEBYTECODE=1` throughout; mutants applied by exact-string replace from a scratchpad harness, restored with `cp`, `shasum -a 256` byte-identical after every run; no stray `__pycache__`; `git status --short` shows only this stamp and the pre-existing `docs/state/STATE.json`.

## Verdict — FAILED. Why, in one paragraph

What the delta names is closed and mutant-verified: `ceiling_reached` now counts as no-verdict (M1/M2/M11 killed by the new real-probe test), the partition buckets are hand-pinned (M3/M5/M9 killed), `_HALT_OUTCOMES` is one definition (M8 killed), MB4 and MB5 are dead. But the delta stops exactly at the two vacuous tests stamp #5 *named* and does not do the sweep stamps #4 and #5 both asked for — and the sweep, done here, finds the class still open: **`--action required=True → False` survives all 426 orchestration tests and the full suite (MB8)**, because both "missing `--action`" tests also omit `--org`, so argparse exits 2 for a different flag whether or not the guard under test exists. That is the same defect shape as MB4/MB5, one flag over, the third time this story has fixed the reported instance instead of the class. Second, the commit message's central claim — *"a future outcome added here and left unclassified breaks a test rather than silently joining 'healthy'"* — is **false**: `ALL_TICK_OUTCOMES` is *defined* as the union of the three buckets, so the totality assertion compares the partition to itself. A new outcome returned by `check_once` and left out of every bucket passes `test_the_outcome_partition_is_total_and_disjoint` (M4b SURVIVED); M4 died only because an unrelated sweep test happens to assert `ceiling_reached` for truncation. Third, stamp #5's F-2 (headline "no new versions found" on a run where **zero** ticks produced a verdict, exit 0) is untouched, and the delta's own new test now **pins the defective headline as expected** — applying the fix (M10) is rejected by that test. At HEAD, a persistently-ambiguous endpoint under a legal budget closes with `no new versions found (40 of 40 tick(s) got no answer).` exit 0, and the degraded-detector halt never fires on it (the standing gap the commit says it recorded as DEBT — no DEBT row exists).

## Answered, per the brief

- **Eighth fail-open? NO new one at HEAD, strictly.** Every `TickResult` construction site in `check_versions.py` (`check_once` lines 463, 471, 651) uses an outcome inside `VERDICT ∪ NO_VERDICT ∪ HALT`; the three excluded constants are raised or CLI-only, as the comment says; `watch.py` consumes only `result.outcome`/`exc.outcome`. But the partition is total over **today's `elif` chain only** — the totality test cannot detect a future unclassified outcome (F-2 below), so the guard the commit relies on to stop the eighth is not a guard. And the seventh's *headline* half (stamp #5 F-2) is still open and now test-enshrined (F-3 below).
- **Does the un-fixed `consecutive_indeterminate_ticks` reset combine with the honest summary into something worse?** Yes, in exactly the way the brief predicts: at HEAD, Repro B (real `check_once`, real Protocol probe, lookahead 5/5/2, `--max-probes-per-tick 20`) and Repro C (CLI defaults, `--max-probes-per-tick 3`) both run 40 ticks of `ceiling_reached`, `detector_degraded` never fires (the reset zeroes the streak every tick), the watcher never halts, exit 0, headline `no new versions found`. The parenthesis is now honest (`40 of 40 … got no answer`) while the headline, the exit code and the halt logic all still say "fine". Repro A (pure CLI defaults) does halt — at tick 4 via `indeterminate` — so the gap is budget-dependent, which is precisely why it must be filed and is not (F-5).
- **Ninth vacuous-test survivor? YES — MB8**, `--action required=True → False` with a default, survives `tests/orchestration` (426) and both named tests in isolation (MB8b). Two tests are vacuous: `test_missing_action_exits_via_the_argparse_required_path_not_calling_run_eval` (argv `--version --run --dataset`, no `--org`) and `test_idp_action_id_env_var_is_no_longer_read_as_a_fallback` (argv `--version --run`, no `--org`, no `--dataset`). A tenth: `test_malformed_action_id_exits_nonzero_without_calling_run_eval` (bare `!= 0`, no `--dataset`) does not kill the deleted UUID guard — MB6b survives `test_cli.py` alone; the guard is held only incidentally by `tests/orchestration/test_logging_config.py::test_main_error_line_reaches_real_stderr_not_only_caplog`. And MB13 (an `IDP_ACTION_ID` env fallback re-introduced) is killed only by the static grep test, never by the behavioural test named for it. Plus MB11: `--run required` is pinned by nothing (no test omits only `--run`).

## Findings

| # | Sev | File · symbol | Scenario | Suggested fix |
|---|---|---|---|---|
| **F-1** | **High** — vacuous-sentinel class, still open after two "closures" | `tests/orchestration/test_cli.py` · `test_missing_action_exits_via_the_argparse_required_path_not_calling_run_eval` (L54) · `test_idp_action_id_env_var_is_no_longer_read_as_a_fallback` (L76) · `test_malformed_action_id_exits_nonzero_without_calling_run_eval` (L97) | **MB8** `--action` `required=True → required=False, default=<valid uuid>` **survives 426 orchestration tests and the full suite.** Both missing-`--action` tests omit `--org`, so argparse exits 2 on `--org` regardless. **MB6b** (UUID guard → `if False`) survives `test_cli.py` (42 tests) — L97 omits `--dataset` and asserts bare `!= 0`; only a logging-config test elsewhere kills it. **MB13** (env fallback) dies only to `test_static_orchestration_checks`'s grep. This is the sweep stamp #4 item 2 and stamp #5 item 3 asked for; the delta fixed the two named tests and did not run it. | Complete argv on all three (every other required flag present) and exact `== 2` for the argparse cases; `"run_eval: --action is not a valid UUID" in caplog.text` for L97. Then: **one parametrized test over `_build_parser()`'s required actions** — `[a.dest for a in parser._actions if a.required]` must equal the declared set `{org, action, version, run_name, dataset}`, and for each, drop only that flag from a complete argv and assert `== 2`. That pins the class, not the instance, and also closes MB11. |
| **F-2** | **High** — the totality guard is self-referential | `src/idp_regression/orchestration/check_versions.py` · `ALL_TICK_OUTCOMES` (L150) · `tests/orchestration/test_check_versions.py` · `test_the_outcome_partition_is_total_and_disjoint` | `ALL_TICK_OUTCOMES = VERDICT_OUTCOMES \| NO_VERDICT_OUTCOMES \| HALT_OUTCOMES`, and the test asserts `VERDICT \| NO_VERDICT \| HALT == ALL_TICK_OUTCOMES` — true by definition. **M4b**: add `OUTCOME_SWEEP_TRUNCATED` and return it from a new `elif sweep_truncated:` branch in `check_once`, classify it nowhere → the totality test **passes**. The commit message claims the opposite. M4 (same mutant, whole orchestration suite) died only because `test_sweep_truncation_is_a_visible_signal_r1` asserts `ceiling_reached` for truncation — coincidence, not the guard. Stamp #5 item 1 asked for a classification of the *whole vocabulary* against an independent source; the hand-enumerated bucket asserts do pin today's members (M3/M5/M9 killed) but the totality line pins nothing. | Derive the vocabulary independently and compare: `{v for k, v in vars(check_versions).items() if k.startswith("OUTCOME_")} - {REFUSED_UNINITIALISED, REFUSED_UNPARSEABLE_VERSION_SCHEME, SKIPPED_LOCKED} == ALL_TICK_OUTCOMES`, and separately `set(watch._OUTCOME_PHRASES) - {SKIPPED_LOCKED} == ALL_TICK_OUTCOMES` (the phrase table is the other place a new outcome must land). Delete the tautological assert or keep it as disjointness only. Verify with M4b red before the fix (P7). |
| **F-3** | **Medium** — stamp #5 F-2, unaddressed and now enshrined by test | `src/idp_regression/orchestration/watch.py` · `run_watch_loop`, `if healthy_ticks > 0:` (L596) · `tests/orchestration/test_watch.py` · `test_f1_ceiling_reached_ticks_are_no_verdict_too_not_just_indeterminate` | Repro B/C at HEAD: 40 of 40 ticks no-verdict → `Stopped after 40 tick(s). no new versions found (40 of 40 tick(s) got no answer).`, exit 0. The headline claims a verdict zero ticks produced. The new test asserts `"no new versions found (" in out` for this exact run, so **M10** (branch on `healthy_ticks - no_verdict_ticks > 0`, the fix) is **KILLED by the delta's own test** — the suite now defends the defect. The commit title says F-1..F-3 but the body and the diff address F-1 and F-3 only. | `answered_ticks = healthy_ticks - no_verdict_ticks`; branch on `answered_ticks > 0`; else `no answer -- {no_verdict_ticks} tick(s) completed without a verdict, {failed_ticks} raised`. Rewrite the new test's expectation to the `no answer --` phrase. Consider `exit_code = 1` when `answered_ticks == 0` on an interrupted run — an operator's Ctrl-C after an hour of blindness should not read as green in a shell history. |
| F-4 | Low — carried from #5 | `watch.py` · `tick_failures_exceeded` and `detected_versions` arms | Unchanged: neither reports `no_verdict_ticks`. Not fail-open (exit 1 / detection leads). | Emit the no-verdict count on every arm from the same tally. |
| F-5 | Low — process: a gap "recorded as DEBT" that is not in the register | `docs/state/DEBT.md` · (missing row) · `check_versions.py` · `check_once`, `else: consecutive_indeterminate_ticks = 0` (L590) | The commit message and the pin test's docstring both say the ceiling-reached reset is "recorded as a standing gap (DEBT)". `grep ceiling_reached docs/state/DEBT.md` finds nothing; the last row is DEBT-128. The gap is real (Repro B/C: 40 ticks blind, no halt) and budget-dependent, so it needs a register row, not a docstring. | Dunga: `/debt add` — "`check_once` resets the indeterminate streak on `ceiling_reached` even with `pending_unknowns`; a ceiling-heavy ambiguous run never reaches `detector_degraded`". Reference this stamp. |
| F-6 | Low — unpinned required flag | `cli.py` · `--run` `required=True` (L224) | **MB11** `required=False, default="nightly"` survives the full suite; no test omits only `--run`. Not A8-critical (run name is identity, not measurement) but the parser's contract is held by nothing. | Covered by F-1's parametrized required-set test. |

## Mutation matrix (R1: origin named per mutant; exact-string replace, `cp` restore, `shasum` OK after every run; `PYTHONDONTWRITEBYTECODE=1`; `-x`)

| # | File · mutant | Invariant / origin | Diff line? | Target | Result |
|---|---|---|---|---|---|
| M1 | `watch.py` `in NO_VERDICT_OUTCOMES` → `== OUTCOME_INDETERMINATE` (regress to #5) | A: every no-verdict tick counted | yes | test_watch | KILLED (`test_f1_ceiling_reached_…`) |
| M2 | `no_verdict_ticks += 1` → `pass` | A | yes | test_watch | KILLED |
| M3 | `check_versions.py` `NO_VERDICT_OUTCOMES` drops `CEILING_REACHED` | A — bucket membership | yes | tests/orchestration | KILLED (totality test, hand-enumerated bucket) |
| M4 | new `OUTCOME_SWEEP_TRUNCATED`, returned by a new `elif sweep_truncated:` branch, classified nowhere | A — "a future outcome cannot be left unclassified" | no (elif chain) | tests/orchestration | KILLED — but by `test_sweep_truncation_is_a_visible_signal_r1`, not the totality test |
| **M4b** | same mutant, run against the totality test alone | A — is the totality test a guard? | no | `test_the_outcome_partition_is_total_and_disjoint` | **SURVIVED → F-2** |
| M5 | `ALL_TICK_OUTCOMES` drops `HALT_OUTCOMES` | what the totality line does check | yes | test_check_versions | KILLED |
| M6 | priority swap: `indeterminate` before `ceiling_reached` | D7 outcome priority | no | tests/orchestration | KILLED (new pin test) |
| M7 | delete `consecutive_indeterminate_ticks = 0` reset | consecutive semantics | no | tests/orchestration | KILLED |
| M8 | `watch.py` `_HALT_OUTCOMES = frozenset()` | a halt always breaks | yes | test_watch | KILLED |
| M9 | `HALT_OUTCOMES` drops `DETECTOR_DEGRADED` | A — bucket membership | yes | tests/orchestration | KILLED (totality test) |
| **M10** | `if healthy_ticks > 0` → `if healthy_ticks - no_verdict_ticks > 0` (the F-2/#5 **fix**) | A — headline on answered, not completed, ticks | no | test_watch | **KILLED by the new test → F-3** (the suite rejects the fix) |
| M11 | count `CEILING_REACHED` only, drop `INDETERMINATE` | A | yes | test_watch | KILLED (#4's F-1 test) |
| MB4 | `cli.py` `_VERSION_PATTERN` `{1,64}` → `{1,65}` | B: C2 cap (stamp #5 F-3) | no | tests/orchestration | KILLED (`test_version_one_char_past_…`) |
| MB5 | `--version` `required=True` → `False` | B: A8 (stamp #5 F-3) | no | tests/orchestration | KILLED (`test_missing_version_…`, exact 2) |
| MB6 | UUID guard → `if False` | B: A8 | no | tests/orchestration | KILLED — only by `test_logging_config.py::test_main_error_line_reaches_real_stderr_not_only_caplog` |
| **MB6b** | same, against `test_cli.py` only | B — is the named test vacuous? | no | test_cli (42) | **SURVIVED → F-1** |
| **MB8** | `--action` `required=True` → `False` + valid default | B: A8 | no | tests/orchestration (426) + full suite | **SURVIVED → F-1** |
| MB8b | same, against the two named missing-`--action` tests | B | no | 2 tests | SURVIVED (both vacuous) |
| MB13 | `--action` default from `os.environ["IDP_ACTION_ID"]` (env fallback re-introduced) | B: A8/A9 | no | tests/orchestration | KILLED — only by the static grep (`test_no_production_code_reads_the_retired_env_var_names`); the behavioural test is vacuous |
| MB9 | `--org` required → False | B: A8 | no | tests/orchestration | KILLED |
| MB10 | `--dataset` required → False | B: A8 | no | tests/orchestration | KILLED |
| **MB11** | `--run` required → False | B: parser contract | no | tests/orchestration (426) | **SURVIVED → F-6** |
| MB12 | version guard `return 1` → `return 0` | B: a guard never exits 0 | no | tests/orchestration | KILLED |

**Equivalent mutants: none.** 23 mutant runs; 18 killed, 5 surviving, all non-equivalent (M4b, MB6b, MB8/MB8b, MB11). 15 of 23 sit on lines `34dfe14` did not change (R1). Reproductions A/B/C are not mutants: HEAD unmodified, real `check_once`, real Protocol probe (scratchpad `repro8.py`, not committed).

## Standing gaps — re-checked at `34dfe14`

1–5 from stamp #4 (`--auto-run` POC override; no backoff; no heartbeat; nothing under a loaded `launchd` schedule; `_base_kwargs` hard-codes `sweep_every_n_ticks=0`) — **all still true**. Gap 5 still matters: no watcher-loop test runs a sweep, so the `sweep_truncated` producer of `ceiling_reached` is exercised only in `check_versions` tests (stamp #5 item 4, not done).
6. Scope note (`scorer_store.py`, `version_discovery.py`, `run_artifact.py`, `facade.py` `--document`/`--classifier`) — **still not mutated by this gate**, which concentrated on the fail-open and vacuous-test families per the brief. A PASS must cover them or say so.
7. `check_versions.main`'s one-shot exit map (`_ZERO_EXIT_OUTCOMES`) treats `indeterminate` as exit 0 and `ceiling_reached` as exit 1 — the two no-verdict outcomes exit differently in the one-shot CLI. Not in scope of this delta, not a fail-open (the on-hold CI path); noted for Soneca.

## Freshness (P5, DEBT-72/77) — explicit file list pinned to `34dfe14`

`git ls-files src/idp_regression/orchestration` (14 files) equals the on-disk `*.py` listing (14); `git status --short src tests` empty; `git diff --diff-filter=A --name-only 1e931fe..HEAD -- src tests` empty (no files added since stamp #5). Blob ids are HEAD's.

| File (HEAD blob) | `1e931fe..HEAD` |
|---|---|
| `__init__.py` (e69de29b) | unchanged |
| `bootstrap.py` (1e13515d) | unchanged |
| `check_versions.py` (7057b55a) | +48 |
| `cli.py` (163e2416) | unchanged |
| `dotenv_support.py` (902d1144) | unchanged |
| `errors.py` (c9bcbfc6) | unchanged |
| `facade.py` (c4cab023) | unchanged |
| `log_sanitize.py` (50b57623) | unchanged |
| `prerun.py` (b741fba0) | unchanged |
| `run_artifact.py` (418e3d42) | unchanged |
| `run_naming.py` (dc2be96b) | unchanged |
| `scorer_store.py` (38d15122) | unchanged |
| `version_discovery.py` (de048193) | unchanged |
| `watch.py` (361649a9) | +33 −21 |

Tests changed in the delta: `tests/orchestration/test_check_versions.py` (+70), `tests/orchestration/test_cli.py` (+37 −13), `tests/orchestration/test_watch.py` (+42 −7).

## What re-stamp #7 must check

1. **F-1 as a class:** the parametrized required-set test over `_build_parser()`; MB8, MB6b, MB11 and MB13 (behavioural, with the static grep test temporarily excluded) all killed by `test_cli.py` alone.
2. **F-2:** the totality test derives the vocabulary from `vars(check_versions)` and `watch._OUTCOME_PHRASES`; M4b killed by that test alone.
3. **F-3:** M10's shape is the code; Repro B/C print `no answer --`; decide and record the exit code for an interrupted run with zero verdicts.
4. F-5 filed in `docs/state/DEBT.md`; F-4; gap 5 (a loop-level sweep test).
5. Gap 6 (unmutated modules) — cover or say so explicitly.

## History

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ FAILED | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |
| `8a01c62` (2026-09-28, re-stamp #4, stamp committed as `79315d5`) | ❌ FAILED | F-2/F-5/F-6/F-7 of `f3b0b65` **closed and mutant-verified** (M3–M8). **F-1** fail-open #6 (interleaved `indeterminate` ticks read as healthy; 30-of-40 ambiguous → "no new versions found.", exit 0) · **F-2** `if auto_run:` quota guard unpinned by 626 tests; the test named for it is vacuous · **F-3** the `AssertionError`-sentinel class in `test_cli.py` — three A8/A10 guards deletable green · F-4 summary boundary unpinned · F-5 vacuous dotenv test · F-6 P7 ordering |
| `1e931fe` (2026-09-28, re-stamp #5, stamp committed as `3abfb25`) | ❌ FAILED | F-1..F-5 of `8a01c62` **closed and mutant-verified** (MA1–MA6, MB1–MB3, MB7, MC1–MC5); F-6 filed as DEBT-128. **F-1** fail-open #7 (`ceiling_reached` is a no-verdict outcome that outranks and resets `indeterminate`; CLI defaults + legal lookahead → "no new versions found (1 of 40 …)", exit 0; MA12 survives) · **F-2** `healthy_ticks > 0` branches on completed, not answered, ticks (40/40 indeterminate → headline "no new versions found", exit 0) · **F-3** two more vacuous-sentinel tests (MB4 64-cap, MB5 `--version required`) · F-4 counts omitted on the non-fail-open arms |
| `34dfe14` (2026-09-28, this stamp, re-stamp #6) | ❌ **FAILED** | F-1 (fail-open #7) and F-3 of `1e931fe` **closed and mutant-verified** (M1–M3, M8, M9, M11, MB4, MB5). **F-1** the vacuous-sentinel class is still open — `--action required` deletable green across the full suite (MB8), two more vacuous tests plus one held only incidentally (MB6b) · **F-2** the totality test is tautological (`ALL_TICK_OUTCOMES` is the union it is compared to; M4b survives) — the commit's central guarantee does not hold · **F-3** stamp #5's F-2 untouched and now pinned as expected behaviour by the delta's own test (M10) · F-4 carried · F-5 the "recorded as DEBT" gap has no DEBT row · F-6 `--run required` unpinned (MB11) |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

**DEBT-44 / P6:** commit this FAILED stamp before the fix. Re-stamp #7 must be run by an instance that issued no APPROVE on the fix and is not this instance, on a model other than the implementer's.

— Atchim [atchim] …excuse me.
