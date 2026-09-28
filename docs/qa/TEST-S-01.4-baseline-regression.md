# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ❌ **FAILED at `1e931fe`** — re-stamp #5, fresh gate (DEBT-44/46/72/77, Wave C).
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Date:** 2026-09-28 · **Commit:** `1e931fed7c46cfc548a842e683ca8965a91b5c92` · **Author:** alex@divinocosta.com.br (solo)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of the `prototype` profile. A `src/` fix never qualifies for `re-gate skipped`. **Profile ≠ risk level.**
**Independence (R3/DEBT-44):** run by a **new Atchim instance on Claude Fable 5.1**. This instance issued **no APPROVE and no prior verdict** on any commit in this story's history; re-stamp #4 (`79315d5`) was a different instance. The delta `1e931fe` carries `Co-Authored-By: Claude Sonnet 5` — a different model from the gate. ✅ structural (different models, fresh instance).
**Static:** `pytest -q` **1933 passed / 15 skipped** · `mypy src tests scripts` (strict) **0 errors / 151 files** · `ruff check src tests scripts` **clean**; reintroducing `assert` at `facade.py:846` makes ruff report **`S101 Use of assert detected`** (demonstrated, restored, `shasum` OK) · `python -O -m pytest tests/orchestration` **423 passed** (the assert-vs-raise axis holds under `-O`). No live IDP/platform call; `.env` never sourced. `PYTHONDONTWRITEBYTECODE=1` throughout; no stray `__pycache__`.

## Verdict — FAILED. Why, in one paragraph

Everything the delta *names* is closed and mutant-verified: the cumulative `indeterminate_ticks` tally is genuinely cumulative (re-adding a reset is killed, MA3), the F-2 spy distinguishes `auto_run`'s guard from the `max_runs_per_tick` default-0 gate (MC1/MC2/MC5 all killed independently), the three F-3 survivors and F-5 die on their own log lines (MB1–MB3, MB7), and the F-4 boundary is pinned (MA6). But re-stamp #4's checklist item 1 landed **again**, through the real `check_once`: **`ceiling_reached` is the seventh fail-open.** It is a no-verdict outcome by the design's own test (`test_budget_exhausted_with_no_hits_at_all_is_ceiling_reached`, "unknown beyond here"), it *outranks* `indeterminate` in `check_once`'s priority order, it *resets* `consecutive_indeterminate_ticks`, and the loop does not count it. With the watcher CLI's **default** probe budget and a legal lookahead, an endpoint answering UNKNOWN on every candidate closes with `no new versions found (1 of 40 tick(s) got no answer)`, exit 0 — 39 truncated, ambiguous ticks unaccounted for and the degraded-detector halt never fires. The fix closed the reported outcome (`indeterminate`) rather than the invariant (*every outcome is classified verdict / no-verdict / halt*), which is exactly what stamp #4's carry-forward item 1 asked for and R1 forbids. And the vacuous-sentinel class has **two** more members than F-3 named: a `{1,64}`→`{1,65}` cap mutant and a `--version required=False` mutant both survive all 423 orchestration tests.

## Findings

