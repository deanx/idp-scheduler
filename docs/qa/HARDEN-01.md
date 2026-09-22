# HARDEN-01 — Containment report — UC-01 baseline regression

**Verdict: ⚠️ PASS WITH RESIDUALS** — re-run #3 2026-09-21 (see §9; §8 is the superseded re-run #2 block)

```
Verdict: PASS_WITH_RESIDUALS
Date: 2026-09-21 (re-run #3)
Commit: f63500d (code in 65098a5) - supersedes the 5c0f1ac pin of re-run #2
GAP-1: CLOSED (re-run #2, unchanged)
GAP-2: CLOSED (re-run #2, unchanged)
GAP-3: OPEN - Minor, accepted + carded (DEBT-53)
GAP-4: CLOSED (re-run #2, unchanged)
GAP-5: CLOSED - CancelledError contained at all 4 catch-alls (facade x2, cli x2); KeyboardInterrupt/SystemExit still propagate from all 4; asyncio cancellation NOT wedged (W1)
GAP-6: CLOSED - all 11 reproductions now type-name + frame-location only, 0 sentinel hits
GAP-7: CLOSED - load_dotenv inside the try in BOTH entry points; no .env path in any log or stderr, no traceback printed
A-5 (incl. the re-run #2 sub-case): STILL OPEN - Minor, non-blocking - neither catch-all guards its OWN handling (extract_tb / relpath / log handler raising all escape) - DEBT-57
GAP-8: NEW - Minor, non-blocking - frame_location() is unsafe for a frame outside the package tree: os.path.relpath TRAVERSES above _PACKAGE_PARENT and, for a non-absolute filename, prepends the cwd (reproduced on a stdlib frozen-dataclass "<string>" frame); it also RAISES FileNotFoundError when the cwd is gone
GAP-8 at 1e8e1aa (landed mid-run, probed, NOT part of this discharge): disclosure half CLOSED by the <external>/ clamp; the raise half is STILL OPEN (only ValueError is caught, FileNotFoundError from getcwd is not) - see 9.7
__context__: PASS (re-run #2, unchanged)
Leak: PASS - 0 sentinel hits on all 11 GAP-6 paths and both GAP-7 paths; the 2 surviving str(exc) clauses (DatasetFetchFailedError, RunAborted) are typed and constructor-vetted (redact() at the transport source)
Fail-open: PASS (no probe produced a GREEN build; green run still exits 0 with a single complete marker)
Containment-REQUIRED: DISCHARGED at f63500d - discharge now covers the full committed tree, no longer pinned to an older commit
```

**Use case:** UC-01 · **Story under gate:** S-01.4 (orchestrator + CLI) · **Date:** 2026-09-21
**Red-team:** Branca · **Branch:** `feat/S-01.2-idp-adapter` · **Baseline:** 780 passed / 14 skipped, `mypy src` + `ruff check src tests` clean
**Marker discharged:** NFR-01 `Containment: REQUIRED — verified by /harden (docs/qa/HARDEN-01.md) before Done.`
**Scope:** ADR-0004 § Containment #1–#19 + abort taxonomy + exit-code contract; ADR-0005 #8/#9; INV-01/02/04/05/06/07/08; NFR-01 N1/N5/N6/N7/N10/N14/N15/N16/N21/N22/N28; the **five widened conditions** carried forward from the S-01.3 `/qa` re-audit.

> **Method.** In-code fault injection against the real `run_eval`, the real `LangfuseAdapter` and the real classifier, with typed doubles for the IDP and the platform (`PlatformAdapter`-shaped) and one local `http.server` for the 3xx probe. **No live IDP call, no live platform call, no document submitted.** Probes were throwaway, run from the session scratchpad, never added to `tests/`. `src/` and existing tests were not modified.

---

## 1. Verdict summary

| Area | Result |
|---|---|
| Abort taxonomy + fail-closed on every **typed** failure seam | ✅ CONTAINED |
| `run_status` marker discipline (written iff a run exists, never on a pre-run abort, best-effort) | ✅ CONTAINED |
| Path containment (`document_id` → local path) | ✅ CONTAINED |
| Leak envelope (golden / extracted / credential / path sentinels) | ✅ CONTAINED |
| Untrusted input at the IDP and platform **adapter** boundaries | ✅ CONTAINED |
| Env-config boundary (non-finite timeouts, bad allowlist, blank credentials) | ✅ CONTAINED |
| Fail-open class (unrecognised verdict / gate / status / non-`str` id) | ✅ CONTAINED |
| The five widened S-01.3 conditions | ✅ ALL FIVE PASS |
| **`run_eval`'s own `-> int` contract under an *untyped* exception** | ❌ **GAP-1 (Major)** |
| **The SDK seam that makes GAP-1 reachable** | ❌ **GAP-2 (Major)** |
| **Record-phase wall-clock bound** | ⚠️ **GAP-3 (Minor, accepted + documented here)** |

