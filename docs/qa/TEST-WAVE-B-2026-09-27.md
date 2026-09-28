# TEST stamp — Wave B (FO-6 / DEBT-48, DEBT-27(a), DEBT-17, DEBT-78)

**Range:** `419fbe7..HEAD` (7 commits, HEAD `ba52371`: `f1725aa` FO-6 · `b3e7e43` DEBT-27(a)+DEBT-17 ·
`fbfafc7` DEBT-78 · `ba52371` DEBT-27 review (logs/metrics pipelines) · three `docs/state` commits)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**. `src/` is touched on the
record path (`platform/tracing.py`, `platform/langfuse_adapter.py`) and the pre-run path
(`orchestration/prerun.py`), so under `## Rigor` nothing is lightened: this `/test` stamp runs at `full`,
fresh instance, own mutation matrix.
**Verdict:** ✅ **PASS WITH FINDINGS** — every invariant the delta claims is pinned by a test that dies
under the aimed mutant (17 killed of 21 well-formed production/config mutants; the 4 survivors are 2
equivalent-marker redundancies, 1 accepted, 1 real test gap). One **Medium** finding found by probing, not
by a mutant: the OTel SDK's own `DuplicateFilter` hides a *second* identical span drop within a 20 s bucket
from the watcher, so a process that records two runs in that window is fail-open on the second. That is
outside the delta's claim (which was "the watcher hears the drop at all", previously false for every drop)
and does not reopen DEBT-27(a); it is a new row.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and never saw the implementer's
mutants. **DEBT-44 honoured.** All 27 mutants are the gate's own; each was applied after a scratchpad
copy was taken, run, and restored byte-identically (sha256 compared before/after for all 5 mutated
files; `git status --short` shows only the pre-existing `docs/state/STATE.json` change; `__pycache__`
outside `.venv`/`frontend` deleted). No live IDP/platform call; `.env` never sourced; the installed SDK
never modified; no production or test file edited.

## Gates (measured at `ba52371`)

| Gate | Result |
|---|---|
| `pytest -q tests/platform tests/orchestration tests/tooling` | **1021 passed / 13 skipped** (opt-in integration), 34.2 s |
| `mypy src tests scripts` (strict) | Success — no issues, 150 files |
| `ruff check src tests scripts` | All checks passed |
| `gitleaks detect --log-opts="419fbe7..HEAD"` | no leaks |

Suite for every mutant: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
tests/platform tests/orchestration tests/tooling`.

## Mutation matrix — 17 killed / 4 survived / 6 contract-test sensitivity probes

| # | Mutant (invariant attacked) | Result | Killed by |
|---|---|---|---|
| **DEBT-27(a)** `src/idp_regression/platform/tracing.py` | | | |
| M01 | `OTEL_SDK_EXPORT_LOGGER_NAME` back to `opentelemetry.sdk.trace.export` | killed | `test_a_real_sdk_span_drop_fails_the_record_phase` |
| **M02** | logger narrowed to `opentelemetry.sdk._shared_internal` only | **SURVIVED** | — → F-2 (the `SimpleSpanProcessor` "Exception while exporting Span." on `...trace.export` has no test; probed: shipped code catches it) |
| M03 | marker `"queue is full"` removed | survived (equivalent) | future-wording marker; nothing on the installed SDK emits it |
| M04 | marker `"queue full"` removed | survived (redundant) | the real record also matches `"dropping"` |
| M05 | marker `"dropping"` removed | survived (redundant) | the real record also matches `"queue full"` |
| M21 | `"queue full"` AND `"dropping"` removed together | killed | `test_debt27a_span_queue_full_drop_warning_raises_flush_failed` |
| M06 | marker `"dropped"` removed | survived (equivalent) | future-wording marker, as M03 |
| M07 | marker `"exception while exporting"` removed | killed | `test_a_real_sdk_batch_export_exception_fails_the_record_phase` |
| M08 | `"span" in message` requirement removed | killed | `test_a_logs_or_metrics_pipeline_record_does_not_abort_a_healthy_run[…dropping Log.]` |
| M09 | `"span"` requirement inverted (`not in`) | killed | `test_debt27a_span_queue_full_drop_warning_raises_flush_failed` |
| M10 | watcher level raised to ERROR (drops are WARNING) | killed | same |
| M11 | `otel_export_logger.removeHandler` dropped from `finally` | killed | `test_debt27a_watcher_handler_is_also_removed_after_the_call` |
| M12 | `if otel_export_watcher.failed` never raises | killed | `test_debt27a_span_queue_full_drop_warning_raises_flush_failed` |
| M19 | `_DropClassFilter` not attached (bare WARNING floor) | killed | `test_debt27a_an_unrelated_benign_warning_on_the_same_logger_does_not_raise` |
| M20 | `.lower()` removed (SDK writes `Span`, capitalised) | killed | `test_a_real_sdk_span_drop_fails_the_record_phase` |
| **FO-6** `src/idp_regression/platform/langfuse_adapter.py` | | | |
| M13 | non-object guard removed | killed | `test_get_dataset_refuses_a_schema_that_is_not_an_object[a string]` |
| M14 | absent schema refused too (`is not None` dropped) | killed | `test_get_dataset_returns_none_schema_when_dataset_has_no_schema` |
| M15 | guard moved AFTER the items fetch | killed | `test_get_dataset_refuses_a_schema_that_is_not_an_object[a string]` (`client.calls` asserts one GET) |
| **FO-6** `src/idp_regression/orchestration/prerun.py` | | | |
| M16 | consumer guard removed | killed | `test_check_schema_drift_says_when_the_schema_is_not_an_object[a string]` |
| M17 | log says `actual=absent` for a non-object | killed | same |
| **DEBT-78** `.env.example` | | | |
| M26 | `IDP_POLL_INTERVAL_SECONDS` line removed | killed | `test_every_knob_the_adapter_reads_is_documented_in_env_example` |
| **DEBT-17** `tests/platform/test_sdk_contract.py` — would it fail on a REAL SDK change? (the assertion is aimed at what a renamed SDK would look like) | | | |
| M22 | `run_experiment` expected to take a keyword it does not (`experiment_name`) | fails (sensitive) | `test_run_experiment_still_takes_every_keyword_record_experiment_passes` |
| M23 | duck-typing string aimed at `hasattr(item, "datasetId")` | fails (sensitive) | `test_the_sdk_still_links_items_to_a_dataset_run_by_duck_typing` |
| M24 | `ExperimentItemResult` expected to take `run_id` | fails (sensitive) | `test_the_result_still_exposes_what_record_experiment_reads` |
| M25 | both `hasattr` assertions weakened to `assert source` | survived (expected) | a contract test is its own oracle; nothing else guards it — F-3 |

Verified against the installed packages directly: `opentelemetry-sdk 1.44.0` logs `"Queue full, dropping
%s."` (WARNING) and `"Exception while exporting %s."` (ERROR) on `opentelemetry.sdk._shared_internal`, and
`SimpleSpanProcessor` logs `"Exception while exporting Span."` on `opentelemetry.sdk.trace.export`; both
are children of the watched `opentelemetry.sdk`. `langfuse 4.15.4`'s `run_experiment` takes exactly the
six keywords, and `hasattr(item, "dataset_id")` / `hasattr(item, "id")` occur at `_client/client.py`
lines 2982–2983 and 3018.

