# /test stamp — SPEC-01

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-21 (re-stamp — FO-5)
**Commit (reviewed code):** `1428520` — **re-gated 2026-09-22** by a fresh Atchim instance (DEBT-44) after S-01.4 added the additive alias `validate_golden_structure = _validate_golden` to `gate.py` (+11 lines), which had staled this stamp (DEBT-46). Falsifiable check, re-baselined: `git diff --stat 1428520 HEAD -- src/idp_regression/classifier/` must be empty. Re-gate evidence: classifier suite 104 passed standalone; N2 mean 1.38 ms vs the 100 ms budget; INV-02 purity intact (the dependency runs orchestration → classifier, never the reverse); the FO-5 fail-open stays closed — disarming both unknown-verdict guards in `overall_gate` kills two tests; alias-as-wrapper mutation killed; 3 further spot-mutants killed. Prior history: `6d525ef` (FO-5), stamp commit `ab0734d`; original `1d50e978`.
**Author:** alex@divinocosta.com.br  <!-- solo mode — raw git config user.email -->
**Atchim TDD gate:** PASSED (2026-09-21, Opus 5, fresh instance per DEBT-44 — it had seen none of these diffs). **0 findings against S-01.1.**

**Scope of this re-stamp — FO-5, `6d525ef`.** `overall_gate` (`classifier/gate.py`) fell through to `return "PASS"` for any verdict outside the six `VerdictLiteral` values, at **both** the top-level and the per-row site inside a `detail` entry. Not reachable via `classify()` (pure, emits only the six) but `overall_gate` is **public API** taking a caller-supplied `VerdictMap`, and the realistic path is extension — add a seventh verdict, forget the two tuples, and the CI gate goes green on it. **Fail-open toward PASS on the one function whose entire job is to be trusted.**

**RED reproduced independently, in the claimed shape.** The reviewer's first attempt via pytest was inconclusive (the module fails to *collect* pre-fix, which is red but says nothing about behaviour), so it drove the pre-fix function directly:
```
top-level unknown verdict  -> RETURNED: 'PASS'   <-- NO RAISE
unknown row verdict        -> RETURNED: 'PASS'   <-- NO RAISE
control (wrong_value,crit) -> RETURNED: 'FAIL'
```
**Silent `"PASS"` at both sites, with a control proving the function was otherwise working** — a true fail-open, not a broken fixture. Post-fix both raise `MalformedActualError`, and INV-02 holds: neither `match_key` nor the value appears in the message.

**The drift pin survived adversarial falsification.** The reviewer built a four-scenario matrix specifically to break it:
```
GREEN | A baseline
GREEN | B production hand-written, Literal unchanged   [honest negative]
GREEN | C Literal extended, production DERIVED
RED   | D Literal extended + production hand-written   [pin fires]
```
Its note: *"this is the first stamp cycle in this story where the headline mechanism claim survived my attempt to falsify it."* The valid set derives from `frozenset(get_args(VerdictLiteral))` — not a hand-written tuple (DEBT-40/43/47).

**Accepted honest-negative, documented in the test's own words:** replacing the derived set with a today-equivalent literal survives, and `test_gate.py:211` says so explicitly. Do not let anyone claim it is covered.
**Independence:** ✅ structural (different models) — implementer Dengoso (sonnet) reviewed by Atchim (opus). SPEC-01 risk level is **high** (driven by ADR-0001/0002/0004), so the high-risk Done gate requires structural reviewer independence; sonnet ≠ opus satisfies it. S-01.1 itself depends only on the Low-risk ADR-0003.
**Static:** ✅ clean (2026-09-21 re-verification) — `uv run mypy src/idp_regression/classifier tests/classifier` → 0 issues / **11 files**; `ruff check .` → clean; `tests/classifier` → **103 passed**. ⚠️ **Rigor profile changed to `prototype` on 2026-09-21** (user decision, `ab0734d`) — **this stamp predates nothing and waives nothing**: `Risk level: high` still requires a `/test` stamp because `/qa` reads risk from the SPEC header, not the profile.

**Superseded original static line:** `uv run mypy src/idp_regression/classifier tests/classifier` (strict) → 0 issues / 11 files; `uv run ruff check` → All checks passed. Pyright LSP `reportMissingImports` is a known src-path config gap (DEBT-06), not the gate of record — mypy is the configured static-analysis gate per CLAUDE.md `## Tooling`.

