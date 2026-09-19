# QA-S-01.5: audit of SPEC-01 / Story S-01.5 — SPIKE-01 Langfuse form-mode nested-JSON

**Verdict:** ⚠️ Pass with follow-ups
**Author:** alex@divinocosta.com.br (solo mode)
**Date:** 2026-09-19 · **Rigor:** standard · **Branch:** spike/langfuse-form-mode (working tree, nothing committed for S-01.5)
**Story type:** manual spike. No production code, no TDD stamp expected, and none written (`docs/qa/TEST-S-01.5-*` absent: correct). Observability and performance N/A per DoD.

## Summary
The spike answered its question with live evidence: Langfuse 4.38.0 OSS stores and server-validates every nested shape, and renders none of them as form fields. The re-decision ADR-0005 supersedes ADR-0001 and is Atchim-APPROVED. ASM-05 is closed with a split verdict. No blockers. Two Major follow-ups: the platform image is not actually pinned, and the durable deliverables are sitting uncommitted on a branch that the spike report says to discard. Several Minor doc-drift items remain.

**ADR-0005 Atchim review line, as read at audit time:** `APPROVED (R1–R4 closed, re-checked 2026-09-19)` (ADR-0005 l.3, l.6, l.198). Status: Accepted.

## DoD walk (SPEC-01 l.184–198, TP-26/TP-27)

| # | DoD item | Result | Evidence |
|---|---|---|---|
| 1 | Live self-hosted Langfuse (Docker); version pinned + recorded | ⚠️ Met with deviation | Recorded as 4.38.0 OSS (SPIKE-2026 l.12; SPIKE-01 §Result l.59). Running images `langfuse:4` / `langfuse-worker:4` carry the label 4.38.0. **Not pinned:** `../langfuse/docker-compose.yml:8,109` use floating `:4`. See F-1 |
| 2 | Representative nested golden with fields (number/date/id/text), tables (≥2 rows, column-keyed), prompts (≥1), synthetic only | ⚠️ Met with deviation | `spikes/langfuse-form-mode/golden.py`: 4 typed fields, `line_items` with 2 rows, 1 prompt. All values synthetic (`SYN-0001`, `Synthetic Widget A`, `Synthetic Vendor Ltd`). The shape diverged from DATA-MODEL-01 (prompts array, `answer.value`, `{value}` cells, numeric `total`). ADR-0005 #2 classifies this as a probe artifact, and Addendum 2 re-probed the fields+prompts map with string values. The tables block under the committed shape was not live-probed; that is ADR-0005 F2. See F-6 |
| 3 | Form mode enabled; UI mechanism recorded | ✅ Met | SPIKE-01 §Result l.59: JSON Schema on the dataset (`expectedOutputSchema`/`inputSchema`), no inference. Edit dialog is a CodeMirror raw-JSON editor |
| 4 | EX-C1-1/EX-C1-2: nested edit + save + read-back; table-cell edit; invalid-value rejection; new-row add (TP-26/TP-27) | ⚠️ Met with deviation | **Nested-field edit, table-cell edit and new-row add were done through the API** (`probe.py`, `probe2.py`; SPIKE-2026 l.23 "API update + read-back"). **Only invalid-value rejection went through the live UI** (Chrome: Edit → invalid value → Save changes blocked → API read-back unchanged; SPIKE-2026 l.17, l.25, l.28). The deviation is acceptable because the UI has a single raw-JSON editor that writes to the same schema-validated endpoint, and the refutation comes from observing that editor, not from the API edits. SPIKE-01 §Result does not state which channel was used. See F-3 |
| 5 | Verdict recorded with per-shape detail in SPIKE-01; artifacts redacted of PII/golden values | ✅ Met | SPIKE-01 §Result l.57–70: PARTIALLY CONFIRMED / no-code Curator REFUTED, per shape. There are no screenshots in `docs/` or `spikes/`. The text descriptions quote only synthetic values |
| 6 | Observability N/A | ✅ Met (N/A) | Manual spike, no runtime surface |
| 7 | Performance N/A | ✅ Met (N/A) | Same |
| 8 | ADR-0001 label + ASM-05, branched per verdict | ⚠️ Met with deviation (intent satisfied) | The SPEC's "partially confirmed" branch (keep PROVISIONAL, ASM-05 partially-resolved) was written before the re-decision ADR. The part that failed is the *load-bearing* capability, so the "refuted" branch applies: a superseding ADR. What was done: ADR-0005 Accepted + Atchim APPROVED; ADR-0001 → `Superseded by ADR-0005` (l.3, l.5); ASM-05 → resolved, split verdict (ASSUMPTIONS.md l.11). A superseding, reviewed ADR is stricter than a retained PROVISIONAL label, so the intent is met. S-01.3 is gated on the confirmed shapes through ADR-0005 F2/CT-05. Stale text remains; see F-4 |
| 9 | If refuted: re-decision ADR opened (symmetric Langfuse vs Opik) | ✅ Met | ADR-0005 options A/B/C. Opik was evaluated at source level only, and the ADR says so (l.80, SPIKE-2026 l.54) |
| 10 | Self-hosting obligations (N25) handed to Mestre | ✅ Met | HANDOFFS.md l.27–31: encryption at rest, DB access control, tested backup/restore. These block any real-golden load |
| 11 | Langfuse credentials registered with Mestre; env-only; never in artifacts | ✅ Met | Same handoff block. CLAUDE.md External services row. `.env` is gitignored (`.gitignore:15`). `lf.py` reads from `.env`/environ and never prints keys. Secret grep over `spikes/` and `docs/spikes/` (key prefixes, key/secret/password/token literals, Bearer/Basic/JWT): **0 hits**. See F-7 |
| 12 | Time-box half a day | ✅ Met (weak evidence) | All artifacts are dated 2026-09-19. Spike scripts are timestamped 00:57–01:18. No duration was recorded explicitly |
| 13 | Reviewed by Atchim (verdict + ADR update) | ✅ Met | HANDOFFS.md l.15–19: "Spike verdict wording faithful", APPROVE WITH NOTES. R1–R4 re-check APPROVED (PROGRESS l.8; ADR-0005 l.198) |
| 14 | Zangado `/qa` | ✅ This report | — |
| — | Spike code throwaway, uncommitted, on spike branch, nothing merged | ✅ Met | `spikes/` untracked. `git log --all -- spikes docs/spikes` is empty. `spike/langfuse-form-mode` is not an ancestor of `main`. `__pycache__` gitignored |
| — | No `docs/qa/TEST-*.md` stamp for S-01.5 | ✅ Met | `docs/qa/` contains no S-01.5 stamp |

