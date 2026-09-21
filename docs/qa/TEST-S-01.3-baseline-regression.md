# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-21 (re-stamp #4 — the FO sweep + DEBT-49)
**Commit (reviewed code):** `ab0734d`. ⚠️ `/qa` freshness: corroboration returns the stamp commit itself because `docs/qa/` and `docs/state/` are in the `Files:` set — expected, not stale. Falsifiable check: `git diff --stat ab0734d HEAD -- src/` must be empty.
**Author:** alex@divinocosta.com.br
**Scope:** the fail-open sweep and its fallout — `afc3331` (FO-4), `139b965` (FO-2 + FO-7 third leg), `8455a39` (DEBT-49 + FO-1 + FO-3 + FO-8), plus two Required fix rounds.

**Atchim TDD gate:** PASSED — with an explicit caveat recorded rather than smoothed over. A fresh instance (DEBT-44) gated the four FO commits and returned **REQUEST CHANGES** on two findings; both were fixed. **The fix round was NOT re-gated by a third instance.** The closure was verified by the orchestrator: R-1 by grep (the falsified sentence is gone), R-2 by independently reproducing the mutant analysis. **At `prototype` rigor a re-gate of a comment reword plus an honestly-described pin is disproportionate — recorded here as `re-gate skipped: prototype profile`, not silently dropped.**

**Independence:** ✅ structural at every hop — implementer Dengoso (**sonnet**), gates by Atchim (**opus**) on **three distinct instances** across this story, none gating a diff it had reviewed. The gate that found R-1/R-2 explicitly disqualified itself from the fix round after authoring the fix direction.

**Static:** ✅ `uv run mypy` clean (23 files) · `ruff check .` clean · `pip-audit` clean · **560 passed / 14 skipped** · `tests/platform` stable across `PYTHONHASHSEED` ∈ {0,1,2,7}. ⚠️ **Rigor is now `prototype`** (`ab0734d`) — the mechanical floor still holds, and `Risk level: high` still requires this `/test` stamp because `/qa` reads risk from the SPEC header, not the profile.

## What this delta fixed — five fail-open defects

| ID | Defect | Why it mattered |
|---|---|---|
| **FO-2** | `_require_record_shape` omitted `ScoreInput.value` | A non-`str` value was written to the span **and** POSTed as the score |
| **FO-4** | `build_score_inputs` never validated its `gate` Literal | Any string became the published **per-document CI verdict** |
| **FO-7** (3rd leg) | `golden` had no `isinstance(dict)` at `get_dataset` | CT-05 guards `expectedOutput` **on write, never on read** |
| **FO-1** | `RunMetadata`'s three `str` fields unvalidated | A run with `action_version=None` was **indistinguishable from a good one** (INV-04) |
| **FO-3 / FO-8** | `RunStatus` Literal unenforced; `dataset_id` unchecked | An off-allowlist marker reads as valid forever; items carry no dataset linkage while the run reports clean |

**DEBT-49 — the instrument itself was faulty.** `ScoreInput.__required_keys__` reports `comment` (declared `NotRequired[str | None]`) as **required**, with `__optional_keys__` empty — `NotRequired` is invisible under `from __future__ import annotations`. **DEBT-43's rule, which this project applied everywhere, half-rested on it.** Fixed via `get_type_hints(td, include_extras=True)` + a `NotRequired` check, validated the right way round: a `NotRequired` field was **added** to `DocumentRecord` and the guard confirmed **not** to break, with the old derivation printed as a negative control showing it *would* have regressed.

⚠️ **The trap inside that fix, avoided:** `DocumentRecord.scores` is `list[ScoreInput]`, not a `str` field. Collapsing presence into the `str`-only derivation would have **silently stopped checking its presence**. Presence and value-type are separate derivations — verified: `DocumentRecord` presence is `['document_id','item_id','scores']` while `str`-value is `['document_id','item_id']`.

## Two prescribed fixes that did not work, and were caught before shipping

This is the most useful thing in this cycle and it happened **twice in two rounds**:

1. **The reviewer's prescribed assertion for Required A was insufficient.** `_DOCUMENT_RECORD_REQUIRED_FIELDS == _required_fields(DocumentRecord)` compares two values that are **both invariant** under the mutants it was meant to kill, because `DocumentRecord` has zero `NotRequired` fields. The implementer distrusted it, proved it, and added a **probe-level** pin instead. The gate verified this independently: *"The prescribed fix would have shipped a 'pinned' mechanism that was still un-pinned."*
2. **My prescribed `RunMetadata` drift pin also cannot work.** Verified: `RunMetadata` has three fields, all `str`, all required, so `_required_field_names`, `__required_keys__`, `_str_annotated_field_names` and a hand-written literal are **byte-identical**. There is no divergence for a value-comparison to key on. The pin is **kept** for its narrower real value (catching the constant going stale against a future type change) and its docstring **says so** instead of claiming a disproven kill.

