# Architecture brief — IDP Regression Tester

**Audience:** a sponsor who used to be a Solutions Architect. Assumes
seams, contracts and failure modes are the interesting part; does not
assume familiarity with this codebase or with Anypoint IDP.

**How to use this:** the four levels below go from context diagram down to
component internals. Each level stands on its own — stop at whatever depth
the conversation wants. Level 4 and the cross-cutting section are where an
SA will actually push.

---

## Level 0 — What it is, in one breath

- **Problem:** document extraction runs on a prompt. Someone edits that
  prompt and publishes a new version. Nothing errors — the extraction still
  "succeeds", with high confidence — but a field is now quietly wrong.
  Today that surfaces when a customer complains.
- **What this does:** re-runs a curated set of documents through the new
  prompt version, compares every extracted field against a known-good
  answer, and returns a **pass/fail exit code** a pipeline can act on.
- **Shape:** a Python library + CLI. Not a service. No web framework, no
  daemon, no database of its own.
- **Positioning:** it is a **regression gate**, not a quality score. The
  question it answers is "is this version worse than the last one", not
  "is this extraction good".

---

## Level 1 — Context (the black box and its four neighbours)

```
    ┌─────────────────┐
    │  Prompt         │  edits + publishes a new action version
    │  Engineer       │
    └────────┬────────┘
             │
             ▼
   ┌──────────────────────┐  submit + poll      ┌──────────────────┐
   │                      │ ──────────────────► │ MuleSoft         │
   │  IDP Regression      │ ◄────────────────── │ Anypoint IDP     │
   │  Tester              │  extracted fields   └──────────────────┘
   │  (library + CLI)     │
   │                      │  golden set in,     ┌──────────────────┐
   │                      │ ◄─────────────────► │ Langfuse         │
   │                      │  verdicts out       │ (self-hosted)    │
   └───┬──────────────┬───┘                     └──────────────────┘
       │              │
       │ exit code    │ run artifact (JSON, local disk, 0600)
       ▼              ▼
   ┌─────────┐    ┌──────────────────┐
   │ CI      │    │ Prompt Engineer  │
   │ pipeline│    │ (what went wrong)│
   └─────────┘    └──────────────────┘
```

- **Two external dependencies, deliberately.** Anypoint IDP (the system
  under test) and Langfuse (golden-set storage + run history). Nothing else.
- **Two outputs, deliberately split.** A machine-readable **exit code** for
  the pipeline, and a human-readable **run artifact** on local disk for the
  engineer. They serve different readers and live in different places.
- **Three personas:** the Prompt Engineer (runs regressions), the Golden Set
  Curator (a non-engineer who maintains expected answers through the
  Langfuse UI — no code, no repo access), and the CI pipeline.

---

## Level 2 — Four components, one dependency direction

```
        orchestration/          ← the only layer that knows about all others
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
 adapter/ → classifier/ → platform/
 (I/O)      (pure)       (I/O)
```

- **The arrow never reverses.** `adapter → classifier → platform`, with
  `orchestration` composing them. No component calls back up.
- **Rough weight** (source lines, indicative of where the complexity lives):

  | Package | Lines | Role |
  |---|---|---|
  | `adapter/` | ~1,970 | the IDP boundary — auth, transport, polling, shape normalisation |
  | `classifier/` | ~830 | the comparison and the gate. **Pure.** |
  | `platform/` | ~2,120 | the Langfuse boundary — datasets, scores, traces |
  | `orchestration/` | ~3,780 | the run facade, CLI, pre-run guards, version watcher |

- **The point of the split:** two of the four are pure I/O boundaries
  against volatile third-party shapes, one is pure logic, and one composes
  them. The volatile parts are the ones that get rewritten when a vendor
  changes; the logic is the part that must never silently change.

### The seam that matters most: `classifier/` is pure

- **No I/O. No network, no disk, no logging.** It takes two dicts and
  returns a dict.
- **Imports nothing but the standard library and itself** — verified: its
  entire import surface is `re`, `datetime`, `typing`, `collections.abc`,
  and its own three modules.
- **Why this is the design decision to defend:** this is the component that
  decides whether a build is red or green. Keeping it pure means it is
  exhaustively testable without a network, a vendor account, or a fixture
  server — and it means a vendor change *cannot* alter a verdict. The whole
  CI gate is this module; everything else is plumbing to feed it.

---

## Level 3 — What each component owns

### `adapter/` — the IDP boundary

- **Owns:** OAuth token acquisition + caching, HTTP transport, document
  submit, the poll loop, and `normalize()` — mapping IDP's response shape
  into a stable internal contract.
- **Key decision — a no-redirect opener.** The HTTP client refuses to
  follow 3xx, so a redirect can never re-send the Bearer token to another
  host. Credentials leaving the intended origin is the failure you cannot
  take back.
