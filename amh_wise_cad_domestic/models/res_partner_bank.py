from odoo import _, api, fields, models


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

    def action_amh_forget_wise_recipient(self):
        """Drop the stored Wise recipient id so the next payment re-links it.

        The id belongs to Wise, not to this database, and it can stop being
        valid on their side - the recipient deleted, or created on a profile the
        company no longer pays from. When that happens every payment to the
        vendor dies at the quote with "We couldn't find an account with that ID",
        and until now there was nothing anywhere in Odoo to clear: the field is
        readonly, computed, and on no form.

        Clearing is the ONLY edit offered here, and that is deliberate. A
        mistyped recipient id is a payment into a stranger's account; an empty
        one costs the next run a lookup and nothing else. So the field is shown
        and can be emptied, never typed into.
        """
        for bank in self:
            forgotten = bank.wise_bank_account
            if not forgotten:
                continue
            bank.wise_bank_account = False
            bank.partner_id.message_post(
                body=_(
                    "Wise recipient %(recipient)s forgotten. The next Wise payment "
                    "to this bank account will match or create the recipient in "
                    "Wise and store the new id.",
                    recipient=forgotten,
                )
            )
        return True
