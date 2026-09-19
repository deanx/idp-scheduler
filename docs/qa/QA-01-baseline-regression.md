# QA-01 — S-01.1 Classifier & gate (Epic B, ADR-0003 risk:Low)

**Verdict:** ✅ Pass
**Rigor:** full (user override of the project's `standard` profile for this run)
**Story:** S-01.1 (pure classifier — `classify()` + `overall_gate()`, ADR-0003)
**Spec:** docs/specs/SPEC-01-baseline-regression.md (SPEC-01 risk: high; S-01.1 touches only Low-risk ADR-0003)
**TDD stamp:** docs/qa/TEST-S-01.1-baseline-regression.md — `Status: ✅ PASSED`, `Source: /test gap-fill (Atchim TDD gate)`, `Commit: 1d50e978`
**Author:** alex@divinocosta.com.br (solo mode — raw git config user.email)
**Date:** 2026-09-18
**Auditor:** Zangado (opus, structural independence vs Dengoso sonnet)

> **Re-audit note.** A prior `/qa S-01.1` at `standard` rigor returned **⚠️ Pass with follow-ups** (F-1 `.DS_Store`, F-2 gitleaks-absent, F-3 stamp prose typo). This full-rigor re-audit upgrades the verdict to **✅ Pass**: every gate is either green or N/A with an explicit reason — none silently skipped. F-1 and F-3 are closed; F-2 remains open as **DEBT-12** (tooling gap, by design — the classifier is pure with no secret surface; a real gitleaks gate is owed before S-01.4's credential surface). No new findings.

The classifier is correct, typed, pure (INV-02), fast (N2 p95 ~1.36 ms), and well-tested (82 pass, N22 at field+table+prompt). This is the CI-gate component. **S-01.1 is Done.**

## Entry gates

1. **Spec + DoD read:** SPEC-01 S-01.1. DoD covers AC2/AC3/AC4/AC6, BR2/BR3/BR8, CT-02, purity, N22, N2, INV-02, observability-N/A-by-design, Atchim review, Zangado sign-off.
2. **TDD stamp:** present, `✅ PASSED`, `Source: /test gap-fill (Atchim TDD gate)`. Confirmed.
3. **Rigor gate (step 2b):** SPEC-01 `Risk level: high`; stamp Source is `/test gap-fill (Atchim TDD gate)` → **PASS** (independent /test-sourced audit, not /implement-sourced).
4. **Stamp freshness:** `git log --oneline 1d50e978..HEAD -- src/idp_regression/classifier tests/classifier pyproject.toml uv.lock` → empty. The only commit since the stamp is `81cf903` (pyrightconfig.json — NOT in the stamp's Files set). Uncommitted working-tree edits this session are to `docs/adr/0003*`, `docs/design/DATA-MODEL-01*`, `docs/design/SEQ-UC-01*`, `docs/state/*` — none in the Files set. **Fresh.** HEAD = `81cf903`.
5. **slug:** `baseline-regression`; report path `docs/qa/QA-01-baseline-regression.md`.

## Mechanical floor

- **Tests:** `uv run pytest -q` → **82 passed** in 0.31s (matches stamp; no drop).
- **N2 perf (pytest-benchmark):** `test_classify_and_gate_under_100ms_p95` — 200 rounds, min 1.29 ms / mean 1.36 ms / max 1.66 ms / median 1.35 ms. p95 ~1.36 ms << 100 ms budget. ✅ observed.
- **Static (gate of record):** `uv run mypy src tests` (strict) → Success, 0 issues / 16 files. `uv run ruff check src tests` → All checks passed.
- **pyright (info, not gate of record):** 0 errors / 0 warnings / 0 informations — DEBT-06 closed by `pyrightconfig.json` (`extraPaths:["src"]`); the prior `reportMissingImports` false-positive noise is gone.
- **TDD spot-check:** `git diff --name-status 1d50e978 HEAD | grep "^D.*test"` → empty (no test files deleted). No `pytest.skip`/`.todo`/`@pytest.mark.skip` in `tests/classifier/`.
- **pip-audit:** `No known vulnerabilities found` (self-package `idp-regression-tester` skipped — not on PyPI, expected). **Clean.**
- **Secret scan:** `gitleaks` binary ABSENT (DEBT-12, open). Ran grep fallback over `src/` + `tests/` for `sk-…`, `AKIA…`, `ghp_…`, `client_secret=`, `LANGFUSE_SECRET_KEY=`, `password=`, `BEGIN … PRIVATE KEY`, `Bearer …` → **no hits**. Classifier is pure, no secret surface.

## DoD checklist

- [x] AC2/AC3/AC4/AC6 verifiable in code (`gate.py`, `canonical.py`)
- [x] Unit tests pass (82); spike's 11 ported + edge matrix + N22 gap-fill
- [x] TDD stamp present, fresh (Atchim's /test TDD gate)
- [x] Observability: classifier is pure — no logging/telemetry (INV-02). Telemetry is the orchestrator's job (S-01.4/N10). Correct by design.
- [x] Atchim review: APPROVED (round-3, commit 1d50e978, structural sonnet≠opus) + /test TDD gate PASS
- [x] **Code quality:** typed `ClassifierError` family (`MalformedGoldenError`/`MalformedActualError` subclass `ClassifierError`, test_validation.py:160); inputs validated at the `classify()` trust boundary (N22) at field+table+prompt; naming aligned with glossary verdicts; no commented-out code; no TODO without issue; 12 traceable incremental commits.
- [x] **Security & compliance:** classifier is pure — **no I/O, no adapter, no platform imports** (INV-02 verified: only `re`, `datetime`, `collections.abc`, `typing`, `__future__`, internal `idp_regression.classifier.*`). Never sees document files, never touches the platform, never logs. PII/golden-value plaintext logging N/A by construction. No secrets in src/tests.
- [x] **Regressions:** CT-02 contract test passes (12); full suite green; no adjacent features touched (classifier is a leaf dependency consumed only by the orchestrator, not yet built).

## NFR-01 verdicts (row-by-row; written back to docs/qa/NFR-01.md)

Feature rows S-01.1 owns (verified with measurement this audit):
- **N2 — ✅ PASS** — test_performance.py:67, 50 fields + 500 rows, 200 rounds, p95 ~1.36 ms << 100 ms.
- **N22 — ✅ PASS** — test_validation.py: typed `ClassifierError` at field (36–115), golden table row (118), actual table cell (140), golden prompts (195/203/211/219 — 4 tests), actual prompts top-level (227) + per-cell (235). Subclass check :160, loud-not-silent-match :166. 21 tests. mypy strict confirms typed surfaces.

Feature rows owned by later stories (⬜ PENDING — correct, not S-01.1's gate): N1, N3, N6, N7, N8, N10, N14, N15, N16, N28 (→ S-01.4); N21 (→ S-01.2); N26 (→ S-01.3).

System rows (⚠️ WAIVED → /signoff): N4, N5, N9, N11, N12, N13, N17, N18, N19, N20, N23, N24, N25, N27.
- Signoff note: N5 — classifier emits no logging (pure, INV-02), so N5's plaintext-logging concern is satisfied-by-design for this module; the system-level N5 gate binds S-01.2/S-01.3/S-01.4. N17 — `uv.lock` committed (pytest-benchmark pin), verified present.

## Per-gate verdicts (full rigor — every gate run, every gate recorded)

- **8c Containment: N/A for this story (recorded, not dropped).** NFR-01 marker `Containment: REQUIRED (binds S-01.4)`. S-01.1 is pure (INV-02 verified — no I/O/adapter/platform imports). Its fault boundary is the typed `ClassifierError` validation (N22, tested at field+table+prompt). `HARDEN-01.md` does not exist yet — by design; it is owed by S-01.4 (the orchestrator) before the epic is Done. Containment does not bind a pure leaf module with no failure-cascade surface; false-blocking it would be wrong.
- **8d UI: N/A** — S-01.1 has no UI.
- **8e LLM-evals: N/A** — NFR-01 marker `LLM-Evals: N/A`. No LLM calls; deterministic comparison.
- **8f Observability: N/A for this story (owed by S-01.4/N10).** NFR-01 marker `Observability: REQUIRED (owed by S-01.4/N10; N/A for pure module)`. S-01.1 emits no telemetry (pure, INV-02). The `Observability: ✅ VERIFIED` QA report line is owed by S-01.4. Recorded, not dropped, not blocked.
- **8g SCA + secret scan: PASS (with tooling caveat).** pip-audit clean. gitleaks absent → grep fallback (clean). DEBT-12 tracks installing gitleaks before S-01.4's credential surface. No committed secret.
- **8h Composition: PASS.** CT-02 contract test `tests/classifier/test_classify_contract.py` → 12 passed, no drift. INV-02 purity: real-import grep of `src/idp_regression/classifier/` → only `re`, `datetime`, `collections.abc`, `typing`, `__future__`, internal `idp_regression.classifier.*` — no adapter/platform/orchestration/os/sys/pathlib/open()/requests/httpx/asyncio/dotenv. CT-01/CT-03/CT-04 and INV-01/INV-03/INV-04/INV-05/INV-06/INV-07/INV-08 are not in scope for S-01.1 (owned by S-01.2/S-01.3/S-01.4) — recorded as "not in scope", not dropped.
- **8i Cleanliness: PASS.** `check_clean.py` → `clean — no stale artifacts`, exit 0. `.DS_Store` and `docs/.DS_Store` stay gone; `.gitignore` carries `.DS_Store`.

## Findings

**Critical (blockers):** none.
**Major:** none.
**Minor / new findings:** none — no new findings at full rigor.

Existing non-blocking debt carried (already in docs/state/DEBT.md, not re-opened): DEBT-04, DEBT-05, DEBT-09, DEBT-10, DEBT-11, DEBT-12 (all open, all non-blocking, all for Soneca/Dunga/Mestre — not code defects). DEBT-02, DEBT-06, DEBT-07, DEBT-08 closed by the top-level /loop this session.

### Prior ⚠️ follow-ups (current state)
- **F-1 (.DS_Store at root + docs/.DS_Store):** **closed.** Both files absent; `.gitignore` carries `.DS_Store`; `check_clean.py` exit 0.
- **F-2 (gitleaks binary absent):** **open as DEBT-12 (by design).** gitleaks still not installed; grep fallback ran clean. DEBT-12 tracks Mestre installing gitleaks before S-01.4 `/qa` (credential surface). Not blocking S-01.1 (pure module, no secret surface).
- **F-3 (stamp prose typo "5 golden-side" → 4):** **closed.** Stamp line 47 now reads "the 4 golden-side prompt tests". Verified: `test_validation.py` 195/203/211/219 = 4 golden-side, 227/235 = 2 actual-side, 6 total. Coverage intact.

## Regression-worthiness (Atchim PIN ruling, on record from /test)

- **REG-01 — PIN, covered.** `test_actual_prompt_cell_non_mapping_raises_malformed_actual` (test_validation.py:235) guards the non-dict-cell-leaks-`.get`-AttributeError defect class (2nd occurrence: table-cell caught in /implement round-1 commit 921e270; prompt-cell caught in /test Scenario B → fixed commit 1d50e978). Test exists and passes.
- **REG-02 — PIN, covered.** Golden-side prompt malformation pins (test_validation.py:195/203/211/219) — pin pre-existing `_validate_golden` prompt validation that had no direct coverage. Tests exist and pass.

No new confirmed defects at full rigor → no new PIN/SKIP ruling needed; the prior ruling stands. (9a escape tags: not applicable — no confirmed defects this round; the classifier was reviewed by Atchim across 3 rounds + the /test TDD gate and the re-audit found nothing that escaped that lens.)

## Board sync (boardless — state only)

Board: none in CLAUDE.md → no Trello card move. S-01.1 is **Done** in state (`docs/state/PROGRESS.md`). The remaining UC-01 stories (S-01.2/S-01.3/S-01.4) are blocked on credentials (IDP + Langfuse, both MISSING — Mestre) + upstream + (S-01.4) `/harden`; S-01.5/S-01.6 spikes need live access.

## Bottom line

S-01.1 meets its DoD at full rigor. The pure classifier is correct, typed, pure (INV-02), fast (N2 p95 ~1.36 ms), and well-tested (82 pass, N22 at field+table+prompt). Every gate is either green or N/A with an explicit reason. F-1 and F-3 are closed; F-2 is open as DEBT-12 by design. No new findings. **S-01.1 is Done.**