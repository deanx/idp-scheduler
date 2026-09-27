# TEST stamp — Lane G, High rows (DEBT-86/114, 87, 88, 89, 90/93, 91, 92)

**Range:** `c8d4fbb..HEAD` (8 commits: `20c7b2f` DEBT-91 · `1466fda` DEBT-86/114 · `40be681` DEBT-87 ·
`dd288ba` DEBT-88 · `39dd859` DEBT-90/93 · `acea5c6` lint/type fixes · `c36cbdc` DEBT-89 · `89a2098` DEBT-92)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**; `src/` touched
(`run_artifact.py`, `facade.py`, `ui/reader.py`, `ui/jobs.py`), so no `re-gate skipped` (`## Rigor` row 3).
**Verdict:** ✅ **PASS WITH FINDINGS** — 6 non-blocking findings, all test gaps over correct shipped code;
none is a fail-open in the tree.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and did not see the implementer's
mutants. **DEBT-44 honoured.** All 50 mutants below are the gate's own, aimed at each fix's invariant
rather than its lines; each was applied to the working file, run, and restored byte-identically from a
scratchpad copy (`shasum -a 256` verified for all 14 files; `git status --short` shows only the
pre-existing `docs/state/STATE.json` change). No live IDP/platform call; `.env` never sourced.

## Gates (measured at `89a2098`)

| Gate | Result |
|---|---|
| `pytest -q` (full) | **1721 passed / 15 skipped** (opt-in integration), 50.6 s |
| `mypy src tests` (strict) | Success — no issues, 135 files |
| `ruff check src tests scripts` | All checks passed |
| `gitleaks detect --log-opts="c8d4fbb..HEAD"` | no leaks |

## Mutation matrix — 43 killed / 7 survived

Suites: SRC = `tests/orchestration tests/ui`, TOOL = `tests/tooling`, UI = `tests/ui`.

