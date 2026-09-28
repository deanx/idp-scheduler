# ADR-0008: Splitting the console's one-shot compare into a drafting stage and a reviewed verification stage

**Status:** ✅ **ACCEPTED 2026-09-28 — Atchim APPROVE on re-review (round 2), after round 1 REQUEST CHANGES (R1: platform-golden integrity across the review pause; R2: ReviewSession record-shape drift between CT-06 and DATA-MODEL-01) was resolved.** One residual noted by Atchim as acceptable, not gating: a narrow TOCTOU window between INV-09(d)'s hash check and `verify_document`'s own read inside stage-2, now bounded to seconds under the re-taken `flock` for same-workspace concurrency; the only actor that could still slip in is DEBT-116's already-open, already-tracked root (cross-workspace/second-machine writes sharing a dataset name).
**Date:** 2026-09-28
**Context (use case):** UC-02 (`docs/use-cases/UC-02-console-golden-review-gate.md`)
**Risk:** high — this changes the shape of the console's quota-approval boundary (`TestQuotaBoundary`), the exact surface that CLAUDE.md and `docs/state/PROGRESS.md` record as having fail-open history (T4, the preflight round that shipped fail-open; the health-declaration drift round). Atchim review is required before `/plan`.

## Context

The console's primary use case ("my corpus is valid at version X; I changed the LLM and published Y — is it still valid?") is implemented today as **one** quota-spending route, `POST /api/workflows/compare/{plan,start}`, which runs `scripts/compare_versions.py` as a single subprocess. That script does two things back to back with **one** approval covering their combined cost (2N extractions): it unpacks the corpus once, **pins** every document at the trusted version (drafts the golden, N extractions), then immediately **verifies** every one of them at the candidate version (N extractions). `jobs.py::build_compare_argv` is the only argv builder for this flow; there is no standalone "draft only" or "verify only" job in the console today (`grep -n "^def build_" jobs.py` shows only `build_compare_argv` and `build_noise_floor_argv`).

UC-02 requires a human checkpoint between those two halves: the curator must see what got drafted, be able to correct it or replace it with their own golden file, and give a **second, separately-priced approval** before the verification half — the second N-extraction spend — is allowed to start. That checkpoint cannot exist inside a single subprocess invocation; it has to be a seam in the console's own orchestration.

## Decision

**Split the console's guided wizard into two independently-approved stages, each its own job, with a human review step in between that runs no subprocess at all.** `scripts/compare_versions.py` itself is untouched — it remains available as a one-shot, unreviewed path for direct/CLI use. The console's UI wizard stops calling it for the guided flow and instead orchestrates the two halves it already knows about individually:

