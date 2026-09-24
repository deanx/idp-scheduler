# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ❌ **FAILED at `f3b0b65`** — fixes have since landed; **a re-stamp is owed.**
**Date:** 2026-09-24 · **Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of
the `prototype` profile. **Profile ≠ risk level.**

> ⚠️ **Record correction, 2026-09-24.** This file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22)
> while the gate had **failed twice** — at `a5805ec` and again at `f3b0b65`. Both verdicts were
> produced by fresh instances and both were acted on, but neither was written here. **The register
> said PASSED for roughly a day while the gate said FAILED**, which is DEBT-54's own defect class
> (a register lagging reality) landing on the one artifact whose entire job is to state whether a
> `Risk: high` delta is gated. Recorded rather than quietly overwritten.

## History — what each gate found

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ **FAILED** | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |

## Fixes landed since `f3b0b65` — verified by the coordinator, NOT by an independent gate
- **F-5** — the summary is now keyed on `failed_ticks`, not `healthy_ticks == 0`. Reproduced
  before and after: a probe failing 3 ticks in 4 previously closed with
  `"no new versions found."` at exit 0; it now reads
  `"no new versions found (30 of 40 tick(s) got no answer)."`
- **F-2** — one **outer** catch-all on `main()`'s whole body, mirroring `check_versions.main()`,
  replacing the enumerate-the-sites approach that missed a third site. The two vacuous tests were
  replaced with a parametrized invariant test over sites the old pair never reached.
- **F-6** — an interleaved failing/succeeding probe test; the reset mutant now goes RED.
- **F-7** — the wrong `and failed_ticks > 0` conjunct removed.
- Plus (found separately, ruff `S101` once the bandit family was finally selected): the A10 quota
  **bug detector was an `assert`**, which `python -O` strips. Now an explicit raise, with a test
  driving it through the real call sites via a length-lying dataset. Demonstrated, not argued:
  `assert-based guard under -O: no guard fired` / `raise-based guard under -O: RuntimeError`.

## What a re-stamp must still check
1. **A sixth fail-open.** Five were found this week and **each fix created the next**, because each
   closed the reported case rather than the violated invariant. Three of those were in this module.
2. **An eighth vacuous test.** Seven found so far, two in this tree.
3. The gaps every prior stamp named and that still stand: `--auto-run` ships as a POC override of
   four unmet ADR-0006 §A′.5 preconditions; **no backoff exists** despite the ADR naming it;
   **no heartbeat** (a dead watcher is indistinguishable from a quiet one); nothing has run under a
   loaded `launchd` schedule; `test_watch.py::_base_kwargs` hard-codes `sweep_every_n_ticks=0` so
   **the sweep never runs through `run_watch_loop`** in any watcher test.

**DEBT-44:** the re-stamp must be run by an instance that issued no APPROVE on this delta.
