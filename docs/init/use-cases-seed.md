# Candidate use cases (seed for /discover)

Draft use cases in the format Feliz expects: actor, goal, flow, acceptance criteria in
Given/When/Then, plus an `EX-n` example table. **Provenance:** every example here is
assistant-drafted — tagged `[approved]` at best. `/discover` will (correctly) ask you for at
least one example in your own words, ideally one that should FAIL, before building. Split any
UC whose example table would exceed ~5 rows.

---

## UC-A1 — Run a baseline regression over a golden set

**Actor:** Prompt Engineer
**Goal:** Establish the known-good reference result for the current action version.
**Epic:** B, D

**Flow:** engineer selects a golden set → runs the tool at the baseline version → the tool
extracts each document, classifies against the golden, writes per-field scores and a gate to a
named run.

**Acceptance criteria**
- **Given** a golden set of N documents and the baseline action version, **when** the engineer
  runs a baseline, **then** every document is extracted, classified, and scored, and a run
  named "baseline" holds one `field:<name>` score per field plus a `gate` score.
- **Given** a document whose extraction matches its golden exactly, **then** every field verdict
  is `match` and its gate is `PASS`.
- **Given** the run completes, **then** it records the action version and golden version used.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-A1-1 | golden of 5 invoices, all extract correctly → 5 gates PASS, all verdicts `match` | [approved] |
| EX-A1-2 | one invoice's `total` is empty in output → that field `missing`, gate FAIL (total is critical) | [approved] |
| EX-A1-3 | IDP execution times out for one doc → run surfaces the error, does not silently pass | [approved] |

---

## UC-A2 — Compare a candidate prompt against the baseline

**Actor:** Prompt Engineer
**Goal:** Decide whether a changed prompt/version is better or worse than baseline.
**Epic:** B, D

**Flow:** engineer runs the tool at the candidate version over the same golden set → the tool
produces a candidate run → engineer compares candidate verdicts to baseline verdicts.

**Acceptance criteria**
- **Given** a baseline run exists, **when** the engineer runs the candidate version with a
  distinct run name, **then** a second run is scored over the same golden set.
- **Given** the candidate fixes a previously-`wrong_value` field, **then** that field's verdict
  changes to `match` in the candidate run.
- **Given** the candidate regresses a critical field to `wrong_value`, **then** that document's
  candidate gate is `FAIL`.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-A2-1 | candidate fixes `invoice_date` on 3 docs, breaks none → 3 verdicts flip to `match`, gates PASS | [approved] |
| EX-A2-2 | candidate improves one doc but breaks `total` on another → mixed: one PASS→still PASS, one PASS→FAIL | [approved] |
| EX-A2-3 | candidate reformats every date (`2024-03-15` → `March 15, 2024`) → verdicts stay `match` (format-equivalent), no false regression | [approved] |

---

## UC-A3 — Extend a prompt to extract a new field without a false failure

**Actor:** Prompt Engineer
**Goal:** Add extraction of a field not in the golden without turning the run red.
**Epic:** B

**Acceptance criteria**
- **Given** the candidate extracts a `discount` field absent from the golden, **when** the run
  is scored, **then** `discount` is verdict `new_field` and does not affect the gate.
- **Given** the candidate emits a line-item row not present in the golden, **then** that row is
  `new_line` and does not fail the gate.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-A3-1 | candidate adds `discount` field → `new_field`, gate unaffected | [approved] |
| EX-A3-2 | candidate adds an extra line item "Widget B" → `new_line`, gate unaffected | [approved] |
| EX-A3-3 | candidate both adds `discount` AND breaks critical `total` → `discount` `new_field` (pass-ish) but gate FAIL on `total` | [approved] |

---

## UC-B1 — Baseline-diff without a curated golden

**Actor:** Prompt Engineer
**Goal:** Check whether a prompt change moved anything for a document type that has no golden yet.
**Epic:** B, D

**Acceptance criteria**
- **Given** a captured prior execution output as the reference and a candidate version, **when**
  the engineer runs a baseline-diff, **then** the same classifier compares candidate output to
  the prior output field by field.
- **Given** no field changed, **then** all verdicts are `match`.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-B1-1 | candidate output identical to prior run → all `match` | [approved] |
| EX-B1-2 | candidate changes one value vs prior run → that field `wrong_value` (flagged, not gated as critical unless configured) | [approved] |

---

## UC-C1 — Curator corrects an expected value in the platform UI

**Actor:** Golden Set Curator (non-engineer)
**Goal:** Fix a wrong expected value in a golden without touching code.
**Epic:** C

**Acceptance criteria**
- **Given** a golden item with a wrong expected `total`, **when** the curator edits it through
  the platform UI, **then** the corrected value is stored and used by the next run.
- **Given** the edit produces a malformed value, **then** the UI rejects it (schema validation).
- **Given** the platform versions datasets, **then** the correction is recorded as a new golden
  version.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-C1-1 | curator fixes `total` from `1150.00` to `1250.00` in form mode → next run uses `1250.00` | [approved] |
| EX-C1-2 | curator types `twelve fifty` into a number field → rejected by schema | [approved] |

> Note: EX-C1-1 assumes Langfuse form-mode. On Opik, nested-golden editing goes through the SDK
> or a custom surface — this UC's feasibility depends on the platform decision.

---

## UC-D1 — CI gate blocks a regressing prompt-change PR

**Actor:** CI Pipeline
**Goal:** Prevent a prompt change that regresses a critical field from merging.
**Epic:** D

**Acceptance criteria**
- **Given** a PR that changes the extraction prompt, **when** CI runs the candidate regression,
  **then** the process exits non-zero if any document's gate is `FAIL`.
- **Given** all gates `PASS`, **then** the process exits zero and the PR is unblocked.

| EX-n | Scenario → expected outcome | Provenance |
|---|---|---|
| EX-D1-1 | candidate regresses `total` on one doc → CI exits non-zero, PR blocked | [approved] |
| EX-D1-2 | candidate only adds a `new_field` → CI exits zero, PR unblocked | [approved] |
