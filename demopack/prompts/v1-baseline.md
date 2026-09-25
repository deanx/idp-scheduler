# v1 — the GOOD prompt (baseline, and the one you restore at the end)

Paste this into the Anypoint IDP action's prompt configuration and publish it
as the **first** version of the demo (e.g. `1.0.0`).

This is also the prompt you re-publish in the last step to show the gate
going back to green. Keep this file open in a tab during the demo — you will
paste from it twice.

---

## Document type
Invoice

## Fields to extract

| Field | Instruction |
|---|---|
| `vendor_name` | The company that issued the invoice, as printed. |
| `bill_to` | The company being billed. |
| `invoice_number` | The invoice's own identifier, e.g. `INV-2001`. |
| `invoice_date` | **Extract the invoice date and return it in ISO 8601 format, `YYYY-MM-DD`. If the document prints the date in another format (for example `19/03/2026` or `March 19, 2026`), convert it to `YYYY-MM-DD`.** |
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

---

> **The one line that matters for the demo is `invoice_date`'s.** Everything
> else is here so the action extracts a realistic document. The bolded
> sentence is what v2 deletes.
