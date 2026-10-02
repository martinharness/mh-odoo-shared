"""Vendor invoice parser registry.

A parser turns the text layer of one vendor's invoice PDF into the neutral
dict the import engine consumes. Everything vendor-specific lives here; the
engine in ``account_move.py`` knows nothing about any particular layout.

The dict a parser returns:

    {
        "vendor_marker":  str   - short label, for logging only
        "invoice_no":     str   - the vendor's own invoice number
        "invoice_date":   str   - "YYYY-MM-DD"
        "po_candidates":  [str] - purchase order names to try, best first
        "backorder_of":   str or None
        "subtotal":       Decimal
        "tax":            Decimal
        "total":          Decimal
        "lines": [
            {
                "code":         str      - the vendor's product code
                "description":  str
                "qty_ordered":  int
                "qty_shipped":  int
                "price":        Decimal  - unit price AS PRINTED
                "uom":          str      - "EA", "C", ...
                "line_total":   Decimal or None
                "note":         str or None   - e.g. "Included"
            },
            ...
        ],
    }

Lines with ``line_total`` of None and a ``note`` are commentary, not charges.

A carrier or customs broker has no purchase order behind it, nothing is
received and there is nothing to match, so those parsers set ``expense`` and
describe the money instead of the goods:

    {
        "expense":   True
        "charges": [
            {
                "label":     str            - what goes on the bill line
                "amount":    Decimal
                "tax_rate":  Decimal, "blended", or None
                "role":      "brokerage" to code it somewhere other than the
                             default expense account (optional)
            },
            ...
        ],
        "pass_through": [{"label": str, "amount": Decimal}, ...]
            - money the vendor advanced on our behalf, most often the GST the
              broker paid CBSA. It is not an expense and carries no tax.
        "tax_breakdown": [(rate, amount), ...]   - optional, where the invoice
            prints what it charged at each rate, so each tax line can be set to
            the vendor's own figure rather than to Odoo's arithmetic.
    }

``tax_rate`` of "blended" means the invoice printed a tax total but never said
what it was charged on - true of every parcel carrier, whose tax follows the
delivery province. The engine resolves it to a configured rate and forces the
amount to the printed figure; see ``expense_bill.py``.
"""

import re
from decimal import Decimal, ROUND_HALF_UP


class ParseError(Exception):
    """The document looked like ours but did not parse. Park it, never guess."""


class VendorInvoiceParser:
    name = "base"

    def detect(self, text):
        """True if this parser owns the document."""
        raise NotImplementedError

    def parse(self, text):
        """Return the neutral dict, or raise ParseError."""
        raise NotImplementedError


_REGISTRY = []


def register(parser):
    _REGISTRY.append(parser)
    return parser


def find_parser(text):
    for parser in _REGISTRY:
        try:
            if parser.detect(text):
                return parser
        except Exception:
            continue
    return None


class MissingPdfLibrary(Exception):
    """No usable PDF text extractor on this server."""


def _pdf_reader_class():
    """The PDF reader this Odoo actually has.

    Odoo does not ship one library, it ships whichever suits the Python it is
    running on: ``pypdf`` on 3.13 and later, ``PyPDF2`` 2.x on 3.11 and 3.12,
    ``PyPDF2`` 1.26 on anything older. Declaring ``pypdf`` as an external
    dependency therefore blocks installation on most servers, so the module
    declares none and adapts here instead.

    Verified: PyPDF2 2.12.1 and pypdf produce byte-identical text on all 38
    reference invoices, so the column offsets in the parsers hold for both.
    PyPDF2 1.26 is a different extractor and its layout is NOT verified - if
    the server is old enough to be using it, the parsers' arithmetic
    cross-checks will fail and every bill will park rather than post wrong.
    """
    import importlib

    for module_name, attribute in (
        ("pypdf", "PdfReader"),
        ("PyPDF2", "PdfReader"),
        ("PyPDF2", "PdfFileReader"),
    ):
        try:
            return getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError):
            continue
    raise MissingPdfLibrary(
        "no pypdf or PyPDF2 available to read the invoice PDF")


def _page_text(page):
    for method in ("extract_text", "extractText"):
        func = getattr(page, method, None)
        if func:
            try:
                return func() or ""
            except Exception:
                return ""
    return ""


def extract_text(pdf_bytes):
    """Text layer of a PDF, pages joined with newlines.

    Returns "" for a scan with no text layer, which leaves the bill to the OCR.
    """
    import io

    reader = _pdf_reader_class()(io.BytesIO(pdf_bytes))
    return "\n".join(_page_text(page) for page in reader.pages)


# ---------------------------------------------------------------------------
# shared reading helpers
#
# Carrier invoices are laid out in columns, and no PDF text extractor puts a
# column back together the way a reader does: labels and figures often arrive
# as two separate runs, and the run order moves when the layout does. So the
# carrier parsers never read "the number to the right of this label". They read
# the numbers in document order and then prove which is which by arithmetic -
# the components have to add up to the printed total, and a tax has to be its
# own rate times its own base. A layout change therefore turns into a failed
# proof and a parked bill with a reason, never a posted bill with wrong money.
# ---------------------------------------------------------------------------
MONEY_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\d%])")
RATE_RE = re.compile(r"(\d{1,2}\.\d{2,3})\s*%")

# The only GST/HST rates in force in Canada. A rate outside this set means the
# parser has misread a figure, or the invoice carries a tax this module has no
# business guessing at - Quebec's QST above all, which is not an input tax
# credit for an Ontario-only registrant.
CANADIAN_RATES = (Decimal("5"), Decimal("13"), Decimal("14"), Decimal("15"))


def normalize(text):
    """Whitespace-collapsed text, so the parsers do not depend on line breaks."""
    return re.sub(r"\s+", " ", text or "").strip()


def money(raw):
    return Decimal(str(raw).replace(",", "").replace("$", "").strip())


def money_tokens(text):
    """Every money figure in the text, in document order."""
    return [Decimal("%s.%s" % (m.group(1).replace(",", ""), m.group(2)))
            for m in MONEY_RE.finditer(text)]


def tax_of(base, rate):
    """``base`` taxed at ``rate`` percent, rounded to the cent the way a carrier
    rounds it."""
    return (base * rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def runs_summing_to(tokens, total, low=2, high=5):
    """Every contiguous run of ``tokens`` that adds up to ``total``.

    Returned as ``(net, taxes)`` pairs, keeping only the runs whose first
    figure is the largest - a charge subtotal is always bigger than the tax on
    it, and without that rule a run can be found reading the same three figures
    starting one place further along.

    This is the loosest of the readers here and the one to be most careful
    with: a run adding up is evidence, not proof. Use it only where the summary
    block has no component breakdown of its own for it to trip over.
    """
    found = []
    for start in range(len(tokens)):
        running = Decimal("0")
        for length in range(1, high + 1):
            if start + length > len(tokens):
                break
            running += tokens[start + length - 1]
            if length < low or running != total:
                continue
            run = tokens[start:start + length]
            if any(value >= run[0] for value in run[1:]):
                continue
            found.append((run[0], run[1:]))
    return found
