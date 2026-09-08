from odoo import api, models
from odoo.exceptions import UserError

from .account_payment_method import WISE_CAD_PAYMENT_METHOD_CODE


class AccountPayment(models.Model):
    _inherit = "account.payment"

    @api.model
    def _get_method_codes_using_bank_account(self):
        method_codes = super()._get_method_codes_using_bank_account()
        if WISE_CAD_PAYMENT_METHOD_CODE not in method_codes:
            method_codes.append(WISE_CAD_PAYMENT_METHOD_CODE)
        return method_codes

    @api.model
    def _get_method_codes_needing_bank_account(self):
        method_codes = super()._get_method_codes_needing_bank_account()
        if WISE_CAD_PAYMENT_METHOD_CODE not in method_codes:
            method_codes.append(WISE_CAD_PAYMENT_METHOD_CODE)
        return method_codes

    def action_draft(self):
        sent_wise_payments = self.filtered(
            lambda payment:
                payment.batch_payment_id
                and payment.payment_method_code == WISE_CAD_PAYMENT_METHOD_CODE
                and payment.batch_payment_id.wise_payment_status != "uninitiated"
        )
        if sent_wise_payments:
            raise UserError(
                self.env._("You cannot modify a payment that has already been sent to Wise.")
            )
        return super().action_draft()
