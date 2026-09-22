# HARDEN-01 — Containment report — UC-01 baseline regression

**Verdict: ❌ GAPS**

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
