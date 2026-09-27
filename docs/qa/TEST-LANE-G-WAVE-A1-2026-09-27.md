# TEST stamp — Lane G, wave A1 (DEBT-99, 100, 102, 117)

**Range:** `d570623..HEAD` (6 commits: `8f0698b` DEBT-99 · `b4de151` DEBT-100 · `43a693c` DEBT-102 ·
`888424b` DEBT-117 · `ab7508b` review round (`--from-captures` byte check, worklist 0600) · `475d265` docs)
**Spec:** `docs/specs/SPEC-01-baseline-regression.md` — **Risk level: high**. One commit touches `src/`
(`orchestration/facade.py`: `select_items(exact=)`, `run_eval(exact_documents=)`), so under `## Rigor`
nothing is lightened: the `/test` stamp is run at `full`, fresh instance, own mutation matrix.
**Verdict:** ✅ **PASS WITH FINDINGS** — 5 non-blocking findings, every one a test gap over correct shipped
code (each survivor was read against the source; none changes shipped behaviour). No fail-open is in the
tree; every refusal this delta adds happens before quota is spent.

**Independence:** implementer **Opus 5.5** · this gate run by a **fresh Atchim instance on Fable 5.1
(`claude-fable-5-1`)** that did **not** issue the code-review APPROVE and never saw the implementer's
mutants. **DEBT-44 honoured.** All 38 mutants are the gate's own, aimed at each row's invariant rather than
the implementer's lines; each was applied to the working file, run, and restored byte-identically from a
scratchpad copy (`shasum -a 256 -c` OK for all 7 files; `git status --short` shows only the pre-existing
`docs/state/STATE.json` change; `__pycache__` outside `.venv`/`frontend` deleted). No live IDP/platform
call; `.env` never sourced.

## Gates (measured at `475d265`)

| Gate | Result |
|---|---|
| `pytest -q` (full) | **1767 passed / 15 skipped** (opt-in integration), 37.5 s |
| `mypy src tests` (strict) | Success — no issues, 135 files |
| `ruff check src tests scripts` | All checks passed |
| `gitleaks detect --log-opts="d570623..HEAD"` | 6 commits scanned, no leaks |

Suites: TOOL = `tests/tooling/test_batch_tools.py` (206), FAC = `tests/orchestration/test_document_filter.py`
+ `test_facade.py` (111), CLI = `tests/orchestration/test_cli.py`.

## Mutation matrix — 32 killed / 6 survived

