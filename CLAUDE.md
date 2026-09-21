# IDP Regression Testing

> Pre-filled for the Lemon Studio SDD squad. `/scaffold-it` in **adapt mode** will add its
> own sections (Squad agents, External services, Board columns table, etc.) and preserve the
> domain-specific sections below. The grep-contract headers the squad depends on are present:
> `## Language`, `## Stack`, `## Tooling`, `## Rigor`, `## Domain`, `## Issue tracker`.

## Language
English

## Stack
- **Core app:** Python 3.13 — a library + CLI (orchestration, adapter, classifier). No web framework required for the core.
- **Remediation UI (later epic):** optional lightweight web UI on the evaluation platform's API; defer the framework choice until Epic E.
- **Runtime:** local (Apple Silicon / M1) and CI. **Docker is NOT used for the custom app** — only for the self-hosted evaluation-platform server (Langfuse/Opik), which is a separate concern.

## Tooling
- **Language server:** pyright — status: available (pyright-lsp MCP connected).
- **Static analysis:** mypy + ruff.
- **Security scan:** pip-audit + gitleaks.
- **Tests:** pytest (the classifier suite is the CI gate).
- **CI:** GitHub Actions — the regression gate exposed as a blocking exit code.
- **Dependency pinning:** install, then `pip freeze` to a committed lock file; install from the lock thereafter.

## Rigor
Profile: prototype

> **Changed 2026-09-21 by user decision: `standard` → `prototype`, to reach the MVP faster.**
>
> Previous rationale, preserved because it weighed this exact argument and reached the opposite
> conclusion: *"this is a real, long-lived internal product, but the first milestone is an MVP.
> Start `standard`; run individual gates at `--rigor=full` before the tool is trusted to gate
> other teams' prompt changes."* The reversal is deliberate and is recorded, not silently applied.
>
> **What does NOT change — these are not profile-driven and still bind:**
> - **The mechanical floor holds in every profile:** tests exist and pass, static analysis clean,
>   secret scan. The classifier is pure and must always stay unit-tested.
> - **`Risk level: high` still requires a `/test` stamp.** `/qa`'s rigor gate reads the risk level
>   from the **SPEC header**, not from this profile, so SPEC-01 cannot be QA'd on an
>   `/implement`-only stamp regardless of what this line says. **Profile ≠ risk level.**
> - **NFR-01's markers are per-UC, not per-profile:** `Containment: REQUIRED` (HARDEN-01, five
>   conditions), `Observability: REQUIRED`. Lowering the profile does not waive them.
> - **DEBT-44** (a `/test` gate on a Risk:high delta may not be run by the APPROVE-issuing
>   reviewer instance) is a written project rule, not a profile setting.
>
> **What this buys, and what it costs.** Fewer reviewer passes and lighter gates per story, so
> stories close faster. The cost is a lighter *Done*, honestly labelled — and every gate this
> profile turns off must be **recorded** (`skipped: prototype profile`) in the QA report and the
> state log, never silently dropped.
>
> ⚠️ **The specific risk, stated once so it is on the record.** This product's entire value is
> being a CI gate other teams trust. On 2026-09-21 alone, the gates being lightened found two
> **fail-open** defects — `overall_gate` returning `"PASS"` for any unrecognised verdict, and a
> non-string `document_id` being written to the platform while the call returned success. Both
> would have produced **silently-wrong GREEN builds**, which is the worst failure this system can
> produce and the one a lighter profile is least likely to catch. Neither was found by a test
> suite; both were found by mutation testing and independent review.
>
> **Re-raise to `standard` before this tool gates another team's prompt changes**, and run the
> individual gates at `--rigor=full` before that point. Until then, treat green as "the MVP's own
> tests pass", not as "this gate can be trusted".

