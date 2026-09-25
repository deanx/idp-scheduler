"""Ground truth for the three synthetic SPONSOR-DEMO invoices.

Same contract as ``testpack/invoice_data.py`` (which this is trimmed from):
both ``generate_pdfs.py`` and ``build_golden.py`` import this module, so the
PDF text and the golden assertions can never drift -- amounts are computed
once, here, with ``decimal.Decimal``.

**Why a separate, smaller pack.** The test pack has five documents, two of
them multi-page/table-heavy at ~23 s of extraction each -- ~83 s per run.
That is a long silence on a screen share. These three are all single-page
and small, so a run lands in ~40 s, which is narratable.

Every value is **synthetic** (CLAUDE.md ## Domain: golden-set contents are
treated as sensitive; these are invented vendors and amounts, never real
customer data).

The three documents, and the one job each does in the demo:

- ``demo-001-clean``    -- nothing unusual. The control: it must stay green
                          in every run, so when the gate goes red the
                          sponsor can see it went red for a *reason*.
- ``demo-002-intl-date`` -- prints its date as ``19/03/2026`` (DD/MM/YYYY)
                          instead of ISO. **This is the document the demo
                          turns on.** The baseline prompt tells the model to
                          normalise it to ``2026-03-19``; the degraded
                          prompt does not, so the model echoes the page
                          verbatim and the gate catches it.
- ``demo-003-split-shipment`` -- ships ``SKU-500`` on two separate lines (a
                          partial back-order). Ordinary invoice data that
                          used to false-FAIL the gate until 2026-09-24
                          (DEBT-09); included so the demo shows the tool
                          NOT crying wolf, which is the other half of being
                          trustworthy.

⚠️ ``demo-002``'s day-of-month is deliberately **19**, not a value <= 12.
``_DATE_FORMATS`` in the classifier tries ``%m/%d/%Y`` before ``%d/%m/%Y``,
so an ambiguous ``04/03/2026`` would silently resolve American-first
(DEBT-81, open). A day > 12 is unambiguous and keeps that open defect out
of the demo. Do not "tidy" this date.
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
    quantity: str
    unit_price: str

    @property
    def amount(self) -> str:
        return _money(Decimal(self.quantity) * Decimal(self.unit_price))


@dataclass
class Invoice:
    document_id: str
    title_note: str
    vendor_name: str
    bill_to: str
    invoice_number: str
    invoice_date: str  # canonical ISO YYYY-MM-DD -- what the golden asserts
    invoice_date_display: str  # as printed on the page
    po_number: str | None
    po_label: str = "PO Number"
    currency: str = "USD"
    currency_symbol: str = ""
    thousands_sep: bool = False
    line_items: list[LineItem] = field(default_factory=list)
    tax_rate: Decimal | None = Decimal("0.08")
    critical_currency: bool = True

    @property
    def subtotal(self) -> str:
        return _money(sum((Decimal(li.amount) for li in self.line_items), Decimal("0")))

    @property
    def tax(self) -> str | None:
        if self.tax_rate is None:
            return None
        return _money(Decimal(self.subtotal) * self.tax_rate)

    @property
    def total(self) -> str:
        tax = Decimal(self.tax) if self.tax is not None else Decimal("0")
        return _money(Decimal(self.subtotal) + tax)

    def display_money(self, value: str) -> str:
        """Render ``value`` the way it appears printed on this document."""
        if self.thousands_sep:
            whole, _, cents = value.partition(".")
            sign = "-" if whole.startswith("-") else ""
            whole = whole.lstrip("-")
            value = f"{sign}{int(whole):,}.{cents}"
        return f"{self.currency_symbol}{value}"


INVOICES: dict[str, Invoice] = {
    "demo-001-clean": Invoice(
        document_id="demo-001-clean.pdf",
        title_note="Control document -- ISO date, nothing unusual. Stays green.",
        vendor_name="Acme Robotics Ltd",
        bill_to="Northwind Traders Inc.",
        invoice_number="INV-2001",
        invoice_date="2026-03-04",
        invoice_date_display="2026-03-04",
        po_number="PO-7781",
        line_items=[
            LineItem("SKU-100", "Widget Assembly Kit", "10", "25.00"),
            LineItem("SKU-200", "Precision Bearing", "20", "12.50"),
            LineItem("SKU-300", "Servo Motor", "5", "80.00"),
        ],
    ),
    "demo-002-intl-date": Invoice(
        document_id="demo-002-intl-date.pdf",
        title_note="Prints its date DD/MM/YYYY -- the document the demo turns on.",
        vendor_name="Britannia Components PLC",
        bill_to="Northwind Traders Inc.",
        invoice_number="INV-2002",
        invoice_date="2026-03-19",
        invoice_date_display="19/03/2026",  # day 19 > 12 on purpose -- see module docstring
        po_number="PO-7782",
        po_label="Purchase Order",
        line_items=[
            LineItem("SKU-410", "Hydraulic Seal", "12", "7.25"),
            LineItem("SKU-420", "Copper Fitting", "40", "3.10"),
        ],
    ),
    "demo-003-split-shipment": Invoice(
        document_id="demo-003-split-shipment.pdf",
        title_note="SKU-500 ships on two lines (partial back-order). Must NOT false-fail.",
        vendor_name="Global Parts Supply Co",
        bill_to="Meridian Manufacturing",
        invoice_number="INV-2003",
        invoice_date="2026-03-11",
        invoice_date_display="2026-03-11",
        po_number="PO-7783",
        line_items=[
            LineItem("SKU-500", "Bolt M6x20", "100", "0.10"),
            LineItem("SKU-501", "Nut M6", "100", "0.05"),
            LineItem("SKU-500", "Bolt M6x20 (back-order)", "50", "0.10"),
            LineItem("SKU-502", "Washer M6", "100", "0.02"),
        ],
    ),
}
