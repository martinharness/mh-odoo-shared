from odoo import api, models
from odoo.tools.misc import formatLang


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.depends_context("amh_show_residual")
    def _compute_display_name(self):
        """Append the balance still due, when asked for by context.

        Choosing between two invoices for the same customer is guesswork if all
        you can see is INV/20636 and INV/20641. The amount is the thing that
        tells you which one the cheque is for.

        Scoped behind a context key so this only affects fields that ask for it.
        Every other invoice reference in Odoo is left alone.
        """
        super()._compute_display_name()
        if not self.env.context.get("amh_show_residual"):
            return
        for move in self:
            if move.amount_residual:
                amount = formatLang(
                    self.env, move.amount_residual, currency_obj=move.currency_id,
                )
                move.display_name = "%s — %s" % (move.display_name, amount)
