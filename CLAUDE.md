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

## Commands

Everything runs from the project venv; dependencies are uv-managed (`uv add`, `uv lock`) with `uv.lock` committed.

| Task | Command |
|---|---|
| Tests | `.venv/bin/python -m pytest -q` |
| One test | `.venv/bin/python -m pytest tests/platform/test_scoring.py::test_name` (or `-k <expr>`) |
| Live/integration | `set -a; source .env; set +a; RUN_INTEGRATION_TESTS=1 .venv/bin/python -m pytest -q` |
| Types + lint | `.venv/bin/mypy src tests` (strict) · `.venv/bin/ruff check src tests` |
| Dependency CVEs | `.venv/bin/pip-audit` |
| Secret-scan hook | `./scripts/install-git-hooks.sh` — once per clone; sets `core.hooksPath=.githooks` (not versionable) |
| CLI | `.venv/bin/python -m idp_regression.orchestration.cli --org <id> --action <id> --version <v> --dataset <name> --run <name> [--max-documents-per-run <n>]` |
| CLI (local convenience wrapper) | `./scripts/run_eval_local.sh` — composes `--org`/`--action`/`--version`/`--dataset` from `IDP_ORG_ID` / `IDP_ACTION_ID` / `IDP_TEST_ACTION_VERSION` / `GOLDEN_DATASET_NAME` in `.env`; generates `--run` from a timestamp. Nothing under `src/` reads these vars (ADR-0004 A8/A9, statically pinned) — the wrapper only composes a command line. |

- **`--org` / `--action` / `--version` / `--dataset` are all required, no environment fallback (ADR-0004 A8/A9, 2026-09-22)** — every value that defines *what a run measured* must be visible in the invocation itself (and, for CI, in the reviewed workflow file), never resolved from ambient `.env`. `--max-documents-per-run` is the one optional flag (MVP override of A10; defaults to a guard-rail ceiling, not a real IDP quota).
- **Integration tests are opt-in** (`RUN_INTEGRATION_TESTS=1`) and need the local Langfuse at `LANGFUSE_HOST` plus IDP credentials from `.env`. Live IDP *submit/poll* stays skipped until a real action id + published version exist (S-01.6); **do not submit documents to the live IDP casually** — it costs org quota and processes real files.
- **After mutation testing, run `PYTHONDONTWRITEBYTECODE=1` and delete `__pycache__`.** A stale `.pyc` from a same-second revert once made three passing tests fail, and the source looked correct.
- `pytest-timeout` is set to 120 s so a regression in poll/retry budget math cannot hang the suite.
- **Headless regression run (Epic D step 1, ADR-0006 Decision B)** — `.github/workflows/regression-run.yml` invokes the same one-shot CLI from an external scheduler (`workflow_dispatch` + `schedule`), never a self-triggering daemon. See the workflow file's header comment for the trigger/secrets/gate reasoning.

## Architecture

Four packages under `src/idp_regression/`, one seam each. The dependency direction is adapter → classifier → platform, with orchestration on top; nothing flows back.

- **`adapter/`** — the IDP boundary. OAuth token cache, urllib transport (no-redirect opener, so a 3xx can never re-send the Bearer token), submit + poll loop against a configurable terminal-status allowlist, and `normalize()` mapping IDP's volatile `pages[]` into the stable `NormalizedOutput`. Every failure is a typed error from `errors.py`; **no raw exception may escape `normalize()` or `extract()`**, and no extracted value, token or path appears in a message, log or `__cause__` chain.
- **`classifier/`** — pure. `classify()` + `overall_gate()` over the six verdicts. No I/O and no adapter/platform imports (INV-02, enforced by a test). This suite is the CI gate.
- **`platform/`** — Langfuse. The `PlatformAdapter` Protocol is `get_dataset` / `record_run` / `mark_run_status`. **Record-after (ADR-0005 #9):** the orchestrator computes every gate first and writes nothing during its loop; then one `record_run` replays the stored results through the SDK's `run_experiment` and attaches scores to the trace ids it returns. The Langfuse SDK import is confined to `make_platform()` (NFR N24).
- **`orchestration/`** — the `run_eval` facade and CLI (S-01.4, in progress). `load_dotenv()` runs before any SDK client is constructed (INV-05). The action id and version are **per-run CLI parameters, never env config**; `--version` is required with no fallback.

Where the "why" lives: `docs/adr/` (0002 adapter · 0003 classifier · 0004 orchestration and failure containment · 0005 platform), `docs/design/{CONTRACTS,INVARIANTS,DATA-MODEL-01}.md`, per-story stamps and audits in `docs/qa/`, and running state in `docs/state/` (`PROGRESS.md` is the source of truth for what happened and why; also `HANDOFFS.md`, `DEBT.md`, `REGRESSIONS.md`).

## Langfuse facts that bite

Established live against 4.38.0 OSS in `events_only` mode; re-verify before a version bump.

- Every score needs **exactly one** target (`traceId` / `sessionId` / `datasetRunId`) plus an explicit `dataType`, or the write is a 400. Scores are idempotent on a client-supplied `uuid5` id.
- A run appears in the UI **only** when created via the SDK's `run_experiment`. REST `dataset-run-items` alone stores the link but surfaces nothing.
- `GET /v2/scores` is gone — use `/v3/scores`, filtered by `id`. Public reads lag, so poll with a bound and never read-after-write.
- `GET /api/public/v2/datasets/{name}` does **not** return items; fetch them from the paginated `/api/public/dataset-items`. Getting this wrong yields a silently empty golden set that passes vacuously.
- The committed golden schema (`src/idp_regression/platform/schema/golden_schema_v1.json`) must satisfy Ajv `strict: true`: every `if`/`then` declares a `type`, every `required` key is in `properties`, and the minified schema stays under 10,000 characters (CT-05).
