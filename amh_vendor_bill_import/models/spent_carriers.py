"""Delete the empty drafts this module makes to hold one PDF.

Nothing can be attached to a bill that does not exist yet, so the Documents
intake creates a draft first and reads the PDF off it second. When the import
succeeds the real bill is built, the PDF is moved onto it, and the draft is
left with nothing: no vendor, no lines, no attachment, no money.

``_amh_hand_over_to`` cancels that draft rather than deleting it, and on the
mail path it is right to - there the same record IS the email, and the vendor's
own message is sitting in its chatter. On the Documents path it is a container
this module made seconds earlier and has since emptied, and cancelling it only
fills the Vendor Bills list with zero-value cancelled rows that have to be
cleared out by hand.

So they are deleted, deliberately narrowly. A draft has to have been superseded
by a bill this module built, be cancelled, have never been numbered, carry no
lines, have no attachment and no document still pointing at it, and have
nothing in its chatter but Odoo's own notifications. A single message from a
person or a vendor is enough to leave it alone for good.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Anything a person or a vendor put there. Odoo's own "Vendor Bill Created"
# notice is message_type 'notification' and is not worth keeping on its own.
HUMAN_MESSAGE_TYPES = ("email", "comment")


class AccountMove(models.Model):
    _inherit = "account.move"

    def _amh_is_spent_carrier(self):
        """True if this draft is an emptied container and nothing else."""
        self.ensure_one()
        if not self.amh_successor_move_id or self.state != "cancel":
            return False
        if self.name not in (False, "", "/"):
            return False
        if self.posted_before or self.line_ids:
            return False
        if self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "account.move"),
                ("res_id", "=", self.id)]):
            return False
        if self.env["mail.message"].sudo().search_count([
                ("model", "=", "account.move"),
                ("res_id", "=", self.id),
                ("message_type", "in", HUMAN_MESSAGE_TYPES)]):
            return False
        if "documents.document" in self.env and self.env[
                "documents.document"].sudo().search_count([
                    ("res_model", "=", "account.move"),
                    ("res_id", "=", self.id)]):
            return False
        return True

    @api.model
    def _amh_discard_spent_carriers(self, limit=200):
        candidates = self.sudo().search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "cancel"),
            ("amh_successor_move_id", "!=", False),
        ], limit=limit)
        spent = candidates.filtered(lambda move: move._amh_is_spent_carrier())
        if spent:
            _logger.info(
                "Vendor bill import: deleting %s emptied carrier draft(s): %s",
                len(spent), spent.ids)
            spent.unlink()
        return True

    @api.model
    def _amh_run_vendor_bill_import(self, limit=100):
        result = super()._amh_run_vendor_bill_import(limit=limit)
        # After the passes, not before: a carrier is only spent once the bill
        # it was read into has been built and the PDF moved across.
        self._amh_discard_spent_carriers()
        return result
