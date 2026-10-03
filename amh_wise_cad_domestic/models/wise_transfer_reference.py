"""Keep the transfer reference inside the character set Wise accepts.

Wise refuses a transfer whose reference carries anything outside
``[a-zA-Z0-9- ]``::

    paymentReference: Please use only English letters and numbers. Accents,
    special characters or punctuation are not recognised
    (Value: parameter_invalid_format, [a-zA-Z0-9- ]*)

Odoo builds that reference from the payment memo, which is whatever the bills
being settled happen to carry. Two shapes break it here, and both are ordinary:
a vendor's own punctuation (one vendor bills ``INV26_1067``) and the
comma-separated list Odoo writes when one payment settles several bills
(``64145, 64163``, or twenty-six invoice numbers in a single payment to one
vendor). Neither is going to stop happening, so the reference is cleaned on the
way out rather than policed on the way in.

Every disallowed character becomes a single space, runs of whitespace collapse,
and the ends are trimmed. A space is what those separators mean anyway -
``198074, 198271`` reads as ``198074 198271`` and ``INV26_1067`` as
``INV26 1067`` - and it is how references already come out on the Wise side.

Length is deliberately left alone. Wise did not object to it, references far
longer than these have been accepted before, and whatever truncation happens
further down should carry on doing what it already does. Cleaning can only
shorten a reference, never lengthen it. An empty result is valid too: Wise's own
pattern ends in ``*``.
"""
import logging
import re

from odoo import models

_logger = logging.getLogger(__name__)

# Anything Wise will not take in a transfer reference.
WISE_REFERENCE_REJECTS = re.compile(r"[^A-Za-z0-9 -]+")


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    @staticmethod
    def _amh_clean_wise_reference(reference):
        """Return *reference* with everything Wise rejects turned into a space."""
        spaced = WISE_REFERENCE_REJECTS.sub(" ", reference or "")
        return re.sub(r"\s+", " ", spaced).strip()

    def _prepare_wise_transfer_data(self, payment, *args, **kwargs):
        """Clean the reference on the payload Odoo is about to send to Wise.

        Both spellings are handled because the reference is the one field whose
        home has moved between Wise API versions, and a silent miss here shows
        up as the same rejected batch rather than anything louder.
        """
        data = super()._prepare_wise_transfer_data(payment, *args, **kwargs)
        if not isinstance(data, dict):
            return data

        touched = False
        details = data.get("details")
        if isinstance(details, dict) and details.get("reference"):
            details["reference"] = self._amh_clean_wise_reference(details["reference"])
            touched = True
        if data.get("reference"):
            data["reference"] = self._amh_clean_wise_reference(data["reference"])
            touched = True

        if not touched:
            _logger.debug(
                "amh_wise_cad_domestic: no reference found on the Wise transfer "
                "payload for payment %s; nothing cleaned.", payment.id)
        return data
