# QA-01 (S-01.2): audit of SPEC-01 / Story S-01.2, the IDP adapter + normalize() (Epic A)

**Verdict (re-check 2026-09-19, current):** ✅ Pass. S-01.2 is **Done**. See the re-check section at the end.
**Verdict (initial audit, superseded):** ⚠️ Pass with follow-ups. No Critical findings. One Major (F-1) and four Minor (F-2..F-5).
**Rigor:** standard (CLAUDE.md ## Rigor). SPEC Risk: high.
**Author:** alex@divinocosta.com.br (solo mode). Auditor: Zangado.
**Date:** 2026-09-19. **Branch:** feat/S-01.2-idp-adapter @ d44797f.
**Path note:** `docs/qa/QA-01-baseline-regression.md` is S-01.1's report, and the S-01.3 report carries the `-S-01.3` suffix. Neither was touched. This file carries the `-S-01.2` suffix.

## Entry gates
- Stamp `docs/qa/TEST-S-01.2-baseline-regression.md`: ✅ PASSED. Source `/test gap-fill (Atchim TDD gate)`. Commit d84bfa6. PASS.
- Rigor gate: SPEC-01 is `Risk level: high`, and the stamp is /test-sourced. PASS.
- Freshness: `git log --oneline d84bfa6..HEAD -- src tests` returns nothing. d44797f is docs only. PASS.
- Branch: feat/S-01.2-idp-adapter. PASS.

## Tests
- Unit/contract: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q` → **387 passed, 14 skipped**. The 14 skips are the integration tests, which are off by design without `RUN_INTEGRATION_TESTS` (tests/conftest.py:20).
- Live: `RUN_INTEGRATION_TESTS=1` with `.env` sourced → **400 passed, 1 skipped**.
  - The live IDP OAuth token fetch and cache reuse passed (test_integration_idp.py:36).
  - The skip is test_integration_idp.py:63. Live submit/poll needs `IDP_ACTION_ID`, a version and `IDP_TEST_DOCUMENT_PATH`. `IDP_ACTION_ID` is empty in `.env`, and no document was submitted to the live IDP.
  - These results match the stamp.
- Static: `mypy` over src and tests: "no issues found in 57 source files". `ruff check src tests`: clean.
- TDD spot-check:
  - (a) `git diff --name-status 8306d61 HEAD | grep '^D.*test'` returns nothing. The same holds against the main merge-base d3d1444.
  - (b) The only skips are the env-gated integration skips (conftest.py:20, test_integration_idp.py:32,63). There is no xfail and no unlinked skip.
- History: 31 commits since 8306d61, all tagged `#S-01.2`. No merge commits and no WIP commits.

## DoD walk (S-01.2)
| # | DoD line | Result | Evidence |
|---|---|---|---|
| 1 | All AC met (3c/3d, A3, BR7, BR9) | met, with a deviation | 3c/3d: test_normalize.py:23; test_idp_client.py (fake executions API). A3: test_idp_client.py:324. BR7: test_token_cache.py:35,52; test_idp_client.py:149. BR9: test_idp_client.py:427; test_module_boundary.py:28. The live leg of 3c waits on S-01.6. That deferral is legitimate: the DoR says "not Ready for live/integration" |
| 2 | Unit tests (normalize against fixtures, TokenCache, fake-API poll loop, A3 no retry) | met | Suite is green. See the rows above |
| 3 | CT-01 incl. mypy type-check | met, with a deviation | test_normalize_contract.py: 29 contract tests green, including exact Required/Optional key parity with `classifier.types` (:107) and a real `classify()` call (:121). mypy passes that call with no ignore. The fixture is synthetic (DEBT-22). That is legitimate: T-01.2.6 and the DoR both say the captured fixture comes from S-01.6. CT-01 must be re-pinned then |
| 4 | Untrusted-input contract (N21) | met | test_normalize.py: 99/119/133/141/148 names; 68 duplicate prompt; 247/268 value_too_large; 344/352 table_too_large; 393 NaN/out-of-range → None, not clamped; 366 huge int; 377 surrogate; 414/420/429 three-state; 442/447/467–484 malformed → typed. Code reading of normalize.py found no raw KeyError/AttributeError path |
| 5 | Name sanitization (fields, tables, prompts) | met, with a deviation | `\A[A-Za-z0-9_-]{1,128}\Z` (normalize.py:25) applies to fields, table names and table columns. Prompt keys follow the verbatim rule instead (normalize.py:35), per the ADR-0002 amendment of 2026-09-19 (F10). The amendment supersedes the DoD wording |
| 6 | Configurable allowlist, no `== "SUCCEEDED"` | met | `SUCCEEDED` appears only as the env default (idp_client.py:286-287). There are no literal comparisons. Static test test_module_boundary.py:28. `success ⊆ terminal` is checked in the constructor (:72) |
| 7 | Monotonic clock (INV-07), budget-math unit test | met, but with **F-1** | test_idp_client.py:675,689,725,734,741. The budget runs from before submit (:589). Budget values belong to S-01.4. However, the budget math does not survive a non-finite config value: F-1 |
| 8 | Merge semantics | met | test_normalize.py:44,56,68 |
| 9 | Compliance (N23, INV-02, N5, extracted values) | met, with Minor F-2 | Sentinel PII in fields, tables and prompts never reaches caplog, stdout or stderr (test_idp_client.py:256). Secrets are absent from token and submit failure logs (:776,:805). Bearer redaction: test_transport.py:148. Injection-safe log lines: :210,:330. Code reading found three log calls, all `%s` + `sanitize_for_log`, and none carries a value. Error messages never carry body values. Chains are cut by the deferred raise. Open caveats: DEBT-25a (case-sensitive `Bearer`) and F-2 (redirects) |
| 10 | Containment via S-01.4 /harden | deferred, as the SPEC allows | The DoD wording is "before the **epic** is Done". That doesn't block the story |
| 11 | Observability: per-document timing metric; status alongside the output | met | `idp_extraction_timing` (idp_client.py:98). The value equals the clock delta (test_idp_client.py:302). `status` is embedded in `NormalizedOutput` |
| 12 | Integration tests marked and skipped in CI | met | `pytestmark = pytest.mark.integration`. The live token test passes. Submit/poll skips pending S-01.6 |
| 13 | Docs updated | met, with a deviation (F-3) | CT-01 and INV-07 statuses are updated. ADR-0002's TypedDict listing no longer matches the `NotRequired` declarations. R6 surfaced that drift, and it wasn't written back |
| 14 | Reviewed by Atchim | met | The stamp: TDD gate PASSED, structural independence sonnet≠opus. See F-4 on the stamp's accuracy |
| 15 | Zangado /qa | this report | |

## NFR-01 (S-01.2 scope). Verdicts only; this audit does not edit NFR-01.md.
- **N21 (feature): ✅ PASS.** Evidence is in DoD row 4.
- **N1 (feature): the S-01.2 leg PASSES. The row stays ⬜ PENDING (S-01.4).**
  - S-01.2's part passes: the per-document metric is emitted on a monotonic clock (test_idp_client.py:138,302,675).
  - The budget values and the /harden hung-POST check belong to S-01.4.
  - F-1 is an abort-not-hang hole in the budget mechanism. Fix it before /harden.
- **N6 / N14 (feature): ⬜ PENDING (S-01.4).**
  - N6 support: `make_idp_adapter()` raises before any network call on missing creds.
  - N14 support: poll 5xx/429 retry is DEBT-24.
- **N3 / N7 / N8 / N10 / N15 / N16 / N28 (feature):** ⬜ PENDING (S-01.4). They are outside S-01.2's scope.
- **System rows: ⚠️ WAIVED → /signoff.** S-01.2 evidence, for the record:
  - N4: the grep fallback is clean, and no `.env` value appears in the repo. gitleaks is absent (DEBT-12).
  - N5: sanitize_for_log tests, plus the sentinel test at :256.
  - N23: redaction and deferred-raise tests (:776,:805; test_transport.py:148). Caveats: DEBT-25a and F-2.
  - N19: the fixture is synthetic.
  - N9, N11, N12, N13, N17, N18, N20, N24, N25 and N27 have no S-01.2 surface.

## Gates
- **Containment:** deferred to S-01.4 /harden (HARDEN-01.md). That's acceptable for story Done because the SPEC binds it to the epic.
  - Branca must cover F-1 (non-finite budget) and F-2 (redirect token forwarding).
  - Branca must also cover DEBT-21 (mid-poll 401), DEBT-24 (5xx not retried) and the CR/LF token path.
- **LLM-Evals:** N/A (NFR-01 marker).
- **UI:** N/A.
- **Observability:** ✅ VERIFIED (S-01.2 scope). The marker is REQUIRED, and the per-document timing metric fires with the correct value. Two notes for S-01.4's N10 lines:
  - The line carries no `document_id`, because the adapter only sees the path and must not log it.
  - The metric is emitted on the success path only.
  - So the orchestrator must correlate per-document start/end and log elapsed time on abort. The UC-level `Observability: ✅ VERIFIED` line is still owed by S-01.4.
- **SCA:** `.venv/bin/pip-audit` reports "No known vulnerabilities found". The local package is skipped because it is not on PyPI. PASS.
- **Secret scan:** gitleaks and trufflehog are not installed, so the grep fallback ran (DEBT-12).
  - Token, key and secret patterns over src/, tests/ and pyproject.toml: 0 hits.
  - Real `.env` secret and ID values in tracked files, src, tests or docs: 0 hits.
  - `.env` is gitignored (.gitignore:15), and `.env.example` holds empty values.
  - The fixture tests/adapter/fixtures/raw_idp_response.json is synthetic: `exec-synthetic-0001`, INV-1001, Widget A/B, Acme Corp. It holds no real PII and no credentials. PASS.
- **Composition:**
  - CT-01 is green, including parity with the classifier's types and the real `classify()` call.
  - INV-07 ✅: the `time.time` monkeypatch raises and is never called, and a static test confirms no `time.time` reference.
  - INV-02 (adapter scope) ✅: the sentinel and secret tests pass, and code reading agrees.
  - CT-02 also runs green in the same run. PASS.
- **Cleanliness:** `check_clean.py` reports "clean — no stale artifacts." (exit 0). The user's `.swp` file and `spikes/` are known.

## Critical (blockers)
None.

## Major (should fix this iteration)
- **F-1: a non-finite poll/submit budget config makes `extract()` hang forever, or leaks a raw exception.** idp_client.py:66-67 (no validation in the constructor), :288-293 (the factory does `float()` without a range check), :95 and :206 (the deadline check is `now >= deadline`).
  - Repro: set `IDP_EXECUTION_TIMEOUT_SECONDS=nan` (or `inf`), use a fake transport that always answers `RUNNING`, and call `extract()`. It polled 1,000 times over 20,040 simulated seconds and never raised `IDPPollTimeoutError`. With NaN, `now >= nan` is always False.
  - `IDP_SUBMIT_TIMEOUT_SECONDS=inf` makes urllib raise a raw `OverflowError` that escapes the typed-error contract. `nan` is mislabelled as "request headers were rejected".
  - This is exactly the NaN/inf class that /test item 3 fixed for `expires_in` (:158). The sibling timeouts were left open. It breaks N1's "abort, not hang", and an operator typing `inf` to mean "no limit" hangs CI.
  - Fix: in `__init__`, require `math.isfinite(x) and x > 0` for the submit, poll-timeout and poll-interval values, and `>= 0` for the refresh margin. Otherwise raise `ValueError` naming the parameter. The factory should wrap `float()` failures the same way. Add a parametrised test over nan/inf/-1/0 for each value.
  - Defect tag: `escaped-atchim: yes`. It falls inside the robustness and correctness lens, and the analogous `expires_in` case was caught in the same gate.

## Minor (follow-up cards)
- **F-2: `urllib` follows 3xx redirects and forwards `Authorization: Bearer` to the new host, including an `https→http` downgrade.** transport.py:205 uses the default opener.
  - In Python 3.13.5, `HTTPRedirectHandler.redirect_request` copies every non-content header.
  - A redirect from the IDP, or anything that can inject one, would receive the token. A redirected poll GET would also accept a body from an arbitrary host.
  - The likelihood is low: the hosts are TLS-authenticated and internal. It is still an N23 gap, not just hygiene.
  - Fix: build an opener with a redirect handler that refuses redirects. The IDP APIs have no reason to redirect. Map a refused redirect to `IDPTransportError`.
  - Sibling: `platform/transport.py:85` has the same pattern with Basic auth. That's S-01.3 scope and not verified here; route it to /harden.
  - Defect tag: `escaped-atchim: yes`. It is on the security axis.
- **F-3: docs drift between the ADR-0002 `NormalizedOutput` listing and the implemented types.**
  - The ADR lists all keys as required and says "confidence is always present as a key". adapter/types.py:21,28-29,37-38 declare `confidence`/`source`/`tables`/`prompts` `NotRequired`, following Atchim's R6 mirror.
  - The runtime still always emits them (test_normalize_contract.py:47), so the observable contract holds. A mypy-typed consumer, however, is told otherwise.
  - The stale comment at test_normalize_contract.py:94-96 still says "the adapter's is always-present".
  - Fix: Soneca adds one note to DATA-MODEL-01 §2 and/or ADR-0002 saying that the static type is `NotRequired` for classifier parity and the runtime guarantees presence. Fix the stale comment.
  - Defect tag: `escaped-atchim: no`. R6 was a deliberate trade-off; only the doc write-back was missed.
- **F-4: the /test stamp's claims are inaccurate.**
  - "No `# type: ignore` in src/tests" is false. There are two, both legitimate and narrow: tests/adapter/test_idp_client.py:225 and :648.
  - The AC-table line refs are stale. For example, INV-07 is cited as `:464,473,480`, but the tests are at `:675,689,741`.
  - The code is not affected.
  - Fix: correct the stamp text on the next re-stamp.
  - Defect tag: `escaped-atchim: yes`. Atchim authored the gate claim.
- **F-5: a 401/403 on submit is mis-typed.**
  - A cached token that is revoked or expired between documents gets a 401/403 on submit, which raises `IDPSubmitError` (idp_client.py:184-185). That maps to `hard_failure`, not `auth_failure` (ADR-0004 #7 and the N10 taxonomy).
  - The HTTP status appears only in the message and is not a structured attribute, so S-01.4 cannot remap it.
  - The run still aborts fail-closed, but the abort reason is wrong.
  - Fix: fold it into DEBT-21, the adapter-side 401 handling. Raise `IDPAuthenticationError` on a 401/403 from submit, or expose `http_status` on `IDPSubmitError`.
  - Defect tag: `escaped-atchim: yes`. DEBT-21 covered only the poll path.

## Debt surfaced (for `/debt add` via Dunga)
- F-1 if it isn't fixed before S-01.4, F-2 (plus the platform sibling) and F-5 (append to DEBT-21).
- DEBT-21..25 stay open as recorded. DEBT-22 must close with S-01.6 (real CT-01 fixture), and DEBT-21 and DEBT-24 with S-01.4.

## Hand-off to Dunga
Cards to create:
- BUG: validate non-finite/non-positive IDP timeouts in `MuleSoftIDPAdapter` (F-1). Severity: Major. DoD: nan/inf/≤0 for each of the submit, poll and interval values → `ValueError` at construction; the factory wraps `float()` errors; a parametrised test; land it before S-01.4 /harden.
- BUG: refuse HTTP redirects in the adapter transport (F-2). Severity: Minor. DoD: a no-redirect opener; a 3xx → `IDPTransportError`; a test proving the Bearer token is never sent to a redirect target. Check the platform transport sibling too.
- TASK: Soneca adds a `NotRequired` vs runtime-presence note to DATA-MODEL-01 §2 / ADR-0002 and fixes the stale test comment (F-3).
- TASK: correct the TEST-S-01.2 stamp text on the next re-stamp (F-4).
- DEBT-21 append: a submit-path 401/403 must surface as auth (F-5).
- A regression-worthiness ruling is needed for F-1, F-2 and F-5.

## Done?
Under standard rigor, S-01.2 **is Done-eligible**. Every DoD line is met or legitimately deferred per the SPEC wording, and there is no Critical finding.
- Containment deferral: the SPEC binds it to the epic.
- Live/CT-01-real deferral: covered by the DoR and T-01.2.6.

F-1 is the one to fix now, not later: it's a five-line fix sitting in the Containment path Branca is about to red-team.

## Re-check after the fix round (2026-09-19, Zangado)

**Verdict:** ✅ Pass. **S-01.2 is Done.** F-1, F-2, F-3 and F-5 are closed. F-4 has a docs-only residual. F-6 is new: a live flake in S-01.3's test file, not caused by S-01.2.

### Entry gates
- Stamp: ✅ PASSED. Source `/test gap-fill (Atchim TDD gate)`. Commit 2ae8617. 68918f2 is the stamp's docs commit. PASS.
- Rigor: the spec is Risk high and the stamp is /test-sourced. PASS.
- Freshness: `git log 2ae8617..HEAD -- src tests` returns nothing. PASS.
- Branch: feat/S-01.2-idp-adapter @ 68918f2.

### Re-verification
- Unit: **459 passed, 14 skipped**, all of them env-gated integration skips.
- Live: `RUN_INTEGRATION_TESTS=1`, **472 passed, 1 skipped** on 50 of 52 full-suite runs. The skip is IDP submit/poll pending S-01.6, and no document was submitted to the live IDP. The other 2 runs had one failure each: see F-6.
- mypy: 57 files clean. ruff: clean. No deleted files since d44797f, and no new skips or xfails.
- pip-audit: no known vulnerabilities.
- Secret grep fallback over src/, tests/ and pyproject.toml, plus the real `.env` values checked against the repo: 0 hits. gitleaks is still absent (DEBT-12).
- CT-01, CT-02 and the module-boundary tests: 29 passed.
- INV-07: the monotonic tests are green. INV-02 and compliance: redirect, secret, redaction and "never appear" tests all pass (87 selected). The adapter still has exactly three log calls, all `%s` + `sanitize_for_log`.
- Config error messages are static. The only echo is the parameter name, never a secret or an extracted value.
- `check_clean.py`: exit 0.

### Per-finding status
- **F-1: CLOSED (REG-06).** `_validate_timing` (idp_client.py) plus the typed `IDPConfigurationError`.
  - My original probe now fails closed: nan, inf and -5 are rejected when the adapter is built.
  - Per env var (submit timeout, execution timeout, refresh margin), each of these gets a typed error: nan, inf, -1, `banana`, 1e400 and 3601. So does 0, except for the margin, where 0 is allowed.
  - Passing `10**400` directly raises a typed error. Its `__context__` holds only the float-conversion `OverflowError`, which carries no secret.
  - Cap-boundary tests are present.
- **F-2: CLOSED (REG-07).** transport.py uses a `_NoRedirectHandler` opener, and nothing in the adapter calls `urllib.request.urlopen` anymore.
  - A 3xx closes the response and raises a typed error through the deferred raise.
  - test_transport.py:419 uses two real local servers and asserts that the Bearer token never reaches the target, and never appears in the message, `__cause__` or `__context__`.
  - The platform sibling is tracked as DEBT-26 (S-01.3).
- **F-3: CLOSED.** ADR-0002:82 has the typing amendment. Residual nit, no card: the comment at test_normalize_contract.py:94-96 still says "the adapter's is always-present".
- **F-4: OPEN (Minor, docs only).** The stamp now admits the ignores but says they are all "15 deliberate ones in tests/adapter/test_idp_client.py". That count is right for that file, but the F-2 fix added three more in tests/adapter/test_transport.py:364,442,452. Fix the stamp text at the next re-stamp. It doesn't block Done.
- **F-5: CLOSED (routed).** Appended to DEBT-21. Atchim ruled SKIP on a regression pin because it only changes a label.

### New finding
- **F-6 (Minor, S-01.3 scope): the live test `tests/platform/test_integration_langfuse.py::test_tp37_same_run_name_different_run_ids_finding` is flaky.**
  - It failed 2 times in 52 full-suite runs with `RUN_INTEGRATION_TESTS=1`, and 0 times in 15 isolated runs.
  - The failing runs finished in normal time (about 19 s), so the failure isn't the 30 s bounded poll running out. The likely suspects are the `record_run` structural check on the same-run_name merge (DEBT-19) or cross-test interference in the full suite. No traceback was captured: the loop reproductions all passed.
  - S-01.2 didn't touch `platform/` or `tests/platform/` (the `git diff 8306d61..HEAD` stat for those paths is empty), so this isn't a fix-round regression.
  - Fix: Dunga cards it against S-01.3 / DEBT-19. Reproduce it with `--tb=long` in a loop, find the failing assertion or exception, and either deflake it or fix `record_run`.
  - Defect tag: `escaped-atchim: yes`. The test was deflaked in an Atchim round and still flakes.
