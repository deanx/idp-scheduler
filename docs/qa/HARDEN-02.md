Verdict: ✅ CONTAINED

> **Re-probed 2026-09-28 after DEBT-142/DEBT-143 fixes (commit `c5da2d7`, test coverage added at `d95eee2`) — see "Re-probe" section at the end. Original first-pass findings are preserved below unmodified for audit history; the re-probe section is authoritative for current status.**

# HARDEN-02 — Containment audit, SPEC-02 (S-02.1 / S-02.2)

**Target:** S-02.1 (two-stage job orchestration, ReviewSession, INV-09 gate) and S-02.2 (golden edit/replace API), both `/test`-stamped PASSED (`docs/qa/TEST-S-02.1-console-golden-review-gate.md`, `docs/qa/TEST-S-02.2-console-golden-review-gate.md`).
**Auditor:** Branca (read-only; every probe below was executed against the real implementation via `TestClient(create_app(...))`, not reasoned abstractly).
**Read:** SPEC-02, ADR-0008 (incl. R1 amendment + Branca's round-1 containment section), NFR-02 (N1–N9), both `/test` stamps, and the real implementation (`ui/api.py`, `ui/review_sessions.py`, `ui/golden_edits.py`, `ui/jobs.py`, `ui/workspace.py`).

## Domain B (Prompt/LLM security): N/A — confirmed in code

The touched surfaces (`api.py` edit/replace/review routes, `review_sessions.py`, `golden_edits.py`) compose no prompt and process no model output. Golden edit/replace inputs are schema-validated JSON (`jsonschema.Draft7Validator(load_golden_schema())` + `validate_golden_structure`), never prompt text. No LLM call, no `eval`, no model-output sink. ADR-0008's "none applicable" claim holds in the implementation. **Domain B N/A.**

## Domain A (fault containment) — 6 probes

### Probe 1 — INV-09(e) golden-hash under platform-read failure — ⚠️ PARTIAL (one real weakness)

- **1a** `fetch_golden_hash → None` at `/reviews/{id}/complete`: HTTP **409**, session stays `drafted`, `approved_golden_hash` stays `None` (never persisted). ✅ fail-closed.
- **1b / 1b2** `verify-candidate/start` with current-hash `None` (and with both hashes `None`): HTTP **409**, state→`stale`. ✅ The "both-None" case the test stamp called "untested-but-unreachable" is in fact reachable and correctly refused — `None == None` is never treated as a match.
- **1a2 (new path Atchim didn't consider)** `fetch_golden_hash` **raising** (not returning None) at `/complete`: the exception is **uncaught** and propagates as a 500. Containment-safe (no None persisted, state stays `drafted`, no spend) but ungraceful — `fetch_golden_hash` only catches `OSError`; a JSON/`AttributeError`/other failure becomes a 500 instead of the intended clean 409. ⚠️ hardening nit.
- **1c (the real finding) — empty-dataset vacuous hash match.** The hash is `sha256(json.dumps(sorted(items)))`. Over **zero** platform items this is a fixed constant, **not** `None`. Executed end-to-end: dataset empty at `/complete` AND empty at `verify-start` → `/complete` **200** (records the empty-set hash as `approved_golden_hash`), `verify-start` **200** → state `verifying`. **INV-09(e) is satisfied vacuously for an empty golden set.** A draft/pin stage that silently wrote zero platform items (crash, silent platform-write failure) would produce an empty dataset that sails through the swap-guard. The only backstop is `verify_document.py`'s own empty-pin-set refusal — out of this story's scope and not exercised by any CI-runnable test here. Note the *deletion-to-empty* case is caught (H_N ≠ H_empty → 409 stale); only *empty-at-both-times* is vacuous.
- **Recommendation:** refuse an empty item list explicitly at `/complete` (treat `items == []` like `None`), so the guard can't be satisfied by "nothing was drafted."

### Probe 2 — Review-session file under corruption / wrong types — ✅ CONTAINED (one nit)

- Truncated/torn file → `ReviewSessionCorruptError` → **422** at verify-start. ✅
- Wrong-typed **required** str field (`dataset` as int), bogus `state` string → `ReviewSessionCorruptError`. ✅
- Writer uses `tempfile.mkstemp` + `os.fsync` + `os.replace` (atomic within-dir rename): a reader sees the old complete record or the new one — **no torn-read window** from this code's own writes. ✅
- Nit: **optional** fields are not type-checked in `from_dict` — `approved_golden_hash=99999` (int) and `stage2_job_id=12345` (int) load intact. **Not exploitable**: `current_hash` is always a sha256 hex string, so an int `approved_golden_hash` can never `==` it → confirmed **409 stale**. Defense-in-depth only; recommend asserting `str | None` on load.

### Probe 3 — no-flock review pause + stage-2 TOCTOU — ⚠️ PARTIAL (contained by flock only, as ADR admits)

Two **concurrent** `verify-candidate/start` on the same `reviewed` session: **both** passed INV-09(a) state-check and INV-09(e) hash-check and **both reached `registry.start`** (entered=2, both HTTP 200 in the probe). The session state machine (`reviewed`→`verifying`) does **NOT** serialize them — both load `reviewed` before either persists `verifying`. The *only* thing preventing a double-spend is the workspace `flock`, which `registry.start` holds for the **job's lifetime**: in production a real verify batch runs for minutes, so the second `start` hits `WorkspaceBusyError` → 409. Double-spend would require the first job to acquire-extract-and-release within a ~0.25s retry budget — not a realistic verify batch (and a fast-failing verify spends nothing). This matches ADR-0008's explicitly-accepted residual ("a narrow TOCTOU … bounded to seconds under the re-taken flock"). **Contained in practice, but the guard is the flock, not the gate** — there is no session-level lock/CAS. Acceptable as designed; flagged so it isn't mistaken for a state-machine guarantee.

N7 (unrelated job starts during a pending review): structurally true — `save_session` never locks; safe because stage-2 re-takes the flock and re-checks INV-09(e). ✅

### Probe 4 — Golden-edit write paths under mid-operation platform failure — ❌ GAP (blocking the stated guarantee)

`POST /reviews/{id}/replace` with `upsert_platform_item` failing on the **3rd of 5** entries: HTTP **502**, but `['doc0','doc1']` were **already written to the platform** before the failure. **The platform now holds a partial replace (docs 0–1 new, docs 2–4 old-draft).** This directly contradicts ADR-0008 §Containment-4 and S-02.2 DoD: *"all-or-nothing … a failed replace leaves the drafted set intact."* The all-or-nothing property is enforced only on **validation** (all entries validated before any write); the **write loop has no rollback / no staging**.

- For a `reviewed` session, the partial write is caught downstream: state stays `reviewed` with the *old* `approved_golden_hash`, so verify-start recomputes over the mutated dataset → mismatch → 409 stale → **no quota spent against the partial set.** Good — it cannot produce a silently-wrong GREEN.
- For a `drafted` session (probe 4b: state stayed `drafted`, provenance `{}`), the curator is told "replace failed" and — per the documented contract — reasonably assumes the drafted set is intact. It isn't. A subsequent `/complete` hashes and can approve the mongrel set. Also, the provenance-update loop runs *after* the writes, so on 502 the already-written docs 0–1 are recorded as neither edited nor rolled back — the UI would render them as untouched "drafted" values.
- Severity: moderate. Not the worst-class fail-open (INV-09(e) still binds review↔verify), but a real violation of a stated containment guarantee that can mislead a curator into approving a half-replaced golden.
- PATCH single-field edit is fine — one upsert, validated-before-write, no cross-entry partiality.

**Recommendation:** make `/replace` genuinely all-or-nothing (stage to a temp dataset then swap, or capture prior items and compensate on failure), or at minimum mark the session non-approvable / force a re-draft on partial failure and correct the docstring/DoD claim.

### Probe 5 — N5 over-ceiling ordering — ✅ CONTAINED

`draft-golden/start` with plan returning `CEILING+1` (1001): HTTP **422**, and **no session file created** (`sessions_dir` diff empty). The ceiling check sits between `jobs.plan` and `ReviewSession` construction, and `plan()` itself runs before any `registry.start`, so nothing is written and no quota is spent on refusal. No window observed. ✅

### Probe 6 — Stage-2 start idempotency — ✅ CONTAINED

- Double `verify-candidate/start` on a `verified` session: HTTP **200, 200**, **zero** new subprocess starts (returns the existing job). ✅
- `verify-start` on a `verifying` session: **409** (state ≠ `reviewed`), no start. ✅
- (The only non-idempotent path is the truly-concurrent race in Probe 3, contained by flock.)

## Cross-cutting note (not a probe, but load-bearing for the verdict)

NFR-02 **N9** states the session "binds to its corpus by both the document directory **and the archive's sha256, checked again at stage-2 start**." The implementation checks **only `doc_dir.is_dir()`** at verify-start; `archive_sha256` is explicitly **not** compared, delegating byte-level integrity to `verify_document.py`. Defensible (that script refuses changed bytes), but N9-as-written is satisfied by an out-of-scope subprocess that no CI-runnable test in this story exercises — the disk-bytes containment claim rests entirely on the integration-gated downstream. Already flagged scope-honestly in the S-02.1 stamp; re-raised here as a containment dependency, not a new defect.

## Summary

| Probe | Seam | Verdict |
|---|---|---|
| 1 | INV-09(e) under platform-read failure | ⚠️ PARTIAL — fail-closed on None/exception, but ❌ vacuous match on empty dataset |
| 2 | ReviewSession file corruption | ✅ CONTAINED (nit: optional-field types unchecked, not exploitable) |
| 3 | No-flock review pause / stage-2 TOCTOU | ⚠️ PARTIAL — contained by flock duration only, matches ADR's accepted residual |
| 4 | Golden-edit replace under mid-op failure | ❌ GAP — not actually all-or-nothing on the write side |
| 5 | N5 ceiling-before-session-creation | ✅ CONTAINED |
| 6 | Stage-2 start idempotency | ✅ CONTAINED |

## Blocking gaps (card via `/debt add` / Dunga)

1. **[Moderate] Partial golden-set replace (Probe 4).** `/replace` violates its own stated all-or-nothing guarantee on platform-write failure. A `drafted`-state session can end up holding a half-replaced golden that the curator is told is "intact." Fix: stage-then-swap, or capture-and-compensate on failure, or explicitly force the session non-approvable/re-draft on partial failure and correct the DoD/docstring claim.
2. **[Low] Empty-dataset vacuous INV-09(e) match (Probe 1c).** `/complete` should refuse `items == []` the same way it refuses `None`, so the swap-guard can't be satisfied by "nothing was drafted."

## Non-blocking hardening nits (→ `/debt`)

- `fetch_golden_hash` only catches `OSError`; other exceptions (JSON parse, `AttributeError`) propagate as an ungraceful 500 at `/complete` instead of a clean 409 (Probe 1a2).
- Optional `ReviewSession` fields (`approved_golden_hash`, `stage2_job_id`) are not type-validated on load — not exploitable today (type mismatch always fails the equality check downstream) but worth asserting `str | None` for defense-in-depth (Probe 2).
- Stage-2 double-spend protection rests on flock duration, not a session-level lock/CAS — matches ADR-0008's explicitly accepted residual, not a new finding, but worth naming so it isn't mistaken for a state-machine guarantee (Probe 3).
- N9's `archive_sha256` claim rests entirely on `verify_document.py`'s own pin-sha check, which no CI-runnable test in this story exercises — confirm this dependency is understood, not silently assumed covered.

Probe scripts were executed against the real implementation (`TestClient(create_app(...))`); all evidence above is from their observed output.

---

## Re-probe — 2026-09-28, after DEBT-142/DEBT-143 fixes (commit `c5da2d7`, tests `d95eee2`)

**Verdict: ✅ CONTAINED.**

### Domain B: N/A — unchanged. No LLM surface introduced by either fix.

### Probe 1c (DEBT-143 — empty-dataset vacuous match) — ✅ FIXED

`fetch_golden_hash` now returns `None` for an empty item list, so the existing None→409 guard at `/complete` catches it. Re-executed: empty dataset → `/complete` **409**, session stays `drafted`, `approved_golden_hash` stays `None`. Regression check on the normal (non-empty) path: `/complete` → **200**, state `reviewed`, real hash recorded — unaffected.

### Probe 4 (DEBT-142 — partial replace on mid-batch failure) — ✅ FIXED (fail-closed), one doc-accuracy nit

Re-ran the failure-on-3rd-of-5 probe against both starting states:
- **DRAFTED start:** `/replace` → 502; session → `replace_failed` (no longer silently `drafted`); hash cleared. `/complete` → 409. ✅
- **REVIEWED start:** identical outcome — 502, `replace_failed`, hash cleared, `/complete` refused. ✅
- **Repair via a subsequent full `/replace`:** succeeds (200), state → `drafted`, then `/complete` → 200, `reviewed`. Approval correctly re-enabled. ✅

Expected physical note (not a defect): the partial write still physically lands on the platform (the platform has no transaction/staging API) — the chosen fix (option c, `REPLACE_FAILED`) is a non-approvable state guarding the mongrel set, not prevention of the partial write itself. Correct containment given the real constraint: no path lets a partial golden be approved, and INV-09(e) still binds review↔verify as a backstop.

**Nit (non-blocking, → `/debt`):** a PATCH repair of a `REPLACE_FAILED` session does NOT clear the state — the PATCH handler's transition only maps `REVIEWED→DRAFTED`, not `REPLACE_FAILED→DRAFTED`, so `/complete` stays 409 after a PATCH-only repair. This is fail-closed and arguably correct (a single-field edit doesn't prove the whole partially-written set is coherent again — only a full `/replace` re-writes every entry), but it contradicts an implicit expectation that per-field repair would also clear the state. Recommend documenting "only a full `/replace` repairs `REPLACE_FAILED`" or adding that transition to PATCH if per-field repair is meant to work — filed as a UX/liveness debt item, not a safety gap.

### Probes 2, 3, 5, 6 — ✅ unchanged, no regression

- **Probe 2** (session-file fail-closed reads): unchanged; the new `replace_failed` enum value parses correctly, bogus states still refused.
- **Probe 3** (no-flock pause / stage-2 TOCTOU): unchanged, contained by flock duration as ADR-0008 explicitly accepts.
- **Probe 5** (N5 ceiling ordering): unchanged, no window.
- **Probe 6** (stage-2 idempotency): unchanged, genuinely idempotent.

### Coverage note (informational — not production code)

Confirmed `d95eee2`'s independent `/test` gap-fill added `test_inv09e_refuses_when_both_hashes_are_none` — a direct both-hashes-None unit assertion at the verify-start layer, closing the "untested-but-unreachable-by-construction" note carried from the original S-02.1 stamp. The N7 no-flock test was also upgraded from a trivially-true `is_busy()` read to a real unrelated-job-starts proof (see DEBT-146 for a residual test-quality nit on the specific "actually start" variant, unrelated to this containment re-probe).

### Summary — final status

| Probe | Seam | First pass | Re-probe |
|---|---|---|---|
| 1 | INV-09(e) under platform-read failure | ⚠️ PARTIAL (vacuous empty-match) | ✅ FIXED |
| 2 | ReviewSession file corruption | ✅ CONTAINED | ✅ unchanged |
| 3 | No-flock review pause / TOCTOU | ⚠️ PARTIAL (ADR-accepted residual) | ✅ unchanged (same accepted residual) |
| 4 | Golden-edit replace under mid-op failure | ❌ GAP | ✅ FIXED |
| 5 | N5 ceiling-before-session-creation | ✅ CONTAINED | ✅ unchanged |
| 6 | Stage-2 start idempotency | ✅ CONTAINED | ✅ unchanged |

Both blocking findings (DEBT-142, DEBT-143) are closed and verified by execution, not just code reading. `/qa`'s containment gate may treat this report as current.
