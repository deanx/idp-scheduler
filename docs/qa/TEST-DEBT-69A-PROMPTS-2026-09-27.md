# TEST stamp — DEBT-69(a), `prompts` in MuleSoft's documented shape

**Range:** `c1a36cb` (one commit: `feat(adapter): parse prompts in MuleSoft's documented shape -- DEBT-69(a)`),
measured at `HEAD 1e332dd`.
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**; this is a **wire-contract delta**
in `adapter/normalize.py` (`_merge_prompts`, the `pages[]` loop default), so under `## Rigor` rows 1–3 nothing
is lightened: `/test` runs at `full`, fresh instance, own mutation matrix. **SR-1 binds** and is honoured
in the *labelling* sense only — see "Wire-contract status" below.
**Verdict:** ✅ **PASS WITH FINDINGS** — 5 non-blocking findings, every one a test gap over correct shipped
code (each survivor was probed against the shipped source; none changes behaviour). **No fail-open survivor:**
every surviving mutant either moves the parser in the fail-closed direction or drops an assertion on a value
the shipped code computes correctly. Every refusal happens inside `normalize()`, before any classify or write.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and never saw the implementer's four
mutants. **DEBT-44 honoured.** All 29 mutants below are the gate's own, aimed at the invariants named in the
brief, not at the implementer's lines. Each was applied to the working file from a scratchpad copy, run, and
restored byte-identically (`shasum -a 256 -c` OK after every mutant and at the end; `git status --short`
shows only the pre-existing `docs/state/STATE.json` change; `__pycache__` outside `.venv`/`frontend`
deleted). No live IDP/platform call; `.env` never sourced.

## Gates (measured at `1e332dd`)

| Gate | Result |
|---|---|
| `pytest -q tests/adapter tests/classifier tests/orchestration` | **1012 passed / 3 skipped** (opt-in integration), 4.5 s |
| `mypy src tests` (strict) | Success — no issues, 136 files |
| `ruff check src tests` | All checks passed |
| `gitleaks detect --log-opts="c1a36cb^..c1a36cb"` | no leaks found |

## Wire-contract status (SR-1)

The pin is `tests/adapter/fixtures/mulesoft_docs_prompts_example.json` — MuleSoft's own documentation
example, **not a live capture**. Checked that nothing claims otherwise: the fixture's README, the test-module
comment (`test_normalize.py:975`), the `_merge_prompts` comment, the ADR-0002 amendment and the DEBT-69 row all
say *unverified against a live response*. **DEBT-69(a) stays open**; this stamp certifies the parser against
the documented shape only. It is the best available evidence and it is still authored from a document, not
from the wire.

## Mutation matrix — 17 killed / 12 survived

Suite = `tests/adapter tests/classifier tests/orchestration` (1012 tests). `-x` per mutant, first killer named.

