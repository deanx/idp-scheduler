# QA-01 (S-01.3): audit of SPEC-01 / Story S-01.3, the Langfuse platform adapter (Epic D)

**Verdict (re-audit 2026-09-20 after FU-01.3-A, CURRENT):** ⚠️ Pass with follow-ups. **S-01.3 STAYS DONE.** Auditor: Zangado (Fable 5.1). Two Minor findings (F-1, F-2), neither gating. Two rulings recorded (containment deferral upheld; gitleaks gate semantics decided). See the 2026-09-20 section at the end.
**Verdict (re-check 2026-09-19, superseded):** ✅ Pass. S-01.3 is **Done**. One Minor doc follow-up remains (F-7). See the re-check section at the end.
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


---

# Re-audit 2026-09-20 — after FU-01.3-A (Zangado, Fable 5.1)

**Verdict: ⚠️ Pass with follow-ups. S-01.3 stays Done.** FU-01.3-A regressed nothing; every gate re-run is green.
**Independence:** Dengoso (Opus 5) → Atchim (Fable 5.1) → Zangado (Fable 5.1). Structural — reviewer and auditor both differ from the implementer's model, as SPEC-01's `Risk level: high` requires.
**Branch:** feat/S-01.2-idp-adapter @ cde7b2a. **Rigor:** standard. **Gates skipped by profile:** none — every gate was run and recorded.

## Subject of the re-audit
- `1187944` — FU-01.3-A, the A3 `run_id` precondition control in `record_run` (+ docstring-only Protocol obligation in `types.py`).
- `bb58be0` — test-only; positive path parametrized over two `run_id`s, closing a surviving mutant.

## Entry gates (verified, not trusted)
| Gate | Result |
|---|---|
| TDD stamp | ✅ PASSED, `Source: /test gap-fill (Atchim TDD gate)`, `Commit: bb58be0`. SPEC-01 is Risk: high, which requires exactly that Source — rigor gate PASS |
| Freshness | `bb58be0..HEAD` touches only `docs/` — stamp fresh |
| Cleanliness (8i) | ✅ `check_clean.py` exit 0, "clean — no stale artifacts" |
| Containment (8c) | `HARDEN-01.md` still absent, but the deferral is **upheld** — see Rulings |

## Tests (Zangado's own run, matching mine exactly)
| Scope | Passed | Failed |
|---|---|---|
| Unit + contract (default) | 464 | 0 |
| `tests/platform` | 143 | 0 |
| Full suite incl. live integration (Langfuse 4.38.0) | 477 | 0 |

`mypy --strict src` clean (23 files); `ruff check src tests` clean. The one live skip is `tests/adapter/test_integration_idp.py:63` (no published IDP action id — S-01.6), by design. No Critical regression.

**TDD spot-check (step 7):** no test files deleted since `71e5e6a`; the only skips are the by-design integration gates; no `.skip`/`xfail`/`.todo` elsewhere; no TODO/FIXME in `src` or `tests`.

## Gate results
- `Observability: ✅ VERIFIED` (S-01.3 scope). Verified by running the suites with `log_cli` and **reading the lines that actually fired**, not the code: `dataset_fetch_failed`, `score_write_failed` (with `attempts=3`), `run_status_write_failed` — each with a redacted `detail`. Log-injection probes (a newline inside `document_id`) stay one escaped line, so N5 holds. The A3 raise emits no log line by design; it is a precondition abort S-01.4 maps to `hard_failure`.
- `Containment: deferred → S-01.4 /harden (HARDEN-01.md), upheld` — with a condition, see Rulings.
- `LLM-Evals: N/A` (NFR-01:12 — deterministic classifier, no LLM call).
- `UI conformance: N/A` (no UI surface).
- `SCA: PASS` — `pip-audit`, no known vulnerabilities.
- `Secrets: PASS` — `gitleaks git .` clean over full history. The two `dir`-scan hits are `.env:2` and `.env:10`, gitignored and not committed (`git check-ignore` confirms).
- `Composition: PASS` — 80 contract/invariant tests + 2 live N26. INV-01 ✅, INV-02 (adapter leg) ✅, INV-03 ✅, INV-04 (adapter leg) ✅. INV-08-adjacent: the A3 check sits before `records_by_item_id`/`record_experiment`, so a refused run writes nothing — pinned by `run_experiment_calls == 0`.

## NFR-01 walk (8b)
No feature row **owned by S-01.3** is ❌ or ⬜.
- **N26 ✅ PASS — strengthened by FU-01.3-A.** Contract: `test_score_contract.py:104`, `test_scoring.py:23`. Boundary: `test_record_run_preconditions.py:280` (foreign `run_id` refused, `run_experiment_calls == 0`, `http_client.calls == []`), `:306`, `:337`, `:378`. DEBT-19's experiment-merge half closed in `293d9fc`.
- **N10 and N16:** S-01.3 legs ✅ PASS; UC legs ⚠️ WAIVED → S-01.4.
- **N2, N21, N22:** ✅ PASS, unchanged (S-01.1/S-01.2).
- **N1, N3, N6, N7, N8, N14, N15, N28:** ⚠️ WAIVED → S-01.4 — these are the rows NFR-01:74 already assigns to S-01.4; none is owned by S-01.3.
- **System rows** (N4, N5, N9, N11-N13, N17-N20, N23-N25, N27): ⚠️ WAIVED → `/signoff`, with S-01.3 evidence recorded for that gate.

## Findings

### Critical / Major
None.

