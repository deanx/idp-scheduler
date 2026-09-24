# Test-data pack — end-to-end exercise materials

Everything needed to exercise UC-01 end to end against a real MuleSoft
Anypoint IDP action and a real Langfuse instance: five invoice PDFs, the IDP
action definition to create, and a golden dataset to provision. **Synthetic
data only** — every vendor, customer, invoice number and amount below is
invented for this pack; none of it is real customer content.

## Files

| File | What it is |
|---|---|
| `invoice_data.py` | Single source of ground truth (vendor/fields/line items) for every invoice, in `Decimal` arithmetic. Imported by both generators below so the PDF text and the golden values can never drift apart. |
| `pdfkit.py` | A ~150-line dependency-free PDF 1.4 writer (Helvetica text placement + rule lines only). See "How the PDFs were generated" below. |
| `generate_pdfs.py` | Renders the five PDFs from `invoice_data.py`, then re-reads each file's `/Count` back off disk to verify page count. Run: `.venv/bin/python testpack/generate_pdfs.py`. |
| `build_golden.py` | Builds `golden_set.json` from `invoice_data.py` and validates every entry against the committed `src/idp_regression/platform/schema/golden_schema_v1.json` with `jsonschema` (same library, same schema file `scripts/provision_golden_dataset.py` and `tests/platform/test_golden_schema_contract.py` use). Run: `.venv/bin/python testpack/build_golden.py`. |
| `golden_set.json` | Generated output — five golden entries, one per PDF, in DATA-MODEL-01 §1 shape. |
| `idp-action-definition.md` | What to type into the Anypoint IDP console: 9 fields + 1 table (`line_items`), each with type and prompt text, plus the degraded `invoice_date` prompt variant for the deliberate v1.0.1 regression. |
| `inv-*.pdf` | The five generated invoices. |

## How the PDFs were generated, and why

**Chose:** a small pure-Python PDF emitter (`pdfkit.py`), not `textutil` /
`cupsfilter` (both present on macOS and considered first).

**Why not `textutil`/`cupsfilter`:** both convert a plain-text or RTF input by
running it through their own print/layout pipeline. That pipeline decides
line wrapping and page breaks itself — there is no reliable, portable way to
*guarantee* an exact page count or exact text position from it. `inv-004`'s
entire reason for existing is a **guaranteed >= 3-page** document with the
line-item table spanning a page break; a renderer that paginates for you
can't make that guarantee reproducibly across machines/OS versions.

**Why a hand-rolled PDF emitter is reasonable here:** these are simple
text-and-table layouts — positioned `Tj` text-show operators plus a couple of
rule lines, one built-in Helvetica font (no embedding needed, one of the 14
standard PDF fonts every reader must support), no images, no compression.
`pdfkit.py` is ~150 lines: a page tree, one content stream per page, and a
correctly-offset xref table. No new dependency, and it puts page breaks
exactly where the pack needs them.

**Verification performed** (not just "trust the code"):
- `generate_pdfs.py` regexes `/Count` back out of the **actual bytes written
  to disk** (not the in-memory page list) after every run, and asserts
  `inv-004-multipage.pdf` is `>= 3`.
