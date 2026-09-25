# IDP action definition — invoice extraction (test pack)

What to type into the Anypoint IDP console to create the action this test pack
targets. Nine flat fields (the set the existing live capture already uses) plus
one table, `line_items`.

⚠️ **Name-collision check (fail-open #6, `gate.py`):** field and table names
below are pairwise disjoint — no field is named `line_items`, and no table is
named after a field. Verified against `src/idp_regression/classifier/gate.py`'s
collision check before this doc was written.

⚠️ **Charset check:** every field/table/column name below matches
`normalize.py`'s `_SAFE_NAME_PATTERN` (`^[A-Za-z0-9_-]{1,128}$`) and the golden
schema's `propertyNames` pattern (identical). Verified programmatically — see
`testpack/build_golden.py`'s successful validation run.

## Fields

| Name | Type | Prompt text |
|---|---|---|
| `vendor_name` | text | "Extract the name of the vendor or company issuing this invoice, exactly as printed in the document header." |
| `bill_to` | text | "Extract the name of the customer or organization this invoice is billed to." |
| `invoice_number` | id | "Extract the invoice number or invoice ID exactly as printed, including any prefix (e.g. 'INV-1001')." |
| `invoice_date` | date | "Extract the invoice date and return it in ISO 8601 format, YYYY-MM-DD. If the document prints the date in another format (e.g. DD/MM/YYYY or 'March 15, 2026'), convert it to YYYY-MM-DD." |
| `po_number` | id | "Extract the purchase order number or reference (may be labeled 'PO Number', 'Order Ref', 'Purchase Order No.', or similar). If the document does not include one, leave this field empty." |
| `currency` | text | "Extract the ISO currency code or symbol used for the monetary amounts on this invoice (e.g. USD, EUR, $)." |
| `subtotal` | number | "Extract the subtotal amount (before tax) as a plain number, with no currency symbol or thousands separator (e.g. '1250.00', not '$1,250.00')." |
| `tax` | number | "Extract the tax amount as a plain number, with no currency symbol or thousands separator. If the document has no tax line, leave this field empty." |
| `total` | number | "Extract the total amount due as a plain number, with no currency symbol or thousands separator." |

## Table: `line_items`

| Column | Prompt text |
|---|---|
| `sku` | "Extract the SKU or item code for this line item." |
| `description` | "Extract the item description or name for this line item." |
| `quantity` | "Extract the quantity for this line item as a plain number (use '0' if the row explicitly shows zero quantity)." |
| `unit_price` | "Extract the unit price for this line item as a plain number, with no currency symbol or thousands separator." |
| `amount` | "Extract the extended amount (quantity × unit price) for this line item as a plain number, with no currency symbol or thousands separator." |

## Degraded prompt variant — for the deliberate v1.0.1 regression

To *cause* a regression the gate must catch, publish v1.0.1 with the
`invoice_date` prompt degraded to drop the explicit format instruction:

> **v1.0.0 (baseline):** "Extract the invoice date and return it in ISO 8601
> format, YYYY-MM-DD. If the document prints the date in another format (e.g.
> DD/MM/YYYY or 'March 15, 2026'), convert it to YYYY-MM-DD."
>
> **v1.0.1 (degraded):** "Extract the invoice date from the document."

Why this is realistic, not a nonsense degradation: dropping the format
instruction is the single most common real-world prompt regression for date
fields — the model falls back to echoing whatever format the source document
happens to use. Since `inv-002-format-variance.pdf` prints its date as
`22/01/2026` (DD/MM/YYYY) rather than ISO, this document in particular is
likely to surface the regression: v1.0.0 should still normalize to
`2026-01-22`, while v1.0.1 is likely to return `22/01/2026` or `01/22/2026`
verbatim — a `wrong_value` against the golden's `2026-01-22`. `invoice_date`
is marked `critical: true` **and `format_critical: true`** in every golden
entry (see `README.md`), so this degradation flips `overall_gate` from `PASS`
to `FAIL` — exactly the regression-catching behavior UC-01 exists to verify.

> **Corrected 2026-09-24.** This paragraph used to claim the flip on
> `critical: true` alone. That was wrong, and the live run proved it: the
> degradation produced `22/01/2026` against a golden of `2026-01-22`, which
> is the same calendar date in another format, so the verdict was
> `wrong_format` — and under BR3 that did **not** fail the gate. The run
> returned `exit_code=0` on a genuine prompt regression. `format_critical`
> (ADR-0003 Amendment 2026-09-24, DEBT-80) is what makes the claim above
> true; without it on this field, this degradation is still a green build.
