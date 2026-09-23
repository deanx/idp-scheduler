# NFR-02 — Non-functional requirements checklist — unattended version watch

> ✅ **RE-ACTIVATED 2026-09-23, re-scoped to ADR-0006 §Decision A′ Phase 1 (detect-and-notify).** The 2026-09-22 dormancy is lifted: a **zero-quota version-existence probe** was found (`docs/spikes/PROBE-2026-09-22-idp-version-listing.md` Addendum 3) and the user decided to poll. **Read Pass D first — it is the checklist for the story that is actually proposed (S-02.1′).**
>
> ⚠️ **Passes A–C below were written against the WITHDRAWN Decision A and most of their rows do NOT apply to Phase 1.** Rows whose subject is **unattended quota spend or the two-tier state store** — **M3, M5, M6, M12, D2, D4, C1's budget half, C2's quota flags** — are **Phase-2 rows**: they return, unchanged, with `--auto-run`. Rows whose subject is the **management-plane listing** — **M7, D1, D3** — are **dead as written**: there is no management plane and no listing response; the version id is now locally *generated*, not externally *received* (ADR-0006 §A′.1), which removes the untrusted-input boundary entirely. M7 survives only as a defence-in-depth assertion at the URL-build site. Rows that carry over **unchanged** to Phase 1: **M1, M2, M4, M8, M9, M10, M11, C3, C4**.
>
> **Rows that re-homed to `NFR-01` as N29–N31 (ADR-0004 A9/A10) and stay there:** M3 and D4's quota metering / pre-flight enforcement, and the no-default half of C2. They bind the **one-shot CLI**, the caller that exists.
>
> **Deferred-row update:** *"Rate limiting of the management plane"* is **re-scoped, still blocked** — there is no management plane, but A′ issues ~11 probes per tick against the **runtime** plane, so the row becomes *"rate-limit behaviour of the runtime plane under scheduled probing"*. Still no target, because nobody has measured it. **Blocked, not waived** — and D5 below is the fail-safe that makes the gap survivable.
>
> **Header honesty, restated:** "all rows addressed" means everything I thought to require is decided. Pass C has **still not** been run by a fresh Atchim instance. **This checklist is one architect's coverage, not two** — a SPEC-02 Definition-of-Ready item, unchanged.

## Pass D — rows forced by Decision A′ (2026-09-23) — **the Phase-1 checklist**

