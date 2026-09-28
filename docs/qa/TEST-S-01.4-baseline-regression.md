# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ❌ **FAILED at `8a01c62`** — re-stamp #4, fresh gate (DEBT-44/46/72/77, Wave C).
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Date:** 2026-09-28 · **Commit:** `8a01c625172bf8946f3cf28b2afa20d53981ed93` · **Author:** alex@divinocosta.com.br (solo)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of the `prototype` profile. **Profile ≠ risk level.**
**Independence (R3/DEBT-44):** run by a **new Atchim instance on Claude Fable 5.1**. This instance issued **no APPROVE and no prior verdict** on any commit in this story's history. The fixes since `f3b0b65` carry `Co-Authored-By: Claude Opus 5 (1M context)` / `Claude Opus 5.5 (1M context)` trailers — a different model from the gate. ✅ structural (different models, fresh instance).
**Static:** `pytest -q` **1931 passed / 15 skipped** · `mypy src tests scripts` (strict) **0 errors / 151 files** · `ruff check src tests scripts` **clean**, `S` family selected (`pyproject.toml` `select` includes `"S"`; `S101` fires — demonstrated below). No live IDP/platform call; `.env` never sourced.

## Verdict — FAILED. Why, in one paragraph

The four coordinator-verified fixes (F-2, F-5, F-6, F-7) **are** closed and each has a mutant that goes RED (M3–M8 below). But the re-stamp checklist's own item 1 landed: a **sixth fail-open, reproduced through the real probe path** — the F-5 fix keyed the summary qualifier on `failed_ticks` (ticks that *raised*), and a tick that returns `indeterminate` (every candidate `UNKNOWN`: a 429/5xx-ing API) is counted as **healthy**, so a watcher whose API was ambiguous on **30 of 40 ticks** closes with `no new versions found.` unqualified, exit 0. Same violated invariant as F-1/F-5, one outcome to the left. And item 2 landed harder than expected: the eighth vacuous test is a **class** — five tests in this tree pass because their `AssertionError` sentinel is swallowed by the very catch-all the code under test is required to have, and the mutant set shows three A8/A10 CLI guards plus the watcher's `if auto_run:` quota guard are pinned by **nothing** in 626 tests.

## Findings