**Scans:** SCA N/A (spike scripts are stdlib-only; nothing new in the lock). The secret scan used the grep fallback because gitleaks/trufflehog are not installed (DEBT-12). The fallback was clean.

## Critical (blockers — must fix before Done)
None.

## Major (should fix this iteration)
- **F-1: The Langfuse version is recorded but not pinned.** `../langfuse/docker-compose.yml:8,109` use `langfuse:4` / `langfuse-worker:4`. A `docker compose pull` would silently move to 4.39+. ADR-0005 relies on version-specific behaviour: Ajv `strict:true`, the 10,000-char schema cap at `jsonSchemaValidation.ts:42`, `events_only`, and `/v3/scores`. The compose file is also outside this repo. Fix: pin both images to `4.38.0` (or a digest). Record the compose location and pin in CLAUDE.md External services. Re-probe CT-05 before any version bump. Owner: Mestre. Must land before the S-01.3 integration tests.
  - **CLOSED 2026-09-19.** All 6 compose images pinned to exact tag + manifest-list digest (not just web/worker): `langfuse:4.38.0@sha256:47ef2f12…`, `langfuse-worker:4.38.0@sha256:8631cf42…`, `clickhouse-server:25.12@sha256:8a790dd3…`, `minio:latest@sha256:83b4a2d9…`, `redis:7@sha256:71da9275…`, `postgres:17@sha256:67f41722…`. Verified: `docker compose config` parses, and every pinned digest resolves to the image ID of the container currently running (6/6 MATCH — no recreate on next `up`). Web+worker both report `org.opencontainers.image.version=4.38.0`, revision `4ecaabed`. Compose location + pin recorded in CLAUDE.md External services. Two caveats carried forward: (a) the postgres `${POSTGRES_VERSION:-17}` override was dropped — a digest pin and a caller-chosen tag cannot coexist; (b) Chainguard's free tier publishes only `:latest` and GCs old digests, so the minio pin may become unpullable (image is cached locally; re-pin and record if a pull fails).
