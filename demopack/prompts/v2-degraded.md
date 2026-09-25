# v2 — the BAD prompt (the deliberate regression)

Paste this as the **second** version (e.g. `1.1.0`). It is identical to v1
except for one row: `invoice_date` has lost its format instruction.

**The whole change is this, and it is worth showing the sponsor side by
side:**

```diff
- invoice_date: Extract the invoice date and return it in ISO 8601 format,
-   YYYY-MM-DD. If the document prints the date in another format (for
-   example 19/03/2026 or March 19, 2026), convert it to YYYY-MM-DD.
+ invoice_date: Extract the invoice date from the document.
```

**Why this is a realistic regression, not a strawman.** Dropping a format
instruction is the most common way a date field silently breaks in the real
world — somebody shortens a prompt, or rewrites it for a different document
type, and the model quietly falls back to echoing whatever the page happens
to print. Nothing errors. Nothing looks wrong. The extraction still
"succeeds" with 0.99 confidence. `demo-002-intl-date.pdf` prints
`19/03/2026`, so v2 returns that verbatim instead of `2026-03-19`.

This is precisely the class of change a human reviewer waves through.

---

## Document type
Invoice

## Fields to extract

| Field | Instruction |
|---|---|
| `vendor_name` | The company that issued the invoice, as printed. |
| `bill_to` | The company being billed. |
| `invoice_number` | The invoice's own identifier, e.g. `INV-2001`. |
| `invoice_date` | **Extract the invoice date from the document.** |
| `po_number` | The purchase-order reference. The label varies — it may read "PO Number" or "Purchase Order". |
| `currency` | The three-letter currency code, e.g. `USD`. |
| `subtotal` | The sum of line amounts, before tax. Digits and a decimal point only, no currency symbol or thousands separators. |
| `tax` | The tax amount. Same number formatting as `subtotal`. |
| `total` | The final amount payable. Same number formatting as `subtotal`. |

## Table to extract

`line_items`, one row per printed line, with columns `sku`, `description`,
`quantity`, `unit_price`, `amount`.

Return **every printed line as its own row, even when two lines share the
same `sku`** — a partial back-order legitimately ships one SKU across two
lines, and collapsing them loses real data.
