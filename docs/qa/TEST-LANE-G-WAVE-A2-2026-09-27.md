# TEST stamp — Lane G, wave A2 (DEBT-101, 103, 104, 106(a), 107, 108, 109, 110, 111, 112, 113, 115)

**Range:** `771a661..HEAD` (11 commits, HEAD `94e4edc`: `f808bcf` DEBT-103 · `1615aab` DEBT-104 · `461ea57`
DEBT-101 · `a8949ed` DEBT-108/112 · `630aeb5` DEBT-107 · `c3e71ef` DEBT-109 · `738cd29` DEBT-111 ·
`cae2b26` DEBT-113 · `fac83ae` DEBT-106(a) · `8d614e1` DEBT-110 · `94e4edc` DEBT-115)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**. `f808bcf` touches `src/`
(`classifier/gate.py::_validate_declared_date_format`, `platform/schema/golden_schema_v1.json`) on the
verdict path, so under `## Rigor` nothing is lightened: this `/test` stamp runs at `full`, fresh instance,
own mutation matrix, with the `src/` commit taken first.
**Verdict:** ✅ **PASS WITH FINDINGS** — 4 non-blocking test-gap findings over correct shipped code, plus
2 accepted-unobservable survivors. No fail-open is in the tree: every refusal this delta adds (capture_raw
`--yes`, mixed-org verify, duplicate `document_id`, undeclared/inert `date_format`) fires before quota is
spent or anything is written, and `show_run`'s exit code is the whole run's again.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and never saw the implementer's
mutants. **DEBT-44 honoured.** All 49 mutants are the gate's own, aimed at each row's invariant; each was
applied to the working file after a scratchpad copy was taken, run, and restored byte-identically
(sha256 compared before/after for all 12 mutated files; `git status --short` shows only the pre-existing
`docs/state/STATE.json` change; `__pycache__` outside `.venv`/`frontend` deleted). No live IDP/platform
call; `.env` never sourced; no production or test file edited.

## Gates (measured at `94e4edc`)

