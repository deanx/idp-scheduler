"""Ground truth for the five synthetic test-pack invoices.

Single source of truth: both ``generate_pdfs.py`` (renders the PDF text)
and ``build_golden.py`` (emits ``golden_set.json``) import this module, so
line-item amounts, subtotal, tax and total can never drift between what
the PDF says and what the golden asserts -- they are computed once, here,
with ``decimal.Decimal`` so there is no floating-point rounding surprise.

Every value in here is **synthetic** (CLAUDE.md ## Domain: golden-set
contents are treated as sensitive; these are invented vendors/amounts,
never real customer data).

Two kinds of "formatting" are modelled deliberately, and they must not be
confused:
  - ``*_display`` strings: how a value is *printed on the PDF page*. This
    is what varies across documents (date format, currency symbol,
    thousands separators, field-label wording) -- it is the raw material
    an OCR/IDP model has to normalize.
  - the plain (canonical) value: the ISO/plain-number form we believe IDP
    *should* extract, and what the golden asserts. Per the package README,
    this is a best-effort guess until reconciled against a real capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


def _money(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class LineItem:
    sku: str
    description: str
    quantity: str  # canonical (golden) quantity string, e.g. "10" or "0"
    unit_price: str  # canonical unit price, e.g. "25.00"

    @property
    def amount(self) -> str:
        return _money(Decimal(self.quantity) * Decimal(self.unit_price))


@dataclass
class Invoice:
    document_id: str  # PDF filename, also the golden's document_id
    title_note: str  # short human note shown under the header, not extracted
    vendor_name: str
    bill_to: str
    invoice_number: str
    invoice_date: str  # canonical ISO YYYY-MM-DD
    invoice_date_display: str  # as printed on the page
    po_number: str | None  # canonical; None => field omitted from the page entirely
    po_label: str = "PO Number"  # label as printed (varies for inv-002)
    currency: str = "USD"
    currency_symbol: str = ""  # printed prefix on money amounts, e.g. "$"
    thousands_sep: bool = False  # print totals with a thousands separator
    line_items: list[LineItem] = field(default_factory=list)
    tax_rate: Decimal | None = Decimal("0.08")  # None => no tax line at all
    critical_currency: bool = True

    @property
    def subtotal(self) -> str:
        total = sum((Decimal(li.amount) for li in self.line_items), Decimal("0"))
        return _money(total)

    @property
    def tax(self) -> str | None:
        if self.tax_rate is None:
            return None
        return _money(Decimal(self.subtotal) * self.tax_rate)

    @property
    def total(self) -> str:
        subtotal = Decimal(self.subtotal)
        tax = Decimal(self.tax) if self.tax is not None else Decimal("0")
        return _money(subtotal + tax)

    def display_money(self, value: str) -> str:
        """Render ``value`` the way it appears printed on this document."""
        if self.thousands_sep:
            whole, _, cents = value.partition(".")
            sign = "-" if whole.startswith("-") else ""
            whole = whole.lstrip("-")
            grouped = f"{int(whole):,}"
            value = f"{sign}{grouped}.{cents}"
        return f"{self.currency_symbol}{value}"


INVOICES: dict[str, Invoice] = {
    "inv-001-clean": Invoice(
        document_id="inv-001-clean.pdf",
        title_note="Clean baseline -- every field present, no format tricks.",
        vendor_name="Acme Robotics Ltd",
        bill_to="Northwind Traders Inc.",
        invoice_number="INV-1001",
        invoice_date="2026-01-15",
        invoice_date_display="2026-01-15",
        po_number="PO-4471",
        line_items=[
            LineItem("SKU-100", "Widget Assembly Kit", "10", "25.00"),
            LineItem("SKU-200", "Precision Bearing", "20", "12.50"),
            LineItem("SKU-300", "Servo Motor", "5", "80.00"),
            LineItem("SKU-400", "Control Board", "2", "150.00"),
        ],
    ),
    "inv-002-format-variance": Invoice(
        document_id="inv-002-format-variance.pdf",
        title_note=(
            "Same shape as inv-001, deliberately reformatted: DD/MM/YYYY date, "
            "$ + thousands separator on money, and the PO field relabeled "
            "'Order Ref' instead of 'PO Number'."
        ),
        vendor_name="Acme Robotics Ltd",
        bill_to="Northwind Traders Inc.",
        invoice_number="INV-1002",
        invoice_date="2026-01-22",
        invoice_date_display="22/01/2026",
        po_number="PO-4488",
        po_label="Order Ref",
        currency_symbol="$",
        thousands_sep=True,
        line_items=[
            LineItem("SKU-101", "Widget Assembly Kit v2", "15", "26.00"),
            LineItem("SKU-201", "Precision Bearing", "30", "13.00"),
            LineItem("SKU-301", "Servo Motor", "6", "82.00"),
            LineItem("SKU-401", "Control Board", "3", "160.00"),
        ],
    ),
    "inv-003-table-heavy": Invoice(
        document_id="inv-003-table-heavy.pdf",
        title_note=(
            "13 line items: SKU-500 appears twice (split shipment -- exercises the "
            "duplicate match_key collapse, DEBT-09) and SKU-503 has quantity 0."
        ),
        vendor_name="Global Parts Supply Co",
        bill_to="Meridian Manufacturing",
        invoice_number="INV-1003",
        invoice_date="2026-02-03",
        invoice_date_display="2026-02-03",
        po_number="PO-4501",
        line_items=[
            LineItem("SKU-500", "Bolt M6x20", "100", "0.10"),
            LineItem("SKU-501", "Nut M6", "100", "0.05"),
            LineItem("SKU-502", "Washer M6", "100", "0.02"),
            LineItem("SKU-500", "Bolt M6x20 (2nd shipment)", "50", "0.10"),
            LineItem("SKU-503", "Gasket Set", "0", "15.00"),
            LineItem("SKU-504", "Hinge Bracket", "40", "3.50"),
            LineItem("SKU-505", "Cable Tie 200mm", "500", "0.03"),
            LineItem("SKU-506", "Spacer Ring", "60", "0.75"),
            LineItem("SKU-507", "Mounting Plate", "20", "9.00"),
            LineItem("SKU-508", "Rubber Grommet", "80", "0.40"),
            LineItem("SKU-509", "Lock Washer", "100", "0.06"),
            LineItem("SKU-510", "Thread Insert", "30", "1.20"),
            LineItem("SKU-511", "Shim Pack", "10", "4.50"),
        ],
    ),
    "inv-004-multipage": Invoice(
        document_id="inv-004-multipage.pdf",
        title_note=(
            "14 line items split across 3 physical pages; the table continues "
            "over the page 1/2 break and totals land on page 3. Closes DEBT-69(b)."
        ),
        vendor_name="Continental Freight & Logistics",
        bill_to="Harbor Point Distribution",
        invoice_number="INV-1004",
        invoice_date="2026-02-20",
        invoice_date_display="2026-02-20",
        po_number="PO-4522",
        line_items=[
            LineItem("SKU-700", "Freight Item 1", "12", "9.00"),
            LineItem("SKU-701", "Freight Item 2", "8", "14.50"),
            LineItem("SKU-702", "Freight Item 3", "20", "3.25"),
            LineItem("SKU-703", "Freight Item 4", "5", "40.00"),
            LineItem("SKU-704", "Freight Item 5", "16", "6.75"),
            LineItem("SKU-705", "Freight Item 6", "10", "11.00"),
            LineItem("SKU-706", "Freight Item 7", "25", "2.40"),
            LineItem("SKU-707", "Freight Item 8", "6", "18.00"),
            LineItem("SKU-708", "Freight Item 9", "9", "7.50"),
            LineItem("SKU-709", "Freight Item 10", "14", "5.00"),
            LineItem("SKU-710", "Freight Item 11", "18", "3.00"),
            LineItem("SKU-711", "Freight Item 12", "7", "22.00"),
            LineItem("SKU-712", "Freight Item 13", "11", "8.25"),
            LineItem("SKU-713", "Freight Item 14", "4", "30.00"),
        ],
    ),
    "inv-005-missing-fields": Invoice(
        document_id="inv-005-missing-fields.pdf",
        title_note=(
            "Sparse invoice: no PO number, no tax line -- exercises `missing` "
            "verdicts for both (non-critical by design)."
        ),
        vendor_name="Riverside Office Supplies",
        bill_to="Cedar Grove Elementary",
        invoice_number="INV-1005",
        invoice_date="2026-03-01",
        invoice_date_display="2026-03-01",
        po_number=None,
        tax_rate=None,
        line_items=[
            LineItem("SKU-900", "Copy Paper A4 (500 sheets)", "20", "4.50"),
            LineItem("SKU-901", "Stapler Heavy Duty", "5", "12.00"),
            LineItem("SKU-902", "Whiteboard Markers (Pack of 8)", "10", "6.50"),
        ],
    ),
}
