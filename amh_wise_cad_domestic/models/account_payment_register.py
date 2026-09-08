from odoo import Command, _, api, models
from odoo.exceptions import UserError

from .account_payment_method import WISE_CAD_PAYMENT_METHOD_CODE


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    @api.depends("can_edit_wizard", "payment_method_line_id")
    def _compute_group_payment(self):
        super()._compute_group_payment()
        for wizard in self:
            if (
                wizard.payment_method_code == WISE_CAD_PAYMENT_METHOD_CODE
                and wizard.can_group_payments
            ):
                # Default to one transfer per vendor/bank account. The user may
                # still untick Group Payments when separate transfers are needed.
                wizard.group_payment = True

    def action_create_wise_batch(self):
        """Create accounting payments and their draft Wise batch in one step."""
        self.ensure_one()

        if self.payment_method_code != WISE_CAD_PAYMENT_METHOD_CODE:
            raise UserError(
                _("Select the Wise CAD Domestic EFT payment method first.")
            )
        if self.payment_type != "outbound":
            raise UserError(_("Wise CAD Domestic EFT is only available for vendor payments."))
        if self.currency_id != self.env.ref("base.CAD"):
            raise UserError(_("Wise CAD Domestic EFT only supports CAD payments."))
        if not self.journal_id:
            raise UserError(_("Select a bank journal before creating the Wise batch."))

        if self.missing_account_partners:
            raise UserError(
                _(
                    "Every selected vendor must have a Canadian bank account before "
                    "the Wise batch can be created."
                )
            )
        if self.untrusted_payments_count:
            raise UserError(
                _(
                    "Every recipient bank account must be trusted before the Wise "
                    "batch can be created."
                )
            )

        payments = self._create_payments()
        if not payments:
            raise UserError(_("No payments were created for the selected bills."))

        unexpected_payments = payments.filtered(
            lambda payment:
                payment.journal_id != self.journal_id
                or payment.payment_method_line_id != self.payment_method_line_id
        )
        if unexpected_payments:
            raise UserError(
                _(
                    "The selected bills did not all create payments with the same "
                    "Wise payment method and bank journal."
                )
            )

        batch = self.env["account.batch.payment"].create({
            "batch_type": "outbound",
            "date": self.payment_date,
            "journal_id": self.journal_id.id,
            "payment_method_id": self.payment_method_line_id.payment_method_id.id,
            "payment_ids": [Command.set(payments.ids)],
        })

        return {
            "name": _("Wise Batch"),
            "type": "ir.actions.act_window",
            "res_model": "account.batch.payment",
            "res_id": batch.id,
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "account_batch_payment.view_batch_payment_form"
                    ).id,
                    "form",
                )
            ],
            "target": "current",
        }
