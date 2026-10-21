from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class WiseCadBalanceFundingWizard(models.TransientModel):
    _name = "amh.wise.cad.balance.funding.wizard"
    _description = "Confirm Wise CAD Balance Funding"

    batch_id = fields.Many2one(
        "account.batch.payment",
        string="Batch",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="batch_id.currency_id",
        readonly=True,
    )
    amount = fields.Monetary(
        related="batch_id.amount",
        currency_field="currency_id",
        readonly=True,
    )
    payment_count = fields.Integer(
        string="Transfers",
        compute="_compute_payment_count",
    )
    is_uninitiated = fields.Boolean(compute="_compute_is_uninitiated")
    date_note = fields.Char(compute="_compute_date_note")

    @api.depends("batch_id.payment_ids")
    def _compute_payment_count(self):
        for wizard in self:
            wizard.payment_count = len(wizard.batch_id.payment_ids)

    @api.depends("batch_id.wise_payment_status")
    def _compute_is_uninitiated(self):
        for wizard in self:
            wizard.is_uninitiated = (
                wizard.batch_id.wise_payment_status != "completed"
            )

    @api.depends("batch_id.payment_ids.date")
    def _compute_date_note(self):
        """One line when the batch is not dated today.

        Core says this in a dialog headed "The batch cannot be validated",
        which models/account_batch_payment_warnings.py now drops for a batch
        dated today or earlier. The fact itself is worth saying once - but here,
        where the money is actually about to move, and about the money rather
        than about validation.
        """
        today = fields.Date.context_today(self)
        for wizard in self:
            dates = sorted({
                date
                for date in wizard.batch_id.payment_ids.mapped("date")
                if date and date != today
            })
            if not dates:
                wizard.date_note = False
                continue
            shown = format_date(self.env, dates[0])
            if len(dates) > 1:
                shown = "%s - %s" % (shown, format_date(self.env, dates[-1]))
            wizard.date_note = self.env._(
                "Not every payment in this batch is dated today (%(dates)s). "
                "Wise sends the whole batch as soon as it is funded; the "
                "accounting dates are not changed by that.",
                dates=shown,
            )

    def action_confirm_funding(self):
        self.ensure_one()
        batch = self.batch_id
        if not batch:
            raise UserError(_("The Wise batch no longer exists."))

        if batch.wise_payment_status == "completed":
            return batch.action_fund_from_wise_balance()

        # Run the standard validation and Wise batch creation. The request is
        # WRITTEN on the batch as well as put in the context: when the batch
        # raises a validation warning, core answers with its own dialog and the
        # context does not survive the "Proceed with validation" button on it.
        # See _send_after_validation in models/account_batch_payment_funding.py
        # for what that cost on 3 Oct 2026.
        batch.amh_fund_from_balance_requested = True
        return batch.with_context(
            amh_fund_from_wise_balance=True
        ).validate_batch_button()
