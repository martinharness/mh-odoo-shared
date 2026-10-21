"""Core's Wise scheduling warning, on the batches this module funds.

Before a Wise batch is validated, Odoo warns "Wise will send the entire batch
payment as soon as you fund it." / "Scheduled payments are not yet supported.",
and names every payment whose date is not today. It is per-payment, which was
proved on 21 Oct 2026 by re-dating ONE payment of a seventeen-payment batch to
today: the warning came back naming the other sixteen, and the re-dated one had
dropped out of it.

The warning is true, and on a batch prepared on Monday and funded on Wednesday
it is also entirely uninteresting. The money leaves when the batch is funded,
which is what the funding wizard already says in bold before anything is
pressed. What it costs is a second dialog headed "The batch cannot be
validated" - on a batch where check_payments_for_errors returns nothing at all -
answered by a button called "Proceed with validation". Read it the way a new
user reads it and it says the pay-run has failed.

So the warning is dropped for the batches this module funds, but only while
every payment is dated today or earlier.

A payment dated in the FUTURE is the case the warning is actually for: fund it
now and Wise sends now, while the books say next week. Fund from Wise Balance
refuses those outright in _amh_check_wise_balance_funding_ready, but plain
Initiate Payment does not, and there this warning is the only thing in the way.
It stays.

Matched on the title, the same way check_payments_for_errors in
account_batch_payment.py already drops the parent module's USD-only error. If
Odoo ever reworded it the match would stop and the warning would come back -
which is the safe direction for this to fail in, since the result is the dialog
we have today rather than a silence nobody intended.
"""
from odoo import fields, models

from .account_batch_payment_funding import WISE_BALANCE_CURRENCY_BY_METHOD


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    def _amh_wise_scheduling_warning_title(self):
        """Core's own wording, so the comparison is against what it produced."""
        return self.env._(
            "Wise will send the entire batch payment as soon as you fund it."
        )

    def _amh_has_future_dated_payments(self):
        """True if anything in the batch is dated after today.

        The same test the balance-funding guard uses, deliberately: a batch
        that guard would refuse to fund is a batch whose scheduling warning is
        worth reading.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        return any(
            payment.date and payment.date > today
            for payment in self.payment_ids
        )

    def check_payments_for_warnings(self):
        self.ensure_one()
        warnings = super().check_payments_for_warnings()
        if self.payment_method_code not in WISE_BALANCE_CURRENCY_BY_METHOD:
            return warnings
        if self._amh_has_future_dated_payments():
            return warnings
        title = self._amh_wise_scheduling_warning_title()
        return [
            warning for warning in warnings
            if warning.get("title") != title
        ]
