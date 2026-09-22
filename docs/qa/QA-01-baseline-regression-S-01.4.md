# QA-01 — audit of SPEC-01 Story S-01.4 (Orchestrator + CLI, Epic D)

**Verdict:** ⚠️ **Pass with follow-ups — S-01.4 is DONE**
**Auditor:** Zangado (Opus 5) · **Date:** 2026-09-22 · **Author:** alex@deanx.com.br
**Branch:** `feat/S-01.2-idp-adapter` · **HEAD:** `25ec24e` · **Stamp:** `cfd2bd7`
**Rigor profile:** `prototype` (CLAUDE.md ## Rigor)

## Entry gates (verified by this audit, not taken on trust)

| Gate | Result |
|---|---|
| TDD stamp `docs/qa/TEST-S-01.4-baseline-regression.md` | ✅ PASSED, `Source: /test gap-fill (Atchim TDD gate)`, `Commit: cfd2bd7`, fresh instance per DEBT-44 |
| Rigor gate (`Risk level: high`) | ✅ `/test`-sourced stamp satisfies it; profile ≠ risk level |
| Freshness `git log cfd2bd7..HEAD -- src tests` | ✅ empty (`25ec24e` is docs-only) |
| Test files deleted since stamp | ✅ none (`git diff --name-status cfd2bd7 HEAD \| grep ^D` empty) |
| Skips | ✅ 15 default / 2 live, every one honest + linked (S-01.6 blocker) |

## Mechanical floor

- **Unit + contract:** `857 passed, 15 skipped` — matches the stamp exactly.
- **Live:** `870 passed, 2 skipped` with `RUN_INTEGRATION_TESTS=1`. Live Langfuse + live IDP-token tests **ran**. Only live IDP submit/poll and the TP-01 placeholder skip, both naming the missing published action id/version (S-01.6). **No document was submitted to the live IDP.**
- **SCA:** `pip-audit` — no known vulnerabilities.
- **Secrets:** `gitleaks git . --log-opts="$(git merge-base main HEAD)..HEAD"` (8.30.1, 218 commits) — **no leaks found.**
- **Cleanliness:** `check_clean.py` exit 0 — clean, no stale artifacts.
- **Composition:** 86 contract/invariant tests passed (CT-03, CT-04, CT-05, INV-01/02/04/05/06/07/08 + module-boundary + static orchestration checks).

## DoD walk — Story S-01.4

Every line met unless noted. Evidence is a measurement I took, not a re-reading of the stamp.

| DoD line | Verdict |
|---|---|
| All AC (AC1, AC5, A1–A4, post-condition) | ✅ met — probed end-to-end, see § Observability |
| `run_eval` facade + CLI + `load_dotenv()` first (INV-05) | ✅ met |
| `--version` required, `--action` fallback, boundary validation (TP-31) | ✅ met |
| Abort-on-failure, exit code is the invariant (INV-06/CT-04) | ✅ met — every probed path exits 1 |
| **Raise shape (ADR-0004 A7)** — post-run paths raise `RunAborted` + marker; the three pre-run guards return `1` without raising and write no marker | ✅ met as **reconciled**; observed: pre-run aborts write zero platform writes |
| Fail-closed on missing credential (N6) | ✅ met — 9 tests incl. `[×4]` IDP-var exhaustiveness; whitespace-only also fails closed |
| Two distinct timeouts / no-retry-on-poll-timeout / 401 refresh (N1, ADR-0004 #7) | ✅ met (values remain S-01.6-provisional, as the DoD itself states) |
| Pre-run order pinned (1–6) | ✅ met — drift→empty→N28 ordering observed directly |
| Schema-drift check (ADR-0005 #8) | ✅ met — **and fail-closed on every malformed shape**, see F-4 note |
| N28 pre-run golden validation reusing N22 (`is`-identical alias) | ✅ met — TP-46 static AST pin present |
| `document_id → path` resolution (INV-01) | ✅ met |
| INV-08 gate-before-single-record; no platform read-back | ✅ met — TP-19 static AST pin derives the forbidden set from the Protocol |
| `run_status` aborted/complete; no marker on pre-run aborts | ✅ met — observed on all five pre-run reasons |
| INV-04 four-field run metadata | ✅ met |
| Experiment-name uniqueness `{run}-{run_id[:8]}` (N26/DEBT-19) | ✅ met — distinct experiment per invocation observed |
| Record-failure mapping, `record_run` exactly once, no retry | ✅ met |
| No flush seam / `flush_failed` on one path | ✅ met |
| CT-04 exit-code contract | ✅ met |
| Unit tests (state machine, budget math, ordering, mapping) | ✅ met |
| Observability (N10, REQUIRED) | ✅ **VERIFIED** — see below |
| Compliance: env-only creds, no plaintext sensitive logging, **gitleaks green per T-01.4.10's three legs** | ✅ met — see § T-01.4.10 |
| Passing `/harden` containment report | ✅ met **at HEAD by my own re-gate** — see **F-1** |
| N3 CLI exit latency < 2 s | ✅ met — measured **0.0001 s** |
| Integration tests `@pytest.mark.integration` incl. TP-01 | ⚠️ **met with deviation** — TP-01 deferred, see § Deferrals |
| N8 50-doc sequential run | ✅ met — measured **0.012 s** for 50 docs × 50 fields, exit 0 |
| Dependency pinning (N17) | ✅ met — `uv.lock` committed; CI `lockfile-pin` job runs `uv lock --check` + `uv sync --locked` |
| Docs updated (ADR-0004 A1–A7, ADR-0005 #8/#9, NFR-01 N10/N26) | ✅ met |
| Atchim review | ✅ met |
| Zangado sign-off + `Observability: ✅ VERIFIED` | ✅ this report |

### Deferrals — judged on their merits, all three ACCEPTED

1. **TP-01 live orchestration e2e** — skipped, named, with an honest reason pointing at S-01.6 and a real external blocker (no published IDP action id + version exists yet). A skip that names its blocker and its unblocking story is not a coverage hole, it is a scheduled one. The placeholder exists so the gap is visible in the suite rather than absent from it. **Accepted; it is the epic's business, not S-01.4's.**
2. **In-loop `MalformedGoldenError` unreachable branch** — the argument is genuinely test-backed, not asserted: N28 runs `validate_golden_structure`, the `is`-identical alias of the `_validate_golden` that `classify()` calls, over every item before the loop, and the *ordering premise itself* is pinned by `test_prerun.py::test_validate_golden_set_runs_before_any_idp_call`. A `# pragma: no cover` on a branch whose unreachability is pinned by a test is correct practice, not coverage-dodging. **Accepted.**
3. **T-01.4.10 leg 1 / DEBT-45** — **RESOLVED, not deferred.** See below.

### T-01.4.10 / DEBT-45 — the carried Done-blocker: **DISCHARGED**

DEBT.md recorded the hard predicate: *"If DEBT-45 is still open at S-01.4's `/qa`, it is an S-01.4 Done-blocker."* Judged against Soneca's **reconciled** leg-1 wording (diff-scoped blocking, not full-history blocking), all of it has landed and I verified each leg myself:

- **Leg 0** — `.github/workflows/secrets-audit.yml`: full-history, weekly + `workflow_dispatch`, `continue-on-error`, **never on `pull_request`**. Correct shape.
- **Leg 1** — `secrets-gate.yml` job `gitleaks-diff`: merge-base-scoped, blocking, on PR + push to main. **Scan run for real: no leaks found.**
- **Leg 2** — `.githooks/pre-commit` with `gitleaks protect --staged`, wired via `scripts/install-git-hooks.sh`.
- **Leg 3** — job `env-hygiene`: `git check-ignore -q .env` **and** `git ls-files --error-unmatch .env` must fail. Mechanical, independent of gitleaks' rule set.
- **The load-bearing remedy** — `.gitleaksignore` carries exactly one **fingerprint** (`353549d…:tests/platform/test_langfuse_adapter.py:generic-api-key:305`), commit × file × line × rule, with a written reason and an explicit prohibition on a `paths` allowlist. This is the shape I ruled for. It is not a `paths = ['tests/']` blind.

**`Secrets: ✅`.** The two-audit caveat is closed. **DEBT-45 is no longer an S-01.4 Done-blocker.**

## Containment gate

`docs/qa/HARDEN-01.md` — `Verdict: ⚠️ PASS WITH RESIDUALS`, `Containment-REQUIRED: DISCHARGED at b473ce4`, and **all five widened conditions PASS** (§2a). The five-item condition I attached to the S-01.3 deferral is satisfied, including leg (iii), the A3 foreign-`run_id` → typed abort with zero platform writes. The deferral that let S-01.3 reach Done is retroactively supported.

**But see F-1** — the discharge SHA is not the shipped SHA. I re-gated it myself rather than waving it through.

## Observability — `Observability: ✅ VERIFIED`

N10 is REQUIRED, so I **ran the flow and watched the telemetry fire**. I did not accept that the code has loggers. Eight scenarios against a mock IDP + mock platform, logs captured off the root handler:

- **(a) run-start** — one line after the pre-run chain, carrying `run` / `experiment` / `action` / `version` / `golden_version` / `golden_dataset_name` / **`items=3`**. Item count present. ✅
- **(b) run-end at EVERY exit** — observed on success (`outcome=success exit_code=0 pass_count=3 fail_count=0 elapsed_seconds=0.001`), on gate-fail (`outcome=gate_failed exit_code=1 pass_count=1 fail_count=1`), on mid-run abort, on record-phase abort, and on **all four pre-run aborts** (empty_set, schema_drift, malformed_golden, and the unexpected-error path). Every exit point. ✅
- **(c) per-document** — `document_id` + `gate` + `elapsed_seconds` on the **same line**. ✅
- **(d) abort triple** — `reason` + `document_id` (or `<none>` for run-level) + sanitized `detail`, e.g. `hard_failure document_id=<none> detail="…"`. `schema_drift` carries **both hashes** and the literal `absent` when the schema is missing. `malformed_golden` carries `document_id="d1" path="$.fields.total.type"` — JSON path only, **never the value**. ✅

## Compliance (## Domain) — own sentinels, planted fresh

I planted four sentinels — a golden value, an extracted value, a credential, and a filesystem path — and swept the captured logs, the platform payload, the `mark_run_status` kwargs, and every exception's `str`/`repr`/`args` plus the full `__cause__`/`__context__` chain, on all eight scenarios.

**Result: zero sentinel hits on every path.** The `record_run` payload carries only `item_id`, `document_id`, deterministic score ids, verdict literals and `comment: null` — DEBT-18 option B holds exactly as written. Document paths never reach a log. Credentials never reach a log.

One hit occurred only when I injected a sentinel **into a platform exception message myself**; the orchestrator then logged it in `detail=`. I checked every real raise site in `langfuse_adapter.py`, `idp_client.py` and `transport.py`: they interpolate key names, `document_id`, HTTP status and attempt counts — **never a golden or extracted value**. So INV-02 holds in practice; the `detail=` pass-through is escaped-but-not-redacted, which is the already-recorded **DEBT-54 A-1** event-shaped obligation, not a new defect.

Log-injection defence re-probed directly at HEAD: a newline, a `"` and a NUL byte in a frame filename all come back escaped on one physical line — Branca's forged `run_end outcome=success` is not reproducible.

## NFR-01 verdicts (S-01.4's rows)

| Row | Verdict | Evidence |
|---|---|---|
| **N1** — two distinct timeouts | ✅ PASS | per-document `elapsed_seconds` emitted; hung-POST abort-not-hang red-teamed in HARDEN-01. Values stay S-01.6-provisional by design |
| **N3** — CLI exit latency < 2 s | ✅ PASS | measured **0.0001 s** after `record_run` returned; pinned by `test_all_gates_pass_success_path_returns_promptly` |
| **N6** — fail-closed on credential absence | ✅ PASS | 9 tests; `[×4]` IDP-var exhaustiveness; whitespace-only rejected (`.strip()`, DEBT-30 closed); zero outbound calls |
| **N7** — abort-on-failure (ASM-02) | ✅ PASS | INV-06 tests + every probed abort path exits 1, loop stops, no partial reference |
| **N8** — 50-doc sequential | ✅ PASS | **0.012 s** for 50 docs × 50 fields, exit 0, well inside budget |
| **N10** — per-run telemetry | ✅ PASS | observed firing, all four line classes — see above |
| **N14** — transient retry / no auth retry | ✅ PASS | per-branch unit tests; mid-run 401→refresh→retry→`invalidate()` once pinned |
| **N15** — empty-set guard | ✅ PASS | observed: `empty_set`, exit 1, no marker, never a silent zero |
| **N16** — platform-write failure aborts | ✅ PASS | `record_run` raise → `hard_failure`/`flush_failed`, exit 1, `complete` never written |
| **N26** — concurrency / run-name idempotency | ✅ PASS | experiment name `{run}-{run_id[:8]}` distinct per invocation; DEBT-19 merge closed |
| **N28** — pre-run schema validation | ✅ PASS | `malformed_golden` before any IDP call; TP-46 static AST pin; no value leak |
| **N2, N21, N22** | ✅ PASS (unchanged, earlier stories) | re-run green |
| **N11** (exit-code CI signal) | ⚠️ WAIVED → `/signoff` | `system` scope. CT-04 green here as supporting evidence |
| N4, N5, N9, N12, N13, N17, N18, N19, N20, N23, N24, N25, N27 | ⚠️ WAIVED → `/signoff` | `system` scope |

**All 11 S-01.4 feature rows PASS. Zero FAIL, zero left PENDING.** Markers: `Containment: ✅ DISCHARGED` · `Observability: ✅ VERIFIED` · `LLM-Evals: N/A` · `UI: N/A`.

## Findings

### Major

**F-1 — HARDEN-01 discharges containment at a SHA that is not the shipped SHA.**
`docs/qa/HARDEN-01.md` §10.8 states the discharge is at `b473ce4` and calls it *"the commit S-01.4 is stamped and QA'd on."* That is false. The stamp is `cfd2bd7`; HEAD is `25ec24e`; and **two `src/` commits landed after `b473ce4`** — `3893347` (route `frame_location`'s return through `sanitize_for_log`, A-6/DEBT-60) and `823b7ef` (move `extract_tb` inside the totality wrapper, P18/DEBT-58). Both touch `orchestration/log_sanitize.py`, *the exact module the containment report gates*. HARDEN-01 applied a scope pin for precisely this situation twice before (§8.8, §9.6: *"a full re-gate at whatever commit the tree settles on"*), and did not this time.

The substance is fine and I verified it rather than assuming it — **I re-gated at HEAD myself**: 12 probes over `frame_location` (newline/quote/NUL forging, `extract_tb` raising `MemoryError`/`RecursionError`/`OSError`, bytes/`None` filenames, `$HOME`/cwd disclosure). **0 raise-escapes, 0 line-splits, 0 path disclosures.** Both commits strictly *narrow* the escape surface and are the one-line completions Branca herself prescribed. So containment is met at HEAD — **on my measurement, recorded here, not on the report's claim.**

Not Done-blocking, because the re-gate is done and it passed. But the report's SHA claim must be corrected, or the next reader inherits a discharge that doesn't cover the code.
`escaped-atchim: no` — report-accuracy/process, outside the five-axis code lens.

### Minor

**F-2 — `DEBT-45` still reads `open` in DEBT.md though its remedy shipped.**
All three legs, the scheduled audit and the fingerprint baseline are in the tree and the diff-scoped scan is green. The row is the literal predicate of an S-01.4 Done-blocker, so a stale `open` here is the kind of bookkeeping that gets a story bounced for nothing. Mark it closed and name the landing commits. Same for the DEBT-48 sweep line, which still lists **FO-6** as remaining — see F-3.
`escaped-atchim: no` — state-log bookkeeping, not code.

**F-3 — FO-6 can be closed; I measured it.** DEBT-48 left `expected_output_schema` open because *"its consumer, the orchestrator's `schema_drift` comparison, does not exist until S-01.4."* It exists now. I probed seven shapes — string, list, int, `None`, an unserializable object, a NaN-bearing dict, an empty dict. **All seven fail closed:** exit 1, **zero IDP calls, zero platform writes.** The unserializable case lands in the pre-run catch-all as a contained `TypeError` with a `<external>/`-clamped frame. No action beyond closing the row.
`escaped-atchim: no` — not a defect; a verification that retires an open item.

**F-4 — FO-9 remains genuinely open and is worth a card.** `LANGFUSE_HOST` gets no scheme or format validation: `http://evil.invalid`, `ftp://x` and `not-a-url` all pass `validate_platform_credentials`, which means the platform API key is sent to whatever host the env names — including plaintext `http://`. Empty and whitespace-only *do* fail closed (DEBT-30). This is the same threat family the project already takes seriously in REG-07/REG-10 (a credential reaching a host the caller didn't choose), and the asymmetry is stark: the same module fails closed on a `LANGFUSE_BASE_URL` split-brain and both transports refuse cross-host redirects, yet the env path is unguarded. DEBT-48 routed it to T-01.4.10 as a **card**, not as a DoD line — so it does **not** block S-01.4's Done, but it should not drift either.
`escaped-atchim: no` — config-validation gap at a trust boundary, carded but never scheduled.

### Carried, not new (no action from this audit)

- **DEBT-53 / HARDEN GAP-3** — `record_deadline_seconds` ships disabled with no env knob; worst case ~63 h at N8's ceiling. Fails safe (hang, not wrong result). Arm it before this tool gates another team.
- **DEBT-54 A-1** — `detail=` escaped but not redacted; safe today by construction across every real raise site.
- **DEBT-47 / DEBT-51** — `mypy tests` is in no gate, and CI has no type or test job at all, only `secrets-*.yml`. Drift protection that does not run is not protection.

## Stale stamps (DEBT-46) — stated plainly

DEBT-46 records S-01.1, S-01.2 and S-01.3's stamps as stale from cross-story edits.

- **Does it block S-01.4's Done? No.** The staleness rule is per-story: only the story's own `Files:` set matters. S-01.4's stamp is `cfd2bd7`, its Files set is intact, and `git log cfd2bd7..HEAD -- src tests` is empty. S-01.4's Done rests on a fresh stamp of its own.
- **Does it block UC-01's epic Done? Yes.** Three of the epic's stories are currently marked Done on stamps that no longer cover the code that ships. The epic cannot be signed off on that. The re-stamps must each be run under **DEBT-44** by an instance that did not review the diff.
- **Neither is a reason to re-open S-01.1/.2/.3** — their Done standing was correct when granted; it needs re-backing, not reversal.

## Rigor — `prototype` profile

Ran in full: the mechanical floor (tests, SCA, secret scan, cleanliness), the full DoD walk, the NFR row-by-row, containment, observability, composition, and the compliance sentinel sweep.
**`skipped: prototype profile`** — Readability and Architecture axes, design-pattern conformance, and the doubt-driven adversarial pass. Re-runnable at `--rigor=full`.

⚠️ CLAUDE.md § Rigor's standing condition is untouched by this verdict: **re-raise to `standard` before this tool gates another team's prompt changes.** Green here means "the MVP's own tests pass", not "this gate can be trusted".

## Hand-off to Dunga

- **BUG** — *Correct HARDEN-01's containment discharge SHA* — severity Minor-process/Major-accuracy. §10.8 names `b473ce4` and calls it the stamped/QA'd commit; the stamp is `cfd2bd7` and two `src/` commits to the gated module landed after. DoD: §10.8 states the true SHA range, records that `3893347`/`823b7ef` are covered, and cites QA-01 S-01.4's re-gate as the evidence.
- **TASK** — *Close DEBT-45 and the DEBT-48 FO-6 row* — severity Minor. DoD: DEBT-45 marked closed with landing commits; FO-6 closed citing this audit's seven-shape fail-closed probe; DEBT-48 sweep line updated to "Remaining: FO-9".
- **TASK** — *FO-9: validate `LANGFUSE_HOST` scheme/format* — severity Minor (security). DoD: reject a non-`https` scheme and a non-URL value at `validate_platform_credentials`, fail closed before any client construction, with a test per shape; decide `IDP_REGION`/`IDP_ORG_ID` in the same pass.
- **TASK (epic-blocking)** — *Re-stamp S-01.1, S-01.2, S-01.3 (DEBT-46)* — severity Major for the epic, not for S-01.4. DoD: each re-stamp run under DEBT-44 by an instance that did not review the diff.

**Verdict: ⚠️ Pass with follow-ups. 0 Critical, 1 Major, 3 Minor. S-01.4 is DONE.** Nothing bounced back to Dengoso.