### Minor
- **F-1: the A3 precondition raises raw `KeyError`/`TypeError` on a malformed score dict, escaping `record_run`'s typed-error contract.** `langfuse_adapter.py:341-344` subscripts `score["name"]` and `score["id"]` immediately after the defensive `record.get("scores", [])`. **Independently reproduced** (probe against the adapter with the test fakes): score missing `"id"` → `KeyError: 'id'`; missing `"name"` → `KeyError: 'name'`; `"scores": None` → `TypeError: 'NoneType' object is not iterable`. The Protocol docstring (`types.py:99-101`) promises `ExperimentRecordFailedError | ScoreWriteFailedError | FlushFailedError`.
  - *Why Minor:* all three escape **before any SDK call**, so the run is fail-closed and nothing reaches the platform; and the input is unreachable from `build_score_inputs` under mypy --strict (`DocumentRecord.scores` is a required typed key).
  - *The honest trade:* pre-A3 the same malformed input surfaced as a **typed** error, but only *after* `record_experiment` had created the experiment. A3 traded "typed but late" for "untyped but early".
  - **escaped-atchim: yes** — flagged the `.get` tolerance, not the untyped subscripts beside it.
- **F-2: three pieces of S-01.3 debt exist only in `HANDOFFS.md` and the TEST stamp — not on any card, not in `DEBT.md`.** (a) the weak `test_make_platform_dispatches_on_platform_env` "routed to FU-01.3-B" — but SPEC-01's FU-01.3-B card does not mention it; (b) the `.get("scores")` tolerance; (c) hoisting the two local `scoring` imports (`langfuse_adapter.py:339, :418`). A baton sentence is not a backlog. Not a code defect; no escape tag. Owner: Dunga.

## Rulings (decisions, not findings)
- **Containment deferral: UPHELD.** Zangado re-examined it rather than inheriting it. NFR-01:11 binds `Containment: REQUIRED` to UC-01; SPEC L136 binds it to "before the **epic** is Done" via S-01.4 `/harden`; NFR-01:80 scopes it onto S-01.4. S-01.3 has no run loop for a failure to cascade through. **Condition attached:** HARDEN-01 must now also red-team the A3 precondition (a record set carrying foreign-`run_id` ids → typed abort, zero platform writes), alongside retry upsert safety and the aborted marker. S-01.4 cannot reach Done without it.
- **gitleaks gate semantics (T-01.4.10 / DEBT-12): DECIDED.** Neither option originally offered was right alone. The gate protects against a *committed* secret, so a working-tree `dir` scan that flags a gitignored `.env` measures the wrong thing. Three legs: (1) **blocking** — `gitleaks git .` over full history, on every PR; this is what "gitleaks clean" means; (2) **blocking** — `gitleaks protect --staged` as a pre-commit hook, which catches the exact failure mode an allowlist would mask (someone force-adds or un-ignores `.env`) *before* it reaches history; (3) **assertion, not a scan** — CI asserts `git check-ignore -q .env` exits 0 and `git ls-files --error-unmatch .env` exits non-zero, so if `.env` ever becomes tracked the build fails independently of gitleaks' rules. **Do not add a `.gitleaks.toml` allowlist for `.env`** — it would silence leg 2 on that exact file for zero benefit. A local `dir` scan may exist as advisory only. **DEBT-12 → close** (tool installed, gate defined).

## Known-open confirmations (not rediscovered as findings)
- **INV-05** (`load_dotenv()` ordering): not S-01.3's — `make_platform`'s docstring assigns it to the caller and `orchestration/` is empty. S-01.4 duty. Agreed.
- **F-7** (stale 1,641-char F2 measurement): **CLOSED** — `DATA-MODEL-01.md:46` now reads 1,898 with the correction note.
- **F-6** (compose image pin): closed 2026-09-19; ops, outside this story's diff.
- **Weak `test_make_platform_dispatches_on_platform_env`:** confirmed `isinstance`-only; a factory ignoring `LANGFUSE_HOST` would pass. Routing to FU-01.3-B is adequate in substance — but see F-2, the routing is recorded nowhere a card owner reads.

## Debt surfaced (→ Dunga)
- F-1 (precondition should own malformed records) — Low, fail-closed today.
- F-2 (a)-(c) — record on FU-01.3-B as explicit DoD bullets so the re-stamp covers them.
- **N6 note:** `make_platform` fails on a missing env var with a bare `KeyError` (`langfuse_adapter.py:468-470`). S-01.4's fail-closed-with-clear-message check must wrap or pre-validate. Not S-01.3's row — recorded so it is not lost.

---

# Re-audit 2026-09-21 (after FU-01.3-B)

**Verdict: ⚠️ Pass with follow-ups. S-01.3 STAYS DONE.** FU-01.3-B regressed nothing; every gate re-run green. 0 Critical, 0 Major, 2 Minor. One prior ruling overturned in part (M9).
**Auditor:** Zangado (Fable 5.1). **Branch:** `feat/S-01.2-idp-adapter` @ `2cc4dde`. **Rigor:** standard — **no gate skipped by profile**.
**Independence chain:** Dengoso (**sonnet**, implementer) → Atchim (**opus**, TDD gate) → Zangado (**Fable 5.1**, QA). Structural at every hop, as `Risk level: high` requires.

## Subject of the re-audit
The FU-01.3-B delta `bb58be0..7a45037` — `353549d` (typed malformed-record guard + record-phase deadline + no-redirect transport), `3ad1388` (split-brain fail-closed guard), `7a45037` (two mutant-closing test rounds) — plus today's re-stamp commits `0a534ec` / `2cc4dde`.

