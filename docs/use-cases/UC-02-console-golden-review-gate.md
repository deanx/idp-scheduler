# UC-02: Review and approve the drafted golden dataset before spending validation quota

**Parent PRD:** `docs/init/PRD-idp-regression.md`
> Note: this is the informal source doc in `docs/init/` — nothing has been promoted to `docs/prd/` yet.

**Epic:** E — Remediation UI (the console) · C — Golden-set management (the review/edit surface) · D — Run orchestration & platform integration (the quota-approval gate this extends)
**Persona:** Golden Set Curator (primary — reviews/edits/approves); Prompt Engineer (drives the console session; same person may hold both hats in solo use)
**Priority:** P1 — extends the console's existing primary use case (compare-versions wizard, CLAUDE.md), does not replace it
**Business value:** Today the console's wizard drafts a golden dataset from the incumbent action version and immediately spends a second batch of IDP quota verifying against it, with no human check that the drafted "expected" values are actually right. A wrong or accidentally-invented drafted value would make the candidate-version comparison meaningless while still reporting `STILL VALID` / `CHANGED` with confidence. This UC inserts a mandatory human review/edit/approve step between drafting and spending, and lets the customer supply their own golden set instead of trusting the draft — turning an unverified "agreement baseline" into ground truth the customer actually signed off on, before real quota is spent measuring against it.

**Intent-validated:** yes — Alex Costa, 2026-09-28

