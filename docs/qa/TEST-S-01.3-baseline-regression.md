# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ✅ PASSED
**Source:** /implement (Atchim code review TDD gate)
**Date:** 2026-09-19
**Commit:** 02cff1be9a607c91b3dc267a601f5003614a210a
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** PASSED (after 3 rounds of REQUEST CHANGES; final APPROVE on 2026-09-19)
**Independence:** ✅ structural (different models): Dengoso (sonnet) reviewed by Atchim (opus). SPEC-01 is Risk: high.
**Static:** ✅ clean: mypy strict (41 files) + ruff; pip-audit clean (`langfuse==4.15.4` confined to `make_platform()`, N24)
**Files:** docs/adr/0004-run-orchestration-and-failure-containment.md, docs/adr/0005-evaluation-platform-redecision.md, docs/design/CONTRACTS.md, docs/design/DATA-MODEL-01.md, docs/design/INVARIANTS.md, docs/qa/TEST-S-01.3-baseline-regression.md, pyproject.toml, src/idp_regression/platform/errors.py, src/idp_regression/platform/hashing.py, src/idp_regression/platform/langfuse_adapter.py, src/idp_regression/platform/schema/__init__.py, src/idp_regression/platform/schema/golden_schema_v1.json, src/idp_regression/platform/schema_provisioning.py, src/idp_regression/platform/scoring.py, src/idp_regression/platform/tracing.py, src/idp_regression/platform/transport.py, src/idp_regression/platform/types.py, tests/conftest.py, tests/platform/__init__.py, tests/platform/_tp45_subprocess_scenario.py, tests/platform/test_golden_schema_contract.py, tests/platform/test_hashing.py, tests/platform/test_integration_langfuse.py, tests/platform/test_inv01_payload.py, tests/platform/test_langfuse_adapter.py, tests/platform/test_log_redaction.py, tests/platform/test_module_boundary.py, tests/platform/test_record_run_preconditions.py, tests/platform/test_schema_provisioning.py, tests/platform/test_score_contract.py, tests/platform/test_scoring.py, tests/platform/test_tracing.py, tests/platform/test_transport.py, uv.lock
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
| Unit + contract (default run) | 164 | 0 |
| Full suite incl. live integration (`RUN_INTEGRATION_TESTS=1`, local Langfuse 4.38.0, synthetic data) | 175 | 0 |
| Bug-repros (`@bug-repro`) | — | none |

Atchim mutation checks: every survivor from round 2 is killed. The final round killed two more TP-45 mutations: span output leaking `repr(exc)`, and an extra `document_id` in the output.

## AC coverage

| Row | Tests | Status |
|---|---|---|
| TP-15 INV-01 payloads | test_inv01_payload.py:76,129 | ✅ COVERED |
| TP-22 N26 contract | test_score_contract.py:104; test_scoring.py:23 | ✅ COVERED |
| TP-32 CT-05 | test_golden_schema_contract.py:69-118 | ✅ COVERED |
| TP-33 tables/schema live | test_integration_langfuse.py:184,130 | ✅ COVERED |
| TP-34 invalid write 400 | test_integration_langfuse.py:141 | ✅ COVERED |
| TP-35 schema passthrough | test_langfuse_adapter.py:72; test_integration_langfuse.py:130 | ✅ COVERED |
| R1 regression pin (items, pagination) | test_langfuse_adapter.py:109; test_integration_langfuse.py:114 | ✅ COVERED |
| TP-36 score_id | test_scoring.py:11-38 | ✅ COVERED |
| TP-37 N26 live | test_integration_langfuse.py:261,301 | ✅ COVERED (experiment merge on a repeated run_name → DEBT-19, S-01.4) |
| TP-38 events_only / v3 | test_integration_langfuse.py:366 | ✅ COVERED |
| TP-39 prompt family | test_scoring.py:43-55; test_score_contract.py:76 | ✅ COVERED |
| CT-03 / N9 | test_score_contract.py:63,91,124,139 | ✅ COVERED |
| INV-04 hash_dataset | test_hashing.py:21-55 | ✅ COVERED |
| TP-42 never drop schema | test_schema_provisioning.py:25,38,47 | ✅ COVERED |
| TP-43 flush_failed | test_tracing.py:87,103,127; test_integration_langfuse.py:466 | ✅ COVERED |
| TP-44 redaction | test_log_redaction.py:34-70; test_transport.py:57; test_integration_langfuse.py:493 | ✅ COVERED |
| TP-45 span allowlist | test_inv01_payload.py:311,338 (subprocess) | ✅ COVERED |
| run_status / make_platform / N24 | test_langfuse_adapter.py:224,259; test_module_boundary.py:14 | ✅ COVERED |
| ADR-0005 #9 record_run preconditions | test_record_run_preconditions.py:85-233 | ✅ COVERED |

**N/A or deferred:**
- **TP-43 abort-reason mapping** and the **containment DoD item** → S-01.4 and `/harden`.
- **DEBT-19** (unique experiment name per invocation) → Soneca/S-01.4, before S-01.4 is Done.
- **T-01.3.11 docs + Soneca schema review** → Zangado to confirm.
- **F-1 image pin** → user/Mestre.
- **N25** and **DEBT-18** (actual extracted values on the platform, a policy decision) → user, before any real-document run.

## History
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round 1 findings R1–R7, then round-2 and round-3 test gaps; all closed. Round-1 detail: (Atchim, 2026-09-19)  **Required:** - **R1 (Critical)** `langfuse_adapter.py:68`: `get_dataset` reads `body["items"]`, but live `GET /api/public/v2/datasets/{name}` on 4.38.0 returns no `items` key. The result is 0 items: an empty golden set that passes without checking anything, and `hash_dataset` hashes nothing. The unit mock (`test_langfuse_adapter.py:53`) invents `items`. Fix: fetch from the paginated `/api/public/dataset-items?datasetName=` and add a live test that asserts the item count. **PIN as a regression.** - **R2** Tests that don't assert anything:   - `test_integration_langfuse.py:105` `_score_visible` only checks HTTP 200;   - the N26 test (`:113`) has no assertion and uses `uuid4` instead of `score_id()`;   - TP-34 lacks "stored value unchanged" and "400 body not logged";   - TP-33's tables-block write is untested. - **R3** TP-45 is missing: there is no in-memory span-exporter test. `run_experiment` writes `EXPECTED_OUTPUT`, `input`, `output` and `str(exception)` into span attributes, so INV-01 is unverified on spans. - **R4** TP-44 is missing: no captured-log test on the REST/provisioning path, and `transport.redact()` is never called. - **R5** `tracing.py:72` `flush_or_raise` gives false negatives:   - (a) `run_experiment` calls `self.flush()` internally, outside the watcher;   - (b) batches exported by the background thread during the run are not watched;   - (c) dataset-run-item failures are logged on the `langfuse` logger, which is not watched. - **R6** Ar
- Initial stamp
