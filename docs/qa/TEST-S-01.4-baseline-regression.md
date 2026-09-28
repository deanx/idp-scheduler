# /test stamp — SPEC-01 (S-01.4 run orchestration + CLI)

**Status:** ❌ **FAILED at `04e7a22`** — re-stamp #7, fresh gate (DEBT-44/46/72/77, Wave C). Narrower than #6: **everything the delta claims is closed and mutant-verified**; the gate fails on the *derivation direction* of the line it changed, and on two CLI forwardings that 1,942 tests do not hold.
**Source:** /test re-stamp (Atchim TDD gate, fresh instance)
**Date:** 2026-09-28 · **Commit:** `04e7a22800e2432e379cc545624c0703228f4fe7` · **Author:** alex@divinocosta.com.br (solo)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp binds regardless of the `prototype` profile. A `src/` fix never qualifies for `re-gate skipped`. **Profile ≠ risk level.**
**Independence (R3/DEBT-44):** run by a **new Atchim instance on Claude Fable 5.1**. This instance issued **no APPROVE and no prior verdict** on any commit in this story's history; re-stamp #6 (`6f0a89f`, FAILED at `34dfe14`) was a different instance. The delta `04e7a22` carries `Co-Authored-By: Claude Sonnet 5` — a different model from the gate. ✅ structural (different models, fresh instance).
**Static:** `pytest -q` **1942 passed / 15 skipped** · `mypy src tests scripts` (strict) **0 errors / 151 files** · `ruff check src tests scripts` **clean**. No live IDP/platform call; `.env` never sourced. `PYTHONDONTWRITEBYTECODE=1` throughout; mutants applied by exact-string replace from a scratchpad harness (`restamp-s014d/mut.py`), restored with `cp`, `shasum -a 256 -c` byte-identical for all 14 orchestration files after the run; no stray `__pycache__`; `git status --short` shows only this stamp and the pre-existing `docs/state/STATE.json`.

## Verdict — FAILED. Why, in one paragraph

Stamp #6's F-1, F-2 and F-3 are genuinely closed: the parametrized required-flag sweep kills MB8, MB11, MB6b **and** MB13 from `test_cli.py` alone; the totality test now kills a new `OUTCOME_*` constant left unclassified (CV2′) and a bucket drop (CV3) by itself; the headline branches on `verdict_ticks` and every regression mutant on that line dies (W1–W5). The arithmetic is sound in every arm (`no_verdict_ticks ⊆ healthy_ticks` by construction, so `verdict_ticks ≥ 0`; only the `interrupted` arm reads it; a halt tick can never reach that arm; a detection always leads). **But the derivation is fail-open by construction:** `verdict_ticks = healthy_ticks − no_verdict_ticks` counts *anything the watcher does not recognise* as a verdict. Injecting an outcome outside `ALL_TICK_OUTCOMES` on every tick at HEAD, unmodified, prints `Stopped after 10 tick(s). no new versions found.` with exit 0. The only thing standing between that and a live fail-open is the producer-side vocabulary pin — and CV1 shows a bare string literal returned from a new `elif` in `check_once` passes the totality test (both derivations scan constants, not return sites), killed at HEAD only by the same incidental sweep test that killed M4 last round. This is the eighth instance of the shape R1 names: the consumer trusts a guard elsewhere instead of failing closed itself; one line (`if result.outcome in VERDICT_OUTCOMES: verdict_ticks += 1`) closes it for good. Second, on unchanged lines (R1): `cli.main` forwarding `--platform-values` (C8) and `--document` (C6) to `run_eval` survives the **full suite** — `--platform-values verdicts-only` is the per-run privacy mode over a `## Domain` sensitive surface, and nothing would notice if it were silently ignored. Third, `run_eval`'s unknown-classifier refusal returning 0 (FA7) survives the full suite; argparse `choices` covers the CLI, but `scripts/verify_document.py` and `scripts/golden_pipeline.py` call `run_eval(classifier=…)` directly.

## Answered, per the brief

