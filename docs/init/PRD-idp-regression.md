# PRD — IDP Regression Tester

## Problem

MuleSoft Anypoint IDP extracts structured fields from documents using an extraction action
that has a prompt and a version. When someone changes that prompt (or bumps the action
version) to fix one document type, they have no reliable way to know whether they improved
extraction overall or silently broke other fields. Today "did this change help?" is answered
by spot-checking a few documents by eye — slow, subjective, and blind to regressions.

## Goal

Give the team a **field-level, positive/negative regression check** for IDP extraction: run a
candidate prompt/version against a set of documents, compare the output to a known-good
reference, and get a clear per-field verdict plus a pass/fail gate. Make prompt iteration
safe enough to gate on in CI.

## Who it's for

- **Prompt Engineer** — iterates the extraction prompt, needs fast, trustworthy feedback on
  whether a change is a net improvement, field by field.
- **Golden Set Curator** (non-engineer) — owns the reference "expected outputs" and maintains
  them through a UI, without touching code.
- **CI Pipeline** — blocks a prompt-change PR when the change regresses a critical field.

## Success criteria

1. A prompt engineer can run a baseline and a candidate over the same documents and see, per
   field, one of: match / missing / wrong value / wrong format / new field, plus a gate.
2. Extending a prompt to extract a *new* field does **not** turn the run red (new fields are
   informational).
3. A non-engineer can correct an expected value in the platform UI without help.
4. The gate can run in CI and block a PR on a critical-field regression.
5. A regression run is reproducible: it records which action version and which golden version
   it used.

## Scope

**In scope**
- Two comparison modes: golden-set (vs curated expected outputs) and baseline-diff (vs a
  prior execution's output).
- Field-level classification with six verdicts and a criticality-based gate.
- Type-aware, per-field normalization (numbers, dates, ids, text) and format-vs-value
  distinction.
- Line-item / table comparison matched by key, not position.
- Orchestration against an evaluation platform (Langfuse or Opik) for golden storage,
  runs, scores, and a curator UI.
- A guided remediation view built on the platform's API.

**Out of scope (for now)**
- Changing IDP itself or its prompts (this tool judges output; it does not author prompts).
- Storing document files in the evaluation platform (only `document_id` + expected fields).
- Deep customization or forking of the platform console.
- Multi-tenant / customer-facing productization beyond the internal team.

## Epics

- **A — IDP adapter:** authenticate, submit a document at a given action version, poll to a
  terminal status, normalize the response.
- **B — Classifier & gate:** the six-verdict comparison and the pass/fail gate. Pure logic.
- **C — Golden-set management:** golden schema, bootstrap-by-correction, curator UI editing.
- **D — Run orchestration & platform integration:** push goldens, run baseline/candidate,
  write scores; behind a swappable platform adapter.
- **E — Remediation UI:** read a run's verdicts and present expected-vs-actual with a
  correction path.
- **F — Routing & structural novelty:** document-type → golden-set routing; a signal when
  output shape departs from the golden.

## Milestones

1. **MVP** — Epics A + B + D against one platform, golden-set mode, CLI gate. This is the
   smallest thing that answers "better or worse?".
2. **Curator loop** — Epic C: non-engineers maintain goldens in the UI.
3. **Product layer** — Epics E + F.

## Key constraints & decisions already made

- The evaluation platform is **substrate**, not the system; the classifier/routing/remediation
  are custom regardless of platform.
- **new_field / new_line are informational**, never a failure — this is what lets prompts be
  extended safely.
- **Document files stay out of the platform;** only `document_id` and expected fields are stored.
- Golden sets are built by **correcting IDP output** (bootstrap-by-correction), favouring
  small, failure-mode-focused sets over large ones.
- Platform choice (Langfuse vs Opik) is **still open** — see `discovery-notes.md`.