- All five files were also rendered through macOS Quick Look (`qlmanage -t`,
  the OS's own PDF renderer, independent of the code that wrote the bytes) to
  confirm they are valid, readable PDFs, not just byte-plausible ones.

**Verified page counts** (2026-09-24 run):

```
inv-001-clean.pdf: 1
inv-002-format-variance.pdf: 1
inv-003-table-heavy.pdf: 1
inv-004-multipage.pdf: 3
inv-005-missing-fields.pdf: 1
```

`inv-004-multipage.pdf` — the most valuable artifact in the pack per the
brief — is genuinely 3 pages: page 1 header + first 5 line items, page 2
continuation + next 5 items, page 3 final 4 items + subtotal/tax/total. This
is the only document in the pack that can exercise the `pages[]`-plus-rollup
wire shape DEBT-69(b) needs closed.

## Schema validation

`build_golden.py` validates every entry with `jsonschema.Draft7Validator`
against the exact committed `golden_schema_v1.json` (the same file the
server-side Ajv `strict: true` schema is provisioned from). Output from the
2026-09-24 run:

```
inv-001-clean: VALID
inv-002-format-variance: VALID
inv-003-table-heavy: VALID
inv-004-multipage: VALID
inv-005-missing-fields: VALID

Wrote .../testpack/golden_set.json (5 entries)
```

Per DATA-MODEL-01 N2, this is local `jsonschema` validation (`re.search`
semantics for `$`), not the server's Ajv — treat it as a strong local check,
never a substitute for the platform's own acceptance on provision.

## Critical markers — and why

| Field | Critical | Why |
|---|---|---|
| `vendor_name` | true | Identifies who issued the invoice; a wrong vendor is a hard extraction failure, not a formatting nuance. |
| `bill_to` | false | Useful context, but a mismatch here doesn't invalidate the financial content the gate exists to protect. |
| `invoice_number` | true | Primary identifier; a wrong invoice number breaks reconciliation with the source system. |
| `invoice_date` | true | Drives the format-normalization behavior the pack is specifically testing (`inv-002`'s DD/MM/YYYY, the degraded v1.0.1 prompt in `idp-action-definition.md`); getting it wrong is exactly the class of regression UC-01 exists to catch. |
| `po_number` | **false** | `inv-005` legitimately has no PO number on the page — see "Deliberate `missing` exercise" below. A field that's sometimes absent by design cannot be critical without making every sparse-but-valid invoice fail the gate. |
| `currency` | true | A misread currency silently changes what every other number on the invoice means; treated as financial-correctness-critical, same tier as the amounts. |
| `subtotal` | true | Financial total; feeds `total` and is independently checkable against the line-item sum. |
| `tax` | false | Legitimately absent on `inv-005` (no tax line), and optional/jurisdiction-dependent on real invoices in general — the pack marks it non-critical for the same reason as `po_number`. |
| `total` | true | The number every downstream system actually reconciles against; the single highest-value field to get right. |
| `tables.line_items` (table-level) | true | The subtotal is derived from these rows; a systematically wrong line-item table would still show a plausible subtotal only by coincidence. |

## Deliberate `missing` exercise (`inv-005`)

`inv-005-missing-fields.pdf` prints **no PO number line and no tax line at
all** — not blank labels, the lines don't exist on the page. `_classify_field`
in `src/idp_regression/classifier/gate.py` returns verdict `"missing"` the
moment the *actual* side is absent or empty, **before it ever looks at the
golden's `value`** (see lines ~182–200: the `acell is None` / `_is_empty`
branches both short-circuit to `missing` ahead of `compare_value`). That means:

- The golden **must** declare `po_number` and `tax` (a golden schema entry
  requires a non-empty `value` for `type: id`/`type: number` — an empty
  string fails the id/number patterns) for a `missing` verdict to have
  something to be "missing" *against*.
- `build_golden.py` therefore stores inert placeholders (`po_number: "N/A"`,
  `tax: "0.00"`) for this one document. **These values are never compared.**
  They exist only so the golden entry is schema-valid; the expected, by-design
  outcome for this document is that both fields classify as `missing` and,
  being `critical: false`, do not fail the gate.
- Do not read `"N/A"` or `"0.00"` here as a claim about the real invoice —
  they are inert by construction, documented here so nobody mistakes them for
  guessed ground truth later.

## Reconcile after the first real run

**These goldens are best-effort, not verified.** I authored the PDFs, so I
know what the documents *say* — but how IDP actually *formats* an extracted
value is not knowable until it runs (`1150.00` vs `1150`, `2026-01-15` vs
`15/01/2026`, `USD` vs `$`). `invoice_data.py` stores the canonical/ISO form
we believe IDP *should* return; `golden_set.json` is built directly from that,
not from a real capture.

**Step one of the execution plan must be:** run the action once against these
five PDFs, capture the real output, and reconcile `golden_set.json` against
it — field by field — exactly as `SEED-001` was built (see
`scripts/provision_golden_dataset.py`'s docstring and `id_seeds/README.md`
for that precedent). Do not trust this file's values as ground truth before
that reconciliation happens.

## Tracked vs. untracked — `testpack/` should be **tracked**

Unlike `id_seeds/` (gitignored — `## Domain` in `CLAUDE.md` treats real
golden-set contents as sensitive, since real extracted values can be
financial data / PII), `testpack/` is **entirely synthetic** and is meant to
be shared — it's onboarding/demo material for exercising the whole pipeline,
not a store of real extracted data. Recommend committing every file under
`testpack/`, including the generated `*.pdf` and `golden_set.json` (they're
small, deterministic, and reproducible from `invoice_data.py` if ever lost —
regenerate with `generate_pdfs.py` + `build_golden.py`). **This is a
deliberate exception to the `id_seeds/` sensitivity rule, stated explicitly so
nobody later assumes the same gitignore treatment applies here.**

## Reproducing the pack

```bash
.venv/bin/python testpack/generate_pdfs.py   # writes the 5 PDFs, verifies page counts
.venv/bin/python testpack/build_golden.py    # writes golden_set.json, validates against the schema
```

Both scripts are deterministic — re-running them with unchanged
`invoice_data.py` reproduces byte-identical `golden_set.json` and PDFs with
identical text content (the PDF's `%\xe2\xe3\xcf\xd3` binary marker and object
layout are fixed; the only bytes that would differ run-to-run come from
`invoice_data.py` itself).
