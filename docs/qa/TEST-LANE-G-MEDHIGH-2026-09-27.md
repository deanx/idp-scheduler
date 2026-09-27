# TEST stamp — Lane G, Med-High rows (DEBT-94, 95, 96, 97, 98)

**Range:** `fd4cbbd..26df510` (5 commits: `d857013` DEBT-94 · `bd781c8` DEBT-95 · `1a6b1d9` DEBT-96 ·
`423b80c` DEBT-97 · `26df510` DEBT-98)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**. Delta is `scripts/` + tests
only (no `src/` edit — DEBT-96's console half reuses `show_run._leaves` through `ui/reader.py`, unchanged).
Under `## Rigor` this is the docs/tests-only *reviewer-pass* relief only; the `/test` stamp is required by
the SPEC header and is run at `full` regardless.
**Verdict:** ✅ **PASS WITH FINDINGS** — 6 non-blocking findings, every one a test gap over correct shipped
code; no fail-open is in the tree. Verified by reading: every survivor below was checked against the source
and none changes the shipped behaviour — they show what a future edit could break unnoticed.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and never saw the implementer's
mutants. **DEBT-44 honoured.** All 37 mutants are the gate's own, aimed at each row's invariant rather
than the implementer's lines; each was applied to the working file, run, and restored byte-identically from
a scratchpad copy (`shasum -a 256` verified for all 8 files; `git status --short` shows only the
pre-existing `docs/state/STATE.json` change; `__pycache__` outside `.venv`/`frontend` deleted). No live
IDP/platform call; `.env` never sourced.

## Gates (measured at `26df510`)

| Gate | Result |
|---|---|
| `pytest -q` (full) | **1739 passed / 15 skipped** (opt-in integration), 47.9 s |
| `mypy src tests` (strict) | Success — no issues, 135 files |
| `ruff check src tests scripts` | All checks passed |
| `gitleaks detect --log-opts="fd4cbbd..HEAD"` | no leaks |

Suites: TOOL = `tests/tooling/test_batch_tools.py` (183), UI = `tests/ui` (+ `test_reader.py`).

## Mutation matrix — 23 killed / 14 survived

| # | Mutant (invariant attacked) | Result | Killed by |
|---|---|---|---|
| M01 | `bootstrap --resume` keyed on the capture again (DEBT-94's exact bug) | killed | `test_bootstrap_resume_redrafts_a_failed_draft_from_its_capture_for_free` |
| M02 | redraft path re-extracts instead of reading the capture | killed | same (`calls == []`) |
| **M03** | cost preview counts the free redrafts as extractions | **SURVIVED** | — → F-3 |
| M04 | `golden_pipeline` ignores draft rc when a golden exists | killed | `test_pipeline_stops_on_a_partial_draft_that_still_wrote_a_golden` |
| M05 | pipeline stops but returns 0 | killed | same |
| M06 | `draft_exit_code` not recorded on the stop path | killed | same |
| **M07** | resume's "drafted" set keyed on golden key, not `document_id` (nothing filtered; every drafted entry re-drafted and overwritten) | **SURVIVED** | — → F-3 |
| M08 | `calibrate` fields: assign corpus decision (promotes) | killed | `test_calibrate_never_marks_a_documents_empty_value_critical` |
| M09 | `calibrate` fields: OR instead of AND | killed | `test_calibrate_demotes_a_field_the_floor_says_is_unstable` (pre-existing) |
| M10 | `calibrate` tables: assign (promotes) | killed | `test_calibrate_keeps_a_human_demotion_of_a_table` |
| M11 | `calibrate` tables: OR | killed | `test_calibrate_demotes_a_table_with_an_unstable_column` (pre-existing) |
| **M12** | `calibrate` fields: absent `critical` defaults to True (promotes a schema-optional, classifier-False field) | **SURVIVED** | — → F-5 |
| M13 | `show_run._leaves`: row leaves carry no `critical` (the DEBT-96 bug) | killed | `test_show_run_never_calls_a_table_regression_noise` |
| **M14** | row leaves inherit the block's `format_critical` | **SURVIVED** | — → F-4 |
| M15 | row leaves always critical | killed | `test_a_non_critical_tables_difference_is_not_flagged_as_failing` |
| **M16** | row leaves `format_critical: True` | **SURVIVED** | — → F-4 |
| M17 | `provisioned` recorded `True` unconditionally | killed | `test_a_failed_provisioning_is_reported_and_retried_without_re_extracting` |
| M18 | re-provisioning loop skipped | killed | same |
| M19 | re-provisioning failure exits 0 (`already_pinned` path) | killed | `test_a_failing_re_provisioning_keeps_the_run_red` |
| **M20** | legacy pin (no `provisioned` key) assumed provisioned | **SURVIVED** | — → F-2 |
| M21 | `_pin_one` returns a success record despite rc ≠ 0 | killed | `test_a_failed_provisioning_is_reported_and_retried_without_re_extracting` |
| **M22** | re-provisioning failures dropped when the batch also has new documents (`failures = []` before the todo loop) | **SURVIVED** | — → F-2 |
| M23 | `provisioned` not persisted to `_pins.json` | killed | same as M21 (third run must touch nothing) |
| M24 | re-provisioning success recorded `False` | killed | same |
| M25 | `run_outcome`: no artifact → CHANGED | killed | `test_run_outcome_calls_only_a_recorded_failure_changed` |
| M26 | `run_outcome`: `since` ignored | killed | `test_run_outcome_ignores_an_artifact_older_than_this_run` |
| **M27** | `run_outcome`: oldest candidate chosen | **SURVIVED** | — accepted (concurrent runs only; docstring concedes) |
| M28 | `run_outcome`: INCOMPLETE → CHANGED | killed | `test_run_outcome_calls_only_a_recorded_failure_changed` |
| M29 | `run_outcome`: abort checked before FAIL (a real failure in an aborted run reads RUN FAILED) | killed | `test_run_outcome_names_an_abort_after_a_real_failure` |
| **M30** | `run_outcome`: unreadable artifact → CHANGED | **SURVIVED** | — → F-6 |
| **M31** | `run_outcome`: uncomputable document → PASS instead of UNKNOWN | **SURVIVED** | — equivalent for the verdict (both branches reach RUN FAILED) |
| M32 | `run_outcome`: rc-0 shortcut removed | killed | `test_run_outcome_calls_only_a_recorded_failure_changed` |
| **M33** | `compare_versions.main` reverts to `"CHANGED" if rc else "STILL VALID"` | **SURVIVED** (386 TOOL+UI) | — → F-1 |
| **M34** | `verify_document.main` reverts to the old mapping | **SURVIVED** | — → F-1 |
| M35 | `verify_document.run`: `started` line deleted | killed (NameError only) | `test_verify_narrows_the_run_to_the_one_pinned_file` — a crash kill, so M35b was run |
| **M35b** | `verify_document.run`: `started` stamped **after** `run_eval` (every real CHANGED reads RUN FAILED) | **SURVIVED** | — → F-1 |
| **M36** | `compare_versions.run`: `verify_started` stamped after `verify.main` | **SURVIVED** | — → F-1 |

## Per-row criterion coverage

| Row | Closure claim | Backed by |
|---|---|---|
| DEBT-94 | partial draft stops the pipeline; `--resume` retries a failed draft free | M01, M02, M04–M06 (5 killed); resume leaves drafted entries alone — untested (F-3) |
| DEBT-95 | calibration only ever demotes, fields and tables | M08–M11 (4 killed); absent-`critical` edge untested (F-5) |
| DEBT-96 | table rows inherit the block's `critical`, never `format_critical` | M13, M15 (2 killed, incl. console via `test_reader.py`); the "never `format_critical`" half untested (F-4) |
| DEBT-97 | `provisioned` recorded; failure is a failure; retry is free | M17–M19, M21, M23, M24 (6 killed); legacy pins and mixed batches untested (F-2) |
| DEBT-98 | CHANGED only with a recorded failing document | M25, M26, M28, M29, M32 (5 killed) on `run_outcome`; **the two `main`s that wire it are untested** (F-1) |

Also confirmed: the literal `\n` in `compare_versions.main`'s closing hint (noted in the High stamp) is
fixed in this delta.

## Findings (all non-blocking; shipped code is correct in every case — the gap is the test)

- **F-1 (Med)** — `scripts/compare_versions.py::main`, `::run`; `scripts/verify_document.py::main`, `::run`.
  M33/M34/M35b/M36 survive: `run_outcome` is well tested but nothing drives either `main` through the
  verdict print, so the DEBT-98 fix can be unwired (back to `"CHANGED" if rc`) or the `since` stamp moved
  after the run (every real regression then prints `RUN FAILED`) with 386 tests green. This is the row's
  own symptom location. Fix: one test per script that calls `main` with a stubbed `_load`/`run_eval`
  writing an artifact under `tmp_path` (chdir, since `run_outcome` reads `ARTIFACT_DIR_NAME` relative to
  cwd) and asserts the banner verdict for rc 1 + failing artifact (`CHANGED`), rc 1 + no artifact
  (`RUN FAILED`), and that `summary["started"]`/`["verify_started"]` precede the artifact's mtime.
- **F-2 (Med)** — `scripts/pin_document.py::run`, re-provisioning block. M20: a `_pins.json` entry with no
  `provisioned` key (every pin written before this delta) is the case the code comment says is included,
  and dropping it passes. M22: a batch mixing one previously-failed provisioning with one new document
  loses the re-provisioning failure (`failures` reset) and exits 0 — exactly DEBT-97's "re-run reports
  success" in a new shape. Fix: (a) write a legacy `_pins.json` without the key and assert
  `reprovisioned == [doc]`; (b) fail-then-add-a-file, second run with a failing provision stub, assert rc 1
  and the failure named.
- **F-3 (Low-Med)** — `scripts/bootstrap_golden_set.py::run`, `--resume`. M07 survives: keying `drafted`
  on the golden key instead of `document_id` filters nothing, so every already-drafted document is
  re-drafted from its capture and `golden_set[key] = entry` overwrites it — a hand reconciliation in
  `golden.json` is undone on the next `--resume` (the DEBT-95 failure mode, moved one script left). M03:
  the cost preview counts free redrafts as paid extractions (over-states, so conservative). Fix: in the
  resume test, hand-edit `doc-001`'s entry between runs and assert it is untouched; assert the printed
  extraction count is `to extract`, not `to go`.
- **F-4 (Low-Med)** — `scripts/show_run.py::_leaves`. M14/M16 survive: the docstring's "never a
  `format_critical`" has no test, so a table `wrong_format` row can be flagged `<-- FAILS THE GATE` and
  `gate_failing: true` in the console while `overall_gate` (which ignores table `wrong_format`) passes the
  document — the inverse of DEBT-96, a diagnostic that over-claims. Fix: `_table("wrong_format")` with
  `format_critical: True` on the block → no leaf `_fails`.
- **F-5 (Low)** — `scripts/calibrate_golden.py::calibrate`. M12 survives: `critical` is optional in
  `golden_schema_v1.json` and `gate.py` reads an absent key as `False`, so defaulting it to `True` in the
  AND promotes a hand-authored non-critical field. Drafts always write the key, so only hand-authored
  goldens hit it. Fix: one entry with the key absent, assert it calibrates to `False`.
- **F-6 (Low)** — `scripts/_batch.py::run_outcome`. M30 survives: a corrupt/truncated artifact reads
  `CHANGED` under the mutant (the model blamed for a plumbing failure — DEBT-98's own complaint). Fix:
  write non-JSON into the artifact dir, assert `RUN FAILED` / "could not be read".

Accepted survivors: M27 (newest-vs-oldest only differs under two concurrent runs in one directory; the
console serialises jobs and the docstring records the CLI caveat) and M31 (equivalent for the verdict).

## Debt to record (Dunga → `/debt add`)
F-1 … F-6 above, as test-gap rows against DEBT-98 (F-1, F-6), DEBT-97 (F-2), DEBT-94 (F-3), DEBT-96 (F-4),
DEBT-95 (F-5). None reopens its parent row.

— Atchim (fresh instance, Fable 5.1) · 2026-09-27

---

## Addendum — findings closed (implementer, same day)

One test per finding, each re-run by the implementer against the gate's own mutant text; every named
survivor now dies (`1 failed` or more each):

| Finding | Mutants now killed | Test |
|---|---|---|
| F-1 | M33, M34, M35b, M36 | `test_verify_main_banner_follows_the_artifact_not_the_exit_code`, `test_compare_main_banner_follows_the_artifact_not_the_exit_code` |
| F-2 | M20, M22 | `test_a_legacy_pin_without_a_provisioned_record_is_re_sent`, `test_a_failed_reprovisioning_is_not_lost_in_a_batch_with_new_documents` |
| F-3 | M07 | `test_bootstrap_resume_never_rewrites_an_entry_it_already_drafted` |
| F-4 | M16 (M14 is the same leaf) | `test_a_table_wrong_format_never_reads_as_gate_failing` |
| F-5 | M12 | `test_calibrate_reads_an_absent_critical_as_false` |
| F-6 | M30 | `test_run_outcome_calls_an_unreadable_artifact_a_failed_run` |

Still accepted, as recorded above: M03 (conservative over-count in a cost preview), M27 (artifact
choice under concurrent runs — conceded in `run_outcome`'s docstring), M31 (equivalent for the verdict).
No production code changed: F-2's mixed-batch case was a test gap, the code already accumulated the
failure.
