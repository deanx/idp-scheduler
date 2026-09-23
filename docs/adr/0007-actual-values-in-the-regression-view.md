# ADR-0007: Making "what did it extract instead?" answerable in the regression view

**Status:** ✅ **ACCEPTED 2026-09-23 — Option E (local run artifact; fingerprint deferred), by user decision.**

> **The decision, and what it settles.** The user chose **Option E**: the full verdict map (expected, actual, confidence, verdict) is written to a **local, gitignored per-run artifact**, and **the platform stays value-free**. **DEBT-18 option B is UNCHANGED and remains in force** — this ADR does not amend it, and `## Domain`, ADR-0005's span allowlist, INV-01, CT-02/CT-03 and DATA-MODEL-01 §4 all stand as written. Options **B** and **C** were considered and **rejected**; they would have reversed a user decision and were additionally conditional on `/signoff` **B-1/B-2** closing first (§4).
>
> **Option D's fingerprint is DEFERRED, not adopted.** Option E includes it only "if and when the user wants cross-run change detection". It is **not** in scope for the first implementation: nothing goes into the `output` map as part of this decision. Re-open it together with ADR-0006's deferred per-field-confidence question — they are the same class of decision and should be put to the user once, not twice.
>
> **What implementation owes (for Dunga to card; no DoD is edited here).** An orchestration story: the artifact writer; the path and retention/deletion policy; a `.gitignore` rule; and an **INV-01-style leak test proving the artifact path can never enter the repo**. The §2 Option A cost analysis is binding, not advisory — in particular, **a CI-run artifact lands on the runner, not the reviewer's laptop**, and publishing it as a CI build artifact would make it readable by the whole org, which is a *worse* disclosure surface than the self-hosted Langfuse it was meant to avoid. **Publishing the artifact from CI is explicitly out of scope and must not be done casually.**
>
> **The honest residual, stated once.** Option E does not remove the ergonomic complaint that prompted this ADR — it answers "what did it extract instead" in a second window, correlated by hand, and it answers it only for whoever ran the regression. For the **CI gate persona** it answers nothing until someone opens the runner's artifact. That was accepted knowingly in exchange for keeping extracted financial values off a platform instance that currently runs on a published encryption key and a default database password.
**Date:** 2026-09-22
**Context (use case):** UC-01; ADR-0006 step 10 (Langfuse is the scoreboard / regression view), §Step 8 "two honest limits"
**Evidence:** Langfuse OSS **4.38.0** source read at `../langfuse` (`web/package.json` = 4.38.0, commit `6e9f8eb`) — the same version running locally; `docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md`; ADR-0005 #9 + its span-attribute allowlist; `docs/qa/SIGNOFF-2026-09-22.md` B-1/B-2.

## Context

ADR-0006 recorded the limit honestly: the version-over-version view answers *which field regressed, on which document* and never *what it extracted instead*. The user has asked how to get the values. This paper investigates; it does not decide.

**The rule in force** (`CLAUDE.md ## Domain`, user decision 2026-09-19, DEBT-18 option B): the golden set lives only in its dataset items; **no extracted (actual), expected or confidence value is written anywhere else on the platform** — score comments and trace spans carry only `document_id` and verdicts.

**The asymmetry is the whole crux, and it must be stated before the options.** Expected values *are* on the platform — they are the dataset items, that is the golden set, and (see §1) the compare view **already renders them in an "Expected Output" column**. What option B excludes is the **actual** and the **confidence**. So "show me what it extracted instead" is not a request to introduce sensitive data to a clean platform. It is a request to place the *actual* next to an *expected* that a viewer of that grid can already read. That makes the incremental-disclosure argument weaker than it first looks — and the blast-radius argument stronger, because the actual is the only column that changes every run and therefore accumulates.