| # | Mutant (invariant attacked) | Result | Killed by / judgement |
|---|---|---|---|
| M01 | non-empty LIST silently ignored (`return`) | killed | `test_the_undocumented_list_form_is_rejected` |
| M02 | non-dict, non-list `prompts` (a string) silently ignored | killed | same (a list is the non-dict case the test uses; a string probes `RAISE invalid_page` on shipped code) |
| M03 | non-dict entry skipped (`continue`) instead of raising | killed | `test_non_dict_prompt_entry_raises_typed_error` |
| M04 | keyed by NAME instead of question text | killed | `test_normalize_walks_pages_fields_tables_prompts` |
| M05 | name validation dropped | killed | `test_an_unsafe_prompt_name_is_rejected` |
| **M06** | name validated with the FIELD-name charset (`_SAFE_NAME_PATTERN`, `[A-Za-z0-9_-]{1,128}`) instead of the prompt-key charset | **SURVIVED** | → F-4 (fail-closed: a name with a space would be refused) |
| M07 | name check applied to the question text (swapped strings) | killed | `test_an_unsafe_prompt_name_is_rejected` |
| M08 | answer read from `answer` (the whole map), not `answer.value` | killed | `test_normalize_walks_pages_fields_tables_prompts` |
| **M09** | prompt answer confidence dropped (always `None`) | **SURVIVED** | → F-2 |
| **M10** | prompt confidence read raw from `confidence` only (`confidenceScore` ignored, never scaled) | **SURVIVED** | → F-2 (D2 class on the prompts branch) |
| **M11** | `source` type check dropped (a non-string source carried through) | **SURVIVED** | → F-5 (shipped code raises `invalid_page`) |
| M12 | `source` size cap doubled | killed | `test_prompt_source_too_large_raises_typed_error` |
| M13 | `source` dropped (always `None`) | killed | `test_normalize_walks_pages_fields_tables_prompts` |
| M14 | within-one-container `duplicate_prompt` dropped | killed | `test_duplicate_prompt_within_the_top_level_rollup_itself_still_raises` |
| **M15** | pages-vs-pages `duplicate_prompt` dropped (`if False`) | **SURVIVED** | → F-1 |
| M16 | `allow_override` inverted | killed | `test_top_level_prompt_wins_over_a_colliding_pages_entry_instead_of_raising` |
| **M17** | `seen_keys.add` dropped (cross-page duplicate invisible) | **SURVIVED** | → F-1 |
| M18 | top-level prompts-only response is `missing_envelope` | killed | `test_top_level_prompt_wins_over_a_colliding_pages_entry_instead_of_raising` (+ `test_top_level_prompts_only_is_a_recognised_envelope_not_missing`) |
| M19 | empty `{}` raises | killed | `test_extract_happy_path_returns_normalized_output` (+ `test_an_empty_prompts_container_is_no_prompts[empty1]`) |
| M20 | empty `[]` tolerance dropped (raises) | killed | `test_an_empty_prompts_container_is_no_prompts[empty0]` (re-run after a first, indentation-broken variant errored at collection — that run is not counted) |
| **M21** | INV-02: prompt NAME echoed (`!r`) into the `unsafe_prompt_key` message | **SURVIVED** | → F-3 |
| **M22** | INV-02: question TEXT echoed (`!r`) into the `unsafe_prompt_key` message | **SURVIVED** | → F-3 |
| **M23** | INV-02: key echoed into the `duplicate_prompt` message | **SURVIVED** | → F-3 |
| **M24** | INV-02: the non-mapping raw ENTRY echoed into the `invalid_page` message | **SURVIVED** | → F-3 |
| M25 | page-loop default for `prompts` becomes `None` (a page without prompts aborts) | killed | `test_extract_happy_path_returns_normalized_output` |
| M26 | list rejected but the message no longer names the documented shape | killed | `test_the_undocumented_list_form_is_rejected` |
| M27 | a non-empty list silently revived as `{p0: …}` (legacy shape accepted) | killed | `test_the_undocumented_list_form_is_rejected` |
| **M28** | name min length 1 → 0 (empty-string name accepted) | **SURVIVED** | → F-4 |
| **M29** | name max length 200 → 2000 | **SURVIVED** | → F-4 |

## Survivors judged

Each survivor was probed against the **shipped** source to confirm the behaviour the missing test should pin:

- name with a space (`"company business"`) → **accepted** (prompt-key charset, as the delta says);
- `confidenceScore: 88.0` on a prompt answer → `confidence: 0.88` (scaled, shared `_coerce_cell`);
- `source: 12` → `RAISE invalid_page`;
- two `pages[]` entries sharing a question → `RAISE duplicate_prompt`;
- `prompts: "oops"` → `RAISE invalid_page` (documented-shape message);
- empty-string name → `RAISE unsafe_prompt_key`;
- `answer` absent / `answer: {}` → `RAISE invalid_cell` (never a silent `None`).

So every survivor is a **test gap, not a defect**. None is fail-open: M06/M28/M29/M11 only ever tighten or
loosen a refusal on the *name*/`source` (the name is validated and otherwise unused; a non-string source is
carried, not gated on); M15/M17 would silently last-wins-merge two pages' answers, which is an integrity loss
but still a gate over a real value; M09/M10 would zero a confidence no shipped classifier decides on; M21–M24
are message-content only.

## Findings (all non-blocking)

