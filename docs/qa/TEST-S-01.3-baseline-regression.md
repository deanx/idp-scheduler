# /test stamp — SPEC-01 (S-01.3 Langfuse platform adapter)

**Status:** ✅ PASSED
**Source:** /test gap-fill (Atchim TDD gate)
**Date:** 2026-09-21 (re-stamp #3 — FU-01.3-G)
**Commit (reviewed code):** `758ab5f` (guards + pins) with the F-G2 fix on top; **stamp commit** carries this file and the `docs/state/` records. ⚠️ `/qa` freshness: git corroboration will return the stamp commit because `docs/` paths are in `Files:` — expected, not stale. Falsifiable check: `git diff --stat <stamp commit>~1 HEAD -- src/` must be empty of anything but the guards.
**Author:** alex@divinocosta.com.br
**Scope:** the **FU-01.3-G** delta — two `isinstance(document_id, str)` guards at both trust boundaries, and the type-driven VALUE-pin mechanism (`_str_fields` + `_StrFieldProbe`). Closes QA-01 re-audit #3 **F-1**, the first **fail-open** member of REG-09.

**Atchim TDD gate:** PASSED (2026-09-21, Opus 5, **third instance** on this delta) after one REQUEST CHANGES round (F-G1/F-G2/F-G3).

**Independence:** ✅ **structurally independent, and this is the first stamp in this story where that is true of the reviewer as well as the model.** Implementer **Dengoso (sonnet)**; gate run by an Atchim instance carrying **neither** instance #1's nor instance #2's review of this delta, per **DEBT-44**. That rule exists because instance #1 approved FU-01.3-D *and* gated it, and the independent QA audit then found the Major fail-open defect (F-1) this very card fixes; instance #2 reviewed FU-01.3-G across two rounds and disqualified itself. `hooks/check_reviewer_independence.py` still does not exist — an honest label, not a machine-checked fact.

**Static:** ✅ `uv run mypy` clean (23 files) · `ruff check .` clean · `pip-audit` clean · **498 passed / 14 skipped** · live **511 / 1** · **`PYTHONHASHSEED` swept over {0,1,2,7}** → 177 passed / 12 skipped at every seed (non-trivial: REG-09's MC pin was seed-dependent through `617dd8c`; `sorted()` in `_str_fields` is what buys the stability) · no test files deleted since `3708b3d` · no new skips.
**Files (delta):** src/idp_regression/platform/langfuse_adapter.py (`:111-119` record_run guard, placed after the required-key loop and before the first use, so zero-platform-writes holds structurally; `:293-303` get_dataset guard, a **raise** before `items.append` and before `_item_cache` write), tests/platform/test_record_run_preconditions.py (`_str_fields` `:35`, module-level `_StrFieldProbe` `:38`, parametrized killing test `:922`, two meta-pins `:889`/`:910`, the F-G2 field-name assertion), tests/platform/test_langfuse_adapter.py (`:239` get_dataset boundary test)
**Sequence:** **RED reproduced independently by the third instance** — scratch worktree, only `langfuse_adapter.py` reverted to `fcc20b0` (`git diff --stat` exactly `1 file changed, 20 deletions(-)`): both boundary tests fail with **`DID NOT RAISE`**. That shape is itself the evidence — a fail-open defect raises *nothing*, unlike every earlier REG-09 member which raised untyped `KeyError`/`TypeError`. It also re-drove the fail-open outside pytest on the reverted source: `RETURNED: None`, `run_experiment_calls: 1`, `http: 1 POST /api/public/scores` — the card's central claim that `record_run` **wrote it and returned success** is verified, not inherited.

## Suite results

| Scope | Passed | Failed |
|---|---|---|
| Unit + contract (default) | 498 | 0 |
| Full suite incl. live Langfuse 4.38.0 | 511 | 0 |
| `tests/platform` at each of 4 hash seeds | 177 | 0 |

## Mutation — 14 on this delta, 14 killed

Re-verified by the third instance (M3, M4, M5, M6, M7, M8, M10) and seven of its own (MY-1…MY-9). Highlights:

| # | Mutation | Killed by |
|---|---|---|
| M7 / M8 | either guard's message interpolates the offending value | the sentinel-absence assertions (INV-02's "never the value" half) |
| **MY-9** | `record_run` guard message drops the field name entirely | 🔴 **SURVIVED at gate time — 498 passed.** Fixed by F-G2; now killed by `assert "document_id" in message` |
| M10 | a platform write before validating `records[1]` | the ME anchor's trailing `_assert_zero_platform_writes`. The gate **proved the line is load-bearing**: with M10 applied *and* that line deleted, the ME test passes |
| **M13 (MY-8)** | `types.py` widens `DocumentRecord.document_id: str` → `str \| None` | 🔑 `test_str_fields_of_document_record_is_document_id_and_item_id` **alone** (`1 failed, 175 passed`; collected 177→176 as the leg vanishes). **No probe can ever reach it** — the mutation is in the production type that feeds the parametrize, not in `_str_fields` |
| MY-1 / MY-4 | guard too wide / too narrow | both boundary tests — the guard is neither |
| M5 / M6 | wrong error type per boundary / skip instead of reject | the get_dataset test (the two error classes are siblings under `PlatformError`, so the type check is real) |

## Gate findings — one code fix, two of my own overclaims

- **F-G2 (blocking, fixed):** REG-09 claimed the `document_id` message "must contain the field NAME and never the value". Only the second half was pinned; **MY-9 survived all 498 tests**. Found independently by the coverage audit *and* the gate, and reproduced by the orchestrator before either reported. One line, mirroring the `get_dataset` twin, which had pinned it all along — the asymmetry is why it was easy to miss. **R-2's defect class returning on the same card: an invariant reasoned about in a comment and enforced nowhere.**
- **F-G1 (my overclaim, corrected):** I wrote "a future **scalar** field auto-generates its value pin" in the commit message and REG-09. `_str_fields` filters `hint is str` — **`str`-scoped, not scalar-scoped**. A future `attempt: int` generates no leg at all. The suite falsifies the prose on its own: the probe test *asserts* `count_field: int` is excluded. Same shape as the "fourth escape structurally unavailable" line F-1 falsified. Now carries an event-shaped forward obligation.
- **F-G3 (my mis-attribution, corrected):** M3 as written down (declaration-order literal) dies on **both** pins, not the probe alone; only its sorted-order variant is probe-exclusive. The genuinely probe-exclusive mutants are M11 and M12.

## Two-pin ruling (third instance, agreeing with #2 on better evidence)
Neither pin is redundant. Probe-exclusive: M11, M12 — neither changes `_str_fields(DocumentRecord)` by a character. Production-exclusive: M4, MY-2, and decisively **M13**. Instance #2 rejected collapsing them because a probe carrying `document_id` re-introduces the name collision its disjointness exists to avoid — accepted, but that is a design-taste argument; **M13 is mechanical**, and the register now leads with it.

## Scenario A bugs
- None.

## History
- /test gap-fill (Atchim TDD gate) on 2026-09-21 at `7a45037` (reviewed) / stamp commit `2cc4dde`: ✅ PASSED. Superseded by this re-stamp after **FU-01.3-D** (`fcadd58`, `ad333fc`, `617dd8c`, `3708b3d`) changed `langfuse_adapter.py` and four test files. That stamp's gate was reviewer-independent (Atchim had not previously reviewed the FU-01.3-B delta's code as a separate APPROVE); **this one is not** — see its Independence field.
- /test gap-fill (Atchim TDD gate) on 2026-09-20 at bb58be0397e5f4855e2d455b8150064a775bad21: ✅ PASSED. Superseded by this re-stamp after FU-01.3-B (`353549d`, `3ad1388`, `7a45037`) changed `langfuse_adapter.py`, `transport.py`, `errors.py` and four test files, staling the stamp. That stamp's gate was run on Fable 5.1 after a same-model (Opus) review was stopped mid-flight without producing a verdict — see its Independence note.
- /test gap-fill (Atchim TDD gate) PASSED on 2026-09-19 at 71e5e6acc81f6b0bd8bd224f6d0997c9f6ccce7b: ✅ PASSED. Superseded after FU-01.3-A (the A3 `run_id` precondition control) changed `langfuse_adapter.py`, `types.py` and three test files.
- /test gap-fill (Atchim TDD gate) PASSED at ba6a9056969136ef488eaeeca37b86e28483ac73, before the QA fix round. Superseded after QA S-01.3 ⚠️ (F-1..F-5) was fixed and re-gated.
- /implement (Atchim TDD gate) PASSED at 825201a07fa8b3de3f31f326eaadbc1dc23a558b. Superseded by the /test stamp required by the /qa rigor gate for Risk: high.
- /implement (Atchim TDD gate) PASSED at 02cff1be9a607c91b3dc267a601f5003614a210a. Superseded after the DEBT-18 option-B change (Atchim found a vacuous score-body assertion, fixed in 825201a, then re-APPROVED).
- /implement (Atchim REQUEST CHANGES) on 2026-09-19: ❌ INVALIDATED. Round-1 findings R1–R7 (R1 Critical: `get_dataset` read a `body["items"]` key the live 4.38.0 API does not return, yielding a silently empty golden set; R2 non-asserting tests; R3 missing TP-45 span-exporter test; R4 missing TP-44 captured-log test; R5 `flush_or_raise` false negatives; R6/R7), then round-2 and round-3 test gaps; all closed.
