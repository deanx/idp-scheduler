# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-19
**Commit:** 367065b — **re-gated 2026-09-22** by a fresh Atchim instance (DEBT-44) after S-01.4 work edited this story's modules (+206 lines in `idp_client.py`/`transport.py`), which had staled this stamp (DEBT-46). Falsifiable check, re-baselined: `git diff --stat 367065b HEAD -- src/idp_regression/adapter/` must be empty. The re-gate initially found **three stated guarantees that no test pinned** (each proven by a surviving mutant); `367065b` closed them tests-only, and a second fresh instance re-applied all four mutants itself — all killed by assertion or wrong-exception-type in under 0.2 s, answering the earlier slow-signal complaint. Prior: 2ae8617d10a0406417260f017d18f7ad3cbf8073
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** PASSED. /test gate over b393564..d84bfa6 (6 Scenario-B defects fixed, 7 coverage gaps pinned). Then, after QA S-01.2 ⚠️, a /test gate over the fix round cadbb69..2ae8617: F-1 non-finite timing config, F-2 redirect credential leak, and an independent fix-round audit (float-overflow defect plus two gaps fixed in 2ae8617). All mutants killed.
**Independence:** ✅ structural (different models): Dengoso (sonnet) reviewed by Atchim (opus). SPEC-01 is Risk: high.
**Static:** ✅ clean: mypy strict (57 files) + ruff. `# type: ignore` appears only in tests: 15 in tests/adapter/test_idp_client.py and 3 in tests/adapter/test_transport.py. All are deliberate: they feed invalid types or type test helpers and fake HTTP handlers. (QA S-01.2 F-4 corrected the earlier claim twice.) None in src/.
**Files:** docs/adr/0002-idp-adapter-and-normalize-contract.md, docs/design/CONTRACTS.md, docs/design/INVARIANTS.md, docs/qa/QA-01-baseline-regression-S-01.2.md, docs/qa/TEST-S-01.2-baseline-regression.md, pyproject.toml, src/idp_regression/adapter/errors.py, src/idp_regression/adapter/idp_client.py, src/idp_regression/adapter/normalize.py, src/idp_regression/adapter/token_cache.py, src/idp_regression/adapter/transport.py, src/idp_regression/adapter/types.py, tests/adapter/__init__.py, tests/adapter/fixtures/raw_idp_response.json, tests/adapter/test_errors.py, tests/adapter/test_idp_client.py, tests/adapter/test_integration_idp.py, tests/adapter/test_make_idp_adapter.py, tests/adapter/test_module_boundary.py, tests/adapter/test_normalize.py, tests/adapter/test_normalize_contract.py, tests/adapter/test_token_cache.py, tests/adapter/test_transport.py, uv.lock
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
| Unit + contract (default run) | 880 | 0 |
| Full suite incl. live integration (`RUN_INTEGRATION_TESTS=1`; live IDP OAuth token + live Langfuse) | 472 | 0 (1 skip: live submit/poll needs a real IDP action id + version → S-01.6) |

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
| N23: CR/LF token not leaked | test_idp_client.py:757; test_transport.py:124 | ✅ COVERED |
| N23: `redact` covers form-encoded + escaped-quote values | test_transport.py:178-204 | ✅ COVERED |
| NUL byte in path → typed error, path not echoed | test_transport.py:289 | ✅ COVERED |
| BR7: `expires_in` fail-closed | test_idp_client.py:346,362 | ✅ COVERED |
| BR7: two `extract()` calls → one token fetch | test_idp_client.py:149 | ✅ COVERED |
| N21: surrogate prompt key / unsafe column name | test_normalize.py:187,148 | ✅ COVERED |
| Status strings capped + sanitized | test_errors.py:14-47 | ✅ COVERED |
| BR9: env default `SUCCEEDED` | test_make_idp_adapter.py:29 | ✅ COVERED |
| Poll budget includes submit time | test_idp_client.py:589 | ✅ COVERED |
| N1: timing equals the clock delta | test_idp_client.py:302 | ✅ COVERED |
| Domain: extracted values never in logs/stdout/stderr | test_idp_client.py:256 | ✅ COVERED |
| QA F-1 / REG-06: invalid timing config → `IDPConfigurationError` at construction | test_idp_client.py:590-657 | ✅ COVERED |
| QA F-1: invalid env timing → typed error; margin 0 accepted | test_make_idp_adapter.py:59-95 | ✅ COVERED |
| QA F-1: timing cap 3600 accepted / 3600.1 rejected (constructor + env) | test_idp_client.py:668,680,685,689; test_make_idp_adapter.py:99,108 | ✅ COVERED |
| QA F-1: huge int → typed error (no raw OverflowError); bool rejected | test_idp_client.py:706,726 | ✅ COVERED |
| QA F-1: NaN poll timeout can't loop forever | test_idp_client.py:696 | ✅ COVERED |
| QA F-2 / REG-07: 3xx → typed error; the token never reaches the redirect target (two real servers); response closed | test_transport.py:400-452 | ✅ COVERED |
| ADR-0004 #3 — the absolute poll deadline still fires after retries consumed the budget (asserts 2 calls against a 3-attempt budget, so deadline-fired is distinguished from budget-exhausted) | test_idp_client.py::test_poll_deadline_still_fires_after_retries_have_consumed_the_budget | ✅ COVERED |
| DEBT-21 class, one level deeper — the retried GET after 401/403 carries the **refreshed** token, not the old Bearer | ::test_poll_second_get_after_401_uses_the_refreshed_token_not_the_old_one | ✅ COVERED |
| `Retry-After` guard — rejects negative / non-finite / over-cap, accepts the cap inclusively | ::test_parse_retry_after_seconds_rejects_negative_nonfinite_and_over_cap (+ boundary companion) | ✅ COVERED |
| `Retry-After` honoured only on 429, never on a 5xx | ::test_poll_retry_sleep_seconds_ignores_retry_after_header_on_a_non_429_status | ✅ COVERED |
| Bounded 5xx/429 poll retry; one-refresh-then-fail-closed; `_validate_timing` hardening; no-redirect opener; `_require` whitespace rejection | test_idp_client.py / test_transport.py (earlier delta, re-mutated and killed in the re-gate) | ✅ COVERED |

