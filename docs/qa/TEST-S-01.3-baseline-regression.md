# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ❌ FAILED (re-stamp #5 — Wave C, DEBT-46/72/77)
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Files:** `src/idp_regression/platform/__init__.py`, `errors.py`, `hashing.py`, `insights.py`, `langfuse_adapter.py`, `schema_provisioning.py`, `scoring.py`, `tracing.py`, `transport.py`, `types.py`, `schema/__init__.py`, `schema/golden_schema_v1.json` — the full tracked set (12 files; `git ls-files` = on-disk = 12, no untracked file under `platform/`).
**Sequence:** re-gate of an already-landed delta; ordering not re-proved here (see History for the gates that proved it per commit). Test order for the three Required findings below is owed by the fix round (P7: red first).
**Date:** 2026-09-28
**Commit (reviewed code):** `fc3fba95ecd32986b51ad661a8b2282c32e5e000` (HEAD, branch `feat/S-01.2-idp-adapter`). Prior pin `1428520`; delta = 13 commits on `src/idp_regression/platform/` (`49c5c19`…`4823e6e`).
**Author:** alex@divinocosta.com.br (solo)
**Atchim TDD gate:** ❌ FAILED — three Required findings (F-1 untested leak on the verdict-publish path, F-2 untyped escape from `record_run`, F-3 an invariant the caller named first that only the live suite pins). Mechanical floor green.
**Independence (R3):** ✅ this gate ran on a **new Atchim instance on Fable 5.1**; the post-stamp implementer is **Opus 5.5** (different model). This instance issued **no** earlier verdict on any commit in this delta and did not run the previous gate on this story. Stamp valid under R3.
**Static:** ✅ `pytest -q`: **1908 passed / 15 skipped** (43 s) · `mypy src tests scripts` strict: clean, 151 files · `ruff check src tests scripts`: clean · `gitleaks detect --no-git -s src`: no leaks · SDK-privates re-probe (`test_make_platform_dispatches_on_platform_env`, `…raises_when_langfuse_base_url_disagrees_with_langfuse_host`, `…constructs_the_sdk_client_with_base_url_equal_to_host`) + `test_sdk_contract.py` + CT-05 `test_golden_schema_contract.py`: **35 passed**; minified schema **2,450 chars** (< 10,000). **Live Langfuse suite (`test_integration_langfuse.py`, 12 tests) and live IDP: `not covered`** — never sourced `.env` (hard limit).
**Rigor:** `prototype`, but `platform/scoring.py::build_score_inputs`'s gate value is on the verdict path → run at **full** (M01/M03/M06/M22 below). Nothing was `re-gate skipped`.

## Freshness (P5, DEBT-77)

`git diff --stat 1428520..HEAD -- <each of the 12 files above>` → 7 changed: `insights.py` +320 (new), `langfuse_adapter.py` +307/−…, `schema/golden_schema_v1.json` +202, `scoring.py` +140, `tracing.py` +281, `transport.py` 6, `types.py` 30 (1,117 insertions / 169 deletions); `__init__.py`, `errors.py`, `hashing.py`, `schema_provisioning.py`, `schema/__init__.py` unchanged. Added-files check: `git diff --diff-filter=A --name-only 1428520..HEAD -- src/idp_regression/platform/` → `insights.py` only. Falsifiable next time: `git diff --stat fc3fba9 HEAD -- <the 12 paths>` must be empty and the added-files check must return nothing.

## Findings

