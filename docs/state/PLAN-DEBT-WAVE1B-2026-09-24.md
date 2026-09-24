# Wave 1 completion plan — Lanes B, D, design, and the remainder (2026-09-24)

Lanes A, C and E are done (14 rows closed, 3 correctly refused). **Lane B never ran** — it was
held behind the S-01.4 re-stamp and then displaced by the fail-open fix rounds. **Lane D's debt
rows never ran either**: its tree was taken over by the F-5/F-2/F-7 fixes, which were a different
piece of work that happened to live in the same files.

## What is actually left

| Group | Rows | Owner |
|---|---|---|
| **B — adapter** | 21 (leg 2), 22 (leg 2), 25, 49, 65, 67, 71, 78, 79 + stamp findings **G-1**, **G-2** | one lane |
| **D — orchestration** | 52, 54, 57, 59, 64 | one lane |
| **Design-blocked** | DEBT-04, DEBT-05 (classifier, need a schema/ADR decision), DEBT-14 (platform, needs CT-03/DATA-MODEL-01) | Soneca, docs only |
| **Deferred by Lane E** | SLF001 (47 sites), N818 (5 sites) | after B and D land |
| **Not closable by code** | DEBT-69's remaining half | needs fresh live captures |
| **Wave 2** | S-01.4 re-stamp (failed twice; fixes landed), re-review of the fix rounds | serial, DEBT-44 |

## Parallelism — three lanes now, two after

```
B — adapter         ─┐
D — orchestration    ├─ disjoint trees, run together
Soneca — design      ─┘  (docs only; unblocks 04/05/14 for a later pass)
                     │
                     └─► SLF001 sweep (touches tests/adapter + tests/platform —
                         MUST wait for B, and for Lane C's files to settle)
                     └─► S-01.4 re-stamp (fresh instance, DEBT-44)
```

**Why SLF001 cannot go now:** its 47 sites live mostly in `tests/adapter/` and `tests/platform/`.
Lane E deliberately deferred it rather than sweep files other lanes were editing — running it
beside Lane B would repeat exactly the collision it avoided.

## Two findings from the S-01.2 stamp that belong to Lane B and were never carded

- **G-1 — the live capture is not load-bearing on the confidence scale.** Rewriting
  `confidenceScore`/0–100 back to `confidence`/0–1 in the captured fixture leaves the **whole suite
  green**. Half of REG-11 (the D2 defect) has no fixture-shape pin, so a "tidy the fixture" pass can
  delete the project's only evidence of the real wire key without one test noticing. **SR-1
  violation in substance.** One-line fix beside the existing envelope assertion.
- **G-2 — `classify_probe_response`'s EXISTS branch is unbound.** A 400 naming a *different*
  action/version returns `exists`; the ABSENT branch is correctly bound. Live consequence: a
  wrong-org 400 reads as "this version exists", so the detector can report a new version after a
  silent credential or org swap. Either bind it, or pin "EXISTS is deliberately unbound" so the
  asymmetry reads as a decision rather than an oversight.

## What today's findings changed about DEBT-69

IDP retains a successful result for **24 hours** (Addendum 4). A raw execution is therefore **not
re-fetchable**, and `tests/fixtures/live/seed-001-clean.raw.json` is the only copy of that
response. Closing DEBT-69's remaining shapes — the `prompts` shape and multi-page behaviour —
requires **fresh extractions against live IDP**, not re-reading old executions. No amount of code
work closes it, and the extraction budget is spent.

## Ground rules (unchanged, and they have earned their place)

1. **Fix the invariant, not the reported case.** Five fail-opens this week, and each fix created
   the next because it closed the case in the repro rather than the property that was violated.
2. **A test must constrain the invariant, not the setup.** Seven vacuous tests found so far.
   Before adding one, know which mutation it kills; verify that mutation.
3. Mutation-verify every pin: hand-revert, confirm RED, restore byte-identical (`shasum`), never
   `git checkout --`.
4. **Commit your own work.** Three lanes have finished leaving work uncommitted.
5. `git status` before each commit; stage explicit paths; never `git add -A`.
6. **Report, don't absorb.** A row that is already closed, or bigger than its line suggests, comes
   back as a finding.