| # | Mutant (invariant attacked) | Result | Killed by |
|---|---|---|---|
| M01 | `run_level_gate`: PASS no longer requires `status == "complete"` | killed | `test_run_artifact.py::test_run_level_gate_is_pass_only_for_a_complete_run_of_passing_documents[gates4-aborted-INCOMPLETE]` |
| M02 | `run_level_gate`: empty gate list on a complete run is PASS | killed | same test `[gates8-complete-INCOMPLETE]` |
| M03 | `run_level_gate`: aborted run with a FAIL reads INCOMPLETE (status checked first) | killed | same test `[gates2-aborted-FAIL]` |
| M04 | `facade`: `RunAbortedError` path writes `status="complete"` | killed | `test_facade.py::test_run_eval_writes_a_partial_local_run_artifact_on_an_abort` |
| M05 | `facade`: unexpected-error path writes `status="complete"` | killed | `test_facade.py::test_an_unexpected_error_mid_run_records_the_artifact_as_aborted` |
| M06 | `facade`: `abort_reason=str(exc)` (exception text leaks into the artifact) | killed | `test_facade.py::test_run_eval_writes_a_partial_local_run_artifact_on_an_abort` |
| M07 | `parse_run_artifact`: legacy envelope treated as `complete` | killed | `test_run_artifact.py::test_a_legacy_artifact_still_parses_but_cannot_claim_it_completed` |
| M08 | `parse_run_artifact`: unknown `status` accepted | killed | `test_run_artifact.py::test_anything_that_is_not_an_artifact_is_refused[data1]` |
| M09 | `show_run`: exit code reverts to `1 if failed else 0` | killed | `test_batch_tools.py::test_show_run_never_reports_an_aborted_run_as_a_pass[documents0-aborted]` |
| M10 | `show_run`: OVERALL line reverts to the old recompute | killed | same |
| M11 | `reader.list_runs`: gate reverts to the old recompute | killed | `test_reader.py::TestCompleteness::test_an_aborted_run_is_incomplete_in_the_list_and_the_detail` |
| M12 | `reader.read_run`: gate reverts to the old recompute | killed | `test_qa_findings.py::TestF3ArtifactMustAgreeWithTheExitCode::test_an_aborted_artifact_is_never_counted_as_still_valid_documents` |
| **M13** | `reader._document_gate`: uncomputable document falls back to `"PASS"` instead of `"UNKNOWN"` | **SURVIVED** (201 UI tests) | — → F-3 |
| M14 | `jobs.summarize`: discrepancy check reverts to `gate == "FAIL"` | killed | `test_qa_findings.py::…::test_an_aborted_artifact_is_never_counted_as_still_valid_documents` |
| M15 | `jobs.summarize`: INCOMPLETE branch removed (partial documents counted as still valid) | killed | same |
| M16 | `refuse_over_ceiling` never raises | killed | `test_batch_tools.py::test_compare_refuses_a_zip_larger_than_its_ceiling_before_spending` |
| M17 | `compare_versions`: `--document-dir` leg capped again | killed | `test_compare_refuses_a_directory_larger_than_its_ceiling` |
| M18 | `compare_versions`: `--zip` leg truncated before the refuse | killed | `test_compare_refuses_a_zip_larger_than_its_ceiling_before_spending` |
| **M19** | `pin_document._resolve_documents`: `--zip` leg `unpacked[:max_documents]` reintroduced | **SURVIVED** (221 TOOL) | — → F-1 |
| M20 | `pin_document`: `--document-dir` leg capped again | killed | `test_pin_refuses_a_batch_larger_than_its_ceiling` |
| M21 | `golden_pipeline`: capped again | killed | `test_pipeline_refuses_over_the_ceiling_before_any_stage` |
| M22 | `bootstrap`: dir leg capped before the resume filter (DEBT-114) | killed | `test_bootstrap_refuses_over_the_ceiling_even_with_resume` |
| **M23** | `bootstrap --plan` zip leg capped again | **SURVIVED** | — → F-6 (accepted, plan-only, fail-closed) |
| M24 | `_name_key`: no casefold / NFC | killed | `test_names_differing_only_in_case_are_kept_apart` |
| M25 | `_name_key`: casefold only, no NFC | killed | `test_unicode_forms_of_one_name_are_the_same_name` |
| M26 | `_flatten_name`: folded candidate returned unchecked | killed | `test_a_folded_name_is_never_overwritten_by_a_real_entry_of_that_name` |
| M27 | `claimed` bookkeeping on the basename, not the returned name | killed | same |
| M28 | collision skips the entry instead of refusing the archive | killed | same |
| M29 | `dry_run` (`--plan`) path does not refuse a collision | killed | same |
| M30 | `noise_floor`: repeats classified with `classify` again | killed | `test_noise_floor_reports_a_stable_extractor_as_zero` (pre-existing) |
| **M31** | `noise_floor._observations` drops every `missing` verdict | **SURVIVED** | — → F-2 |
| M32 | `compare_versions`: shared `<store>/documents/` again | killed | `test_compare_unpacks_each_archive_into_its_own_directory` |
| M33 | `compare_versions`: `extra`-documents check removed | killed | `test_compare_refuses_an_extract_to_that_already_holds_other_documents` |
| M34 | `compare_versions`: `extra` check keyed on full path (never matches) | killed | `test_compare_pins_at_the_trusted_version_then_verifies_the_candidate` |
| M35 | `_pin_set_conflict`: foreign-pins leg removed | killed | `test_compare_refuses_when_the_dataset_already_holds_pins_of_another_corpus` |
| M36 | `_pin_set_conflict`: elsewhere leg removed | killed | `test_compare_refuses_a_document_already_pinned_for_another_dataset` |
| **M37** | `_pin_set_conflict`: foreign leg ignores `dataset` (refuses a second corpus in a second dataset) | **SURVIVED** | — → F-5 |
| M38 | `verify --all`: dataset filter removed | killed | `test_verify_all_selects_only_this_datasets_pins` |
| **M39** | `verify --all`: a pin with no `dataset` record is dropped | **SURVIVED** | — → F-6 (accepted, fail-closed) |
| M40 | `noise_floor`: zip leg re-lists the shared directory | killed | `test_noise_floor_samples_only_the_archive_it_unpacked` |
| M41 | `pinned_elsewhere`: ignores `dataset` | killed | `test_the_same_document_can_be_pinned_at_two_versions` (pre-existing) |
| M42 | `pinned_elsewhere`: does not skip its own directory | killed | `test_pin_refuses_to_respend_on_an_already_pinned_file` (pre-existing) |
| **M43** | `pinned_elsewhere`: scans only the own action's versions | **SURVIVED** | — → F-4 |
| M44 | `pinned_elsewhere`: always empty | killed | `test_pinning_a_document_again_into_the_same_dataset_at_another_version_is_refused` |
| M45 | `pin_document`: refusal block removed | killed | same |
| M46 | `verify_document`: refusal block removed | killed | `test_verify_refuses_a_pin_set_whose_documents_are_pinned_at_another_version_too` |
| M47 | `calibrate`: schema guard removed | killed | `test_calibrate_keeps_a_value_out_of_a_type_it_cannot_satisfy` |
| M48 | `calibrate`: validates the field spec, not the entry | killed | `test_calibrate_unifies_a_type_that_disagreed_across_documents` (pre-existing) |
| M49 | `calibrate`: guard restores the type but not `kept_drafted`/`agreed` | killed | `test_calibrate_keeps_a_value_out_of_a_type_it_cannot_satisfy` |
| M50 | `provision`: partial write by default (`--skip-invalid` ignored) | killed | `test_provision_all_names_an_invalid_entry_and_never_prints_its_values` |

## Per-row criterion coverage

