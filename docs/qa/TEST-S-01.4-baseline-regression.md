# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ✅ **PASSED WITH FINDINGS at `42c7e36`** — re-stamp #8, fresh gate (DEBT-44/46/72/77, Wave C). Every finding from stamp #7 is closed and mutant-verified, every new test kills its own mutant without help from the older suite, and the adversarial sweep over the six modules earlier gates barely touched produced no fail-open and no vacuous test. Findings are all **Low** (dead counter, stale comment, two near-equivalent survivors); none blocks.
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Date:** 2026-09-28 · **Commit:** `42c7e363c239ddd2a60e803daaf65615a21c25ff` · **Author:** alex@divinocosta.com.br (solo)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of the `prototype` profile. The delta touches `src/` (`watch.py`) so it never qualifies for `re-gate skipped`. **Profile ≠ risk level.**
**Independence (R3/DEBT-44):** run by a **new Atchim instance on Claude Fable 5.1**. This instance issued **no APPROVE and no prior verdict** on any commit in this story's history; re-stamp #7 (`d4a6c7b`, FAILED at `04e7a22`) was a different instance. The delta `42c7e36` carries `Co-Authored-By: Claude Sonnet 5` — a different model from the gate. ✅ structural (different models, fresh instance).
**Static:** `pytest -q` **1951 passed / 15 skipped** · `mypy src tests scripts` (strict) **0 errors / 151 files** · `ruff check src tests scripts` **clean**. No live IDP/platform call; `.env` never sourced. `PYTHONDONTWRITEBYTECODE=1` throughout; mutants applied by exact-string replace from a scratchpad harness (`restamp-s014e/mut.py`), one at a time (P1), restored with `cp`, `shasum -a 256 -c` **0 mismatches** across all 14 orchestration files after the run (P2/P4); no stray `__pycache__`; `git status --short` shows only this stamp and the pre-existing `docs/state/STATE.json`.

## Verdict — PASSED WITH FINDINGS. Why, in one paragraph

Stamp #7's F-1 is closed structurally, not by another special case: `verdict_ticks` is now counted positively from `VERDICT_OUTCOMES` membership (watch.py L542–543), the subtraction is gone, and the injected-unclassified-outcome repro is a test (`test_f1_an_unrecognised_outcome_is_never_silently_counted_as_a_verdict`) that alone kills both the regression to `else:` (W1) and the "unknown ⇒ verdict" variant (W9). The two ways the same mistake could return — a tick counted in BOTH tallies (W3, W8) or the `elif` order hiding a double-membership — are answered: W3/W8 die in `test_watch.py`, and a future outcome placed in both sets is caught by the disjointness asserts of `test_the_outcome_partition_is_total_and_disjoint` (CVd killed); even if it slipped past that, the consumer's `if NO_VERDICT … elif VERDICT` order counts it on the no-verdict side, which is the fail-closed direction (CVd-watch survives *in test_watch.py only* — that is the expected shape, not a gap). F-2/F-3/F-4/F-6: each of C8, C6, FA6, FA7, RA5, RA7 is killed by **the new test named for it**, first failure under `-x`, so the commit's "already correct at HEAD, now pinned" claim holds and none of the six new tests is vacuous (I checked the recurring shapes: the CLI lambdas take `run_eval`'s real positional arity so a signature drift would raise, not swallow; the facade FA6 test asserts `exit_code == 0` *and* the absence of the ceiling log so it cannot pass on a different abort; the FA7 test asserts the specific `unknown_classifier` log line and the exit code, and `resolve_classifier` at facade L553 precedes `select_items` at L605, so the refusal is pre-run in fact; the symlink test is non-equivalent because the open uses `O_CREAT|O_TRUNC` without `O_EXCL`, so dropping `O_NOFOLLOW` really does write through the link — RA5 confirmed). R1 unchanged-line mutants on `bootstrap.py`, `prerun.py`, `dotenv_support.py`, `run_naming.py`, `log_sanitize.py`, `errors.py` — 26 mutants, 25 killed — found the guards there are held by behavioural tests, not sentinels. **Ninth fail-open: no. Ninth vacuous test: no.** What remains is housekeeping.

