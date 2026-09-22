# NFR-02 — Non-functional requirements checklist — unattended version watch

> ⚠️ **DORMANT — 2026-09-22. Do not work this checklist.** The use case it is written against is withdrawn: `docs/spikes/PROBE-2026-09-22-idp-version-listing.md` established that **no supported API lists an action's versions**, so ADR-0006 **Decision A is withdrawn** and Decision B deferred with it. There is no UC-02 and no SPEC-02 to card. Kept, not deleted, for two reasons: it is the ready-made checklist if the user ever re-opens detection via the floating-`latest` probe (ADR-0006 §Decision A — Withdrawal, option 1), and an absent file would make a deliberate decision look like an oversight.
>
> **Rows that did NOT lapse — they re-homed to `NFR-01` as N29–N31 (ADR-0004 A9/A10):** M3 and D4 (quota metering, pre-flight enforcement) and the no-default half of C2. They now bind the **one-shot CLI**, which is the caller that actually exists. What did **not** survive: the per-**day** ledger, because it needed the tier-2 state store that dies with Decision A — today's ceiling is per-run only, and that limit is stated in ADR-0004 A10 rather than implied.
>
> Everything below is preserved as written on 2026-09-22 and none of it is a plan of record.

**Use case:** UC-02 (unattended version watch) — **owed by Feliz**; this checklist is written against ADR-0006 and is provisional until UC-02 exists.
**ADR:** ADR-0006 (version-change detection & unattended run triggering)
**Rigor profile:** `prototype` (mechanical floor always applies; every gate the profile turns off is **recorded**, never silently dropped)
**Date:** 2026-09-22
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