- **Is `verdict_ticks = healthy_ticks − no_verdict_ticks` correct in every combination?** Numerically yes: both counters increment in the same block (L524–526), so the difference is exactly the count of completed ticks whose outcome is *not* in `NO_VERDICT_OUTCOMES`, never negative. `failed_ticks` never enters it (W5 killed). `detected_versions` non-empty ⇒ the detection arm leads (parenthetical suppressed — stamp #6 F-4, carried). `refused` / `tick_failures_exceeded` / `halted` never read it. In the `interrupted` arm a halting outcome is impossible (a halt `break`s with `end_reason="halted"`), so the complement is `VERDICT_OUTCOMES` **only while the vocabulary is closed** — see F-1: the complement also contains every outcome nobody classified.
- **Is `_OUTCOME_PHRASES` a genuinely independent derivation?** Partly. It is a different module and a display concern, so it *is* a separate hand — but it can only catch "constant added, phrase added, partition not updated" (and the cosmetic converse), which derivation 1 already catches alone (CV2′ killed by derivation 1 before derivation 2 is reached). Neither derivation sees a **return site**: CV1 (bare literal `"sweep_truncated"` from a new `elif`, no constant, no phrase) passes `test_the_outcome_partition_is_total_and_disjoint`. So the check is weaker than the commit message's "a new `elif` … returning an unclassified string" claim; the honest closure is a fail-closed consumer (F-1), optionally plus an AST pin that every `outcome =`/`TickResult(` site in `check_versions.py` names an `OUTCOME_*` constant.
- **Any vacuous-test instance left in `test_cli.py` / `test_watch.py`?** In `test_cli.py`, **no** for the guards: every required flag (sweep), the UUID guard (MB6b now killed by L97's message assert), `--version` format, blank-after-strip on `--dataset`/`--org` (C2/C3/C4 with complete argv and message asserts), `--max-documents-per-run <= 0` (C1, boundary `0` kills), the argparse exit code (C5), and the env-fallback re-introduction (MB13, behavioural, no static grep needed) all die inside `test_cli.py`. What remains is **not vacuous but absent**: no test in the repo drives `cli.main` with `--document` or `--platform-values` and observes what `run_eval` received (C6/C8 survive 1,942 tests). In `test_watch.py`, every delta mutant died to the test named for it (W1–W5), and the older pins still hold (W2 → `test_f4…`, W8 → `test_f5…`).
- **Eighth fail-open at HEAD?** **No live one** — every `TickResult` site in `check_once` (L463, L471, L651) and every raise site uses a constant inside the partition or one of the three excluded ones; I read them again. **Yes, a latent one, reproduced with injection at HEAD unmodified** (F-1): the watcher's summary is fail-open on any outcome it does not know, exit 0, headline `no new versions found`. It is defended only by tests in the *producer* module, one of which (the totality test) has a demonstrated bypass (CV1). By this story's own standard — "closing the reported case, not the invariant" — it must be closed at the consumer.
- **DEBT-131's four modules, mutated for the first time:** `run_artifact.py` 8 mutants / 6 killed (RA5 `O_NOFOLLOW`, RA7 `abort_reason`-on-complete survive; both Low); `scorer_store.py` 4 / 3 (SS2 equivalent); `version_discovery.py` 6 / 6; `facade.py` `--document`/`--classifier` 7 / 5 (FA6, FA7 survive the full suite). The gate math is solid (`run_level_gate` RA1–RA3 all die; the fail-closed direction is pinned); the unknown-classifier **exit code** is not.

## Findings

| # | Sev | File · symbol | Scenario | Suggested fix |
|---|---|---|---|---|
| **F-1** | **High** — fail-open by construction on the delta's own line | `src/idp_regression/orchestration/watch.py` · `run_watch_loop`, L606 `verdict_ticks = healthy_ticks - no_verdict_ticks` · `tests/orchestration/test_check_versions.py` · `test_the_outcome_partition_is_total_and_disjoint` | Repro (scratchpad `repro_unknown_outcome.py`, HEAD unmodified, `check_once` monkeypatched to return `TickResult("sweep_truncated", …)` ×10, Ctrl-C): `Stopped after 10 tick(s) (0m00s). no new versions found.` exit 0. The subtraction classifies every *unrecognised* outcome as a verdict. Producer-side guard bypass **CV1**: a new `elif sweep_truncated: outcome = "sweep_truncated"` (bare literal, no constant, no phrase) **survives the totality test alone**; killed in the suite only by `test_sweep_truncation_is_a_visible_signal_r1` asserting `ceiling_reached` — the same coincidence that killed M4 in stamp #6. | Count positively and fail closed: import `VERDICT_OUTCOMES`; in the tick block `if result.outcome in VERDICT_OUTCOMES: verdict_ticks += 1 elif result.outcome in NO_VERDICT_OUTCOMES: no_verdict_ticks += 1 else: no_verdict_ticks += 1` (or `logger.error` + treat as halt). Delete the subtraction. Pin with the repro above as a test (P7: see it red first). Optionally an AST test that every `outcome =` / `TickResult(` first argument in `check_versions.py` is a `Name` starting `OUTCOME_`. |
| **F-2** | **High** — sensitive-surface flag held by nothing | `src/idp_regression/orchestration/cli.py` · `main`, L455 `platform_values=args.platform_values` | **C8** `platform_values="full"` (flag ignored) **survives the full suite (1942)**. `--platform-values verdicts-only` is the per-run control `## Domain` names for keeping expected/actual values off the platform; at HEAD it is wired correctly, but an operator asking for `verdicts-only` and getting `full` would be invisible to every test. `test_cli.py`'s `_capture` helpers default `platform_values="full"` and no test passes the flag. | One test: `cli.main([… "--platform-values", "verdicts-only"])` with a capturing `run_eval` double asserting `platform_values == "verdicts-only"`; a second with the flag absent asserting `"full"`. Same for the `choices` rejection (`--platform-values nope` ⇒ 2). |
| **F-3** | **Medium** — `--document` forwarding unpinned | `cli.py` · `main`, L453 `documents=args.documents` · `facade.py` · `run_eval`, L629 `item_count = len(selected_items)` | **C6** `documents=None` (flag ignored ⇒ a `--document x` run measures and *pays for* the whole dataset) survives the full suite. **FA6** `item_count = len(dataset["items"])` (ceiling counts the dataset, not the selection — CLAUDE.md `## Commands` says the opposite) survives the full suite; fail-closed direction, so Low on its own. `select_items` itself is well pinned (FA1–FA5 all die in `test_document_filter.py`). | CLI test asserting `run_eval` receives `documents == ["a", "b"]` for `--document a --document b` and `None` when absent; a facade test with a 3-item dataset, `max_documents_per_run=1`, `documents=["one"]` ⇒ not `quota_ceiling_exceeded`. |
| **F-4** | **Medium** — pre-run refusal's exit code unpinned, reachable by direct callers | `facade.py` · `run_eval`, `except UnknownClassifierError` L668–679 | **FA7** `return 1 → return 0` survives the full suite. `UnknownClassifierError` is tested only where it is raised (`tests/classifier/test_registry.py`), never where it is *handled*. argparse `choices` shields `cli.main`, but `scripts/verify_document.py:394` and `scripts/golden_pipeline.py:344` call `run_eval(classifier=…)` directly — a typo'd or unregistered name there would be a green run that compared nothing. CLAUDE.md: "an unknown name is a pre-run refusal (zero quota), never a silent fallback" — true at HEAD, held by nothing. | Facade test: `run_eval(…, classifier="no-such")` ⇒ `1`, `"run_eval: unknown_classifier"` in caplog, `run_end outcome=aborted`, zero adapter calls. |
| F-5 | Low — stamp #6 item 3, second half not done | `watch.py` · `run_watch_loop`, `interrupted` arm | Stamp #6 asked to "decide and record the exit code for an interrupted run with zero verdicts". At HEAD it is 0 with the honest `no answer --` headline. No decision is recorded in the commit, `DEBT.md`, or ADR-0006 (which records only the `--auto-run` exit-code decision). Defensible either way; must be written down once. | One dated line in ADR-0006's implementation notes (or a DEBT row): Ctrl-C with zero verdicts ⇒ exit 0, reason. |
| F-6 | Low — survivors on DEBT-131 modules | `run_artifact.py` · `write_run_artifact` L221 `O_NOFOLLOW`; `artifact_envelope` L124 | **RA5** dropping `O_NOFOLLOW` survives `test_run_artifact.py` (no symlink test; docstring says the leg is unreachable with a uuid4 `run_id` — defensive, not equivalent). **RA7** keeping `abort_reason` on a `complete` envelope survives the full suite (shape drift only; readers ignore it). | A test planting a symlink at `artifact_path(run_id)` and asserting no write through it; an envelope test asserting `abort_reason is None` for `status="complete"` even when a reason is passed. |

**Debt for Dunga (`/debt add`):** F-3's FA6 and F-6 (RA5, RA7) if not fixed in the next delta; DEBT-131 can be **narrowed** (four modules now mutated: 25 mutants, table below) but not closed until FA7/FA6/RA5/RA7 are pinned; DEBT-130 (loop-level sweep test) re-confirmed still open — `_base_kwargs` still hard-codes `sweep_every_n_ticks=0`, and CV1 is exactly the sweep-adjacent producer path that test would exercise.

## Mutation matrix (R1: origin named; exact-string replace; `cp` restore; `shasum -a 256 -c` OK after every run; `PYTHONDONTWRITEBYTECODE=1`; `-x`)

Targets: `W` = `tests/orchestration/test_watch.py`, `CV` = `test_check_versions.py`, `CLI` = `test_cli.py`, `ORCH` = `tests/orchestration` (432), `ALL` = full suite (1942), `TOT` = the totality test alone.

| # | File · mutant | Invariant / origin | Diff line? | Target | Result |
|---|---|---|---|---|---|
| W1 | `watch.py` `verdict_ticks = healthy_ticks` (regress #6 F-3) | headline on answered ticks | yes | W | KILLED (`test_f1_ceiling_reached…`) — the P7 red for F-3 |
| W2 | `if verdict_ticks > 0` → `>= 0` | same | yes | W | KILLED (`test_f4_ctrl_c_after_only_failed_ticks…`) |
| W3 | else-phrase `unanswered_ticks` → `failed_ticks` (regress) | same | yes | W | KILLED (`test_f1…`) |
| W4 | parenthetical `if unanswered_ticks > 0` → `if False` | mixed-run qualifier | yes | W | KILLED (`test_f3_a_partial_verdict_run…`) |
| W5 | `verdict_ticks = healthy_ticks - failed_ticks` (wrong subtrahend) | same | yes | W | KILLED (`test_f1…`) |
| W6 | `_RUN_VERSION_PATTERN` never matches (detection ⇒ empty `detected_versions`) | a detection always leads | no | W | KILLED (`test_detection_prints_an_unmissable_block…`) |
| W7 | `_OUTCOME_PHRASES` drops `ceiling_reached` | derivation 2 | no | ORCH | KILLED (totality test) |
| W8 | parenthetical `{iteration}` → `{healthy_ticks}` | denominator is attempts | no | W | KILLED (`test_f5…`) |
| **CV1** | `check_versions.py` new `elif sweep_truncated: outcome = "sweep_truncated"` (bare literal, no constant) | totality: "a new elif returning an unclassified string fails" | no | TOT | **SURVIVED → F-1** |
| CV1b | same | same | no | ORCH | KILLED — only by `test_sweep_truncation_is_a_visible_signal_r1` (coincidence, as M4 was) |
| CV2′ | new `OUTCOME_SWEEP_TRUNCATED` constant + `elif`, unclassified, no phrase | derivation 1 | no | TOT | KILLED (derivation 1) — #6 F-2 closed |
| CV3 | `NO_VERDICT_OUTCOMES` drops `CEILING_REACHED` | bucket membership | no | TOT | KILLED |
| MB8 | `cli.py` `--action required=True → False` + valid default | A8 (#6 F-1) | no | CLI | KILLED (`…exits_2[--action]`) |
| MB11 | `--run required → False` + default | parser contract (#6 F-6) | no | CLI | KILLED (`…exits_2[--run]`) |
| MB6b | UUID guard → `if False` | A8 (#6 F-1) | no | CLI | KILLED (`test_malformed_action_id…[not-a-uuid]`, message assert) |
| MB13 | `--action` default from `os.environ["IDP_ACTION_ID"]` | A8/A9, behavioural (no static grep) | no | CLI | KILLED (`…exits_2[--action]`) |
| C1 | `max_documents_per_run <= 0` → `< 0` | A10 boundary | no | CLI | KILLED (`[0]`) |
| C2 | `--dataset` blank guard deleted | N6 | no | CLI | KILLED |
| C3 | `--org` blank guard deleted | N6 | no | CLI | KILLED |
| C4 | `--dataset` `.strip()` dropped | N6 whitespace | no | CLI | KILLED (`[   ]`) |
| C5 | argparse failure `return 2` → `1` | exit-code contract | no | CLI | KILLED |
| **C6** | `documents=args.documents` → `None` | `--document` is run identity | no | ORCH, **ALL** | **SURVIVED (both) → F-3** |
| C7 | `classifier=args.classifier` → `None` | `--classifier` is run identity | no | ORCH | KILLED (`test_cli_custom_scorers`) |
| **C8** | `platform_values=args.platform_values` → `"full"` | `## Domain` privacy mode | no | ORCH, **ALL** | **SURVIVED (both) → F-2** |
| RA1 | `run_artifact.py` `run_level_gate` FAIL check removed | INV-08 fail-closed | no | ALL | KILLED |
| RA2 | `status == "complete"` → `!= "aborted"` (legacy ⇒ PASS) | DEBT-91 | no | ALL | KILLED |
| RA3 | empty gates ⇒ PASS | DEBT-91 | no | ALL | KILLED |
| RA4 | `run_id` containment check removed | path containment | no | `test_run_artifact` | KILLED |
| RA5 | `O_NOFOLLOW` dropped | symlink refusal | no | `test_run_artifact` | SURVIVED → F-6 (Low; not equivalent) |
| RA6 | `_FILE_MODE` 0o600 → 0o644 | owner-only | no | `test_run_artifact` | KILLED |
| RA7 | `abort_reason` kept on `complete` | envelope shape | no | ALL | SURVIVED → F-6 (Low) |
| RA8 | `parse_run_artifact` accepts unknown status | reader fail-closed | no | ALL | KILLED |
| SS1 | `scorer_store.py` `verify_monotone` result ignored on load | monotone-by-construction | no | ALL | KILLED |
| SS2 | `spec_path` parent check removed | traversal | no | ALL | SURVIVED — **equivalent** (`NAME_PATTERN` `^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$` admits no `.`/`/`; the check is unreachable belt-and-braces) |
| SS3 | load error swallowed without a message | "named, never silent" | no | ALL | KILLED |
| SS4 | spec not added to `CLASSIFIERS` | registration | no | ALL | KILLED |
| VD1 | `version_discovery.py` `truncated` never set | partial list says so | no | ALL | KILLED |
| VD2 | `rate_limited` never set | §A′.4 | no | ALL | KILLED |
| VD3 | `UNKNOWN` dropped (rendered absent) | never render unknown as absent | no | ALL | KILLED |
| VD4 | budget `>=` → `>` | probe ceiling | no | ALL | KILLED |
| VD5 | anchor not probed first | anchor first, always | no | ALL | KILLED |
| VD6 | 429 ⇒ `continue` | abandon on 429 | no | ALL | KILLED |
| FA1 | `facade.py` ambiguous selector picks first | refuse, never guess | no | ORCH | KILLED |
| FA2 | unmatched selector ignored | never measure nothing | no | ORCH | KILLED |
| FA3 | `exact=True` falls back to substring | DEBT-117 | no | ORCH | KILLED |
| FA4 | selection error `return 0` | CT-04 | no | ORCH | KILLED |
| FA5 | selector order, not dataset order | run sequence | no | ORCH | KILLED |
| **FA6** | ceiling counts dataset, not selection | `## Commands` claim | no | ORCH, **ALL** | **SURVIVED (both) → F-3** (fail-closed direction, Low alone) |
| **FA7** | unknown classifier `return 0` | "pre-run refusal, never a silent fallback" | no | ORCH, **ALL** | **SURVIVED (both) → F-4** |

**Totals:** 51 mutant runs over 7 files; 42 killed, 9 surviving — **1 equivalent** (SS2), 8 non-equivalent (CV1, C6, C8, RA5, RA7, FA6, FA7, plus CV1b's incidental kill counted as a survivor of the guard it tests). 43 of 51 sit on lines `04e7a22` did not change (R1). One malformed mutant (CV2, referenced an undefined name) was discarded and re-run as CV2′. Repro `repro_unknown_outcome.py` is not a mutant: HEAD unmodified, `check_once` replaced at the seam `test_f3…` already uses.

## Standing gaps — re-checked at `04e7a22`

1–4 from stamp #4 (`--auto-run` POC override; no backoff; no heartbeat; nothing under a loaded `launchd` schedule) — still true. 5 = **DEBT-130** (no loop-level sweep test) — still true. 6 = **DEBT-131** — **now mutated** (25 mutants above); narrow the row to FA6/FA7/RA5/RA7. 7 (`_ZERO_EXIT_OUTCOMES` treats the two no-verdict outcomes differently in the one-shot CLI) — unchanged, for Soneca. **DEBT-129** (persistent `ceiling_reached` never escalates) — filed as promised; the code is unchanged, the summary is honest (W1/W3).

## Freshness (P5, DEBT-72/77) — explicit file list pinned to `04e7a22`

`git ls-files src/idp_regression/orchestration` = 14 files = on-disk `*.py` count (14); `git diff --diff-filter=A --name-only 34dfe14..HEAD -- src tests` **empty** (no files added since stamp #6); `git status --short src tests` empty. Blob ids are HEAD's.

| File (HEAD blob) | `34dfe14..HEAD` |
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
| `watch.py` (064fdb24) | +14 −3 |

Tests changed in the delta: `tests/orchestration/test_check_versions.py` (+42), `tests/orchestration/test_cli.py` (+76 −13), `tests/orchestration/test_watch.py` (+63 −7). **Ordering (P7):** tests and fix landed in one commit; W1 (the pre-fix code shape) is red under the new `test_f1…`, and CV2′/CV3 are red under the rewritten totality test, so each fix has a demonstrated RED.

## What re-stamp #8 must check

1. **F-1:** `verdict_ticks` counted positively from `VERDICT_OUTCOMES`; the injected-outcome repro is a test and was seen red; CV1 dies to the totality test alone (AST pin) or the consumer test — either closes the class.
2. **F-2 / F-3:** C8, C6 and FA6 killed by `test_cli.py` / `test_facade.py` respectively.
3. **F-4:** FA7 killed by `test_facade.py`.
4. F-5 recorded; F-6 fixed or in `DEBT.md`; DEBT-131 narrowed; DEBT-130 still open unless a loop-level sweep test landed.

## History

| Commit | Verdict | Findings |
|---|---|---|
| `cfd2bd7` (2026-09-22) | ✅ PASSED | superseded; stale within a day (DEBT-72: `orchestration/` moved ~7 files, +1626/−78, two new modules) |
| `a5805ec` | ❌ FAILED | **F-1** fail-open #4 (a watcher whose every tick failed reported "no new versions found", exit 0, unbounded) · **F-2** INV-02 raw traceback carrying filesystem paths · **F-4** a vacuous test (mutating its double `return 1`→`return 0` left the file 23/23 green) · F-3 recorded |
| `f3b0b65` | ❌ FAILED | F-3, F-4 **closed and verified**. **F-2 NOT closed** — a third state-file I/O site the fix never enumerated, reproduced live. **F-1 partially closed** — its own fix introduced **F-5** (fail-open #5: mixed failing/healthy ticks defeat both the ceiling and the honest summary), **F-6** (a surviving mutant: deleting the consecutive-failure reset left the whole orchestration suite green — "consecutive" was pinned by nothing), **F-7** (a tick-1 halt also printed "no new versions found") |
| `8a01c62` (2026-09-28, re-stamp #4, stamp committed as `79315d5`) | ❌ FAILED | F-2/F-5/F-6/F-7 of `f3b0b65` **closed and mutant-verified** (M3–M8). **F-1** fail-open #6 (interleaved `indeterminate` ticks read as healthy; 30-of-40 ambiguous → "no new versions found.", exit 0) · **F-2** `if auto_run:` quota guard unpinned by 626 tests; the test named for it is vacuous · **F-3** the `AssertionError`-sentinel class in `test_cli.py` — three A8/A10 guards deletable green · F-4 summary boundary unpinned · F-5 vacuous dotenv test · F-6 P7 ordering |
| `1e931fe` (2026-09-28, re-stamp #5, stamp committed as `3abfb25`) | ❌ FAILED | F-1..F-5 of `8a01c62` **closed and mutant-verified** (MA1–MA6, MB1–MB3, MB7, MC1–MC5); F-6 filed as DEBT-128. **F-1** fail-open #7 (`ceiling_reached` is a no-verdict outcome that outranks and resets `indeterminate`; CLI defaults + legal lookahead → "no new versions found (1 of 40 …)", exit 0; MA12 survives) · **F-2** `healthy_ticks > 0` branches on completed, not answered, ticks (40/40 indeterminate → headline "no new versions found", exit 0) · **F-3** two more vacuous-sentinel tests (MB4 64-cap, MB5 `--version required`) · F-4 counts omitted on the non-fail-open arms |
| `34dfe14` (2026-09-28, re-stamp #6, stamp committed as `6f0a89f`) | ❌ **FAILED** | F-1 (fail-open #7) and F-3 of `1e931fe` **closed and mutant-verified** (M1–M3, M8, M9, M11, MB4, MB5). **F-1** the vacuous-sentinel class is still open — `--action required` deletable green across the full suite (MB8), two more vacuous tests plus one held only incidentally (MB6b) · **F-2** the totality test is tautological (`ALL_TICK_OUTCOMES` is the union it is compared to; M4b survives) — the commit's central guarantee does not hold · **F-3** stamp #5's F-2 untouched and now pinned as expected behaviour by the delta's own test (M10) · F-4 carried · F-5 the "recorded as DEBT" gap has no DEBT row · F-6 `--run required` unpinned (MB11) |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

| `04e7a22` (2026-09-28, this stamp, re-stamp #7) | ❌ **FAILED** | F-1, F-2, F-3 of `34dfe14` **closed and mutant-verified** (W1–W5, CV2′, CV3, MB8, MB11, MB6b, MB13); DEBT-129/130/131 filed as promised; DEBT-131's four modules mutated for the first time (25 mutants, 21 killed). **F-1** `verdict_ticks` by subtraction is fail-open on any unclassified outcome (injected repro: `no new versions found.` exit 0) and the totality guard it leans on has a bare-literal bypass (CV1) · **F-2** `--platform-values` CLI forwarding survives the full suite (C8) · F-3 `--document` forwarding (C6) and selection-count ceiling (FA6) unpinned · F-4 unknown-classifier refusal exit code unpinned (FA7), reachable from two scripts · F-5 exit-code decision not recorded · F-6 RA5/RA7 |

> ⚠️ Record correction (2026-09-24), preserved: this file read `✅ PASSED` (from `cfd2bd7`, 2026-09-22) while the gate had failed twice — at `a5805ec` and `f3b0b65`. The register said PASSED for roughly a day while the gate said FAILED (DEBT-54's own defect class). Recorded rather than quietly overwritten.

**DEBT-44 / P6:** commit this FAILED stamp before the fix. Re-stamp #8 must be run by an instance that issued no APPROVE on the fix and is not this instance, on a model other than the implementer's.

— Atchim [ATCHIM!!] …excuse me.