## Answered, per the brief

- **Can a tick now be double-counted, or can the `elif` order hide a bug?** No on both. Double-count mutants W3 (no-verdict ticks also counted as verdicts) and W8 (verdict ticks also counted as no-verdict) both die in `test_watch.py`. Order: W4 (swap `VERDICT` first) **survives and is equivalent** — the two sets are disjoint, asserted by the totality test, which kills CVd (`OUTCOME_CEILING_REACHED` in both sets). With the sets as written the order cannot matter; if someone breaks disjointness the producer test fails first, and the consumer's `NO_VERDICT`-first order still lands the tick on the safe side.
- **For F-2/F-3/F-4/F-6, is the NEW test what kills each mutant?** Yes: C8 → `test_platform_values_flag_is_passed_through_to_run_eval`; C6 → `test_document_flag_is_passed_through_to_run_eval`; FA6 → `test_run_eval_quota_ceiling_counts_the_selected_items_not_the_whole_dataset`; FA7 → `test_run_eval_unknown_classifier_is_refused_before_any_idp_call`; RA5 → `test_write_run_artifact_refuses_a_preplanted_symlink_at_the_target_path`; RA7 → `test_artifact_envelope_never_carries_an_abort_reason_on_a_complete_run`. Each was the first failure under `-x` against the file it lives in. The default-direction twins (C8b `"verdicts-only"`, C6c `[]`) die to the pre-existing `test_valid_args_call_run_eval_with_the_resolved_values`, which is why C8 alone could survive 1,942 tests at stamp #7: the constant `"full"` coincided with the default.
- **Own vacuous-test risk in the new tests?** None found. The `test_f1_an_unrecognised_outcome…` test builds its own `MonkeyPatch` and undoes it in `finally`, asserts on the headline in both directions (`not in` / `in`), and W1/W9 prove it discriminates. The CLI doubles are positional-arity-exact. The facade FA7 double raises `AssertionError` from `extract`; facade's per-document loop would convert that into `hard_failure` exit 1 — so the `exit_code == 1` assert alone *would* be vacuous, but the `"unknown_classifier" in caplog.text` assert is not, and FA7 confirms.
- **Ninth fail-open anywhere in the story's surface?** **No.** Re-read every `TickResult(` and `outcome =` site in `check_once` (constants only); the consumer no longer trusts the vocabulary. In the six under-touched modules every guard mutant died: whitespace-only credential (B1), first-var-only iteration (B2), value-in-message (B3/B4), schema-drift inversion (P1), empty-set (P2), N28 raise-to-continue (P3), absent/non-object schema (P4/P5), key-order canonicalisation (P6), golden value in the log (P7), `override=True` (D1), bare walk (D2), no-op load (D3), 7-char suffix (N1), dashed/uppercase id (N2/N3), redaction floor (L1/L3/L5), `..` clamp (L2), unsanitised frame (L4), narrowed catch-all (L7), abort-reason constant (E1), reason-as-message (E2). `errors.py`'s `reason` is held by `test_prerun.py` in the first test.
- **Ninth vacuous-test survivor?** **No.** Every mutant on a guarded line dies to a test asserting the guarded behaviour, not a sentinel. The two survivors outside W4/CVd-watch are L6 and RA10, both Low and both explained below — neither is a test that "passes with the guard deleted" in the sense this story has met eight times; L6 is a defense-in-depth branch whose observable difference needs a cwd under `src/`, and RA10 is reader leniency in the fail-closed direction.
- **DEBT-131 / DEBT-132:** both rows present in `docs/state/DEBT.md` (L462–463), DEBT-131 narrowed as the commit says. DEBT-130 (loop-level sweep test) still open — `_base_kwargs` in `test_watch.py` still passes `sweep_every_n_ticks=0`; re-confirmed, not re-argued.

