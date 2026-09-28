# SPEC-02: Console golden-review/approve gate (Epic E)

**Use case:** UC-02 (Review and approve the drafted golden dataset before spending validation quota)
**ADRs:** ADR-0008 (console two-stage golden-review workflow — ✅ ACCEPTED, Atchim APPROVE round 2 after round 1 REQUEST CHANGES)
**NFRs:** NFR-02 (9 rows, all PENDING) — `Containment: REQUIRED`, `LLM-Evals: N/A`, `Observability: REQUIRED` (binding regardless of rigor profile — see NFR-02.md correction, 2026-09-28)
**Owner persona:** Golden Set Curator (primary), Prompt Engineer (drives the console session)
**Risk level:** high (ADR-0008 — Atchim-approved; this is the first story to touch the console's quota-approval boundary, a surface with two prior fail-open incidents per `docs/state/PROGRESS.md`)
**⚠️ First SPEC file for Epic E** — closes the recorded gap in `CLAUDE.md ## Rigor` ("Epic E (the local console) has none [SPEC]... a gate with nothing to read"). Everything under `src/idp_regression/ui/` predates this SPEC and was built without one; this SPEC covers only the new work below, not a retroactive audit of existing console code.

## Summary

The console's existing guided validation wizard drafts a golden dataset from the incumbent action version and immediately spends a second, equal-sized batch of IDP quota verifying a candidate version against it — with no human check between drafting and spending. This SPEC implements ADR-0008: splitting that one-shot flow into two independently-priced-and-approved stages (**draft** → **human review/edit/approve** → **verify**), connected by a new `ReviewSession` record, with the review step holding no subprocess and no workspace lock. The existing one-shot `compare/start` route is kept, unmodified, as an expert/skip-review path.

## Scope

- In scope (UC-02):
  - Two new argv builders in `ui/jobs.py` (`build_pin_argv`, `build_verify_argv`) and two new job kinds, reusing the existing `Job`/`JobRegistry`/`_WorkspaceLock` machinery unchanged.
  - `ReviewSession` persistence (CT-06/DATA-MODEL-01 delta): creation at stage-1 completion, `approved_golden_hash` capture at review-completion, resumability across a console restart.
  - Golden edit surface: single-field edit (schema-validated, immediate upsert) and whole-file replace (reusing `provision_golden_dataset.py`'s existing all-or-nothing batch validation).
  - `POST /api/workflows/draft-golden/{plan,start}`, `POST /api/workflows/verify-candidate/{plan,start}`, `GET /api/reviews/{session_id}`, `POST /api/reviews/{session_id}/complete`, `PATCH /api/reviews/{session_id}/items/{document_id}`, `POST /api/reviews/{session_id}/replace`, `GET /api/reviews` (pending list).
  - INV-09 enforcement (clauses a–e) at `verify-candidate/start`.
  - `/api/health`'s `quota_spending_routes` and `tests/ui/test_api.py::TestQuotaBoundary` updated to the real route set (four: the pre-existing `compare/start` + `floor/start`, plus the two new ones) in the same change — corrected 2026-09-28/QA-02 F-2, the original count omitted the pre-existing `floor/start`.
  - Frontend: `StageProgress`, `GoldenReviewTable`, `EditableValueCell`, `ReplaceGoldenFileUpload`, `SecondApprovalPanel`, `#/reviews` pending list — per `docs/design/UI-SPEC-UC-02.md`.
- Out of scope:
  - Any change to `pin_document.py`, `verify_document.py`, `provision_golden_dataset.py`, `compare_versions.py`, the golden schema, or the classifier/gate — all reused as-is.
  - Resolving `DEBT-116`'s root cause (platform item id has no version/corpus binding) — INV-09(d) closes the gap for this workflow's own review pause only; the root stays open, filed via `/debt add` below.
  - Review-session cleanup/TTL UX beyond "visible in a pending list" — filed via `/debt add`, not designed here.
  - A no-code form editor for golden values — the review UI edits within the existing JSON-under-schema-guard-rails surface (ADR-0005 already ruled out a generated form).

## Definition of Ready

All confirmed before this SPEC is cardable:
- UC-02 `Intent-validated: yes` (Alex Costa, 2026-09-28) ✅
- ADR-0008 ✅ ACCEPTED, Atchim APPROVE round 2 ✅
- `docs/qa/NFR-02.md` exists, all rows PENDING (expected — /qa gate, not /plan) ✅
- EX-1/EX-2 from UC-02 covered by tagged Test-plan rows below ✅ (prototype rigor: one happy-path example is sufficient; both are included)
- No architect-confirmed `High + open` assumption touches this UC — assumptions gate is off at `prototype` rigor per `CLAUDE.md ## Rigor` ✅ N/A
- Estimates present (below, from Dengoso) — pending this document's completion
- No unresolved external-service dependency — none introduced (console-local state only; existing IDP/Langfuse registrations unchanged)

## Implementation order & dependencies

```
S-02.1 (Two-stage job orchestration + ReviewSession + INV-09) ──┐
                                                                  ├──▶ S-02.2 (Golden edit/replace API) ──▶ S-02.3 (Console UI wizard) ──▶ /harden ──▶ /qa
(no other deps — reuses existing pin_document.py/verify_document.py/     │
 provision_golden_dataset.py/jobs.py machinery unchanged)  ──────────────┘
```

- **S-02.1** has no new external dependencies — it composes existing scripts via the existing job runner. Start here.
- **S-02.2** depends on S-02.1's `ReviewSession` existing to write edits against.
- **S-02.3** depends on both — the UI is a thin layer over S-02.1/S-02.2's API surface, per `docs/design/UI-SPEC-UC-02.md`.

## Stories

### Story S-02.1 — Two-stage job orchestration, ReviewSession, and the INV-09 approval gate (Epic E / D)

- **As a** Golden Set Curator, **I want** the console to draft a golden dataset as its own priced, approved stage — separate from the candidate-version verification — and to bind verification to the exact golden I reviewed, **so that** no IDP quota is ever spent verifying against a golden I never saw, and a swapped-out golden can't slip past the gate during my review.
- **Acceptance criteria (from UC-02):** AC1 (named stages: upload/validate/draft), AC2 (review step spends zero additional quota, verification not started), AC5 (second approval priced after review, separate from stage 1's), AC6 (job stays visibly paused, no verification, if never approved).
- **Definition of Done:**
  - [ ] All AC met (AC1, AC2, AC5, AC6)
  - [ ] `build_pin_argv` / `build_verify_argv` added to `ui/jobs.py`, each reusing `_WorkspaceLock`, `_validate`, and the existing `--plan`/`approved_extractions` pattern unchanged
  - [ ] `ReviewSession` persisted at `<workspace>/.idp-regression-jobs/review-sessions/<id>.json`: atomic write (temp+rename), owner-only `0600`, fail-closed read (missing/truncated/invalid → refused, never best-effort resumed)
  - [ ] `approved_golden_hash` captured at an explicit review-completion step (`POST /api/reviews/{id}/complete`) — including on the accept-as-is path where nothing else writes
  - [ ] INV-09 enforced in full at `verify-candidate/start`: (a) session exists and matches, (b) `document_dir`/`archive_sha256` still match, (c) `approved_extractions` computed from a fresh `--plan` after review, (d) that approval bound to route+session_id (cross-route replay refused), (e) live platform dataset item hash matches `approved_golden_hash`
  - [ ] `/api/health`'s `quota_spending_routes` and `TestQuotaBoundary` updated to enumerate all **four** spending routes (corrected 2026-09-28/QA-02 F-2 — the original count omitted the pre-existing `floor/start`) in this same change — no operator-facing route count restated anywhere else (docstrings, `/openapi.json` description) per ADR-0008's S2 instruction; verified by grep at `/qa`
  - [ ] Unit + integration tests per the Test plan below, including the INV-09(e) adversarial test (provision a different item into the same dataset/document_id between review-completion and stage-2 start; assert 409, not a silent verify) — **AND a CI-runnable unit-level variant against a faked/in-memory platform adapter** (Atchim DoD gate round 1): the integration version is gated behind `RUN_INTEGRATION_TESTS=1` + local Langfuse, which is opt-in and not in the classifier CI gate (`CLAUDE.md ## Commands`) — without a unit variant, this project's single most important fail-open regression test for this surface never runs in CI
  - [ ] **N1 (performance):** `GET /api/reviews/{session_id}` (T-02.1.7) returns in < 2s for a 100-document sample — asserted by a test against a fixture-sized `ReviewSession`, not measured manually (Atchim DoD gate round 1 — N1 had no DoD owner)
  - [ ] **N5 (scalability):** `draft-golden/plan` refuses a corpus above the existing `--max-documents` ceiling **before any `ReviewSession` file is created** — asserted by a test (Atchim DoD gate round 1 — N5 had no DoD owner)
  - [ ] **N7 (no-flock concurrency):** a test proving an unrelated job can start in the same workspace while a review session is pending (the deliberate trade-off ADR-0008 §"Why does the review step hold no workspace lock?" makes) — this is the test that proves that trade-off is actually safe as designed, not merely asserted (Atchim DoD gate round 1)
  - [ ] **N6 (dir mode):** `<workspace>/.idp-regression-jobs/review-sessions/` asserted `0700` on creation, not just the `0600` file mode (Atchim DoD gate round 1 — reuse the existing job-history directory-mode test if it already covers this path; otherwise add one)
  - [ ] `mypy src tests scripts` strict clean, `ruff check` clean, `pip-audit` clean, secret scan clean (mechanical floor, binds at every rigor profile)
  - [ ] Atchim independent code review (correctness/readability/architecture/security/performance)
  - [ ] Passing `/harden` containment report (Branca) — `docs/qa/HARDEN-02.md` — required because NFR-02 declares `Containment: REQUIRED`
  - [ ] **Observability (N3, automated, not a manual `/qa` eyeball):** a test asserting each named stage transition (draft started/completed, review pending, review complete, verify started/completed) emits exactly one structured log line, **and** a test asserting no golden field value appears in any log line at any transition (reuse the existing INV-02 log-redaction test pattern) — tightened from "verified firing at /qa" per Atchim DoD gate round 1; binding regardless of rigor profile per `CLAUDE.md ## Rigor`'s "never profile-driven" markers row

- **Tasks:**
  - T-02.1.1 — `build_pin_argv` in `ui/jobs.py` (mirrors `build_compare_argv`'s validation/lock pattern, targets `pin_document.py --all`)
  - T-02.1.2 — `build_verify_argv` in `ui/jobs.py` (targets `verify_document.py --all`)
  - T-02.1.3 — `ReviewSession` model + atomic read/write helpers (`review_sessions.py`, new module) — CT-06 shape, `0600`, temp+rename
  - T-02.1.4 — `POST /api/workflows/draft-golden/{plan,start}` — reuses existing plan/approve pattern; on success writes `ReviewSession` (`state=drafted`)
  - T-02.1.5 — `POST /api/reviews/{session_id}/complete` — reads platform dataset items, computes `approved_golden_hash`, sets `state=reviewed`
  - T-02.1.6 — `POST /api/workflows/verify-candidate/{plan,start}` — implements INV-09 clauses (a)–(e) in full; sets `state=verifying`→`verified`/`stale`
  - T-02.1.7 — `GET /api/reviews` (pending list) and `GET /api/reviews/{session_id}` (single session read, used by S-02.3)
  - T-02.1.8 — Update `/api/health`'s `quota_spending_routes` + `TestQuotaBoundary` to the real four-route set (corrected 2026-09-28/QA-02 F-2); grep-check no operator-facing route count exists elsewhere
  - T-02.1.9 — Tests: INV-09(a)-(e) including the adversarial swap-the-golden 409 test; job-builder unit tests; `ReviewSession` fail-closed-read tests

### Story S-02.2 — Golden edit and whole-file replace API (Epic C / E)

- **As a** Golden Set Curator, **I want** to correct a drafted field value or upload my own golden file before approving verification, **so that** the golden the gate measures against is one I actually trust, not just what the incumbent version happened to produce.
- **Acceptance criteria (from UC-02):** AC3 (edited value reflected in the provisioned item, single upsert, no divergence), AC4 (curator-supplied file schema-validated; invalid entry named and refused; drafted set remains as fallback).
- **Definition of Done:**
  - [ ] All AC met (AC3, AC4)
  - [ ] `PATCH /api/reviews/{session_id}/items/{document_id}`: validates the edited field against the committed golden schema before writing; on success, upserts via the same deterministic-id path `pin_document.py` already uses; on failure, refuses with the specific field named, no partial write
  - [ ] `POST /api/reviews/{session_id}/replace`: reuses `provision_golden_dataset.py`'s existing schema validation and all-or-nothing batch semantics unchanged (no new validation logic written); a failed replace leaves the drafted set intact
  - [ ] Both endpoints assert **zero** IDP quota spent (test, not assumption)
  - [ ] Edited fields are distinguishable from drafted fields in the API response the UI reads (provenance never blended — Branca containment note)
  - [ ] Unit + integration tests per the Test plan below
  - [ ] `mypy`/`ruff`/`pip-audit`/secret scan clean
  - [ ] Atchim independent code review
  - [ ] Containment: covered under S-02.1's `/harden` pass (same review-session surface) — no separate HARDEN report required for this story alone
  - [ ] **Observability (N3, automated):** a test asserting each of the edit and replace actions logs one structured line with the session id, and a test asserting no golden field value appears in that line (INV-02) — tightened from a bare property statement per Atchim DoD gate round 1; binding regardless of rigor profile

- **Tasks:**
  - T-02.2.1 — `PATCH /api/reviews/{session_id}/items/{document_id}` — single-field schema validation + deterministic-id upsert
  - T-02.2.2 — `POST /api/reviews/{session_id}/replace` — wires to `provision_golden_dataset.py`'s existing validation path, no new schema logic
  - T-02.2.3 — Provenance flag on review-read response (`drafted` vs `edited` per field) so S-02.3 can render it distinctly
  - T-02.2.4 — Tests: schema-rejection-leaves-no-partial-write; zero-quota assertion on both endpoints; replace-failure-leaves-drafted-intact

### Story S-02.3 — Console UI: staged wizard, review screen, pending-reviews list (Epic E)

- **As a** Golden Set Curator, **I want** to see distinct progress stages, review drafted values in a readable table, edit or replace them, and see a clear second-approval prompt, **so that** I can actually exercise the review gate S-02.1/S-02.2 implement, not just have it exist as an API.
- **Acceptance criteria (from UC-02):** AC1 (visible named stages), AC2 (review table renders before any second-stage spend), AC3 (inline edit UX), AC4 (file upload UX with named validation errors), AC5 (second-approval panel requires the same explicit confirm interaction as existing quota-spending UI), AC6 (pending/awaiting-review state visible in `#/reviews`).
- **Definition of Done:**
  - [ ] All AC met (AC1–AC6)
  - [ ] Components built per `docs/design/UI-SPEC-UC-02.md`: `StageProgress`, `GoldenReviewTable`, `EditableValueCell`, `ReplaceGoldenFileUpload`, `SecondApprovalPanel`, `ReviewSessionRow`/`#/reviews`
  - [ ] All named states covered (loading, error, ready-to-approve, awaiting-approval/paused) per UI-SPEC-UC-02.md's States section
  - [ ] **Automated component/unit tests** for `StageProgress` state derivation, `EditableValueCell` type-aware validation, and `SecondApprovalPanel`'s confirm-echo interaction — required, not optional (Atchim DoD gate round 1: the mechanical floor "tests exist and pass" binds at every rigor profile, and this DoD previously had zero automated tests over the UI layer for the exact quota-approval boundary with two prior fail-open incidents)
  - [ ] `tsc -b` clean, **frontend `eslint` clean, secret scan clean** (Atchim DoD gate round 1 — the frontend mechanical floor was missing; S-02.1/S-02.2 already carry the backend equivalent)
  - [ ] Manual run-through against the dev server (`npm run dev` + `--dev-cors`) exercising EX-1 and EX-2 end to end, using a **synthetic/fixture corpus with no real customer data** — **never a real corpus** — before anything is recorded in the QA report; any screenshot committed to `docs/qa/` must show only synthetic field values, since `GoldenReviewTable` renders exactly the class of value `## Domain` calls sensitive and the QA report is a tracked, committed location (Atchim DoD gate round 1)
  - [ ] Atchim independent code review
  - [ ] Containment: N/A for this story specifically (pure frontend over an already-hardened API; S-02.1's HARDEN-02 covers the seams this UI calls)
  - [ ] Observability: N/A for this story specifically (no new runtime/server surface — frontend only)
  - [ ] **N1 ownership note:** the < 2s/100-document performance target (NFR-02 N1) is owned by **S-02.1**'s `GET /api/reviews/{session_id}` (the server-side read), not this story — no duplicate DoD line here (Atchim DoD gate round 1 flagged N1 as unassigned across all three stories; resolved to S-02.1 since N1's wording names "the review-step read")

- **Tasks:**
  - T-02.3.1 — `StageProgress` component + wizard wiring for upload/validate/draft
  - T-02.3.2 — `GoldenReviewTable` + `EditableValueCell` (typed inputs per golden schema `type`)
  - T-02.3.3 — `ReplaceGoldenFileUpload` with named-error rendering
  - T-02.3.4 — `SecondApprovalPanel` (reuses existing approved-extractions-echo confirm component)
  - T-02.3.5 — `#/reviews` pending-list page + `ReviewSessionRow`
  - T-02.3.6 — Manual E2E run-through (EX-1, EX-2) against dev server, recorded for the QA report
  - **T-02.3.7 — (added 2026-09-28, Soneca amendment to ADR-0008; Atchim round-1 review finding C1/C2) `GET /api/reviews/{session_id}/values`** — a new, separate, read-only endpoint serving every drafted field value per document, sourced from the platform (the authoritative, post-edit-accurate source per CT-07 — NOT the local pin store, which goes stale after any edit) via a new `fetch_platform_items(dataset, workspace)` helper (one GET over the dataset's items, not an O(N²) per-document fan-out). 503 if the platform is unreachable/unconfigured. Wire `GoldenReviewTable` off its current identity-only placeholder to render `field name · drafted value · type` per document, and fix the `EditModal` to pre-populate with the complete current entry (closing Atchim's C2: a one-field edit must no longer silently drop every other field, since PATCH is whole-item-replace). Read-only, zero quota, touches neither `_require_runnable` nor INV-09/`TestQuotaBoundary` (not added to `/api/health`'s `quota_spending_routes`) — risk: low, no new `/harden` pass required. Also closes Atchim's R1 (missing tests for `GoldenReviewTable`/`EditModal`, `ReplaceGoldenFileUpload`, `ReviewsPage`, `ReviewDetailPage`'s state machine).

## Test plan

| AC | Test type | Notes |
|---|---|---|
| AC1 — named stages (upload/validate/draft) | unit (StageProgress state derivation) + manual UI check | S-02.3 |
| AC2 — review spends zero quota, verification not started | integration (`ui/jobs.py`, `ui/api.py`) | S-02.1; assert no subprocess invoked between draft completion and an explicit stage-2 start |
| AC3 — edit reflected via single upsert | unit + integration | S-02.2; assert deterministic-id upsert, no duplicate item |
| AC4 — replacement file schema-validated, invalid entry named | unit + integration | S-02.2; reuse `provision_golden_dataset.py`'s existing test fixtures for invalid entries |
| AC5 — second approval priced after review, separate from stage 1 | integration | S-02.1; assert a stage-1 `approved_extractions` value is refused if replayed against `verify-candidate/start` |
| AC6 — unapproved session stays visibly paused, zero further spend | integration + unit (`#/reviews` listing) | S-02.1/S-02.3 |
| **EX-1** — accept draft as-is end to end, both approvals given | integration (S-02.1+S-02.2) + E2E manual run-through (S-02.3, dev server) | Happy path; the prototype-rigor minimum example |
| **EX-2** — edit one field before approving, verification measures against the edit | integration | S-02.2; assert the run artifact's verdict reflects the edited value, not the originally drafted one |
| **INV-09(e)** adversarial — golden swapped during the pause | integration + unit (faked platform adapter, CI-runnable) | S-02.1; provision a different item into the same dataset/document_id between review-completion and stage-2 start, assert 409 — the round-1 Atchim finding's regression test, non-optional; the unit variant is what actually runs in the classifier CI gate |

External services/credentials needed for integration tests: local Langfuse instance (`LANGFUSE_HOST`, already configured per `## External services`) and a fixture corpus under `RUN_INTEGRATION_TESTS=1` — no new credential introduced by this SPEC.

## Estimates (Dengoso, batch estimate — 2026-09-28)

| Task | Points | Justification |
|---|---|---|
| T-02.1.1 — `build_pin_argv` | 2 | Direct mirror of `build_compare_argv`; same `_validate`/flag pattern |
| T-02.1.2 — `build_verify_argv` | 2 | Same shape as T-02.1.1 |
| T-02.1.3 — `ReviewSession` model + atomic read/write | 3 | New module; fail-closed reader, atomic writer, 0600/0700 modes, full state enum |
| T-02.1.4 — `draft-golden/{plan,start}` | 3 | Reuses existing plan/approve wiring; creates `ReviewSession`; N5 ceiling-before-session guard |
| T-02.1.5 — `reviews/{session_id}/complete` | 3 | Platform read + hash computation + `approved_golden_hash` capture + state transition |
| T-02.1.6 — `verify-candidate/{plan,start}` | 5 | Five INV-09 refusal clauses; the single most security-critical route in this SPEC |
| T-02.1.7 — `GET /api/reviews[/…]` | 2 | Two read endpoints; N1's <2s/100-doc bound is a fixture assertion, not a load test |
| T-02.1.8 — `/api/health` + `TestQuotaBoundary` update | 1 | Mechanical, two places + a grep-check |
| T-02.1.9 — Tests for S-02.1 | 5 | Ten required tests incl. INV-09(a)-(e), the CI-runnable faked-adapter variant, N1/N5/N6/N7, log/redaction tests |
| **S-02.1 subtotal** | **26** | |
| T-02.2.1 — Single-field edit endpoint | 3 | Schema validation + deterministic upsert + fail-closed, three moving parts |
| T-02.2.2 — Whole-file replace endpoint | 2 | Wires to existing `provision_golden_dataset.py` validation, no new logic |
| T-02.2.3 — Provenance flag (drafted/edited) | 2 | Must survive edit, replace, and a console restart |
| T-02.2.4 — Tests for S-02.2 | 2 | Focused; no adversarial complexity |
| **S-02.2 subtotal** | **9** | |
| T-02.3.1 — `StageProgress` + wizard wiring | 2 | Component-level state machine over session/job state |
| T-02.3.2 — `GoldenReviewTable` + `EditableValueCell` | 5 | Heaviest frontend task — typed inline editors at 100-doc scale, over the fail-open-history approval boundary |
| T-02.3.3 — `ReplaceGoldenFileUpload` | 2 | Named-error rendering from the batch-validation refusal |
| T-02.3.4 — `SecondApprovalPanel` | 2 | Composition + automated echo-confirm test |
| T-02.3.5 — `#/reviews` pending list | 2 | Standard list-page pattern |
| T-02.3.6 — Manual E2E run-through (EX-1/EX-2) | 1 | Execution + documentation only; synthetic corpus required |
| **S-02.3 subtotal** | **14** | |
| **SPEC-02 total** | **49** | |

Heaviest tasks: T-02.1.6 (INV-09's five refusal clauses) and T-02.1.9 (the adversarial regression suite, incl. the CI-runnable faked-platform variant) — both on S-02.1, which starts first per the dependency graph; T-02.3.2 for the same fail-open-history reason on the frontend side.

## `/debt add` (owed from ADR-0008 Open items, to be filed by Dunga)

1. **DEBT-116 re-weight** — the review-pause vector (Atchim, ADR-0008 round 1 R1) makes the already-open "platform dataset item id carries no version/corpus binding" root more exposed than before this SPEC, since the pause is now human-paced and indefinite rather than seconds-long inside one subprocess. INV-09(d) closes the gap for *this* workflow only.
2. **Review-session cleanup/TTL UX** — tracked as a real item, not an implicit TODO (Branca, ADR-0008 round 1).
