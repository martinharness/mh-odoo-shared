from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    amh_wise_cad_domestic_enabled = fields.Boolean(
        string="Wise CAD Domestic EFT",
        copy=False,
        help="Offer Wise CAD Domestic EFT as an outgoing payment method on this journal.\n"
             "Tick this only on journals whose money actually leaves through Wise. Without it the "
             "method line is not maintained here, which keeps Wise off journals that merely happen "
             "to be CAD bank journals - a PayPal or card settlement journal, for example.",
    )