**Bottom line.** Nothing found produces a **silently-green build**. Every probe that changed the outcome still exited non-zero. What is broken is the *containment envelope around the abort*: on the untyped-exception class the run aborts without the platform marker (ADR-0004 #14), without the `run_end` telemetry line (N10), and without a distinguishable abort reason — and survives as non-zero only because the CLI has a catch-all that `run_eval` itself does not.

---

## 2. Conditions checked

### 2a. The five widened conditions (NFR-01, S-01.3 `/qa` re-audit #3)

| # | Condition | Result | Evidence |
|---|---|---|---|
| 1 | A3 foreign-`run_id` → typed abort, **zero writes** | ✅ PASS | `record_run(run_id="OTHER-RUN", …)` with score ids derived from `run-1` → `ExperimentRecordFailedError("… score id was not derived from the run_id passed in this call …")`; `run_experiment` calls = 0, HTTP calls = 0 |
| 2 | Malformed-record seam — every `DocumentRecord` field's **presence AND value type**, incl. **index ≥ 2** | ✅ PASS | 12 record mutations (drop/retype `item_id`, `document_id`, `scores`; drop/retype `ScoreInput.id/name/value`) + 3 `RunMetadata` mutations, each → typed `ExperimentRecordFailedError`, **0 SDK calls, 0 HTTP calls**. Malformed record at **index 2 of 3** also raises before any write. |
| 3 | Record deadline **with a real value set** — hung score POST → abort within deadline | ✅ PASS | `record_deadline_seconds=5.0`, injected client burning 10 s of the injected monotonic clock per POST → `ScoreWriteFailedError("… record-phase deadline exceeded …")` after **exactly 1** POST; no further writes; returns in < 2 s wall clock. **Disabled-default worst case documented as GAP-3.** |
| 4 | Transport 3xx — credential never reaches host B, under harden's own server | ✅ PASS | Local `http.server` returning `302 Location: http://127.0.0.1:1/stolen`; the redirect is **not followed** (host B receives 0 requests, host A receives exactly 1); neither the sentinel public key, the sentinel secret key, nor their base64 Basic pair appears in the exception text or any captured log line |
| 5 | Split-brain (`LANGFUSE_BASE_URL` ≠ `LANGFUSE_HOST`) → **zero client constructions** | ✅ PASS | `PlatformConfigurationError` raised with `UrllibHttpClient` constructions = 0 and `langfuse.Langfuse` constructions = 0; the message names variables only — no key, no host value |

### 2b. ADR-0004 containment items

| ADR-0004 item | Result | Note |
|---|---|---|
| #1/#3/#16 two timeouts, absolute budget, monotonic clock | ✅ | INV-07 already pinned at S-01.2; config boundary re-probed here (§3.6). Live values still owed by S-01.6. |
| #2/#4 poll timeout → abort, no re-submit | ✅ | `IDPPollTimeoutError` → `unknown_status_timeout`, loop stops at the failing document |
| #5 hard IDP failure | ✅ | `IDPExecutionFailedError` → `hard_failure` |
| #6/#14 bounded transient retry | ✅ | Adapter-private; `_write_score_with_retry` capped at 3, deterministic score id makes a retry an upsert |
| #7 auth split (initial vs mid-run) | ✅ | `IDPAuthenticationError` → `auth_failure` at document 1 **and** at document 2 (mid-run), both abort the whole run |
| #8/#9 empty-set vs fetch-failure split | ✅ | Distinct reasons, both non-zero, both pre-run (no marker) |
| #10/N28 pre-run golden validation before any IDP call | ✅ | Malformed golden at index 0 → `malformed_golden` with **0 adapter calls** — no IDP quota spent |
| #11/#12 platform-write failure aborts | ✅ | `ScoreWriteFailedError`/`ExperimentRecordFailedError` → `hard_failure`, exit 1 **even though every gate PASSed** |
| #13 flush failure | ✅ | `FlushFailedError` → `flush_failed`, exit 1, `aborted` marker written |
| #14 marker best-effort, one attempt | ✅ | `mark_run_status` raising does **not** flip a good run (exit stayed 0) and is never retried |
| #17 fail-closed on ambiguous/missing status | ✅ | `normalize()` raises `invalid_status` rather than inferring success |
| #18/#19 credential hygiene + redaction | ✅ | §3.4 |
| **#14 marker on the untyped-exception path** | ❌ | **GAP-1** — the marker is not written at all |

---

## 3. Probes run and observed outcomes

### 3.1 IDP failure seams — all fail closed

| Injection | Exit | Reason logged | Loop stopped | `record_run` | Marker |
|---|---|---|---|---|---|
| `IDPPollTimeoutError` | 1 | `unknown_status_timeout` | yes (1 of 2 docs) | 0 calls | `aborted` ×1 |
| `IDPExecutionFailedError(status=…)` | 1 | `hard_failure` | yes | 0 | `aborted` ×1 |
| `IDPAuthenticationError` (doc 1 — initial) | 1 | `auth_failure` | yes | 0 | `aborted` ×1 |
| `IDPAuthenticationError` (doc 2 — mid-run) | 1 | `auth_failure` | yes (2 of 2 reached, 0 after) | 0 | `aborted` ×1 |
| `MalformedIDPOutputError` | 1 | `malformed_actual` | yes | 0 | `aborted` ×1 |
| `IDPAdapterError` (submit 500 / retry exhausted) | 1 | `hard_failure` | yes | 0 | `aborted` ×1 |

No partial run is ever recorded: under ADR-0005 #9 `record_run` is the single post-loop write, so an in-loop abort leaves **zero** scores on the platform, not a truncated set. `run_end outcome=aborted exit_code=1` fires on every one of these.

### 3.2 Platform / pre-run seams

| Injection | Exit | Reason | Marker | IDP calls |
|---|---|---|---|---|
| `get_dataset` → `DatasetFetchFailedError` | 1 | `dataset_fetch_failed` | **none** ✅ | 0 |
| Dataset schema drifted | 1 | `schema_drift` | **none** ✅ | 0 |
| Dataset schema absent | 1 | `schema_drift` | **none** ✅ | 0 |
| `items == []` | 1 | `empty_set` | **none** ✅ | 0 |
| Golden item missing `type` (N28) | 1 | `malformed_golden` | **none** ✅ | **0 — no quota spent** |
| `record_run` → `FlushFailedError` **after all gates PASS** | 1 | `flush_failed` | `aborted` ✅ | — |
| `record_run` → `ExperimentRecordFailedError` **after all gates PASS** | 1 | `hard_failure` | `aborted` ✅ | — |
| `record_run` → `ScoreWriteFailedError` **after all gates PASS** | 1 | `hard_failure` | `aborted` ✅ | — |
| `mark_run_status` raising on a fully-successful run | **0** ✅ | — | attempted once, not retried | — |

**Marker rule verified exactly as specified:** the `run_status` marker is written **iff a run exists** (`run_id` generated, i.e. after the pre-run chain) and **never** on a pre-run abort. Confirmed on all five pre-run reasons.

### 3.3 Path traversal (`document_id` is Curator-controlled platform content)

`../../../etc/passwd`, `/etc/passwd`, `..`, `a/../../escape`, the unicode-escaped `../…`, the empty string, and a **symlink planted inside `IDP_DOCUMENT_DIR` pointing outside it** — all seven → exit 1, reason `path_containment_violation`, **0 adapter calls** (the escaped path never reaches the IDP submit), and the resolved/candidate path never appears in any log line.

### 3.4 Leak probes (INV-01, INV-02, DEBT-18 option B)

Sentinels planted in: the golden value, the extracted value, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `IDP_CLIENT_SECRET`, and `IDP_DOCUMENT_DIR` (a `tmp_path`).

- **Success path + gate-FAIL path:** 0 sentinel hits across `caplog`, stdout, stderr; 0 hits in the serialised `record_run` payload; the document directory path does not reach the platform. `DocumentRecord` carries `item_id` + `document_id` + verdict-literal scores only — no expected, actual or confidence **value**. ✅
- **Every abort path:** 0 sentinel hits from any code the repo owns. ✅
- **Log-line injection:** `document_id` containing `\nERROR:root:run_eval: run_end outcome=success exit_code=0` — every emitted record is a **single** line; the newline is rendered `\n` by `sanitize_for_log`; no forged telemetry line. ✅ (Same for `argparse`'s own error path at the CLI.)
- **Exception chains:** no probe surfaced a sentinel via `__cause__`/`__context__`; the adapter's deferred-raise pattern (`transport_failed` flag + `from None`) holds.
- **3xx:** see condition 4.

### 3.5 Untrusted input at the boundaries

- **IDP response:** hostile field names (`__proto__`), a 100 000-char field name, a 500 000-byte value, lone surrogates, duplicate prompts → `normalize()`'s safe-charset check, `MAX_VALUE_BYTES` cap and encodability check reject them as typed `MalformedIDPOutputError`; no traceback, no oversized value reaches the platform payload. ✅
- **Platform response:** malformed pagination (`meta.totalPages` negative/non-int/bool), `data` not a list, body not a dict, missing dataset `id`, non-`str` `item_id`, non-`str` `document_id`, non-dict `golden` → all typed `DatasetFetchFailedError`, all raised **before** `items.append` and before the `_item_cache` write; the page cap (`_MAX_DATASET_PAGES = 500`) bounds a bogus `totalPages`. ✅
- **Orchestrator ↔ platform-adapter boundary:** see GAP-1 note (b).

### 3.6 Env-config boundary

`IDP_EXECUTION_TIMEOUT_SECONDS` ∈ {`nan`, `inf`, `-1`, `0`, `banana`, `1e400`} → typed `IDPConfigurationError`, exit 1, before any network call. `IDP_SUCCESS_STATUSES` not a subset of `IDP_TERMINAL_STATUSES` → exit 1 (the `ValueError` leg is caught). Each of `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` / `IDP_CLIENT_SECRET` / `GOLDEN_DATASET_NAME` / `IDP_DOCUMENT_DIR` set to `""` **and** to `"   "` → exit 1, **0 adapter calls, 0 client constructions**, message names the variable only. ✅ N6 holds including the whitespace-only case.

### 3.7 The fail-open class (the 2026-09-21 hunt, CLAUDE.md § Rigor)

Both historical defects are **closed and re-verified independently**:

- `overall_gate` returning `PASS` for an unrecognised verdict → now raises `MalformedActualError` at **both** the top-level and per-row sites, valid set derived from `get_args(VerdictLiteral)`. Probed a drifted gate literal end-to-end: **never exit 0**.
- non-`str` `document_id` written while the call reported success → guarded at **both** trust boundaries (`get_dataset` read side and `_require_record_shape` write side); 12 record mutations all raise before any write.
- `build_score_inputs` re-checks the gate literal (`_VALID_GATES`); `mark_run_status` re-checks the status against `get_args(RunStatus)`; a platform that reports success but returns a drifted experiment result is caught by `record_experiment`'s structural checks (item-result count, every `trace_id`, single shared `dataset_run_id`).
- **Exit-code aggregation is INV-08-clean:** `exit_code` is a pure function of the in-process gates; nothing is read back from the platform.

**No path was found on which a wrong or missing value yields a GREEN build.** Every deviation exits non-zero.

---

## 4. Gaps

### GAP-1 — Major — `run_eval` has no top-level catch-all, so an **untyped** exception escapes it, losing the marker, the telemetry and the reason

`run_eval` catches only `RunAborted` (plus the named typed families). Its own docstring and `orchestration/errors.py` state that **"no `RunAborted`, or any other exception, may escape `run_eval`"** — that is false. 13 reproduced escapes.

**Reproduction (any one of these):**
```python
# facade.make_platform / facade.make_idp_adapter patched to typed doubles
platform.record_run  -> raises TimeoutError("socket hung")   # or get_dataset, or adapter.extract
run_eval(ACTION_ID, "1.0", "nightly")
# observed: TimeoutError propagates OUT of run_eval
```
Also escaping, all observed: a `dataset` whose `items` is `"not-a-list"` / `42` / `{...}` / `[None]` / `[42]` / `["str"]` (`AttributeError`/`TypeError` at `prerun.validate_golden_set`), a dataset with **no `items` key** (`KeyError: 'items'` at `check_empty_set`), an item with **no `document_id` key** (`KeyError` at `facade.py:371`), an item with a non-`str` `document_id` (`TypeError` at `os.path.isabs`), and a drifted gate literal (`ValueError` from `build_score_inputs`).

**Observed consequences at the CLI** (`cli.main([...])`, all three seams):

| Seam | exit | reason logged | `run_status` marker | `run_end` telemetry |
|---|---|---|---|---|
| `get_dataset` | 1 | generic `unexpected error` only | none | **not emitted** |
| `extract` | 1 | generic `unexpected error` only | **none — a run exists** ❌ | **not emitted** |
| `record_run` | 1 | generic `unexpected error` only | **none — a run exists** ❌ | **not emitted** |

So: **ADR-0004 #14 is violated** (an aborted run is left unmarked on the platform, readable as a small complete baseline); **NFR N10 is violated** (`_log_run_end`'s own contract is "emitted at EVERY exit point" — it is not, and no abort reason is distinguishable); **CT-04 holds only by the CLI's accident** — the `-> int` contract is broken for any other caller of the public `run_eval`, which then gets a raw traceback printing `__context__`/`__cause__` chains that may carry platform or IDP response text the codebase is otherwise careful never to log (INV-02 exposure).

**Fix shape (Dengoso, not written here):** wrap the whole body of `run_eval` after `run_id` generation in a final `except Exception` → best-effort `aborted` marker + `hard_failure` reason + `_log_run_end("aborted", 1)` + `return 1`; and a pre-run equivalent that logs `run_end` without a marker. Additionally validate the `Dataset`/`DatasetItem` shape the orchestrator consumes (`items` is a list; each item is a dict with a non-empty `str` `document_id`) as `dataset_fetch_failed`, rather than trusting the Protocol's declared type — the sole implementation validates today, a second one (Opik, Epic F) need not.

### GAP-2 — Major — the SDK call inside `record_experiment` is unwrapped, which is what makes GAP-1 reachable in production

`platform/tracing.py:119-141`: `tracing_client.run_experiment(...)`, `tracing_client.flush()` and `result.item_results` are called with **no `except Exception`** mapping to `ExperimentRecordFailedError`. Any exception the langfuse SDK raises (transport, auth, `AttributeError` on a drifted return shape) propagates untyped through `record_run` and straight out of `run_eval` — GAP-1's blast radius.

This is not hypothetical: `CLAUDE.md ## External services` records that the SDK-private pins are *"the first thing a version bump breaks silently"*, and a drifted `result.item_results` is exactly an `AttributeError`. The `_FailureWatcher` log handlers only catch failures the SDK **logs**; they do nothing for failures it **raises**.

**Reproduction:** patch the tracing client's `run_experiment` to raise any non-platform exception → it exits `record_run`, `run_eval` and `main` untyped, with no marker and no `run_end` line.

### GAP-3 — Minor — the record phase has **no wall-clock bound in the shipped configuration** (accepted, documented here per condition 3)

`make_platform()` constructs `LangfuseAdapter(client=…, tracing_client=…)` with **no `record_deadline_seconds`** — the shipped default is `None`, i.e. the DEBT-20 deadline is **disabled in production**, and there is no env var to enable it. The mechanism works (condition 3 PASS); it is simply never armed.

**Accepted worst-case bound, stated for the record:** `N_documents × (N_fields + 1)` scores × 3 attempts × (30 s transport timeout + ≤ 8 s backoff). At NFR N8's 50-document ceiling with ~50 fields that is ≈ 2 550 × 3 × 38 s ≈ **63 hours** of record phase before the run ends. It **fails safe** (a hung CI job, never a green build) but contradicts ADR-0004's no-unbounded-wait posture and NFR N3. **Accepted until S-01.6 pins the real per-score latency**; must be carded, not forgotten.

### Advisories (non-blocking — surface to `/debt` via Dunga)

- **A-1.** The orchestrator interpolates adapter/platform exception text into `detail=` through `sanitize_for_log`, which **escapes but does not redact**. Verified safe **today by construction** — every raise site in `adapter/errors.py`, `adapter/idp_client.py`, `adapter/normalize.py` and `platform/transport.py` names only a variable, an HTTP status, or a length-capped printable-only IDP status; none interpolates a value. This is an **event-shaped obligation**: the first future raise that embeds a value leaks it straight to the log. A probe that plants a sentinel in each typed exception's message and asserts it never reaches a log line would pin it.
- **A-2.** `adapter/transport.py:180` reads the whole local document into memory (`fh.read()`) with **no size cap**. A pathological file yields `MemoryError`, which is in GAP-1's unmapped class. A `MAX_DOCUMENT_BYTES` pre-check mapped to `IDPTransportError` closes both the resource cap and the escape.
- **A-3.** `IDPExecutionFailedError.status` is IDP-controlled text that is capped, stripped of non-printables and escaped, but **not redacted**, before reaching the log. Acceptable under ADR-0004 #5 ("report the IDP error detail"); recorded so it is a decision, not an oversight.

---

## 5. Explicitly out of scope

- **Live-IDP behaviour — pending S-01.6.** Real timeout values, the real retry budget, the real terminal/success status allowlists, and the live 401-refresh-then-retry path are **not** verified here. Every IDP probe used a typed double. `IDP_EXECUTION_TIMEOUT_SECONDS = 120` remains a placeholder; N1's "≥ 99% of documents within budget" is unmeasurable until S-01.6.
- **Live platform behaviour.** No live Langfuse call was made. Self-hosting obligations (encryption at rest, DB access control, backup/restore — ADR-0001/N25) are `/signoff` scope.
- **Production chaos** (killing nodes, network partitions, CI runner failure) — deploy/ops scope, outside this plugin.
- **Throughput / performance targets** — Soneca's NFRs and Zangado's perf gate. This report covers behaviour **under failure**, not under load.
- **Functional DoD and code correctness** — Zangado (`/qa`) and Atchim respectively.
- **LLM / prompt security — N/A for this system, re-confirmed.** UC-01 composes no prompt and calls no LLM: it consumes IDP's model-generated extraction output as **untrusted data** and compares it with a deterministic rule-based classifier. There is no instruction surface an injected string could hijack, no system prompt to leak, and no model-invocable tool. The relevant red-team obligation — *model-produced text is hostile input* — is discharged in §3.5 (`normalize()`'s charset, size and encodability contract) and §3.4 (that text never reaches a log line unescaped, and never reaches the platform at all). `LLM-Evals: N/A` stands.
- **ADR-0004's T-01.4.12 amendment (A1–A6)** is staged but **uncommitted**; this report red-teamed the committed ADR text plus the code as built. The amendment's A4 (marker best-effort, one attempt, never on a pre-run abort) is verified PASS regardless.
- **DEBT-48's `--dataset` flag and the four-field `RunMetadata`** are a Dunga/Zangado Done-scope item, not a containment gap; INV-04's three committed fields are written correctly on every zero-exit run.

---

## 6. What must be fixed before the epic is Done

1. **GAP-1** — blocking. `Containment: REQUIRED` cannot be discharged while an abort can occur with no platform marker, no abort reason and no `run_end` line, on a path the code's own contract says is impossible.
2. **GAP-2** — blocking. It is the reachable production trigger for GAP-1, and it is the seam CLAUDE.md already flags as the one a version bump breaks silently.
3. **GAP-3** — non-blocking, but must be **carded** (env knob + a default pinned at S-01.6), not left implicit.
4. **A-1 / A-2 / A-3** — non-blocking; `/debt add`.

Re-run this report after the fixes. The other 24 probe families need no re-run unless `orchestration/` or `platform/` changes.

---

## 7. Re-run #1 — 2026-09-21 — GAP-1 and GAP-2 only

**Red-team:** Branca · **Commit under gate:** `33aaca0` (`feat/S-01.2-idp-adapter`) · **Fixes examined:** `2f700c2` (GAP-1), `fd8cdc3` (GAP-2), with `98eacbc` (R-1) and `5af1989` (A6) alongside.

> **Method.** Detached `git worktree` at `33aaca0` in the session scratchpad, `PYTHONPATH` pinned to **that** tree's `src` (module resolution asserted: `idp_regression.__file__` resolves inside the worktree, not the editable install). 42 throwaway probes in the scratchpad; `src/` and `tests/` untouched. Repo suite re-verified in the same worktree: **804 passed / 14 skipped**. No live IDP or platform call; no document submitted. GAP-3 and A-1/A-2/A-3 were **not** re-probed (carded as DEBT-53/54) and stand exactly as written in §4.

### 7.1 GAP-1 — **STILL OPEN** (Major, blocking) — narrowed from 13 reproductions to a residual seam family

`_validate_dataset_shape()` and both catch-alls do what the commit claims. **12 of the 13 reproductions are closed.** One is not, and probing past the fix found the same defect on three further seams and one uncovered window — all of the same class, all with the same consequence (no marker, no `run_end`, no distinguishable reason, raw exception out of a `-> int` function).

**Re-run of the 13 reproductions** (`exit` / `reason logged` / `run_status` markers / `run_end` emitted):

| # | Reproduction | Escaped? | exit | Reason logged | Markers | `run_end` |
|---|---|---|---|---|---|---|
| R1 | `get_dataset` → `TimeoutError` | ❌ **YES — `TimeoutError`** | — | none | none | **not emitted** |
| R2 | `extract` → `TimeoutError` | ✅ no | 1 | `unexpected error: TimeoutError` | `['aborted']` | yes |
| R3 | `record_run` → `TimeoutError` | ✅ no | 1 | `unexpected error: TimeoutError` | `['aborted']` | yes |
| R4–R6 | `items` = `"not-a-list"` / `42` / `{...}` | ✅ no | 1 | `dataset_fetch_failed: "dataset 'items' is missing or not a list"` | none (pre-run) ✅ | yes |
| R7–R9 | `items` = `[None]` / `[42]` / `["str"]` | ✅ no | 1 | `dataset_fetch_failed: "a dataset item is not a dict"` | none ✅ | yes |
| R10 | no `items` key | ✅ no | 1 | `dataset_fetch_failed: "…missing or not a list"` | none ✅ | yes |
| R11 | item with no `document_id` | ✅ no | 1 | `dataset_fetch_failed: "…missing/non-string document_id"` | none ✅ | yes |
| R12 | non-`str` `document_id` | ✅ no | 1 | same | none ✅ | yes |
| R13 | drifted gate literal (`ValueError` from `build_score_inputs`) | ✅ no | 1 | `unexpected error: ValueError` | `['aborted']` | yes |

Marker discipline on the closed 12 is **exactly right**: the in-loop catch-all writes the best-effort `aborted` marker once and only when a `run_id` exists; the pre-run paths write none. All four INV-04 fields are carried on the catch-all's marker (`golden_dataset_name` included, A6-consistent). Every reason line logs `type(exc).__name__` only — verified by planting sentinels **inside the raised exception's own message** (§7.4).

**R1 is not an edge case, it is the first of the three seams the fix commit's own message names** ("TimeoutError from get_dataset/extract/record_run"). The `try` around `platform.get_dataset(dataset_name)` still has **only** `except DatasetFetchFailedError`; the new `except Exception` was added to the *next* `try` (the schema-drift/empty-set/N28 chain), not this one.

**Probing past the fix — three more seams of the same class, all reproduced:**

| Probe | Seam | Result |
|---|---|---|
| P6 | `make_platform()` → `TimeoutError` (SDK constructor hangs/drifts) | ❌ escapes — `except (ValueError, PlatformConfigurationError)` only |
| P7 | `make_idp_adapter()` → `OSError` | ❌ escapes — `except (RuntimeError, IDPConfigurationError, ValueError)` only |
| P9 | `validate_platform_credentials()` → `OSError` | ❌ escapes — `except MissingCredentialError` only |
| P8 | `hash_dataset` → `RecursionError` | ❌ escapes — **the window between the two catch-alls** (`hash_dataset` → `generate_run_id` → `compose_experiment_name` → the `pre-run checks passed` log line) is inside no `try` at all |

At the CLI all four, and R1, still exit **1** (`cli.py:155`'s catch-all) — so **no GREEN build** — but with **no `run_end` line, no reason and no marker**, which is the exact defect GAP-1 named. `run_eval`'s `-> int` contract remains broken for any non-CLI caller on these five paths.

**Probes that came back clean:**

| Probe | Result |
|---|---|
| P1 — the `aborted` marker write itself raises **inside** the catch-all | ✅ contained — `_mark_run_status_best_effort` swallows it, logs `mark_run_status("aborted") failed (best-effort, not retried)`, run still exits 1 with `run_end` |
| P3 — `items` is a **generator** (raises mid-iteration) | ✅ contained — `_validate_dataset_shape`'s `isinstance(items, list)` rejects it before iteration: `dataset_fetch_failed` |
| P5 — an adapter exception whose **`__str__` itself raises** | ✅ contained — the `ValueError` from `str(exc)` inside the `except` handler falls through to the in-loop catch-all: exit 1, marker, `run_end` |
| P10 — `KeyboardInterrupt` | ✅ propagates, as pinned |
| F3 — an item that passes the shape validator but has **no `golden` key** | ✅ `malformed_golden`, pre-run, exit 1, no marker |

### 7.2 GAP-2 — **CLOSED** (with a residual, logged below as GAP-4)

Driven directly against `record_experiment` with a duck-typed `ExperimentRunner`:

| Probe | Injection | Result |
|---|---|---|
| G1 | `run_experiment` → `RuntimeError` (message carrying the secret-key sentinel) | ✅ `ExperimentRecordFailedError: record_experiment: run_experiment raised RuntimeError` — **no sentinel in the message** |
| G2 | `run_experiment` → `AttributeError` (the version-bump drift class) | ✅ `ExperimentRecordFailedError: … raised AttributeError` |
| G3 | `flush()` → `RuntimeError` (sentinel-carrying) | ✅ `FlushFailedError: record_experiment: flush() raised RuntimeError` — distinct from the *logged*-flush-failure mapping, as intended |
| G4 | result object with no `item_results` | ✅ `ExperimentRecordFailedError: … has no 'item_results' attribute (SDK return shape drift?)` |
| G11 | happy path | ✅ unchanged — returns the `item_id → trace_id` map |

No SDK exception text reaches the caller (`from None` on every mapping); the orchestrator then maps each to `hard_failure` / `flush_failed` exactly as §3.2 already verified. **GAP-2's three named call sites are closed.**

### 7.3 New gaps

#### GAP-4 — Minor, non-blocking — `record_experiment`'s post-call reads are still unguarded against the same drift class

`list(result.item_results)` catches **only `AttributeError`**, and the structural loop reads `item_result.trace_id`, `.dataset_run_id` and `.item.id` with no guard at all. Reproduced, each escaping `record_experiment` **untyped**:

| Probe | Injection | Escaping exception |
|---|---|---|
| G5 | `item_results` is a lazy iterator that raises mid-iteration | `RuntimeError` — **and it carried the planted secret sentinel in its message** |
| G6 | `item_results = 42` (not iterable) | `TypeError: 'int' object is not iterable` |
| G7 | `item_results` property raises `RuntimeError` | `RuntimeError` |
| G8 | an item result with no `.trace_id` | `AttributeError` |
| G9 | `item_result.item` with no `.id` | `AttributeError` |

G8/G9 are the *same* renamed-field drift GAP-2 was raised for, one attribute deeper. **Why non-blocking:** end-to-end these now land in `run_eval`'s in-loop catch-all (verified by R3's shape) → exit 1, `aborted` marker, `run_end`, and **only the type name logged**, so the G5 message never reaches a log line. The defect is a broken `PlatformAdapter` typed-error contract, not a leak and not a fail-open. Fix shape: wrap the `item_results` materialisation in `except Exception` (not just `AttributeError`) and the per-item attribute reads in the same mapping.

#### GAP-5 — Minor, non-blocking — a `BaseException` that is neither `KeyboardInterrupt` nor `SystemExit` escapes every catch-all

`except Exception` in `run_eval` (both), in `cli.main` and in `tracing.py` all miss it. Reproduced with a bare `BaseException` subclass and, more realistically, with **`asyncio.CancelledError`** (a `BaseException` since 3.8) raised from the IDP seam (P4b) and from `run_experiment` (G10) — relevant because `run_experiment` runs items under `asyncio.gather` internally, so a cancelled task is a live source. In every case: no marker, no `run_end`, raw propagation out of `cli.main`. The process still exits non-zero (Python's own unhandled-exception exit), so **no GREEN build**. Recommend `/debt`, and recommend it be a *decision* (`BaseException` is deliberately not caught) rather than the current silence.

**Advisory A-4 (new, `/debt`):** `_validate_dataset_shape` accepts a whitespace-only `item_id` (`"  "` is truthy) — probe F1 ran green end-to-end with it. Not a containment gap (`_require_record_shape` governs the write side), but inconsistent with the N6 whitespace-strip discipline applied everywhere else in `run_eval`.

**Advisory A-5 (new, informational):** a **logging handler** that raises while emitting the `run_end` record escapes `run_eval` (probe P2 — the `aborted` marker had already been written, so only the `run_end` line and the return value are lost). Stdlib handlers swallow their own emit errors; a third-party shipper need not. Recorded, not carded as a gap.

### 7.4 Leak re-check on the new paths — **PASS**

Sentinels planted in the golden value, the extracted value, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `IDP_CLIENT_SECRET` and `IDP_DOCUMENT_DIR`, then **planted again inside the raised exception's own message** to attack the new code specifically:

- **L1** — the new `_validate_dataset_shape` errors: 0 hits. Every message is a static string naming a field, never a value.
- **L2** — the in-loop catch-all with `record_run` raising `RuntimeError("resp body <golden> <secret>")`: 0 hits across `caplog`, stdout and stderr. `type(exc).__name__` discipline holds.
- **L3** — the pre-run catch-all with `check_schema_drift` raising a message carrying the secret, the golden value **and the document-dir path**: 0 hits.
- **GAP-1/GAP-2 probe suites**: a sentinel assertion ran on every one of the 26 + 11 probes; 0 failures except the *escaping* G5 (GAP-4), which never reaches a log line.

### 7.5 Fail-open re-check on the new code — **PASS**

- No catch-all can return 0: both return a hard-coded `1`; probed on every reproduction (F2), including the case where every gate had PASSed before the failure.
- `_validate_dataset_shape` rejects, it never coerces or defaults; a dataset it accepts is still gated normally (F1, F3 — the missing-`golden` item still aborts `malformed_golden`).
- A clean run still exits 0 with a single `complete` marker (F4) — the new guards did not make the green path unreachable either.
- No new path was found on which a real failure yields a GREEN build. R1/P6/P7/P8/P9 and GAP-5 all still exit non-zero; they lose *observability*, not *safety*.

### 7.6 Verdict

**GAP-1 STILL OPEN — blocking.** The fix is correct as far as it goes and closes 12 of 13 reproductions, but the class it was raised against is still live on five seams, one of which (`get_dataset`) is named in the fix's own commit message. `Containment: REQUIRED` **cannot be discharged**; NFR-01's marker stands undischarged and S-01.4 cannot reach Done.

**Required (Dengoso):** move the `except Exception` so it covers **the whole pre-run chain** — `validate_platform_credentials`, `make_idp_adapter`, `make_platform`, `get_dataset` + `_validate_dataset_shape`, the pre-run guards, and the `hash_dataset`/`generate_run_id`/`compose_experiment_name` window — rather than one `try` inside it. A single pre-run `try:` spanning from `load_dotenv()` to just before `run_id` generation, with the existing typed `except` clauses kept in front of a final `except Exception`, closes all five at once and restores `_log_run_end`'s "EVERY exit point" contract. **GAP-4 / GAP-5 / A-4 / A-5 → `/debt`, not blocking.**

**Re-run scope for the next pass:** R1, P6–P9 only, plus a re-confirm of F2/F4. The GAP-2 probes and the 24 probe families of §3 need no third run unless `platform/tracing.py` changes again.

---

## 8. Re-run #2 — 2026-09-21 — GAP-1's five seams, GAP-4, GAP-5, A-4/A-5

**Red-team:** Branca · **Commit under gate:** `5c0f1ac` (`feat/S-01.2-idp-adapter`) · **Fixes examined:** `7d7aed3` (pre-run merge + `_frame_location`), `291bd23` (CLI fallback), `bf6b701` (`tracing.py` `__context__` / GAP-4 / GAP-5).

> **Method.** Detached `git worktree` at `5c0f1ac` in the session scratchpad, `PYTHONPATH` pinned to **that** tree's `src`, resolution asserted in-probe (`facade.__file__` resolves under `…/wt2/src/…` — the first attempt silently fell through to the editable install and was discarded and re-run). Project `.venv` interpreter. 60 throwaway probes; `src/` and `tests/` untouched. Repo suite re-verified in the same worktree: **818 passed / 14 skipped**. No live IDP or platform call; no document submitted. GAP-3 and A-1/A-2/A-3 were **not** re-probed (carded DEBT-53/54) and stand as written in §4.

### 8.1 GAP-1 — **CLOSED** (was Major, blocking)

The single merged pre-run `try` does what `7d7aed3` claims. **All 13 original reproductions and all five seams named in §7 are contained.** Each: exit 1, a reason line carrying `type(exc).__name__` + frame location only, `run_end` emitted, and a `run_status` marker written **iff** a `run_id` exists.

| Probe | Seam / injection | Escaped? | exit | `run_end` | Markers |
|---|---|---|---|---|---|
| R1 | `get_dataset` → `TimeoutError` | ✅ no | 1 | yes | none (pre-run) ✅ |
| R2 | `extract` → `TimeoutError` | ✅ no | 1 | yes | `['aborted']` ✅ |
| R3 | `record_run` → `TimeoutError` | ✅ no | 1 | yes | `['aborted']` ✅ |
| R4–R12 | `items` = `"not-a-list"`/`42`/`{…}`/`[None]`/`[42]`/`["str"]`, no `items` key, no `document_id`, non-`str` `document_id` | ✅ no (9/9) | 1 | yes | none ✅ |
| R13 | drifted gate literal (`ValueError` from `build_score_inputs`) | ✅ no | 1 | yes | `['aborted']` ✅ |
| P6 | `make_platform()` → `TimeoutError` | ✅ no | 1 | yes | none ✅ |
| P7 | `make_idp_adapter()` → `OSError` | ✅ no | 1 | yes | none ✅ |
| P9 | `validate_platform_credentials()` → `OSError` | ✅ no | 1 | yes | none ✅ |
| P8 / P8b / P8c | `hash_dataset` → `RecursionError` · `generate_run_id` → `OSError` · `compose_experiment_name` → `ValueError` (the window that had no `try` at all) | ✅ no (3/3) | 1 | yes | none ✅ |
| N2 | a **real** `RecursionError` (genuine deep recursion, not a synthetic raise) — does `_frame_location` survive walking it? | ✅ no | 1 | yes | none ✅ |

**Marker discipline is exact:** never on a pre-run abort (all pre-run probes: `marks == []`), always once on an in-loop/record-phase abort. `run_end` present on every contained path. No probe returned 0 on a failure (fail-open re-check, §8.5).

### 8.2 Probing past the fix — four residual escapes, all of the *same* class, none blocking

| Probe | Injection | `run_eval` | `cli.main` | Consequence |
|---|---|---|---|---|
| X5 / C2 / C3 | `load_dotenv()` raises (`OSError`, `UnicodeDecodeError`) | ❌ **escapes** | ❌ escapes when it is **cli's own** `load_dotenv` (C2); caught with rc 1 but **no `run_end`** when it is the facade's (C3) | **GAP-7** |
| X7 / X10 / C1 | `asyncio.CancelledError` from `extract` / `get_dataset` | ❌ escapes | ❌ **escapes `cli.main` raw** | **GAP-5**, still open here |
| X3 / X3b / C4 | `traceback.extract_tb` raises → `_frame_location` fails **inside the catch-all's own handler** | ❌ escapes (marker already written) | ❌ escapes — cli's `_frame_location` shares the same module and fails identically | **A-5 sub-case** |
| X2 | a logging handler that raises while emitting `run_end` | ❌ escapes (marker written, `run_end` and the return value lost) | — | **A-5**, unchanged |
| X6 | the window between the end of the pre-run `try` and the loop `try` (the `pre-run checks passed` `logger.info`, forced via a raising `sanitize_for_log`) | ❌ escapes | — | **A-5 family** |

**Probes that came back clean:** X1 (the `aborted` marker write itself raising **inside** the catch-all → swallowed by `_mark_run_status_best_effort`, run still exits 1 with `run_end`); X4 (an exception whose **`__str__` itself raises** → falls through to the in-loop catch-all, exit 1, marker, `run_end`); X8 `KeyboardInterrupt` and X9 `SystemExit` → **propagate, as pinned**; F4/C5 a clean run → exit 0, single `complete` marker, `run_end`.

### 8.3 GAP-4 / GAP-5 in `platform/tracing.py` — **GAP-4 CLOSED, GAP-5 CLOSED here**

| Probe | Injection | Result |
|---|---|---|
| G5 | `item_results` a lazy iterator raising mid-iteration (secret sentinel in the message) | ✅ `ExperimentRecordFailedError: … structural check failed: RuntimeError (SDK return shape drift?)` — **sentinel gone** |
| G6 | `item_results = 42` (non-iterable) | ✅ typed (`TypeError` → same mapping) |
| G7 | `item_results` **property** raises | ✅ typed |
| G8 / G9 | item result with no `.trace_id` · `item_result.item` with no `.id` (deeper attribute drift) | ✅ typed (2/2) |
| G10 / G10b / G12 | `asyncio.CancelledError` from `run_experiment` · from `flush()` · from the structural walk | ✅ all three mapped (`ExperimentRecordFailedError` / `FlushFailedError`) |
| G13 / G14 | `KeyboardInterrupt` · `SystemExit` | ✅ **propagate** — cancellation semantics are not wedged; only `CancelledError` is absorbed, and only at a seam where the caller is `run_eval`, which owns the abort |
| G1 / G3 / G11 | `run_experiment`/`flush` raising `RuntimeError`; happy path | ✅ unchanged from §7.2 |

**GAP-5 is closed in `tracing.py` and only there.** `run_eval`'s two catch-alls and `cli.main`'s are still `except Exception`, so a `CancelledError` originating at the IDP seam (X7) or the dataset seam (X10) still escapes to the terminal. Non-zero exit is preserved by Python's own unhandled-exception path — no GREEN build — but no marker, no `run_end`, no reason.

### 8.4 `__context__` — **PASS**

A secret sentinel was planted **inside the SDK exception's own message** (`RuntimeError("SDK Bearer <SENTINEL>")`) and the raised exception's chain walked to depth 10 following both `__context__` and `__cause__`:

```
chain == [('ExperimentRecordFailedError', 'record_experiment: run_experiment raised RuntimeError')]
__context__ is None · __cause__ is None · sentinel in repr(chain): False
```

Verified identically on the `flush()` mapping and on the structural-check catch-all. The deferred-raise pattern holds: `__context__` is **genuinely `None`**, not merely `__suppress_context__`-hidden.

### 8.5 Leak and fail-open re-check — **one new leak class (GAP-6), otherwise PASS**

- **Frame-location string:** `frame.filename:lineno:name` only. Probed with `IDP_DOCUMENT_DIR=/SENTINEL-DOCDIR-9f21`: the document dir does **not** appear; no golden value, no extracted value, no credential. ✅ INV-02-safe as claimed.
- **CLI fallback (`291bd23`):** forced `run_eval` itself to raise `RuntimeError("raw <SECRET-SENTINEL>")` → exit 1, **0 sentinel hits** (C6). The `sanitize_for_log(str(exc))` leak is gone. ✅
- **Fail-open:** 60 probes, **not one returned 0 on a failure**; the clean run still returns 0 with a `complete` marker. No path produces a silently-green build. ✅
- ⚠️ **GAP-6 (below)** is the one leak class found.

### 8.6 New gaps

#### GAP-6 — Minor, non-blocking — the merge widened an `str(exc)`-logging clause over the whole pre-run chain

`facade.py:391` — `except (RuntimeError, IDPConfigurationError, ValueError, PlatformConfigurationError) as exc: logger.error("run_eval: %s", sanitize_for_log(str(exc)))`. Its own comment vets it against `make_idp_adapter` / `make_platform` / `MuleSoftIDPAdapter._validate_timing` — the only raisers it covered **before** `7d7aed3`. The merge moved it in front of the *entire* pre-run chain, so it now also covers `validate_platform_credentials`, `get_dataset` (the platform SDK / transport), `check_schema_drift`, `validate_golden_set`, `hash_dataset`, `generate_run_id` and `compose_experiment_name` — none of them vetted for that clause. `sanitize_for_log` escapes, it does not redact (A-1 / DEBT-54).

Six reproductions, each planting a sentinel in the raised message and finding it in `caplog`:

| Probe | Raiser | Leaked |
|---|---|---|
| N1a | `get_dataset` → `ValueError("platform said: Bearer <SECRET>")` | secret sentinel |
| N1b | `get_dataset` → `RuntimeError("resp body golden=<GOLDEN>")` | golden value |
| N1c | `get_dataset` → `RecursionError` (a `RuntimeError` subclass — lands here, not in the catch-all) | secret sentinel |
| N1d | `check_schema_drift` → `ValueError` | secret sentinel |
| N1e | `validate_golden_set` → `RuntimeError` | golden value |
| N1f | `validate_platform_credentials` → `ValueError` | secret sentinel |
| P8 / P8c | `hash_dataset` → `RecursionError` · `compose_experiment_name` → `ValueError` (golden sentinel in the message) | golden value |

`check_schema_drift` and `validate_golden_set` handle **golden content directly**, which is what turns A-1 from a theoretical obligation into a live one. **Why non-blocking:** no raiser in the widened perimeter embeds a value *today* (A-1's construction argument still holds, now over a much larger and unvetted surface), and the leak is to the log, not to the platform, and never produces a GREEN build. **Fix shape:** drop `str(exc)` from this clause (log `type(exc).__name__` + `_frame_location`, as the catch-all beside it already does), or narrow the clause back to `IDPConfigurationError`/`PlatformConfigurationError` and let `RuntimeError`/`ValueError` fall to the catch-all.

#### GAP-7 — Minor, non-blocking — `load_dotenv()` is outside the merged `try`, in **both** entry points

`facade.py`'s `load_dotenv()` is the statement immediately **before** the merged pre-run `try`; `cli.py:122`'s is before `main()`'s own. `dotenv_support.load_dotenv` does real I/O (`Path.cwd()`, then python-dotenv reading and decoding `./.env`) — a deleted cwd, an unreadable file or a mis-encoded `.env` raises `OSError`/`UnicodeDecodeError`. Reproduced: escapes `run_eval` raw (X5); escapes `cli.main` raw when it is cli's own call (C2); caught by cli's fallback with exit 1 but **no `run_end`** when it is the facade's (C3). Same class as GAP-1, one statement short of the fix. Trivial to close: move both calls inside their existing `try`. Nothing is owed a marker at that point (no `run_id`), so only the `run_end` line and the `-> int` contract are at stake.

### 8.7 A-4 / A-5 — confirmed still only those

- **A-4** — a whitespace-only `item_id` (`"   "`) still runs green end-to-end (exit 0, `complete` marker). Re-probed: it is **not** larger than stated — the write side (`_require_record_shape`) still governs, nothing is coerced, no leak, no fail-open. Stays DEBT-57.
- **A-5** — a raising `run_end` log handler still escapes (X2), after the marker is written. One **new sub-case**, same family: an exception raised inside the catch-all's *own* handling — `_frame_location` failing (X3/X3b/C4) — escapes both `run_eval` and `cli.main`, because both call the same `traceback.extract_tb`. Realism is low (`extract_tb` is iterative and `linecache` swallows I/O errors — a genuine deep `RecursionError` was handled fine, N2), so this remains an advisory on DEBT-57, not a gap. Note it as the general principle: **neither catch-all is itself guarded**.

### 8.8 Verdict — `Containment: REQUIRED` is **DISCHARGED**

GAP-1 and GAP-2 — the two Major, blocking findings — are **closed**, verified against all 13 original reproductions, all five named seams, and eleven fresh probes past the fix. GAP-4 is closed. GAP-5 is closed where it was reachable through the SDK and remains open only as a facade/CLI observability loss. The `__context__` leak is genuinely closed. Nothing found is fail-open; **no probe on any of the 60 produced a GREEN build**, and every residual costs *observability*, never *safety*.

**`Containment: REQUIRED` (NFR-01, UC-01) is discharged as of `5c0f1ac`.** S-01.4 is unblocked from this gate.

**Carry forward to `/debt` (Dunga), none blocking:** GAP-5 (facade + cli `CancelledError`), **GAP-6** (the widened `str(exc)` clause — the highest-value of the three, it is the only reproduced leak), **GAP-7** (`load_dotenv` outside the `try`), plus the unchanged GAP-3 (DEBT-53), A-1/A-2/A-3 (DEBT-54) and A-4/A-5 (DEBT-57).

**Re-run scope if `orchestration/` or `platform/` changes again:** N1a–N1f, X5/X7/X10 and C1–C3 only. The GAP-1/GAP-2/GAP-4 suites and the 24 probe families of §3 are settled.

> ⚠️ **Scope pin, recorded 2026-09-21 at hand-off.** This verdict is pinned to **committed `5c0f1ac`**, which is what the worktree and every probe in §8 ran against. While §8 was being written, **uncommitted changes appeared in the main checkout's `src/` and `tests/`** (`orchestration/facade.py`, a new `log_sanitize.frame_location`, `tests/orchestration/test_facade.py`, `tests/orchestration/test_cli.py`) — they appear to move `load_dotenv()` inside the merged `try` (GAP-7) and to relocate `_frame_location`. **Branca did not author them and has not red-teamed them.** The discharge below does **not** extend to that working tree; if those changes land, re-run the §8.8 scope (N1a–N1f, X5/X7/X10, C1–C3) against the resulting commit.

---

## 9. Re-run #3 — 2026-09-21 — the three residuals of §8 (GAP-5 / GAP-6 / GAP-7) + the A-5 sub-case

**Red-team:** Branca · **Commit under gate:** `f63500d` (code in `65098a5`, `feat/S-01.2-idp-adapter`) · **Scope:** the three residuals §8 raised, the A-5 sub-case, and anything the fixes introduce. `orchestration/facade.py`, `orchestration/cli.py`, `orchestration/log_sanitize.py` only — no other file changed in that batch.

> **Method.** ⚠️ The detached `git worktree` used by §8 **vanished again, mid-session, for the second time** (re-confirmed live: the directory was gone between two consecutive tool calls while its sibling scratchpad files survived; `git worktree prune` then reclaimed the metadata). The tree was therefore re-materialised with **`git archive f63500d | tar -x`** into a plain, non-git directory (`scratchpad/tree3`) — no worktree metadata, nothing for a prune/GC to reclaim — and `PYTHONPATH` pinned to `tree3/src`. **Every probe asserts resolution in-band** (`facade.__file__`, `cli.__file__` and `log_sanitize.__file__` must all start with `…/tree3/src/`) before it injects anything; no probe result below was accepted without that assertion passing. Project `.venv` (CPython 3.13.5). 45 throwaway probes, run from the scratchpad, never added to `tests/`. `src/` and existing tests untouched. Suite re-run inside the exported tree: **830 passed / 14 skipped / 2 failed**, and both failures are artefacts of the export, not regressions — `tests/tooling/test_secrets_gate.py::TestLeg3AssertionNotScan::test_env_is_actually_ignored` and `::TestLockFilePin::test_lockfile_is_tracked_by_git` both shell out to `git`, which a `git archive` export has no repository for (830 + 2 = the stated 832 baseline). No live IDP call, no live platform call, no document submitted.

### 9.1 GAP-6 — **CLOSED** (this was §8's only reproduced leak)

All eight §8 reproductions re-run, plus three more raisers inside the same widened perimeter. Every one now logs **`type(exc).__name__` + `frame_location(exc)`** and nothing else; **zero sentinel hits** in `caplog` across all eleven (golden value, extracted value, platform secret key, public key, IDP client secret all planted in the raised message).

| Probe | Raiser | Type raised | Escaped? | exit | `run_end` | Sentinels leaked |
|---|---|---|---|---|---|---|
| N1a | `get_dataset` | `ValueError` (secret) | no | 1 | yes | **none** |
| N1b | `get_dataset` | `RuntimeError` (golden) | no | 1 | yes | **none** |
| N1c | `get_dataset` | `RecursionError` (secret) — the `RuntimeError`-subclass case | no | 1 | yes | **none** |
| N1d | `check_schema_drift` | `ValueError` (secret) | no | 1 | yes | **none** |
| N1e | `validate_golden_set` | `RuntimeError` (golden) | no | 1 | yes | **none** |
| N1f | `validate_platform_credentials` | `ValueError` (secret) | no | 1 | yes | **none** |
| P8 | `hash_dataset` | `RecursionError` (golden) | no | 1 | yes | **none** |
| P8c | `compose_experiment_name` | `ValueError` (golden) | no | 1 | yes | **none** |
| N1g | `make_platform` | `RuntimeError` (secret) | no | 1 | yes | **none** |
| N1h | `make_idp_adapter` | `ValueError` (IDP client secret) | no | 1 | yes | **none** |
| N1i | `generate_run_id` | `RuntimeError` (golden) | no | 1 | yes | **none** |

Marker discipline held on all eleven (`marks == []` — every one is a pre-run abort, so nothing is owed a marker).

**Probe for other surviving `str(exc)` paths — two remain in `facade.py`, both vetted, neither a new gap.**

- `except DatasetFetchFailedError` (`facade.py:439`) still logs `sanitize_for_log(str(exc))`. Probed with a sentinel-bearing message (N3): it reaches the log verbatim aside from quoting — **as designed**, because this type is constructed only inside `langfuse_adapter.get_dataset`, where every message is either a literal or, on the one interpolating site (`langfuse_adapter.py:406`, `f"get_dataset transport failure: {exc}"`), built from a `TransportError` whose own message has already been through **`redact()`** at `platform/transport.py:131/141`. The vetting chain is real and traced, not assumed.
- `except RunAborted` (`facade.py:443`, `:596`) logs `str(exc)`, whose detail is orchestrator-built from the typed adapter errors (N4). Same A-1/DEBT-54 family, unchanged.

No third `str(exc)` path exists in the orchestration package (`prerun.py:159` interpolates `document_id`, not an exception, and is already sanitized).

### 9.2 GAP-7 — **CLOSED**, in both entry points

`load_dotenv()` now sits inside the merged pre-run `try` in `run_eval` and inside `main()`'s own `try`. Probed with a `PermissionError(13, …, "/very/secret/deploy/path/.env")` and a `UnicodeDecodeError` naming the same path.

| Probe | Raiser | Entry point | Escaped? | exit | `.env` path in log | in stderr | raw traceback |
|---|---|---|---|---|---|---|---|
| X5-oserr / X5-ude | facade's `load_dotenv` | `run_eval` | no | 1 (+`run_end`) | **no** | — | — |
| C2-oserr / C2-ude | cli's own `load_dotenv` | `cli.main` | no | 1 | **no** | **no** | **no** |
| C3-oserr / C3-ude | facade's `load_dotenv`, reached through the CLI | `cli.main` | no | 1 (+`run_end`, now emitted) | **no** | **no** | **no** |

The `run_end`-loss half of §8's C3 is gone too: because the facade now catches it, the `run_end` line is emitted before the CLI ever sees the return value. Nothing is owed a marker at that point (no `run_id`), and none was written. The `OSError` lands in the type-name-only catch-all, the `UnicodeDecodeError` in the (now equally safe) `ValueError` clause — `UnicodeDecodeError` is a `ValueError` subclass, which would have been a live `.env`-path leak before the GAP-6 fix and is not one now.

### 9.3 GAP-5 — **CLOSED**

| Probe | Injection | Escaped? | exit | `run_end` | Markers |
|---|---|---|---|---|---|
| X7 | `CancelledError` from `extract` (in-loop) | no | 1 | yes | `['aborted']` ✅ |
| X10 | `CancelledError` from `get_dataset` (pre-run) | no | 1 | yes | none ✅ |
| X11 | `CancelledError` from `record_run` (record phase) | no | 1 | yes | `['aborted']` ✅ |
| C1 | `CancelledError` from `run_eval`, caught by `cli.main`'s second catch-all | no | 1 | — | — |
| C1b | `CancelledError` from cli's own `load_dotenv`, first catch-all | no | 1 | — | — |

**`KeyboardInterrupt` / `SystemExit` still propagate from every one of the four catch-alls** — 8/8 probes (`prerun`, `inloop`, `cli_dotenv`, `cli_run` × `KI`, `SE`) raised out of the call, none returned an exit code. The `(Exception, asyncio.CancelledError)` tuple is doing exactly the narrow widening it claims; no `except BaseException` crept in.

**Does absorbing `CancelledError` wedge a caller relying on cancellation? No (W1).** `run_eval` was driven from inside an `asyncio` task that had already called `task.cancel()` on itself; `run_eval` swallowed the injected `CancelledError` and returned `1`, and the outer `await` **still raised `CancelledError`** and the task still finished cancelled. The reason is structural, not incidental: `run_eval` is synchronous and contains no `await`, so it is never a cancellation delivery point — a `CancelledError` arriving in its frame can only have been *raised by a callee as a value*, never *delivered by the event loop*. Absorbing it costs a caller nothing; the loop re-delivers at the next real suspension point.

### 9.4 A-5 sub-case — **STILL OPEN** (Minor, non-blocking, DEBT-57)

The move of `_frame_location` into a shared `log_sanitize.frame_location()` did not change this, and was not meant to. **Neither catch-all guards its own handling**, in either module:

| Probe | Injection | `run_eval` pre-run | `run_eval` in-loop | `cli.main` |
|---|---|---|---|---|
| A5-4 | `traceback.extract_tb` raises | ❌ escapes | ❌ escapes (marker `aborted` already written) | ❌ escapes |
| A5-5 | `os.path.relpath` raises (the line the dedup added) | ❌ escapes | — | — |
| A5-6 | a log handler raises **while emitting the type-name line itself** | ❌ escapes | — | — |

Clean on the paths that were expected to be clean: **A5-1** an exception whose `__traceback__` is `None` → `frame_location` returns the literal `'<no traceback>'`, no raise; **A5-7** an abort raised while the marker write is *already* failing (`record_run` raises **and** `mark_run_status` raises) → contained, exit 1, `marks == ['aborted']` attempted-and-swallowed, `run_end` present; **A5-8** the transition into the loop (a raising `sanitize_for_log` on the 7th call, i.e. after the `pre-run checks passed` line's own arguments) → contained, exit 1, marker, `run_end` — the §8 X6 window is genuinely closed by the widened `try`.

Realism is unchanged and still low, so this stays an advisory on DEBT-57 rather than a blocking gap — **with one exception, which is new and is not low-realism:**

#### GAP-8 — NEW — Minor, non-blocking — `frame_location()` is unsafe for a **non-absolute** frame filename

The dedup added `os.path.relpath(frame.filename, _PACKAGE_PARENT)` to strip the deployment path. That is correct for package frames (Python ≥ 3.11 guarantees `__file__` is absolute, so `relpath` is a pure string operation there). It is **not** correct for a last frame that is *not* a real file — and such a frame is entirely ordinary, because `exec`-generated stdlib code carries the filename `<string>`. Two consequences, both reproduced:

- **Path disclosure — the very thing the fix was added to prevent.** `relpath` calls `abspath`, which prepends the **cwd** to a non-absolute filename. Reproduced on a stdlib frozen-`dataclass` `__setattr__` (`FrozenInstanceError`) and on a `namedtuple` `TypeError` — both last frames are `<string>` — and on an `exec`'d module: the rendered location was `'../../../../../../../var/folders/g2/…/pytest-of-alex/pytest-819/test_a5…/<string>:1:<module>'`, i.e. **the absolute working directory, spelled out**, reached through a `../` chain whose depth alone already discloses the cwd's depth. This is a live INV-02 path-disclosure on an exception class that real code raises.
- **`frame_location` itself raises.** With the cwd deleted (a real condition: a run whose working directory is removed under it) and a non-absolute frame filename, `abspath` calls `os.getcwd()` and `frame_location` raised **`FileNotFoundError: [Errno 2]`** — *inside the catch-all's own handler*, so it escapes `run_eval` (A5-2b). This is the A-5 family with a concrete, non-contrived trigger, which is why it is broken out rather than folded into DEBT-57.

**Why non-blocking:** both consequences cost *observability* and *a directory name*, never safety — no golden value, no extracted value, no credential, and no GREEN build (the escape still exits non-zero through Python's own unhandled path). **Fix shape:** `if not os.path.isabs(frame.filename): return f"{frame.filename}:{frame.lineno}:{frame.name}"` before the `relpath`, and wrap the whole body in a `try: … except Exception: return "<unavailable>"`, which closes the A-5 sub-case for this function at the same time.

### 9.5 Leak and fail-open re-check — **PASS**

- **Leak:** 0 sentinel hits across all 11 GAP-6 probes and both GAP-7 probes (log *and* stderr). The two surviving `str(exc)` clauses are typed and vetted to their construction sites (§9.1). The one new disclosure is GAP-8's cwd, which is a directory name, not project data.
- **Fail-open:** no probe of the 45 returned `0` on a failure. Control runs: a non-aborting run whose extracted value differs from the golden → exit 1, marker `complete`, one `record_run`, `run_end` (**a gate failure, correctly not an abort**); a matching run → **exit 0**, marker `complete`, `run_end`. The 0/non-zero CI-gate contract (CT-04) is intact.

### 9.6 Verdict — `Containment: REQUIRED` remains **DISCHARGED**, and the discharge now covers the full tree

The three residuals §8 carried are **closed**: GAP-6 (the only leak it reproduced) on all eleven raisers, GAP-7 in both entry points, GAP-5 at all four catch-alls without wedging cancellation and without swallowing `KeyboardInterrupt`/`SystemExit`. The A-5 sub-case is unchanged and stays advisory, and the fixes introduced exactly one new finding, **GAP-8**, which is Minor and non-blocking.

**`Containment: REQUIRED` (NFR-01, UC-01) is discharged as of `f63500d`.** The §8 scope pin is **lifted**: this run was executed against the committed tree that contains the changes §8 explicitly refused to cover, so the discharge is no longer pinned to `5c0f1ac` and no longer excludes an unreviewed working tree. S-01.4 is unblocked from this gate.

**Carry forward to `/debt` (Dunga), none blocking:** **GAP-8** (new — `frame_location` on a non-absolute frame filename; the highest-value of the open items, it is the only reproduced disclosure and the only non-contrived way the catch-all's own handler raises), A-5 incl. its sub-case (DEBT-57, unchanged), GAP-3 (DEBT-53), A-1/A-2/A-3 (DEBT-54), A-4 (DEBT-57). **DEBT items for GAP-5/GAP-6/GAP-7 can be closed.**

**Re-run scope if `orchestration/` changes again:** N1a–N1i, X5/C2/C3, X7/X10/X11/C1/C1b, the 8 `KI`/`SE` propagation probes and A5-1…A5-8. Everything else in §§3–8 is settled.

### 9.7 Scope note — a newer commit landed **during** this run, and the working tree is dirty again

Recorded for honesty, as §8's pin was. `feat/S-01.2-idp-adapter` advanced from `f63500d` to **`1e8e1aa`** ("fix(orchestration): clamp frame_location to `<external>` outside the package tree") **while §9 was being probed and written**, and the checkout's `src/idp_regression/orchestration/{facade,cli,log_sanitize}.py` and `tests/orchestration/test_{facade,cli}.py` are **currently modified-uncommitted** relative to it, their content reverting to the `f63500d` text (the shape of an in-flight mutation-test restore, but Branca did not author it and cannot vouch for it).

**Everything in §§9.1–9.6 was probed against committed `f63500d`**, as scoped, and stands. `1e8e1aa` was additionally probed **only** for its effect on GAP-8, because it lands on exactly that finding:

| Probe (at `1e8e1aa`) | Result |
|---|---|
| frozen-`dataclass` `__setattr__` (`<string>` frame) | `'<external>/<string>:15:__setattr__'` — **cwd disclosure closed** |
| `json.loads('{')` (stdlib frame, absolute, outside the package) | `'<external>/decoder.py:361:raw_decode'` — the `../../../Users/…` traversal is **closed** too |
| exception with `__traceback__ is None` | `'<no traceback>'` — unchanged, no raise |
| non-absolute frame filename with the **cwd deleted** | ❌ **still raises `FileNotFoundError`** — the clamp catches only `ValueError`, and the `getcwd()` inside `abspath` raises `FileNotFoundError`/`OSError` |

So `1e8e1aa` closes GAP-8's **disclosure** half (and more thoroughly than §9.4 asked for — it clamps the absolute-path traversal case as well), and leaves GAP-8's **raise** half open: `frame_location` can still raise inside the catch-all's own handler. One-line completion: catch `OSError` alongside `ValueError`, or wrap the whole body.

**The discharge in §9.6 is pinned to `f63500d`.** It does *not* extend to `1e8e1aa` or to the current dirty tree; nothing probed at `1e8e1aa` weakens it (the one commit between them strictly improves containment), but a full re-gate at whatever commit the tree settles on should re-run the §9.6 scope.