**One fact that shapes every option below:** the values already exist in-process. `classify()` returns the CT-02 verdict map `{name → {verdict, expected, actual, confidence, critical, type}}`; `facade.py` uses it for `build_score_inputs` and the gate, then **drops it on the floor** (`facade.py` ~L631–669). No new extraction, no new fetch, no new cost — only a decision about where that map is allowed to go.

## 1. Where values could technically go in Langfuse 4.38.0 — and what reaches the comparison view

Read from the 4.38.0 source, `web/src/features/datasets/components/DatasetCompareRunsTable.tsx` (the compare grid) and `DatasetAggregateTableCell.tsx` (each run's cell).

| Surface | Written today? | Reaches the **compare** view (two runs side by side)? | Notes |
|---|---|---|---|
| Dataset item **`expectedOutput`** (the golden) | Yes — by provisioning/Curator, not by a run | ✅ **Yes** — a first-class `Expected Output` column on every row | This is the asymmetry above, confirmed in source, not assumed |
| Dataset item `input` / `metadata` | `input={"document_id"}`; item `metadata=None` | ✅ `Input` column; `Metadata` column exists but is `defaultHidden: true` | Item-level, i.e. one value for all runs — **structurally wrong for a per-run actual** |
| Experiment item **trace `output`** | ✅ Yes — the verdict map + `gate` (ADR-0005 allowlist) | ✅ **Yes — per run, side by side.** `DatasetAggregateTableCell` renders `data.output` in each run's cell, toggleable as `Output` and on by default | **This is the natural home.** It is *already* the surface through which verdicts reach the view. Adding actuals here is a one-line change to a map we already build |
| **Scores** (`field:<name>`, `prompt:<hex>`, `gate`) | ✅ verdict literals | ✅ Yes, as aggregate badges, **with a baseline diff** (the cell computes score diffs vs a chosen baseline run) | This is what makes `match → mismatch` legible today. Values can't live here: a score is a number or a category, not a string payload |
| **Score comments** | ✅ always `None` (option B) | ❌ **No.** The compare grid renders only the aggregate badge; `comment` survives in the aggregate object but is displayed on the trace detail / `ScoreRow`, not in the grid | Important negative result: score comments are the *worst* candidate — maximum disclosure, near-zero benefit in the view people actually use |
| Trace-level **metadata** (`RunMetadata`) | ✅ three strings | ❌ Not a compare-grid column; visible in trace detail | Fine for run identity, useless for per-field values |
| Nested spans / observations | Not used per-field | ⚠️ Only the root trace/observation output is rendered in the cell | Per-field child spans would not surface in the grid |

**Conclusion of §1:** there is exactly **one** surface that both holds a per-run, per-document payload *and* renders side by side in the version-over-version comparison — the **experiment item's trace `output` map**. Everything else is either item-level (same for all runs), invisible in the grid, or typed wrong. Any platform-side option is therefore an option about that one map, which is convenient: the ADR-0005 span-attribute allowlist governs exactly it, and `tests/platform/test_inv01_payload.py` already pins it.

## 2. Options

### Option A — Local run artifact (no platform change)
Write a gitignored JSON artifact per run, keyed `run_id` → `document_id` → field, carrying the full CT-02 verdict map (expected, actual, confidence, verdict). The engineer opens it next to the Langfuse compare view; Epic E's remediation UI reads it later.

- **Buys:** the complete answer, including confidence and expected-vs-actual side by side — *more* than the platform could show. Zero new data on the platform. No `## Domain` change, no DEBT-18 reversal, no new trust boundary. Cheap: the data is already in hand and currently discarded.
- **Costs:** two windows, correlated by hand — the exact ergonomic failure the user is complaining about. Local to whoever ran it: a CI-run artifact is on the runner, not the reviewer's laptop, so for the *CI gate persona* it answers nothing unless the file is published, and publishing it is a new sensitive-data channel (CI build artifacts are usually readable by the whole org — that is a **worse** disclosure surface than a self-hosted Langfuse with project roles, and must not be done casually). Sensitive values now sit on local disks: needs `.gitignore`, a documented retention/deletion rule, and an INV-01-style test that the path never enters the repo.
- **Requires:** an orchestration story (artifact writer + path/retention policy + gitignore + leak test). No ADR-0005 change.
- **DEBT-18:** fully compliant. Option B untouched.

### Option B — Actual values in the trace `output` map (full reversal to DEBT-18 option A)
Extend the allowlist so `output` carries `{field: {verdict, actual}}` (and optionally `expected`, `confidence`).

- **Buys:** exactly what was asked, in the view where the question is asked, for every persona including CI reviewers, with no correlation step. Smallest code change of any option.
- **Costs:** this is a **reversal of a user decision**, not an amendment. Extracted invoice totals, IDs and financial values land in ClickHouse/Postgres/S3 on the Langfuse instance, forever, for every document of every run — the one dataset that grows without bound. It re-opens the DEBT-18 threat surface that ADR-0005's allowlist, INV-01, TP-45 and HARDEN-01 §3.4 were all built to close, and those controls would have to be rewritten rather than merely relaxed.
- **Requires:** explicit user decision; `## Domain` amendment; ADR-0005 allowlist + INV-01 + CT-02/CT-03 + DATA-MODEL-01 §4 amendments; new leak tests inverted; **and B-1/B-2 closed first** (see §4).
- **DEBT-18:** reopens it and flips it to option A.

### Option C — Partial disclosure: actuals only where the verdict is already a mismatch
Same surface as B, but `actual` is included only for fields whose verdict is not `match` (optionally also only for fields the golden marks non-sensitive, and/or truncated).

- **Buys:** the answer precisely where it is wanted (you only ask "what did it extract instead" about a regression), at a fraction of the volume — typically a handful of fields per failing document instead of every field of every document.
- **Costs — and this is the part to be honest about: a mismatch filter reduces *exposure*, not *sensitivity*.** A mismatching `total` on an invoice is exactly the financial value the rule exists to protect; the filter selects *for* the interesting values, not away from them. Per-record sensitivity is unchanged or higher. What it genuinely reduces is **blast radius and growth rate** (fewer records, bounded by failure count), which is a real security property, not a cosmetic one — so "partial is safer" is defensible *only* in that narrow sense and must not be sold as "we don't store sensitive values". Truncation is weaker still: the leading characters of a total or an ID are usually the informative ones. A per-field `sensitive: true` marker in the golden is the one genuinely principled variant, but it does not exist today and makes the Curator responsible for a security classification they have no training for (and the spike showed they edit raw JSON with silent save-blocking).
- **Requires:** the same decision and amendments as B, narrower in scope; plus a rule for what happens when nearly everything mismatches (a bad prompt version fails every field — the filter collapses to option B on exactly the run that leaks most).
- **DEBT-18:** a scoped amendment to option B, still a reversal in principle.

### Option D — Non-disclosing fingerprint / shape summary on the platform
Put, in the `output` map alongside each verdict, a derived non-value: a keyed fingerprint of the actual (HMAC with a secret that never leaves the process) and/or a shape summary (`type=numeric, len=7, changed_since_baseline=true`).

- **Buys:** reaches the compare view (it is just the `output` map), discloses no content, and answers one question the verdict genuinely cannot: **"the field mismatched in both v1 and v2 — did it extract the *same* wrong thing, or a *different* wrong thing?"** That is a real regression-view capability and it is invisible today.
- **Costs:** it does **not** answer the user's actual question — it never tells you *what* it extracted. A plain (unkeyed) hash is **not** a disclosure control for this data: totals, dates and document IDs are low-entropy with known formats and are trivially brute-forced from a digest, so only a keyed HMAC is defensible, and the key becomes a new secret to manage. Length/charclass summaries leak a little too (`len=7` on a currency amount narrows the range). Modest benefit, non-zero complexity, non-zero residual leak.
- **Requires:** a narrow allowlist amendment (derived non-values are arguably already outside "value", but that reading is the user's to make, not the architect's); HMAC key handling; contract test.
- **DEBT-18:** arguably compatible with option B's *intent* — same class of question as the confidence-score amendment already flagged in ADR-0006. Still needs the user to say so.

### Option E — Option A + a pointer (recommended; see §5)
Option A's local artifact, plus **Option D's `changed_since_baseline` fingerprint only** in the `output` map if and when the user wants cross-run change detection. The platform stays value-free and keeps doing what it is good at (which field, which document, which run, diffed against a baseline); the artifact answers "what instead" with full fidelity for the human who ran it.

## 3. What actually changed since 2026-09-19 — the risk is *worse* today, not better

The instance being self-hosted was the strongest argument for relaxing option B. `docs/qa/SIGNOFF-2026-09-22.md` removes that argument for now:

- **B-1 (Critical, N25, open):** the live instance runs on the **published default `ENCRYPTION_KEY`** and the **default `postgres` password**, with **no backup and no restore drill**. At-rest encryption with a public key is not encryption.
- **B-2 (Critical, N18, open):** the golden-set store is **bound on all interfaces** behind that default credential.

Self-hosted is only a mitigation when the host is hardened. Today it is a database on `0.0.0.0` with a documented password and a public encryption key — i.e. for *confidentiality purposes it is currently weaker than a managed SaaS*, which is the opposite of how the 2026-09-19 decision's context is usually recalled. Writing extracted financial values there **today** would be materially worse than writing them there after B-1/B-2 close. Said plainly: **B-1 and B-2 are open Critical blockers, and any option that puts actual values on the platform must not be implemented while they are open.** This is also not hypothetical exposure — the same instance already holds the goldens, so an option-B/C change increases what a single compromise yields.

## 4. Design patterns

No new pattern. Option A adds a writer at an existing seam (the orchestrator's post-loop step, ad-hoc — a function, no pattern intent applies). Options B/C/D are pure data-policy changes to the existing ADR-0005 span-attribute allowlist; the `PlatformAdapter` Protocol (Adapter) is unchanged in shape, so N24 swappability is unaffected by any of them.

## 5. Recommendation

**Take Option E: implement Option A (local, gitignored run artifact carrying the full CT-02 map) now, and leave the platform value-free.** It answers the user's question with *more* fidelity than any platform option, costs one small orchestration story on data we already compute and currently throw away, and requires no reversal of a user decision and no `## Domain` amendment.

**It is not conditional on B-1/B-2** — that is most of the point of recommending it. **Options B and C are conditional on B-1/B-2 being closed first**, and additionally on an explicit user decision; recommending them today would mean writing invoice totals into a database with a published encryption key, no backup, and a default password on all interfaces. If, after B-1/B-2 close, the two-window ergonomics of Option A still bite, **Option C** is the one to re-open — with its cost stated as it is above ("less exposure, not less sensitive"), not as "we only store the failures". Option D is a worthwhile *separate* small feature (cross-run "same wrong value or a different wrong value") and should be judged on its own merits, not as a substitute for values.

**DEBT-18 option B stands until the user says otherwise.** Nothing in this ADR is implemented, and no DoD, invariant, contract or `## Domain` line is changed by it.

## Consequences (if Option E is accepted)

- **Positive:** the question becomes answerable without touching the platform's sensitive-data posture; Epic E gains a defined local input; the currently-discarded CT-02 map stops being wasted.
- **Negative:** two surfaces to look at; the CI-reviewer persona is still not served (deliberately — serving it means publishing sensitive values, and that is a decision, not a convenience); a new sensitive artifact on local disk needs gitignore, retention and a leak test.
- **Follow-up (not scheduled here, not a DoD edit):** a `/plan` story for the artifact writer; ADR-0006's step-10 "honest limits" gains a pointer to the artifact; the deferred **confidence-score** amendment (ADR-0006) and **Option D** are the same class of question and should be put to the user together, once, rather than one at a time.