| # | Mutant (invariant attacked) | Result | Killed by |
|---|---|---|---|
| M01 | `make_private_parents` chmods an EXISTING parent again (DEBT-99's bug) | killed | `test_writing_an_output_leaves_an_existing_parent_alone` |
| M02 | `write_private_json` temp via plain `open` (umask) + no `fchmod` | killed | `test_write_private_json_is_owner_only_and_leaves_no_partial`, `test_a_stale_partial_file_is_rewritten_owner_only` |
| M02b | `write_private_json` `fchmod` dropped only (stale `.partial` keeps 0644) | killed | `test_a_stale_partial_file_is_rewritten_owner_only` |
| M03 | `write_private_text` via plain `open` | killed | `test_the_review_worklist_is_owner_only_in_an_existing_open_directory` |
| M03b | `write_private_text` `fchmod` dropped only | killed | `test_a_stale_partial_worklist_is_rewritten_owner_only` |
| M04 | `noise_floor` preflight removed | killed | `test_noise_floor_refuses_an_unwritable_report_path_before_spending` |
| M04b | preflight failure swallowed (continues to spend) | killed | same |
| M05 | `assert_writable_output`: `os.access` check dropped | killed | same |
| M06 | `make_private_parents` mkdir at default mode | killed | `test_writing_an_output_creates_missing_parents_owner_only` |
| M07 | preflight checks a different path than the one written | killed | `test_noise_floor_refuses_an_unwritable_report_path_before_spending` |
| M08 | preflight moved AFTER the extraction loop | killed | same (`adapter.calls == 0`) |
| **M09** | `pin._pin_one`: digest taken AFTER extraction | **SURVIVED** | — accepted (F-5) |
| M10 | pin records a digest of the NAME, not the bytes | killed | `test_pin_still_skips_the_same_bytes` + 4 pin tests |
| M11 | `changed_since_recorded` compares `==` (same bytes → refused) | killed | 9 tests, e.g. `test_pin_refuses_to_respend_on_an_already_pinned_file` |
| M12 | legacy exemption widened: missing digest → CHANGED | killed | `test_verify_can_take_the_documents_from_a_directory`, `test_a_legacy_pin_without_a_provisioned_record_is_re_sent` + 4 |
| **M12b** | missing FILE → CHANGED | **SURVIVED** | — → F-4 |
| M13 | pin writes the digest under another key (never compared) | killed | `test_pin_refuses_a_same_named_file_with_different_bytes` |
| M14 | `--repin` does not bypass the byte check | killed | same |
| **M15** | pin byte check moved AFTER the todo loop (a mixed batch spends first) | **SURVIVED** | — → F-2 |
| M16 | verify byte check removed | killed | `test_verify_refuses_to_compare_different_bytes_against_a_golden` |
| **M17** | verify checks the bytes in the pin's RECORDED dir, not the `--zip`/`--document-dir` source | **SURVIVED** | — → F-1 |
| M19 | bootstrap digest recorded under the golden key, not `document_id` | killed | `test_bootstrap_resume_refuses_a_document_whose_bytes_changed`, `test_from_captures_refuses_a_document_whose_bytes_changed` |
| M20 | bootstrap `--resume` byte check removed | killed | `test_bootstrap_resume_refuses_a_document_whose_bytes_changed` |
| M21 | bootstrap `--from-captures` byte check removed | killed | `test_from_captures_refuses_a_document_whose_bytes_changed` |
| M22 | `--from-captures` without dir: "NOT checked" notice dropped | killed | `test_from_captures_without_a_document_dir_says_it_cannot_check` |
| M23 | `_digests.json` never persisted | killed | both DEBT-100 bootstrap tests |
| **M24** | bootstrap digest taken AFTER capture | **SURVIVED** | — accepted (F-5) |
| **M25** | `compare_versions` pin half always passes `--repin` (byte check bypassed, every pin re-paid) | **SURVIVED** | — → F-3 |
| M25b | `compare_versions` ignores the pin half's rc ≠ 0 | killed | `test_compare_refuses_to_verify_after_a_partial_pin` |
| M26 | `_golden_key` always the stem (DEBT-102's bug) | killed | `test_bootstrap_keeps_both_documents_that_share_a_stem` |
| M27 | sticky-key rule removed | killed | `test_a_redraft_keeps_the_key_a_document_was_first_drafted_under` |
| M28 | stem rule inverted | killed | 6 bootstrap tests |
| M29 | sticky rule keyed on stem, not `document_id` (collision on redraft) | killed | `test_bootstrap_keeps_both_documents_that_share_a_stem` |
| M30 | `select_items`: `exact` ignored | killed | `test_exact_mode_refuses_a_selector_the_substring_fallback_would_resolve`, `test_run_eval_exact_documents_refuses_a_substring_selector_before_submitting` |
| M31 | `exact` only refuses when NO substring match (fallback still reached) | killed | same two |
| M32 | `run_eval` does not thread `exact_documents` | killed | `test_run_eval_exact_documents_refuses_a_substring_selector_before_submitting` |
| M33 | `verify_document` passes `exact_documents=False` | killed | `test_verify_asks_run_eval_for_exact_document_matches` |
| M35 | `exact` defaults True (CLI substring convenience lost) | killed | 5 FAC tests, e.g. `test_a_unique_substring_selects_that_item` |

Other programmatic callers checked by reading: `golden_pipeline.run` calls `stages.run_eval` with no
`--document` (nothing to make exact); `ui/jobs` reaches `run_eval` only through `compare_versions` →
`verify_document` (exact); `cli.main` is the human path and keeps the fallback on purpose. No caller missed.

## Per-row criterion coverage

| Row | Closure claim | Backed by |
|---|---|---|
| DEBT-99 | no output write chmods an existing parent; files created 0600 (not chmodded after); `noise_floor` checks its report path before spending | M01–M08 (11 killed) |
| DEBT-100 | sha256 per pin and capture; pin/verify/bootstrap refuse changed bytes before spending; `--repin` re-reads; legacy pins exempt | M10–M14, M16, M19–M23, M25b (13 killed); new-source verify, mixed-batch pin and compare's `--repin` pass-through untested (F-1..F-3) |
| DEBT-102 | documents sharing a stem both kept; a redraft keeps its first key | M26–M29 (4 killed) |
| DEBT-117 | programmatic callers get exact matches only; CLI keeps the convenience | M30–M33, M35 (5 killed) |

## Findings (all non-blocking; shipped code is correct in every case — the gap is the test)

- **F-1 (Med)** — `scripts/verify_document.py::run`, byte check. M17 survives: the only DEBT-100 verify
  test pins and verifies in the SAME directory, so a check that reads the bytes at the pin's recorded
  `document_dir` instead of the `--zip`/`--document-dir` source passes. That new-source path is exactly the
  row's scenario (a re-sent archive) and is the path `compare_versions` → `ui/jobs` drives. Fix: pin
  (with `sha256`) under dir A, `verify --document-dir B` where `B/a.pdf` has other bytes → rc 2,
  `run_eval.calls == []`.
- **F-2 (Low-Med)** — `scripts/pin_document.py::run`. M15 survives: "refused before anything is spent"
  is only tested with a single, already-pinned document (`todo` empty). A batch mixing one changed
  already-pinned file with one new file would, under the mutant, extract the new one and then refuse.
  Fix: two files, re-write one's bytes, run with `--all`; assert rc 2 and `calls == []`.
- **F-3 (Low-Med)** — `scripts/compare_versions.py::run`. M25 survives: nothing asserts the pin half's
  argv carries `--repin` only when asked. Always passing it bypasses the byte check AND re-pays every
  already-pinned document on a re-run (the "pinned files are not re-paid for" line in its own message).
  Fix: with a recording `pin` stub, assert `"--repin" not in argv` by default and present with
  `repin=True`.
- **F-4 (Low)** — `scripts/_batch.py::changed_since_recorded`. M12b survives: a pinned-and-digested file
  that is absent from the source is (correctly) not reported as changed, but no test says so; with
  `verify --allow-missing` the mutant would refuse instead of skipping. Related shipped behaviour worth a
  decision, not a fix here: `bootstrap --from-captures --document-dir` re-drafts a captured document that
  is no longer in the directory without saying so (the check only covers files it can read). Fix: a
  digest-bearing pin whose file is missing + `--allow-missing` → still runs; and decide whether
  `--from-captures` should name captures with no current file.
- **F-5 (Low, accepted)** — `pin_document._pin_one` / `bootstrap.run`: M09/M24 (digest taken after the
  extraction instead of before) are unobservable in-process — only a file rewritten during the call would
  tell. The ordering is pinned by comment only. A `capture_fn` stub that rewrites the file would kill it
  if anyone wants the invariant executable.

Observed outside the delta's scope, surfaced as debt candidates rather than findings: (a)
`_batch.extract_documents_from_zip` still writes customer documents via `open(target, "wb")` at umask and
chmods after — the write-then-chmod window DEBT-99 closed for outputs, on the one file class `## Domain`
says never leaves the machine; (b) `calibrate_golden.py:453` writes its `.md` with `write_text` at umask
(DEBT-105's family; it carries field names and rates, not values); (c) `ui/jobs.py:572` chmods a parent,
but `.idp-regression-jobs/` is a directory the tool owns, so it is `ensure_private_dir`'s legitimate case.

## Debt to record (Dunga → `/debt add`)
F-1 … F-4 as test-gap rows against DEBT-100 (F-1, F-2, F-3, F-4); F-4's `--from-captures` half as a
behaviour question on DEBT-100; (a) and (b) above as new Low rows (DEBT-99/DEBT-105 family). None reopens
its parent row.

— Atchim (fresh instance, Fable 5.1) · 2026-09-27

---

## Addendum — findings closed (implementer, same day)

Each finding now has a test that kills the gate's own surviving mutant text: F-1 (M17), F-3 (M25), F-4
(M12b). F-2's test asserts `calls == []` for a mixed batch (one changed, already-pinned file plus one new
file), which is what M15 breaks. F-5 (digest-after-extraction) stays accepted as unobservable in-process.

The two write-then-chmod sites flagged outside the delta are fixed with tests: unpacked documents are now
created 0600, and the calibration markdown is owner-only. With `draft_golden --out` also routed through the
private writer, **DEBT-105 is closed**. Left as noted: `bootstrap --from-captures --document-dir` still
re-drafts a captured document that is no longer in the directory (F-4's behaviour question), and the
`ui/jobs` chmod on a tool-owned directory stays acceptable.
