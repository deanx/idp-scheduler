# End-to-end test plan — IDP Regression Tester

**Goal:** prove the whole loop works, including the part that matters most — that the gate goes
**red** when an extraction genuinely regresses, and green when it doesn't.

**Time:** ~90 minutes, most of it waiting on IDP.
**Cost:** ~12–15 IDP extractions. Nothing else costs money.

> **Read this first.** A green run proves the pipeline executed. It does **not** prove the gate
> works. Steps 7 and 8 are the point of this plan — everything before them is setup.

---

## Phase 0 — Before you start

### 0.1 Confirm the machine is ready
```bash
cd <repo root>
.venv/bin/python -m pytest -q          # expect 1157 passed / 15 skipped
curl -s -o /dev/null -w '%{http_code}\n' "$LANGFUSE_HOST/api/public/health"   # expect 200
```
If Langfuse is down: `docker compose -f ../langfuse/docker-compose.yml up -d`.

### 0.2 Check `.env`
```bash
grep -E '^(IDP_DOCUMENT_DIR|IDP_REGION|LANGFUSE_HOST)=' .env
ls -l .env      # must be -rw------- ; if not: chmod 600 .env
```

⚠️ **`IDP_DOCUMENT_DIR` must point at the folder holding the PDFs you are testing.** For this
pack that is `./testpack`, not the default `./id_seeds`:

```bash
IDP_DOCUMENT_DIR=./testpack
```

Get this wrong and the run aborts with **`hard_failure … "IDP submit call failed or timed out"`** —
a message about the network for what is really a missing file. `_resolve_document_path` performs no
existence check, so a path that does not exist is resolved happily and dies at submit wearing the
wrong label. Confirm before you run:

```bash
set -a; source .env; set +a
ls "$IDP_DOCUMENT_DIR"/inv-00*.pdf | wc -l     # expect 5
```
These are **wrapper conveniences, not app configuration** — nothing under `src/` reads them (see
1.2). What matters is the value you pass as `--org`: it must be the **business group that owns the
action** (`ef1232be-…`), not the parent org. Submitting to the parent returns a 404, and the error
now names `(org, action, version)` so you can tell which of the three is wrong.

`LANGFUSE_HOST` and the two Langfuse keys **are** genuine environment config — credentials and the
service endpoint, which is exactly what `.env` is for.

---

## Phase 1 — Create the action in IDP (~20 min, no cost)

### 1.1 Build the action
In the Anypoint IDP console, create a new Document Action and add each field and the `line_items`
table exactly as specified in **`testpack/idp-action-definition.md`** — names, types and prompt
text. The names matter: the golden set keys off them, and a field/table name collision is now
rejected as a malformed golden.

### 1.2 Publish it as `1.0.0`
Publish, and note the **action id** from the console URL. You will pass it on the command line.

> **The app never reads an action id from the environment — that is deliberate** (ADR-0004 A8/A9).
> `--org`, `--action`, `--version` and `--dataset` are **required flags with no env fallback**,
> because a value that defines *what a run measured* must be visible in the invocation (and, in
> CI, in a reviewed file) rather than resolved from ambient state. A static test asserts that the
> strings `IDP_ORG_ID`, `IDP_ACTION_ID` and `GOLDEN_DATASET_NAME` appear **nowhere under `src/`**.
>
> So every command in this plan passes the action explicitly:
> ```bash
> --org <org-id> --action <action-id> --version 1.0.0 --dataset <dataset-name> --run <name>
> ```
>
> **`.env` is a convenience for the local wrapper only.** `scripts/run_eval_local.sh` reads
> `IDP_ORG_ID` / `IDP_ACTION_ID` / `IDP_TEST_ACTION_VERSION` / `GOLDEN_DATASET_NAME` to *compose*
> that command line and echoes the full invocation before running it — the same as you typing the
> flags. A8 explicitly blesses that: the values may live somewhere convenient, provided nothing in
> `src/` reads them. If you prefer, skip the wrapper entirely and type the flags; the wrapper is
> ergonomics, not configuration.
>
> **Putting an action id in `.env` never changes what the app does.** It only changes what the
> wrapper types for you.