- **F-2: Durable S-01.5 deliverables are uncommitted, on a branch the spike report says to discard** (SPIKE-2026 l.103). Uncommitted items: `docs/adr/` (untracked, including ADR-0005 and the Superseded ADR-0001), `docs/spikes/`, `docs/qa/`, and modified `docs/state/*` and `docs/design/*`. Only `spikes/langfuse-form-mode/` is throwaway. Fix: commit the docs (not `spikes/`) to a durable branch or PR before the spike branch is deleted. Owner: user / Dunga.

## Minor (follow-up cards)
- **F-3: The channel for each EX-C1 interaction is not stated in SPIKE-01 §Result.** l.62–65 says "including add-row and cell edit" without saying these were done through the API. Append: "(nested edit, cell edit, add-row via API; invalid-value rejection via live UI)". Owner: Soneca/Dengoso.
- **F-4: Stale status text.** Items still out of date:
  - SPIKE-01 l.3 still reads `Status: Proposed`.
  - SPIKE-01 l.70 says "ADR-0001 stays PROVISIONAL / ASM-05 partially resolved until that ADR lands". The ADR has landed.
  - ASSUMPTIONS.md ASM-05 says "Atchim review PENDING". It is APPROVED.
  - CLAUDE.md l.50 says "provisional on SPIKE-01, see ADR-0001". It should read "decided by ADR-0005".
  - The Mestre handoff says "keeps PROVISIONAL status in view".
  
  Owners: Soneca (SPIKE-01, ASM-05), Mestre (CLAUDE.md).
- **F-5: HANDOFFS.md ordering.** The block "Soneca → Dunga (ADR-0005 R1–R4 applied)" was appended at l.138. That breaks the newest-first rule, and the header format is non-standard. Its "Atchim has not yet re-checked" is now obsolete. Owner: Dunga.
- **F-6: The tables block has not been live-probed under the committed DATA-MODEL-01 shape** (flat `{column: "string"}` cells). Only fields+prompts were re-probed in Addendum 2. This is already tracked as ADR-0005 F2 / CT-05 in S-01.3, so no new card is needed. Confirm that F2 includes a live write of the tables block.
- **F-7: The secret scan used the grep fallback because gitleaks is absent** (DEBT-12, existing). The fallback was clean. The fallback does not count as a real gitleaks pass.
- **F-8: Spike datasets need cleanup on the instance.** Seven datasets are awaiting UI deletion (SPIKE-2026 l.102). They are synthetic and low risk. Owner: user.

## Debt to record (via `/debt add`, Dunga)
- F-1: unpinned Langfuse image (ops, Mestre).
- F-8: spike dataset cleanup (hygiene).

## Hand-off to Dunga
Cards to create:
- TASK: Pin Langfuse web/worker images to 4.38.0 and record the compose location. Severity Major. DoD: compose pins an exact tag or digest; CLAUDE.md External services notes the pin; S-01.3 integration is gated on it.
- TASK: Commit the S-01.5 durable docs off the spike branch. Severity Major. DoD: `docs/adr`, `docs/spikes`, `docs/state`, `docs/design`, `docs/qa` are committed on a durable branch; `spikes/` is excluded.
- TASK: Doc-drift sweep after ADR-0005 (F-3, F-4, F-5). Severity Minor. DoD: every stale PROVISIONAL/PENDING/Proposed reference is updated, the SPIKE-01 channel note is added, and the HANDOFFS block is moved to the top.
- TASK: Clean up the spike datasets on the local Langfuse instance (F-8). Severity Minor.

Fine. It passes. The spike did what a spike should: it found the premise was false and said so. The pin and the commit are not optional.
— Zangado
