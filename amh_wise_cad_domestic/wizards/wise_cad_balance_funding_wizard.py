from odoo import _, api, fields, models
from odoo.exceptions import UserError


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

    def action_confirm_funding(self):
        self.ensure_one()
        batch = self.batch_id
        if not batch:
            raise UserError(_("The Wise batch no longer exists."))

        if batch.wise_payment_status == "completed":
            return batch.action_fund_from_wise_balance()

        # Run the standard validation and Wise batch creation. The context flag
        # makes the funding extension fund the completed batch instead of opening
        # the Wise website.
        return batch.with_context(
            amh_fund_from_wise_balance=True
        ).validate_batch_button()