### 1.3 Confirm the app can see it — zero cost
```bash
set -a; source .env; set +a
.venv/bin/python - <<'PY'
import os, sys; sys.path.insert(0,'src')
from idp_regression.adapter.version_probe import MuleSoftVersionProbe, ProbeResult
# EXISTS => the action+version resolve; ABSENT => wrong id, version, or org
PY
```
Simplest equivalent: run one `check-versions` tick (Phase 4.1). `positive_control: exists` means
the action and version resolve. **If it says `anchor_vanished`, stop** — the id, version or org is
wrong, and everything downstream will fail confusingly.

---

## Phase 2 — Baseline capture and golden reconciliation (~15 min, 5 extractions)

> **This is the step people skip and regret.** The goldens in `testpack/golden_set.json` are
> authored from the PDFs' visible content. How IDP *formats* an extracted value — `1150.00` vs
> `1150`, `2026-01-15` vs `15/01/2026` — is not knowable until it runs. Reconcile, don't assume.
https://idp-rt.us-east-1.anypoint.mulesoft.com/api/v1/organizations/ef1232be-0e85-43e7-a7b2-927d32eb6d38/actions/fb900ba5-de93-4445-9ddb-89fe175585b2/versions/1.0.0/executions

### 2.1 Extract each document once and keep the raw responses
```bash
ORG=ef1232be-0e85-43e7-a7b2-927d32eb6d38; ACTION=fb900ba5-de93-4445-9ddb-89fe175585b2; VER=1.0.0
mkdir -p testpack/captures
for f in testpack/inv-00*.pdf; do
  .venv/bin/python scripts/capture_raw.py \
    --org "$ORG" --action "$ACTION" --version "$VER" --document "$f" \
    > "testpack/captures/$(basename "$f" .pdf).raw.json"
done
```
Each invocation **spends one real extraction** and says so on stderr before submitting. The
execution id is redacted by default so the captures are safe to commit.

On success it prints the answer to Phase 2.3 for you, per document:
```
capture_raw: done in 12.4s -- top-level keys: ['documentName', 'fields', 'id', 'status', 'tables']
capture_raw: envelope shape -> top-level rollup
```

### 2.2 Reconcile the goldens against reality
For each capture, compare the extracted values to `testpack/golden_set.json` and **fix the golden
to match IDP's actual formatting** wherever the difference is formatting rather than a genuine
extraction error. Note every change you make — the diff between authored and reconciled goldens is
itself useful information about how the action behaves.

⚠️ **Capture retention is 24 hours.** After that the execution is gone and cannot be re-fetched.
Keep `testpack/captures/` — it is the only durable copy, and it is what closes SR-1 for these
shapes.

### 2.3 Check the multi-page document specifically
Open `testpack/captures/inv-004-multipage.raw.json` and answer one question:

**Does the body contain a `pages[]` array, a top-level `fields`/`tables` rollup, or both?**

This is the single most valuable observation in the whole plan. It settles **DEBT-69(b)** and tells
us whether the union/collision rules in `normalize()` — written for a shape nobody has ever seen —
are correct, unnecessary, or wrong. Record the answer in the ADR-0002 A11 note either way.

---

## Phase 3 — Load the golden set into Langfuse (~5 min, no cost)

```bash
DATASET=idp-regression-testpack

# See exactly what would be sent, touching no network.
# --dry-run prints field NAMES plus a payload digest; add --show-values only if
# you deliberately want the expected values on your terminal.
.venv/bin/python scripts/provision_golden_dataset.py \
  --dataset "$DATASET" --golden-file testpack/golden_set.json \
  --seed inv-001-clean --dry-run

# Provision all five. The script does ONE item per invocation by design —
# it also provisions the committed golden JSON Schema onto the dataset.
for s in inv-001-clean inv-002-format-variance inv-003-table-heavy \
         inv-004-multipage inv-005-missing-fields; do
  .venv/bin/python scripts/provision_golden_dataset.py \
    --dataset "$DATASET" --golden-file testpack/golden_set.json --seed "$s"
done
```

Pass `--dataset "$DATASET"` to every later command. (Putting it in `.env` as
`GOLDEN_DATASET_NAME` only feeds `run_eval_local.sh` — see 1.2.)

