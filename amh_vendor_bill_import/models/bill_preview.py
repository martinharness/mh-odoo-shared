"""Show the invoice when an imported bill is opened.

A bill built by this module has its PDF attached but nothing telling Odoo which
attachment to show, so the form opens with an empty preview pane and the
invoice a click away in the chatter. The point of filing the paper on the bill
is being able to look at it, so the PDF is made the main attachment as soon as
it lands.

The trap here is worth recording. Writing ``message_main_attachment_id`` makes
``documents_account`` try to file that attachment as a new document, and when
the attachment is already one - which it always is on the Documents path,
because the file IS the document that was dropped in the folder - Odoo raises
"This attachment is already a document" and the write fails outright. The
documents module's own opt-out, ``no_document=True`` in the context, is what
gets past it.
"""
from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _amh_show_invoice_on_open(self):
        """Make the attached invoice the one the form previews.

        Never overrides a main attachment that is already set - if somebody
        chose what this bill shows, that choice stands.
        """
        for bill in self:
            if bill.message_main_attachment_id:
                continue
            pdf = bill._amh_pdf_attachments()[:1]
            if pdf:
                bill.with_context(no_document=True).write({
                    "message_main_attachment_id": pdf.id,
                })
        return True

    def _amh_hand_over_to(self, bill, parsed):
        result = super()._amh_hand_over_to(bill, parsed)
        bill._amh_show_invoice_on_open()
        return result

    def _amh_adopt_posted(self, bill, parsed):
        result = super()._amh_adopt_posted(bill, parsed)
        bill._amh_show_invoice_on_open()
        return result
