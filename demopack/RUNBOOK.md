# Sponsor demo — runbook

**The one sentence this demo has to land:** *when someone changes an
extraction prompt, this tool notices on its own and tells you whether the
change made things better or worse — before it reaches production.*

Everything below serves that sentence. Anything that does not is cut.

- **Live time: ~8 minutes.** Three extraction runs at ~40 s each, plus two
  prompt publishes you do by hand in the Anypoint UI.
- **Prep time: ~20 minutes**, done *before* the sponsor joins. Do not
  improvise the prep on the call.

---

# Part 1 — Prep (before the meeting)

## 1.1 Fill in `.env`

```bash
cp demopack/env.demo.example .env      # then edit it
```

Set the MuleSoft and Langfuse values. ⚠️ **Do this before you start sharing
your screen** — `.env` holds the IDP client secret and the Langfuse keys.

## 1.2 Confirm Langfuse is up

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$LANGFUSE_HOST/api/public/health"
```

Anything other than `200` and you have no demo. Fix it now, not later.

## 1.3 Create the IDP action and publish **v1** as `1.0.0`

Paste `demopack/prompts/v1-baseline.md` into the action's prompt
configuration and publish it as `1.0.0`. Note the action id.

## 1.4 Load the golden set

```bash
set -a; source .env; set +a
for s in demo-001-clean demo-002-intl-date demo-003-split-shipment; do
  .venv/bin/python scripts/provision_golden_dataset.py \
    --dataset idp-demo-sponsor --golden-file demopack/golden_set.json --seed "$s"
done
```

Re-running this is safe — items upsert on a deterministic id, so the count
stays at 3. (It did not always: see the note at the end.)

## 1.5 Run the baseline and **confirm it is green**

```bash
.venv/bin/python -m idp_regression.orchestration.cli \
  --org "$IDP_ORG_ID" --action "$IDP_ACTION_ID" \
  --version 1.0.0 --dataset idp-demo-sponsor --run demo-baseline-1.0.0
```

**Expect `exit_code=0`, `pass_count=3`.**

> 🛑 **If this is red, stop and fix it before the meeting.** A baseline that
> starts red makes every later step meaningless — you will be unable to tell
> the sponsor whether the red you show them later is the regression or the
> setup. Most likely cause: the goldens disagree with what this action
> actually extracts. Diff the run artifact against
> `demopack/golden_set.json` and correct the golden, not the gate.

## 1.6 Stage the terminals

Two terminals, side by side, **font size up** (18pt+):

- **LEFT — "the watcher".** Start it now and leave it running all meeting:

```bash
.venv/bin/python -m idp_regression.orchestration.watch \
  --org "$IDP_ORG_ID" --action "$IDP_ACTION_ID" --dataset idp-demo-sponsor \
  --state-file ~/.idp-regression/demo-state.json \
  --known-version 1.0.0 --interval-seconds 30
```

`--interval-seconds 30` is a demo setting (the default is 5 minutes) so a
detection lands while the sponsor is still looking at it. It costs no
extraction quota — a tick is ~11 HTTP calls and zero documents.

- **RIGHT — empty.** This is where you run the regressions.

## 1.7 Have these open in a browser

1. The Anypoint IDP action, on the prompt-editing screen.
2. `demopack/prompts/v2-degraded.md` — you will paste from it.
3. Langfuse, on the `idp-demo-sponsor` dataset.

## 1.8 Pre-flight checklist

- [ ] `.env` filled, and **not** on screen
- [ ] baseline run green (`exit_code=0`, 3 PASS)
- [ ] watcher running on the left, has printed at least one tick
- [ ] `demo-state.json` anchored at `1.0.0`
- [ ] v2 prompt open in a tab, ready to paste

---

# Part 2 — The demo (~8 minutes)

## Act 1 — The problem (45 seconds, no terminal)

> "Our document extraction runs on a prompt. Somebody edits that prompt —
> to support a new document type, or just to tidy it up — and publishes a
> new version. Today, nobody finds out what that edit broke until a customer
> tells us. The change doesn't error. It just quietly starts getting a field
> wrong."

Then point at the left terminal:

> "That's a watcher. It's checking, on its own, whether anyone has published
> a new version of our extraction prompt. It costs nothing to run — it isn't
> processing any documents, it's just asking 'has anything changed?'"

## Act 2 — Break it, on purpose (90 seconds)

Switch to the Anypoint tab. Show the v1 prompt's `invoice_date` line, then
paste v2 over it and publish as **`1.1.0`**.

Say exactly what changed — this is the most important thing you say all
demo:

> "One sentence. The old prompt said 'return the date in ISO format,
> YYYY-MM-DD, and convert it if the document prints it differently.' The new
> one just says 'extract the invoice date.' That's the whole change. It's
> the kind of edit that sails through code review."

## Act 3 — It notices (30 seconds — the first payoff)

Switch to the **left** terminal and wait for the banner:

```
  NEW VERSION DETECTED: 1.1.0

  Ready to paste -- run this to regress it:

      .venv/bin/python -m idp_regression.orchestration.cli --org ... --version 1.1.0 ...