| ID | Category | Requirement (concrete target) | How measured | Scope | Status |
|----|----------|-------------------------------|--------------|-------|--------|
| D5 | **Scalability / cost** | A tick issues **at most `--max-probes-per-tick` + 2 control probes** and **zero** document submissions. A sweep tick is bounded by `--max-probes-per-sweep`. On **429**, remaining probes are abandoned and backed off — never retried within the tick. | Call-count assertion over a mock probe for a walk, a sweep and a 429 tick; a static assertion that the detector module can reach **no** submit call site. | feature | ⬜ PENDING |
| D6 | **Correctness / safety** | An ambiguous probe response is **never** read as `ABSENT`. The parser's default branch is `UNKNOWN`. A walk containing an `UNKNOWN` yields `outcome=indeterminate` and re-probes that candidate next tick. | Table-driven unit test over every row of ADR-0006 §A′.4, **including a 400 with a different detail and a 404 with a different detail**; mutation-verified that flipping the default branch to `ABSENT` turns a test RED. | feature | ⬜ PENDING |
| D7 | **Correctness / safety** | **Truncation is never reported as "nothing new."** A walk stopped by the probe budget or a lookahead ceiling **while still hitting** emits `ceiling_reached`, a distinct outcome from `no_new_versions`, and exits non-zero. | Unit test forcing budget exhaustion mid-hit; assert the outcome value **and** the exit code differ from the no-op tick's. | feature | ⬜ PENDING |
| D8 | **Availability / reliability** (**the discriminator canary**) | Every tick runs a **positive control** (re-probe the anchor, expect `EXISTS`) and a **negative control** (a version guaranteed absent, expect `ABSENT`). A violated control **halts** the tick — `anchor_vanished` / `discriminator_invalid` — and never reports `no_new_versions`. | Unit tests injecting each violation; assert halt + exit non-zero. **This is the runtime protection for ADR-0006 §A′.8 R2 and it is not optional.** | feature | ⬜ PENDING |
| D9 | **Correctness / safety** (**R1**) | An anchor not matching `^\d+\.\d+\.\d+$` causes a **refusal to run** (`refused_unparseable_version_scheme`), not a best-effort grid. | Unit test with `v2-draft`, `2025.09.1`, `1.0`, empty. | feature | ⬜ PENDING |
| D10 | **Observability** (**the false-negative answer**) | Every `check_tick` records the **full probed grid** — `anchor`, `probed[]`, `hits[]`, `unknowns[]`, `ceiling_reached` — so a human can read exactly what was and was not checked. Emitted **unconditionally, including a no-op tick**. | `/qa` observes the line firing on a no-op tick and asserts `probed[]` is the complete candidate list, not a summary count. | feature | ⬜ PENDING |
| D11 | **Observability** (**"confirm quiet"**) | Every tick emits `ticks_since_last_detection` and `days_since_anchor_changed`, and the deployment **alarms when no detection has occurred in N days**. Deliberately noisy: it fires during genuine quiet. | `/signoff` reviews the configured alarm and a fired test alert. **Rationale on the record: a false alarm costs one glance; a false silence costs the product its value.** | system | ⬜ PENDING |
| D12 | **Observability / self-calibration** | A wide sweep that finds a version the per-tick walk missed emits `sweep_found_missed_version` as a **loud** event carrying `anchor_at_miss` and the lookaheads. | Unit test: seed a version outside the walk, inside the sweep; assert the event. **This is the only signal that tells anyone the lookaheads are mis-tuned.** | feature | ⬜ PENDING |
| D13 | **Availability / reliability** | A transient ambiguity **does not page**: a single `indeterminate` tick exits 0. `--max-indeterminate-ticks` consecutive ones exit **non-zero** as `detector_degraded`. | Unit test over the counter incl. reset-on-success; assert the exit-code transition. | feature | ⬜ PENDING |
| D14 | **Security** | The probe **reuses `adapter/`'s token cache and no-redirect opener**; no second HTTP transport exists in the detector. | Static AST assertion: the detector module constructs no opener/session of its own. **A second opener is a second place a 3xx can re-send the Bearer token** (ADR-0002). | feature | ⬜ PENDING |
| D15 | **Maintainability** (**R2 / SR-1**) | **CT-06** — the 400-vs-404 discrimination — is pinned against a **captured live response**, committed, with the unit tests reading *that* capture. A fixture hand-authored from the parser satisfies nothing. | Integration test (`RUN_INTEGRATION_TESTS=1`) for the exists and absent cases, committing the captures; unit tests over the captures. **`REGRESSIONS.md` SR-1 applies by its own terms.** | feature | ⬜ PENDING |
| D16 | **Operability** | The local cache file is **explicitly non-authoritative**: deleting it costs one redundant walk and at most one duplicate notification, never a missed detection or a wrong exit code. | Test: delete the file mid-sequence; assert the next tick still detects correctly from tier 1 + a fresh walk. | feature | ⬜ PENDING |

**Marker lines for Phase 1 (unchanged in force, re-aimed):** `Containment: REQUIRED` — `/harden` should aim at **D6 and D8**, which carry the entire safety argument. `Observability: REQUIRED` — **D10, D11, D12 are the deliverable**, not decoration: in Phase 1 the alert *is* the product. `LLM-Evals: N/A` — unchanged.

---

> *Everything from here down was written on 2026-09-22 against the withdrawn Decision A. Read it through the applicability note above.*
>
> ~~⚠️ **DORMANT — 2026-09-22. Do not work this checklist.**~~ (dormancy lifted 2026-09-23) Original note follows.
>
> ⚠️ **DORMANT — 2026-09-22. Do not work this checklist.** The use case it is written against is withdrawn: `docs/spikes/PROBE-2026-09-22-idp-version-listing.md` established that **no supported API lists an action's versions**, so ADR-0006 **Decision A is withdrawn** and Decision B deferred with it. There is no UC-02 and no SPEC-02 to card. Kept, not deleted, for two reasons: it is the ready-made checklist if the user ever re-opens detection via the floating-`latest` probe (ADR-0006 §Decision A — Withdrawal, option 1), and an absent file would make a deliberate decision look like an oversight.
>
> **Rows that did NOT lapse — they re-homed to `NFR-01` as N29–N31 (ADR-0004 A9/A10):** M3 and D4 (quota metering, pre-flight enforcement) and the no-default half of C2. They now bind the **one-shot CLI**, which is the caller that actually exists. What did **not** survive: the per-**day** ledger, because it needed the tier-2 state store that dies with Decision A — today's ceiling is per-run only, and that limit is stated in ADR-0004 A10 rather than implied.
>
> Everything below is preserved as written on 2026-09-22 and none of it is a plan of record.

**Use case:** UC-02 (unattended version watch) — **owed by Feliz**; this checklist is written against ADR-0006 and is provisional until UC-02 exists.
**ADR:** ADR-0006 (version-change detection & unattended run triggering)
**Rigor profile:** `prototype` (mechanical floor always applies; every gate the profile turns off is **recorded**, never silently dropped)
**Date:** 2026-09-22 · **amended 2026-09-23 (Decision A′, Pass D)**
**Architect:** Soneca

