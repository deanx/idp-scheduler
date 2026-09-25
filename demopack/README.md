# demopack — the sponsor demo

A deliberately small, self-contained pack for showing this tool to a
non-technical sponsor in about 8 minutes.

**Start here: [`RUNBOOK.md`](RUNBOOK.md).** It is the script — prep, the
live walk-through, what to say, and what to do when something breaks. The
rest of this file just says what the pieces are.

## What's in here

| | |
|---|---|
| `RUNBOOK.md` | **The demo script.** The only file you need open during the meeting. |
| `ARCHITECTURE-BRIEF.md` | Bullet-based architecture walk-through, context diagram down to component internals. For the conversation *after* the demo. |
| `prompts/v1-baseline.md` | The good prompt. Publish as `1.0.0`; re-publish as `1.1.1` at the end. |
| `prompts/v2-degraded.md` | The bad prompt — one deleted sentence. Publish as `1.1.0`. |
| `env.demo.example` | Copy to the repo root as `.env` and fill in. Never screen-share it. |
| `demo-00*.pdf` | Three synthetic single-page invoices. |
| `golden_set.json` | The curated known-good answers. Generated — do not hand-edit. |
| `invoice_data.py` | Single source of truth for the PDFs *and* the goldens. |
| `generate_pdfs.py` / `build_golden.py` | Regenerate both from `invoice_data.py`. |
| `pdfkit.py` | Minimal PDF writer, no third-party dependency. |

## Why this is separate from `testpack/`

`testpack/` is the correctness pack: five documents, multi-page and
table-heavy, ~83 s per run. That is the right shape for proving the system
works and the wrong shape for a live audience — a minute and a half of
silence loses the room.

This pack is three single-page documents, ~40 s per run, each doing exactly
one job in the story:

- `demo-001-clean` — the control. Stays green, so a red result is visibly
  *specific* rather than "the tool is broken".
- `demo-002-intl-date` — prints `19/03/2026` instead of ISO. **This is the
  document the demo turns on.**
- `demo-003-split-shipment` — ships one SKU across two lines. Shows the tool
  *not* crying wolf on legitimate data.

## Regenerating

If you change `invoice_data.py`, regenerate both artifacts and re-provision
the dataset:

```bash
.venv/bin/python demopack/generate_pdfs.py
.venv/bin/python demopack/build_golden.py
```

`build_golden.py` validates every entry against the committed golden JSON
Schema and prints `VALID` / `INVALID` per document. **`invoice_date` carries
`format_critical: true`** — that flag is what makes the demo's regression
fail the gate rather than pass as informational. If it goes missing, Act 4
goes green and the demo has no payoff.

## Verifying the pack without spending IDP quota

This replays both prompt versions through the real classifier against the
real goldens — no network, no extraction:

```bash
.venv/bin/python - <<'EOF'
import json
from idp_regression.classifier import classify, overall_gate
gs = json.load(open("demopack/golden_set.json"))
def sim(g, ov):
    a = {"status": "SUCCEEDED",
         "fields": {k: {"value": v["value"], "confidence": 0.99} for k, v in g["fields"].items()},
         "tables": {"line_items": [{c: {"value": x} for c, x in r.items()}
                                   for r in g["tables"]["line_items"]["rows"]]}}
    for k, v in ov.items():
        a["fields"][k] = {"value": v, "confidence": 0.99}
    return a
for label, ov in [("v1 baseline", {}), ("v2 degraded", {"invoice_date": "19/03/2026"})]:
    print(label)
    for name, e in gs.items():
        g = dict(e); g.pop("document_id")
        o = ov if name == "demo-002-intl-date" else {}
        print(f"   {name:26} {overall_gate(classify(g, sim(g, o)))}")
EOF
```

Expected: v1 all PASS; v2 shows `demo-002-intl-date` FAIL and the other two
PASS. If that doesn't hold, the demo won't either — fix it before the
meeting, not during.