## Entry gates (verified, not trusted)
- Stamp ✅ PASSED, `Source: /test gap-fill (Atchim TDD gate)` — the rigor gate SPEC-01's `Risk level: high` demands. PASS.
- **Freshness: PASS, and the self-reference checked rather than accepted.** The stamp records the *reviewed* SHA `7a45037`; its `Commit:` field warns that `git log {stamp_commit}..HEAD -- {Files}` returns the two stamp commits because `test_transport.py` and `docs/` paths sit in the `Files:` set. Zangado verified the falsifiable form himself: **`git diff --stat 7a45037 HEAD -- src/` is empty** — zero source drift. Self-reference, not staleness.
- No test files deleted since `bb58be0`. Only `pytest.skip`s are the two documented live gates. No TODO/FIXME in `src/` or `tests/`. No merge/WIP commits in range.

## Tests (Zangado's own runs)
| Scope | Result |
|---|---|
| Default (`uv run pytest`) | **485 passed, 14 skipped** |
| Live (`.env` sourced, `RUN_INTEGRATION_TESTS=1`, Langfuse 4.38.0 health 200) | **498 passed, 1 skipped** (S-01.6 IDP action gate) |
| Composition subset (CT-03, CT-05, INV-01, hashing/INV-04, N24, log-redaction, preconditions) | **77 passed** |
| `uv run mypy` / `uv run ruff check .` | clean / clean |

## Gate results
| Gate | Outcome |
|---|---|
| `Observability:` | **✅ VERIFIED** (S-01.3 scope) — observed firing, not read from code |
| `Containment:` | **REQUIRED → deferred to S-01.4 `/harden`, UPHELD — condition WIDENED from one item to five** |
| `LLM-Evals:` | **N/A** — NFR-01:12 justification confirmed (deterministic classifier, no LLM call) |
| `UI conformance:` | **N/A** — no UI surface |
| `SCA:` | **PASS** — `pip-audit`, no known vulnerabilities |
| `Secrets:` | **⚠️ see F-2** — no committed credential, but `gitleaks git .` now returns 1 hit on a fixture literal |
| `Composition:` | **PASS** — 77 contract/invariant + 2 live N26; INV-07 adapter leg new and passing |
| `Cleanliness:` | **PASS** — `check_clean.py` exit 0 |

**Observability detail.** Observed, not inferred: `transport_failed method="POST" path="/api/public/scores" detail="... unexpected redirect response"` on a real 3xx probe, redacted. INV-02 held on every path — 0 sentinel hits (expected value, score id, score name, secret, embedded URL credential) across all captured output and exception text. ⚠️ **Recorded, not a gap here:** the three *new* abort paths (malformed record, split-brain config, record deadline) emit **no log line by design** — typed raises only, the same convention as the A3 precondition. **S-01.4's N10 walk must log the mapped abort reason for them.**

## NFR-01 walk (8b)
**FU-01.3-B changed no row's ✅/❌ standing.** Owned by S-01.3: **N26 ✅ PASS** (unchanged standing, defence deepened — the shape guard makes A3's *precondition* typed, it does not alter the guarantee); **N10 S-01.3 leg ✅ PASS** (unchanged — new paths add no new abort *reason*); **N16 S-01.3 leg ✅ PASS — strengthened** (record-phase deadline is a new typed abort on the same row). N2 ✅ (p95 ~1.35 ms re-measured), N21 ✅, N22 ✅. N1/N3/N6/N7/N8/N14/N15/N28 ⚠️ WAIVED → S-01.4; **N6 caveat stands** (DEBT-30 — `make_platform` still bare-`KeyError`s on a *missing* env var; `PlatformConfigurationError` covers disagreement, not absence). All `system` rows ⚠️ WAIVED → `/signoff`. Full row-by-row markup in `docs/qa/NFR-01.md`.

## Findings

### Critical / Major
None.

### Minor

**F-1 — three malformed-record shapes still escape `record_run` untyped, on the exact seam FU-01.3-B was closing.**
`langfuse_adapter.py:409` subscripts `record["item_id"]` in a comprehension **seventeen lines before** the `_require_record_shape(record)` loop at `:426`, and the guard (`:66-99`) checks neither `item_id` nor that the record is a dict at all. Reproduced through the real `record_run` path with the dataset cache primed: record missing `item_id` → `KeyError: 'item_id'`; `item_id` a list → `TypeError: unhashable type`; record not a dict → `TypeError: string indices must be integers`. All three are **fail-closed** (0 SDK calls, 0 HTTP calls) — which is why this is Minor, and *not* why it should wait: an `except PlatformError:` caller in Epic D turns any of them into a raw traceback in a CI log instead of a gate message, on another team's prompt-change PR. The Protocol contract at `types.py:99-101` remains broken for these inputs; REG-09 `covered` is true only for its seven enumerated cases.
- **Severity:** Minor · **`escaped-atchim: yes`** — same function, same seam, keys enumerated three lines apart.
- **Second escape on this family in two consecutive audits**, and the third occurrence overall (REG-01, REG-04, REG-09).
- **Ruling: PIN → widen REG-09, no new row** (Atchim). A REG-11 would fragment one class across two rows and let each row's case list look complete on its own — precisely the failure mode in play.

**F-2 — `gitleaks git .` now fails on a fixture literal.**
`tests/platform/test_langfuse_adapter.py:305`, value `distinctive-secret-2c71`, introduced in `353549d`, rule `generic-api-key`. Verified: matches **no** live `.env` value; `.env` remains gitignored and untracked (`check-ignore` 0 / `ls-files` non-zero). **Not a credential — no rotation.** But the gate as T-01.4.10 leg 1 defines it (blocking `gitleaks git .` over full history on every PR) now goes red, and the previous audit recorded this scan clean — the delta broke it.
- **Severity:** Minor · **`escaped-atchim: no`** — the secret scan is the `/qa` 8g gate, outside the TDD gate's five axes.
- **Ruling: no PIN** (Atchim). A regression test here would pin *gitleaks' entropy heuristic*, not our behaviour; the permanent control already exists — gitleaks runs in the gate.