| # | Sev | File · symbol | Scenario | Suggested fix |
|---|---|---|---|---|
| **F-1** | **High** — fail-open #6 | `src/idp_regression/orchestration/watch.py` · `run_watch_loop`, the `end_reason == "interrupted"` summary arm; `healthy_ticks += 1` after `check_once` | Real probe (scratchpad repro, not committed): controls clean, every candidate `UNKNOWN` on ticks where `n % 4 != 0`, `ABSENT` on the 4th; 40 ticks, Ctrl-C. `check_once` returns `indeterminate` 30 times — never >3 in a row, so `max_indeterminate_ticks` (consecutive-only, `check_versions.py`) never escalates to `detector_degraded`. Output: **`Stopped after 40 tick(s) (0m00s). no new versions found.`**, `EXIT 0`. The per-tick line says `ambiguous result (retrying)` 30 times and the closing line contradicts it. Invariant violated: *every tick that produced no verdict is accounted for in the summary.* F-5 closed the exception case; `indeterminate` (and arguably `ceiling_reached`) are the same class on the outcome axis, unchanged line (R1). | Count no-verdict outcomes (`OUTCOME_INDETERMINATE`, decide on `OUTCOME_CEILING_REACHED`) into an `unanswered_ticks` counter beside `failed_ticks`; qualify the summary on their sum; add a **cumulative** ceiling (F-5's own docstring already notes a consecutive-only ceiling is unreachable at 3-of-4). Pin with a real-probe test, not a `check_once` monkeypatch. Consider the same cumulative blind spot in `check_once`'s `consecutive_indeterminate_ticks` (unchanged code). |
| **F-2** | **High** — vacuous test + unpinned quota guard | `tests/orchestration/test_watch.py` · `test_auto_run_off_by_default_never_calls_run_eval`; `watch.py` · `run_watch_loop` `if auto_run:` | The sentinel raises `AssertionError`; `_run_auto_run` catches `Exception`, logs `FAILED TO RUN`, and the loop continues. The test has no other assertion. **M9/M19** (`if auto_run:` → `if True:`) survive that test, the whole of `test_watch.py`, and **626 tests across `tests/orchestration` + `tests/ui`** — the one line keeping a `--auto-run`-less watcher from spending IDP quota is pinned by nothing. (The CLI's argparse default is pinned only incidentally: M20 is killed by the `--max-runs-per-tick has no effect` test, not by a spend test.) | Sentinel records calls and the test asserts `calls == []` (or raise a `BaseException` subclass the catch-all cannot swallow), and assert `"spends real IDP quota" not in out`. |
| **F-3** | **High** — vacuous-test **class** (F-4 of `a5805ec` recurring) | `tests/orchestration/test_cli.py` · `_fail_if_called` (20 uses) and every test whose only assertion is `exit_code == 1` | `_fail_if_called` raises `AssertionError`; `cli.main()`'s catch-all (`except (Exception, asyncio.CancelledError)`) converts it to `return 1` — the exact value the tests assert. Survivors over all of `tests/orchestration`: **M22** `_is_valid_version` guard deleted (`test_malformed_version_exits_nonzero_without_calling_run_eval` green), **M23** blank `--dataset` guard deleted (`test_blank_dataset_flag_value_is_rejected_including_whitespace_only` green), **M13** `<= 0` → `< 0` on `--max-documents-per-run` (`"0"` leg of `test_max_documents_per_run_zero_or_negative_is_a_usage_error` green; `facade.py` has no independent `<= 0` guard). M21/M24 (`--action`, `--org`) are killed only because a sibling test asserts the log message. These are ADR-0004 A8/A10 fail-closed guards. | Same as F-2: a recording double + `assert calls == []`, and assert the specific `logger.error` line for every guard, so a guard's deletion is caught on its own message, not on a coincidental `1`. |
| **F-4** | Medium — test gap | `watch.py` · summary arm `if healthy_ticks > 0:` | **M15** (`healthy_ticks > 0` → `True`) survives `test_watch.py`: no test covers Ctrl-C after only failed ticks below the ceiling (would print `no new versions found (2 of 2 tick(s) got no answer)` instead of `no answer -- …`). The F-1 family's exact boundary is unpinned. | One test: `RaisesForeverProbe`, `max_consecutive_tick_failures=5`, `_stop_after(2)`; assert `"no answer --"` and `"no new versions found" not in out`. |
| **F-5** | Low — vacuous test | `tests/orchestration/test_watch.py` · `test_watch_main_a_load_dotenv_failure_is_a_controlled_exit_not_a_traceback` | **M18** (`load_dotenv()` call deleted) survives: `main()` returns 1 anyway on the missing `IDP_CLIENT_ID`. Under the mutant with credentials in the shell env this test would construct a real `MuleSoftVersionProbe` and tick against the network. | Assert the specific line `watch: unexpected error loading .env` in `caplog`. |
| **F-6** | Low — process (P7) | `148c6dd` (test) landed two minutes **after** `f4eb64b` (fix) | Test-after-fix; the commit message reports a post-hoc mutant, which this gate re-ran (M1/M2 below), so the pin is real — the ordering rule was still broken. | Record; no code change. |

**Not findings, checked:** `_run_auto_run`'s `if not match: continue` (a command without `--version` is silently uncounted — `check_once` always emits one; equivalent today). `Stopped after N tick(s)` overcounts by one if Ctrl-C lands inside `check_once` (cosmetic). `_human_tick_line` prints `controls ok` on `detector_degraded` (cosmetic). `save_state_atomic` raising inside the loop escapes `run_watch_loop` to `_run`'s catch-all → exit 1, no summary line — loud, not silent.

## Mutation matrix (R1: source named per mutant; restore by `cp` from scratchpad, `shasum -c` OK after every run; `PYTHONDONTWRITEBYTECODE=1`; no stray `__pycache__`)

| # | File · mutant | Invariant / origin | Target | Result |
|---|---|---|---|---|
| M1 | `facade.py` quota guard `if submits_made > max …` → `if False` | A10 "ceiling check always fires" (diff line) | test_facade | KILLED |
| M2a | guard → `assert submits_made <= …` (normal interpreter) | assert-vs-raise class | test_facade | KILLED — on the **exception-type** axis (`"RuntimeError" in caplog`), not the `-O` axis |
| M2b | same, under `python -O` | assert-vs-raise class | test_facade | KILLED |
| **M2c** | guard → `if __debug__ and …` (what `assert` compiles to, RuntimeError kept), normal interpreter | isolates the `-O` axis | pin test | **SURVIVED — equivalent by construction** |
| **M2c-O** | same under `python -O` | isolates the `-O` axis | pin test | **KILLED** (exit 0 instead of 1) — the `-O` class is pinned |
| M3 | `watch.py` delete `consecutive_tick_failures = 0` | F-6 "consecutive" | test_watch | KILLED |
| M4 | `failed_ticks += 1` → `pass` | "a failing tick is never dropped" | test_watch | KILLED |
| M5 | ceiling `>` → `>=` | ceiling semantics | test_watch | KILLED |
| M6 | `end_reason = "halted"` → `"interrupted"` | "a halt never reads as no-new-versions" | test_watch | KILLED |
| M7 | summary qualifier `if failed_ticks > 0` → `if False` | F-5 | test_watch | KILLED |
| M8 | `main()` outer `except Exception` → `except KeyboardInterrupt` | F-2 "no raw exception escapes main()" | test_watch | KILLED |
| **M9 / M19 / M9all** | `if auto_run:` → `if True:` | "no quota spend without --auto-run" (unchanged line) | test_watch / named test / **626 tests** | **SURVIVED ×3** → F-2 |
| M10 | `healthy_ticks += 1` deleted | "every tick accounted for" (unchanged line) | test_watch | KILLED |
| M11 | `_run_auto_run` `>=` → `>` | A′.5 per-tick cap (unchanged line) | test_watch | KILLED |
| M12 | `save_state_atomic(...)` → `pass` | "a detection is never re-run" (unchanged line) | test_watch | KILLED |
| **M13 / M13all** | `cli.py` `<= 0` → `< 0` | A10 usage error | test_cli / tests/orchestration | **SURVIVED ×2** → F-3 |
| M14 | argparse error path drops `sanitize_for_log` | INV-02 / N5 | test_cli | KILLED |
| **M15** | summary `if healthy_ticks > 0` → `if True` | F-1 boundary (unchanged line) | test_watch | **SURVIVED** → F-4 |
| M16 | `_run_auto_run` `except Exception` → `except KeyboardInterrupt` | "a failed run never kills the watcher" | test_watch | KILLED |
| M17 | `check_versions.py` `if consecutive_indeterminate_ticks > max` → `if False` | detector_degraded escalation (unchanged file) | test_check_versions | KILLED |
| **M18** | `_run` `load_dotenv()` → `pass` | INV-05 | named test | **SURVIVED** → F-5 |
| M20 | argparse `--auto-run` `default=False` → `True` | CLI default (unchanged line) | tests/orchestration | KILLED (incidentally, via the `--max-runs-per-tick` consistency test) |
| M21 | `cli.py` `--action` UUID guard → `if False` | A8 | tests/orchestration | KILLED (by the log-message test only) |
| **M22** | `--version` format guard → `if False` | A8 | tests/orchestration | **SURVIVED** → F-3 |
| **M23** | blank `--dataset` guard → `if False` | A8 | tests/orchestration | **SURVIVED** → F-3 |
| M24 | blank `--org` guard → `if False` | A9 | tests/orchestration | KILLED (by the log-message test only) |

**Equivalent mutants:** M2c only (deliberately). 27 runs, 18 killed, 8 surviving non-equivalent, 1 equivalent. Ten mutants sit on lines the `f3b0b65..HEAD` diff did not change (R1).

## The four standing gaps and the assert-vs-raise check — re-checked at `8a01c62`

1. **`--auto-run` POC override of four unmet ADR-0006 §A′.5 preconditions** — **still true** (`watch.py` module docstring and `--auto-run` help text say so; no ledger, no claim/resolve, no alerting sink in the tree). Now compounded by F-2: the off-switch is unpinned.
2. **No backoff** — **still true**: 0 occurrences of `backoff` in `watch.py`; sleep is a flat `interval_seconds` on every path including the failing one.
3. **No heartbeat** — **still true**: 0 occurrences; a quiet tick and a dead process print nothing distinguishable after the last line.
4. **Nothing run under a loaded `launchd` schedule** — **still true**: no `com.idp-regression.*` plist in `~/Library/LaunchAgents`; `logs/scheduled-runs/` holds two 2026-09-23 wrapper logs and one `FAILED` marker, which do not establish a loaded schedule, and the wrapper is the `run_eval` path, not the watcher.
5. **`test_watch.py::_base_kwargs` hard-codes `sweep_every_n_ticks=0`** — **still true** (line 103, the only occurrence in the file): the sweep never runs through `run_watch_loop` in any watcher test.
6. **assert-vs-raise (A10 quota bug detector)** — **closed and pinned**: `facade.py:846` is `if submits_made > max_documents_per_run: raise RuntimeError(...)`; no `assert` statement remains under `src/idp_regression/orchestration/`; ruff `select` includes `"S"` with `S101` ignored only under `tests/**`; reintroducing the `assert` at that line makes `ruff check` report **`S101 … facade.py:846:13`** (demonstrated, then restored); M2c/M2c-O show the pin test fails under `python -O` and only there.

## Freshness (P5, DEBT-72/77) — explicit file list pinned to `8a01c62`

`git ls-files src/idp_regression/orchestration` equals the on-disk `*.py` listing (no untracked, no missing); no renames `cfd2bd7..HEAD` under `src/` or `tests/` (`git diff -M --diff-filter=R` empty). Added since the last PASSED pin `cfd2bd7`: `check_versions.py`, `run_artifact.py`, `scorer_store.py`, `version_discovery.py`, `watch.py` (+ tests `test_check_versions.py`, `test_cli_custom_scorers.py`, `test_document_filter.py`, `test_logging_config.py`, `test_run_artifact.py`, `test_scorer_store.py`, `test_version_discovery.py`, `test_watch.py`).

| File (HEAD blob) | `f3b0b65..HEAD` |
|---|---|
| `__init__.py` (e69de29b) | unchanged |
| `bootstrap.py` (1e13515d) | unchanged |
| `check_versions.py` (cbb27bcc) | +80 −20 |
| `cli.py` (163e2416) | +116 −5 |
| `dotenv_support.py` (902d1144) | +16 −2 |
| `errors.py` (c9bcbfc6) | +5 −5 |
| `facade.py` (c4cab023) | +290 −47 |
| `log_sanitize.py` (50b57623) | +56 −2 |
| `prerun.py` (b741fba0) | +14 −6 |
| `run_artifact.py` (418e3d42) | +106 −7 |
| `run_naming.py` (dc2be96b) | unchanged |
| `scorer_store.py` (38d15122) | new, +117 |
| `version_discovery.py` (de048193) | new, +148 |
| `watch.py` (55c63fe3) | +165 −25 |

Scope note: the mutation matrix concentrates on `watch.py`, `cli.py`, `facade.py` (quota guard) and `check_versions.py` — the fail-open family and the `-O` class the brief names. `scorer_store.py`, `version_discovery.py`, `run_artifact.py` and the `facade.py` `--document`/`--classifier` additions were type-checked and suite-covered but **not mutated by this gate**; a PASS at the next re-stamp must say so or cover them.

## What a re-stamp must still check (carried forward, amended)

1. **A seventh fail-open.** F-1 here is the third time the summary invariant was closed by case rather than by invariant. The fix should enumerate the outcome vocabulary (`_OUTCOME_PHRASES` keys) and state for each whether it is a verdict, a no-verdict, or a halt — then the summary derives from that table, and a new outcome cannot land in the wrong bucket silently.
2. **The sentinel class (F-2/F-3).** Every `*_never_calls_run_eval`-shaped test in this tree must be re-run against a deleted guard before it counts.
3. Gaps 1–5 above, unchanged.
4. The unmutated modules named under Scope note.

## History

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ FAILED | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |
| `8a01c62` (2026-09-28, this stamp) | ❌ **FAILED** | F-2/F-5/F-6/F-7 of `f3b0b65` **closed and mutant-verified** (M3–M8). **F-1** fail-open #6 (interleaved `indeterminate` ticks read as healthy; 30-of-40 ambiguous → "no new versions found.", exit 0) · **F-2** `if auto_run:` quota guard unpinned by 626 tests; the test named for it is vacuous · **F-3** the `AssertionError`-sentinel class in `test_cli.py` — three A8/A10 guards deletable green · F-4 summary boundary unpinned · F-5 vacuous dotenv test · F-6 P7 ordering |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

**DEBT-44:** the next re-stamp must be run by an instance that issued no APPROVE on this delta and is not this instance.

— Atchim [ATCHIM!!] …excuse me.