| # | Sev | File · symbol | Scenario | Suggested fix |
|---|---|---|---|---|
| **F-1** | **High** — fail-open #7 | `src/idp_regression/orchestration/watch.py` · `run_watch_loop` (`if result.outcome == OUTCOME_INDETERMINATE:` and the `interrupted` summary arm); `check_versions.py` · `check_once` outcome priority (`elif walk.ceiling_reached or sweep_truncated` before `elif all_unknowns`) and the `else: consecutive_indeterminate_ticks = 0` reset | Real Protocol probe, real `check_once` (scratchpad `repro7.py`, not committed): controls clean, every other candidate `UNKNOWN`. **Repro D — CLI defaults** (`--max-probes-per-tick 20`) with `--patch-lookahead 5 --minor-lookahead 5 --major-lookahead 2`: tick 1 is `indeterminate`; from tick 2 the pending-unknown re-probe plus the grid exceed the budget, so every tick is `ceiling_reached` — the per-tick line reads `probe budget reached (continuing next tick) ok`, `consecutive_indeterminate_ticks` is reset to 0 each tick so `detector_degraded` never fires, and `indeterminate_ticks` never increments. 40 ticks, Ctrl-C: **`Stopped after 40 tick(s). no new versions found (1 of 40 tick(s) got no answer).` EXIT 0.** Repro A (`--max-probes-per-tick 3`, default lookahead) is worse: **`no new versions found.` unqualified**, EXIT 0. Even without unknowns, 40 `ceiling_reached` ticks (the walk truncated every time — "unknown beyond here") read as an unqualified "no new versions found". **MA12** (count `ceiling_reached` as unanswered) **survives 423 tests** — the bucket is pinned in neither direction. | Do what stamp #4 item 1 said: enumerate `_OUTCOME_PHRASES`' keys into three sets — verdict (`no_new_versions`, `new_version_detected`), no-verdict (`indeterminate`, `ceiling_reached`), halt (`_HALT_OUTCOMES`) — assert at import (or in a test) that the union equals the vocabulary, and derive the summary's `unanswered_ticks` from the no-verdict set. In `check_once`, a `ceiling_reached` tick with `all_unknowns` must not reset `consecutive_indeterminate_ticks` (count it, or escalate on a cumulative ceiling). Pin with a real-probe test like Repro D, not a `check_once` monkeypatch. |
| **F-2** | **Medium** — same invariant, the boundary | `watch.py` · `run_watch_loop`, `if healthy_ticks > 0:` in the `interrupted` arm | **Repro C**: `--max-indeterminate-ticks 1000` (a legal flag), every tick `indeterminate`, Ctrl-C after 40: **`no new versions found (40 of 40 tick(s) got no answer).` EXIT 0** — the headline claims a verdict that zero ticks produced and its own parenthesis says so. `healthy_ticks` counts *completed* ticks; the branch should be on *answered* ticks. This is the exception-axis twin of what F-4 (#4) pinned (`RaisesForeverProbe` below the ceiling → `no answer --`). | `answered_ticks = healthy_ticks - <no-verdict count>`; branch on `answered_ticks > 0`, else the `no answer -- …` phrase, and state the no-verdict count there too. One test with `max_indeterminate_ticks` high and a real UNKNOWN-everywhere probe. |
| **F-3** | **High** — vacuous-sentinel class, two more members | `tests/orchestration/test_cli.py` · `test_version_one_char_past_the_64_char_cap_is_rejected` (bare `assert exit_code != 0`) · `test_missing_version_exits_nonzero_without_calling_run_eval` (bare `!= 0`) | **MB4** `_VERSION_PATTERN` `{1,64}`→`{1,65}` **survives**: `"a"*65` passes the guard, `run_eval` (the `_fail_if_called` sentinel) raises `AssertionError`, `main()`'s catch-all logs `run_eval: unexpected error: AssertionError` and returns 1 — confirmed by running the mutant directly. The C2 cap boundary is pinned on the accept side (`test_version_at_the_64_char_cap_is_accepted`) and not on the reject side. **MB5** `--version` `required=True`→`required=False` **survives**: the test's argv also omits `--dataset`, so argparse still exits 2 on *that* flag. The `--action` sibling's docstring explains precisely why exact-code-2 with a complete argv is the mutation-sensitive form; `--version` never got the same treatment. (Both mutants sit on lines the delta did not touch — R1.) | MB4: assert `"run_eval: --version has an invalid format" in caplog.text`. MB5: supply every other required flag and assert `exit_code == 2`. Then sweep: every `_fail_if_called` use whose only assertion is a bare exit code is suspect until a deleted-guard mutant is run against it (stamp #4 carry-forward item 2 — this is the sweep it asked for, still not done). |
| F-4 | Low — cosmetic undercount | `watch.py` · `tick_failures_exceeded` arm: `f"no answer -- {failed_ticks} tick(s) never got an answer …"`; the `detected_versions` arm | Neither arm reports indeterminate/ceiling ticks: a run that was ambiguous on 30 ticks, then hit the failure ceiling, says `no answer -- 4 tick(s) never got an answer` (exit 1 — not fail-open, the verdict is right); a run that detected one version and was blind on 30 of 40 ticks says `detected 1 new version(s)` with no qualifier. | Emit the no-verdict count on every arm from the same tally. |

**Answered, per the brief:**
- **Seventh fail-open? YES** — F-1 (`ceiling_reached`), reproduced with CLI defaults through the real `check_once`.
- **Is `indeterminate_ticks` cumulative? Yes, genuinely** — MA3 (re-adding a reset on a resolved tick) is killed; it is a loop-local counter, never persisted, never reset. The consecutive-only mistake now lives one layer down instead: `check_once` resets `consecutive_indeterminate_ticks` on a `ceiling_reached` tick that still carries unknowns (F-1).
- **Do the `refused` / `tick_failures_exceeded` / `halted` arms account for indeterminate ticks?** They never claim "no new versions found" (MA10/MA11 killed, MA7 killed) and all exit 1, so none is fail-open; their counts omit no-verdict ticks (F-4, Low).
- **Fourth vacuous survivor? YES — two** (MB4, MB5; F-3). `test_malformed_action_id_exits_nonzero_without_calling_run_eval` is also bare, but MB6 (UUID guard deleted) is killed by a sibling's message assertion, so it is redundant rather than vacuous.
- **Does the F-2 spy fix mask a `max_runs_per_tick` bug?** No. MC1 (`if auto_run:`→`if True:`) is killed by the named test alone; MC5 (the `>=` cap deleted) and MC4 (`>=`→`>`) are killed by the bounded test; MC2 (`auto_run or max_runs_per_tick`) is killed. Each gate is pinned independently.

## Mutation matrix (R1: origin named per mutant; apply by exact-string replace, restore by `cp` from scratchpad, `shasum -c` OK after every run; `PYTHONDONTWRITEBYTECODE=1`; `-x`)

| # | File · mutant | Invariant / origin | Diff line? | Target | Result |
|---|---|---|---|---|---|
| MA1 | `watch.py` `indeterminate_ticks += 1` → `pass` | A: every no-verdict tick is accounted for | yes | test_watch | KILLED |
| MA2 | `unanswered_ticks = failed_ticks + indeterminate_ticks` → `failed_ticks` | A | yes | test_watch | KILLED |
| MA3 | add `else: indeterminate_ticks = 0` (the consecutive-only mistake, re-made at the loop layer) | A — "genuinely cumulative?" | yes | test_watch | KILLED |
| MA4 | count `OUTCOME_CEILING_REACHED` *instead of* `OUTCOME_INDETERMINATE` | A | yes | test_watch | KILLED |
| MA5 | `if unanswered_ticks > 0` → `> 1000` | A | yes | test_watch | KILLED |
| MA6 | `if healthy_ticks > 0` → `if True` | A — F-4 (#4) boundary | no | test_watch | KILLED |
| MA7 | `halted` arm `exit_code = 1` → `0` | "a halt never exits 0" | no | test_watch | KILLED |
| MA8 | `if detected_versions:` → `if False:` | "a detection always leads" | no | test_watch | KILLED |
| MA9 | `check_versions.py` delete `consecutive_indeterminate_ticks = 0` reset | consecutive semantics of the escalation | no (other file) | test_check_versions | KILLED |
| MA10 | `tick_failures_exceeded` arm phrase → "no new versions found (…)" | "that phrase lives in one arm" | no | test_watch | KILLED |
| MA11 | `refused` arm phrase → "no new versions found (…)" | same | no | test_watch | KILLED |
| **MA12** | count `OUTCOME_CEILING_REACHED` *as well as* `OUTCOME_INDETERMINATE` | A — the outcome vocabulary, D7 | no | tests/orchestration (423) | **SURVIVED → F-1** |
| MB1 | `cli.py` `--version` format guard → `if False` | B: a guard is pinned by its own log line (A8) | no | tests/orchestration | KILLED (M22 of #4 closed) |
| MB2 | blank `--dataset` guard body → `pass` | B (A8) | no | tests/orchestration | KILLED (M23 closed) |
| MB3 | `<= 0` → `< 0` on `--max-documents-per-run` | B (A10) | no | tests/orchestration | KILLED (M13 closed) |
| **MB4** | `_VERSION_PATTERN` `{1,64}` → `{1,65}` | B (C2 cap) | no | tests/orchestration | **SURVIVED → F-3** |
| **MB5** | `--version` `required=True` → `required=False` | B (A8, argparse) | no | tests/orchestration | **SURVIVED → F-3** |
| MB6 | `--action` UUID guard → `if False` | B (A8) | no | tests/orchestration | KILLED |
| MB7 | `watch.py` `_run` `load_dotenv()` → `pass` | B (INV-05) | no | test_watch | KILLED (M18 closed) |
| MC1 | `if auto_run:` → `if True:` | C: the off switch gates the call | no | test_watch | KILLED (M9/M19 of #4 closed) |
| MC2 | `if auto_run:` → `if auto_run or max_runs_per_tick:` | C | no | test_watch | KILLED |
| MC3 | argparse `--auto-run` `default=False` → `True` | C (CLI default) | no | tests/orchestration | KILLED |
| MC4 | `_run_auto_run` `>=` → `>` | A′.5 per-tick cap | no | test_watch | KILLED |
| MC5 | `_run_auto_run` cap check → `if False` | A′.5 — "does the spy mask the default-0 gate?" | no | test_watch | KILLED |
| S101 | `facade.py:846` guard → `assert` | assert-vs-raise class | no | ruff | **S101 reported** (gate holds) |

**Equivalent mutants: none.** 24 mutants + the S101 probe; 21 killed, 3 surviving non-equivalent. 20 of 24 sit on lines `1e931fe` did not change (R1). Reproductions A/C/D are not mutants: they run HEAD unmodified through a real Protocol probe.

## Standing gaps — re-checked at `1e931fe`

1–5 from stamp #4 (`--auto-run` POC override of §A′.5 preconditions; no backoff; no heartbeat; nothing under a loaded `launchd` schedule; `_base_kwargs` hard-codes `sweep_every_n_ticks=0`) — **all still true**, unchanged by this delta. The sweep gap now matters more: `sweep_truncated` is the second producer of `ceiling_reached` (F-1) and no watcher-loop test ever runs a sweep.
6. assert-vs-raise — **closed and pinned** (S101 row above; `-O` run green).
7. Scope note from #4 (`scorer_store.py`, `version_discovery.py`, `run_artifact.py`, the `facade.py` `--document`/`--classifier` additions **not mutated** by that gate) — **still not mutated by this gate**, which concentrated on the fail-open family per the brief. A PASS must cover them or say so.

## Freshness (P5, DEBT-72/77) — explicit file list pinned to `1e931fe`

`git ls-files src/idp_regression/orchestration` (14 files) equals the on-disk `*.py` listing (14); `git status --short src tests` empty (no untracked, no missing); `git diff --diff-filter=A 8a01c62..HEAD -- src tests` empty (no files added since the last stamp). Blob ids are HEAD's.

| File (HEAD blob) | `8a01c62..HEAD` |
|---|---|
| `__init__.py` (e69de29b) | unchanged |
| `bootstrap.py` (1e13515d) | unchanged |
| `check_versions.py` (cbb27bcc) | unchanged |
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
| `watch.py` (470ea421) | +20 −2 |

Tests changed in the delta: `tests/orchestration/test_cli.py` (+116 −5), `tests/orchestration/test_watch.py` (+137 −25). Also `docs/state/DEBT.md` (+1, DEBT-128).

## What re-stamp #6 must check

1. **The outcome table, not the next outcome.** The fix for F-1 must be a classification of the whole vocabulary (`_OUTCOME_PHRASES` keys ∪ `_HALT_OUTCOMES`) with a test that a new outcome cannot be added without landing in a bucket — otherwise the eighth is `skipped_locked` or whatever comes next. MA12 must be killed *and* a `ceiling_reached`-only run must be pinned on the summary's headline.
2. **F-2's boundary** with a real probe and a raised `--max-indeterminate-ticks`.
3. **The sentinel sweep**: run a deleted-guard mutant against every `_fail_if_called` test in `test_cli.py` whose only assertion is an exit code, and record the result per test. MB4 and MB5 must be killed.
4. A watcher-loop test that actually runs a sweep (gap 5) — the `sweep_truncated` producer of F-1 is otherwise untestable at the loop level.
5. Gaps 1–4, and the unmutated modules under 7.

## History

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ FAILED | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |
| `8a01c62` (2026-09-28, re-stamp #4, stamp committed as `79315d5`) | ❌ FAILED | F-2/F-5/F-6/F-7 of `f3b0b65` **closed and mutant-verified** (M3–M8). **F-1** fail-open #6 (interleaved `indeterminate` ticks read as healthy; 30-of-40 ambiguous → "no new versions found.", exit 0) · **F-2** `if auto_run:` quota guard unpinned by 626 tests; the test named for it is vacuous · **F-3** the `AssertionError`-sentinel class in `test_cli.py` — three A8/A10 guards deletable green · F-4 summary boundary unpinned · F-5 vacuous dotenv test · F-6 P7 ordering |
| `1e931fe` (2026-09-28, this stamp, re-stamp #5) | ❌ **FAILED** | F-1..F-5 of `8a01c62` **closed and mutant-verified** (MA1–MA6, MB1–MB3, MB7, MC1–MC5); F-6 filed as DEBT-128. **F-1** fail-open #7 (`ceiling_reached` is a no-verdict outcome that outranks and resets `indeterminate`; CLI defaults + legal lookahead → "no new versions found (1 of 40 …)", exit 0; MA12 survives) · **F-2** `healthy_ticks > 0` branches on completed, not answered, ticks (40/40 indeterminate → headline "no new versions found", exit 0) · **F-3** two more vacuous-sentinel tests (MB4 64-cap, MB5 `--version required`) · F-4 counts omitted on the non-fail-open arms |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

**DEBT-44 / P6:** commit this FAILED stamp before the fix. Re-stamp #6 must be run by an instance that issued no APPROVE on the fix and is not this instance, on a model other than the implementer's.

— Atchim [ATCHIM!!] …excuse me.
