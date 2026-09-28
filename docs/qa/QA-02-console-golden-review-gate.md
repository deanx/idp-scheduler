# QA-02: audit of SPEC-02 (S-02.1 + S-02.2) — Console golden-review/approve gate

**Verdict:** ⚠️ Pass with follow-ups (Done is **not** blocked — all follow-ups are pre-filed, non-blocking Low debt)
**Author:** alex@divinocosta.com.br (solo)
**Commit audited:** d95eee2 · branch feat/S-01.2-idp-adapter
**Auditor:** Zangado

## Gate summary

- **Test suite:** 2107 passed, 15 skipped, exit 0 — matches the `/test` stamp exactly. Full run, not a subset.
- **Staleness:** stamps' commit `d95eee2` is fresh. The gap-fill entry is the newest PROGRESS log line touching stamped files; the subsequent `/harden` re-probe touched only `docs/qa/HARDEN-02.md` + `docs/state/DEBT.md`, never a stamped `src/`/`tests/` file. No test-file deletions across `404ca13..d95eee2`.
- **Rigor gate (Risk: high):** both stamps' `Source` is `/test gap-fill (Atchim TDD gate)` — satisfies the independent-baseline requirement for a high-risk SPEC (DEBT-44 honored: the reviewing Atchim instance for `/test` was fresh, not one of the ones that approved the `/implement` code reviews). Independence is structural (Dengoso claude-sonnet-4-6 implemented, Atchim claude-opus-4-8 reviewed).
- **Dependency scan:** `pip-audit` — no known vulnerabilities.
- **Secret scan:** `gitleaks` — no leaks found on the full story diff; grep fallback also clean.
- **Composition:** CT-06 (`ReviewSession` record) and INV-09 clauses (a)–(e) verified directly in code (`api.py:1384-1466`) and by test — fresh-plan recompute (c), route/session-bound approval (d), live-hash match with correct fail-closed-on-None-either-direction logic (e). `test_inv09e_refuses_when_both_hashes_are_none` closes the `None == None` vacuous-match hazard directly at the verify-start layer.

## NFR-02 walk (N1–N9)

| # | Scope | Verdict | Evidence |
|---|---|---|---|
| N1 Performance | feature | ✅ PASS | `test_n1_get_session_with_100_doc_edited_ids_returns_in_under_2s` — real 100-entry payload, passes |
| N2 Security | feature | ✅ PASS | INV-09 (c)/(d)/(e) refusals + schema-validate-before-write; `TestQuotaBoundary` (12 tests pass) |
| N3 Observability | feature | ✅ PASS | one log line per transition + no-golden-value-in-log tests, real `sensitive_value not in caplog.text` assertions |
| N4 Availability | system | N/A | loopback single-operator console, no uptime SLO applies |
| N5 Scalability | feature | ✅ PASS | `test_n5_over_ceiling_refused_before_session_created` — 422, no session file written |
| N6 Data/compliance | feature | ✅ PASS | 0600 file / 0700 dir asserted via `os.stat`; no code path writes file bytes to the platform (INV-01 intact) |
| N7 Reliability | feature | ✅ PASS | pending-list + genuine no-flock `is_busy()` proof (DEBT-146 nit is on the *companion* test's own claim only, not the underlying property, which is genuinely guarded) |
| N8 Quota-drift guard | feature | ✅ PASS | `/api/health` declares all 4 spending routes (compare, floor, draft-golden, verify-candidate); test asserts declaration == real route table |
| N9 Session integrity | feature | ✅ PASS (scope-honest) | atomic temp+rename, fail-closed read, `approved_golden_hash` re-check. `archive_sha256` byte-check is delegated to out-of-scope `verify_document.py` — flagged in the code comment, not a defect |

No system-scope `feature` rows exist beyond N4 (correctly N/A).

## Containment: ✅ CONTAINED

`docs/qa/HARDEN-02.md`'s re-probe verdict is ✅ CONTAINED, and the DEBT-142/143 evidence was independently re-verified against the actual code, not accepted on trust:
- **DEBT-143 (empty-dataset vacuous match):** `fetch_golden_hash` returns `None` for zero items → the existing None→409 guard at `/complete` catches it. Fail-closed.
- **DEBT-142 (partial replace):** `api.py:1194-1224` — on any mid-batch upsert failure the session is forced to `REPLACE_FAILED`, `approved_golden_hash` cleared, `/complete` refuses that state (409). The partial write still physically lands on the platform (it has no transaction API — an honest, documented constraint), but no path lets a partial golden be approved, and INV-09(e) is the backstop.

## Observability: ✅ VERIFIED

All specified telemetry fires as real `logger.info`/`warning` calls, traced through the log-assertion test suite: `draft_golden_started` (api.py:917), `review_complete` (1001), `golden_item_edited` (1120), `golden_replace_partial_failure` (1212, key only), `golden_set_replaced` (1259), `verify_candidate_inv09e_check` (1421, bool only), `verify_candidate_started` (1461). Redaction holds — every emitted line carries only session_id/job_id/document_id/field-name/count/bool, never a golden value; two dedicated `sensitive_value not in caplog.text` tests confirm it. Binding regardless of `prototype` rigor per NFR-02's own correction — enforced here, not waived.

## LLM-Evals: N/A — confirmed

The touched surfaces compose no prompt and process no model output. Inputs are schema-validated JSON. No LLM sink.

## Findings

No Critical. No Major. **Zero code-level defects found** — no `escaped-atchim` tags to record.

**Minor / advisory (all non-blocking):**
- **F-1** (cleanliness, advisory at `prototype` rigor per `skipped: prototype profile`): `check_clean.py` reports 5 stale artifacts — `.DS_Store`, `id_seeds/.DS_Store`, 3 `logs/scheduled-runs/run-*.log`. All pre-existing, unrelated to S-02.1/S-02.2, gitignore territory. Not a defect.
- **F-2** (doc-accuracy nit, fixed in this pass): SPEC-02's DoD/Scope text said "three" quota-spending routes; reality is four (the pre-existing `floor/start` was omitted from the SPEC's original count). The stamp and `TestQuotaBoundary` were always correct — only the SPEC prose was stale. Corrected in `docs/specs/SPEC-02-console-golden-review-gate.md` as part of this QA pass.