## Findings

- **F-1 (Medium, new — not a reopen)** — `src/idp_regression/platform/tracing.py::record_experiment` ×
  `opentelemetry.sdk._shared_internal._logger`. The SDK attaches its own `DuplicateFilter` to that logger,
  keyed on `(module, level, msg, time // 20)`. Filters on the originating logger run before propagation,
  so the **second** `"Queue full, dropping Span."` (or `"Exception while exporting Span."`) in the same
  20-second bucket is discarded before the parent watcher ever sees it. Probed (scratch script, not
  committed): two consecutive `record_experiment` calls each driving a real `BatchSpanProcessor` drop —
  call A raises `FlushFailedError`, **call B returns GREEN**. Exposure: any process that records more than
  one run within 20 s — `watch.py --auto-run --max-runs-per-tick > 1` is the reachable one; the one-shot
  CLI is unaffected (its first drop is always the first record). Also a latent flake: the two real-SDK
  tests pass only because each is the first such record in its bucket (a `pytest --count 2` would fail
  the second). Suggested fix: pin the behaviour with a test first, then either reset the SDK filter's
  `last_log` at the start of the watcher window (private state — pair it with a contract test like
  DEBT-17's) or insert the watcher as a filter at index 0 on the `_shared_internal` logger. Record in
  `DEBT.md`; not blocking this delta, which turned an always-invisible drop into a visible one.
- **F-2 (Low)** — `tracing.py::OTEL_SDK_EXPORT_LOGGER_NAME`. M02 survives: no test covers the
  `SimpleSpanProcessor` path (`opentelemetry.sdk.trace.export`), so narrowing the watcher to
  `_shared_internal` would pass. The shipped parent-logger choice does catch it (probed). Fix: one test
  driving a `SimpleSpanProcessor(_RaisingExporter())` through `record_experiment` → `FlushFailedError`.
- **F-3 (Low, accepted)** — `tests/platform/test_sdk_contract.py`. M25: weakening the file's own
  assertions is caught by nothing else; that is the nature of a contract test. M22–M24 show each
  assertion is load-bearing against the real package. No action.
- **F-4 (Low, equivalent survivors)** — M03/M06 (`"queue is full"`, `"dropped"`) are future-wording
  markers no installed code emits; M04/M05 are each redundant with the other on the real record (M21
  kills the pair). No action; noted so the next gate does not re-derive it.

## Debt to record (Dunga → `/debt add`)
F-1 as a **Medium** row in DEBT-27's family (title: "SDK `DuplicateFilter` hides a second span drop in the
same 20 s bucket — second `record_experiment` in one process is fail-open"); F-2 as a Low test-gap row on
DEBT-27(a). F-3/F-4 need no row.

— Atchim (fresh instance, Fable 5.1) · 2026-09-27

---

## Addendum — findings closed (implementer, same day)

- **F-1 (Medium, fail-open) — FIXED in `4823e6e`.** `tracing._ObservingFilter` is inserted first on every
  `opentelemetry.sdk.*` logger carrying its own filters for the duration of each `record_experiment`, so it
  sees a drop before the SDK's `DuplicateFilter` can suppress the repeat. New test: two consecutive runs that
  each drop a span both fail. Mutants killed: no observer, observer appended last, observer left behind, and
  an observer that suppresses (pinned by a separate test that the SDK's own line still reaches the log).
- **F-2 (Low) — CLOSED.** `test_a_simple_span_processor_export_exception_fails_the_record_phase` covers the
  `SimpleSpanProcessor` path on `opentelemetry.sdk.trace.export`.
- **F-3/F-4** stay accepted as recorded.
- **Also found while closing these:** an intermittent "Logging error ... I/O operation on closed file"
  after the suite. A console job thread logged through a handler that an earlier test's
  `configure_logging()` had bound to a closed pytest capture stream. It is fixed suite-wide by an autouse
  logger-isolation fixture (`e16223d`); 6 of 6 full runs are clean, against 2 of 5 before.
