# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-22
**Commit:** cfd2bd7e2bcb3f4a0aaea679b88ec64be7f6bb39
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** PASSED. Run by a fresh instance with no prior verdict on S-01.4, as **DEBT-44** requires. `Risk: high` ⇒ a /test stamp is required regardless of the `prototype` profile (profile ≠ risk level).
**Independence:** ✅ structural (different models): Dengoso (sonnet) reviewed and gated by Atchim (opus).
**Static:** ✅ clean: `mypy src` (31 files) + `ruff check src tests`; `gitleaks` clean over the batch range. `mypy tests` is ungated and reports pre-existing errors — DEBT-51.
**Files:** src/idp_regression/adapter/errors.py, src/idp_regression/adapter/idp_client.py, src/idp_regression/adapter/normalize.py, src/idp_regression/adapter/token_cache.py, src/idp_regression/adapter/transport.py, src/idp_regression/adapter/types.py, src/idp_regression/classifier/gate.py, src/idp_regression/orchestration/bootstrap.py, src/idp_regression/orchestration/cli.py, src/idp_regression/orchestration/dotenv_support.py, src/idp_regression/orchestration/errors.py, src/idp_regression/orchestration/facade.py, src/idp_regression/orchestration/log_sanitize.py, src/idp_regression/orchestration/prerun.py, src/idp_regression/orchestration/run_naming.py, src/idp_regression/platform/__init__.py, src/idp_regression/platform/errors.py, src/idp_regression/platform/langfuse_adapter.py, src/idp_regression/platform/scoring.py, src/idp_regression/platform/tracing.py, src/idp_regression/platform/transport.py, src/idp_regression/platform/types.py, tests/adapter/__init__.py, tests/adapter/fixtures/raw_idp_response.json, tests/adapter/test_errors.py, tests/adapter/test_idp_client.py, tests/adapter/test_integration_idp.py, tests/adapter/test_make_idp_adapter.py, tests/adapter/test_module_boundary.py, tests/adapter/test_normalize.py, tests/adapter/test_normalize_contract.py, tests/adapter/test_token_cache.py, tests/adapter/test_transport.py, tests/classifier/test_gate.py, tests/classifier/test_validation.py, tests/orchestration/__init__.py, tests/orchestration/test_bootstrap.py, tests/orchestration/test_cli.py, tests/orchestration/test_dotenv_support.py, tests/orchestration/test_e2e_orchestration.py, tests/orchestration/test_exit_code_contract.py, tests/orchestration/test_facade.py, tests/orchestration/test_integration_e2e.py, tests/orchestration/test_log_sanitize.py, tests/orchestration/test_prerun.py, tests/orchestration/test_run_naming.py, tests/orchestration/test_static_orchestration_checks.py, tests/platform/_tp45_subprocess_scenario.py, tests/platform/_type_pins.py, tests/platform/test_integration_langfuse.py, tests/platform/test_inv01_payload.py, tests/platform/test_langfuse_adapter.py, tests/platform/test_module_boundary.py, tests/platform/test_record_run_preconditions.py, tests/platform/test_schema_provisioning.py, tests/platform/test_scoring.py, tests/platform/test_tracing.py, tests/platform/test_transport.py, tests/tooling/__init__.py, tests/tooling/test_secrets_gate.py

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Unit + contract (default run) | 857 | 0 |
| Skipped | 15 | — (live integration; incl. the TP-01 placeholder) |

## How this story was verified

Four independent passes, each finding what the previous missed:
1. **Code review gates** (fresh instance each time, DEBT-44): found the `document_id` type/NUL escape, then C-1 (the catch-all on the wrong `try`), then the `frame_location` traversal leak that the previous fix had introduced.
2. **`/harden`** (Branca, 4 rounds, ~180 probes): found GAP-1 (13 reproductions of raw escapes), GAP-2 (unguarded SDK calls), then four more pre-run seams the C-1 fix missed, then GAP-6 — 8 reproduced leaks of golden content and secrets into logs — and GAP-8.
3. **Independent coverage audit** (fresh agent, no implementation context): found 8 gaps, headline being that the **mid-run 401 refresh-and-retry block could be deleted with zero test failures**.
4. **This `/test` gate**: re-applied the killer mutation and 5 more, 6/6 killed.

## AC coverage (gap-fill batch)

| Row | Test |
|---|---|
| DEBT-21 / ADR-0004 #7 — 401 refresh-then-retry, one `invalidate()` | `test_idp_client.py::test_poll_401_or_403_refresh_then_retry_succeeds_and_invalidates_once` |
| `MalformedActualError` reachable raise site | `test_facade.py::test_run_eval_aborts_malformed_actual_from_classify` |
| TP-19 / INV-08 — no platform read beyond `get_dataset` (forbidden set derived from the Protocol) | `test_static_orchestration_checks.py::test_orchestration_issues_no_platform_read_beyond_get_dataset` + 2 derivation pins |
| TP-46 — no `jsonschema` in `orchestration/` (N28 ≡ N22) | `::test_orchestration_never_imports_jsonschema` + 3-form pin |
| N6 — fail-closed over all 4 IDP vars | `test_facade.py::test_run_eval_returns_nonzero_and_names_the_missing_idp_var[×4]` |
| `item_id` shape guard | `::test_run_eval_never_escapes_on_a_malformed_dataset_shape[6/7/8]` |
| Abort-reason raise-site totality (AST, comment-only mention rejected) | `test_exit_code_contract.py::test_every_abort_reason_has_a_real_raise_site` |
| N3 — success-path latency | `test_e2e_orchestration.py::test_all_gates_pass_success_path_returns_promptly` |

Earlier batches' rows (the pre-run chain, the loop, containment, telemetry, `--dataset`/4-field `RunMetadata`, CT-04, the e2e harness) are covered by `test_facade.py`, `test_prerun.py`, `test_cli.py`, `test_exit_code_contract.py`, `test_e2e_orchestration.py`, `test_log_sanitize.py` and `test_tracing.py`, each gated in its own review round.

## Deferred / carried

- **TP-01 live orchestration e2e** — `test_integration_e2e.py`, skipped with an honest reason: needs a real published IDP action id + version (**S-01.6**).
- **In-loop `MalformedGoldenError`** — provably unreachable: N28 runs `validate_golden_structure`, the `is`-identical alias of the `_validate_golden` that `classify()` calls, over every item before the loop. The ordering premise is itself pinned by `test_prerun.py::test_validate_golden_set_runs_before_any_idp_call`, so the argument is test-backed. Documented in place.
- **T-01.4.10 leg 1 / DEBT-45** — carried; `/qa` judges it against Soneca's reconciled DoD wording.
- **Containment** — discharged separately at `b473ce4` (HARDEN-01 §10).

## Rigor — `prototype` profile

Correctness and Security ran in full, plus the mechanical floor (tests, mypy, ruff, secret scan) and a mutation battery in every round.
**skipped: prototype profile** — Readability, Architecture, Performance, design-pattern conformance, regression-worthiness PIN, doubt-driven adversarial pass. Re-runnable at `--rigor=full`.

## History
- Initial stamp (no prior stamp for S-01.4)