## Debt surfaced (already filed, non-blocking — carried forward, not re-created)

- **DEBT-141** (Low) — `fetch_golden_hash` query-string only encodes spaces; display/provenance-only, not a safety gate.
- **DEBT-144** (Low) — `fetch_golden_hash` catches only `OSError`; other failures 500 instead of a clean 409. Containment-safe, ungraceful.
- **DEBT-145** (Low) — `ReviewSession.from_dict` doesn't type-check optional fields on load; not exploitable.
- **DEBT-146** (Low, test-quality) — N7 "actually start" companion test overclaims what its monkeypatching proves; the sibling test genuinely guards the property.
- **DEBT-147** (Low, UX/liveness) — PATCH doesn't clear `REPLACE_FAILED`; fail-closed and arguably correct, needs a doc line or the transition.

Note: S-02.3 (the console UI) is correctly out of scope for this audit — unimplemented, no frontend test files, as both stamps already state.

## Done gate (S-02.1, S-02.2)

Feature rows all ✅ PASS, Containment ✅ CONTAINED, Observability ✅ VERIFIED, no high/critical CVE, no secret found, composition green.

**S-02.1 and S-02.2 are DONE.**

---

# Audit of Story S-02.3 — Console UI: staged wizard, review screen, pending-reviews list (SPEC-02)

**Verdict:** ⚠️ Pass with follow-ups — Done **NOT** blocked
**Scope:** S-02.3 only (S-02.1/S-02.2 already Done above, not re-audited).
**Commit audited:** `23e5a8e` (stamp) / `cbb7ef7` (implementation)
**Author:** alex@deanx.com.br (solo). *(Note: the `/test` stamp records `alex@divinocosta.com.br` — same operator, git identity differs; F-4 below, non-blocking documentation nit.)*
**Auditor:** Zangado

## Verdict: S-02.3 is DONE — SPEC-02 as a whole is COMPLETE.

All prerequisite gates confirmed satisfied:
- **Rigor gate (Risk: high):** stamp `Source: /test gap-fill (Atchim TDD gate)`, fresh independent Atchim instance (DEBT-44 honored).
- **Containment gate:** `HARDEN-02.md` ✅ CONTAINED; S-02.3 adds no new failure-prone seam — verified by a direct read of the new endpoint (below), not accepted on trust.
- Every S-02.3 DoD checkbox is met or explicitly waived (T-02.3.6, see below).

## Gate results (all re-run this pass)