> **Header honesty.** "All rows addressed" means every requirement I thought to require is decided with a target and a way to measure it. It is **not** the claim that every NFR that could matter is listed. Pass C (adversarial absence audit) below was run by me at a second pass; it has **not** been run by a fresh Atchim instance, as NFR-01's was. **Until it is, treat this checklist as one architect's coverage, not two.** That is a Definition-of-Ready item for SPEC-02.

**Marker lines:**
- `Containment: REQUIRED — verified by /harden before Done.` This UC adds a new external boundary (the IDP management plane), a new persisted store, and an **unattended** failure envelope. Not profile-driven; `prototype` does not waive it.
- `Observability: REQUIRED — telemetry per ADR-0006 §B.5, verified firing at /qa.` A background service's characteristic failure is **silence**; observability is the only thing that detects it. Not profile-driven.
- `LLM-Evals: N/A` — unchanged from NFR-01: the app composes no prompts and produces no model output; the comparison is a deterministic classifier.

Scope legend: `feature` = Zangado checks at `/qa`; `system` = deferred to `/signoff`.
Status legend: all rows start `⬜ PENDING`. **I set targets; I do not verify them** — writing the target and judging the result cannot be the same hand.

## Pass A — mandatory coverage core (seven categories; none may be omitted)

| ID | Category | Requirement (concrete target) | How measured | Scope | Status |
|----|----------|-------------------------------|--------------|-------|--------|
| M1 | **Performance** | A tick that finds no new version completes in **< 5 s p95** and issues **exactly one** management-plane call and **at most one** platform read. A no-op tick must be cheap enough that the interval is a free choice. | Timed unit/e2e test against a mock catalog + mock platform; call-count assertion. | feature | ⬜ PENDING |
| M2 | **Performance** | Detection latency ≤ **1 tick interval + 1 run duration**, with the interval **≥ 60 s floor, ≥ 5 min default** (ADR-0006 §B.1). Stated as a bound, not "fast". | Arithmetic from the configured interval; asserted the floor is enforced (a configured interval < 60 s is refused at startup). | feature | ⬜ PENDING |
| M3 | **Scalability / cost** | A tick **never** submits more than `--max-documents-per-day` documents in a UTC day, counted **pre-flight** per run, persisted across ticks and restarts. Breach → exit non-zero `quota_exhausted`. | Unit test: ledger across simulated ticks incl. a restart; assert zero IDP submits once the budget is spent. **This closes the watched-path half of N27 / signoff B-3.** | feature | ⬜ PENDING |
| M4 | **Availability / reliability** | Two ticks can never run concurrently: `flock` non-blocking; a tick that cannot acquire exits **0** with `skipped_locked`. A crashed tick releases the lock with no TTL heuristic. | Test: second process while the first holds the lock; kill -9 the holder and assert the next tick acquires. State file asserted to be on a local FS. | feature | ⬜ PENDING |
| M5 | **Availability / reliability** | Crash between detect and record → **at-least-once with a bounded, detectable duplicate**; a version is **never silently skipped**. An unresolvable `in_flight` claim (tier-1 read fails) **halts**, never guesses. | Test matrix over each crash point in the claim protocol (ADR-0006 §A.5); assert the version is either certified or still in `new` on the next tick. | feature | ⬜ PENDING |
| M6 | **Availability / reliability** | A version that aborts `--max-attempts` (default 3) consecutive times is **quarantined** and never auto-retried. A **gate `FAIL` is a completed run** and is certified, never retried. | Unit test per branch; assert a gate-FAIL version is submitted exactly once. | feature | ⬜ PENDING |
| M7 | **Security** | Every version id from the listing API is validated against `^[A-Za-z0-9._-]{1,64}$` **before** it can reach a URL path; a failing id is refused and alarmed, never skipped quietly. | Test with injection-shaped ids (`../`, absolute, encoded, 65-char, control chars); assert zero outbound execution calls. | feature | ⬜ PENDING |
| M8 | **Security** | No credential, token, `Authorization` header or golden value appears in any watcher log line, the state file, or any exception/`__cause__` chain. | Sentinel sweep through `sanitize_for_log` + a state-file content assertion; extends INV-02 to the new module. | feature | ⬜ PENDING |
| M9 | **Compliance** (`## Domain`: no formal regime; golden contents sensitive, DEBT-18 option B) | The watcher writes **nothing new** to the platform: no new score, span, comment or metadata field beyond what `run_eval` already writes. The state file holds version ids, counters and timestamps **only** — no `document_id`, no golden value. | INV-01-style payload assertion over the watcher path + a state-file schema test enumerating permitted keys. | feature | ⬜ PENDING |
| M10 | **Observability** | Every event class of ADR-0006 §B.5 is **observed firing** (not inferred from code), including a `watch_tick` line on a **no-op** tick — absence-detection depends on it. | `/qa` runs all eight scenarios and records `Observability: ✅ VERIFIED`. | feature | ⬜ PENDING |
| M11 | **Observability** | An alerting sink **consumes** those lines and alarms on (a) no `watch_tick` in N intervals, (b) `outcome ∈ {halted, quota_exhausted, refused_uninitialised}`, (c) any `version_quarantined`. | `/signoff` reviews the configured sink and a fired test alert. ⚠️ **Nothing consumes watcher telemetry today** — this is the exact shape of the already-waived N11, and it is a **DoR item, not a follow-up**. | system | ⬜ PENDING |
| M12 | **Maintainability / operability** | The state file carries `schema_version`; an unrecognised version is a **fail-closed halt**, never a silent reset (a silent reset re-runs the whole version history at full quota cost). Recovery is one documented command. | Unit test with a bumped/absent version; a runbook section in the story's docs. | feature | ⬜ PENDING |

