# Sponsor demo — runbook

Five minutes, one command, real infrastructure.

```bash
docker compose -f ../langfuse/docker-compose.yml up -d     # if not already running
set -a && . ./.env && set +a
uv run python demo/sponsor_demo.py
```

Exit code **1** is the success case — it means the candidate prompt failed the gate,
which is the product working. Open `$LANGFUSE_HOST` afterwards and show the dataset
and its scores in the UI.

---

## The five beats

| # | On screen | The point |
|---|---|---|
| 1 | Golden set seeded into Langfuse | The known-good answers live on the platform, not in a spreadsheet |
| 2 | A bad curator edit rejected, **HTTP 400** | `total = "twelve fifty"` is refused by the *server*. The golden set cannot silently rot |
| 3 | Baseline **PASS**, candidate **FAIL** | The classifier judges field by field. One decimal point lost in `total` fails the build |
| 4 | Scores written to the platform | Every run is a durable, inspectable record |
| 5 | Exit code 1 | The whole product reduces to this: CI goes red on a prompt regression |

## Two details worth pointing out live

**Number canonicalisation.** `1,250.00` vs `1250.00` → **match**. Formatting differences
are not regressions, so the gate doesn't cry wolf.

**`wrong_format` ≠ `wrong_value`.** `14/03/2026` against an ISO golden is flagged
`wrong_format` — and per business rule BR3 that does **not** fail the gate, even on a
critical field. Only a *missing* or *wrong_value* critical field does. That distinction
is what separates a gate teams trust from one they learn to bypass.

## Say this out loud — do not let the demo imply otherwise

- **The extraction is synthetic.** This has never run against a live MuleSoft Anypoint
  IDP org, because no published action id + version exists yet. Story **S-01.6** is
  blocked on precisely that, and **S-01.4** (the orchestrator) is blocked on S-01.6.
- **There is no CLI.** This script does by hand what `run_eval` will do. The components
  are real and tested; the wiring between them is the remaining MVP work.

## What is genuinely proven

- **568 tests pass**, including 12 live integration tests against a real self-hosted
  Langfuse 4.38.0 — not mocks.
- **No expected or extracted value ever reaches the platform.** Verified by reading the
  scores back off the live API: only `document_id`, score names and verdicts. Documents
  themselves never leave the app at all.
- **SPIKE-01 killed a wrong assumption early** (ADR-0005): the plan assumed Langfuse gave
  non-engineer curators a no-code form for golden sets. It is a raw JSON editor. That was
  found before anything was built on it, and the Curator workflow was redesigned instead
  of discovered late.

## The ask

One input unblocks the critical path: **a published IDP action id + version**.
Everything after it — S-01.6, then the orchestrator, then a hardening pass — is
scoped and estimated.
