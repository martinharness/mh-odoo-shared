"""Harness Hardware Inc. (Wallenstein, ON).

Their invoices come out of a fixed-width report writer, so the PDF text layer
lands in stable character columns. Parsing by column is exact; parsing by
whitespace is not, because descriptions run into the price column.

Column boundaries were measured across 38 consecutive invoices (Jul 30 -
Aug 13 2026) and this parser reproduces all 38 - every line code, shipped
quantity, unit price, line total, subtotal, HST and total - with no
exceptions. If the vendor changes the layout, the totals cross-check at the
bottom of ``parse`` fails and the bill is parked rather than guessed at.
"""
import re
from decimal import Decimal

from .base import ParseError, VendorInvoiceParser, register

# character columns in the extracted text layer
C_ORDERED = (0, 6)
C_SHIPPED = (6, 12)
C_BACKORD = (12, 18)
C_CODE = (19, 32)
C_DESC = (32, 64)
C_PRICE = (68, 77)
C_UM = (77, 82)
C_TOTAL = 82

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

HDR_RE = re.compile(r"^[A-Z][a-z]{2} ?\d{1,2}/\d{2}\s")
INVNO_RE = re.compile(r"([A-Z][a-z]{2} ?\d{1,2}/\d{2})\s+(\d{6})\s*$")
TOTALS_RE = re.compile(r"^\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
BACKORDER_RE = re.compile(r"Back order inv#\s*(\d+)")
DATE_RE = re.compile(r"([A-Z][a-z]{2}) ?(\d{1,2})/(\d{2})$")


def _num(raw):
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def _int(raw):
    val = _num(raw)
    return int(val) if val is not None else None


def _date(raw):
    match = DATE_RE.match((raw or "").strip())
    if not match:
        return None
    return "20%s-%02d-%02d" % (match.group(3), MONTHS[match.group(1)], int(match.group(2)))


class HarnessHardwareParser(VendorInvoiceParser):
    name = "Harness Hardware Inc."
    marker = "HARNESS HARDWARE INC."

    def detect(self, text):
        return self.marker in (text or "").upper()

    def parse(self, text):
        if not self.detect(text):
            raise ParseError("not a Harness Hardware invoice")

        out = {
            "vendor_marker": self.name,
            "invoice_no": None,
            "invoice_date": None,
            "customer_code": None,
            "po_candidates": [],
            "backorder_of": None,
            "lines": [],
            "subtotal": None,
            "tax": None,
            "total": None,
        }
        in_body = False

        for raw in (text or "").split("\n"):
            line = raw.rstrip()
            if not line.strip():
                continue

            match = INVNO_RE.search(line)
            if match and out["invoice_no"] is None:
                out["invoice_date"] = _date(match.group(1))
                out["invoice_no"] = match.group(2)
                continue

            match = BACKORDER_RE.search(line)
            if match:
                out["backorder_of"] = match.group(1)
                continue

            match = TOTALS_RE.match(line)
            if match and len(line) > 60:
                out["subtotal"] = _num(match.group(1))
                out["tax"] = _num(match.group(2))
                out["total"] = _num(match.group(3))
                continue

            # order header: "Aug 12/26   Aug 12/26   70038    187419   PU ... Net 30 Days"
            if not in_body and HDR_RE.match(line) and "Net" in line:
                out["date_ordered"] = _date(line[0:12].strip())
                out["date_shipped"] = _date(line[12:24].strip())
                po = line[24:33].strip()
                out["order_no"] = line[33:42].strip() or None
                # printed bare as "70038" but "P00007" on the P000xx series
                if po:
                    out["po_candidates"] = ([po] if po.upper().startswith("P")
                                            else [po, "P" + po])
                in_body = True
                continue

            if not in_body:
                stripped = line.strip()
                if out["customer_code"] is None and re.fullmatch(r"[A-Z]{2,6}", stripped):
                    out["customer_code"] = stripped
                continue

            code = line[C_CODE[0]:C_CODE[1]].strip()
            ordered = _int(line[C_ORDERED[0]:C_ORDERED[1]])
            if not code or ordered is None:
                continue

            total_txt = line[C_TOTAL:].strip()
            line_total = _num(total_txt)
            out["lines"].append({
                "code": code,
                "description": line[C_DESC[0]:C_DESC[1]].strip(),
                "qty_ordered": ordered,
                "qty_shipped": _int(line[C_SHIPPED[0]:C_SHIPPED[1]]) or 0,
                "qty_backordered": _int(line[C_BACKORD[0]:C_BACKORD[1]]) or 0,
                "price": _num(line[C_PRICE[0]:C_PRICE[1]]),
                "uom": line[C_UM[0]:C_UM[1]].strip() or None,
                "line_total": line_total,
                "note": total_txt if (line_total is None and total_txt) else None,
            })

        if not out["invoice_no"]:
            raise ParseError("no invoice number found")
        if out["total"] is None:
            raise ParseError("no totals row found")
        if not any(l["price"] is not None for l in out["lines"]):
            raise ParseError("no priced line items found")

        # cross-checks: if the layout shifted, these are what catch it
        charged = [l for l in out["lines"] if l["line_total"] is not None]
        if sum(l["line_total"] for l in charged) != out["subtotal"]:
            raise ParseError("line totals do not sum to the printed subtotal")
        if out["subtotal"] + out["tax"] != out["total"]:
            raise ParseError("subtotal plus tax does not equal the printed total")
        return out


register(HarnessHardwareParser())
