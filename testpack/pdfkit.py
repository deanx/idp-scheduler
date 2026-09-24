"""A minimal, dependency-free PDF 1.4 writer.

Why this exists: the pack needs exact control over page count (inv-004
must be verifiably >= 3 pages via /Count) and exact text content, and the
task deliberately rules out adding reportlab/fpdf as new dependencies.
`textutil`/`cupsfilter` were considered (both are on macOS already) but
were rejected: they paginate a plain-text/RTF input using their own
layout engine, which gives no reliable, portable guarantee of the exact
page count or exact line placement a regression test-data pack needs --
and the whole point of inv-004 is a *guaranteed* >=3-page document. A
few hundred lines of plain PDF syntax (page tree, one built-in Helvetica
font, one content stream per page of positioned `Tj` text-show
operators) is well within "simple text-and-table layout" and gives
byte-exact, reproducible control instead.

This is intentionally tiny: text placement only (`Td`/`Tj`), one
built-in font (Helvetica, no embedding needed -- it's one of the 14
standard PDF fonts every reader must support), and simple horizontal
rules for table separators. No compression, no images, no embedded
fonts. That is all this test-data pack needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PAGE_WIDTH = 612  # US Letter, points
PAGE_HEIGHT = 792


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


@dataclass
class Page:
    ops: list[str] = field(default_factory=list)

    def text(self, x: float, y: float, s: str, size: int = 10, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        self.ops.append(f"BT /{font} {size} Tf {x:.2f} {y:.2f} Td ({_escape(s)}) Tj ET")

    def hline(self, x1: float, x2: float, y: float, width: float = 0.5) -> None:
        self.ops.append(f"{width:.2f} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")


class PDFDocument:
    def __init__(self) -> None:
        self._pages: list[Page] = []

    def add_page(self) -> Page:
        page = Page()
        self._pages.append(page)
        return page

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def to_bytes(self) -> bytes:
        if not self._pages:
            raise ValueError("a PDF must have at least one page")

        n_pages = len(self._pages)
        # Object numbering: 1=Catalog 2=Pages 3=Font-Regular 4=Font-Bold
        # then for page i (0-based): page_obj = 5 + 2*i, content_obj = 6 + 2*i
        catalog_obj = 1
        pages_obj = 2
        font_regular_obj = 3
        font_bold_obj = 4

        def page_obj(i: int) -> int:
            return 5 + 2 * i

        def content_obj(i: int) -> int:
            return 6 + 2 * i

        kids = " ".join(f"{page_obj(i)} 0 R" for i in range(n_pages))

        objects: dict[int, bytes] = {
            catalog_obj: f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode(),
            pages_obj: (
                f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode()
            ),
            font_regular_obj: (
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
            ),
            font_bold_obj: (
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
            ),
        }

        for i, page in enumerate(self._pages):
            stream = "\n".join(page.ops).encode("latin-1")
            objects[content_obj(i)] = (
                f"<< /Length {len(stream)} >>\nstream\n".encode()
                + stream
                + b"\nendstream"
            )
            objects[page_obj(i)] = (
                f"<< /Type /Page /Parent {pages_obj} 0 R "
                f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_regular_obj} 0 R "
                f"/F2 {font_bold_obj} 0 R >> >> "
                f"/Contents {content_obj(i)} 0 R >>"
            ).encode()

        max_obj = max(objects)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: dict[int, int] = {}
        for num in range(1, max_obj + 1):
            offsets[num] = len(out)
            body = objects.get(num, b"<< >>")
            out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_offset = len(out)
        out += f"xref\n0 {max_obj + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for num in range(1, max_obj + 1):
            out += f"{offsets[num]:010d} 00000 n \n".encode()

        out += (
            f"trailer\n<< /Size {max_obj + 1} /Root {catalog_obj} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
        return bytes(out)

    def write(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())