- **Frontend `npx vitest run`:** 80/80 passed (7 files).
- **Backend `pytest -q`:** 2116 passed, 15 skipped (all `RUN_INTEGRATION_TESTS`-gated).
- **CT-07 composition test** (`tests/ui/test_review_values.py`): 9/9 passed — O(1) call-count proof, INV-02 sentinel-not-in-logs proof, zero-quota proof, 503/404, provenance drafted/edited, multi-doc completeness.
- **`pip-audit`:** no known vulnerabilities.
- **`gitleaks`** (real run, `7b3807c..23e5a8e`): no leaks found.
- **`check_clean.py`:** exit 0, advisory. 5 pre-existing stale artifacts flagged (`.DS_Store` ×2, three `logs/scheduled-runs/*.log`) — unrelated to S-02.3, not blocking.

## Staleness check — clean

`git diff cbb7ef7..23e5a8e --name-only` touches only docs (the stamp, DEBT/HANDOFFS/PROGRESS). No stamped `src/`/`tests/`/`frontend/` file changed after the stamp commit. No test-file deletions, no unexplained `.skip`/`.todo`.

## Direct code read of the new endpoint (T-02.3.7 only)

`fetch_platform_items` (api.py:184) + `GET /api/reviews/{session_id}/values` (api.py:1003), read directly:
- Read-only, zero quota — one platform GET, no subprocess, no IDP call. Correctly absent from `/api/health`'s `quota_spending_routes` and `TestQuotaBoundary`. Containment N/A holds.
- INV-02: the only log line (`review_values session_id=%s document_count=%d`) carries no field name or value — verified against the passing sentinel test.
- 503 on platform unconfigured/unreachable, 404/422 on session miss.
- No real/sensitive golden values anywhere in the new code, tests, or docs — synthetic sentinels only.

## NFR-02 walk

No new row warranted for S-02.3 — a pure frontend consumer plus one read-only addendum, its properties already covered by the existing suite and project invariants. All 8 feature rows remain ✅ PASS from the S-02.1/S-02.2 audit above.

## LLM-Evals

N/A confirmed — no LLM surface.

## T-02.3.6 (manual E2E run-through) — WAIVED, with reason

**Decision: explicit waiver, not a silent skip, not a blocker.**
- **Who/when:** Zangado, this `/qa` pass, 2026-09-28.
- **Why:** a meaningful EX-1/EX-2 run-through spends live IDP org quota on real files (`CLAUDE.md` forbids this casually), and live IDP submit/poll is itself still gated on **S-01.6** (a real action id + published version), which `## External services` records as not yet configured — so a true end-to-end wizard run cannot be performed on any corpus, synthetic or not, until that infra lands. A render-only smoke test would not exercise EX-1/EX-2's actual behaviors.
- **Why it's safe to waive at prototype rigor:** the UI behavior it would confirm is already covered by 80 passing component tests spanning all 6 ACs and every state branch, plus 9 backend values-endpoint tests and S-02.1/S-02.2's integration coverage.
- **Recommendation:** perform T-02.3.6 opportunistically on a synthetic corpus once S-01.6 provides a live action version. Does not gate S-02.3 Done.

## Findings

- **F-1 (new, non-blocking):** CT-07 tables/prompts shape drift — CT-07 and the ADR-0008 amendment declare a shaped `tables[{name,match_key,rows[]}]`/`prompts[{key,answer,source}]`, but the endpoint passes raw platform dicts through untransformed. `GoldenReviewTable` doesn't consume tables/prompts at all, so this is dead/unused in both layers and fails safe. Filed as new debt below. `escaped-atchim: yes` — a contract-vs-implementation mismatch outside the five-axis correctness focus, harmless because unconsumed.
- **F-2 (already filed, DEBT-148):** `missing_from_platform` dead contract, confirmed in code, fails safe. `escaped-atchim: no` — Atchim found and filed it.
- **F-3 (already filed, DEBT-149):** C2 regression test proves no-drop but not value-transmission. `escaped-atchim: no` — filed by the `/test` gate.
- **F-4 (nit, non-blocking):** `/test` stamp's Author address differs from the repo's git identity — same operator, cosmetic; correct at next touch.

## Done gate (S-02.3 / SPEC-02 overall)

No Critical, no Major, no code-level defect. **S-02.3 is DONE. SPEC-02 (S-02.1, S-02.2, S-02.3) is COMPLETE.**