| # | Sev | File · symbol · scenario | Suggested fix |
|---|---|---|---|
| **F-1** | Medium | `tests/adapter/test_normalize.py` — no test has **two `pages[]` entries** sharing a prompt question (M15, M17). The delta states the pages-vs-pages `duplicate_prompt` rule is "unchanged", but the only duplicate tests are within one container (M14) and page-vs-top-level (M16), so "unchanged" is asserted by reading, not by a test. Pre-existing gap; this delta rewrote the loop it lives in. | Add `test_two_pages_sharing_a_prompt_question_raise_duplicate_prompt`: `pages: [{prompts: {a: {prompt: "q?"…}}}, {prompts: {b: {prompt: "q?"…}}}]` → `reason == "duplicate_prompt"`. |
| **F-2** | Medium | `tests/adapter/test_normalize.py::test_the_documented_map_is_also_accepted_at_the_top_level` and `::test_normalize_walks_pages_fields_tables_prompts` — neither asserts the prompt answer's **confidence** (M09, M10). The top-level test even carries `confidenceScore: 88.0` and never reads it back, so the REG-11 D2 scale defect could recur on the prompts branch with these pins green — the exact "compounding" shape DEBT-69's row warned about for `pages[]`. | Assert `out["prompts"]["Who is the vendor?"]["confidence"] == 0.88` in the top-level test and `== 0.88` (legacy key) in the walk test. `ScoreContext.confidence` reaches every scorer, so this value is contract, not decoration. |
| **F-3** | Medium | `tests/adapter/test_normalize.py::test_an_unsafe_prompt_name_is_rejected` (new in this delta) asserts `reason` only; the pre-existing key tests assert only that the **surrogate character** is absent from `str(exc)`, which a `{key!r}` echo passes (M21–M24). The delta added a new error site (the name check) with no INV-02 message assertion at all. | In each of the four raise sites' tests, assert the offending name / question / raw entry text is `not in str(excinfo.value)` and `not in repr(excinfo.value.__cause__)` — the pattern `test_normalize.py:403-405` already uses for cell values. |
| **F-4** | Low | `tests/adapter/test_normalize.py` — the name's charset and length bounds are unpinned (M06, M28, M29): a name outside `[A-Za-z0-9_-]` (space, punctuation, non-ASCII) is accepted by shipped code but no test says so, and neither the 1-char minimum nor the 200-char cap is exercised. MuleSoft's example (`"business"`) fits every charset, so it discriminates nothing. Fail-closed direction. | Parametrize a positive case (`"company business"`, `"razão-social"`) and two negatives (`""`, `"x"*201`) → `unsafe_prompt_key`. |
| **F-5** | Low | `tests/adapter/test_normalize.py` — non-string `source` (M11) has no test; shipped code raises `invalid_page`. | One test: `source: 12` → `reason == "invalid_page"`. |

## Debt to hand to Dunga

- **F-1..F-5 as one DEBT row** (test/contract, Low-Med): "prompts branch — five mutation survivors, all
  test gaps over correct code", cross-referenced from DEBT-69. F-2 is the one to do first: it is the D2
  class on the branch the D2 pins do not cover.
- **Reminder, not new:** DEBT-69(a) remains open until a real response carries `prompts`; this stamp does
  not close it and SR-1 is not discharged by a documentation example.

— Atchim [atchim] (fresh instance, Fable 5.1)

---

## Addendum — findings closed (implementer, same day)

Each finding now has a test, re-run against the gate's own mutant text, and each named survivor dies:

| Finding | Survivor(s) re-run | Test |
|---|---|---|
| F-1 | M15 (pages-vs-pages duplicate dropped) | `test_a_question_shared_by_two_pages_is_a_duplicate` |
| F-2 | M09 (confidence dropped) | `test_a_prompt_answers_confidence_is_read_like_a_fields`: `confidenceScore` 88 → 0.88, `confidence` 0.7, absent → None |
| F-3 | M21 (name echoed into the message) | `test_no_prompt_name_question_or_entry_is_echoed_in_the_error`: name, question, raw entry and duplicate key; checks `str(exc)` and `repr(__cause__)` |
| F-4 | M06 (field-name charset) | `test_the_prompt_name_follows_the_prompt_key_rule`: punctuation allowed, 200 ok, 201 and empty refused |
| F-5 | M11 (source type check dropped) | `test_a_non_string_prompt_source_is_rejected` |

No production code changed. DEBT-69(a) stays open for the first real prompt-bearing response.
