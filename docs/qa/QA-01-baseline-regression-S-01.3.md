# QA-01 (S-01.3): audit of SPEC-01 / Story S-01.3, the Langfuse platform adapter (Epic D)

**Verdict (re-check 2026-09-19, current):** ✅ Pass. S-01.3 is **Done**. One Minor doc follow-up remains (F-7). See the re-check section at the end.
**Verdict (initial audit, superseded):** ⚠️ Pass with follow-ups. F-1 was a Done precondition.
**Rigor:** standard (CLAUDE.md ## Rigor). SPEC Risk: high.
**Author:** alex@divinocosta.com.br (solo mode). Auditor: Zangado.
**Date:** 2026-09-19. **Branch:** feat/S-01.3-langfuse-adapter @ c3ed448.
**Path note:** the canonical path `docs/qa/QA-01-baseline-regression.md` (artifact_paths.py) is S-01.1's report. It was not overwritten; this file carries the `-S-01.3` suffix.

## Entry gates (re-confirmed)
- Stamp `docs/qa/TEST-S-01.3-baseline-regression.md`: ✅ PASSED. Source `/test gap-fill (Atchim TDD gate)`. Commit ba6a905 (the parent of c3ed448).
- Rigor gate: Risk high, and the stamp is /test-sourced. PASS.
- Freshness: `git log ba6a905..HEAD -- src tests` is empty. PASS.
- Branch: feat/S-01.3-langfuse-adapter. PASS.

## Tests
- Unit/contract: `pytest -q` → **188 passed, 12 skipped**. The 12 skips are the integration tests, skipped by design without `RUN_INTEGRATION_TESTS` (tests/conftest.py:20). This is not an unlinked skip.
- Live: `RUN_INTEGRATION_TESTS=1` against local Langfuse (health reports 4.38.0), synthetic `test-s013-` data only → **200 passed, 0 failed**.
- Static: `mypy --strict src` clean. `ruff check src tests` clean.
- TDD spot-check: (a) no test files deleted since 518249e. (b) no `.skip`/xfail beyond the by-design integration gate.

## DoD walk (S-01.3)
| # | DoD line | Result | Evidence |
|---|---|---|---|
| 1 | AC 3a/3g, post-condition, BR4, BR11 | met, with a deviation | get_dataset/record_run/mark_run_status. The run metadata write for the post-condition lands with S-01.4 (INV-04 PENDING) |
| 2 | Protocol + adapter + `make_platform()` | met, with a deviation | types.py:73. `write_scores`/`flush` became private under ADR-0005 #9 (accepted design change). make_platform langfuse_adapter.py:317 |
| 3 | `get_dataset` returns the schema verbatim, absent → None | met | test_langfuse_adapter.py:73,110; live :131 |
| 4 | Golden schema + provisioning + F2 measurement | **not met: the Soneca schema review is unrecorded (F-1)** | The schema v1 is committed. DATA-MODEL-01 §1: 1,641 chars. Tables-block live write passes (test_integration_langfuse.py:234) |
| 5 | CT-05 | met | test_golden_schema_contract.py, green |
| 6 | Never drop (TP-42) | met | test_schema_provisioning.py:25,38,47,96 |
| 7 | events_only, /v3 reads, SDK pin, OTLP not best-effort (TP-38/43) | met | uv.lock langfuse 4.15.4. test_tracing.py:87,103,127. Live :551 |
| 8 | Unit-test enumeration | met | Suite green. Stamp AC table |
| 9 | Single-fetch hash, no get_golden_version | met | hashing.py. test_hashing.py |
| 10 | DEBT-13 / INV-03 / CT-03 widened | met | CONTRACTS.md:9, INVARIANTS.md:9. The DEBT-13 status text is stale (F-4) |
| 11 | CT-03 + N9 | met | test_score_contract.py. The metadata leg is owed by S-01.4 |
| 12 | INV-01 incl. spans (TP-15/45) | met | test_inv01_payload.py (subprocess span allowlist) |
| 13 | Deterministic score id | met | scoring.py:27 pinned NAMESPACE. TP-36/37 |
| 14 | flush is the bounded-retry seam | superseded | ADR-0005 #9: no flush retry. See F-2 on score retry |
| 15 | run_status marker | met | langfuse_adapter.py:270. test :245,:266 |
| 16 | N24 confinement | met | test_module_boundary.py. The only `import langfuse` is langfuse_adapter.py:334 |
| 17 | Compliance (INV-05, INV-02, N5, TP-44, N19) | met, with one minor finding (F-5) | The sanitize_for_log json.dumps tests pass. Test Basic tokens decode to fake `public:secret` |
| 18 | Observability (typed errors → abort reasons) | met | errors.py taxonomy. Tests listed under Gates |
| 19 | Containment via S-01.4 /harden | deferred, as the SPEC allows | The SPEC wording is "before the **epic** is Done" |
| 20 | N26 | met | test_score_contract.py. Live :310,:350. The experiment merge is DEBT-19 (open, S-01.4) |
| 21 | Integration vs pinned 4.38.0 | **met** (F-6 closed 2026-09-19) | All 6 compose images pinned to exact tag + manifest digest; web/worker at `4.38.0`; 6/6 pinned digests match the running container image IDs |
| 22 | Docs updated | met, with drift (F-4) | DATA-MODEL-01 §1/§4 and CT-05 show ✅ |
| 23 | Atchim review | met | Stamp. Structural independence |
| 24 | Zangado /qa | this report | |

## NFR-01 (S-01.3 scope)
- **Feature:** **N26 ✅ PASS**. Evidence: test_score_contract.py N26 contract, plus the live test_integration_langfuse.py:310 (distinct runs) and :350 (same run_name, distinct run_id, no score overwrite). N1/N3/N6/N7/N8/N10/N14/N15/N16/N28 stay ⬜ PENDING (S-01.4). N21 stays ⬜ PENDING (S-01.2). S-01.3 supplies the typed-error seam for N10/N16.
- **System (⚠️ WAIVED → /signoff), with S-01.3 evidence noted for the record:**
  - N4: grep clean; gitleaks absent (DEBT-12).
  - N5: sanitize_for_log tests pass.
  - N9: CT-03 count formula.
  - N11: S-01.4.
  - N12/N20: INV-01 tests, TP-15/45.
  - N13: RunMetadata is passed through; the zero-exit leg belongs to S-01.4.
  - N17: uv.lock pins langfuse 4.15.4, but pyproject says `>=`. Install from the lock.
  - N18: no local golden write found.
  - N19: fixtures are synthetic `test-s013-` data.
  - N23: TP-44.
  - N24: boundary test.
  - N25: open. It blocks any real-golden load.
  - N27: n/a here.

## Gates
- **Containment: REQUIRED → deferred to S-01.4 `/harden` (HARDEN-01.md).** The SPEC binds it to the epic's Done, not the story's. S-01.3 may reach Done. /harden must cover the deterministic-id retry safety (see F-2) and the aborted marker.
- **LLM-Evals:** N/A (the marker is N/A).
- **UI:** N/A.
- **Observability: REQUIRED.** S-01.3 scope: **✅ VERIFIED.** Typed errors (DatasetFetchFailed, ScoreWriteFailed, FlushFailed, ExperimentRecordFailed, RunStatusWriteFailed, TracingNotConfigured, Transport) are raised and structured-logged (`dataset_fetch_failed`/`score_write_failed`/`run_status_write_failed`/`transport_failed`, key=value with json-escaped values). Test evidence: test_langfuse_adapter.py:177-224,266,332,424,452,509; test_tracing.py:87-229; live :551. The UC-level N10 telemetry lines (the per-run and abort-reason lines) are owed by S-01.4.
- **SCA:** `pip-audit` reports no known vulnerabilities. The only skip is the local package itself.
- **Secrets:** gitleaks is absent (**DEBT-12, must close before S-01.4 /qa**). The grep fallback over src/tests/docs found no key/token hits. The only `Basic` literals are fake test tokens:
  - test_tracing.py:218,248;
  - test_transport.py:14;
  - test_log_redaction.py:20.

  The real .env credential values were checked against the last 200 commits: 0 hits. `.env` is gitignored.
- **Composition:** 35 passed across CT-03 (test_score_contract.py), CT-05 (test_golden_schema_contract.py), INV-01 (test_inv01_payload.py), INV-04 hashing and N24.
  - INV-01 ✅ (payloads + span allowlist).
  - INV-03 ✅.
  - INV-04: the adapter side is ✅ (single fetch, RunMetadata passthrough); the run-level assertion is PENDING (S-01.4).
- **Cleanliness:** `check_clean.py` reports "clean", exit 0. Stray files it does not flag:
  - `docs/qa/.TEST-S-01.3-baseline-regression.md.swp` is an editor swap file (03:03). Close the editor session or recover and discard it, then delete it. Never commit it. Add `*.swp` to .gitignore.
  - `spikes/` (langfuse-form-mode) is an untracked throwaway. Keep it out of every S-01.3 commit, then delete it or move it outside the repo. The durable spike write-up already lives in docs/spikes.
- **Compliance (DEBT-18 option B): holds in code.**
  - Score `comment` is always None (scoring.py:98,110,119).
  - Span `output` is the verdict map only (langfuse_adapter.py:229). `expected_output={}` (:242). `input={"document_id"}`.
  - `DocumentRecord` has no `actual` (types.py:48). `_item_cache` holds only dataset_id (:70).
  - run_status comment = ids/versions only (:285).
  - Logs carry names/ids only, json-escaped. Error bodies are reduced to their shape (:47).
  - No expected, actual or confidence value reaches scores, spans, run_status or logs. One adjacent leak via an exception message: F-5.

## Critical
None.

## Major
- **F-1: DoD line 4 is unmet. Soneca's review of `golden_schema_v1.json` is not recorded** in any artifact (HANDOFFS, PROGRESS, DATA-MODEL-01, commits). **Done precondition.** Owner: Soneca. Not a defect; no escaped tag.
- **F-2: No component retries a score write.**
  - ADR-0005 #9 ("Scores … retried within the transport budget because it is idempotent") and DoD line 13 allow a 5xx retry. But `_write_scores` makes exactly one attempt (langfuse_adapter.py:157-180).
  - `record_run` must not be retried (#9 step 4).
  - SPEC T-01.4.3/TP-41 still assigns the retry to the orchestrator on a public `write_scores` seam that #9 removed.
  - Result: one transient 5xx after `run_experiment` aborts a fully gated run and leaves a partial experiment behind. errors.py:21 has a stale docstring ("orchestrator decides retry").
  - Suggested fix: a bounded, deadline-limited retry inside `_write_scores` (the ids are deterministic, so it is an upsert). Re-scope TP-41 to the adapter. Soneca decides the owner; Dunga re-scopes.
  - Does not block S-01.3 ("may retry"; TP-41 is S-01.4's). It must close before S-01.4 build.
  - **escaped-atchim: yes.** record_run was reviewed against #9, and the single-attempt loop is visible in the diff.

## Minor
- **F-3: `get_dataset` trusts the pagination body shape.**
  - Reproduced: `data: null` and `meta.totalPages: "2"` escape as raw `TypeError`, not `DatasetFetchFailedError` (langfuse_adapter.py:114,133,148). That breaks DoD line 8's "typed … mappable to `dataset_fetch_failed`".
  - A bogus `totalPages` with empty pages loops with no cap. The probe stopped it at 50 requests.
  - Fix: validate `data` as a list and `totalPages` as a positive int, stop on an empty page, cap the pages, and wrap failures in `DatasetFetchFailedError`.
  - **escaped-atchim: yes** (trust-boundary validation, correctness axis).
- **F-4: Doc drift.**
  - INVARIANTS.md:7 (INV-01 status) still says span `output` = `DocumentRecord.actual`. The code and option B say the verdict map only.
  - ADR-0005:177 still lists `actual: NormalizedOutput` on `DocumentRecord`.
  - DEBT-13's status still says "T-01.3.11 docs widening still owed", but it is done.
  - Owner: Soneca/Dunga.
  - **escaped-atchim: yes** (INVARIANTS.md is in the stamp's reviewed file set).
- **F-5: scoring.py:123 `_require_verdict` puts the verbatim prompt key (Curator-authored golden content) into the `ValueError` message.** If S-01.4 logs that exception, golden content reaches the logs (INV-02). This is the same class as the T-01.4.5 risk. Fix: report the score name (`prompt:<hex>`), not the key.
  - **escaped-atchim: yes** (security axis, INV-02).
- **F-6: Carry-over F-1 image pin is incomplete.** `../langfuse/docker-compose.yml:109` is `langfuse:4` (floating) and the worker is `:4.38`. The live instance is 4.38.0 today. Owner: user/Mestre. Not an S-01.3 code defect; no tag.
  - **CLOSED 2026-09-19.** Pinned all 6 images (web, worker, clickhouse, minio, redis, postgres) to exact tag + manifest-list digest; web/worker resolve to `4.38.0` (revision `4ecaabed`). `docker compose config` parses and every pin matches the currently-running container image ID. Compose path + pin recorded in CLAUDE.md. See QA-S-01.5 F-1 for the digest list and the two carried-forward caveats (postgres `${POSTGRES_VERSION}` override dropped; Chainguard minio digest may be GC'd).

## Hand-off to Dunga
- TASK (Soneca): record the golden_schema_v1 review (F-1). S-01.3 → Done once recorded.
- DESIGN (Soneca → Dunga): pick the score-retry owner and re-scope T-01.4.3/TP-41, and fix the errors.py:21 docstring (F-2). Must close before S-01.4 build.
- BUG (Minor): harden `get_dataset` pagination parsing (F-3). DoD: typed error on malformed data/meta, page cap, unit tests for all three shapes.
- BUG (Minor): value-free `_require_verdict` message (F-5). Fold into T-01.4.5 or a small S-01.3 follow-up.
- DOCS (Soneca): INV-01 status, ADR-0005 #9 `DocumentRecord`, DEBT-13 status (F-4).
- OPS (user/Mestre): ~~pin the web and worker images to 4.38.0 (F-6)~~ **done 2026-09-19** — all 6 images pinned to tag+digest. Install gitleaks (DEBT-12) before S-01.4 /qa.
- Hygiene (user): delete the .swp (and gitignore `*.swp`); keep `spikes/` out of the commits.
- Debt to record via `/debt add`: F-3, and F-2 if it is deferred rather than fixed. DEBT-17/DEBT-19 remain open.

## Re-check after the fix round (2026-09-19, Zangado)

**Entry gates:**
- Stamp `TEST-S-01.3`: ✅ PASSED. Source `/test gap-fill (Atchim TDD gate)`. Commit 71e5e6a (stamp commit a7d9081).
- Rigor gate: Risk high, /test-sourced. PASS.
- Freshness: `git log 71e5e6a..HEAD -- src tests` is empty. PASS.
- Branch: feat/S-01.3-langfuse-adapter.

**Re-run (independent):**
- Unit: 220 passed, 12 skipped (the by-design integration gate).
- Live, `RUN_INTEGRATION_TESTS=1` against Langfuse 4.38.0 with synthetic data: **232 passed**. This includes the provisioning round-trip of the tightened schema under Ajv strict.
- mypy strict and ruff: clean.
- No test files deleted. No unlinked skip or xfail.

**Gates:**
- **SCA:** pip-audit reports no known vulnerabilities.
- **Secrets:** gitleaks is still absent (DEBT-12). The grep fallback over src/tests/docs is clean. The real `.env` values appear in 0 of the commits c3ed448..HEAD.
- **Composition:** CT-03, CT-05, INV-01, INV-04 hashing and N24: 53 passed. INV-01 and INV-03 still hold.
- **DEBT-18 option B holds:**
  - score `comment` is still `None` (scoring.py:97,109,118);
  - the span allowlist is unchanged;
  - the new retry logs carry the document_id, the score name, the attempt count, and either a redacted transport message or the body shape. No values.
- **Cleanliness:** `check_clean.py` reports clean, exit 0. The `.swp` file and `spikes/` are still untracked and user-owned. The handling from the initial audit stands: delete the `.swp` and gitignore `*.swp`; keep `spikes/` out of commits.

| Finding | Status | Evidence |
|---|---|---|
| F-1 Soneca schema review | **CLOSED** | DATA-MODEL-01.md "Schema review (Soneca, 2026-09-19)": CHANGES REQUIRED, and "**APPROVED** once exactly C1–C3 land with the CT-05 assertions … needs no further review". I verified the 17a4f64 schema diff is exactly C1 (`propertyNames` `^[A-Za-z0-9_-]{1,128}$` on fields and tables), C2 (`additionalProperties:false` on the field, table and prompt entries, with `rows` items left open) and C3 (`fields.minProperties:1`, `match_key.minLength:1`). Nothing else changed. CT-05 pins all three (test_golden_schema_contract.py:209-321). The approval condition is met, so DoD line 4's review clause is satisfied. |
| F-2 score-write retry (REG-03) | **CLOSED** | c779a51 `_write_score_with_retry` (langfuse_adapter.py). It retries 5xx and `TransportError` only, with 3 attempts, full-jitter backoff (base 1 s, cap 8 s), the same deterministic payload (an upsert), and no retry on 4xx. The errors.py docstring is corrected. Tests: test_langfuse_adapter.py:613,634 and the edce8eb/5795993 set. The missing whole-record-phase deadline is tracked as DEBT-20 (→ S-01.4). |
| F-3 pagination hardening (REG-04) | **CLOSED** | 1340a13. `data` that is not a list, a missing `meta`, and a non-int, bool or negative `totalPages` now raise `DatasetFetchFailedError`. The loop is capped at 500 pages. `totalPages: 0` (an empty dataset) is accepted. Tests: test_langfuse_adapter.py:762,801,816. |
| F-4 doc drift | **CLOSED** | fc0c02e/d64aeed. The INV-01 status is amended to the verdict map only, with no `actual`. ADR-0005 #9 has an amendment noting `DocumentRecord` has no `actual`; the original line 177 is kept for history, which is acceptable. DEBT-13's status is corrected. |
| F-5 prompt key in exception (REG-05) | **CLOSED** | 747cf80. `_require_verdict(report_key=prompt_score_name(key))`. Test: test_score_contract.py:164 (a sentinel prompt key is absent from `str(exc)`). |
| F-6 compose web image `:4` | **OPEN**, carry-over | User/Mestre-owned. Not an S-01.3 code item. |
| **F-7 (new, Minor)** stale F2 measurement | **OPEN** | DATA-MODEL-01.md:46 still records the minified length as **1,641** chars. After C1–C3 the committed schema minifies to **1,898** chars (re-measured by this audit). Soneca's review explicitly asked to "re-measure the F2 length". The bound itself (< 10k) is enforced by CT-05, so there is no functional risk. It is a one-line doc correction. Owner: Soneca. This is a doc omission from the fix round, not a code defect; **escaped-atchim: no** (the number is outside a code diff and nothing mechanical checks it). |

**NFR-01:** unchanged. N26 ✅ PASS. The rest are as in the initial audit.
**Observability:** ✅ VERIFIED at S-01.3 scope. The new `score_write_failed … attempts=` lines are structured and json-escaped.
**Containment:** still deferred to S-01.4's `/harden`. The retry is now in the adapter, so /harden should red-team it (upsert safety, 4xx not retried).

**Re-check verdict: ✅ Pass. S-01.3 is Done.** F-7 is a one-line doc fix for Soneca, and F-6 stays user-owned. Neither gates the story.