## Pass B — domain-triggered rows (forced by `## Domain`, regardless of what Pass A remembered)

| ID | Trigger | Requirement | How measured | Scope | Status |
|----|---------|-------------|--------------|-------|--------|
| D1 | Sensitive surface: **IDP credentials** | If the management plane requires a different scope/credential than the runtime plane, it is registered in `## External services` (**Mestre**) and sourced from env only — never a flag, never a default. | Config review at `/signoff` + the existing N4 credential-hygiene test extended to the new client. | system | ⬜ PENDING |
| D2 | Sensitive surface: **golden-set storage** | The watcher performs **no golden-set write of any kind** — it reads dataset items for a count and reads certified versions. Read-only against the golden store. | Static assertion: the watcher module issues no platform write other than through `run_eval_detailed`. | feature | ⬜ PENDING |
| D3 | External/public input | The listing response is **untrusted input**: bounded response size, bounded version count (a listing returning > `--max-listed-versions` halts rather than iterating), no field other than the version id retained. | Unit tests with an oversized and a hostile listing body. | feature | ⬜ PENDING |
| D4 | Unattended spend (**new to this UC**) | No quota-bearing action may occur without a **pre-flight** budget check. Mid-flight abort on budget is forbidden — it spends the quota and produces no reference. | Test: a run whose item count exceeds the remainder is never started. | feature | ⬜ PENDING |

## Pass C — adversarial absence check (categories neither pass above had listed)

Run by me as a fresh pass over ADR-0006 hunting for what A and B both missed. Four genuinely absent categories surfaced. *(Caveat repeated from the header: this pass has not been run by an independent instance.)*

| ID | Requirement | How measured | Scope | Status |
|----|-------------|--------------|-------|--------|
| C1 | **Clock / timezone.** The daily quota window resets on a fixed **UTC** boundary. A local-time reset silently doubles the budget on a DST transition. Elapsed timings use a **monotonic** clock (INV-07's reasoning, new module). | Test across a simulated DST boundary; static check that the budget window never consults local time. | feature | ⬜ PENDING |
| C2 | **Configuration divergence.** No watcher flag has a default that could let CI and a laptop silently diverge (the A6 argument). `--action`, `--dataset`, `--state-file`, `--run-prefix`, `--max-runs-per-tick`, `--max-documents-per-day` are **all required, no env fallback** (A8). Only `--version` is discovered — the documented exception. | Static test: no `default=` on those flags; no production read of a watcher env var. | feature | ⬜ PENDING |
| C3 | **Run-naming for the version-over-version view.** The run name carries the version (`{prefix}-{version}`) so the Experiments tab reads as a version series. Without it, step 10's "regression view" is a pile of unrelated experiments. | Test asserting the composed name; a live check that the Experiments tab groups as intended. | system | ⬜ PENDING |
| C4 | **Deployment integrity.** The tick runs from a **pinned** artefact (the committed lock file, per `## Tooling`), and the scheduler config that carries the full invocation is a **versioned, reviewed file**. An unattended service running an unpinned tree is an unreviewable gate. | `/signoff` reviews the scheduler unit/workflow as a committed file; lock-file assertion reused from N17. | system | ⬜ PENDING |

## Deferred, with the reason (so an absent row is never mistaken for a forgotten one)

- **i18n / accessibility** — internal service, no human-facing surface. N/A.
- **Multi-tenancy isolation** — single org, single action per invocation. N/A until Epic F routing.
- **Horizontal scale / concurrency** — explicitly out of scope: one action per tick, sequential runs (inherited from ADR-0004's sequential-only MVP). Watch two actions with two schedules.
- **Rate limiting of the management plane** — cannot set a target before T-01.6.6 reports whether one exists. **Not waived — blocked.** It becomes a row the moment the answer lands.