| Gate | Result |
|---|---|
| `pytest -q` (full) | **1817 passed / 15 skipped** (opt-in integration), 37.1 s |
| `mypy src tests scripts` (strict) | Success — no issues, 149 files (DEBT-115's widened scope, verified) |
| `ruff check src tests scripts` | All checks passed |
| `gitleaks detect --log-opts="771a661..HEAD"` | no leaks |
| CT-05 (`test_minified_schema_under_10000_chars`) | passes with the two new schema rules |

Suites: CLS = `tests/classifier` + `tests/platform/test_golden_schema_contract.py`; TOOL =
`tests/tooling/test_batch_tools.py`; RES = `tests/tooling/test_scripts_resilience.py`.

## Mutation matrix — 43 killed / 6 survived

| # | Mutant (invariant attacked) | Result | Killed by |
|---|---|---|---|
| **DEBT-103** (`src/`, Risk: high — first) | | | |
| M01 | schema: `not` clause dropped (ISO forced even with `date_format`) | killed | `test_the_schema_accepts_a_date_written_in_its_declared_format` |
| M02 | schema: `not` inverted (ISO forced only WHEN `date_format` declared) | killed | same |
| M03 | schema: new `date_format ⇒ type:date` rule removed | killed | `test_the_schema_refuses_a_date_format_on_a_non_date_field` |
| M04 | schema: new rule demands `type:text` | killed | `test_the_schema_accepts_a_date_written_in_its_declared_format` |
| M05 | gate: `_validate_declared_date_format` call removed from `_validate_golden` | killed | `test_the_classifier_refuses_a_date_format_that_cannot_do_anything[spec0]` |
| M06 | gate: type check removed | killed | same `[spec0]` |
| M07 | gate: blank/non-string check removed | killed | `test_a_blank_date_format_is_refused_even_on_an_empty_value` |
| M08 | gate: parse check removed | killed | `…cannot_do_anything[spec1]` |
| M09 | gate: validator parses the wrong key (`expected` instead of `value`) | killed | same `[spec1]` |
| M10 | gate: INV-02 echo — value and format in the message | killed | same `[spec1]` (`"03/04/2024" not in str(exc)`) |
| **M11** | gate: validator parses the unstripped value (`canonical` strips) | **SURVIVED** | — → F-1 |
| M12 | gate: non-string/blank format returns instead of raising | killed | `…cannot_do_anything[spec2]` |
| **DEBT-104** | | | |
| M13 | majority vote replaced by alphabetical | killed | `test_calibrate_unifies_a_table_columns_type_across_the_corpus` |
| M14 | unified types reported, never written to the blocks | killed | same |
| M15 | a document with no `types` block left un-unified | killed | same |
| M16 | agreed columns reported too | killed | `test_an_agreed_column_type_is_not_reported` |
| M17 | 2-way disagreement counted as agreed | killed | `test_calibrate_unifies_…` |
| M18 | votes not counted (presence only) | killed | same |
| **DEBT-101** | | | |
| M19 | pooled "within the floor" verdict restored | killed | `test_show_run_never_prints_a_pooled_reassurance_above_an_above_floor_field` |
| M20 | `above` count zeroed | killed | same |
| **M21** | "N field(s) cannot be judged against it" note dropped | **SURVIVED** | — → F-2 |
| **DEBT-108** | | | |
| M22 | `--yes` refusal removed | killed | `test_capture_raw_spends_nothing_without_yes` |
| M23 | refusal moved after the adapter is built | killed | same |
| **DEBT-112** | | | |
| M24 | `new_table` dropped from the totals line | killed | `test_show_run_counts_a_new_table_as_new` |
| **M25** | `new_table` dropped from the per-document row | **SURVIVED** | — → F-3 |
| **DEBT-107** | | | |
| M26 | capture reused for changed bytes | killed | `test_pin_re_extracts_when_the_bytes_changed_or_repin_is_asked` |
| M27 | capture reused under `--repin` | killed | same |
| M28 | digest never recorded (retry re-pays) | killed | `test_pin_retries_a_failed_draft_from_its_capture_for_free` |
| **M29** | reuse without checking the capture file exists | **SURVIVED** | — accepted (F-5) |
| M30 | digest recorded as the NAME (any bytes reuse) | killed | `test_pin_retries_a_failed_draft…` |
| **DEBT-109** | | | |
| M31 | `extractions_spent` = plan again | killed | `test_noise_floor_reports_the_extractions_it_attempted_not_planned` |
| M32 | reference attempts not counted | killed | same |
| M33 | repeat attempts not counted | killed | same |
| M34 | `--org` precedence inverted (pin's org wins) | killed | `test_verify_refuses_pins_from_several_orgs_unless_one_is_named` |
| M35 | mixed-org refusal removed | killed | same |
| M36 | single-org pin refused without `--org` (over-refusal) | killed | `test_verify_narrows_the_run_to_the_one_pinned_file` |
| **DEBT-111** | | | |
| M38 | exit code from the subset gate | killed | `test_show_run_document_filter_narrows_the_view_never_the_gate` |
| M39 | whole-run gate computed from the subset | killed | same |
| M40 | `SHOWN` prints the whole-run gate (lines swapped) | killed | same |
| M41 | pipeline: time bound ignored (`newest_artifact_since(0)`) | killed | `test_pipeline_never_shows_a_previous_runs_artifact` |
| M42 | pipeline: no artifact → old newest-in-dir fallback | killed | same |
| **M43** | `_batch.newest_artifact_since` returns the OLDEST since | **SURVIVED** (TOOL + `tests/ui`) | — → F-4 |
| **DEBT-113** | | | |
| M44 | PID dropped from `RUN_KEY` | killed | `test_two_runs_started_in_the_same_second_keep_separate_files` |
| **M45** | `STATUS_FILE` keyed by `TS` only (log/marker keep the PID) | **SURVIVED** | — accepted (F-5) |
| **DEBT-106(a)** | | | |
| M46 | duplicate-`document_id` check removed | killed | `test_provision_refuses_two_entries_naming_one_document` |
| M47 | collision reported but provisioning proceeds (`return 1` dropped) | killed | same (`item_posts == []`) |
| M48 | message echoes the entry (INV-02) | killed | same (`"87.48" not in err`) |
| M49 | collision keyed by golden key (never fires) | killed | same |
| **DEBT-110** (real wiring) | | | |
| M50 | `verify_document` passes `exact_documents=False` | killed | `test_verify_asks_run_eval_for_exact_document_matches` (+ e2e) |

DEBT-115 has no runtime mutant: its closure is the gate itself — `mypy src tests scripts` clean at 149
files, `pyproject.toml [tool.mypy] files` and both CI commands read the same three directories, and
`SupportsExtract.extract` now returns `NormalizedOutput` (typed through `extract_with_containment` and
`draft_golden._draft_from_normalized`). Note: nothing asserts `pyproject`'s `files` mirrors the CI
command (the DEBT-33 invariant); a drift there is a config regression no test would catch — Low, below.

## Per-row criterion coverage

| Row | Closure claim | Backed by |
|---|---|---|
| DEBT-103 | ISO only for an undeclared `date`; `date_format` only on `date`; classifier refuses a format that cannot act (wrong type, blank, value that does not parse); INV-02 kept | M01–M10, M12 (11 killed); whitespace edge untested (F-1). Probe: `calibrate`'s DEBT-92 re-validation keeps a demoted `date`+`date_format` field on `date` under the new rule — no 103/104 interaction |
| DEBT-104 | one `type` per table column, majority, minority reported, no-vote documents still unified | M13–M18 (6 killed) |
| DEBT-101 | no pooled verdict; per-field `above` count only | M19–M20 (2 killed); the "cannot be judged" branch untested (F-2) |
| DEBT-108 | `capture_raw` spends nothing without `--yes`, refusal before the adapter | M22–M23 (2 killed) |
| DEBT-112 | `new_table` counted as new | M24 (killed); per-document row untested (F-3) |
| DEBT-107 | capture reused only for the same bytes, never under `--repin`, digest persisted | M26–M28, M30 (4 killed) |
| DEBT-109 | attempted count, not plan; mixed-org pins refused before spending; `--org` wins | M31–M36 (6 killed) |
| DEBT-111 | `--document` narrows the view, never the gate/exit code; pipeline shows ITS run or nothing | M38–M42 (5 killed); `newest_artifact_since` two-candidate ordering untested (F-4) |
| DEBT-113 | same-second runs keep separate files | M44 (killed); status-file race window unobservable dynamically (F-5) |
| DEBT-106(a) | duplicate `document_id` refused before any write, keys named, no value | M46–M49 (4 killed) |
| DEBT-110 | real pin → verify wiring drives the e2e test | M50 (killed) + the e2e tests exercised by M26/M28/M30 through `_RealHalf` |
| DEBT-115 | scripts under mypy/ruff locally and in CI | gate row above |

## Findings (all non-blocking; shipped code is correct in every case — the gap is the test)

- **F-1 (Low)** — `src/idp_regression/classifier/gate.py::_validate_declared_date_format`. M11 survives:
  the validator strips the value before `strptime` exactly as `canonical._format_date_with` does, but no
  test has a value with surrounding whitespace, so a validator that refuses `" 03/04/2024 "` while the
  comparison would accept it goes unnoticed. Fix: one parametrised case with a padded value → no raise.
- **F-2 (Low)** — `scripts/show_run.py::_print_baseline_section`. M21 survives: the branch that says
  "N field(s) cannot be judged against it" (`unknown`/`no_floor`) is never exercised. That sentence is
  the fail-closed half of DEBT-101 — without it "No field disagrees more than the floor predicts." can be
  read as a clean bill on a field the floor never measured. Fix: a run with one field absent from the
  floor report → assert the count line.
- **F-3 (Low)** — `scripts/show_run.py::main`, per-document table. M25 survives: DEBT-112 names both the
  per-document row and the totals, but only `new=` (totals) is asserted. Fix: assert the row's `new`
  column, e.g. a regex on the `a.pdf` line.
- **F-4 (Low)** — `scripts/_batch.py::newest_artifact_since`. M43 survives across TOOL and `tests/ui`:
  the function is only ever exercised with one candidate after `since`, so `min` passes for `max`. The
  pipeline and `ui/jobs` each run one `run_eval`, so the real exposure is a concurrent run in the same
  workspace (`flock` prevents it in the console; nothing prevents it for the pipeline). Fix: a 3-line
  unit test with two artifacts after `since` → the newer one.
- **F-5 (Low, accepted)** — M29 (`pin_document._pin_one`: reuse without checking the capture exists)
  needs a hand-deleted capture beside a kept `_digests.json`; M45 (`run_eval_scheduled.sh`: status file
  keyed by `TS` alone) is the write-then-read window between lines 209 and 212/241 that the test's
  1-second offset cannot hit deterministically. Both are correct in the tree; M45 could be pinned by a
  static assertion (`.status-${RUN_KEY}`) alongside the workflow-file greps that suite already does.
- **F-6 (Low, config)** — `pyproject.toml [tool.mypy] files` vs `.github/workflows/quality-gate.yml`
  commands: DEBT-33's "config mirrors the gate" invariant is comment-enforced only. A test that parses
  both and compares the directory set would close it; not part of this delta's claim.

## Debt to record (Dunga → `/debt add`)
F-1 … F-4 as test-gap rows against DEBT-103 / 101 / 112 / 111 respectively; F-5's M45 half as a
Low "static pin" note on DEBT-113; F-6 as a Low row in DEBT-33's family. None reopens its parent row.

— Atchim (fresh instance, Fable 5.1) · 2026-09-27

---

## Addendum — findings closed (implementer, same day)

Each named survivor re-run by the implementer with the gate's mutant text against a new test; each dies.

| Finding | Mutant | Test |
|---|---|---|
| F-1 | M11 (validator parses the unstripped value) | `test_a_padded_value_is_parsed_the_way_the_comparison_reads_it` |
| F-2 | M21 ("cannot be judged" note dropped) | `test_show_run_names_the_fields_the_floor_cannot_judge` |
| F-3 | M25 (per-document row drops `new_table`) | `test_show_run_per_document_row_counts_a_new_table` |
| F-4 | M43 (`newest_artifact_since` returns the oldest) | `test_newest_artifact_since_picks_the_newest` |
| F-5 | M45 (status file keyed by the bare timestamp) | `test_every_per_run_file_in_the_scheduled_script_is_keyed_by_run_key` (static, as suggested) |
| F-6 | mypy `files` drifting from the CI command | `test_mypys_configured_scope_matches_what_ci_checks` |

M29 (capture reused without an exists check) stays accepted: it needs a capture hand-deleted between the
digest write and the reuse. No production code changed.