| Row | Closure claim | Backed by |
|---|---|---|
| DEBT-91 | aborted run is INCOMPLETE, never PASS, on every reader | M01–M12, M14, M15 (14 killed) |
| DEBT-86 / 114 | a corpus over the ceiling is refused, never truncated | M16–M18, M20–M22 (6 killed; zip leg of `pin_document` untested — F-1) |
| DEBT-87 | zip flattening never overwrites; the count is the files on disk | M24–M29 (6 killed) |
| DEBT-88 | empty-vs-empty is agreement, invented content is not | M30 (killed); value-then-empty untested — F-2 |
| DEBT-90 / 93 | spend = what `--plan` priced; verify measures this dataset's pins | M32–M36, M38, M40 (7 killed) |
| DEBT-89 | one (dataset, document) pinned at one action/version | M41, M42, M44–M46 (5 killed; cross-action untested — F-4) |
| DEBT-92 | calibration writes only schema-valid entries; no partial provision | M47–M50 (4 killed) |

## Findings (all non-blocking; shipped code is correct in every case — the gap is the test)

- **F-1 (Med)** — `scripts/pin_document.py::_resolve_documents`, `--zip` leg. M19 survives: a
  reintroduced `unpacked[: args.max_documents]` makes `pin_document.py --zip corpus.zip --all
  --max-documents 4` on a 10-document archive pin four, exit 0, and nothing in the suite notices.
  `test_pin_refuses_a_batch_larger_than_its_ceiling` exercises `--document-dir` only. `compare_versions`
  does not reach this leg (it passes `--document-dir`), so the primary use case is covered, but the
  direct CLI documented in `CLAUDE.md` is not. Fix: parametrise that test over both source flags.
- **F-2 (Med)** — `scripts/noise_floor.py::_observations`. M31 survives: dropping every `missing`
  observation passes 221 tooling tests. Confirmed against `classify_pinned_file`: a field read as a
  value on pass 1 and empty on the repeat is `missing` — the "extractor forgets a field" instability,
  exactly what `calibrate_golden` should demote on. DEBT-88's tests pin two of three leaf cases
  (always-empty = stable, invented = unstable); the third has no test, so an over-correction that
  hides drop-outs would land green. Fix: one test, value-then-empty counts as unstable.
- **F-3 (Low-Med)** — `src/idp_regression/ui/reader.py::_document_gate`. M13 survives: the `"UNKNOWN"`
  fallback is what makes `run_level_gate`'s documented rule ("a document whose gate cannot be computed
  is INCOMPLETE") true in the console, and returning `"PASS"` there passes all 201 UI tests. Scenario: a
  truncated or hand-edited verdict map inside a `complete` artifact renders gate PASS on the Runs page.
  Fix: a `TestCompleteness` case with one malformed document in a complete artifact, asserting
  INCOMPLETE in both `list_runs` and `read_run`.
- **F-4 (Low-Med)** — `scripts/_batch.py::pinned_elsewhere`. M43 survives: restricting the scan to the
  own action's versions is undetected. The platform item id is `uuid5(dataset|document_id)` — it carries
  neither version **nor action** — so a document pinned into one dataset at action A1 and again at A2 is
  the same DEBT-89 overwrite spelled differently. The code scans `*/*/_pins.json` correctly; the test
  only covers same-action/other-version. Fix: one cross-action case on the existing refusal test.
- **F-5 (Low)** — `scripts/compare_versions.py::_pin_set_conflict`, foreign leg. M37 survives: ignoring
  `dataset` falsely refuses a second corpus under a second dataset in the same store — DEBT-93's headline
  scenario, tested only at `verify_document`'s filter, not at `compare_versions`' pre-spend check.
  Fail-closed (a false refusal), hence Low. Fix: an admit-path test (two datasets, one store, compare runs).
- **F-6 (Low, accepted survivors — recorded so they are not re-discovered)** — M23 (`bootstrap --plan`'s
  zip leg capped: prints a truncated plan while the real run refuses; fail-closed) and M39 (`verify --all`
  drops a legacy pin without a `dataset` record: fail-closed, at worst "nothing pinned", rc 2).

**Observation, not a finding:** `scripts/verify_document.py::_resolve_source` still lists with
`discover_documents(args.document_dir, args.glob, 100_000)` — a residual cap. It feeds presence
reconciliation only, and a pinned document beyond it would be refused as MISSING, so it is fail-closed;
`None` would be the consistent spelling after DEBT-86.

## Debt to hand to Dunga (`/debt add`, tests-only rows)

F-1, F-2, F-3, F-4, F-5 above — each is one test, each has its killing mutant named in the matrix so the
fix can be verified mechanically (M19, M31, M13, M43, M37 must die).

— Atchim (gate instance, Fable 5.1) [atchim]

---

## Addendum — findings closed (implementer, same day)

All five named survivors now die, each against its own new test, re-run by the implementer with the
gate's mutant text (M19, M31, M13, M43, M37: each `1 failed`).

- **F-3 was larger than a test gap.** Writing its test exposed a real defect: `reader.list_runs`
  counted verdicts (`show_run._counts`) *before* `_document_gate`'s guard, so one malformed
  verdict map raised `KeyError` out of `list_runs` and `read_run` — the Runs page failed whole
  rather than showing the document as UNKNOWN. Fixed in `ui/reader.py` (`_document_counts`, and an
  UNKNOWN short-circuit in `read_run`); the new test asserts INCOMPLETE on both surfaces.
- F-6's accepted survivors (M23, M39) stay accepted, as recorded above.