## Files (Dengoso's record — repo-relative, git-independent)
src/idp_regression/classifier/__init__.py, src/idp_regression/classifier/types.py, src/idp_regression/classifier/canonical.py, src/idp_regression/classifier/gate.py, tests/classifier/test_classify.py, tests/classifier/test_gate.py, tests/classifier/test_tables.py, tests/classifier/test_validation.py, tests/classifier/test_edge_matrix.py, tests/classifier/test_classify_contract.py, tests/classifier/test_performance.py, pyproject.toml (+pytest-benchmark dev dep, mypy/ruff/pytest config), uv.lock (pytest-benchmark pin)

## Sequence (TDD per slice — test file before/with impl, co-committed per slice)
Branch `feat/S-01.1-baseline-regression`. ADR-0003 is risk:Low; the SPEC-01 DoD waives a stricter TDD ordering gate ("the unit suite IS the gate"), so co-committed test+impl per slice is accepted. Atchim git-verified the sequence (`git log --diff-filter=A`): each test file lands in the same commit as (or before) its implementation slice. The /test prompt-level N22 gap-fill tests + the /implement Scenario-B fix (the per-cell actual-prompt dict check) landed together in `1d50e978` — consistent with the co-commit waiver and the /test-Scenario-B-then-/implement pattern (RED observed by /test, GREEN by /implement).

1. `0a07f4f` — T-01.1.1: `test_classify.py` + `types.py`+`canonical.py`+`gate.py` (classify field-level six verdicts + four types)
2. `5048496` — T-01.1.2: `test_gate.py` + `overall_gate()` BR2/BR3 + TypedDict NotRequired
3. `9ea13f4` — T-01.1.3: `test_tables.py` + line-item `match_key` pairing + new_line/missing rows (BR8)
4. `94f8846` — T-01.1.4: `test_validation.py` + typed `ClassifierError` family + input validation (N22 fields/prompts)
5. `6077c58` — T-01.1.5: `test_edge_matrix.py` (11 spike tests ported + canonical-form/criticality edge matrix; EX-A1-1/EX-A1-5)
6. `74e6d75` — T-01.1.6: `test_classify_contract.py` (CT-02: Verdict TypedDict + six literals + key union)
7. `40dd3b9` — T-01.1.7: `test_performance.py` (N2 pytest-benchmark <100ms p95 over 50 fields / 500 rows)
8. `e70c465` — lock pin (pytest-benchmark, NFR N17)
9. `921e270` — fix: N22 table-shape validation (golden table rows must be dicts; actual table cells must be dict-with-`value`) + 2 regression tests (Atchim REQUEST CHANGES round 1)
10. `9e5bacb` — refactor: canonicalizer class hierarchy → registry of callables (ADR-0003 §Design patterns conformance)
11. `f90a091` — test: drop dead `r.get('field')` clause + pin `RowVerdict` contract keys
12. `1d50e978` — fix: N22 validate actual prompt cells are dicts (Scenario B from /test) + 6 prompt-level malformation tests (the /test gap-fill)

## /test gap-fill (this stamp)

**Step 1 — independent coverage audit (fresh agent, no implementation context):** 13/14 S-01.1 items COVERED on the first pass; 1 PARTIAL gap — TP-21/NFR N22 had field-level and table-level malformation coverage but NO prompt-level coverage. After the gap-fill + fix, the re-audit found **ALL S-01.1 ACs/TPs COVERED** (AC2/AC3/AC4/AC6, BR2/BR3/BR8, TP-02/03/04/06/07/08/10/11/14/21, EX-A1-1/A1-2/A1-4/A1-5, CT-02, N2, N22, four field types, new_line/missing) with file:line evidence — no remaining gap.

**Step 2 — Dengoso wrote 6 prompt-level N22 tests** (5 PASS pinning existing `_validate_golden`/`_validate_actual` behavior; 1 RED). The RED test `test_actual_prompt_cell_non_mapping_raises_malformed_actual` revealed **Scenario B**: `_validate_actual` validated `fields` and `tables` per-cell but NOT `prompts` per-cell, so a non-dict actual prompt cell leaked a raw `AttributeError` (`acell.get("answer")` in `_classify_prompt`, gate.py:173) instead of typed `MalformedActualError` — same defect class Atchim caught for tables in /implement round-1 (commit 921e270). Per /test protocol, /test did NOT write implementation; it routed the Scenario B to /implement.

**/implement fix (commit 1d50e978):** Dengoso added a per-cell `isinstance(pcell, dict)` check to `_validate_actual`'s prompts loop (dict-check-only; an `answer`-presence check was deliberately NOT added — `_classify_prompt` tolerates a missing `answer` → `missing` verdict, so requiring it would tighten behavior beyond NFR N22 / ADR-0003 and break the "actuals may be incomplete → `missing`" contract; Atchim confirmed NOT an under-fix). The RED test turned GREEN. 82 passed, mypy strict + ruff clean. Atchim five-axis + TDD review: APPROVE (see Atchim review below).