**Standing conclusion: prescriptions from review are hypotheses, not instructions.**

## Overclaims — five found in this story, all now corrected

The fifth was found by this gate: *"a fourth escape is structurally unavailable"* — **the exact sentence formally falsified by re-audit #3 F-1** and recorded as falsified in four registers — was **still live in test source**. The registers were corrected; the code comment was not. Structurally identical to overclaim #4 (the "scalar" wording recurring in production source after its own correction). Now scoped to what the mechanism proves.

## Accepted honest negatives — do NOT let these be claimed as covered
- Replacing a type-derived set with a today-equivalent literal survives, for `VerdictLiteral`, `GateLiteral`, `ScoreInput` and `DatasetItem`. Both drift-pin docstrings say so in their own words.
- **M4** (`_RUN_METADATA_REQUIRED_FIELDS = _str_annotated_field_names(RunMetadata)`) is equivalent today and latent-only. Nothing cheap kills it. Not chased with AST or reload-provenance inspection — heavier than anything else in the file and out of scope at `prototype` rigor.
- **M17** — the presence loop body bypassing the constant. Benign: dropping `scores` still fails closed via the downstream guard; only the message differs.

## Debt carried, not closed
- **Two independent copies** of the type-derivation helpers (`tests/platform/_type_pins.py` vs `langfuse_adapter.py`) with nothing asserting agreement beyond `DocumentRecord`. The structural fix is a shared `_require_shape(td, value)` taking the TypedDict as a parameter, so a probe can drive the whole guard — killing that family in one stroke. **Not done.** This story's documented pattern is precisely *"fixed one guard, reintroduced it one guard over."*
- The `platform/` error-type boundary rule (`ValueError` for caller bugs in pure helpers vs `PlatformError` for operational failures on the Protocol) is **unwritten**. Land it in `scoring.py`'s module docstring before Epic D becomes the first real caller of `build_score_inputs`.
- ⚠️ **DEBT-52** — two of this delta's commits are mislabelled: `b56f105` (titled spec-only) carried an **applied mutant**, and `dd3ad51` (titled docs-only) carried the whole fix round. Cause: `git add -A` while a subagent was mid-mutation-test. History intentionally not rewritten.

## History
- /test gap-fill (Atchim TDD gate) on 2026-09-21 at `7a45037` (reviewed) / stamp commit `2cc4dde`: ✅ PASSED. Superseded by this re-stamp after **FU-01.3-D** (`fcadd58`, `ad333fc`, `617dd8c`, `3708b3d`) changed `langfuse_adapter.py` and four test files. That stamp's gate was reviewer-independent (Atchim had not previously reviewed the FU-01.3-B delta's code as a separate APPROVE); **this one is not** — see its Independence field.
- /test gap-fill (Atchim TDD gate) on 2026-09-20 at bb58be0397e5f4855e2d455b8150064a775bad21: ✅ PASSED. Superseded by this re-stamp after FU-01.3-B (`353549d`, `3ad1388`, `7a45037`) changed `langfuse_adapter.py`, `transport.py`, `errors.py` and four test files, staling the stamp. That stamp's gate was run on Fable 5.1 after a same-model (Opus) review was stopped mid-flight without producing a verdict — see its Independence note.
- /test gap-fill (Atchim TDD gate) PASSED on 2026-09-19 at 71e5e6acc81f6b0bd8bd224f6d0997c9f6ccce7b: ✅ PASSED. Superseded after FU-01.3-A (the A3 `run_id` precondition control) changed `langfuse_adapter.py`, `types.py` and three test files.
- /test gap-fill (Atchim TDD gate) PASSED at ba6a9056969136ef488eaeeca37b86e28483ac73, before the QA fix round. Superseded after QA S-01.3 ⚠️ (F-1..F-5) was fixed and re-gated.
- /implement (Atchim TDD gate) PASSED at 825201a07fa8b3de3f31f326eaadbc1dc23a558b. Superseded by the /test stamp required by the /qa rigor gate for Risk: high.
- /implement (Atchim TDD gate) PASSED at 02cff1be9a607c91b3dc267a601f5003614a210a. Superseded after the DEBT-18 option-B change (Atchim found a vacuous score-body assertion, fixed in 825201a, then re-APPROVED).
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round-1 findings R1–R7 (R1 Critical: `get_dataset` read a `body["items"]` key the live 4.38.0 API does not return, yielding a silently empty golden set; R2 non-asserting tests; R3 missing TP-45 span-exporter test; R4 missing TP-44 captured-log test; R5 `flush_or_raise` false negatives; R6/R7), then round-2 and round-3 test gaps; all closed.
