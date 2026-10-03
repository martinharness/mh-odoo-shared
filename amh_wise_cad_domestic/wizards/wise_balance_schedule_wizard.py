from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WiseBalanceScheduleWizard(models.TransientModel):
    _name = "amh.wise.balance.schedule.wizard"
    _description = "Schedule Wise Balance Funding"

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
    scheduled_at = fields.Datetime(
        string="Fund At",
        required=True,
        default=lambda self: self._default_scheduled_at(),
        help="Odoo funds the batch from the Wise balance at this time. The "
             "cron runs every 15 minutes, so funding happens on its first run "
             "at or after the time set here.",
    )

    @api.model
    def _default_scheduled_at(self):
        """Tomorrow at 15:00 in the user's own timezone, skipping the weekend.

        An EFT into Wise takes up to a business day, so today is rarely the
        answer, and a weekend is never it: money moved on a Saturday is money
        that moves on Monday.
        """
        timezone = pytz.timezone(self.env.user.tz or "UTC")
        local = pytz.utc.localize(fields.Datetime.now()).astimezone(timezone)
        target = (local + timedelta(days=1)).replace(
            hour=15, minute=0, second=0, microsecond=0
        )
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return target.astimezone(pytz.utc).replace(tzinfo=None)

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

    def action_schedule_funding(self):
        self.ensure_one()
        batch = self.batch_id
        if not batch:
            raise UserError(_("The Wise batch no longer exists."))
        return batch._amh_schedule_wise_balance_funding(self.scheduled_at)
