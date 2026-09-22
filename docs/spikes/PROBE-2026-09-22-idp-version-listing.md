# Probe — does the IDP API expose an action's version list? (T-01.6.6)

**Date:** 2026-09-22 · **Method:** read-only HTTP against the live org, no document submitted, no extraction quota spent.
**Gates:** ADR-0006 §Preconditions (version-change detection), SPEC-02 / S-02.1 Definition of Ready.

## Question
ADR-0006 Decision A is written for the branch *"a management API lists an action's versions"*. T-01.6.6 asks whether that API exists. Until this is answered, nothing in ADR-0006 Decision A is implementable.

## Result — **the runtime plane has no listing; the management plane exists but is refused to this credential.**

| Probe | Result | Reading |
|---|---|---|
| `OPTIONS idp-rt…/actions/{id}/versions` | **200, `Allow: POST,OPTIONS`** | Definitive. The runtime plane exposes **no GET at all** on this path. A version listing can never come from `idp-rt`. |
| `GET idp-rt…/actions/{id}/versions` | 405 Method Not Allowed | Consistent with the above. |
| `GET idp-rt…/actions/{id}` | 405 | Path routes, method refused. |
| `GET idp-rt…/actions` | 404 `No static resource …` | Path does not exist. |
| `GET anypoint…/idp/api/v1/organizations/{org}/actions` | **403 Forbidden** — both the parent org and the publishing org | Path is recognised (it does not 404 the way an unknown runtime path does), but this credential is refused. |
| `GET anypoint…/idp/api/v1/…/actions/{id}/versions` | **403**, both orgs | Same. |
| `GET anypoint…/idp/v1/…` | 200, HTML | The IDP single-page app, not an API. |
| `GET accounts/api/me` | **200** | Token is valid. Connected app `IS-IDP-Test-Automation`, `client_type: control`, org `e10ae12a…`. |
| `GET accounts/api/connectedApplications/{client_id}` | 401 | Cannot introspect this app's own scopes with this token. |
| `GET exchange/api/v2/assets?organizationId={org}` | **200 `[]`** — both orgs | Reaches Exchange, sees zero assets. |
| `GET exchange/api/v2/assets/{org}/{action_id}` | 404 | The action is not readable as an Exchange asset by this credential. |
| `GET accounts/api/organizations/{org}/hierarchy` | 401 | No org-read permission. |

## Conclusion
The credential in `.env` carries **IDP runtime execution permission and nothing else**. `POST …/executions` succeeds (a real extraction ran today); every *read* surface — IDP management, Exchange assets, org hierarchy, its own scope list — is refused.

**This is a permission grant, not a missing capability, and not an org mismatch** — both the parent org (`e10ae12a…`) and the publishing org (`ef1232be…`) return the identical 403, so switching org id does not help.

⚠️ **Honest limit of the evidence:** a 403 on `/idp/api/v1/...` is *consistent with* the management API existing and being refused, and it contrasts with the clear 404 an unknown runtime path produces — but an API gateway may also answer 403 for paths it does not route. **This probe narrows T-01.6.6 to one branch; it does not close it.** Only a credential with the grant can distinguish "exists and refused" from "does not exist".

## What unblocks it
Grant the connected app read access to the IDP management surface (Anypoint Access Management → the connected app's scopes), and ensure it is entitled in the business group that owns the action (`ef1232be…`). Then re-run this probe — it takes seconds and spends no quota.

## Two facts worth carrying forward regardless
1. **The runtime plane is write-only for actions.** Whatever the answer, version detection cannot be served by `idp-rt`. ADR-0006 A.1's separate `IDPVersionCatalog` Protocol on a different host is the right shape.
2. **`IDP_ORG_ID` in `.env` is the wrong org for this action.** Submit 404s against `e10ae12a…` and succeeds against `ef1232be…`. Unrelated to the 403, and it must be settled before anything runs unattended.

## Scripts
Not committed — throwaway, in the session scratchpad. Reproducible from the table above: client-credentials token from `accounts/api/v2/oauth2/token`, then a bearer GET per row.

---

## Addendum — the documentation answers it (2026-09-22, same day)

Source: [Processing Documents and Retrieving Results With the IDP API](https://docs.mulesoft.com/idp/automate-document-processing-with-the-idp-api) and [Creating Connected Apps](https://docs.mulesoft.com/access-management/creating-connected-apps-dev).

### The scope question
The IDP permission family lives in Access Management under **Document Actions**, granted **per business group**: **Manage Actions**, **Build Actions**, **Execute Published Actions**.

This credential demonstrably holds **Execute Published Actions** — `POST …/executions` succeeded today — and behaves as though it holds none of the others: every read surface returns 403/401. That is consistent with the probe table above and is the likely reason the management-plane calls are refused.

### The version-listing question — **the public IDP API documents no such endpoint**
The documented IDP REST API is **two endpoints only**:

| Method | Path | Scope |
|---|---|---|
| `POST` | `https://idp-rt.{region}.anypoint.mulesoft.com/api/v1/organizations/{orgId}/actions/{actionId}/versions/{actionVersion}/executions` | Execute Published Actions |
| `GET` | `…/executions/{executionId}` | not stated |

**There is no documented endpoint that lists an action's versions, and none that lists actions.** This matches the runtime-plane probe exactly (`Allow: POST,OPTIONS`).

**Consequence for ADR-0006.** `anypoint.mulesoft.com/idp/api/v1/...` — the 403 path — is the IDP web app's own backing API (the sibling route `/idp/v1/...` serves the SPA). It is **undocumented and unsupported**. So even with Manage Actions granted, ADR-0006 Decision A's "listing exists" branch would rest on a **private API with no compatibility guarantee**, for a tool whose entire purpose is to be a CI gate other teams trust.

**This does not decide the matter — Soneca owns that — but it changes which branch is live.** ADR-0006's own named fallbacks are (i) a floating-`latest` probe that spends quota to detect change, and (ii) *not feasible — stay with CI-on-PR*. A third now exists: **build on the undocumented management API with eyes open**, accepting that a silent upstream change breaks version detection. Granting Manage Actions and re-probing is still worth doing — it tells us what the endpoint actually returns — but the decision is no longer "does it exist" so much as "do we depend on something unsupported".

### Also settled by the same page — `?valueOnly=false` is correct
*"add the `valueOnly=false` query parameter to your GET request."* Verbatim from the docs, on the execution-results GET. This **closes Atchim's unverifiable items 1 and 2** (correct parameter name, casing, and that it belongs on the result GET) — previously unfalsifiable by the suite, now confirmed by both the vendor docs and a live response that came back in the full cell shape.