**Step 3 — Atchim TDD gate (opus):** PASS, overall and per test file. Mutation spot-checks: all four trivially-wrong mutants (always-match-on-critical, position-based row pairing, gate-FAILs-on-wrong_format, new_field-fails-gate) are caught by the suite. TP-21/N22 confirmed covered at field + table + prompt level. TDD sequence git-verified consistent with the ADR-0003 co-commit waiver. No REQUEST CHANGES.

## Atchim review

**/implement round 1 (initial review of the original S-01.1):** REQUEST CHANGES — one Required finding (N22 table-shape validation gap: non-dict golden table row / actual table cell leaked a raw `AttributeError` instead of a typed `ClassifierError`) + 4 Suggestions. TDD gate, purity, correctness, performance all PASS.

**/implement round 2 (re-review after table-shape fix):** APPROVE — Required finding CLOSED; both error types subclass `ClassifierError`. 76 tests pass, benchmark p95 ~1.37ms << 100ms N2.

**/implement round 3 (re-review of the /test-Scenario-B fix, commit 1d50e978):** APPROVE — five-axis PASS, TDD gate PASS. Dict-check-only fix is the correct minimal closure of the N22 leak class (NOT an under-fix — an `answer`-presence check would tighten behavior beyond NFR N22 / ADR-0003). No Critical/Required findings. Suggestions: (a) prompts-vs-fields `answer`-presence asymmetry — a DEBT candidate for Dunga (behavior change, separate story if wanted), NOT a defect; (b) missing trailing newline in test_validation.py (cosmetic). Regression-worthiness ruling: PIN `test_actual_prompt_cell_non_mapping_raises_malformed_actual` (second occurrence of the non-dict-cell-leaks-`.get`-AttributeError defect class) and the 4 golden-side prompt tests (pin pre-existing validation that had no direct coverage).

**/test TDD gate (opus, this stamp):** PASS — per file and overall. Tests exercise AC behavior (not internals); assertions are discriminating (all four mutants red); names read as spec statements; every S-01.1 AC/Test-plan row has ≥1 meaningful test; TDD sequence git-verified.

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Story tests | 104 | 0 |
| Bug-repro (`@bug-repro`) | — | 0 (none) |

`uv run pytest -q` → **82 passed** in ~0.32s. Per-file: test_classify 10, test_gate 8, test_tables 8, test_validation 21 (15 original + 6 prompt-level gap-fill), test_edge_matrix 22, test_classify_contract 12, test_performance 1. Benchmark p95 ~1.37ms (N2 budget 100ms).

## AC coverage

| AC / TP | Tests | Status |
|---|---|---|
| AC2 / TP-02 (all-match → gate PASS) | test_classify.py:27, test_gate.py:24 | ✅ COVERED |
| AC3 / TP-03 (critical missing → `missing`/FAIL) | test_classify.py:45, test_classify.py:61 (TP-08/EX-A1-2), test_gate.py:35 | ✅ COVERED |
| AC4 / TP-04 / TP-10 / EX-A1-4 (format-only date → `wrong_format`, gate PASS) | test_classify.py:74, test_edge_matrix.py:90, test_gate.py:59 | ✅ COVERED |
| AC6 / TP-06 / TP-11 / TP-14 / EX-A1-5 (`new_field` informational, gate unaffected) | test_classify.py:88, test_edge_matrix.py:315, test_gate.py:71 | ✅ COVERED |
| TP-07 / EX-A1-1 (5 invoices all match → 5 gates PASS) | test_edge_matrix.py:285 | ✅ COVERED |
| TP-08 / EX-A1-2 (critical total empty → `missing`, gate FAIL) | test_classify.py:61, test_gate.py:35 | ✅ COVERED |
| BR2 (criticality governs gate) | test_gate.py:35,47,86,98 | ✅ COVERED |
| BR3 (`new_field`/`new_line` never fail gate) | test_gate.py:71, test_tables.py:94, test_edge_matrix.py:161,241 | ✅ COVERED |
| BR8 (match_key not position) | test_tables.py:62 | ✅ COVERED |
| TP-21 / NFR N22 (malformed → typed `ClassifierError`, no raw leak, no silent match) | test_validation.py:36-115 (field), :118 (golden table row), :140 (actual table cell), :195-224 (golden prompts: non-mapping/spec/missing-answer/non-bool-critical), :227-242 (actual prompts: non-mapping/non-mapping-cell), :160 (subclass `ClassifierError`), :166 (loud not silent match) | ✅ COVERED (field + table + **prompt-level gap-filled**) |
| CT-02 (Verdict/RowVerdict/TableVerdict contract) | test_classify_contract.py:27,31,37,42,47,62,69,109,115,145 | ✅ COVERED |
| NFR N2 (perf <100ms p95, ≤50 fields / ≤500 rows) | test_performance.py:67 | ✅ COVERED |
| Four field types per-type canonical (number/date/id/text) | test_edge_matrix.py:191,197,203,209 | ✅ COVERED |
| `new_line` on unmatched actual row | test_tables.py:94, test_edge_matrix.py:241 | ✅ COVERED |
| `missing` on unmatched golden row | test_tables.py:112, test_edge_matrix.py:263 | ✅ COVERED |
| Non-critical difference → PASS | test_gate.py:86,98, test_edge_matrix.py:222 | ✅ COVERED |
| Purity (no IDP/platform/I/O imports) | test_classify_contract.py:145 + static grep | ✅ COVERED |
| N28 / ADR-0005 #8 — orchestration reuses the N22 validator (same function object, no second dialect) | tests/classifier/test_validation.py:248 (identity pin; wrapper mutation killed) | ✅ COVERED |

