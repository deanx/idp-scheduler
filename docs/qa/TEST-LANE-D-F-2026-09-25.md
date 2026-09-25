# TEST stamp — Lanes D + F

**Range:** `358f16b~1..bcace07` (4 commits) · `358f16b` Lane D · `398a7e8` Lane D review round 1 ·
`5ab2f41` Lane F · `bcace07` QA round 2 (N-1…N-4)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**
**Verdict:** ✅ **PASS** — 3 non-blocking findings, all since closed (see *After the stamp*).

**Independence:** implementer **Opus 5** (this session) · APPROVE issued by an Atchim instance on
**Sonnet** · this gate run by a **fresh Atchim instance on Fable 5.1 (`claude-fable-5-1`)** that
did not issue, see, or reuse that APPROVE. **DEBT-44 honoured.** The gate wrote its own mutants
rather than reusing the implementer's, on scratchpad copies via `PYTHONPATH`; the repo working
tree was never edited by it.

> Transcribed verbatim from the gating instance's report by the coordinator, which has no write
> tools of its own. Per the standing rule, transcribing an already-decided verdict is permitted;
> fabricating one is not. The *After the stamp* section below is the coordinator's own work and is
> labelled as such.

## Gates (measured by the gating instance at `bcace07`)

| Gate | Result |
|---|---|
| `pytest -q` | **1637 passed / 15 skipped** (opt-in integration), 34.1 s |
| `mypy src tests` | Success — no issues, 133 files |
| `ruff check src tests` | All checks passed |
| `gitleaks detect --log-opts="358f16b~1..bcace07"` | 4 commits, **no leaks** |

**Flake check** — `TestT7History::test_a_finished_job_survives_a_console_restart`: **6/6 full-suite
runs green** at `bcace07` (plus 6/6 at `5ab2f41`), several under concurrent load. Reverting the
disk-polling change gave 0 failures in 30, so that change is a determinism improvement rather
than load-bearing at current timings.

## Mutation matrix

**27 killed / 8 survived.** Killed, by row: DEBT-83 (M01, M17, M18, M08) · DEBT-52 (M03) ·
DEBT-54 A-2 (M02, M09) · DEBT-57 A-4/A-5 (M19, M20 — killed by *pre-existing* tests, confirming
the "already closed" claim) · DEBT-59 (M04 ×3, M21) · DEBT-73 (N818 revert flagged by ruff) ·
DEBT-84 (M07, M12 10/10, M24, M25, M27) · DEBT-85 (M05 5/5, M15, M06, M16, M16b, M22, M28, M32,
M33).

Survivors: M10, M11, M13, M23, M26, M29, M34, M35, M36 — four accepted as unobservable from a
unit test or on non-AC lines (`del file_bytes`, `os.fsync`, temp-cleanup `raise`, per-writer
`mkstemp`); five became findings F-1…F-3.

## Per-row criterion coverage

Every DEBT row's closure claim is backed by at least one killed mutant. DEBT-52 is verified for
its **shipped half only** and is recorded as NARROWED, residual named — not closed.

## Findings (all non-blocking, none fail-open)

- **F-1 (Low)** — the size-cap test's name claimed an ordering it cannot prove (M10: moving the
  check after `fh.read()` survived). Shipped code is correct; the name overclaimed.
- **F-2 (Low-Med)** — the torn-write test pinned the shipped defect class, not the invariant its
  docstring stated (M36: serialize-first then truncate-in-place survived 3/3).
- **F-3 (Med)** — `is_busy()`'s single-attempt contract was unpinned and `start()`'s budget only
  pinned as `> 0` (M13, M26, M34 survived). **A coverage regression introduced by `bcace07`'s own
  N-1 fix**: moving the genuinely-held test onto the budget path removed what it had been covering
  incidentally.

## Notes carried to the register

- **DEBT-59's Wave-0 text was already stale at `358f16b~1`** — `3bff5f0` had pinned
  `KeyboardInterrupt` at the tail marker. Lane D's contribution is the `SystemExit` parameter.
- Accepted survivors recorded so they are not re-discovered: M11, M35, M23, M29.

---

## After the stamp — F-1, F-2 and F-3 closed (coordinator, same day)

Not part of the gating instance's verdict; recorded here so the stamp and the tree do not drift.
All three were test-only changes, each mutation-verified by the coordinator:

| Finding | Fix | Mutant now |
|---|---|---|
| **F-3** | `test_is_busy_probes_with_a_single_non_blocking_attempt` asserts the call *and* `inspect.signature`'s default; `test_start_uses_the_retry_budget` asserts `== START_LOCK_RETRY_SECONDS` rather than `> 0` | **M13, M26, M34 killed** |
| **F-2** | `test_the_record_is_written_by_rename_and_never_truncated_in_place` wraps `os.open`/`os.replace` and pins write-then-rename structurally — no sleeps, nothing machine-dependent | **M36 killed** |
| **F-1** | test renamed to `..._is_refused_by_the_cap`, i.e. to what it proves | M10 remains (ordering is correct in code; the name no longer claims it) |

Gates after those fixes: **1639 passed / 15 skipped**, mypy clean (133 files), ruff clean.