> **Flag names matter here:** it is `--golden-file` (not `--golden`), `--dataset` is required, and
> `--seed` selects **one** key from the file. The keys are the `inv-00*` names above.

> The committed golden JSON Schema is provisioned onto the dataset at the same time. If you later
> change the schema without re-provisioning, every run aborts `schema_drift` — that is deliberate.

---

## Phase 4 — Version detection (~10 min, no extraction cost)

### 4.1 One tick, by hand
```bash
set -a; source .env; set +a
.venv/bin/python -m idp_regression.orchestration.check_versions \
  --org "$IDP_ORG_ID" --action "$IDP_ACTION_ID" --dataset "$GOLDEN_DATASET_NAME" \
  --state-file ~/.idp-regression/state.json \
  --max-probes-per-tick 12 --max-probes-per-sweep 40 \
  --patch-lookahead 3 --minor-lookahead 5 --major-lookahead 1 \
  --sweep-every-n-ticks 96 --max-indeterminate-ticks 3 \
  --known-version 1.0.0
```
**Expect:** `outcome: no_new_versions`, `positive_control: exists`, `negative_control: absent`,
`hits: []`. Zero documents processed.

**Read the `probed` list.** It shows exactly which versions were checked — that is the coverage
claim, and it is the only way to know what was *not* checked.

### 4.2 Start the watcher and leave it running
```bash
.venv/bin/python -m idp_regression.orchestration.watch \
  --org "$IDP_ORG_ID" --action "$IDP_ACTION_ID" --dataset "$GOLDEN_DATASET_NAME" \
  --state-file ~/.idp-regression/state.json --interval-seconds 60
```
Leave this terminal open. Drop `--known-version` — the anchor is in the state file now.

---

## Phase 5 — The green run (~10 min, 5 extractions)

```bash
.venv/bin/python -m idp_regression.orchestration.cli \
  --org   ef1232be-0e85-43e7-a7b2-927d32eb6d38 \
  --action fb900ba5-de93-4445-9ddb-89fe175585b2 \
  --version 1.0.0 \
  --dataset idp-generator-test1 \
  --run    baseline-1.0.0
```
Or, if you put those values in `.env`, the wrapper types them for you and echoes the full
invocation before running it:
```bash
./scripts/run_eval_local.sh
```
**Expect exit 0**, and in Langfuse: `gate=PASS`, one `field:<name>` score per golden field per
document, `run_status=complete`.

Check the local artifact — this is where the values live (the platform stays value-free by your
DEBT-18 option B decision):
```bash
ls -l .idp-regression-run-artifacts/        # must be -rw------- / drwx------
```

**If the gate is red here, do not proceed.** It means the goldens still disagree with reality —
go back to 2.2. A baseline that starts red makes every later result meaningless.

---

## Phase 6 — Prove the gate discriminates, cheaply (~5 min, 5 extractions)

Before spending a prompt change, prove the gate can fail at all.

```bash
# Same golden, ONE critical field deliberately wrong, into a separate dataset.
# --perturb-field/--perturb-value exist for exactly this.
PERT=idp-regression-testpack-perturbed
for s in inv-001-clean inv-002-format-variance inv-003-table-heavy \
         inv-004-multipage inv-005-missing-fields; do
  .venv/bin/python scripts/provision_golden_dataset.py \
    --dataset "$PERT" --golden-file testpack/golden_set.json --seed "$s" \
    --perturb-field total --perturb-value 1.00
done

.venv/bin/python -m idp_regression.orchestration.cli \
  --org <org-id> --action <action-id> --version 1.0.0 \
  --dataset "$PERT" --run discriminates-check
```

**Expect exit non-zero**, `gate=FAIL`, and **only `total`** flipped to `wrong_value` — every other
field still `match`. If more than one field changes, something is wrong with the comparison, not
with your edit.

Sanity check before you run it: the dry-run's `payload_sha256` must **differ** from the clean
dataset's. If the digests match, the perturbation did not apply and the run proves nothing.

> A gate that has only ever been observed green is not evidence of a working gate.

> Keep the perturbed dataset. Phase 8 is easier to read if you can point at a run that failed for a
> *known* reason next to one that failed for a real one.