**Deferred:**
- DEBT-26: the platform transport redirect leak (sibling of F-2) → S-01.3 follow-up; REG-07 pending-test there.
- DEBT-24: 5xx/429 retry (ADR-0004 #6) → S-01.4.
- DEBT-21: mid-poll 401 refresh; the refresh must live in the adapter poll loop → S-01.4.
- DEBT-22: CT-01 real fixture + live submit/poll → S-01.6.
- DEBT-23: sanitize `IDPExecutionFailedError.status` → S-01.4.
- Containment → /harden before the epic is Done.

## History
- /test gap-fill (Atchim TDD gate) PASSED at d84bfa6fe2ffdc28ff2cdf7375569e06f1787637, before the QA S-01.2 fix round. Superseded.
- /implement (Atchim TDD gate) PASSED at 481e0db788a94b1ff39bd96879dab5168812322d. Superseded by this /test stamp (the Risk: high rigor gate requires one).
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round-1 R1–R8 and round-2 test gaps, all closed. Round-1 summary: (Atchim, 2026-09-19)  ### Required 1. **Name check too loose** (`normalize.py:22,66`): `$` matches before a trailing newline, so `"total\n"` is accepted; there is also no length cap (DoD line 82). 2. **Raw exceptions escape `normalize()`:** `OverflowError` on a huge-int confidence (`:96`); `UnicodeEncodeError` on a lone surrogate (`:83`), which carries the PII value. 3. **Raw exceptions escape `extract()` through the transport** (`transport.py:108-131`): RemoteDisconnected, ConnectionResetError, IncompleteRead, UnicodeDecodeError and RecursionError. A missing file raises `FileNotFoundError` with the path in the message. 4. **A missing or null status polls to timeout** (`idp_client.py:179-192`), but ADR-0004 #17 says it must abort. `test_idp_client.py:247` pins the wrong behaviour. 5. **The poll ignores non-2xx except 401/403** (`:174-178`): a 404/400 keeps polling (ADR-0004 #5 says hard 
- Initial stamp
