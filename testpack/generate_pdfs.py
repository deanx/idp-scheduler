#!/usr/bin/env python
"""Generate the five synthetic invoice PDFs for the test-data pack.

No new dependencies: `reportlab`/`fpdf` are deliberately not used (see
``pdfkit.py`` docstring for why `textutil`/`cupsfilter` were considered
and rejected too). This script renders each invoice with the tiny
in-repo PDF writer in ``pdfkit.py``, from the single source of ground
truth in ``invoice_data.py`` -- so the numbers on the page and the
numbers in ``golden_set.json`` (built by ``build_golden.py`` from the
same module) can never drift apart.

Usage::

    .venv/bin/python testpack/generate_pdfs.py

Verifies and prints each output file's page count (`/Count` in the
generated PDF, read back from the actual bytes written -- not just the
in-memory page list) before exiting.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from invoice_data import INVOICES, Invoice
from pdfkit import Page, PDFDocument

OUT_DIR = Path(__file__).resolve().parent

# Column x-positions shared by every table layout in the pack.
COL_SKU = 50
COL_DESC = 115
COL_QTY = 330
COL_UNIT = 390
COL_AMOUNT = 470
TABLE_RIGHT = 560

TOP_Y = 740
LINE_H = 15


def _header(page: Page, inv: Invoice, y: float) -> float:
    page.text(50, y, inv.vendor_name, size=16, bold=True)
    y -= 22
    page.text(50, y, "INVOICE", size=12, bold=True)
    y -= 20
    page.text(50, y, f"Bill To: {inv.bill_to}", size=10)
    y -= LINE_H
    page.text(50, y, f"Invoice Number: {inv.invoice_number}", size=10)
    y -= LINE_H
    page.text(50, y, f"Invoice Date: {inv.invoice_date_display}", size=10)
    y -= LINE_H
    if inv.po_number is not None:
        page.text(50, y, f"{inv.po_label}: {inv.po_number}", size=10)
        y -= LINE_H
    page.text(50, y, f"Currency: {inv.currency}", size=10)
    y -= LINE_H * 1.5
    return y


def _table_header(page: Page, y: float) -> float:
    page.text(COL_SKU, y, "SKU", size=9, bold=True)
    page.text(COL_DESC, y, "Description", size=9, bold=True)
    page.text(COL_QTY, y, "Qty", size=9, bold=True)
    page.text(COL_UNIT, y, "Unit Price", size=9, bold=True)
    page.text(COL_AMOUNT, y, "Amount", size=9, bold=True)
    y -= 6
    page.hline(50, TABLE_RIGHT, y)
    y -= LINE_H
    return y


def _table_row(page: Page, y: float, li) -> float:  # type: ignore[no-untyped-def]
    page.text(COL_SKU, y, li.sku, size=9)
    page.text(COL_DESC, y, li.description, size=9)
    page.text(COL_QTY, y, li.quantity, size=9)
    page.text(COL_UNIT, y, li.unit_price, size=9)
    page.text(COL_AMOUNT, y, li.amount, size=9)
    return y - LINE_H


def _totals(page: Page, inv: Invoice, y: float) -> float:
    y -= 6
    page.hline(COL_UNIT - 10, TABLE_RIGHT, y)
    y -= LINE_H
    page.text(COL_UNIT, y, "Subtotal:", size=10)
    page.text(COL_AMOUNT, y, inv.display_money(inv.subtotal), size=10)
    y -= LINE_H
    if inv.tax is not None:
        page.text(COL_UNIT, y, "Tax:", size=10)
        page.text(COL_AMOUNT, y, inv.display_money(inv.tax), size=10)
        y -= LINE_H
    page.text(COL_UNIT, y, "Total:", size=10, bold=True)
    page.text(COL_AMOUNT, y, inv.display_money(inv.total), size=10, bold=True)
    y -= LINE_H
    return y


def render_single_page(doc: PDFDocument, inv: Invoice) -> None:
    page = doc.add_page()
    y = _header(page, inv, TOP_Y)
    y = _table_header(page, y)
    for li in inv.line_items:
        y = _table_row(page, y, li)
    _totals(page, inv, y)


def render_multipage(doc: PDFDocument, inv: Invoice) -> None:
    """inv-004: force >= 3 physical pages, table spanning the break,
    totals on the final page -- the shape DEBT-69(b) needs to exercise
    the pages[]-plus-rollup wire shape."""
    items = inv.line_items
    # 5 rows on page 1 (after the header block), 5 on page 2, remaining
    # 4 + totals on page 3. Chosen so no page overflows its content area.
    chunks = [items[0:5], items[5:10], items[10:]]

    page1 = doc.add_page()
    y = _header(page1, inv, TOP_Y)
    y = _table_header(page1, y)
    for li in chunks[0]:
        y = _table_row(page1, y, li)
    page1.text(50, y - 10, "(continued on next page)", size=8)

    page2 = doc.add_page()
    y = TOP_Y
    page2.text(50, y, f"Invoice {inv.invoice_number} -- continued", size=11, bold=True)
    y -= 20
    y = _table_header(page2, y)
    for li in chunks[1]:
        y = _table_row(page2, y, li)
    page2.text(50, y - 10, "(continued on next page)", size=8)

    page3 = doc.add_page()
    y = TOP_Y
    page3.text(50, y, f"Invoice {inv.invoice_number} -- continued", size=11, bold=True)
    y -= 20
    y = _table_header(page3, y)
    for li in chunks[2]:
        y = _table_row(page3, y, li)
    _totals(page3, inv, y)


def _verify_count(pdf_bytes: bytes) -> int:
    """Read /Count back from the actual bytes on disk, not the in-memory
    page list -- the invariant we care about is what a real PDF reader
    would see."""
    match = re.search(rb"/Count (\d+)", pdf_bytes)
    if not match:
        raise AssertionError("no /Count found in generated PDF")
    return int(match.group(1))


def main() -> int:
    results: list[tuple[str, int]] = []
    for key, inv in INVOICES.items():
        doc = PDFDocument()
        if key == "inv-004-multipage":
            render_multipage(doc, inv)
        else:
            render_single_page(doc, inv)
        pdf_bytes = doc.to_bytes()
        out_path = OUT_DIR / inv.document_id
        out_path.write_bytes(pdf_bytes)
        count = _verify_count(pdf_bytes)
        if count != doc.page_count:
            raise AssertionError(
                f"{inv.document_id}: in-memory page_count={doc.page_count} "
                f"but on-disk /Count={count}"
            )
        results.append((inv.document_id, count))

    print("Generated PDFs (filename -> verified /Count page count):")
    for name, count in results:
        print(f"  {name}: {count}")
    multipage_count = dict(results)["inv-004-multipage.pdf"]
    if multipage_count < 3:
        raise AssertionError(
            f"inv-004-multipage.pdf must have >= 3 pages, got {multipage_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
