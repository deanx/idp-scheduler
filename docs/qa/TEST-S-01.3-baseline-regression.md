# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-20
**Commit:** bb58be0397e5f4855e2d455b8150064a775bad21
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** PASSED (2026-09-20, over FU-01.3-A `1187944..bb58be0`. Atchim re-ran mypy/ruff and the live suite himself rather than accepting the claim, and — because tests and source landed in one commit, so git cannot prove ordering — **reproduced RED independently** in a scratch worktree by reverting only `langfuse_adapter.py`/`types.py` to `1187944~1`: 3 failed, 9 passed, the three failures being exactly the refusal tests with `DID NOT RAISE`, and the positive-path test passing on old code, proving it is not vacuous. He then ran **seven hand-written mutants**; all died. M3 — `run_id` hard-coded, i.e. the parameter not consumed, the very defect A3 closes — survived all four new tests and died only incidentally in an unrelated INV-01 test; fixed in bb58be0 by parametrizing the positive path over two run_ids, and I re-applied M3 locally to confirm it now dies in the right file. Verdict APPROVED, 0 Critical, 0 Required. Earlier gates: 71e5e6a, ba6a905.)
**Independence:** ✅ structural (different models): Dengoso (Opus 5) reviewed by Atchim (**Fable 5.1**). SPEC-01 is Risk: high. ⚠️ Note: the first re-review of this change was launched on Opus — same model as the implementer — which the stamp schema forbids for a Risk: high spec. It was stopped before producing a verdict and relaunched on Fable 5.1; the Opus run contributed nothing to this stamp. `hooks/check_reviewer_independence.py`, named by the schema as the enforcement point, does not exist in this repo, so this line is the honest label and not a machine-checked fact.
**Static:** ✅ clean: mypy strict (41 files) + ruff; pip-audit clean (`langfuse==4.15.4` confined to `make_platform()`, N24)
**Files:** CLAUDE.md, docs/adr/0002-idp-adapter-and-normalize-contract.md, docs/adr/0004-run-orchestration-and-failure-containment.md, docs/adr/0005-evaluation-platform-redecision.md, docs/design/CONTRACTS.md, docs/design/DATA-MODEL-01.md, docs/design/INVARIANTS.md, docs/design/SEQ-UC-01-baseline-regression.md, docs/qa/NFR-01.md, docs/qa/QA-01-baseline-regression-S-01.3.md, docs/qa/TEST-S-01.3-baseline-regression.md, pyproject.toml, src/idp_regression/platform/errors.py, src/idp_regression/platform/hashing.py, src/idp_regression/platform/langfuse_adapter.py, src/idp_regression/platform/schema/__init__.py, src/idp_regression/platform/schema/golden_schema_v1.json, src/idp_regression/platform/schema_provisioning.py, src/idp_regression/platform/scoring.py, src/idp_regression/platform/tracing.py, src/idp_regression/platform/transport.py, src/idp_regression/platform/types.py, tests/conftest.py, tests/platform/__init__.py, tests/platform/_tp45_subprocess_scenario.py, tests/platform/test_golden_schema_contract.py, tests/platform/test_hashing.py, tests/platform/test_integration_langfuse.py, tests/platform/test_inv01_payload.py, tests/platform/test_langfuse_adapter.py, tests/platform/test_log_redaction.py, tests/platform/test_module_boundary.py, tests/platform/test_record_run_preconditions.py, tests/platform/test_schema_provisioning.py, tests/platform/test_score_contract.py, tests/platform/test_scoring.py, tests/platform/test_tracing.py, tests/platform/test_transport.py, uv.lock
**Files (FU-01.3-A delta, 1187944..bb58be0):** src/idp_regression/platform/langfuse_adapter.py (record_run precondition, +24), src/idp_regression/platform/types.py (PlatformAdapter.record_run docstring only), tests/platform/test_record_run_preconditions.py (+4 tests, one now parametrized over two run_ids), tests/platform/test_langfuse_adapter.py (fixture realignment to the real score_id() derivation + one assertion strengthened), tests/platform/test_inv01_payload.py (fixture realignment)
**Sequence:** test-first per slice (git-verified, tests in the same or an earlier commit):
- CT-05 schema contract, then the schema file (90bc241)
- test_hashing, then hashing.py (471d7d3)
- test_scoring, then scoring.py (8462eef)
- test_langfuse_adapter, then the adapter plus types/errors/transport (9f5f755)
- CT-03 / INV-01 / N24 tests, then build_score_inputs (8e3aaa1)
- test_schema_provisioning, then provisioning (f3416ce)
- integration tests (9da95f3)