| ID | Sev | File · symbol | Scenario |
|---|---|---|---|
| **F-1** | **Required** | `platform/scoring.py` · `_gate_comment` (new, `build_score_inputs` gate score) | The `gate` score's comment is `FAIL on: <verdict-map keys>`; prompt entries are keyed by the **raw Curator-authored prompt key** (`gate.py:653`), so a critical prompt failure publishes e.g. `FAIL on: What is the supplier's bank IBAN on this invoice?` — in **every** `--platform-values` mode. Under `verdicts-only` this breaks the promise both `types.py` and `scoring._comment` state ("reproduces option B's payload exactly") and undoes DEBT-13/REG-05, which hash the prompt score *name* precisely so the raw prompt never leaves the app. Reproduced by probe. **Untested**: mutant M22 (always `None`) survives all 1,908 tests; `test_score_comment_is_always_none` only exercises `gate="PASS"`. Fix: name prompts by `prompt_score_name(key)` in the comment (pass the golden's prompt keys, or detect them from the golden as the field/prompt loops already do), and land the test red first. |
| **F-2** | **Required** | `platform/langfuse_adapter.py` · `_expected_output` / `_leaf_values`, `_require_record_shape` | `DocumentRecord.verdicts` (`NotRequired[VerdictMap \| None]`) is a declared field with **no shape guard**: R2's two derivations skip it because it is neither `str` nor `NotRequired[str]` — "hand-written with one automated column". `_expected_output` is evaluated **outside** the total `task` (the `ExperimentItem` list comprehension), so a malformed map escapes `record_run` **raw**: probe with `{"total": "not-a-dict"}` → `TypeError`, `{"t": {"verdict":"detail","rows":[{}]}}` → `KeyError`, `["list"]` → `AttributeError` (3 of 4 shapes; only the well-formed-but-odd one reached the typed path). HARDEN-01 GAP-1 class (an untyped escape `run_eval`'s contract says is impossible), on a `Containment: REQUIRED` UC. Fix: validate `verdicts` in `_require_record_shape` (None or dict; every entry a dict with `"verdict"`; a `detail` entry's `rows` a list of dicts each with `"verdict"`) and raise `ExperimentRecordFailedError` naming `document_id` only; one parametrized test per shape, seen red (P7). |
| **F-3** | **Required** | `platform/langfuse_adapter.py` · `_write_scores` payload (unchanged lines) | The first invariant this gate was asked to draw mutants from — *every score has exactly one target plus `dataType`* — is **not pinned** for the per-document write: M04b (drop `"dataType"`) and M05b (add a second target `sessionId`) survive the entire suite; both would be a 400 on the live API ("Langfuse facts that bite"), i.e. every document's score write fails after the extractions were paid for. `mark_run_status` IS pinned (M25 killed at `test_langfuse_adapter.py:550`); the per-document path is the one that carries the verdicts. Fix: in `test_score_contract.py`/`test_langfuse_adapter.py`, assert over every POSTed `/api/public/scores` body that `keys ∩ {traceId, sessionId, datasetRunId} == {traceId}` and `dataType == "CATEGORICAL"`. Live suite is the only current guard and is `not covered` here. |
| F-4 | Low / debt | `platform/insights.py` (module level), `tests/platform/test_module_boundary.py` | N24 ("Langfuse SDK import confined to `make_platform()`") is not pinned **inside** the package: M26 (`import langfuse` at `insights.py` top level) survives; the boundary test guards vendor references outside `platform/` only. → `/debt add`. |
| F-5 | Low / debt | `platform/insights.py` · `capabilities` | `f"unreachable: {exc}"` and `body["message"][:200]` render transport/platform error text into the console payload. `TransportError` text is `redact()`ed (checked, `transport.py:132/144`), so no secret; the platform's own error message reaches the loopback UI unredacted, unlike the write side's `_body_snippet_for_error`. → `/debt add`. |
| F-6 | Low / debt | `platform/insights.py` · `dataset_items` | Docstring says "from the PAGINATED items endpoint" but reads one page of ≤ `limit`; `total_items` is reported so truncation is visible. Read seam, no gate impact. → `/debt add`. |

## Mutation matrix (R1: source = invariant, not diff)

| # | File · line | Mutant | Source invariant | Diff-touched? | Result |
|---|---|---|---|---|---|
| M01 | scoring.py `build_score_inputs` | remove `_VALID_GATES` guard | FO-4 gate literal | no | KILLED |
| M02 | scoring.py `_worst_row_verdict` | fallback `"wrong_value"`→`"match"` | fail-closed fallback | yes | SURVIVED — **equivalent** under production order: `comparison.gate()` raises `MalformedActualError` (FO-5) on any off-set row verdict before `build_score_inputs` runs (probe confirmed) |
| M03 | scoring.py `_comment` | ignore `include_values` | verdicts-only leaks no value | yes | KILLED |
| M04b | langfuse_adapter.py `_write_scores` | drop `"dataType"` | one target + dataType | **no** | **SURVIVED → F-3** |
| M05b | langfuse_adapter.py `_write_scores` | add `"sessionId"` (2 targets) | exactly one target | **no** | **SURVIVED → F-3** |
| M06 | scoring.py `score_id` | drop `run_id` from key | idempotent `uuid5`, no cross-run overwrite | no | KILLED |
| M07 | langfuse_adapter.py `get_dataset` | remove FO-6 guard | non-object schema refused | yes | KILLED |
| M08 | langfuse_adapter.py `_fetch_all_dataset_items` | first page only | paginated `/dataset-items` | no | KILLED |
| M09 | tracing.py `_ObservingFilter.filter` | never sets `failed` | drop seen inside DuplicateFilter bucket | yes | KILLED (real `BatchSpanProcessor`, two runs) |
| M10 | tracing.py `_DropClassFilter` | drop `"span" in message` | logs/metrics pipeline never aborts a run | yes | KILLED |
| M11 | tracing.py `record_experiment` | `insert(0)`→`append` (after DuplicateFilter) | repeat drop within 20 s seen | yes | KILLED (P3, real SDK) |
| M12 | tracing.py | logger name back to `opentelemetry.sdk.trace.export` | drop detected on installed SDK | yes | KILLED |
| M13 | tracing.py | ignore `otel_export_watcher.failed` | span drop → `FlushFailedError` | no | KILLED |
| M14 | tracing.py structural check | skip empty `trace_id` | ADR-0005 #9 structural check | no | KILLED |
| M15 | langfuse_adapter.py `record_run` | ignore `task_failed` | record-after, no scores on a failed task | no | KILLED |
| M16 | langfuse_adapter.py `_record_deadline_seconds_from_env` | accept `0` | deadline fails closed | yes | KILLED |
| M17 | insights.py `capabilities` | `is_gate_source: True` | dashboard never a gate source | yes | KILLED |
| M18 | insights.py `dataset_items` | read `/v2/datasets/{name}` | items from `/dataset-items` | yes | KILLED |
| M19 | insights.py `insights_from_env` | build reader when unconfigured | dashboard can't stop a run | yes | KILLED |
| M20 | langfuse_adapter.py `_require_record_shape` | skip `_SCORE_OPTIONAL_STR_FIELDS` loop | DEBT-53 optional-str type check | yes | SURVIVED — **equivalent today** (set is empty; latent, as the prior stamp's M4 class) |
| M21 | langfuse_adapter.py `_str_annotated_field_names` | drop the `NotRequired` skip | DEBT-53 required-str derivation | yes | SURVIVED — **equivalent**: with `include_extras=True`, `NotRequired[str]` already fails `hint is str` (probe: `{'a': True, 'b': False, 'c': False}`); the skip is redundant, `include_extras` is the fix |
| M22 | scoring.py `_gate_comment` | always `None` | gate comment names failing fields | yes | **SURVIVED → F-1 (untested)** |
| M24 | langfuse_adapter.py `_leaf_values` | drop `actual` | reversal: actual reaches span | yes | KILLED |
| M25 | langfuse_adapter.py `mark_run_status` | drop `"dataType"` | one target + dataType (run level) | no | KILLED |
| M26 | insights.py module level | `import langfuse` | N24 SDK import confined | yes | **SURVIVED → F-4** |

**26 mutants: 19 killed, 3 equivalent (M02, M20, M21), 4 surviving non-equivalent (M04b, M05b, M22, M26).** 9 mutants sat on lines the delta did not change (R1). Every mutant restored by `cp` and verified byte-identical by `shasum` against the scratchpad baseline; `PYTHONDONTWRITEBYTECODE=1` throughout; no stray `__pycache__` outside `.venv`/`frontend`.

## Invariants checked (beyond the matrix)

- **Record-after (ADR-0005 #9):** `record_run` writes nothing before `record_experiment` returns; scores written after, gated on `task_failed` (M15). ✅
- **Silently empty golden set:** `totalPages: 0` returns `[]` from `get_dataset`; refusal is the orchestrator's N28 check (out of this module, unchanged). ✅
- **INV-02:** `_require_record_shape`, `_write_scores`, `mark_run_status` name fields/ids only; `transport` redacts. ✅ except F-1 (prompt key in the gate comment).
- **Schema drift refused:** FO-6 non-object schema refused (M07); `check_schema_drift` untouched. ✅ CT-05: Ajv-strict walk + 2,450 chars. ✅
- **Span drop, incl. repeat in the 20 s bucket:** M09/M11/M12 against the real `BatchSpanProcessor` + `SimpleSpanProcessor`. ✅
- **Dashboard read can never stop a run:** `PlatformInsights` is a separate Protocol, nothing under `orchestration/` imports it (grep), `insights_from_env` returns `None` unconfigured (M19). ✅

## Debt surfaced (for Dunga → `/debt add`)
- F-4 N24 in-package pin; F-5 platform error text rendered unredacted by the read seam; F-6 `dataset_items` single page.
- The prior stamp's carried debt is still open: two copies of the type-derivation helpers (`tests/platform/_type_pins.py` vs production), and the unwritten `platform/` error-type boundary rule.

## History
- /test gap-fill (Atchim TDD gate) on 2026-09-21 (re-stamp #4, re-gated 2026-09-22 by a fresh instance) at `1428520`: ✅ PASSED — 10 mutants planted / 10 killed, live Langfuse suite 12 passed, FO-9 verified on 6 accept / 24 reject shapes, DEBT-18 option B re-probed live; fix round of R-1/R-2 recorded as `re-gate skipped: prototype profile`. Superseded by this re-stamp after 13 platform commits (`49c5c19`…`4823e6e`) staled it (DEBT-46).
- /test gap-fill (Atchim TDD gate) on 2026-09-21 at `7a45037` (reviewed) / stamp commit `2cc4dde`: ✅ PASSED. Superseded after **FU-01.3-D** (`fcadd58`, `ad333fc`, `617dd8c`, `3708b3d`). That gate was reviewer-independent; the #4 re-stamp's fix round was not.
- /test gap-fill (Atchim TDD gate) on 2026-09-20 at bb58be0397e5f4855e2d455b8150064a775bad21: ✅ PASSED. Superseded after FU-01.3-B (`353549d`, `3ad1388`, `7a45037`). Gate run on Fable 5.1 after a same-model (Opus) review was stopped mid-flight.
- /test gap-fill (Atchim TDD gate) PASSED on 2026-09-19 at 71e5e6acc81f6b0bd8bd224f6d0997c9f6ccce7b: ✅ PASSED. Superseded after FU-01.3-A.
- /test gap-fill (Atchim TDD gate) PASSED at ba6a9056969136ef488eaeeca37b86e28483ac73, before the QA fix round. Superseded after QA S-01.3 ⚠️ (F-1..F-5) was fixed and re-gated.
- /implement (Atchim TDD gate) PASSED at 825201a07fa8b3de3f31f326eaadbc1dc23a558b. Superseded by the /test stamp required for Risk: high.
- /implement (Atchim TDD gate) PASSED at 02cff1be9a607c91b3dc267a601f5003614a210a. Superseded after the DEBT-18 option-B change.
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round-1 findings R1–R7 (R1 Critical: `get_dataset` read a `body["items"]` key the live 4.38.0 API does not return), then round-2/3 test gaps; all closed.