1. **Stage 1 — Draft** (`POST /api/workflows/draft-golden/{plan,start}`): the console unpacks the ZIP itself (reusing `uploads.py`'s trust-boundary unpacking — the console owns the unpacked directory now, not the script), then runs `pin_document.py --document-dir <that dir> --all --dataset <name> --org <id> --action <id> --version <trusted-version> --yes` as its own job (`build_pin_argv`, new). Priced and approved exactly like every other quota-spending route (`--plan` first, `approved_extractions` echoed back). On completion this job is **terminal** — it releases the workspace lock like any other finished job.
2. **Review** (no subprocess, no lock held): the console reads back the dataset items `pin_document.py` just wrote (via `reader.py` / a thin read of the platform dataset) and renders them for the curator. The curator may accept as-is, edit individual field values, or upload a replacement `golden.json`. See §Golden edit surface below.
3. **Stage 2 — Verify** (`POST /api/workflows/verify-candidate/{plan,start}`): started only once the curator has explicitly approved *after* stage 1 and the review step have both completed. Runs `verify_document.py --document-dir <same dir> --all --dataset <name> --version <candidate-version> --yes` as its own job (`build_verify_argv`, new), priced and approved independently of stage 1's approval — a fresh `--plan` computed at approval time, not a number carried over from stage 1.

A **review session** record ties the two stages together: `{session_id, dataset, org_id, action_id, trusted_version, candidate_version, document_dir, archive_sha256, approved_golden_hash, stage1_job_id, stage2_job_id, state, created_at}`, persisted at `<workspace>/.idp-regression-jobs/review-sessions/<session_id>.json` (mirrors the existing job-history persistence pattern in `jobs.py`, same owner-only `0600` mode, atomic temp-file+rename write). This is what lets stage 2 be started **later, even after a console restart** — the document directory and the version pair don't have to be re-typed or re-uploaded, because they're what stage 1 already pinned. `approved_golden_hash` and `state` exist specifically for R1/R2 below — see INV-09 clause (d).

### Why not keep one subprocess and add a pause inside it?

`compare_versions.py` is a script meant to be run start-to-finish from a terminal too (`--yes` once, "the archive is unpacked ONCE... both halves are pointed at that directory"). Teaching it to pause mid-run for a human in a browser would either (a) require a bidirectional protocol between the console and the subprocess (stdin prompts, a signal, a file it polls) — real complexity for a script whose whole design principle is "the real script called in process, never a reimplementation" — or (b) fork the script into a console-only variant, which is exactly the reimplementation `golden_pipeline.py`'s rule forbids. Two ordinary subprocess invocations, each already a first-class script with its own `--plan`/`--yes` contract, is less new code than either alternative, and it reuses the existing job/lock/history machinery unchanged for both halves.

### Why does the review step hold no workspace lock?

The `flock` in `_WorkspaceLock` exists because two subprocess batches against the same pin store interleave badly (`pin_document` and `verify_document` racing on the same deterministic-id writes). Nothing is running during review — it's a human looking at data and the console handling ordinary API reads/writes. Holding the lock across an indefinite human-paced pause would mean a curator who steps away for lunch blocks every other validation in that workspace for no operational reason. The trade-off, accepted here: a second console session *could* start an unrelated job against the same workspace while a review is pending, and — because `flock` is per-workspace — a **different workspace or machine** entirely is never excluded by this lock at all.

**Correction (Atchim review round 1, R1 — the original version of this section was wrong about what that trade-off actually risks.** The `document_dir`/`archive_sha256` reasoning above protects the **corpus bytes on disk**, and that's genuinely enough for `pin_document.py`'s own concurrency. But stage 2 (`verify_document.py`) does not verify against disk — it verifies against **the platform dataset item**, keyed `uuid5(dataset | document_id)` with **no version or corpus binding in that key** (the pin-store layout note in `CLAUDE.md` and the open `DEBT-116` item both already say this: the same item id can be silently overwritten by an unrelated pin into the same dataset name, last-write-wins). During an unlocked, human-paced review pause — now made *indefinite* by this ADR, where it used to be a seconds-long window inside one subprocess — nothing stops a second workspace, a second operator, or a stray script from provisioning a different golden into the same dataset name. In the **accept-as-is** path the curator writes nothing back, so the platform item the curator reviewed could be silently replaced by the time stage 2 reads it — and because the corpus files on disk are untouched, the sha256/document_dir checks alone would pass. That is a silently-wrong `STILL VALID`, the exact failure class `## Rigor` names as this system's worst, produced by verifying against a golden the curator never actually saw approved.

**Fix, folded into this ADR rather than deferred to `/harden`:** at the moment the curator finishes review (accepts as-is, finishes editing, or replaces the file), the console computes a content hash over the platform dataset items it is about to let stage 2 measure against and stores it as `ReviewSession.approved_golden_hash`. INV-09 gains clause (d): `verify-candidate/start` re-fetches the current dataset items and refuses (409) if their hash no longer matches `approved_golden_hash` — the same shape as `pin_document.py`'s existing sha256-mismatch refusal, no new validation primitive invented. This closes the gap for THIS workflow's own review pause; it does not fix `DEBT-116`'s underlying root (the platform item id still carries no version/corpus), which remains open and is now more exposed because the pause this ADR introduces is longer than any window that existed before it. `DEBT-116` should be re-weighted accordingly when `/plan` cards the debt register update.

### Golden edit surface

Two write paths, both reusing existing, already-hardened validation — no new validation logic is introduced:

- **Single-field edit:** a new endpoint validates the edited value against the committed golden schema for that one field (the same local Ajv-equivalent check `provision_golden_dataset.py` runs before writing) and, on success, upserts that one dataset item via the same deterministic-id path `pin_document.py` already uses. One edit = one immediate write; there is no local-only "draft of a draft" state, per UC-02 BR3.
- **Whole-set replacement:** the curator's uploaded `golden.json` goes through `provision_golden_dataset.py`'s existing batch path unchanged — schema-validated entry by entry, **all-or-nothing** (the existing 2026-09-27 "one invalid entry refuses the whole batch" rule), and on success it fully replaces what stage 1 drafted for this dataset.

Both paths spend **zero** IDP quota — they are platform writes only, never a re-extraction.

## Consequences

**What this buys:** the console's guided flow can no longer spend the second (verification) half of its quota against an unreviewed, possibly-wrong drafted golden — closing exactly the gap UC-02 was written to close. It does this by composing two already-independently-tested scripts rather than inventing new extraction logic, and it reuses the existing `--plan`/`approved_extractions` pattern twice instead of building a new approval primitive.

**What it costs:**
- `TestQuotaBoundary`'s route table grows from one quota-spending route to **three** (`compare/start` kept for the unreviewed/expert path, plus the two new ones) — `/api/health`'s declared list and its drift-guard test must be updated in the same change, or this reintroduces the exact "health claims fewer spending routes than exist" defect PROGRESS.md already records happening once (the noise-floor round).
- A corpus can now be "half validated" indefinitely — pinned at the trusted version, drafted, reviewed, and never verified — which didn't exist as a durable state before (the old flow was one job, start to finish). The review-session file is that new durable state, and it needs its own lifecycle answer (stale/orphaned sessions, a corpus whose files were deleted between stages) — carded below for `/harden`.
- Two approvals instead of one is genuinely more friction for a curator who trusts the draft outright — accepted deliberately per UC-02's business value; a "skip review" path remains available via the pre-existing `compare/start` route for whoever wants the old one-shot behavior.

**Alternatives considered:**
- *Pause inside `compare_versions.py` itself* — rejected, see above (reimplementation / new IPC surface for no net code reduction).
- *One job with an internal paused state instead of two terminal jobs + a session file* — rejected because the existing `Job`/`JobRegistry` model in `jobs.py` is built around "a job runs a subprocess to completion, then is history" (`summarize()`, `find_run_artifact()`, the INTERRUPTED-on-restart semantics all assume this); forcing a "job that's running fine, just not right now" state into that model would touch more of the existing, well-tested job machinery than adding a small, separate review-session record next to it.
- *Aggregate both approvals into one, priced upfront as 2N before drafting even starts* — rejected; it's exactly today's behavior and defeats the purpose (the second approval must be priced and given **after** the curator has seen the actual drafted data, per UC-02 BR1).

## Containment (Branca co-design, 2026-09-28)

**1. Review-session file.** Fail-closed on read: missing, unparseable, truncated, or schema-invalid → stage 2 refused with a named error, never a best-effort resume. Written atomically (temp file + rename) so a crash mid-write cannot leave a half-file that reads as valid. Binds to its corpus by **both** `document_dir` and the archive's sha256 (not just the directory path) — both checked at stage-2 start. Owner-only `0600` (run-identity data, sensitive-adjacent). Idempotent on `session_id`: re-POSTing stage 2 for an already-verified session returns the existing job, never a second spend. Visible, not auto-expired — an "awaiting review" list with a displayed age; cleanup is operator-driven, never a silent TTL discard of a paid-for draft.

**2. The stage-1→stage-2 gap, including a console restart.** Stage 2 independently re-runs preflight and re-prices via a fresh `--plan` at approval time — it never trusts stage 1's numbers or stage 1's preflight result. It refuses to start if `document_dir` is gone or any document's sha256 diverges from what stage 1 pinned (reusing `pin_document.py`'s existing mismatch refusal — no new check invented); `--allow-missing`/`--allow-partial` stay off. Restart semantics: a session with a terminal stage-1 job and no stage-2 job is **resumable** and shown as such; a stage-2 job interrupted mid-run stays **INTERRUPTED-never-resumed** per the existing `jobs.py` rule — the spend is spent either way. Because the review pause holds no `flock` (by design, §Why does the review step hold no workspace lock?), stage 2 re-takes the lock and re-verifies pin-store state on entry rather than assuming the store is unchanged since stage 1.

**3. The two independent `approved_extractions` gates.** Each route computes its own count from its own real `--plan`; a mismatch is a 409, unchanged from the existing pattern. The echoed count is bound to **route + session_id**, so a stage-1 approval is structurally unusable as a stage-2 approval (cross-route replay also 409s, not just a stale-number mismatch). `/api/health` and `TestQuotaBoundary` must enumerate all **three** spending routes (`compare/start` kept, plus the two new ones) in the same commit that adds them — the health-declaration-drift defect has already shipped once (the noise-floor round, PROGRESS.md) and must not repeat here. Both new routes are refused server-side when preflight says the machine cannot finish, exactly like the existing two.

**4. Golden-edit write paths.** Single-field edit: validated against the committed schema before the upsert; a rejected edit leaves the drafted item unchanged (fail-closed, no partial write) — the deterministic-id upsert is naturally idempotent. Whole-file replace: all-or-nothing (existing "one invalid entry refuses the batch" rule); a failed replace leaves the drafted set intact as the fallback. Provenance must stay visible in the UI: an edited field renders as **edited**, never blended into "drafted" — an operator must never read a curator's correction as what the extractor actually found. Both write paths spend zero IDP quota (asserted by test, not just assumed).

**Prompt-injection / LLM security:** none applicable. This UC composes no LLM prompt and processes no model output — the IDP extraction is an external MuleSoft call, and the golden/edit surfaces are schema-validated JSON, not prompt text. The curator-uploaded `golden.json` is untrusted input, but it is contained by the existing Ajv-strict schema gate, the same boundary `provision_golden_dataset.py` already enforces — no new trust boundary is introduced.

**Non-blocking follow-ups for `/debt`** (Branca, not gating this ADR): document the review-session cleanup UX as a tracked item rather than an implicit TODO; confirm `0600`/`0700` on `review-sessions/` holds on multi-user hosts the same way the existing job-history directory's does.

## Open items for `/plan`

- Epic E has no SPEC file (recorded gap, `CLAUDE.md ## Rigor`). This ADR does not resolve that; `/plan` decides whether UC-02 gets its own SPEC or is carded directly.
- Exact review-session TTL / cleanup UX is a product decision, not an architecture one — flagged, not decided, here.
- **`/debt add`, owed to Dunga at `/plan`:** (1) `DEBT-116`'s review-pause vector — even after `approved_golden_hash`/INV-09(d) close it for this workflow, the platform item id's lack of version/corpus binding remains the open root cause and is now exposed over a longer, human-paced window than before this ADR; (2) review-session cleanup/TTL UX, so it lands as a tracked item rather than an implicit TODO (Branca, round 1).
- **Reversibility, stated explicitly (Atchim S1):** this change is additive — the two new routes can be removed and `compare/start` (the pre-existing one-shot path) continues to work unmodified. Rolling back leaves durable `<workspace>/.idp-regression-jobs/review-sessions/*.json` files on disk (harmless — they reference a dataset/corpus, not credentials or extracted values) and requires reverting `/api/health`'s three-route declaration and `TestQuotaBoundary` back to one. No data migration is needed either direction.
- **Operator-facing route counts belong in `/api/health`, never restated as a number in prose (Atchim S2):** PROGRESS.md already records a round where a third surface (`/openapi.json`'s FastAPI `description=`) drifted from the real quota-spending route count because nothing bound it there — fixed by removing the count and pointing at `/api/health` instead of updating it. `/implement` must apply that same pattern to any new UC-02 operator-facing text (docstrings, UI copy) rather than hard-coding "three routes" anywhere; `/qa` verifies no such count exists outside `/api/health`'s own declaration.

---

## Amendment — the review screen's values source (2026-09-28, gap-fill during S-02.3)

**Status of this amendment:** Accepted. This is a scope-completion gap-fill on the already-accepted ADR-0008 design, **not** a new architectural direction. It does not touch the quota-approval boundary (INV-09, `TestQuotaBoundary`), spends no quota, and is read-only.

### The gap

UC-02's whole premise (AC2, BR3) is that the curator *sees the drafted expected value of every field of every document* before giving the second approval. But the design above never named **where the review screen reads those values from**, and the assumption baked into `docs/design/UI-SPEC-UC-02.md` (that the review table would be fed by "the platform dataset items" the way a dashboard is) was wrong in a way that only surfaced during S-02.3 implementation:

- `GET /api/reviews/{session_id}` returns **only** the `ReviewSession` record (CT-06) — dataset, versions, hashes, state, provenance field *names* (`edited_document_ids`). It carries **no field values**, by design, and correctly so.
- The console's platform-read seam `insights.dataset_items()` (`platform/insights.py`) **deliberately strips `expectedOutput`** — it is identity-only, the correct `## Domain`/INV-01-adjacent choice for a dashboard listing, and that choice must **not** change.
- So S-02.3 shipped a `GoldenReviewTable` that could render only document ids + provenance flags — a review screen that cannot show what is being reviewed (PROGRESS.md 2026-09-28: "GoldenReviewTable shows identity-only … api.py frozen").

The drafted values are **not** actually unreachable; there was simply no endpoint that exposed them:

1. Stage-1 `pin_document.py` writes every drafted golden to the **local pin store** (`<store>/goldens/<action-id>/<action-version>/<doc>.json`, owner-only, already read by `ui/reader.py`), **and** upserts each as a platform dataset item.
2. The **raw** `GET /api/public/dataset-items?datasetName=…` response carries `expectedOutput` per item — it is only `insights.dataset_items()` that strips it. The existing module-level helpers `fetch_golden_hash()` and `fetch_platform_item()` in `api.py` already read `expectedOutput` off that raw response, bypassing the identity-only listing.

### Decision

**Add one new read endpoint, `GET /api/reviews/{session_id}/values`, sourced from the platform via a single raw `dataset-items` fetch.** Do **not** fold values into `GET /api/reviews/{session_id}`, and do **not** source them from the local pin store.

**Why the platform, not the pin store.** The pin store holds what stage 1 *drafted*; it is **not** updated by the golden-edit surface — a single-field PATCH and a whole-file `/replace` write only to the platform (`upsert_platform_item`), never back to the pin store. Reading the review table from the pin store would therefore show **stale drafted values after any edit**, directly violating AC3 ("no divergence between what the console shows and what verification later reads"). Stage-2 `verify_document.py` measures against the **platform** dataset items, and INV-09(e) hashes those same items — so the review table must read from exactly that source to show the curator the bytes the gate will actually use. The platform is the single authoritative golden; the pin store is a stage-1 artifact only.

**Why one fetch, not a per-document fan-out.** `fetch_platform_item()` fetches the *whole* `?limit=1000` list and returns one item — calling it per document would be O(N²) network at 100 documents. The raw list already contains every item's `expectedOutput` in one round-trip (the same call `fetch_golden_hash` makes), so the new endpoint does **one** GET and maps every item to `{document_id, expectedOutput}`. A new module-level, injectable helper (`fetch_platform_items(dataset, workspace)`, same pattern as `fetch_golden_hash`/`fetch_platform_item`) makes the CI-runnable unit test possible against a faked adapter with no live platform.

**Why a separate endpoint, not an extension of `GET /api/reviews/{session_id}`.** Three reasons, all consistency with existing patterns:
1. **Fail-mode separation.** `GET /api/reviews/{session_id}` is a *local, fail-closed* read (404/422, never a network dependency); the console consistently separates that class from *platform* reads (`GET /api/platform/datasets/{name}` 503s when unconfigured). Folding a platform fan-out into the session read would make a local metadata read 503 because the platform is down — and would drag N1's `< 2s`/100-document target (a network-free fixture assertion today) behind a live round-trip.
2. **Payload size & lifecycle.** The session record is tiny and read by the pending-list navigation and stage-2 pricing; the values payload for 100 documents is large (full `expectedOutput` × 100). They have different sizes, different cache lifetimes, and — per UI-SPEC's *Loading* state ("skeleton rows while fetching the drafted dataset items") — different loading states. A separate fetch is exactly the shape the frontend already assumes.
3. **The frontend already splits them.** `GoldenReviewTable` consumes per-(document, field) rows; the session read feeds the header/state machine. Two endpoints map cleanly onto the two components with no reshaping.

### Response shape (the contract `GoldenReviewTable` consumes — CT-07)

One object per document, values-carrying, provenance-tagged from `ReviewSession.edited_document_ids` (reusing T-02.2.3's existing provenance — no new mechanism):

```jsonc
{
  "session_id": "…",
  "dataset": "…",
  "platform_configured": true,
  "documents": [
    {
      "document_id": "inv-001",
      "fields": [
        {"name": "invoice_date", "value": "2024-06-28", "type": "date",
         "confidence": 0.98, "critical": true, "provenance": "drafted"},
        {"name": "invoice_total", "value": "1250.00", "type": "number",
         "confidence": 0.90, "critical": true, "provenance": "edited"}
      ],
      "tables": [
        {"name": "line_items", "match_key": "sku",
         "rows": [ {"sku": {"value": "…"}, "qty": {"value": "…"}} ]}
      ],
      "prompts": [ {"key": "…", "answer": "…", "source": "…"} ]
    }
  ],
  "missing_from_platform": ["doc-x"]   // pinned but not yet on the platform — a bug signal, shown, never hidden
}
```

- `provenance` is `"edited"` iff the field name is in `session.edited_document_ids[document_id]`, else `"drafted"` (Branca containment note: edited fields must render distinctly, never blended into "drafted").
- Platform unconfigured/unreachable → **503** (same as `/api/platform/*`), so the review screen shows a clear error rather than an empty table read as "nothing to review".
- **INV-02:** the endpoint *returns* values to the loopback client — that is its whole purpose and the same disclosure posture as run artifacts and the existing `fetch_platform_item` — but it **never logs** a field name or value (log `session_id` + document count only).

### Consequences

- **Positive:** the review gate becomes actually exercisable; the values shown are provably the bytes stage 2 measures against and INV-09(e) hashes (no draft-vs-edit divergence); the local session read stays fail-closed and network-free; no change to the identity-only `insights.dataset_items()` dashboard seam.
- **Negative / follow-up:** one more platform-dependent route (read-only, no quota — **not** added to `/api/health`'s `quota_spending_routes`, which stays four). At >1000 dataset items the single `?limit=1000` fetch would need pagination; today the N5 ceiling caps a run at `DEFAULT_MAX_DOCUMENTS_PER_RUN` = 1000, so one page suffices — noted so a future ceiling-raise revisits it.

### Task placement

This lands as **new task T-02.3.7 on S-02.3** (the console-UI story, in flight), **not** as a retroactive edit to S-02.1/S-02.2 (both DONE, QA-02). The project's rule holds: a Done story's DoD is not reopened; a capability surfaced by a later consumer is carded on that consumer. Although the endpoint physically lives in `api.py` (S-02.1 territory), its entire reason to exist is S-02.3's `GoldenReviewTable`, and S-02.3 is where the work is live — so it is scoped there as a small backend-plus-frontend addendum (new `fetch_platform_items` helper + the `GET /api/reviews/{session_id}/values` route + wiring `GoldenReviewTable` to it and replacing its identity-only placeholder). Its DoD inherits S-02.1's mechanical floor (mypy/ruff clean, a CI-runnable unit test against a faked platform adapter) and the INV-02 no-values-in-logs assertion.