- **Key decision — a configurable terminal-status allowlist**, not a
  hardcoded `== "SUCCEEDED"`. Test-enforced (`test_no_hardcoded_succeeded_
  comparison_in_adapter`): a vendor adding a status must be a config change,
  not a silent mis-read.
- **Key decision — no raw exception escapes.** Every failure becomes a typed
  error, and no extracted value, token or filesystem path appears in a
  message, log, or exception chain. The thing being tested is financial
  documents; the error path is a disclosure surface like any other.
- **Deliberately does NOT:** interpret values, decide pass/fail, or know
  what a golden set is.

### `classifier/` — the comparison and the gate

- **Owns:** `classify(golden, actual)` → a verdict per field, and
  `overall_gate(verdicts)` → `PASS` / `FAIL`.
- **Two-tier comparison per field type** (`number`, `date`, `id`, `text`):
  - equal on the **value tier** → `match`
  - equal only on the **format tier** → `wrong_format`
  - neither → `wrong_value`
  - This is what lets `1,250.00` vs `1250.00` be a formatting note rather
    than a regression, while `1250.00` vs `1150.00` is a hard failure.
- **Six verdicts**: `match`, `missing`, `wrong_value`, `wrong_format`,
  `new_field`, `new_line`.
- **Gate rules:**
  - `missing` or `wrong_value` on a **critical** field → `FAIL`
  - `wrong_format` on a field marked **`format_critical`** → `FAIL`
  - `new_field` / `new_line` → never fail. *Extending* an extraction must
    not turn a baseline red, or nobody will extend anything.
- **Line items pair by `match_key`, not by row position** — reordering is
  not a diff. Within a repeated key (one SKU on two lines — a split
  shipment) rows pair by content, so legitimate duplicates don't false-fail.
- **Deliberately does NOT:** know about IDP, Langfuse, files, or the
  network.

### `platform/` — the Langfuse boundary

- **Owns:** fetching the golden dataset, recording run results, and marking
  run status.
- **The interface is three methods** — `get_dataset`, `record_run`,
  `mark_run_status` — expressed as a `Protocol`. **Langfuse is swappable**;
  the vendor SDK import is confined to a single factory function, and a test
  enforces that no vendor reference appears outside this package.
- **Key decision — "record after".** The orchestrator computes *every* gate
  first and writes **nothing** during its loop; then one call replays the
  stored results. A run that dies halfway leaves no half-written history to
  misread.
- **Key decision — idempotent writes.** Every score id is a `uuid5` derived
  from `run_id | document_id | score_name`. A retry overwrites itself
  instead of double-counting.
- **Deliberately does NOT:** decide anything. It records verdicts that were
  already computed.

### `orchestration/` — composition, policy, and the operator surface

- **Owns:** the `run_eval` facade, the CLI, the pre-run guards, and the
  version watcher.
- **Pre-run guards, in order, before any document is touched:** schema
  drift → empty dataset → golden-set structural validation. Cheap checks
  first; nothing is submitted to a paid service until the run is known to be
  well-formed.
- **Key decision — run identity comes from the invocation, never from
  ambient config.** `--org`, `--action`, `--version`, `--dataset` are all
  required with no environment fallback. Every value that defines *what a
  run measured* must be visible in the command line and, for CI, in the
  reviewed workflow file.
- **Eleven typed abort reasons**, each mapped to an exit path:
  `dataset_fetch_failed`, `schema_drift`, `empty_set`, `malformed_golden`,
  `hard_failure`, `auth_failure`, `unknown_status_timeout`,
  `malformed_actual`, `flush_failed`, `path_containment_violation`,
  `quota_ceiling_exceeded`.
- **Why a closed taxonomy matters:** "the run failed" is not actionable.
  "the golden schema drifted" and "the IDP token expired" go to different
  people.

---

## Level 4 — Cross-cutting concerns

### Data boundary (the one to lead with — it's a design decision, not an accident)

- **Document files never reach the evaluation platform.** Only a
  `document_id` and the expected fields are stored there.
- **No extracted value, expected value, or confidence is written to the
  platform.** Score comments and trace spans carry **verdicts only**.
- **The values live in one place:** a per-run JSON artifact on local disk,
  written `0700`/`0600`, gitignored, never transmitted.
- **Consequence to state honestly:** the platform can tell you *that*
  `invoice_date` regressed, not *what it extracted instead*. That was a
  deliberate trade — disclosure surface over convenience.

### Failure posture

- **Fail closed, not open.** An unrecognised verdict raises rather than
  falling through to `PASS`. A golden whose field/table/prompt names collide
  is rejected rather than letting one silently overwrite another.
- **Why this is emphasised:** the worst outcome this system can produce is
  not a crash — it is a **silently green build on a broken prompt**. Two
  such defects were found and fixed during live testing; both would have
  produced trustworthy-looking green.
- **Abort semantics:** a failed run produces **no partial history**. Either
  the whole run records or none of it does.

### Idempotency and retry

