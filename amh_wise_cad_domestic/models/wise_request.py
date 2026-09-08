from odoo.addons.l10n_us_direct_deposit.models.wise_request import Wise


class WiseCad(Wise):
    """Wise API additions used by the CAD domestic payment workflow."""

    def fund_batch_group_from_balance(self, batch_group_id):
        return self._Wise__make_api_request(
            "POST",
            (
                f"/v3/profiles/{self.profile_id}/batch-payments/"
                f"{batch_group_id}/payments"
            ),
            {"type": "BALANCE"},
        )
