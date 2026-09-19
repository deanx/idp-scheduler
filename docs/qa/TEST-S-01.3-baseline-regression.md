# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ❌ INVALIDATED
**Source:** /implement (Atchim REQUEST CHANGES)
**Date:** 2026-09-19
**Commit:** 4dcb540b135b4732c00189d6674d14da8f5fb535
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** REQUEST CHANGES — see findings below
**Independence:** ✅ structural (Dengoso sonnet, reviewed by Atchim opus)
**Static:** ✅ clean (mypy strict, 37 files; ruff); pip-audit clean

## Findings (Atchim, 2026-09-19)

**Required:**
- **R1 (Critical)** `langfuse_adapter.py:68`: `get_dataset` reads `body["items"]`, but live `GET /api/public/v2/datasets/{name}` on 4.38.0 returns no `items` key. The result is 0 items: an empty golden set that passes without checking anything, and `hash_dataset` hashes nothing. The unit mock (`test_langfuse_adapter.py:53`) invents `items`. Fix: fetch from the paginated `/api/public/dataset-items?datasetName=` and add a live test that asserts the item count. **PIN as a regression.**
- **R2** Tests that don't assert anything:
  - `test_integration_langfuse.py:105` `_score_visible` only checks HTTP 200;
  - the N26 test (`:113`) has no assertion and uses `uuid4` instead of `score_id()`;
  - TP-34 lacks "stored value unchanged" and "400 body not logged";
  - TP-33's tables-block write is untested.
- **R3** TP-45 is missing: there is no in-memory span-exporter test. `run_experiment` writes `EXPECTED_OUTPUT`, `input`, `output` and `str(exception)` into span attributes, so INV-01 is unverified on spans.
- **R4** TP-44 is missing: no captured-log test on the REST/provisioning path, and `transport.redact()` is never called.
- **R5** `tracing.py:72` `flush_or_raise` gives false negatives:
  - (a) `run_experiment` calls `self.flush()` internally, outside the watcher;
  - (b) batches exported by the background thread during the run are not watched;
  - (c) dataset-run-item failures are logged on the `langfuse` logger, which is not watched.
- **R6** Architecture conflicts with ADR-0004:
  - `run_experiment` wraps items in `asyncio.gather(return_exceptions=True)`, which logs and swallows task exceptions, so INV-06 abort and INV-08 gate-before-write can't be expressed;
  - it needs SDK `DatasetItemClient` objects, which either leaks past N24 or forces a second fetch against INV-04;
  - `run_dataset_experiment` is not on the `PlatformAdapter` Protocol.

  → Soneca decision.
- **R7** `transport.py:48`: `urlopen` has no timeout, and `URLError` escapes untyped.

**Suggestions:**
- URL-encode the dataset name.
- Turn a `KeyError` on a malformed item into `DatasetFetchFailedError`.
- The never-drop guard in `schema_provisioning.py:28` is an `assert`; raise instead.
- `scoring.py:121`: a missing verdict silently becomes `"missing"`; raise instead.
- `scoring.py:130`: the comment sends `actual` values to the platform; confirm that's in scope.
- `verdicts: object` is weakly typed.

**OK:**
- Pinned NAMESPACE; deterministic uuid5 score and trace ids; UTF-8 prompt hash.
- CATEGORICAL `dataType`.
- DEBT-15 acceptable; DEBT-14 faithful to CT-03.
- `langfuse==4.15.4` confined to the platform module.
- Classifier unchanged.

## History
- Initial stamp (no prior stamp for S-01.3)