Fix rounds: traceId/dataType (f107524); tracing, **probed live before its tests**, a declared and Atchim-accepted deviation for SDK-behaviour discovery (e4aca51); R7 transport (5aba5d4); R1 pagination (5a7d46e); record_run per ADR-0005 #9 (9de97bd); redaction (504a8ab); round-2 test gaps (a003843, 6e20b7d, 1b110e0, 56d1c42); TP-45 subprocess isolation (02380cd); /v3/scores id= filter (4956278).

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Unit + contract (default run, whole repo) | 464 | 0 |
| Full suite incl. live integration (`RUN_INTEGRATION_TESTS=1`, local Langfuse 4.38.0, synthetic data) | 477 | 0 |
| `tests/platform` only (this story's package) | 143 | 0 |
| Bug-repros (`@bug-repro`) | — | none |

One skip in the live run: `tests/adapter/test_integration_idp.py:63`, the S-01.6 live submit/poll test — no published IDP action id/version exists yet. Skipped by design, not an unlinked skip. The 12 `test_integration_langfuse.py` skips in the default run are the by-design integration gate (`tests/conftest.py:17-25`).

Baseline before this round was 472/1 at `fe09898`. +4 at `1187944` (the A3 tests), +1 at `bb58be0` (the second parametrize case) = 477.

Atchim mutation checks: every survivor from round 2 is killed. The earlier round killed two TP-45 mutations (span output leaking `repr(exc)`; an extra `document_id` in the output). **2026-09-20 (FU-01.3-A, Fable 5.1):** seven further mutants against the A3 precondition — check only the first record (killed), only the first score (killed), `run_id` hard-coded (survived the four new tests; see the gate line, fixed in `bb58be0`), `run_name` substituted for `run_id` (killed), `raise`→`continue` warn-only (killed), check relocated after `record_experiment` (killed by the `run_experiment_calls == 0` assertions), offending id appended to the message (killed by the INV-02 test). No mutant survives the suite.

**Independent coverage audit (2026-09-20, fresh agent, no implementation context):** no MISSING rows across 23 DoD items and 15 S-01.3 test-plan rows. Pre-#9 DoD items (L119, L131, and parts of L125/L135/L136) are classified **SUPERSEDED**, not gaps — ADR-0005 #9 reshaped the Protocol to `get_dataset`/`record_run`/`mark_run_status`. One genuine uncovered obligation, correctly deferred: **INV-05** (`load_dotenv()` before client construction) is neither tested nor implemented here — `langfuse_adapter.py` assigns it to the caller and `orchestration/` is still empty, so it is an S-01.4 duty. Do not read this stamp as covering it. The audit also flagged six weak-but-passing tests; the one with real teeth is `test_make_platform_dispatches_on_platform_env` (`test_langfuse_adapter.py:294-303`), an `isinstance` smoke check that never asserts host/keys were wired, so a factory ignoring `LANGFUSE_HOST` would pass it — routed to FU-01.3-B rather than re-opened here.

## AC coverage

| Row | Tests | Status |
|---|---|---|
| TP-15 INV-01 payloads (incl. no sentinel value in any posted score body) | test_inv01_payload.py:76,129,150 | ✅ COVERED |
| TP-22 N26 contract | test_score_contract.py:104; test_scoring.py:23 | ✅ COVERED |
| TP-32 CT-05 | test_golden_schema_contract.py:69-118 | ✅ COVERED |
| TP-33 tables/schema live | test_integration_langfuse.py:184,130 | ✅ COVERED |
| TP-34 invalid write 400 | test_integration_langfuse.py:141 | ✅ COVERED |
| TP-35 schema passthrough | test_langfuse_adapter.py:72; test_integration_langfuse.py:130 | ✅ COVERED |
| R1 regression pin (items, pagination) | test_langfuse_adapter.py:109; test_integration_langfuse.py:114 | ✅ COVERED |
| TP-36 score_id | test_scoring.py:11-38 | ✅ COVERED |
| TP-37 N26 live | test_integration_langfuse.py:261,301 | ✅ COVERED (experiment merge on a repeated run_name → DEBT-19, S-01.4) |
| TP-38 events_only / v3 (live comment-is-None readback) | test_integration_langfuse.py:366,446 | ✅ COVERED |
| TP-39 prompt family | test_scoring.py:43-55; test_score_contract.py:76 | ✅ COVERED |
| CT-03 / N9 (comment always None, DEBT-18 B) | test_score_contract.py:63,76,91,124,139,164,180 | ✅ COVERED |
| INV-04 hash_dataset | test_hashing.py:21-55 | ✅ COVERED |
| TP-42 never drop schema | test_schema_provisioning.py:25,38,47 | ✅ COVERED |
| TP-43 flush_failed | test_tracing.py:87,103,127; test_integration_langfuse.py:466 | ✅ COVERED |
| TP-44 redaction | test_log_redaction.py:34-70; test_transport.py:57; test_integration_langfuse.py:493 | ✅ COVERED |
| TP-45 span allowlist (no golden/expected/actual/confidence sentinel in any span attribute) | test_inv01_payload.py:402,451 (subprocess) | ✅ COVERED |
| run_status / make_platform / N24 | test_langfuse_adapter.py:224,259; test_module_boundary.py:14 | ✅ COVERED |
| ADR-0005 #9 record_run preconditions | test_record_run_preconditions.py:85-233 | ✅ COVERED |
| Coverage-audit gap-fill (/test, 1ec2ace) | ScoreWriteFailedError; CT-05 `then` walker + `\t`/`\u007F`/201-char cases; absent schema → None; TP-42 no-DELETE/expand/contract/remove-raises; TP-34 date case (live); prompt-hash non-ASCII digest + hash_dataset known digest; OTLP 4xx/5xx redaction; no /api/public/ingestion traffic; Protocol has no get_golden_version; mark_run_status values + RunMetadata | ✅ COVERED |
| N5 log injection (SCENARIO-B → fixed 86b7da4/ba6a905) | test_langfuse_adapter.py:424,448,504; test_schema_provisioning.py:107; test_transport.py:99 (`\n` + `"` cases per log path) | ✅ COVERED |
| QA fix F-2 / REG-03: bounded score-write retry | test_langfuse_adapter.py:613,634,660,678,695,714 + persistent TransportError test | ✅ COVERED |
| QA fix F-3 / REG-04: untrusted pagination shapes + page cap + empty dataset | test_langfuse_adapter.py:735,760,774,789 | ✅ COVERED |
| QA fix F-5 / REG-05: no raw prompt key in errors | test_score_contract.py:164 | ✅ COVERED |
| QA fix F-1: Soneca schema C1–C3 + root additionalProperties | test_golden_schema_contract.py:162-262 (behavioral) + C1/C2/C3/root structural walk | ✅ COVERED |

**N/A or deferred:**
- **TP-43 abort-reason mapping** and the **containment DoD item** → S-01.4 and `/harden`.
- **DEBT-19** (unique experiment name per invocation) → Soneca/S-01.4, before S-01.4 is Done.
- **T-01.3.11 docs + Soneca schema review** → Zangado to confirm.
- **F-1 image pin** → user/Mestre.
- **N25** → user/Mestre, before any real-document run. DEBT-18 is closed: the user chose option B (no values on the platform), implemented in 50ce083, 49c2150, 320460f, 22d7c3c and 825201a.

## History
- /test gap-fill (Atchim TDD gate) PASSED on 2026-09-19 at 71e5e6acc81f6b0bd8bd224f6d0997c9f6ccce7b: ✅ PASSED. Superseded by this re-stamp after FU-01.3-A (the A3 `run_id` precondition control) changed `langfuse_adapter.py`, `types.py` and three test files, staling the stamp.
- /test gap-fill (Atchim TDD gate) PASSED at ba6a9056969136ef488eaeeca37b86e28483ac73, before the QA fix round. Superseded after QA S-01.3 ⚠️ (F-1..F-5) was fixed and re-gated.
- /implement (Atchim TDD gate) PASSED at 825201a07fa8b3de3f31f326eaadbc1dc23a558b. Superseded by this /test stamp (required by the /qa rigor gate for Risk: high).
- /implement (Atchim TDD gate) PASSED at 02cff1be9a607c91b3dc267a601f5003614a210a. Superseded by this re-stamp after the DEBT-18 option-B change (Atchim found a vacuous score-body assertion, fixed in 825201a, then re-APPROVED).
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round 1 findings R1–R7, then round-2 and round-3 test gaps; all closed. Round-1 detail: (Atchim, 2026-09-19)  **Required:** - **R1 (Critical)** `langfuse_adapter.py:68`: `get_dataset` reads `body["items"]`, but live `GET /api/public/v2/datasets/{name}` on 4.38.0 returns no `items` key. The result is 0 items: an empty golden set that passes without checking anything, and `hash_dataset` hashes nothing. The unit mock (`test_langfuse_adapter.py:53`) invents `items`. Fix: fetch from the paginated `/api/public/dataset-items?datasetName=` and add a live test that asserts the item count. **PIN as a regression.** - **R2** Tests that don't assert anything:   - `test_integration_langfuse.py:105` `_score_visible` only checks HTTP 200;   - the N26 test (`:113`) has no assertion and uses `uuid4` instead of `score_id()`;   - TP-34 lacks "stored value unchanged" and "400 body not logged";   - TP-33's tables-block write is untested. - **R3** TP-45 is missing: there is no in-memory span-exporter test. `run_experiment` writes `EXPECTED_OUTPUT`, `input`, `output` and `str(exception)` into span attributes, so INV-01 is unverified on spans. - **R4** TP-44 is missing: no captured-log test on the REST/provisioning path, and `transport.redact()` is never called. - **R5** `tracing.py:72` `flush_or_raise` gives false negatives:   - (a) `run_experiment` calls `self.flush()` internally, outside the watcher;   - (b) batches exported by the background thread during the run are not watched;   - (c) dataset-run-item failures are logged on the `langfuse` logger, which is not watched. - **R6** Ar
- Initial stamp