```

> "Nobody told it. It found the new version by itself, and it's handing me
> the exact command to test it."

**Copy that command.**

## Act 4 — It catches the regression (90 seconds — the main payoff)

Paste into the **right** terminal, change `--run` to `demo-regressed-1.1.0`,
and run it. Narrate while it works — roughly 40 seconds:

> "It's pulling three invoices through the new prompt and comparing every
> field against a known-good answer a human curated."

Then the last two lines land:

```
document processed document_id="demo-002-intl-date.pdf" gate=FAIL
run_end outcome=gate_failed exit_code=1 pass_count=2 fail_count=1
```

This is the moment. Land these three points, in this order:

1. **It's red, and it's red for one document.** The other two passed — so
   this isn't a broken test, it's a specific finding.
2. **It names the field.** Show the artifact:

   ```bash
   .venv/bin/python -c "import json,glob,os; f=max(glob.glob('.idp-regression-run-artifacts/*.json'),key=os.path.getmtime); d=json.load(open(f)); r=list(d)[0]; print(json.dumps(d[r]['demo-002-intl-date.pdf']['invoice_date'],indent=2))"
   ```

   ```json
   {
     "expected": "2026-03-19",
     "actual":   "19/03/2026",
     "verdict":  "wrong_format"
   }
   ```

   > "It expected `2026-03-19` and got `19/03/2026`. That's the same day —
   > which is exactly why a human skims past it — but it's the wrong format,
   > and anything downstream parsing that date breaks."

3. **`exit_code=1`.** Tap this:

   > "That's the number a CI pipeline reads. This doesn't need anyone to be
   > watching — it can block the change automatically."

## Act 5 — And it clears when you fix it (2 minutes)

Back to Anypoint. Paste **v1** back in and publish as **`1.1.1`**.

The watcher detects it. Run the command it prints, with
`--run demo-fixed-1.1.1`:

```
run_end outcome=success exit_code=0 pass_count=3 fail_count=0
```

> "Same three documents, same golden answers. Green. So the signal is
> trustworthy in both directions — it goes red when something breaks, and it
> clears when it's fixed. A gate that only ever says 'no' gets switched off
> within a month."

## Act 6 — The history (45 seconds, optional — cut this first if you're long)

Switch to Langfuse, the `idp-demo-sponsor` dataset.

> "Every run is recorded against the same curated answer set, so you can see
> the quality of this extractor over time, per version."

⚠️ **Say this out loud, do not skip it:**

> "Note what's *not* here: the actual values. No invoice totals, no customer
> names. The platform stores only the verdicts. The values stay on the
> machine that ran the test."

That is a deliberate design decision and sponsors tend to ask.

---

# Part 3 — If it goes wrong

| Symptom | What to do, live |
|---|---|
| Watcher doesn't detect within ~3 ticks | Don't wait in silence. Say "it polls on an interval; let me run the same check directly" and run the command from Act 3 by hand with `--version 1.1.0`. The demo continues — detection is a convenience, the gate is the product. |
| Run aborts `schema_drift` | The golden schema changed without re-provisioning. Re-run step 1.4. Should never happen if you did the prep. |
| Submit returns 404 | Wrong org / action / version. `IDP_ORG_ID` must be the **business group** id. |
| A document other than `demo-002` fails | A genuine finding, not the scripted one. Say so honestly — "that's a second thing it caught, I'll look at it after" — and carry on. Do not improvise a diagnosis on the call. |
| The gate goes green in Act 4 | The publish didn't take, or it took under a different version. Check the Anypoint version list. |

---

# What this demo does NOT prove — if asked, say so

Being straight about the limits is what makes the rest credible.

- **Three synthetic invoices.** Nothing here says anything about fifty real
  ones, or about document types nobody has tried yet.
- **One regression class.** It shows a date-format regression being caught.
  A curated answer set only catches what it has answers for.
- **Line-item changes aren't visible in the platform view yet** — only the
  nine top-level fields are scored there. A line-item regression still fails
  the gate locally and appears in the run artifact; it just isn't on the
  dashboard (tracked as DEBT-14).
- **The curated answer set is the product's ceiling.** If a golden answer is
  wrong, the gate is confidently wrong with it. Keeping that set correct is
  a real, ongoing job — worth naming, because it is the thing a sponsor
  needs to resource.

---

# Notes for you, not for the sponsor

- `demo-003-split-shipment.pdf` ships `SKU-500` on two lines on purpose. That
  shape used to make the gate false-FAIL (DEBT-09, fixed 2026-09-24). It's in
  the pack so the demo shows the tool *not* crying wolf — a gate that fires on
  correct output is worse than no gate.
- `demo-002`'s date is the **19th** deliberately. The classifier resolves an
  ambiguous `NN/NN/YYYY` American-first, so a day ≤ 12 could be misread
  (DEBT-81, open). A day > 12 is unambiguous. **Don't change that date.**
- `invoice_date` is the only field carrying `format_critical: true`. Without
  it, a format-only change is informational and the gate stays green — which
  is precisely how this regression escaped on 2026-09-24 before DEBT-80 was
  fixed. If Act 4 goes green, check that flag survived in the golden.
- Re-provisioning used to duplicate every item instead of upserting
  (DEBT-82). Fixed, but if you ever see `items=6` for a 3-document pack,
  that's what happened — the dataset has stale twins and the run is not
  trustworthy.
