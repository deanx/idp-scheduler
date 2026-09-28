# Review rules — this repo

**Adopted 2026-09-27 by user decision** ("option 2"): the review-method rules the register
has carried as DEBT-40, DEBT-43 and DEBT-44 are written down **here**, where every `/test` gate
and every code review in this repo can read them. The shared `lemon-studio-sdd:code-review-protocol`
skill still owes the same amendment. That half lives outside this repo, and those three rows stay open
for it alone.

A gate or review that breaks a rule below is **not valid**, whatever its verdict. Say which
rule it broke in the stamp, then re-run it.

---

## R1 — Build mutants from the invariant, not from the diff (DEBT-40)

When a review checks a guard, validator or gate, list what it must guarantee from the **declared
type or contract**. That means `TypedDict.__required_keys__`, dataclass fields, the JSON schema,
the verdict vocabulary, or the INV-/CT- rule it serves. Then mutate **every place that
guarantee can be broken**, including callers above the guard and code the commit did not touch.

**Why:** in the QA-01 re-audit, all 12 of one review's mutants came from the changed lines. The
unguarded subscript at `langfuse_adapter.py:409` sat above the guard, on an unchanged line, so it
was never in the mutant set. The same defect family then escaped three times. As that review put it:
*"reviewing a guard by mutating the guard is circular."*

**Check:** the stamp's mutation matrix says where each mutant came from (which invariant or
which declared field), not only which line it changed. One or more mutants must sit on a line
the diff did not change.

## R2 — Derive both obligations: presence and value type (DEBT-43)

For every field a guard protects, derive two checks mechanically and parametrize both:
- **Presence**, from `__required_keys__`.
- **Value type**, from `typing.get_type_hints(T, include_extras=True)`.

If presence is type-driven but value type is still written by hand, the guard is really
hand-written with one automated column. It is not a type-driven guard.

**Why:** FU-01.3-D automated only the presence pins. The next escape, a non-string `document_id`
written to the platform while the call reported success, landed on the value-type axis that
was still written by hand, within a day.

## R3 — A gate runs on a fresh instance, on a different model from the implementer (DEBT-44)

A `/test` gate on a `Risk: high` delta (SPEC-01 and everything under it) must meet all three
conditions:
1. It runs on a **new agent instance**, never the one that issued the diff's code-review
   APPROVE, and never the one that FAILED the previous gate on the same story.
2. It runs on a **different model** from the implementer's.
3. The stamp's `**Independence:**` line names the instance, its model, and whether it issued
   any earlier verdict on the diff. If it did, the stamp is void.

**Why:** FU-01.3-D's gate was run by the instance that had just approved the diff. It missed a
fail-open write that R1 would have found in one lookup. Re-reading a diff you approved confirms
a conclusion. It does not review it again.

---

## Working practices this project also binds on

Each one is here because breaking it cost this repo a wrong result at least once.

- **P1 — Run gates one at a time.** A gate's mutants are live in the working tree while it runs.
  Any other gate, review or test run started at the same time measures the mutant, not the code. (On
  2026-09-27, a review running beside a gate reported spurious failures.) One gate at a time, and no
  commits while a gate runs, because each stamp pins HEAD.
- **P2 — Undo an experiment from a copy, never with `git checkout -- <file>`.** Copy the file to
  the scratchpad before mutating it, restore it with `cp`, and check with `shasum` that it is
  byte-identical. `git checkout --` also throws away every other uncommitted change in the file.
  (In Wave B it deleted four uncommitted gate tests, and the "clean" runs that followed were
  clean only because those tests were gone.) When a result improves right after a revert,
  compare the test count before trusting it.
- **P3 — Exercise the real SDK, never a synthetic record of it.** When code depends on how a third
  party behaves (log wording, a logger name, a duck-typing check, a result attribute), the test
  drives the **installed** package, e.g. a real `BatchSpanProcessor` and a real `run_experiment`
  signature. A fake written from our own reading of the SDK agrees with our code by construction.
  (DEBT-27(a) shipped once watching the wrong logger, with tests that fed it wording nobody
  emits. See also `tests/platform/test_sdk_contract.py`.) Wire contracts are held to
  **SR-1** (`docs/state/REGRESSIONS.md`): check against a recorded live response, never
  against a fixture written from the parser.
- **P4 — Mutation hygiene.** Use `PYTHONDONTWRITEBYTECODE=1` during mutation runs, and delete stray
  `__pycache__` afterwards. A stale `.pyc` once made correct source fail three tests. In zsh, pass
  paths literally, because an unquoted `$VAR` holding several paths is **not** split into words.
- **P5 — Freshness is a file list pinned to a commit (DEBT-77).** A stamp's freshness check names
  the exact files it covers, e.g. `git diff --stat <pin>..HEAD -- <file> <file> …`. A directory
  glob is not enough: a file added later drops out of it without anyone noticing.
- **P6 — A FAILED stamp is committed before its fix.** The record of what the gate found must be in
  history before the code that answers it, so the fix can be checked against the finding rather
  than rewriting it.
- **P7 — Write the test before the fix and see it fail.** Every fix lands with the test that kills its
  mutant, and that mutant is actually run. A test that has never been seen red proves nothing.
