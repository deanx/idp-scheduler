# Glossary — ubiquitous language

One word per concept, used identically in docs, code, tests, and cards.

| Term | Meaning |
|---|---|
| **IDP** | MuleSoft Anypoint Intelligent Document Processing — the extraction service. |
| **Action** | An IDP extraction action; has a prompt and versions. |
| **Action version** | A specific version of the action. The regression variable. |
| **Baseline** | The last known-good action version (the reference to beat). |
| **Candidate** | The changed action version under test. |
| **Extraction / actual** | The output IDP returns for a document at a version. |
| **Golden / expected** | A hand-verified expected output for a document. The reference in golden-set mode. |
| **Golden set** | A curated collection of goldens, usually per document type. |
| **Golden-set mode** | Comparison against curated goldens. |
| **Baseline-diff mode** | Comparison against a captured prior execution (no curated golden). |
| **Verdict** | The per-field classification: `match`, `missing`, `wrong_value`, `wrong_format`, `new_field`, `new_line`. |
| **match** | Type-aware canonical forms equal. |
| **missing** | Expected field absent or empty in the actual output. |
| **wrong_value** | Genuinely different content. |
| **wrong_format** | Same content, different formatting only. |
| **new_field** | A field in the actual output not in the golden. Informational; never fails the gate. |
| **new_line** | A line-item row in the actual output not in the golden. Informational. |
| **Gate** | The run's PASS/FAIL, from `overall_gate()`. FAILs only on a critical `missing`/`wrong_value`. |
| **Critical** | A golden field/line-item block whose loss fails the gate. Set by the curator. |
| **match_key** | The field used to pair line-item rows (e.g. `description`) instead of position. |
| **Normalize** | The adapter step mapping IDP's raw response to the internal contract. |
| **Run / experiment** | A named scored execution over a dataset (a platform concept). |
| **Score** | A per-field verdict or the gate, persisted to a run on the platform. `field:<name>` / `gate`. |
| **Dataset item** | One golden on the platform: `document_id` input + expected-output golden. |
| **Platform** | The evaluation/observability substrate — Langfuse or Opik. |
| **Platform adapter** | The one module isolating platform SDK calls (keeps the platform swappable). |
| **Adapter** | The IDP client module (auth, submit, poll, normalize). |
| **Classifier** | The pure comparison logic (`classify` + `overall_gate`). |
| **Remediation UI** | The guided expected-vs-actual view built on the platform API. |
| **Bootstrap-by-correction** | Building a golden by correcting an IDP output rather than authoring from scratch. |
| **Structural novelty** | A signal that an output's shape departs from the golden's. |
| **Curator** | Non-engineer persona who maintains goldens via the platform UI. |
