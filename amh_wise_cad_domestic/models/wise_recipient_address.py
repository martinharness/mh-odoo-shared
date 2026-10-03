"""Give Wise a physical street where one exists, and say so plainly where none does.

Wise refuses a recipient whose first address line is a post box::

    address.firstLine: Please use a physical business address. P.O. boxes
    cannot be used. (Value: firstLine, Box 1234)

Dozens of the suppliers in the database this was written for carry a box in
``street``, and they are two quite different problems wearing the same error
message.

The fixable ones already hold the real address; the box is simply next to it -
``1065 Example ST, BOX 76``, ``P.O. Box 86, 3533 Sample St.``, ``Box 390, 154
HWY 7B`` - or the street sits on ``street2`` while ``street`` holds only the
box. Dropping the box fragment there sends Wise the address the vendor actually
has, which is what should have gone out in the first place.

The rest have no physical address anywhere in Odoo: ``Box 1234``, and nothing
else. Nothing can be removed, because there is nothing to remove it *to*. The
address is precisely what Wise checks the recipient against, so putting
something invented or blank in its place would be answering a compliance
question with data we do not have - and a blank line is rejected too. Those are
reported by ``check_payments_for_errors`` instead, before the batch starts, so
they surface as an ordinary Odoo validation naming the vendor rather than as a
half-built batch on Wise.

The test for "is what remains a real address" is whether it contains a digit.
That reads crude and is in fact exactly the distinction being drawn: a street
address carries a number and a postal station does not, so ``Box 400 Station
D``, ``P.O. BOX 4900, STATION A`` and ``Box 3500, RPO Example`` reduce to
``Station D``, ``STATION A`` and ``RPO Example`` and are refused, rather than
being passed off as somewhere a courier could stand.
"""
import re

from odoo import models

from .account_batch_payment_us_key import WISE_US_PAYMENT_METHOD_CODE
from .account_payment_method import WISE_CAD_PAYMENT_METHOD_CODE

WISE_METHOD_CODES = (WISE_CAD_PAYMENT_METHOD_CODE, WISE_US_PAYMENT_METHOD_CODE)

# A post box and its number, however it is spelled. The trailing digits are
# required so that a street genuinely named Boxwood, or a bare word "box", is
# left alone; the optional brackets catch "840 Example Street (P.0 Box 1240)",
# where the O has been typed as a zero.
WISE_PO_BOX = re.compile(
    r"\(?\s*\b(?:p\s*[.\s]\s*[o0]\s*[.\s]?\s*box|po\s*box|post\s+office\s+box|box)\b"
    r"[\s#]*[0-9]+[A-Za-z\-]*\s*\)?",
    re.IGNORECASE,
)


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    @staticmethod
    def _amh_tidy_street(value):
        """Close up the punctuation left behind when a box fragment is removed."""
        value = re.sub(r"\(\s*\)", " ", value or "")
        value = re.sub(r"\s*,\s*", ", ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip(" ,;-")

    @staticmethod
    def _amh_is_physical_street(value):
        """Does this look like somewhere a courier could stand?

        A street address carries a number. A postal station - "Station A",
        "Stn Main", "RPO Example" - does not, and is exactly what is left
        over when a box is stripped out of a mailing address.
        """
        return bool(value) and bool(re.search(r"\d", value))

    def _amh_wise_physical_street(self, partner):
        """The street to give Wise, or an empty string if the partner has none.

        An address with no post box in it is returned untouched - this only ever
        edits addresses that would otherwise be refused.
        """
        street = (partner.street or "").strip()
        if street and not WISE_PO_BOX.search(street):
            return street
        for source in (street, (partner.street2 or "").strip()):
            if not source:
                continue
            candidate = self._amh_tidy_street(WISE_PO_BOX.sub(" ", source))
            if self._amh_is_physical_street(candidate):
                return candidate
        return ""

    def _amh_prepare_wise_cad_recipient_data(self, payment):
        data = super()._amh_prepare_wise_cad_recipient_data(payment)
        address = (data.get("details") or {}).get("address")
        if isinstance(address, dict):
            street = self._amh_wise_physical_street(payment.partner_id)
            if street:
                address["firstLine"] = street
        return data

    def check_payments_for_errors(self):
        errors = super().check_payments_for_errors()
        if self.payment_method_code not in WISE_METHOD_CODES:
            return errors

        # Only the box-shaped ones. A partner with no address at all is already
        # reported by the existing recipient-address check, and saying it twice
        # would just make the list harder to read.
        boxed = self.payment_ids.filtered(
            lambda payment: payment.partner_id
            and WISE_PO_BOX.search(payment.partner_id.street or "")
            and not self._amh_wise_physical_street(payment.partner_id)
        )
        if boxed:
            errors.append({
                "title": self.env._(
                    "Wise will not send to a P.O. box; a physical address is required."),
                "records": boxed,
                "help": self.env._(
                    "These vendors have only a post box on file: %(vendors)s. Wise "
                    "checks the recipient against this address, so it has to be a "
                    "real street. Ask them for one and put it on the vendor's "
                    "Address, then this batch will go through.",
                    vendors=", ".join(sorted(set(boxed.mapped("partner_id.display_name")))),
                ),
            })
        return errors