## Rulings (decisions, not findings)

**1. The permanent fix for F-1 is structural, not a fourth case list.** Atchim's ruling, and the load-bearing part of this audit: **enumerating keys by hand is not the fix — it is the defect.** `DocumentRecord` is a `TypedDict` at `types.py:49-66`; the orchestrator confirmed `DocumentRecord.__required_keys__ == ['document_id', 'item_id', 'scores']`. Three consecutive attempts have enumerated a subset of a field list that was **importable all along**, and the base rate on that experiment is now 0 for 3. FU-01.3-D must land three structural parts:
   1. **Ordering invariant, not a longer list** — validate *every* record at the very top of `record_run`, before any subscript of any record; derive `record_item_ids` from already-validated records. The `:409`-before-`:426` seam must become *physically impossible*, not merely patched at `:409`.
   2. **Guard opens with a type check, not a key check** — `isinstance(record, dict)` as the first statement. `"document_id" not in record` on a `str` silently does substring semantics: the same trap FU-01.3-B already patched one level down for non-dict *scores* (REG-09's seventh case) and not one level up.
   3. **Required keys come from the type, not from a human** — parametrize the missing-key cases over `DocumentRecord.__required_keys__`, so adding a field auto-generates its pin. This is the change that makes a fourth escape structurally unavailable.

**2. ⚠️ Correction to the ruling — the property test carries a cost Atchim did not price.** He proposed a `hypothesis` property test over generated record shapes and stated *"`hypothesis 6.161.2` is already installed."* **It is not** — the orchestrator checked: absent from `pyproject.toml`, absent from `uv.lock`, absent from the venv (`ModuleNotFoundError`). So it is a **new dependency**, which under `CLAUDE.md ## Tooling` drags in a lock-file update and a `pip-audit` pass, and under N24/DEBT-13 discipline deserves a deliberate decision rather than riding in on a bug fix. **Parts 1–3 above need no new dependency and deliver most of the anti-enumeration value.** The property test is recorded as a *separate, optional* decision for Dunga to size — not a silent prerequisite of FU-01.3-D.

**3. M9 — partly overturned, and Atchim accepted the correction.** He had ruled "drop `base_url=host`" an **equivalent** mutant ("do not test, do not delete"). Zangado monkeypatched `langfuse.Langfuse` with a kwargs spy and showed `make_platform()` passes `base_url == host` — directly assertable, at zero marginal cost since DEBT-32's fix installs that same spy. Atchim's restatement of his own error: it was an **unobserved** mutant, not an equivalent one — he scoped observability to *runtime behaviour* (where the guard does make the two indistinguishable) when the right scope is the **call contract**. The substantive argument decides it: `Langfuse(host=...)` alone *loses* to `LANGFUSE_BASE_URL` while `base_url=...` *wins*, so the day someone relaxes the guard, that kwarg is the only thing keeping credentials and OTLP traffic on the named host — and today nothing pins it. **"Do not delete" stands; "do not test" is struck.** REG-10's wording is amended accordingly.

**4. Three-card split D/E/F — SOUND**, with F-1 landing on D (same file, same re-stamp). Estimate **3 → 5** (Atchim; Zangado said 4, Atchim raised it because the reordering touches `record_run`'s control flow).

**5. Review-method debt — the root cause, recorded as such.** Atchim accepted `escaped-atchim: yes` without qualification and named the method error: all 12 of his mutants were drawn from the **changed lines**, so `:409` — unchanged — was never in the mutant population. *"Reviewing a guard by mutating the guard is circular."* The correct method, not applied: for each key in the declared type's `__required_keys__`, ask whether any path reaches a subscript of it **before** validation. One lookup would have surfaced `item_id` in both prior audits. This belongs in the review protocol, not just in one file's fix.

## Known-open confirmations (not rediscovered as findings)
- **HARDEN-01 still does not exist** — containment deferral to S-01.4 upheld, condition widened to five items (see NFR-01 markup). S-01.4 cannot reach Done without all five.
- **DEBT-30** (`make_platform` bare `KeyError` on a *missing* env var) — S-01.4 T-01.4.1's duty, unchanged.
- **DEBT-20's deadline value stays `None` = disabled and PROVISIONAL** pending S-01.6 timings.
- **REG-08** remains `pending-test` on its false-positive residual (FU-01.3-C); its false-negative leg was confirmed covered today.
- Zangado could **not** independently reproduce the SDK `base_url`-beats-`host=` precedence claim (a subprocess constructor probe emitted nothing). Two prior reviewers did reproduce it, and **the verdict does not depend on it** — the guard fails closed either way. Recorded so the claim is not treated as thrice-confirmed.

## Debt surfaced (→ Dunga)
- **F-1 → FU-01.3-D DoD bullet, est. 3 → 5**, with the three structural parts above. Widen REG-09's case list; no new row.
- **F-2 → rename the fixture value** (e.g. `distinctive-secret-NOT-A-REAL-KEY-2c71`) — preserves the distinctiveness the assertion needs while killing the entropy signature, and leaves the detector armed on that line. `# gitleaks:allow` is the **fallback only**, and only with a same-line reason comment; **re-run `gitleaks git .` to verify, do not assume**. **Never** add a `.gitleaks.toml` path allowlist for `tests/` — both reviewers were emphatic: it blinds the gate on the highest-risk file class in the repo, since fixtures are exactly how a real captured value gets committed by accident.
- **M9 pin → FU-01.3-D DoD (a)**: the DEBT-32 constructor spy must also assert `Langfuse` is called with `base_url == host == LANGFUSE_HOST`.
- **Review-method debt**: guard-function reviews must enumerate obligations from the **declared type** (`TypedDict.__required_keys__` / dataclass fields), not from the diff's changed lines; a mutant population drawn only from changed lines cannot detect an unguarded caller *above* the guard.
- **`hypothesis` property test** — optional, needs a dependency decision (see Ruling 2).
- **ruff `select` has no `S` (bandit) family at all** (`pyproject.toml:57`), so the `noqa: S310` at `transport.py:117` never suppressed anything and the transport files have never had the URL-scheme audit run against them. Low; decide deliberately whether to enable `S3xx` or record it as out of scope. Not a finding.

---

# Re-audit #3 — 2026-09-21 (after FU-01.3-D)

**Verdict: ⚠️ Pass with follow-ups. S-01.3 STAYS DONE.** 0 Critical, **1 Major**, 4 Minor.
**Auditor:** Zangado (Fable 5.1) — **the first genuinely independent look at this delta.** **Branch:** `feat/S-01.2-idp-adapter` @ `4d6d837`. **Rigor:** standard, no gate skipped.
**Independence chain:** Dengoso **sonnet** → Atchim **opus** (*same instance for both the code-review APPROVE and the `/test` gate — NOT reviewer-independent, self-declared in the stamp*) → Zangado **Fable 5.1**. Nothing in this audit rests on Atchim's APPROVE or PASS; every claim used was re-derived.

## Entry gates (verified, not trusted)
`git diff --stat 3708b3d HEAD -- src/` **empty** → the stamp's self-reference warning checks out, freshness PASS. No test files deleted since `8f55017`. No merge/WIP commits in range, no TODO/FIXME in `src/`/`tests/`, only the documented live-gate skips.

## Own runs
| Scope | Result |
|---|---|
| Default | **493 passed, 14 skipped** |
| Live (Langfuse 4.38.0, health 200) | **506 passed, 1 skipped** |
| Composition subset | **83 passed** |
| mypy / ruff / pip-audit / `check_clean.py` | clean / clean / no known vulns / exit 0 |

**Independent mutation re-execution** (`PYTHONHASHSEED=2`, source restored byte-identical each time): MA, MB, MC, MD, ME **all re-killed in his hands**. Two of his own: **MG** (`['document_id','scores']`) KILLED; **MF** (`['document_id','item_id']`, dropping `scores`) **SURVIVES all 172**.

## Gate lines
| Gate | Outcome |
|---|---|
| `Observability:` | **✅ VERIFIED** — observed, not read (see below) |
| `Containment:` | **REQUIRED → deferred to S-01.4, UPHELD. No sixth item — leg (2) AMENDED instead** |
| `SCA:` | **✅ PASS** |
| `Secrets:` | **⚠️ — see F-2** (no credential; gate definition is the defect) |
| `Composition:` | **✅ PASS** (83 + live N26/INV-01) |
| `Cleanliness:` | **✅ PASS** |
| `LLM-Evals:` / `UI:` | **N/A** / **N/A** |

**Observability detail.** Drove **six** malformed shapes through the real `record_run` with a DEBUG handler on the root logger: missing `item_id`, missing `scores`, missing `document_id`, `item_id` a list, record a `str`, malformed record at index 1. All six produced a typed `ExperimentRecordFailedError` with **0 log lines, 0 HTTP calls, 0 SDK calls** and **0 INV-02 sentinel hits** (score id/name/value absent from both exception text and captured logs; messages name `document_id` only). "No log line by design" holds for all three FU-01.3-D paths; **S-01.4's N10 walk must log the mapped abort reason.**

## Findings

### Major

**F-1 — a non-string `document_id` passes the guard and `record_run` WRITES it and returns success. Fourth REG-09 family member, and the first that is FAIL-OPEN.**
`langfuse_adapter.py:101-114` checks `document_id` for **presence** (via `__required_keys__`) and checks `item_id`'s **value** is a `str` at `:112` — but never checks `document_id`'s value, though `types.py:65` declares `document_id: str`. **Verified independently by the orchestrator by reading the source: there is no `isinstance` check for `document_id`.** Reproduced through the real `record_run` with the cache primed, for `None`, `7`, `["doc"]`, `{"k":"v"}`: **no raise, `http=1`, `sdk=1`** on all four. `score_id()` (`scoring.py:32`) is an f-string, so it stringifies anything and the A3 derivation check passes whenever the caller derived the id the same way; the score lands and the value reaches the experiment span `input` at `:506`. Upstream, `get_dataset:278` also takes `raw_item["input"]["document_id"]` untyped — the golden schema guards `expectedOutput`, not `input` — so a malformed platform item flows in and back out (REG-04 family).
- **Why Major, not Minor:** every prior REG-09 shape was Minor *because* it was fail-closed (0 SDK, 0 HTTP). This one performs writes and reports success on malformed input. The `types.py:99-101` Protocol contract is broken in the **fail-open** direction.
- **It falsifies the stamp's central sentence.** "A fourth escape structurally unavailable" is untrue: part (3) made key **presence** type-driven; **value types are still hand-enumerated** — `item_id` yes, `document_id` no, 1 of 2 `str` fields.
- **Why S-01.3 still stays Done:** no *new* sensitive data leaves the app (whatever sat in `input.document_id` was already on the platform as item input; `expected_output={}` still holds); realistic reachability is low (S-01.4 resolves the document file by `document_id` first, and N28 validates the golden set); the fix is two one-liners at the two trust boundaries.
- **`escaped-atchim: yes`** — the guard's own declared type says `str`; DEBT-40's one-lookup method finds it.
- **Fix:** (i) `isinstance(document_id, str)` right after the key loop; (ii) the same at `get_dataset:278`, raising `DatasetFetchFailedError`; (iii) **make value pins type-driven too** — parametrize a wrong-type-value test over the `str`-annotated fields of `typing.get_type_hints(DocumentRecord)`, so a future scalar field auto-generates its **value** pin exactly as part (3) does for presence. Widen REG-09 (no new row). **Re-stamp required, and it must be run by an Atchim instance that did NOT review the diff.**

### Minor

**F-2 — the recommended merge-base scoping does NOT clear this branch; the fingerprint baseline is the load-bearing remedy, not an optional extra.**
`gitleaks protect --staged` = 0. `gitleaks git .` = 1 (`353549d:…:generic-api-key:305`, 136 commits). Zangado also ran the shape Atchim recommended — `--log-opts="$(git merge-base main HEAD)..HEAD"` — and it **still reports 1 leak**, because merge-base is `d3d1444`, 129 commits back, putting `353549d` *inside this PR's own range*. **A diff-scoped blocking gate would go red on this branch exactly like the full-history one.** `.env` gitignored + untracked; value verified not a credential; no rotation.
- **Ruling:** working-tree-clean / history-dirty **is** acceptable for S-01.3 to stay Done, *conditionally* — (a) the fingerprint entry goes into `.gitleaks.toml` `[allowlist] fingerprints` with a same-line reason (permitted: it pins one commit × file × line × rule and cannot blind a future commit); (b) T-01.4.10 leg 1 redefined **with a named owner and a real card** — he could not find the "already DEBT-1" card referenced in the handoff; (c) no history rewrite, no `paths` allowlist. Agrees the blocking-full-history definition is the defect. `Secrets: ⚠️` stands until (a) lands — not a downgrade.
- `escaped-atchim: no` — gate design, outside the five-axis lens.

**F-3 — DEBT-42's third instance was mis-described *by the orchestrator*, and the correction is recorded.** `assert out["status"]` at `tests/adapter/test_integration_idp.py:72` **cannot** "pass for a FAILED status": `extract()` returns `body` only when `raw_status in self._success_statuses` (`idp_client.py:314-317`), and `normalize()` raises again outside the success set (`normalize.py:53`). **Verified independently by the orchestrator, who wrote the erroneous prose and has corrected it in `DEBT.md`.** The assertion is **redundant, not dangerous** — a weaker finding than the `:88` twin, not a stronger one. Debt remains right; the implied urgency did not.
- `escaped-atchim: n/a` — register prose authored after the gate.

**F-4 — the stamp overclaims what MB proves.** Mutant **MF** (a literal hand-list omitting `scores`) survives all 172 tests. MB proves only that the loop is **non-vacuous**. Accurate claim: type-driven for **source generation of the key list**, mutation-pinned for **2 of 3 keys**, and (per F-1) **no coverage of value types at all**. Corrected in the stamp and in REG-09's MB note. **N-2 ruling: agrees it is not a gate** (MF is behaviourally equivalent — the downstream guard raises typed), but "auto-generates its pin" deserves *less* weight than the stamp gave it: it auto-generates a **case**; whether that case observes the key loop is per-key luck.
- `escaped-atchim: no` — Atchim surfaced N-2 himself; the residual is wording.

**F-5 — FU-01.3-D's card header reads `✅ DONE` with (e) and (h) open** (SPEC-01:195). The inline disclosure two bullets down is honest; the header is not — the exact "closed bullet readable as a closed class" failure the process amendment (`DEBT.md`) was written against **on the same day**. Reword to `✅ DONE — (e) CLAUDE.md line and (h) exit condition carried, see below`. Not a reopen.
- `escaped-atchim: n/a` — card wording.

## Rulings on the four questions put to him
1. **N-1 (DEBT-35's class relocated):** **agrees with Atchim.** DoD (c) named `:38` and only `:38`; it is closed. *"Not a bullet marked done that isn't; a bullet that was scoped too narrowly, which is a different failure and is now recorded as one."* The process amendment already fixes it at the right level.
2. **The third instance:** **debt is right, the description was wrong** — see F-3.
3. **N-2:** **not a gate**, but the stamp's weighting was too strong — see F-4.
4. **The stamp's non-independence:** **admissible for this delta, once, and only because this audit exists. Not a precedent.** Model pairing passes; the mutation evidence is reproducible (he reproduced MA–ME himself); it is labelled honestly. **But the cost is not hypothetical** — a same-instance gate re-checked its own APPROVE and missed a fail-open escape its own recorded method finds in one lookup. **No re-gate is required for S-01.3 to stay Done — this audit is the independent look.** **Binding going forward: the re-stamp landing F-1 must be run by an Atchim instance that did not review that diff.**

## NFR-01 walk (8b)
**FU-01.3-D changed no row's ✅/❌ standing.** N26 ✅ (unchanged — the new guard doesn't touch the id-scoping guarantee), N10 S-01.3 leg ✅ (unchanged; **F-1 is a fail-*open* path producing no abort at all — a correctness finding, not an N10 gap**), N16 S-01.3 leg ✅, N2 ✅, N21 ✅, N22 ✅. N1/N3/N6/N7/N8/N14/N15/N28 ⚠️ WAIVED → S-01.4 (N6/DEBT-30 caveat stands). No `feature` row left ⬜ PENDING. All `system` rows ⚠️ WAIVED → `/signoff`, **with a new note carried there: N12/N20 — F-1 shows `document_id` reaches the span `input` unvalidated; the "only `document_id` and expected fields" invariant assumes it is a string. Enforce it.**

## Containment — leg (2) AMENDED, no sixth item
`HARDEN-01.md` still absent; deferral to S-01.4 **UPHELD** (S-01.3 has no run loop to cascade through, and every *abort* path is fail-closed before the first SDK call — verified). The new guard is the same seam as leg (2), so **leg (2) is amended** rather than a leg added: *"the malformed-record seam — every `DocumentRecord` field's **presence and value type** (including F-1's non-string `document_id`, which is fail-open today), with the malformed record at index ≥ 2 (ME's larger-prefix residual, which fixtures cannot buy)."* **Condition remains five items; S-01.4 cannot reach Done without all five.**

## Open decision — `hypothesis` DEFER should now be REVISITED, not merely counted
Dunga's own trigger rule was "a third **distinct** residual appearing". F-1 is that third residual (**value types**), on top of ME's larger-prefix gap and N-2's fourth-key caveat. The trigger has fired.

---

# Re-audit #4 — 2026-09-21 (after FU-01.3-G)

**Verdict: ⚠️ Pass with follow-ups. S-01.3 STAYS DONE.** 0 Critical · 0 Major · **2 Minor** (1 new, 1 carried).
**Auditor:** Zangado (Fable 5.1) @ `f2ac3a6`. **Rigor:** standard, no gate skipped.
**Independence chain:** Dengoso **sonnet** → Atchim **opus ×3 distinct instances** (#1 FU-01.3-D, #2 FU-01.3-G review ×2 rounds, #3 the `/test` gate per DEBT-44) → Zangado **Fable 5.1**. Nothing below rests on any APPROVE, PASS or register row.

## Entry gates — all five stand, none falsified
Stamp ✅ PASSED with the `/test` gap-fill source `Risk: high` requires; `git diff --stat 758ab5f HEAD -- src/` **empty** (F-G2 was test-only); no deleted tests; only documented skips; `check_clean.py` exit 0; no merge/WIP commits, no TODO/FIXME.

## Own runs
| Scope | Result |
|---|---|
| Default | **498 passed, 14 skipped** |
| Live (Langfuse 4.38.0) | **511 passed, 1 skipped** |
| Composition subset | **72 passed** |
| mypy / ruff / pip-audit | clean / clean / clean |

**Independent mutation re-execution** (source restored byte-identical; `git diff --stat -- src/` empty at end): **M13** reproduced exactly — mutating `types.py:65` (`DocumentRecord`, *not* `DatasetItem` at `:24`) gives `1 failed, 175 passed`, collected 177→176, the sole failure being the production pin. **Confirmed decisive and probe-unreachable.** M1, M2, MY-9, M7 each re-killed, **each by a single test** — the pins are not redundant and each is the sole guard for its mutant.

## Gate lines
| Gate | Outcome |
|---|---|
| `Observability:` | **✅ VERIFIED** — observed, not read |
| `Containment:` | **REQUIRED → deferred to S-01.4, UPHELD. Five-item condition STANDS; leg (2) partially discharged, not closed** |
| `SCA:` | **✅ PASS** |
| `Secrets:` | **⚠️ — F-2 carried** (no credential; gate definition is the defect) |
| `Composition:` | **✅ PASS** (72 + live N26/INV-01) |
| `Cleanliness:` | **✅ PASS** |
| `LLM-Evals:` / `UI:` | N/A / N/A |

**Observability detail.** Root-logger DEBUG handler; both guards driven with `None` / `7` / `[MARKER]` / `{"k": MARKER}`. `record_run`: 4/4 typed `ExperimentRecordFailedError`, **0 log lines, 0 HTTP, 0 SDK**, marker absent from exception text *and* logs, score sentinels absent. `get_dataset`: 4/4 typed `DatasetFetchFailedError`, 0 log lines, marker absent. **INV-02 holds in everything they emit — which is exactly nothing beyond a constant message.** Recorded, not a gap: both guards are silent by design, so **S-01.4/N10 owes the log line** for both.

## The fix itself — verified structurally, not by fixture luck
`record_run` (`:452-470`): tracing-client check → dataset-name check → `for record in records: _require_record_shape(record)` — **before** `record_item_ids`, the A3 loop, `records_by_item_id`, `ExperimentItem` construction (`:522`) and `record_experiment` (`:535`). **No path from entry to any HTTP or SDK call skips the guard for any record.** Inside the guard, the `document_id` isinstance (`:111`) sits after the key loop and before first use (`:120`). `get_dataset`'s guard (`:293`) raises before `items.append` and before `_item_cache[item_id]`, so a malformed item never primes the cache. **F-1 of re-audit #3 is closed at both boundaries.**

## Findings

### Minor

**F-1 (NEW) — `get_dataset`'s value-type guard is hand-enumerated: `DatasetItem.item_id` is never checked. 1 of 2 `str` fields — the exact shape of re-audit #3's F-1, one boundary over.**
`langfuse_adapter.py:286` `item_id = raw_item["id"]` → `:307` `self._item_cache[item_id] = dataset_id`. Verified independently by the orchestrator: `get_type_hints(DatasetItem)`'s `str` fields are exactly `['document_id', 'item_id']`, and only the first is guarded. Driven live:
- `id: 7` / `id: None` → **`get_dataset` returns normally**, `items[0].item_id == 7`, `_item_cache` keyed `{7: …}`, 0 log lines. A later `record_run` then raises typed with **http=0, sdk=0** — so **fail-closed at the second boundary**.
- `id: [..]` / `id: {..}` → **untyped `TypeError: unhashable type`** inside `get_dataset` (REG-04/REG-09 untyped-escape class, fail-closed, 0 writes). No INV-02 leak either way.
- **Why Minor, not a reopen:** every path is fail-closed. **Why it matters anyway:** the card's own principle was *both trust boundaries or neither*, and bullet (iii) made the `record_run` side type-driven **precisely so this asymmetry could not recur** — but the `get_dataset` side got one hand-written test for one field. `DatasetItem` has exactly the two-`str`-field coincidence the probe was built to break.
- **`escaped-atchim: yes`** — declared type says `str`; DEBT-40's one-lookup method finds it; **three** instances looked at this function.

⚠️ **PROCESS FINDING (orchestrator, verified): this gap was already reported four hours earlier and never carded.** Atchim instance #2 raised it as **S-1** in its FU-01.3-G review — *"`get_dataset` type-checks `document_id` but not `item_id`, on the same three lines… the identical asymmetry that was F-1, reproduced in the fix for F-1, one variable to the left"* — and repeated it in its re-review under "Debt to hand to Dunga". `docs/state/DEBT.md` has **no row for it** (DEBT-43 records the general *rule*, not this gap). It was surfaced, routed, and evaporated; Zangado then rediscovered it as a finding. **This is SPEC-01:193 — "a baton sentence is not a backlog" — failing again, on the very card where a reviewer's routed item was supposed to become an explicit DoD bullet in the same session.** The rule exists; the enforcement does not.

**F-2 (CARRIED, = re-audit #3 F-2) — `Secrets: ⚠️` stands; `.gitleaks.toml` still absent.** `escaped-atchim: no` — gate design.
Re-run: staged **0**; `gitleaks git .` **1** (140 commits); **merge-base-scoped also 1** — re-confirmed that merge-base scoping does not clear this branch. `.gitleaks.toml` does not exist.
**Ruling: acceptable for S-01.3 to stay Done — for one more audit, not indefinitely.** Predicate: (a) working tree clean ✓; (b) the historical hit is triaged and verified non-credential ✓; (c) the fingerprint entry is a **human** decision (DEBT-45) blocked on nobody but the human ✓ — *"and that is the part that must not drift."* **S-01.3 is not the story whose Done depends on the secret gate's definition — T-01.4.10/S-01.4 is. If DEBT-45 is still open at S-01.4's `/qa`, that is an S-01.4 Done-blocker.**

## NFR-01 walk — FU-01.3-G changed no row's standing
N26 ✅ (score-id scoping untouched; the guard is a precondition on the record, not the id derivation), N10 S-01.3 leg ✅ (new paths add no new abort *reason*; silent by design), N16 ✅, N2 ✅, N21 ✅, N22 ✅. N1/N3/N6/N7/N8/N14/N15/N28 ⚠️ WAIVED → S-01.4 (N6/DEBT-30 stands). System rows ⚠️ WAIVED → `/signoff`.
✅ **The carried N12/N20 note is now DISCHARGED.** Re-audit #3 left: *"`document_id` reaches the span `input` unvalidated; the invariant assumes it is a string — enforce it."* `ExperimentItem.input={"document_id": …}` at `:526` is now only reachable after `_require_record_shape` has proven `isinstance(record["document_id"], str)` for **every** record (`:469-470`), and inbound, `get_dataset` refuses a non-string before it can prime the cache. **Rewritten for `/signoff`:** *"N12/N20 — `document_id` is type-enforced at both trust boundaries as of FU-01.3-G (`:111`, `:293`); residual: `item_id` is not enforced at `get_dataset` (re-audit #4 F-1), fail-closed downstream."*

## Containment — leg (2) partially discharged, five-item condition stands
FU-01.3-G discharges leg (2)'s *"value type — including F-1's non-string `document_id`"* clause at the unit level. It does **not** discharge *"every `DocumentRecord` field"* (only `str` fields are type-driven — F-G1's forward obligation) and does **not** touch *"malformed record at index ≥ 2"* (the killing test uses a single record at index 0; ME's anchor still only reaches index 1 — FU-01.3-H's residual). **No sixth item.** Leg (2) reworded: *"…every `DocumentRecord` field's presence and value type (str-typed value guards landed by FU-01.3-G; non-`str` fields are an event-shaped obligation), with the malformed record at index ≥ 2."*

## Rulings on the five questions put to him
1. **The fix** — closes F-1 at both boundaries; zero-platform-writes holds **structurally**, verified by tracing every path from `record_run`'s entry.
2. **The type-driven mechanism** — genuine, and the two-pin design is sound. He reproduced **M13** himself (avoiding the `DatasetItem`/`DocumentRecord` false-negative trap). *"Collapsing them would delete a guarantee, not a redundancy."* The register's "lead with M13" is right.
3. **My two corrections** — **accurate, not overcorrected.** One optional precision: `hint is str` is also **`NotRequired[str]`-inclusive** (the probe asserts `optional_field` *is* in the result), so "str-annotated" should read "resolves to `str` after `get_type_hints`". Not a finding.
4. **The sweep / FO-5** — **correctly out of scope for S-01.3.** `overall_gate` is S-01.1's surface; `VerdictMap` is produced only by `classify()`; S-01.3 consumes verdict strings it never interprets. **But he independently judges FO-5 real and sizes it Major on S-01.1's ledger** — *"fail-open in the direction that flips a CI gate green, and INV-08 makes the gate the source of truth"* — closed by a one-line `else: raise ClassifierError`. Card it against S-01.1/REG-01's family, not here.
5. **S-01.3 stays Done** — the only fail-open is closed structurally and pinned by single-test-kill mutants he re-ran; the new F-1 is fail-closed on every path.

## Escape-capture (9a)
- **F-1** — `escaped-atchim: yes` — same declared-type lookup, other boundary, three instances.
- **F-2** — `escaped-atchim: no` — gate design, outside the lens.

⚠️ **Signal worth more than either finding:** *"two consecutive audits, two '1 of 2 `str` fields' misses on the same card family, under three reviewer instances. The lens is finding **the reported field**, not **the declared type's field set**. DEBT-40's method is right; it is being applied to the field named in the finding rather than to the TypedDict."* That belongs in the DEBT-40/43/44 protocol amendment.