- **Score writes:** `uuid5`-derived ids, so retries converge.
- **Golden dataset items:** `uuid5`-derived ids, so re-provisioning is an
  upsert. (This was a real defect — it used to append, silently doubling the
  dataset and double-charging extraction quota. Found by running the thing.)
- **Schema drift guard:** the dataset carries a hash of the golden schema it
  was provisioned with. If the committed schema changes without
  re-provisioning, every run aborts loudly instead of validating against a
  stale contract.

### The CI contract

- **Exit `0`** — gate passed.
- **Exit `1`** — gate failed, or the run aborted.
- **Exit `2`** — invalid arguments.
- That is the entire integration surface. No parsing of stdout, no artifact
  scraping, no callback.

### Enforcement — the boundaries are tested, not just documented

- Vendor SDK references confined to `platform/`.
- MuleSoft host references confined to `adapter/`.
- `urllib` confined to `adapter/` and `platform/`.
- **Architecture that is only written down drifts.** These are assertions in
  the suite, so a violation fails the build.

### Test posture

- **1,184 tests**, plus strict type-checking and linting on every change.
- **Mutation testing on the critical paths:** a fix is not accepted until
  the change has been deliberately reverted and the suite confirmed to go
  red. A passing test that cannot fail is worse than no test.

---

## The runtime flow, end to end

1. **CLI** parses the run identity (`--org`, `--action`, `--version`,
   `--dataset`, `--run`) — all explicit.
2. **Pre-run guards:** schema drift → empty set → golden validation. Abort
   here costs zero extraction quota.
3. **Per document, in a loop:**
   - `adapter` submits the file to IDP and polls to a terminal status
   - `adapter.normalize()` maps the response to the internal contract
   - `classifier.classify()` compares it to the golden
   - `classifier.overall_gate()` produces PASS/FAIL
   - **nothing is written anywhere yet**
4. **After the loop:** one `record_run` replays every result to Langfuse and
   attaches verdict-only scores.
5. **The run artifact** is written to local disk with the full detail.
6. **Exit code** returns to the caller.

---

## Deployment modes (all the same one-shot core)

- **Interactive CLI** — an engineer testing a change by hand.
- **Foreground watcher** — polls for newly published action versions and
  prints a ready-to-run command on detection. **Detect-and-notify by
  default**; auto-run exists but is off, because spending extraction quota
  unattended should be an explicit choice. A poll tick costs ~11 HTTP calls
  and **zero** documents.
- **Scheduled (`launchd`)** — a local timer with an overlap lock and dated
  logs. Built, kept, not the primary path.
- **CI workflow** — the same one-shot CLI invoked by an external scheduler.
  Never a self-triggering daemon. Retained for when a hosted platform
  exists.
- **The architectural point:** all four are the *same* one-shot invocation.
  Scheduling is not a system property; it is a caller.

---

## Current limits — state these before you're asked

- **Scale is unproven.** Exercised at 3–5 documents. Nothing here
  characterises 50, or concurrency, or quota behaviour at volume.
- **Line-item regressions aren't on the dashboard.** They fail the gate and
  appear in the local artifact, but only top-level fields are scored to the
  platform today.
- **The golden set is the ceiling.** If a curated answer is wrong, the gate
  is confidently wrong with it. Curating that set is a real, ongoing job —
  and it is the line item a sponsor has to resource.
- **Self-hosting obligations are outstanding** — encryption at rest, DB
  access control, and a tested backup for the Langfuse instance are required
  before real (non-synthetic) golden data goes in.
- **One vendor, one action, one point in time.** The IDP response shape is
  pinned to what has actually been observed live, not to documentation.

---

## Questions an SA will probably ask

- **"Why not a service?"** — Nothing needs to be long-running. A gate is a
  function from (version, golden set) to a verdict. Making it a service adds
  availability, deployment and state concerns to something a pipeline can
  invoke directly. The watcher is a convenience wrapper, not the product.

- **"How do I swap Langfuse out?"** — Implement three methods and change one
  factory function. The vendor SDK is confined to that factory, and a test
  enforces that confinement. The classifier and adapter are untouched by
  the choice.

- **"What stops this becoming a flaky gate nobody trusts?"** — Three things:
  the decider is pure and exhaustively tested; format-only differences are
  informational by default, so cosmetic churn doesn't cry wolf; and
  legitimate data shapes (duplicate SKUs, extra extracted fields, sparse
  documents) are explicitly non-failures. A gate that fires on correct
  output gets switched off.

- **"What's the blast radius when IDP changes its response shape?"** —
  `normalize()`, one function, one package. The classifier's contract is
  internal and unaffected. That is the reason the boundary exists.

- **"Can it tell me *why* a build went red?"** — Yes, but in two places by
  design: the platform shows which field changed verdict; the local artifact
  shows the actual value. Splitting those is the data-boundary decision, not
  an oversight.

- **"What's the biggest risk?"** — Not a crash. A **false green** — the gate
  passing a genuinely regressed prompt. That is the failure mode the design
  optimises against, and the one live testing has actually caught twice.
