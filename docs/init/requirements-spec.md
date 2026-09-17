# Requirements specification

Allocates responsibility across the three layers, defines the data contracts between them, and
enumerates what the custom app must implement. This is the reference for `/discover` (use
cases) and `/design` (architecture, NFRs).

## Layers and responsibility boundary

```
Engineer / CI ── trigger run ──▶ Custom app ── submit doc + version ──▶ IDP
                                     │  ◀── extraction JSON ────────────┘
                                     │  classify actual vs expected
                                     ├── push scores + verdicts ──▶ Platform (Langfuse|Opik)
Curator ── edit goldens in UI ─────────────────────────────────▶ Platform
                                     │  ◀── read goldens ─────────────── Platform
                                     └── read scores ──▶ Remediation UI
```

- **IDP** — runs the extraction action at a given version; returns fields, confidences, line
  items. Source of the *actual* output. No comparison, no golden storage.
- **Evaluation platform** — stores golden sets (dataset items), records runs, persists per-field
  scores, gives the Curator a UI. Source of the *expected* output and the score store.
- **Custom app** — orchestrates a run, calls IDP, normalizes, classifies actual-vs-expected,
  computes the gate, writes scores back, drives remediation. The only layer that knows what
  "better or worse" means.

## Layer 1 — IDP contract (what the adapter consumes)

Three calls:

1. **Auth** — OAuth2 client credentials. `POST https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token`
   with `grant_type=client_credentials`, `client_id`, `client_secret` → `access_token`.
2. **Submit** — `POST {base}/organizations/{org}/actions/{action}/versions/{version}/executions`
   with the document as a file upload → execution `id`. Base host is region-specific:
   `https://idp-rt.{region}.anypoint.mulesoft.com/api/v1`.
3. **Poll** — `GET {base}/.../executions/{id}` until status is terminal → extraction body.

