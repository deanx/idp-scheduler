# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED
**Source:** /test re-gate (DEBT-46 staleness — `adapter/` moved substantially since 2026-09-22)
**Date:** 2026-09-23 · **Commit gated:** `a5805ec` (`feat/S-01.2-idp-adapter`)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp is required regardless of the `prototype`
profile. **Profile ≠ risk level.**

## DEBT-44 independence
The auditing instance issued **no** APPROVE, REQUEST CHANGES or review verdict on `6755fa1`,
`6489560`, `17a34c4`, `97f3d13`, `71b1bbf`, `a06c01f` or `a5805ec`. Fresh instance, no prior
context on the delta — which is why it ran this gate. No self-gating occurred.

## Delta gated
`367065b..a5805ec -- src/idp_regression/adapter/` — 5 files, +617/−37: `normalize.py` (REG-11 D1
union rule, D2 confidence scale, R-1/R-2 collision precedence), `version_probe.py` (new),
`idp_client.py` (`?valueOnly=false` incl. retry paths, 10 s poll floor), `oauth.py` (new),
`transport.py` (`post_empty_multipart`).

**Freshness check re-baselined against an explicit FILE LIST, not the directory** — closes
DEBT-77, which correctly observed the directory-scoped check fires red for a non-reason the
moment a story adds a module (`version_probe.py` did exactly that). **Scope ruling:**
`version_probe.py` is IN scope — adapter-package code reusing the adapter's transport and token
cache by design (ADR-0006 §A′.1 D14); excluding it would leave a new fail-open-capable module
with no `/test` gate at all.

## Mechanical floor — actual numbers at `a5805ec`, `__pycache__` cleared
| gate | result |
|---|---|
| `pytest -q` | ✅ **1085 passed, 15 skipped**, 28.26 s |
| `mypy src tests` (strict) | ✅ no issues, 94 files |
| `ruff check src tests` | ✅ clean |
| `pip-audit` | ✅ no known vulnerabilities |
| `gitleaks` | **skipped: prototype profile** — not independently attested by this stamp |

## Mutants verified by hand (reverted, run, restored byte-identical, `shasum`-verified)
| # | mutation | observed |
|---|---|---|
| M1 | resurrect the original REG-11 D1 fail-open (`raw.get("pages", [])`, drop the top-level container) | ✅ **40 failed** |
| M2 | swap the two `logical_pages` blocks (invert union collision precedence) | ✅ **exactly 2 failed** — surgical, not incidental |
| M3 | `_coerce_confidence` out-of-range raise → `return None` (the D2 fail-open) | ✅ **6 failed** across both scales |
| M4 | drop the R3 echo binding (any 404 reads ABSENT) | ✅ **3 failed** |

**REG-11's family is terminally pinned.** M1 proves a regression to the original shape is caught
40 ways; M2 proves the precedence the union made newly observable is caught too. The arbitration
is gone, not moved one level deeper again.

## Vacuity sampling — two findings, stamped OVER, not hidden
**G-1 — the live capture is NOT load-bearing on the confidence scale.** The fixture-shape pin
asserts the ENVELOPE (`"pages" not in LIVE_FIXTURE`) and nothing else. Mechanically rewriting
`tests/fixtures/live/seed-001-clean.raw.json` to convert every `"confidenceScore": 99.0` (the real
0–100 wire key) into legacy `"confidence": 0.99` — i.e. back into the hand-authored shape REG-11 D2
was about — left the suite **fully green at 1085**. So half of REG-11 has no fixture-shape pin: a
"tidy up the fixture" pass can delete the project's only evidence of the real confidence key and
scale without one test noticing. **SR-1 violation in substance** — load-bearing for the envelope,
decorative for the scale. Control probe: reshaping into `pages[]` correctly turned exactly one test
RED, so the D1 half works. Fix is one line beside the existing assertion.

**G-2 — `classify_probe_response`'s EXISTS branch is unbound, and its test's parameters are
vacuous.** Verified directly at HEAD:
```
classify_probe_response(400, "Invalid query parameter 'file'",
                        action_id='totally-different-action', version='0.0.0')  -> exists
classify_probe_response(404, "...Id: other and version 9.9.9 not found",
                        action_id='mine', version='1.0.0')                      -> unknown
```
The 400 body carries no echo to bind against, so this is arguably unbindable — but the test's
arguments give false assurance of a binding that does not exist, and nothing pins "EXISTS is
deliberately unbound", so a later reviewer cannot tell the asymmetry is a decision. **Live
consequence:** a wrong-org or wrong-action 400 reads as EXISTS, so the detector can report "a new
version exists" after a silent credential/org swap. The negative-control probe mitigates it;
mitigation is not a pin.

## What this stamp does NOT cover — read before trusting a green build
1. **No prompt-bearing live capture exists.** The whole `prompts` branch — `_merge_prompts`,
   `_SAFE_PROMPT_PATTERN`, `duplicate_prompt`, the R-2 `allow_override` relaxation — is pinned only
   against hand-authored fixtures. DEBT-69(a). ⛔ Do not fabricate a capture to close this.
2. **No multi-page live capture exists.** The `pages[]` path is now the legacy branch and nothing
   real has ever exercised it. DEBT-69(b). The old "compounding confidence" warning is **struck and
   re-verified by mutation at HEAD** — one `_coerce_confidence`, both paths through the same loop —
   so Wave-0's PROVISIONAL flag on that strike is **lifted**.
3. **No live capture of the wrong-org 404.** R3's mismatch→UNKNOWN branch is pinned by synthetic
   input only. SR-1 clause (3).
4. **G-1 and G-2**, above.
5. **The 10 s poll floor is unreachable in production** — `DEFAULT == MIN == 10.0` and no env var,
   so the guard hardened in `6755fa1` is dead code on the only path that matters. DEBT-71/78.
6. `gitleaks` skipped; live IDP submit/poll unexercised until S-01.6.
7. ⚠️ **All four defects this module produced in two days were fail-open, and not one was found by
   the test suite** — every one came from mutation testing or independent review, exactly as
   `CLAUDE.md ## Rigor` warns. Green here means "the MVP's own tests pass", not "this gate can be
   trusted to gate another team's prompt changes."

## Verdict
✅ **PASSED.** Floor clean at the expected numbers; the REG-11 D1/D2 fixes and the R-1/R-2
precedence rules genuinely constrain (four mutants killed with tight blast radii, hand-verified);
the union rule is terminal. G-1 and G-2 are pin/coverage gaps, not defects in shipped behaviour,
and are named rather than absorbed.

## History
- 2026-09-22 — ✅ PASSED (superseded, stale per DEBT-46)
- 2026-09-23 @ `a5805ec` — ✅ PASSED (this stamp)
