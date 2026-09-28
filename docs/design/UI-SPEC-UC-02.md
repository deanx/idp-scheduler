# UI-SPEC-UC-02: Golden-dataset review/approve gate in the console wizard

No wireframes/mockups exist for this flow — derived from UC-02's text and the console's existing wizard behavior (`frontend/src/pages/JobsPage.tsx` and the wizard steps already shipped for the compare/floor workflows, per `docs/state/PROGRESS.md`'s Epic E entries). This extends the existing wizard; it does not replace it.

## Screen inventory

| Screen | Route (existing app is a hash-routed SPA, e.g. `#/...`) | Purpose |
|---|---|---|
| Wizard — stages | `#/validate` (existing) | Now shows **three** named, sequential states before any review: Uploading → Validating → Drafting golden dataset (was previously one undifferentiated running state covering drafting AND verification together). |
| Review screen (new) | `#/validate/review/<session_id>` | Shows the drafted golden dataset per document/field; lets the curator accept, edit, or replace it; presents the second approval once ready. |
| Pending reviews list (new) | `#/reviews` | Lists review sessions awaiting curator action — the visible answer to "is anything waiting on me" (NFR-02 N7). |
| Run result | `#/runs/<id>` (existing, unchanged) | Verdict display after stage 2 completes — no change to this screen's contract. |

## Component breakdown per screen

**Wizard — stages**
- `StageProgress` (new small component): three steps, each `pending` / `in-progress` / `done` / `failed`. Replaces the single spinner previously shown across the whole compare job.
- Existing upload/plan/approve controls for stage 1 (draft) reuse the existing `approved_extractions`-echo confirmation pattern already built for `compare/start` and `floor/start` — same component, pointed at the new `draft-golden/plan` endpoint.

**Review screen**
- `GoldenReviewTable`: one row per (document, field), columns = document id, field name, drafted value, type. Table-valued fields (line items) get a nested sub-table per document, matching the existing platform dataset item shape (`match_key`-paired rows).
- `EditableValueCell`: inline edit control per row, type-aware (number/date/id/text input, matching the golden schema's declared `type` — reuse whatever input-typing convention the custom-scorer-authoring UI already uses, since it already edits typed JSON under the same schema).
- `ReplaceGoldenFileUpload`: a file picker for a customer-supplied `golden.json`, wired to the whole-set-replacement endpoint; shows the same schema-validation error surface `provision_golden_dataset.py`'s CLI already produces (named invalid entry), rendered as text, not swallowed.
- `SecondApprovalPanel`: shows the freshly computed `--plan`-equivalent cost for stage 2 and requires the same explicit "type/click to confirm N extractions" interaction already used elsewhere in the console, never a bare "Continue" button.

**Pending reviews list**
- `ReviewSessionRow`: dataset name, corpus identifier, drafted-at timestamp, document count, a link into the review screen. No delete/expiry action in this UI-SPEC — cleanup UX is explicitly deferred (ADR-0008 "Open items for /plan").

## States

- **Loading** — review screen while fetching the drafted dataset items from the platform; skeleton rows, no interaction enabled.
- **Empty** — a review session whose sample was 0 documents should not be reachable (stage 1 refuses an empty selection today) — if this state is ever hit, it is a bug, and the screen shows a explicit error, not a blank table.
- **Error** — stage 1 failed before completing a draft: no review session exists, the wizard shows the failure and offers retry (existing job-failure UI pattern, unchanged).
- **Edited-but-unsaved** — N/A by design (UC-02 BR3): every edit writes back immediately, so there is no local-only dirty state to lose. The UI should reflect "saved" per-cell (e.g. a brief inline confirmation), not a page-level "Save" button.
- **Ready-to-approve** — every document in the sample has been reviewed at least once (accepted or edited) or replaced wholesale; `SecondApprovalPanel` becomes active. Before that point it stays disabled — the AC5 requirement that the second approval is priced *after* review, not offered speculatively.
- **Awaiting-approval / paused** — after stage 1 completes and before the curator acts; this is the state `#/reviews` surfaces.

## Layout & responsive intent

The console is an operator/curator tool used on a laptop, not a phone-optimized surface (per `## Stack`: local/CI runtime, no stated mobile requirement). `GoldenReviewTable` should scroll horizontally on narrow viewports rather than reflow into cards — table-shaped data (many typed fields per document) loses meaning if collapsed. No further responsive requirement is stated; flagged as an open question if a narrower target ever matters.

## Interaction → behavior (cross-referenced to UC-02 ACs)

| Control | Action | AC |
|---|---|---|
| Stage progress steps | Read-only status display | AC1 |
| Review screen load | Fetch drafted dataset, render, zero quota spent | AC2 |
| `EditableValueCell` commit | Validate + upsert one dataset item | AC3 |
| `ReplaceGoldenFileUpload` submit | Validate whole file, replace drafted set on success, name the bad entry on failure | AC4 |
| `SecondApprovalPanel` confirm | Compute fresh plan, require exact echo, start stage 2 | AC5 |
| Leaving the review screen without approving | No action fires; session persists in `#/reviews` | AC6 |

## Open UI questions (flagged, not decided)

- Whether editing should support bulk "accept all remaining as drafted" versus per-row confirmation — UC-02 doesn't specify, and both satisfy the ACs. Left to `/plan`/`/implement` judgment; not a gating decision.
- Whether `#/reviews` needs any notification (beyond being a page someone can navigate to) for a long-idle session — out of scope per ADR-0008.