Config (env/secrets, never in code): `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `IDP_REGION`,
`IDP_ORG_ID`, `IDP_ACTION_ID`, `IDP_ACTION_VERSION_BASE`, `IDP_ACTION_VERSION_CAND`.

**Version is the regression variable.** Baseline = last known-good version; candidate = the
change under test. Every run takes the version as a parameter; never hard-code it.

**Confirm against a live org** (org-specific, not a design gap): terminal status strings, and
the shape/location of line items/tables in the response. The adapter's `normalize()` absorbs
both.

## Layer 2 — Evaluation platform contract

Owns: **datasets** (item = one document input + its expected golden), **runs/experiments**,
**scores** (per-field verdicts + gate), **curator UI**. Identical across Langfuse and Opik for
our purposes except two points (form-mode editing → Langfuse; immutable versioning → Opik) —
see `discovery-notes.md`. Enterprise auth is paid on both. Access it only through SDK/REST,
behind one swappable module.

## Layer 3 — Custom app components

1. **Adapter (IDP client)** — `extract(file_path, version) -> normalized_output`. Owns token
   handling, polling with timeout, and `normalize()`. Only component that knows IDP's HTTP shape.
2. **Classifier** — `classify(golden, actual) -> verdicts`, then `overall_gate(verdicts) -> PASS|FAIL`.
3. **Gate** — FAIL only on a critical `missing`/`wrong_value` (fields or line-item rows).
4. **Run orchestration** — `push_dataset` and `run_eval --version --run`, behind the platform
   adapter. `load_dotenv()` at the top of every script before any SDK client.
5. **Remediation UI** — reads a run's verdicts via the platform API; guided expected-vs-actual view.
6. **Custom signals** — document-type → golden routing; structural-novelty detection.

## Data contracts

**Golden (expected output)** — the durable, curated asset:

```json
{
  "fields": {
    "invoice_number": { "value": "INV-1", "type": "id", "critical": true },
    "invoice_date":   { "value": "2024-03-15", "type": "date", "critical": true },
    "total":          { "value": "1250.00", "type": "number", "critical": true }
  },
  "line_items": {
    "match_key": "description",
    "critical": true,
    "rows": [ { "description": "Widget A", "qty": "10", "unit_price": "50.00" } ]
  }
}
```

**Normalized IDP output (actual)** — what `normalize()` produces from any raw execution:

```json
{
  "status": "SUCCEEDED",
  "fields": {
    "invoice_number": { "value": "INV-1", "confidence": 0.99 },
    "invoice_date":   { "value": "March 15, 2024", "confidence": 0.90 },
    "total":          { "value": "$1,250.00", "confidence": 0.80 }
  },
  "line_items": [ { "description": "Widget A", "qty": "10", "unit_price": "50.00" } ]
}
```

**Verdicts (classifier output)** — one entry per field, plus a `line_items` detail block:

```json
{
  "invoice_number": { "verdict": "match", "expected": "INV-1", "actual": "INV-1", "confidence": 0.99, "critical": true },
  "total":          { "verdict": "wrong_value", "expected": "1250.00", "actual": "1150.00", "confidence": 0.80, "critical": true },
  "discount":       { "verdict": "new_field", "expected": null, "actual": "5.00", "confidence": 0.70, "critical": false },
  "line_items":     { "verdict": "detail", "critical": true, "rows": [ { "line": "Widget A", "field": "unit_price", "verdict": "match" } ] }
}
```

**Platform mapping.** Each field verdict → a score `field:<name>` (value = verdict; comment =
expected/actual/confidence). The gate → a score `gate` (value = `PASS`/`FAIL`). Dataset item:
`input` references the document, `expected_output` is the golden. Score names are the contract
the remediation UI reads — keep them stable.

## The six verdicts

| Verdict | Meaning | Gate impact |
|---|---|---|
| `match` | type-aware canonical forms equal | pass |
| `missing` | expected field absent/empty in actual | FAIL if field critical |
| `wrong_value` | genuinely different content | FAIL if field critical |
| `wrong_format` | same content, different formatting only (`INV 001` vs `INV-001`) | pass |
| `new_field` | key in actual not in golden | pass (informational) |
| `new_line` | line-item row in actual not in golden | pass (informational) |

## Functional requirements

Priority: **H** = core (weak here near-disqualifying), **M** = matters, **L** = nice to have.

| # | Capability | Requirement | Pri |
|---|---|---|---|
| F1 | Adapter | OAuth client-credentials auth; cache token for a run | H |
| F2 | Adapter | Submit doc at a specified version; poll to terminal status with timeout | H |
| F3 | Adapter | Normalize any IDP response to the normalized-output contract | H |
| F4 | Classifier | Emit the six verdicts with type-aware, per-type normalization | H |
| F5 | Classifier | Distinguish `wrong_format` from `wrong_value` (looser alphanumeric tier) | H |
| F6 | Classifier | Match line items by `match_key`; emit `new_line` for unmatched output rows | H |
| F7 | Classifier | Treat `new_field`/`new_line` as informational (never auto-fail) | H |
| F8 | Gate | FAIL only on a critical `missing`/`wrong_value` (fields or rows) | H |
| F9 | Orchestration | `push_dataset`: load goldens into a platform dataset | H |
| F10 | Orchestration | `run_eval --version --run`: extract, classify, write scores + gate | H |
| F11 | Orchestration | Support golden-set AND baseline-diff modes | H |
| F12 | Config | Credentials/versions from env/secrets; `load_dotenv()` before any SDK client | H |
| F13 | Routing | Detect document type; route to that type's golden set | M |
| F14 | Structural novelty | Flag runs whose output shape departs from the golden | M |
| F15 | Remediation UI | Read run verdicts via API; show expected/actual, confidence, correction path | M |
| F16 | Reproducibility | Record action version + golden version per run | M |
| F17 | Platform adapter | Isolate all platform SDK calls behind one module (Langfuse/Opik swappable) | M |
| F18 | CI | Expose the gate as a CI-blocking exit code | L |

H rows = MVP scope. F13–F17 make it a product. F18 is the automation payoff once the gate is trusted.

## Non-functional requirements

- **Reproducibility:** pinned dependency lock; run records version + golden version.
- **Golden versioning:** a run's result is meaningful only against a known golden version
  (automatic on Opik; app-tracked on Langfuse).
- **Security:** no secrets in code or repo; golden contents treated as sensitive; document
  files never leave the app for the platform.
- **Testability:** the classifier is pure and unit-tested independent of IDP and platform.
- **Swappability:** platform SDK usage confined to one module.
- **Deployment:** platform server in Docker (self-hosted); custom app native (no Docker).