> **⚠️ Known gap surfaced by this UC, not created by it:** Epic E (the console) has no SPEC file — `docs/specs/SPEC-01-baseline-regression.md` is the only spec in the repo (per `## Rigor`'s recorded gap). This UC is the first formal artifact for a console-UI capability; `/design` and `/plan` will need to decide whether it lands under a new SPEC-02/03 or as an amendment.
>
> No wireframes/mockups exist for this flow. The console already has a working wizard (upload → plan → run) described narratively in `CLAUDE.md`'s Commands table and PROGRESS.md's Epic E entries — this UC is derived from that existing behavior plus the new requirement, not from a visual design.

---

## Use case

- **Actor:** Golden Set Curator (reviews, edits, or replaces the drafted golden dataset and gives the approval that releases the second batch of quota); Prompt Engineer (uploads the corpus and drives the wizard — may be the same physical person)
- **Goal:** Walk the existing console validation wizard (`docs/init` primary use case: "my corpus is valid at version X; I changed the LLM and published Y — is it still valid?") through named, visible stages — upload, validate/unpack, draft the golden dataset — then **stop** before any candidate-version extraction runs, show the curator what was drafted, let them correct it or substitute their own golden file, and only start the quota-spending verification once they explicitly approve.
- **Preconditions:**
  1. The console (`idp_regression.ui`) is running and its `/api/preflight` reports this machine can run a validation (credentials present).
  2. The curator has a corpus ZIP and knows the org/action id, the trusted (incumbent) action version, and the dataset name — the same inputs the existing wizard already requires.
  3. The existing quota-approval pattern holds unchanged: any route that spends IDP quota requires `approved_extractions` echoed back from a real `--plan`/preflight count computed server-side (CLAUDE.md's `TestQuotaBoundary` contract) — this UC does not weaken that, it adds a second, independent gate in front of the second spending route.

- **Main flow:**
  1. Curator uploads the corpus ZIP in the console. The console validates and unpacks it (existing behavior — a trust boundary, nothing written yet that costs quota).
  2. The console shows the corpus a named-stage progress view: **Uploading → Validating → Drafting golden dataset**, each stage visibly in-progress or complete (not a single opaque spinner over the whole job).
  3. The **drafting stage runs and spends its own quota** (one extraction per sampled/selected document against the **trusted** version — the existing pin/draft behavior of `pin_document.py`/`bootstrap_golden_set.py`), priced and approved the same way any quota-spending stage already is (`--plan`-equivalent shown, approval echoed back).
  4. When drafting completes, the wizard **pauses** and presents the drafted golden dataset: per document, the fields it read and the values it is about to treat as "expected," in a form the curator can actually read (not a raw platform JSON blob).
  5. The curator does ONE of:
     - **(a) Accepts** the drafted values as-is, or
     - **(b) Edits** one or more field values inline before accepting, or
     - **(c) Supplies their own golden file** (a `golden.json` in the already-committed schema), which replaces the drafted set for this run entirely.
  6. Whichever the curator chose, the console validates it against the committed golden schema (reusing `provision_golden_dataset.py`'s local schema check — a bad entry is named and refused before anything is provisioned, per the existing "one invalid entry refuses the whole batch" rule) and provisions/updates the dataset on the platform.
  7. The console then computes and shows the **real cost of the next stage** (the candidate-version verification — N extractions) and asks for an explicit, separate approval.
  8. Only after that second approval does the console start the candidate-version verification batch (the existing `verify_document`/`compare_versions` second half), which runs exactly as it does today: gate, exit code, run artifact.
  9. The finished job reports `STILL VALID` / `CHANGED` / `RUN FAILED` exactly as today, linked to the run artifact — this UC changes what happens *before* that call, not the verdict semantics.

- **Alternate / exception flows:**
  - **A1 — Curator abandons at the review step (never approves).** The job stays paused; the drafting-stage quota already spent is not refunded (consistent with the existing "cancelling refunds nothing already extracted" rule); the second batch never starts, so zero additional quota is spent. The console must say plainly that the job is waiting on the curator, not silently stuck.
  - **A2 — Curator's uploaded golden file fails schema validation.** Refused with the specific invalid entry named (reuse of `provision_golden_dataset.py`'s existing behavior) — the drafted golden set (from step 4) remains the fallback the curator can still accept or edit instead.
  - **A3 — Curator edits a value that would make a field's type invalid under the schema.** Refused inline, before provisioning; the curator corrects it or leaves the drafted value.
  - **A4 — Two console sessions target the same workspace.** The existing one-job-per-workspace `flock` still applies; a paused, unapproved review does not count as "idle" — it still holds the workspace lock (open design question for `/design`: whether a long human-paced pause should hold the same lock a running extraction batch holds, or a lighter one).

- **Acceptance criteria (Given/When/Then):**
  - **AC1:** Given a corpus ZIP has been uploaded, When the job proceeds, Then the console shows distinct, named stages for upload, validation, and golden-dataset drafting — never a single undifferentiated "running" state for the whole pipeline.
  - **AC2:** Given the drafting stage has completed, When the wizard reaches the review step, Then the console displays the drafted expected value for every field of every document in the sample, and the candidate-version verification batch has **not** started and has spent **zero** additional quota.
  - **AC3:** Given the curator edits a drafted field value and accepts, When the dataset is provisioned, Then the platform dataset item reflects the curator's edited value, not the originally drafted one (single upsert on the deterministic id — no divergence between what the console shows and what verification later reads).
  - **AC4:** Given the curator instead uploads their own golden file, When it passes schema validation, Then it entirely replaces the drafted set for this run's provisioning step; When it fails validation, Then the specific invalid entry is named and nothing is provisioned from it.
  - **AC5:** Given the golden dataset has been accepted (drafted-as-is, edited, or curator-supplied) and provisioned, When the console prices the candidate-version verification, Then it requires a second, explicit approval (echoing a real computed cost) separate from the drafting stage's own approval, before spending any further quota.
  - **AC6:** Given the curator never gives the second approval, When any amount of time passes, Then the job remains visibly paused/waiting-on-curator and no candidate-version extraction occurs.

- **Business rules:**
  - **BR1:** The candidate-version verification batch (the second, real-quota-spending half) must never start without an explicit human approval action taken *after* the drafted golden dataset has been shown to the curator. This is a stronger gate than "approved_extractions echoes a plan" alone — it requires that the plan being approved was priced *after* the review step, not before it.
  - **BR2:** A curator-supplied golden file, when accepted, is validated against the same committed schema `provision_golden_dataset.py` already enforces — no separate, weaker validation path for console-authored goldens.
  - **BR3:** An edit made in the review UI is never a local-only change — it must be written back to the platform dataset item (the same deterministic-id upsert `pin_document.py` already performs) before the verification stage can read it, so what the curator approved is provably what gets measured.
  - **BR4:** This review/approve gate applies **per run** — it is not a one-time setup step. Every corpus validation that goes through drafting gets its own review, because the drafted golden set is generated fresh (or re-used) per invocation, not a standing configuration.

- **Compliance / sensitivity notes (per `## Domain`):**
  - No change to the existing sensitivity posture: document files still never enter the evaluation platform, and the console remains loopback-only with no authentication. This UC adds an editing surface over data (drafted expected field values) that is already documented as sensitive (`## Domain`: "extracted fields ... can contain financial data / PII") and already reaches the platform under the 2026-09-25 "information point" reversal — it does not create a new data flow, only a human checkpoint on an existing one.

---

## Intent summary (for confirmation)

1. The console's existing validation wizard currently drafts a golden dataset from the incumbent version and immediately spends a second batch of quota verifying against it — with no human check in between.
2. This UC adds a **hard stop** after drafting: the curator sees the drafted values, can edit them or replace them with their own golden file, and only after that is a **second, separate approval** (pricing the actual verification batch) required before any more quota is spent.
3. The stages before the stop (upload, validate, draft) become **visibly distinct progress states**, not one opaque "running" spinner.
4. This does not change the verdict semantics (`STILL VALID`/`CHANGED`/`RUN FAILED`), the schema, or the underlying scripts (`pin_document.py`, `verify_document.py`, `provision_golden_dataset.py`) — it sequences and gates the console's orchestration of them.
5. Out of scope for this UC: building a full no-code form editor (Epic C's differentiator was already resolved as JSON-under-schema-guard-rails in ADR-0005, not a generated form) — "edit" here means correcting values within that same schema-guarded surface, in the console rather than the platform's own UI.

## Concrete examples

| ID | Scenario | Expected outcome |
|----|----------|-------------------|
| EX-1 | Curator uploads a 5-document corpus, drafting completes, curator reviews and accepts every field as drafted with no edits, then approves the verification batch. | The dataset is provisioned exactly as drafted; verification runs only after the second approval; final result is `STILL VALID`/`CHANGED` as normal, linked to the run artifact. |
| EX-2 | Curator uploads a corpus, drafting completes, curator edits one field's value on one document before approving. | The provisioned dataset item for that document reflects the edited value (not the drafted one); the later verification batch measures against the edited value. |

---

**Confirmed** — Alex Costa, 2026-09-28: "yes. Go for it." No corrections; EX-1/EX-2 stand as written.