## Findings

| # | Sev | File · symbol | Scenario | Suggested fix |
|---|---|---|---|---|
| F-1 | Low — dead counter | `src/idp_regression/orchestration/watch.py` · `run_watch_loop`, L402 / L539 `healthy_ticks` | Assigned and incremented, **never read** since the subtraction was deleted. Ruff cannot flag it (`+=` counts as a use). Harmless today; the next reader will assume the summary depends on it. | Delete both lines and the paragraph at L403–412 that introduces it, or fold it into a structured `tick_counts` log line on exit so it earns its keep. |
| F-2 | Low — stale comment describes deleted code | `watch.py` · L618–620 | The block above `unanswered_ticks` still says the branch is on "`verdict_ticks = healthy_ticks - no_verdict_ticks`" — the very derivation this delta removed as fail-open. A comment that names a formula the code no longer uses is how the subtraction comes back. | Reword to "`verdict_ticks`, counted positively from `VERDICT_OUTCOMES` at L542". |
| F-3 | Low — near-equivalent survivor | `src/idp_regression/orchestration/log_sanitize.py` · `frame_location`, L169 `if not os.path.isabs(frame.filename)` · `tests/orchestration/test_log_sanitize.py::test_frame_location_for_a_non_absolute_filename_never_joins_the_cwd` | **L6** (`if False`) survives: with the suite's cwd at the repo root, `relpath(join(cwd, "<string>"), src/)` yields `../<string>` and the `..` clamp returns the same `<external>/<string>`; a deleted cwd is caught by the `OSError` clause. Observable difference only when cwd is *under* `src/` (returns `idp_regression/<string>:…`, still not absolute, still not the cwd string). The test's name promises more than its cwd exercises. | Add a `monkeypatch.chdir(Path(log_sanitize._PACKAGE_PARENT) / "idp_regression")` case to the same test; L6 then dies. |
| F-4 | Low — reader leniency, fail-closed direction | `src/idp_regression/orchestration/run_artifact.py` · `parse_run_artifact`, `if status not in ("complete", "aborted")` | **RA10** (also accepting `None`) survives `test_run_artifact.py` + `tests/ui` (237 tests). A format/2 envelope with `"status": null` would parse as `status=None` and `run_level_gate` returns `INCOMPLETE`, never `PASS` — so this cannot green a run, but it blurs "not an artifact" (`ValueError`) into "an incomplete run". | Parametrize the existing unknown-status test over `["weird", None, 1]`. |

**Debt for Dunga (`/debt add`):** F-1/F-2 together as one housekeeping row on `watch.py` (dead `healthy_ticks` + stale formula comment); F-3/F-4 as one row "two near-equivalent survivors at re-stamp #8 (L6, RA10)". DEBT-130 re-confirmed open. Nothing here blocks `/qa`.

## Mutation matrix (R1: origin named; exact-string replace; one at a time; `cp` restore; `shasum -a 256 -c` 0 mismatches; `PYTHONDONTWRITEBYTECODE=1`; `-x`)

Targets: `W` = `tests/orchestration/test_watch.py`, `CV` = `test_check_versions.py`, `CLI` = `test_cli.py`, `FAC` = `test_facade.py`, `DF` = `test_document_filter.py`, `RA` = `test_run_artifact.py`, `B/P/D/N/L` = the module's own test file, `ORCH` = `tests/orchestration`. **51 mutants · 47 killed · 4 survived (1 equivalent, 1 expected-by-design, 2 Low).**