## Phase 7 — The real test: regress the prompt (~20 min, 5 extractions)

This is what the product exists for.

### 7.1 Degrade one prompt and publish `1.0.1`
Apply the **degraded prompt variant** from `testpack/idp-action-definition.md` to the live action
and publish it as `1.0.1`.

### 7.2 Watch the watcher
Within one interval the terminal from 4.2 should print a detection banner naming `1.0.1` and a
ready-to-paste `run_eval` command.

**If it does not detect within a few ticks:** read the `probed` list. If `1.0.1` is in it and was
not a hit, the publish did not take. If it is absent, the lookahead did not reach it — which is the
known false-negative risk, and worth recording.

### 7.3 Run the regression against `1.0.1`
Paste the command the watcher printed.

**Expect exit non-zero and `gate=FAIL`**, with `inv-002-format-variance.pdf`'s `invoice_date`
showing `wrong_format` while the rest stay `match`.

> **Corrected 2026-09-24 — this step failed the first time it was run, and the plan was wrong, not
> the code.** As originally written it said to expect `gate=FAIL` "with the degraded field showing
> `wrong_value` **or** `wrong_format`". Those cannot both hold: under BR3 a `wrong_format` verdict
> never failed the gate, even on a `critical` field. The live `1.1.0` run did exactly what the
> degradation intended — `invoice_date` went `2026-01-22` → `22/01/2026`, the only changed cell of
> 45 — and the tool returned **`exit_code=0`**, because `_format_date` parses both to the same
> calendar date. The fix was a decision, not a patch: `format_critical` is now a per-field opt-in
> (ADR-0003 Amendment 2026-09-24, DEBT-80), set on `invoice_date` in the golden, so a format
> regression on *that* field fails the gate while BR3's default holds everywhere else. **If you are
> re-running this plan from scratch, the dataset must carry the re-provisioned schema and goldens
> (Phase 3) or this step aborts `schema_drift` instead of failing the gate.**

### 7.4 Read it in Langfuse
Open the dataset's compare view. You should see `1.0.0` and `1.0.1` side by side with the per-field
verdicts and the gate, and the regression visible as a verdict change.

**What you will *not* see there: the values.** That is your DEBT-18 option B decision working as
intended — "what did it extract instead" lives in the local run artifact
(`.idp-regression-run-artifacts/<run_id>.json`), not on the platform.

---

## Phase 8 — Prove it recovers

Publish `1.0.2` with the **original** prompt restored. The watcher detects it; the run returns to
`gate=PASS`. The Langfuse series should read **PASS → FAIL → PASS**.

That series is the deliverable. It is the thing to show a sponsor: the gate caught a real prompt
regression and cleared once it was fixed.

---

## What this plan does NOT prove — state these honestly

| | |
|---|---|
| **The watcher under a flaky endpoint** | Fixed this week, but only reproduced synthetically. A real network fault has never been observed. |
| **Notification delivery** | The failure notification's delivery under a loaded `launchd` job is unverified; a TCC denial can be silent. |
| **The `prompts` wire shape** | Only closes if your action emits prompt answers. If it does, keep that capture — it closes DEBT-69(a). |
| **Non-semver versions** | If IDP lets you publish a non-semver version string, the walk cannot find it. Worth one deliberate attempt. |
| **Scale** | Five documents. Nothing here says anything about fifty. |

---

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| Submit returns **404** | Wrong org/action/version — the message now names all three. Check `IDP_ORG_ID` is the business group. |
| Run aborts `schema_drift` | The committed schema changed without re-provisioning. Re-run Phase 3. |
| Run aborts `empty_set` | The dataset has no items, or `GOLDEN_DATASET_NAME` points at the wrong one. |
| Tick says `anchor_vanished` | The action/version no longer resolves, **or** the API was unreachable. The log distinguishes them. |
| Tick says `discriminator_invalid` | The 400-vs-404 behaviour this detector relies on has changed upstream. Stop and re-probe; the detector is no longer trustworthy. |
| Everything passes suspiciously | Check the artifact: are the fields actually populated? An empty extraction compared against an empty golden is vacuously green — that was a real defect here. |
