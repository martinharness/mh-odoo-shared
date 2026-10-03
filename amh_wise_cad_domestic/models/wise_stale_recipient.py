"""Say which vendor a Wise "we couldn't find an account" error is about.

Wise identifies a recipient by a number, and when it cannot find one that
number is the whole of what it tells you::

    Failed to create a quote on Wise:
    - We couldn't find an account with that ID (1,234,567,890). (Value: 1234567890)

The number is not a mystery on this side: it is the Wise recipient id Odoo
stored on a vendor's bank account, so the database can turn it straight back
into a name. In the live run that produced this, it was one vendor out of
fourteen in a batch, which stopped there with four transfers already created and
nothing on screen to say which vendor to go and look at.

So the message is rewritten to name the vendor and say what to do about it. The
rest of the batch's recipients are checked against Wise at the same time, so a
second stale one is found now rather than on the next run - but ONLY on the
error path, where the batch has already failed. A recipient list that came back
short can therefore never stop a batch that would otherwise have gone out.

This wraps ``_send_after_validation``, which is the common hook both the US and
the CAD rails pass through, rather than either module's sending method. It has
to be imported LAST in ``models/__init__.py``: Odoo puts the most recently
registered override first, and this one has to sit outside the others to see
what they raise.
"""
import logging
import re

from odoo import _, models
from odoo.exceptions import UserError

from .wise_request import WiseCad

_logger = logging.getLogger(__name__)

# Wise prints the id twice - once with thousands separators, once bare. The bare
# one in "(Value: ...)" is the one that matches what is stored here.
MISSING_ACCOUNT_RE = re.compile(
    r"couldn't find an account with that ID.*?\(Value:\s*(\d+)\)",
    re.IGNORECASE | re.DOTALL,
)


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    def _send_after_validation(self):
        try:
            return super()._send_after_validation()
        except UserError as error:
            explained = self._amh_wise_explain_missing_recipient(error)
            if not explained:
                raise
            raise UserError(explained) from error

    def _amh_wise_explain_missing_recipient(self, error):
        """The same error with the vendor's name in it, or nothing.

        Returns None whenever anything is not as expected, so an error this
        cannot improve is re-raised exactly as Wise wrote it.
        """
        original = str(error or "")
        match = MISSING_ACCOUNT_RE.search(original)
        if not match:
            return None
        wise_id = match.group(1)
        bank = self.env["res.partner.bank"].sudo().search(
            [("wise_bank_account", "=", wise_id)], limit=1
        )
        if not bank:
            return None

        partner_name = bank.partner_id.display_name
        parts = [
            original,
            "",
            _(
                "That ID is the Wise recipient stored against %(partner)s in Odoo. "
                "Wise does not have a recipient with it any more - the usual causes "
                "are that it was deleted in Wise, or that it was created on a "
                "different Wise profile.",
                partner=partner_name,
            ),
            _(
                "To clear it: open %(partner)s, and on the bank account Odoo pays "
                "press Forget beside the Wise Account ID. Initiating this batch "
                "again then matches or creates the recipient in Wise and stores the "
                "new id. Transfers already created for this batch are kept - the run "
                "picks up where it stopped.",
                partner=partner_name,
            ),
        ]

        others = self._amh_wise_recipients_wise_does_not_know(bank)
        if others:
            parts += [
                "",
                _(
                    "Wise does not list a recipient for these either, so they are "
                    "worth forgetting in the same pass: %s",
                    ", ".join(others),
                ),
            ]
        return "\n".join(parts)

    def _amh_wise_recipients_wise_does_not_know(self, known_bad):
        """Other bank accounts in this batch whose stored Wise id is not on Wise.

        Advisory only. If the listing comes back empty, short, or in a shape this
        does not recognise, nothing is claimed: a wrong name here would send
        someone to fix an account that is perfectly fine.
        """
        try:
            banks = self.payment_ids.partner_bank_id.filtered(
                lambda bank: bank.wise_bank_account
            ) - known_bad
            if not banks:
                return []

            wise_api = WiseCad(self.company_id)
            listing = wise_api.get_recipients()
            if wise_api.has_errors(listing):
                return []
            rows = listing.get("content") if isinstance(listing, dict) else listing
            if not isinstance(rows, list) or not rows:
                return []
            on_wise = {
                str(row.get("id")) for row in rows if isinstance(row, dict)
            }

            missing = [
                bank for bank in banks if bank.wise_bank_account not in on_wise
            ]
            # The listing has to look believable before it is used to accuse
            # anybody: the id we already know is bad must be absent from it, and
            # at least one other recipient in this batch must be present. A page
            # that arrived truncated fails both and says nothing.
            if known_bad.wise_bank_account in on_wise:
                return []
            if len(missing) >= len(banks):
                return []
            return sorted(bank.partner_id.display_name for bank in missing)
        except Exception:  # noqa: BLE001 - explaining an error must not raise one
            _logger.exception(
                "Wise: could not check the other recipients in batch %s", self.id
            )
            return []
