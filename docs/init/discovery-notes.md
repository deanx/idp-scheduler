# Discovery notes

Domain knowledge, settled decisions, and open questions for the IDP Regression Tester.
This is context for `/discover` and `/design` — the "why behind the what".

## Mental model

Three layers, one flow: **IDP produces output → the custom app judges it → the platform stores
and shows the judgement.** The governing principle is that the platform is *substrate*. It
contributes durable golden storage, a run/experiment model, per-field score persistence, and a
curator UI. It does **not** compare document maps or decide pass/fail — that is the custom
app's job and stays custom whichever platform is chosen.

## Testing modes (both required)

- **Golden-set mode** — compare a candidate run against curated, hand-verified expected
  outputs. The durable, authoritative regression suite.
- **Baseline-diff mode** — compare a candidate run against a prior *execution's* output. A
  fast "did anything move?" check when no golden exists yet for a document type. Same
  classifier, different reference source.

These two modes are distinct and shaped the classifier design: the comparison logic is the
same, but the "expected" side comes from a golden in one mode and from a captured prior run in
the other.

## Settled decisions (treat as design constraints)

1. **Six-verdict classifier.** Every field gets one of: `match`, `missing`, `wrong_value`,
   `wrong_format`, `new_field` (or `new_line` for line-item rows). See `requirements-spec.md`.
2. **new_field / new_line are informational.** Never a failure. Extending a prompt to extract
   an extra field must not turn every run red. This is a critical design choice, not a nicety.
3. **Criticality-based gating.** The gate fails only on a `missing`/`wrong_value` in a field
   marked `critical`. Non-critical differences pass.
4. **Type-aware normalization.** Compare per field type (`number`, `date`, `id`, `text`).
   Distinguish `wrong_format` (same content, different formatting) from `wrong_value` (real
   difference) via a looser alphanumeric normalization tier.
5. **Line items matched by `match_key`,** not by position — row reordering is not a diff.
6. **Wrapper / extend, not fork.** Build on the platform's API; never reskin or fork its
   console. A bespoke remediation UI is custom on either platform.
7. **Document files stay out of the platform.** Store only `document_id` and expected fields.
   Addresses both data sensitivity and the platforms' weak multimodal UI.
8. **Bootstrap-by-correction golden sets.** Build goldens by correcting IDP output rather than
   authoring from scratch. Prefer small, failure-mode-focused sets.
9. **No Docker for the custom app.** Docker is only for the self-hosted platform server. The
   Python app runs natively on M1 and in CI.
10. **Reproducibility.** A run records the action version and golden version it used.

## Platform trade-off (STILL OPEN — resolve at /design)

Both Langfuse and Opik clear the baseline bar: genuinely open-source, free to self-host
(Langfuse MIT, Opik Apache-2.0), Python + TypeScript SDKs, REST APIs, datasets/experiments/
custom evaluators/CI gating. The IDP integration and the wrapper integration are even between
them. The choice comes down to two features that touch the custom app:

- **Langfuse** — JSON-schema-driven **form mode** lets non-engineers edit nested expected
  outputs through generated form fields with validation. Best match for the Curator persona.
  Also emphasises a directly queryable database.
- **Opik** — **automatic immutable dataset versioning** on every edit. Best match for
  audit-grade golden history. UI dataset creation is CSV-based (no nested JSON in CSV), which
  pushes structured golden editing to the SDK.

Enterprise features (RBAC, SSO, SCIM, audit logs) are **paid on both**. Decision rule: build
the platform integration behind one swappable module (see `design-inputs.md` ADR-1) so the
choice stays reversible, then decide during `/design` by scoring the substrate against *our*
workload — specifically the Curator's nested-golden editing experience.

## Open questions for the human (Feliz should ask these at /discover)

- Which document types are in the first golden set (invoices only, or others)? What is the
  criticality of each field per type?
- Is the Curator loop (Epic C) in the MVP, or does the MVP ship engineer-only (goldens edited
  via SDK/JSON) with the UI deferred?
- Should the CI gate block on any critical regression, or only on a net decrease across the
  golden set?
- Langfuse or Opik — or should the MVP stay platform-neutral behind the adapter and defer?

## Org-specific unknowns (must be confirmed against a live IDP org)

- IDP terminal **status strings** (e.g. `SUCCEEDED` vs `COMPLETED`, review-queue statuses).
- IDP **line-item / table shape** in the response.
- Pinned **platform SDK call shapes** for the chosen versions.
