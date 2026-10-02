"""Freightcom Inc. - the freight-reseller invoice, roughly seven a month.

An expense invoice: no purchase order, nothing received. Freightcom bills a
batch of shipments and prints one summary, so the bill is one line to freight,
which is how Aaron keys it today (54100, "FREIGHTCOM - <invoice date>").

TWO DIFFERENT DOCUMENTS ARRIVE FROM FREIGHTCOM and only one of them is an
invoice. The ``-detail`` PDF is the invoice: it carries the summary block below
and is what gets billed. The other is a credit-card receipt for a single
shipment already paid - it has no invoice subtotal, prints its labels in one
run and its values in another, and says "Invoice Status: Paid". This parser
deliberately does not claim it: without the summary block there is nothing to
prove the money against, so it goes to the digitisation and a human, which is
cheaper than a parser nobody can trust. Two of the first eighteen were that
shape.

THE INVOICE NUMBER IS A TRAP. The invoice prints its own number in the footer
of every page, as "Invoice # / # de facture : FC17369453" - but it ALSO carries
a shipment history that names the PREVIOUS invoice, as "Invoice/Facture #1 -
FC17235979". Taking the first FC-number in the document therefore bills this
month's money against last month's reference. So only the footer label is read,
and every page of it has to agree before the number is accepted. On a
three-page invoice that is three independent confirmations.

Everything else is read off its own bilingual label and then proved: the
amounts due and the invoice total have to agree once what was already paid is
taken off, and where the charge row is readable its components have to add up
to the printed subtotal and its tax column has to equal the tax. Every invoice
seen so far prints 0.00 of tax - Freightcom's charges come through untaxed -
but if one ever does carry tax, the rate is nowhere on the page, so it is
handed over as "blended" and the engine forces the printed figure, exactly as
for the parcel carriers.
"""
import re
from decimal import Decimal

from .base import (ParseError, VendorInvoiceParser, money, normalize, register)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# The footer, on every page. NOT any FC-number anywhere in the document.
FOOTER_NO_RE = re.compile(
    r"Invoice\s*#\s*/\s*#\s*de facture\s*:\s*(FC\d{6,12})", re.IGNORECASE)
DATE_RE = re.compile(
    r"Invoice Date\s*/\s*Date de facture\s*:\s*([A-Za-z]{3})\w*\s+(\d{1,2}),"
    r"\s*(\d{4})", re.IGNORECASE)
SUBTOTAL_RE = re.compile(
    r"Invoice Subtotal\s*/\s*Sous-total de la facture\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE)
TOTAL_RE = re.compile(
    r"Invoice Total\s*/\s*Total de la facture\s*:\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE)
DUE_RE = re.compile(
    r"Total Amount Due\s*/\s*Montant total d[uû]\s*:\s*\$?([\d,]+\.\d{2})"
    r"\s*([A-Z]{3})?", re.IGNORECASE)
PAID_RE = re.compile(
    r"Total Amount Paid\s*/\s*Montant total pay[eé]\s*:\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE)
# The charge row sits between the last column heading and the subtotal label.
# Anchored at both ends rather than hunted for, so it cannot be confused with
# the per-shipment figures further down the page.
ROW_RE = re.compile(
    r"Accessorials\s*/\s*Accessoires\s*Taxes(.{0,120}?)Invoice Subtotal",
    re.IGNORECASE | re.DOTALL)
MONEY_RE = re.compile(r"\$?(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})\b")


class FreightcomParser(VendorInvoiceParser):
    name = "Freightcom Inc"
    marker = "FREIGHTCOM"

    def detect(self, text):
        upper = normalize(text).upper()
        return "FREIGHTCOM" in upper and "SOUS-TOTAL DE LA FACTURE" in upper

    def parse(self, text):
        text = normalize(text)
        if not self.detect(text):
            raise ParseError("not a Freightcom invoice")

        numbers = set(FOOTER_NO_RE.findall(text))
        if len(numbers) != 1:
            raise ParseError(
                "the page footers name %d different invoice numbers (%s), so "
                "the number cannot be trusted"
                % (len(numbers), ", ".join(sorted(numbers)) or "none"))
        invoice_no = numbers.pop()

        stamp = DATE_RE.search(text)
        month = MONTHS.get(stamp.group(1).lower()) if stamp else None
        if not month:
            raise ParseError("no invoice date could be read")
        invoice_date = "%s-%02d-%02d" % (
            stamp.group(3), month, int(stamp.group(2)))

        figures = {}
        for key, regex in (("subtotal", SUBTOTAL_RE), ("total", TOTAL_RE),
                           ("due", DUE_RE), ("paid", PAID_RE)):
            found = regex.search(text)
            if not found:
                raise ParseError(
                    "the %s is not printed where this parser reads it, so the "
                    "invoice layout has changed" % key)
            figures[key] = money(found.group(1))

        currency = (DUE_RE.search(text).group(2) or "CAD").upper()
        if currency != "CAD":
            raise ParseError(
                "this invoice is in %s, and this parser only handles Canadian "
                "dollars" % currency)

        if figures["paid"]:
            raise ParseError(
                "%s of this invoice has already been paid, which is not a "
                "shape this parser is sure of - code it by hand"
                % figures["paid"])
        if figures["total"] - figures["paid"] != figures["due"]:
            raise ParseError(
                "the total of %s less %s paid does not make the %s due"
                % (figures["total"], figures["paid"], figures["due"]))
        if figures["subtotal"] <= 0:
            raise ParseError("the invoice subtotal is %s" % figures["subtotal"])

        tax = figures["total"] - figures["subtotal"]
        if tax < 0:
            raise ParseError(
                "the total of %s is less than the subtotal of %s"
                % (figures["total"], figures["subtotal"]))

        # Independent proof, where the charge row reads cleanly: freight, fuel
        # and accessorials add up to the subtotal and the tax column is the tax.
        # Four figures or nothing - a row of some other shape is not proof of
        # anything, and the three labelled figures above already agree.
        row = ROW_RE.search(text)
        if row:
            tokens = [money("%s.%s" % (m.group(1), m.group(2)))
                      for m in MONEY_RE.finditer(row.group(1))]
            if len(tokens) == 4:
                if sum(tokens[:3]) != figures["subtotal"]:
                    raise ParseError(
                        "the charge row adds up to %s where the subtotal says "
                        "%s" % (sum(tokens[:3]), figures["subtotal"]))
                if tokens[3] != tax:
                    raise ParseError(
                        "the charge row shows %s of tax where the totals imply "
                        "%s" % (tokens[3], tax))

        return {
            "vendor_marker": "Freightcom",
            "invoice_no": invoice_no,
            "invoice_date": invoice_date,
            "expense": True,
            "charges": [{
                "label": "FREIGHTCOM - %s" % invoice_date,
                "amount": figures["subtotal"],
                "tax_rate": "blended" if tax else None,
            }],
            "pass_through": [],
            "po_candidates": [],
            "backorder_of": None,
            "lines": [],
            "subtotal": figures["subtotal"],
            "tax": tax,
            "total": figures["total"],
        }


register(FreightcomParser())