## Domain
- **Product:** IDP Regression Tester — a system that validates MuleSoft Anypoint IDP extraction output against a known-good reference, so a changed extraction prompt (or action version) can be judged better or worse with confidence.
- **Domain/URL:** internal tool — n/a
- **Personas:** Prompt Engineer (iterates extraction prompts and runs regressions), Golden Set Curator (non-engineer; maintains expected outputs through the platform UI), CI Pipeline (automated gate on prompt-change PRs).
- **Epics:** A IDP adapter · B Classifier & gate · C Golden-set management · D Run orchestration & platform integration · E Remediation UI · F Document-type routing & structural novelty
- **Compliance regime:** none formal — but extracted fields (invoices, IDs, totals) can contain financial data / PII. **Design rule: document files never enter the evaluation platform; only `document_id` and expected fields are stored.** The golden set lives only in its dataset items. No extracted (actual), expected or confidence **value** is written anywhere else on the platform: score comments and trace spans carry only `document_id` and verdicts (user decision 2026-09-19, DEBT-18 option B). Treat golden-set contents as sensitive.
- **Sensitive surfaces:** golden-set storage (expected values), IDP credentials (OAuth client secret), evaluation-platform API keys.
- **Canonical source docs:** glob `docs/init/` (discover by role, not by filename).

## Issue tracker
- Board: none (boardless — state tracked in docs/state/ only)

> Switch to a Trello mirror later with `/board-adopt` if you want one; the pipeline runs fully boardless.

## External services
| Service | Purpose | Credential | Status |
|---|---|---|---|
| MuleSoft Anypoint IDP | Source of extraction output under test (Epic A adapter) | OAuth client secret | configured locally — IDP_CLIENT_ID + IDP_CLIENT_SECRET in gitignored `.env` (never in repo); S-01.6 spike still needed to pin timeouts/status-allowlists against a live org |
| Evaluation platform — Langfuse (self-hosted; confirmed by ADR-0005 after SPIKE-01) | Golden-set storage, run tracking, regression gate (Epic D) | API key (LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY) + LANGFUSE_HOST | self-hosted instance running locally; API keys + host in gitignored `.env`; self-hosting obligations (encryption-at-rest, DB access control, backup per ADR-0001/N25) still required before production golden data; **compose file: `../langfuse/docker-compose.yml`** (sibling checkout, outside this repo) — all 6 images pinned to exact tag + manifest digest on 2026-09-19, web/worker at `4.38.0` (S-01.5 QA F-1 / S-01.3 F-6 **closed**); re-probe **CT-05 and the three `make_platform` tests that assert langfuse SDK privates** (`_base_url`, `api._client_wrapper.get_headers()` — `test_make_platform_dispatches_on_platform_env`, `test_make_platform_raises_when_langfuse_base_url_disagrees_with_langfuse_host`, `test_make_platform_constructs_the_sdk_client_with_base_url_equal_to_host`, all in `tests/platform/test_langfuse_adapter.py`) **before any version bump** — these are pinned to SDK internals by design (DoD (b) of FU-01.3-B; the M9 call-contract pin of FU-01.3-D) and are the first thing a version bump breaks silently. **Named, not line-numbered, deliberately** (DEBT-42 process amendment); platform decision final per ADR-0005 |

> Solo mode: no owner assignment per credential. Escalate a missing credential to Mestre when it starts blocking a story.

## Squad agents
Solo mode — no per-human role assignment. Each role below is driven by whoever runs the corresponding command.

| Agent | Role |
|---|---|
| Feliz | Product/business analysis — `/brief`, `/discover` |
| Soneca | Solutions architecture, ADRs, NFRs — `/design` |
| Dunga | Planning, board/state tracking — `/plan` |
| Dengoso | Implementation — `/implement` |
| Atchim | Independent code review — correctness/readability/architecture/security/performance |
| Branca | Resilience & prompt-security red-team — `/harden` |
| Zangado | QA / Definition-of-Done audit — `/qa` |
| Mestre | Project setup, credential tracking, blocker escalation |
