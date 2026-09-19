# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED
**Source:** /implement (Atchim code review TDD gate)
**Date:** 2026-09-19
**Commit:** 481e0db788a94b1ff39bd96879dab5168812322d
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** PASSED after 2 rounds of REQUEST CHANGES; round-3 APPROVE on 2026-09-19
**Independence:** ✅ structural (different models): Dengoso (sonnet) reviewed by Atchim (opus). SPEC-01 is Risk: high.
**Static:** ✅ clean: mypy strict (55 files) + ruff. No `# type: ignore` in src/tests.
**Files:** docs/design/CONTRACTS.md, docs/design/INVARIANTS.md, docs/qa/TEST-S-01.2-baseline-regression.md, pyproject.toml, src/idp_regression/adapter/errors.py, src/idp_regression/adapter/idp_client.py, src/idp_regression/adapter/normalize.py, src/idp_regression/adapter/token_cache.py, src/idp_regression/adapter/transport.py, src/idp_regression/adapter/types.py, tests/adapter/__init__.py, tests/adapter/fixtures/raw_idp_response.json, tests/adapter/test_idp_client.py, tests/adapter/test_integration_idp.py, tests/adapter/test_module_boundary.py, tests/adapter/test_normalize.py, tests/adapter/test_normalize_contract.py, tests/adapter/test_token_cache.py, tests/adapter/test_transport.py, uv.lock
**Sequence:** test-first per slice:
- types/errors/TokenCache (6bc5e02)
- transport (622d7c8)
- normalize (ec6805a)
- extract + CT-01 (e86b3ed)
- live integration (354b863)

Fix rounds: R1 8d84491, R2 7131712, R3 e1742d3, R4/R5/R8 75987a8, R7 320fd7c, R6 fa94db1, suggestions f89d354/02ad58c, round-2 mutant kills 481e0db. Atchim re-applied every earlier surviving mutant, and all are killed.

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Unit + contract (default run) | 348 | 0 |
| Full suite incl. live integration (`RUN_INTEGRATION_TESTS=1`; live IDP OAuth token + live Langfuse) | 361 | 0 (1 skip: live submit/poll needs a real IDP action id + version → S-01.6) |

## AC coverage

| DoD / TP row | Tests | Status |
|---|---|---|
| 3c/3d `pages[]` walk | test_normalize.py:23; test_idp_client.py:115 | ✅ COVERED |
| A3 / TP-12 auth fail-closed, no retry | test_idp_client.py:149; test_token_cache.py:69 | ✅ COVERED |
| BR7 / TP-24 token cached, refresh margin | test_token_cache.py:35,52 | ✅ COVERED |
| BR9 / TP-25 configurable allowlist, no literal | test_idp_client.py:211,361; test_module_boundary.py:28 | ✅ COVERED |
| ADR-0004 #17 missing status → abort | test_idp_client.py:267 | ✅ COVERED |
| ADR-0004 #5 non-2xx → hard failure | test_idp_client.py:339 | ✅ COVERED |
| Poll timeout with last status | test_idp_client.py:249 | ✅ COVERED |
| Submit not retried | test_idp_client.py:167 | ✅ COVERED |
| INV-07 / TP-18 monotonic clock | test_idp_client.py:464,473,480 | ✅ COVERED |
| Budget clamp | test_idp_client.py:373 | ✅ COVERED |
| Merge semantics (last-wins / concat / duplicate prompt) | test_normalize.py:44,56,68 | ✅ COVERED |
| N21 / TP-20 unsafe names incl. 129 chars | test_normalize.py:99,119,133 | ✅ COVERED |
| Size caps | test_normalize.py:211,308,316 | ✅ COVERED |
| Confidence NaN/out-of-range → None | test_normalize.py:357 | ✅ COVERED |
| Three-state absent/null/empty | test_normalize.py:378,384,393 | ✅ COVERED |
| Malformed body / missing status → typed error | test_normalize.py:406,411 | ✅ COVERED |
| CT-01 key parity + real `classify()` | test_normalize_contract.py:107,121 | ✅ COVERED |
| `extract` signature (ADR-0002 amendment) | test_normalize_contract.py:68 | ✅ COVERED |
| N23/N5 redaction + sanitized logs | test_transport.py:124,154,261; test_idp_client.py:495,524 | ✅ COVERED |
| N1 timing metric; integration | test_idp_client.py:138; test_integration_idp.py:36,53 | ✅ COVERED (live submit/poll skipped) |

**Deferred:**
- DEBT-24: 5xx/429 retry (ADR-0004 #6) → S-01.4.
- DEBT-21: mid-poll 401 refresh; the refresh must live in the adapter poll loop → S-01.4.
- DEBT-22: CT-01 real fixture + live submit/poll → S-01.6.
- DEBT-23: sanitize `IDPExecutionFailedError.status` → S-01.4.
- Containment → /harden before the epic is Done.

## History
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round-1 R1–R8 and round-2 test gaps, all closed. Round-1 summary: (Atchim, 2026-09-19)  ### Required 1. **Name check too loose** (`normalize.py:22,66`): `$` matches before a trailing newline, so `"total\n"` is accepted; there is also no length cap (DoD line 82). 2. **Raw exceptions escape `normalize()`:** `OverflowError` on a huge-int confidence (`:96`); `UnicodeEncodeError` on a lone surrogate (`:83`), which carries the PII value. 3. **Raw exceptions escape `extract()` through the transport** (`transport.py:108-131`): RemoteDisconnected, ConnectionResetError, IncompleteRead, UnicodeDecodeError and RecursionError. A missing file raises `FileNotFoundError` with the path in the message. 4. **A missing or null status polls to timeout** (`idp_client.py:179-192`), but ADR-0004 #17 says it must abort. `test_idp_client.py:247` pins the wrong behaviour. 5. **The poll ignores non-2xx except 401/403** (`:174-178`): a 404/400 keeps polling (ADR-0004 #5 says hard 
- Initial stamp
