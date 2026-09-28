# /test stamp — SPEC-02

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-28
**Commit:** d95eee258af5c6f6c92e59d7dc16e6e6e1b3d3f4
**Author:** alex@divinocosta.com.br (solo)
**Atchim TDD gate:** PASSED
**Independence:** ✅ structural (different models) — Dengoso (claude-sonnet-4-6) implemented, Atchim (claude-opus-4-8) reviewed; this gate's Atchim pass is a fresh instance, independent of the ones that approved the S-02.1/S-02.2 code review (DEBT-44)
**Static:** ✅ clean (mypy strict: no issues in 157 files; ruff: all checks passed; pip-audit: no CVEs; full suite 2107 passed/15 skipped)
**Files:** `src/idp_regression/ui/api.py`, `src/idp_regression/ui/jobs.py`, `src/idp_regression/ui/review_sessions.py`, `tests/ui/test_api.py`, `tests/ui/test_jobs_new_builders.py`, `tests/ui/test_review_session.py`, `tests/ui/test_review_workflow.py`, `docs/state/DEBT.md`, `docs/state/PROGRESS.md`, `docs/state/HANDOFFS.md`
**Sequence:** test-first per slice across three original commits (`05280ef`, `9e0cebf`, `404ca13`), a fail-open fix round (`deab50c`), a containment-gap fix round (`c5da2d7`, DEBT-142/143), and a `/test` gap-fill round (`d95eee2`) adding 8 tests against an independent coverage audit's findings — all Scenario B (coverage gaps in already-correct code), no implementation defects found, no `src/` changes in the gap-fill round

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Full suite | 2107 | 0 |
| Skipped | 15 | — |
| `tests/ui/test_review_workflow.py` | 43 | 0 |

## AC coverage

| AC | Tests | Status |
|---|---|---|
| AC1 — named stages (upload/validate/draft) | `test_jobs_new_builders.py` (build_pin_argv/build_verify_argv shape tests); the stage-naming UI itself is S-02.3, not yet implemented | ✅ COVERED (backend half) |
| AC2 — review spends zero quota, verification not started | `TestZeroQuota::test_complete_spends_zero_idp_quota`, `test_get_review_spends_zero_idp_quota` (real `subprocess.Popen` spy, zero calls) — added in gap-fill round, closing a prior direct-assertion gap | ✅ COVERED |
| AC5 — second approval priced after review, separate from stage 1 | `test_review_workflow.py::test_inv09c_*`, `test_inv09d_subsumed_by_a_and_c` | ✅ COVERED |
| AC6 — unapproved session stays visibly paused, zero further spend | `test_review_workflow.py` (GET /api/reviews pending-list tests) | ✅ COVERED |
| INV-09(a) session exists+reviewed | `test_review_workflow.py::test_inv09a_*` | ✅ COVERED |
| INV-09(b) document_dir/archive_sha256 | `test_review_workflow.py::test_inv09b_refused_when_document_dir_missing` — scope-honestly narrowed (directory-existence only; byte-level integrity delegated to `verify_document.py`) | ✅ COVERED (scope-honest) |
| INV-09(c) fresh-plan approval | `test_review_workflow.py::test_inv09c_*` | ✅ COVERED |
| INV-09(d) route+session binding | `test_inv09d_subsumed_by_a_and_c` — subsumption reasoning re-verified sound by a fresh, independent Atchim instance; no literal token-replay test exists because no separate approval token exists by design | ✅ COVERED (subsumed, documented) |
| INV-09(e) live golden hash match | `test_inv09e_adversarial_golden_swap_unit`, `test_inv09e_passes_when_hash_matches`, `test_inv09e_refuses_when_current_hash_is_none`, `test_inv09e_refuses_when_approved_golden_hash_is_none`, **`test_inv09e_refuses_when_both_hashes_are_none`** (new — closes the previously-flagged gap by directly constructing the both-None state at the verify-start layer, independent of the upstream DEBT-143 guard) | ✅ COVERED (fail-closed at every angle: current-None, approved-None, both-None, and review-complete) |
| N1 (<2s/100-doc review read) | `test_n1_get_session_with_100_doc_edited_ids_returns_in_under_2s` (new — replaces the prior near-empty-payload fixture with a real 100-entry `edited_document_ids` payload matching the DoD's claim) | ✅ COVERED |
| N5 (over-ceiling refused before session creation) | `test_n5_over_ceiling_refused_before_session_created` (asserts 422 and no session file created) | ✅ COVERED |
| N6 (0600/0700 modes) | `test_review_session.py` (mode assertions via `os.stat`) | ✅ COVERED |
| N7 (no-flock concurrency) | `test_n7_unrelated_job_can_start_while_review_session_is_pending` (real `is_busy()` probe against the actual `_WorkspaceLock` — genuinely guards the property) + `test_n7_unrelated_job_can_actually_start_while_review_session_is_pending` (companion, but see DEBT-146: its own monkeypatching of `JobRegistry.start` bypasses the flock it claims to prove) | ✅ COVERED (genuinely, via the sibling test) — DEBT-146 filed for the companion test's overclaiming docstring |
| N3 (observability, automated) | `test_draft_started_log_line_emitted`, `review_complete` log assertion, **`test_verify_candidate_started_log_emitted_on_success`** (new — closes the "only refusal path tested" gap with a happy-path assertion) | ✅ COVERED |
| N8 (quota-boundary drift guard, 4 routes) | `tests/ui/test_api.py::TestHealthThreeRoutes` (asserts the real 4-route set: compare, floor, draft-golden, verify-candidate — corrected from the SPEC's stale "three" which omitted the pre-existing `floor/start`) | ✅ COVERED |

## Notes carried to `/qa`

- **DEBT-146 filed** (this round): the "actually start" N7 companion test monkeypatches `JobRegistry.start`, the only place `_WorkspaceLock.acquire()` runs, so it would pass even under a hypothetical held-flock regression. Non-blocking — the sibling test genuinely guards the property via a real `is_busy()` probe.
- **DEBT-141/144/145 remain open** (hardening nits from `/harden`, non-blocking).
- **`Containment: REQUIRED`** per NFR-02 — `/harden` (`docs/qa/HARDEN-02.md`) has now been re-run and both prior blocking gaps (DEBT-142, DEBT-143) were fixed before this `/test` pass; containment status should be independently re-verified at `/qa`'s containment gate, not assumed from this stamp alone.
- Independent coverage audit (pre-gap-fill) confirmed S-02.3 (the console UI) is entirely unimplemented — no frontend test files exist. This is expected; S-02.3 has not started.

## History
- 2026-09-28, `/implement` (Atchim code review TDD gate) at commit `deab50c`: PASSED, after one REQUEST CHANGES round (Critical: INV-09(e) fail-open on unreadable-platform None/None hash comparison).
- 2026-09-28, `/test gap-fill` (Atchim TDD gate) at commit `d95eee2`: PASSED — independent coverage audit found 6 gaps/weak spots across S-02.1 (INV-09(e) both-None direct test, N7 real-start proof, N1 fixture-vs-claim mismatch, N3 missing happy-path transition, AC2 missing direct zero-quota test); all closed with 8 new tests, no implementation changes (Scenario B throughout); a fresh, independent Atchim instance confirmed PASS with one non-blocking test-quality finding (DEBT-146).
