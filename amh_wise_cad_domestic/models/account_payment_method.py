from odoo import api, models


WISE_CAD_PAYMENT_METHOD_CODE = "wise_cad_domestic"


class AccountPaymentMethod(models.Model):
    _inherit = "account.payment.method"

    @api.model
    def _get_payment_method_information(self):
        information = super()._get_payment_method_information()
        information[WISE_CAD_PAYMENT_METHOD_CODE] = {
            "mode": "multi",
            "type": ("bank",),
            "currency_ids": self.env.ref("base.CAD").ids,
            "country_id": self.env.ref("base.ca").id,
        }
        return information

    @api.model
    def _amh_ensure_wise_cad_journal_links(self):
        """Restore the journal-specific Wise payment method lines if missing.

        The method's own domain matches *every* CAD bank journal in a Canadian
        company, which is too broad once the company has bank journals that are
        not funded through Wise - a PayPal journal, or a card settlement journal.
        This used to add an unwanted Wise line to each of them on every upgrade.

        Journals now opt in through ``amh_wise_cad_domestic_enabled``. Journals
        that already carry a Wise line are flagged automatically, so existing
        databases keep exactly the setup they have today and nothing needs to be
        reconfigured after this upgrade.
        """
        payment_method = self.env.ref(
            "amh_wise_cad_domestic.account_payment_method_wise_cad_domestic",
            raise_if_not_found=False,
        )
        if not payment_method:
            return True

        eligible_journals = self.env["account.journal"].search(
            payment_method._get_payment_method_domain(
                WISE_CAD_PAYMENT_METHOD_CODE
            )
        )
        linked_lines = self.env["account.payment.method.line"].search([
            ("payment_method_id", "=", payment_method.id),
            ("journal_id", "in", eligible_journals.ids),
        ])

        # Journals already using Wise opt in on their own, so upgrading an
        # existing database is a no-op.
        already_using = linked_lines.journal_id.filtered(
            lambda journal: not journal.amh_wise_cad_domestic_enabled
        )
        if already_using:
            already_using.write({"amh_wise_cad_domestic_enabled": True})

        enabled_journals = eligible_journals.filtered("amh_wise_cad_domestic_enabled")
        missing_journals = enabled_journals - linked_lines.journal_id

        detached_lines = self.env["account.payment.method.line"].search([
            ("payment_method_id", "=", payment_method.id),
            ("journal_id", "=", False),
        ])
        for journal in missing_journals:
            detached_line = detached_lines[:1]
            if detached_line:
                detached_line.write({
                    "journal_id": journal.id,
                    "name": payment_method.name,
                })
                detached_lines -= detached_line
            else:
                self.env["account.payment.method.line"].create({
                    "name": payment_method.name,
                    "payment_method_id": payment_method.id,
                    "journal_id": journal.id,
                })
        return True
