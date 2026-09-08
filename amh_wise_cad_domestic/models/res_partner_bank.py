from odoo import api, fields, models


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    amh_wise_cad_account_type = fields.Selection(
        selection=[
            ("CHECKING", "Checking"),
            ("SAVINGS", "Savings"),
        ],
        string="Wise CAD Account Type",
        default="CHECKING",
        help=(
            "Wise requires an account type when creating a Canadian recipient. "
            "Most Canadian business accounts are checking accounts."
        ),
    )

    def _amh_is_wise_cad_account(self):
        """Is this bank account one the CAD domestic flow sends to?

        Deliberately not keyed on ``amh_wise_cad_account_type``: that field
        defaults to CHECKING, so every account in the database carries a value
        and it discriminates nothing. The Canadian financial institution number
        and the partner's country are the only honest signals.
        """
        self.ensure_one()
        return bool(
            self.l10n_ca_financial_institution_number
            or (self.partner_id.country_id.code or "") == "CA"
        )

    @api.depends(
        "wise_account_type",
        "acc_number",
        "clearing_number",
        "l10n_us_bank_account_type",
        "l10n_ca_financial_institution_number",
        "amh_wise_cad_account_type",
        "acc_holder_name",
        "partner_id.country_id",
    )
    def _compute_wise_bank_account(self):
        """Forget the linked Wise recipient whenever its bank details change.

        Canadian accounts only. This override used to blank the field for every
        record, discarding the recipient ids the parent US Direct Deposit module
        caches. US accounts are now handed back to super() untouched.
        """
        canadian = self.filtered(lambda bank: bank._amh_is_wise_cad_account())
        others = self - canadian
        if others:
            super(ResPartnerBank, others)._compute_wise_bank_account()
        for bank in canadian:
            bank.wise_bank_account = False