| # | File · mutant | Invariant / origin | Diff line? | Target | Result |
|---|---|---|---|---|---|
| W1 | `watch.py` `elif … in VERDICT_OUTCOMES` → `else` (regress #7 F-1) | fail-closed on unclassified outcome | yes | W | KILLED (`test_f1_an_unrecognised_outcome…`) — the P7 red |
| W2 | `verdict_ticks += 1` branch deleted | headline needs a verdict | yes | W | KILLED (`test_startup_banner_and_quiet_tick_lines`) |
| W3 | no-verdict ticks ALSO counted as verdicts (double count) | tallies disjoint | yes | W | KILLED (`test_f1_ceiling_reached…`) |
| W4 | `if VERDICT … elif NO_VERDICT` (order swapped) | order irrelevance | yes | W+CV | SURVIVED — **equivalent** (sets disjoint, asserted by totality test; CVd proves the assert is live) |
| W5 | `if verdict_ticks > 0` → `>= 0` | headline on verdicts | no | W | KILLED (`test_f4_ctrl_c_after_only_failed_ticks…`) |
| W6 | `unanswered_ticks = failed_ticks` (drops no-verdict) | qualifier counts both axes | no | W | KILLED (`test_f1_ceiling_reached…`) |
| W7 | `in NO_VERDICT_OUTCOMES` → `== OUTCOME_INDETERMINATE` (regress #5 F-1) | partition, not special case | no | W | KILLED (`test_f1_ceiling_reached…`) |
| W8 | verdict ticks ALSO counted as no-verdict (double count, other direction) | tallies disjoint | yes | W | KILLED (`test_f3_a_partial_verdict_run…`) |
| W9 | `elif … in VERDICT_OUTCOMES or … not in ALL_TICK_OUTCOMES` (unknown ⇒ verdict) | fail-closed on unclassified | yes | W | KILLED (`test_f1_an_unrecognised_outcome…`) |
| CVd | `check_versions.py` `OUTCOME_CEILING_REACHED` added to `VERDICT_OUTCOMES` too (in both sets) | partition disjointness | no | CV | KILLED (`test_the_outcome_partition_is_total_and_disjoint`) |
| CVd-watch | same mutant | consumer's elif order | no | W only | SURVIVED — **expected**: `test_watch.py` cannot see double-membership; the `NO_VERDICT`-first order counts such a tick as no-verdict (fail-closed). Covered by CVd. |
| C8 | `cli.py` `platform_values=args.platform_values` → `"full"` | `## Domain` privacy mode (#7 F-2) | no | CLI | KILLED (`test_platform_values_flag_is_passed_through…`) — the new test |
| C8b | → `"verdicts-only"` | default direction | no | CLI | KILLED (`test_valid_args_call_run_eval_with_the_resolved_values`) |
| C6 | `documents=args.documents` → `None` | `--document` is run identity (#7 F-3) | no | CLI | KILLED (`test_document_flag_is_passed_through…`) — the new test |
| C6b | → `args.documents[:1]` (drops 2nd doc) | all selectors forwarded | no | CLI | KILLED (`test_document_flag_is_passed_through…`) |
| C6c | → `args.documents or []` | `None` vs `[]` | no | CLI+FAC+DF | KILLED (`test_valid_args_call_run_eval…`) |
| FA6 | `facade.py` `item_count = len(dataset["items"])` | ceiling counts the selection (#7 F-3) | no | FAC | KILLED (`test_run_eval_quota_ceiling_counts_the_selected_items…`) — the new test |
| FA7 | unknown-classifier handler `return 1` → `0` (+ `run_end` 0) | pre-run refusal exit code (#7 F-4) | no | FAC | KILLED (`test_run_eval_unknown_classifier_is_refused…`) — the new test |
| FA8 | ceiling `>` → `>=` | A10 boundary | no | FAC | KILLED (`test_run_eval_quota_ceiling_boundary_equal_is_accepted`) |
| FA9 | `select_items` `if not selectors` → `is None` | `[]` ≡ no filter | no | DF+FAC | KILLED (`test_no_selector_returns_every_item_unchanged`) |
| FA10 | unmatched selector `raise` → `continue` | filtered run measuring nothing must not exit 0 | no | DF+FAC | KILLED (`test_an_unmatched_selector_is_refused`) |
| RA5 | `run_artifact.py` `O_NOFOLLOW` dropped | symlink refusal (#7 F-6); non-equivalent (no `O_EXCL`) | no | RA | KILLED (`test_write_run_artifact_refuses_a_preplanted_symlink…`) — the new test |
| RA7 | `abort_reason` kept on `complete` | envelope shape (#7 F-6) | no | RA | KILLED (`test_artifact_envelope_never_carries_an_abort_reason…`) — the new test |
| RA9 | `os.chmod(ARTIFACT_DIR_NAME, 0o700)` dropped | owner-only tightening | no | RA | KILLED (`test_write_run_artifact_tightens_a_pre_existing_world_readable_directory`) |
| RA10 | `parse_run_artifact` status check also accepts `None` | reader fail-closed | no | RA + `tests/ui` | SURVIVED → F-4 (Low; `INCOMPLETE`, never `PASS`) |
| B1 | `bootstrap.py` `.strip()` dropped | N6 whitespace-only credential | no | B | KILLED (`…whitespace_only[LANGFUSE_HOST-   ]`) |
| B2 | iterate only the first required var | every var checked | no | B | KILLED (`…raises_when_a_var_is_absent[LANGFUSE_PUBLIC_KEY]`) |
| B3 | error carries the value when set | INV-02 | no | B | KILLED (`…whitespace_only…`) |
| B4 | message drops the variable name | "clear message" | no | B+FAC | KILLED (`…names_only_the_variable_not_any_value`) |
| P1 | `prerun.py` hash mismatch → never aborts | ADR-0005 #8 | no | P | KILLED (`test_check_schema_drift_aborts_on_mismatch`) |
| P2 | empty-set guard → never aborts | A4 | no | P | KILLED (`test_check_empty_set_aborts_on_an_empty_dataset`) |
| P3 | N28 `raise` → `continue` (log only) | malformed_golden aborts | no | P | KILLED (`…aborts_malformed_golden_on_a_missing_fields_key`) |
| P4 | absent-schema branch removed | `actual=absent` log | no | P | KILLED (`…aborts_when_schema_is_absent`) |
| P5 | non-object-schema branch removed | FO-6 | no | P | KILLED (`…says_when_the_schema_is_not_an_object[a string]`) |
| P6 | `sort_keys=True` dropped from canonical hash | JSONB key-order drift | no | P | KILLED (`…insensitive_to_key_order_and_whitespace`) |
| P7 | N28 log line carries the golden | INV-02 | no | P | KILLED (`…never_leaks_the_golden_value_into_the_log`) |
| D1 | `dotenv_support.py` `override=True` | env wins over file | no | D + `tests/ui/test_workspace.py` | KILLED (`…never_overrides_an_already_set_var`) |
| D2 | bare `_load_dotenv()` (walks from source tree) | REG-07/10 | no | D | KILLED (`…sets_a_var_from_the_env_file`) |
| D3 | `load_dotenv_file` no-op | loads at all | no | D | KILLED (`…sets_a_var_from_the_env_file`) |
| N1 | `run_naming.py` `[:8]` → `[:7]` | DEBT-19 suffix | no | N | KILLED (`…appends_the_first_eight_chars_of_run_id`) |
| N2 | `uuid4().hex` → `str(uuid4())` | 32-hex run_id (`_RUN_ID_PATTERN`) | no | N | KILLED (`…is_a_32_char_lowercase_hex_string`) |
| N3 | `.hex.upper()` | lowercase pin | no | N+RA | KILLED (same) |
| L1 | `log_sanitize.py` min-redactable 6 → 1 | DEBT-54 floor | no | L | KILLED (`…ignores_a_too_short_env_value`) |
| L2 | `..` clamp removed | absolute-path disclosure | no | L | KILLED (`…never_returns_an_absolute_path`) |
| L3 | IDP credential names dropped from redaction set | A-1/A-3 | no | L | KILLED (`…replaces_a_live_credential_value`) |
| L4 | frame string returned unsanitised | DEBT-58 line forgery | no | L | KILLED (`…never_returns_an_absolute_path`) |
| L5 | status not redacted before quoting | A-3 | no | L | KILLED (`…redacts_a_live_credential_value`) |
| L6 | `if not isabs(frame.filename)` → `if False` | GAP-8 | no | L | SURVIVED → F-3 (Low; near-equivalent at the suite's cwd) |
| L7 | catch-all `Exception` → `ValueError` | totality | no | L | KILLED (`…is_total_against_an_arbitrary_relpath_failure`) |
| E1 | `errors.py` `self.reason` fixed to `"hard_failure"` | abort taxonomy | no | P+FAC | KILLED (`test_check_schema_drift_aborts_on_mismatch`) |
| E2 | message replaced by reason | message/reason distinct | no | ORCH | KILLED (`…says_when_the_schema_is_not_an_object`) |

## Standing gaps — re-checked at `42c7e36`

1–4 from stamp #4 (`--auto-run` POC override; no backoff; no heartbeat; nothing under a loaded `launchd` schedule) — still true. **DEBT-130** (no loop-level sweep test) — still open. **DEBT-131** — narrowed as the commit says; this pass added FA8–FA10, RA9–RA10 on the same modules (all but RA10 killed). **DEBT-132** — filed as promised. Standing gap 7 (`_ZERO_EXIT_OUTCOMES` in the one-shot CLI) — unchanged, for Soneca. **DEBT-129** — unchanged.

## Freshness (P5, DEBT-72/77) — explicit file list pinned to `42c7e36`

`git ls-files src/idp_regression/orchestration` = 14 files = on-disk `*.py` count (14); `git diff --diff-filter=A --name-only 04e7a22..HEAD -- src tests` **empty** (no files added since stamp #7); `git status --short src tests` empty. Blob ids are HEAD's.

| File (HEAD blob) | `04e7a22..HEAD` |
|---|---|
| `__init__.py` (e69de29b) | unchanged |
| `bootstrap.py` (1e13515d) | unchanged |
| `check_versions.py` (7057b55a) | unchanged |
| `cli.py` (163e2416) | unchanged |
| `dotenv_support.py` (902d1144) | unchanged |
| `errors.py` (c9bcbfc6) | unchanged |
| `facade.py` (c4cab023) | unchanged |
| `log_sanitize.py` (50b57623) | unchanged |
| `prerun.py` (b741fba0) | unchanged |
| `run_artifact.py` (418e3d42) | unchanged |
| `run_naming.py` (dc2be96b) | unchanged |
| `scorer_store.py` (38d15122) | unchanged |
| `version_discovery.py` (de048193) | unchanged |
| `watch.py` (ae9bc6c0) | +17 −1 |

Tests changed in the delta: `tests/orchestration/test_cli.py` (+89), `test_facade.py` (+68), `test_run_artifact.py` (+34), `test_watch.py` (+52); `docs/state/DEBT.md` (+3 −1). **Ordering (P7):** tests and fix landed in one commit; W1 is the pre-fix code shape and is red under the new `test_f1_an_unrecognised_outcome…`, so the F-1 fix has a demonstrated RED. F-2/F-3/F-4/F-6 pinned code that was already correct; each new test is shown red by its mutant above.

## Next step

Ready for Zangado (`/qa`). The four Low findings go to `DEBT.md` via Dunga; none needs a fix round before QA.

## History

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ FAILED | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |
| `8a01c62` (2026-09-28, re-stamp #4, stamp committed as `79315d5`) | ❌ FAILED | F-2/F-5/F-6/F-7 of `f3b0b65` **closed and mutant-verified** (M3–M8). **F-1** fail-open #6 (interleaved `indeterminate` ticks read as healthy; 30-of-40 ambiguous → "no new versions found.", exit 0) · **F-2** `if auto_run:` quota guard unpinned by 626 tests; the test named for it is vacuous · **F-3** the `AssertionError`-sentinel class in `test_cli.py` — three A8/A10 guards deletable green · F-4 summary boundary unpinned · F-5 vacuous dotenv test · F-6 P7 ordering |
| `1e931fe` (2026-09-28, re-stamp #5, stamp committed as `3abfb25`) | ❌ FAILED | F-1..F-5 of `8a01c62` **closed and mutant-verified** (MA1–MA6, MB1–MB3, MB7, MC1–MC5); F-6 filed as DEBT-128. **F-1** fail-open #7 (`ceiling_reached` is a no-verdict outcome that outranks and resets `indeterminate`; CLI defaults + legal lookahead → "no new versions found (1 of 40 …)", exit 0; MA12 survives) · **F-2** `healthy_ticks > 0` branches on completed, not answered, ticks (40/40 indeterminate → headline "no new versions found", exit 0) · **F-3** two more vacuous-sentinel tests (MB4 64-cap, MB5 `--version required`) · F-4 counts omitted on the non-fail-open arms |
| `34dfe14` (2026-09-28, re-stamp #6, stamp committed as `6f0a89f`) | ❌ FAILED | F-1 (fail-open #7) and F-3 of `1e931fe` **closed and mutant-verified** (M1–M3, M8, M9, M11, MB4, MB5). **F-1** the vacuous-sentinel class is still open — `--action required` deletable green across the full suite (MB8), two more vacuous tests plus one held only incidentally (MB6b) · **F-2** the totality test is tautological (`ALL_TICK_OUTCOMES` is the union it is compared to; M4b survives) — the commit's central guarantee does not hold · **F-3** stamp #5's F-2 untouched and now pinned as expected behaviour by the delta's own test (M10) · F-4 carried · F-5 the "recorded as DEBT" gap has no DEBT row · F-6 `--run required` unpinned (MB11) |
| `04e7a22` (2026-09-28, re-stamp #7, stamp committed as `d4a6c7b`) | ❌ FAILED | F-1, F-2, F-3 of `34dfe14` **closed and mutant-verified** (W1–W5, CV2′, CV3, MB8, MB11, MB6b, MB13); DEBT-129/130/131 filed as promised; DEBT-131's four modules mutated for the first time (25 mutants, 21 killed). **F-1** `verdict_ticks` by subtraction is fail-open on any unclassified outcome (injected repro: `no new versions found.` exit 0) and the totality guard it leans on has a bare-literal bypass (CV1) · **F-2** `--platform-values` CLI forwarding survives the full suite (C8) · F-3 `--document` forwarding (C6) and selection-count ceiling (FA6) unpinned · F-4 unknown-classifier refusal exit code unpinned (FA7), reachable from two scripts · F-5 exit-code decision not recorded · F-6 RA5/RA7 |
| `42c7e36` (2026-09-28, **this stamp**, re-stamp #8) | ✅ **PASSED WITH FINDINGS** | F-1..F-4, F-6 of `04e7a22` **closed and mutant-verified** (W1, W9, C8, C6, FA6, FA7, RA5, RA7 — each killed by its own new test); F-5 → DEBT-132; DEBT-131 narrowed. 51 mutants / 47 killed; W4 equivalent, CVd-watch expected, RA10 + L6 Low. **No ninth fail-open, no ninth vacuous test.** Four Low findings: dead `healthy_ticks`, stale subtraction comment, L6 near-equivalent, RA10 reader leniency |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

**DEBT-44:** this PASS was issued by an instance that issued no earlier verdict on this story, on a model other than the implementer's. Nothing in this stamp should be re-used by the `/qa` gate as its own evidence — Zangado runs his own audit.

— Atchim [atchim] …excuse me. Clean matrix, and I am saying so plainly.
