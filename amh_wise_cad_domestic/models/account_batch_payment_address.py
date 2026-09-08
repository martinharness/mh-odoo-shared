from odoo import models


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    @staticmethod
    def _amh_wise_cad_has_recipient_address(partner):
        return bool(
            partner.street
            and partner.city
            and partner.zip
            and partner.state_id.code
            and partner.country_id.code
        )

    def _amh_prepare_wise_cad_recipient_data(self, payment):
        recipient_data = super()._amh_prepare_wise_cad_recipient_data(payment)
        recipient_data["details"]["address"]["state"] = payment.partner_id.state_id.code
        return recipient_data
