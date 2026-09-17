# Backlog seed (for /plan)

Candidate stories grouped by epic, each with a Definition of Done. `/plan` (Dunga) turns these
into formal story cards with tagged acceptance tests; treat these as a starting backlog, not
final cards. Build order follows the spike's proven sequence: classifier first (pure,
lowest-risk), then adapter, then orchestration on one platform, then the second platform, then
the product layer.

## Suggested build order

1. Epic B (classifier + gate) — pure, unit-testable, no external deps. Start here.
2. Epic A (adapter) — resolves the two org-specific unknowns against live IDP.
3. Epic D (orchestration on one platform) — first end-to-end baseline + candidate run.
4. Epic D (second platform) — the real Langfuse-vs-Opik evaluation on our workload.
5. Epics C, E, F — curator loop, remediation UI, routing/novelty.

---

## Epic B — Classifier & gate

- **S-B1 — Type-aware field comparison.** Implement `classify()` for scalar fields with
  per-type normalization (`number`, `date`, `id`, `text`).
  *DoD:* `match`, `missing`, `wrong_value` emitted correctly; unit tests for each; currency and
  date-format equivalence normalize to `match`.
- **S-B2 — Format-vs-value distinction.** Add the looser alphanumeric tier so `wrong_format` is
  reachable and distinct from `wrong_value`.
  *DoD:* `INV 001` vs `INV-001` → `wrong_format`; test proves reachability (this was a real bug
  in the spike — the fallback made `wrong_format` unreachable).
- **S-B3 — new_field / informational verdicts.** Keys in actual not in golden → `new_field`;
  never affect the gate.
  *DoD:* adding a field passes the gate; test covers new_field + a concurrent critical failure.
- **S-B4 — Line-item comparison by match_key.** Compare rows by key; unmatched output rows →
  `new_line`; per-cell verdicts for matched rows.
  *DoD:* reordered rows still match; extra row → `new_line`, gate unaffected; a bad critical
  cell → gate FAIL.
- **S-B5 — Gate.** `overall_gate()` FAILs only on critical `missing`/`wrong_value` (fields or
  line-item rows).
  *DoD:* gate matrix tested across all six verdicts × critical/non-critical.

## Epic A — IDP adapter

- **S-A1 — OAuth + submit + poll.** `extract(file, version)` end to end against live IDP with a
  poll timeout.
  *DoD:* returns a raw execution body for a real document; timeout surfaces as an error.
- **S-A2 — Confirm terminal status strings.** Capture a real execution; set the terminal-status
  set from it.
  *DoD:* `poll()` recognises the org's real terminal statuses (incl. any review-queue status).
- **S-A3 — normalize() to the internal contract.** Map the raw response (incl. line items/
  tables) to `{status, fields:{value,confidence}, line_items:[...]}`.
  *DoD:* normalized output validated against a captured real response; line items land as a flat
  list the classifier accepts.

## Epic D — Orchestration & platform integration

- **S-D1 — Platform adapter interface.** One interface (`get_dataset`, `write_scores`, `flush`,
  golden CRUD) with the first concrete implementation.
  *DoD:* no other module imports a platform SDK; interface covers the run sequence.
- **S-D2 — push_dataset.** Load goldens into a platform dataset (item = document_id + golden).
  *DoD:* goldens visible in the platform UI; no document files uploaded.
- **S-D3 — run_eval --version --run.** Extract, classify, write `field:<name>` + `gate` scores.
  `load_dotenv()` before any client.
  *DoD:* a baseline run and a candidate run both produce scores end to end; version passed as a
  value (not an env-var name — spike footgun).
- **S-D4 — Baseline-diff mode.** Use a captured prior execution as the reference instead of a
  golden.
  *DoD:* same classifier path; identical output → all `match`.
- **S-D5 — Second platform.** Implement the other platform behind the same interface.
  *DoD:* the same app runs on both; this is the input to the ADR-9 decision.
- **S-D6 — CI gate exit code.** Expose the gate as a non-zero exit on any critical FAIL.
  *DoD:* CI blocks a regressing candidate; passes a new-field-only candidate.

## Epic C — Golden-set management

- **S-C1 — Golden schema + validation.** Formalize and validate the golden shape (types,
  criticality, match_key).
  *DoD:* malformed goldens rejected with a clear error.
- **S-C2 — Bootstrap-by-correction.** Tool to seed a golden from a corrected IDP output.
  *DoD:* an engineer turns one execution into a golden by correcting values.
- **S-C3 — Curator UI editing.** Non-engineer edits an expected value through the platform UI;
  malformed input rejected; edit versioned.
  *DoD:* dependent on ADR-9 — Langfuse form-mode vs a custom surface on Opik.

## Epic E — Remediation UI

- **S-E1 — Read run verdicts via API.** Fetch a run's `field:<name>` + `gate` scores.
  *DoD:* verdicts rendered per document with expected/actual/confidence.
- **S-E2 — Guided correction path.** From a failed field, guide the curator to the fix.
  *DoD:* a failing field links to its golden edit.

## Epic F — Routing & structural novelty

- **S-F1 — Document-type routing.** Detect type; route to that type's golden set.
  *DoD:* an invoice compares against invoice goldens via a routing table.
- **S-F2 — Structural-novelty signal.** Flag runs whose output shape departs from the golden
  (clustered new_*/missing blocks).
  *DoD:* a structural shift raises the signal separately from per-value verdicts.