## Scenario A bugs (quarantined repros)
(none — no `@bug-repro` tests; the one Scenario B found during /test was this story's own AC gap, fixed via /implement commit 1d50e978, not a pre-existing unrelated bug.)

## Non-blocking debt (recorded in docs/state/DEBT.md — for Soneca/Dunga/Mestre, NOT code defects)
- **DEBT-04** — table columns default to `text` (no per-column `type`); Soneca to add an optional per-column `type` map.
- **DEBT-05** — actual-only tables absent from the verdict map (no `new_table` verdict literal); needs a versioned ADR change if required.
- **DEBT-06** — pyright LSP src-path config gap (false `reportMissingImports`); mypy is gate of record.
- **DEBT-07** — ADR-0003 line 64 `date` bullet reads as `match` but AC4 requires `wrong_format`; code is correct (reviewer-confirmed), Soneca to reconcile the doc.
- **DEBT-08** — DATA-MODEL-01 §3 example uses `"field"` where the implemented + contract-pinned `RowVerdict` key is `"column"`; Soneca one-line doc fix.
- **DEBT-09** — duplicate `match_key` behavior unspecified & untested; Dunga/Soneca to decide.
- **DEBT-10 (new, from /test Atchim)** — prompts-vs-fields `answer`-presence asymmetry: actual fields/table cells require `value` present, but actual prompts require only a dict (not `answer`). This is intentional (a missing `answer` → `missing` verdict is valid), but if the team wants strict golden/actual symmetry on prompts it's a separate behavior-change story. Dunga to track.
- **DEBT-11 (new, from /test Atchim)** — `tests/classifier/test_performance.py:47` comment says "half the rows match, half are new (exercise match + new_line)" but `_build_actual` makes all 500 rows match (no new_line path under benchmark). Stale comment; the perf gate is still validly measured. Update the comment or split rows half-and-half.
- **cosmetic** — test_validation.py missing trailing newline (Atchim suggestion, ruff doesn't enforce).
- ~~**gitleaks** binary not installed locally — recorded as tool-absent~~ ⚠️ **STALE, corrected 2026-09-21.** `gitleaks 8.30.1` **is** installed (confirmed by the user's own `brew` output and by the independent gate, which flagged this note as out of date). Current state: `gitleaks git .` is **clean** as of `ee9770e`, which added a `.gitleaksignore` fingerprint baselining the one triaged historical test-fixture hit at `353549d` (**DEBT-45**, verified not a credential across four audits). The detector was proven **still armed** after that entry by planting a secret of the same shape and confirming it is caught. The remaining work is not the tool but the **gate definition** — T-01.4.10 leg 1 must move to merge-base scope with full history as a scheduled non-blocking audit, because merge-base scoping alone does **not** clear this branch.

## History
- `/test gap-fill (Atchim TDD gate)` on 2026-09-21 at `ab0734d`: ✅ **PASSED** — re-stamp after **FO-5** (`6d525ef`) closed the `overall_gate` fail-open. Gated by a **fresh instance** (DEBT-44). Previous stamp was stale from 2026-09-18: `classifier/gate.py` changed and nothing re-stamped it, so S-01.1 sat marked Done with no backing stamp until this one.
- `/implement (Atchim code review TDD gate)` on 2026-09-18 at `f90a0915a27495e10f946de5eceeb234678d2d3d`: ✅ PASSED — the original S-01.1 /implement stamp (Atchim 2-round review, 76 tests, structural sonnet≠opus, static clean). Superseded by this /test stamp after /test found the prompt-level N22 gap (Scenario B) and /implement fixed it (commit 1d50e978). The /qa rigor gate blocked on the /implement-sourced stamp (high-risk spec requires a /test-sourced stamp); this stamp resolves that.