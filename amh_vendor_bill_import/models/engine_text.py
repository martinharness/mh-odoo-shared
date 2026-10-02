"""See what the import engine actually reads out of a PDF.

A parser can only be written against text someone can look at, and until now
the only text anyone could look at from outside the server was
``ir.attachment.index_content`` - what Odoo extracted for full-text search.
The engine does not use that. It runs the PDF through pypdf itself, and the
two do not agree: they produce the same figures in the same order, but they
break lines in different places, so anything anchored on a label sitting next
to its value can work against one and fail against the other.

That is exactly how the first five carrier parsers shipped with header regexes
that found nothing. The bills parked rather than posting anything wrong, which
is the design working, but it should not have taken a live run to find out.

So this returns the engine's own reading, sliced, for an internal user. It is a
diagnostic and nothing else: no cron calls it, it writes nothing, and it reads
only attachments the calling user could open anyway.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError

from .parsers import base as parser_base


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def amh_engine_text(self, attachment_id, start=0, length=4000,
                        normalized=True):
        """The text the parsers are given for one PDF attachment.

        Returns ``{"length": int, "text": str}`` - the slice asked for, and how
        much there was to slice, so a caller can page through it.
        """
        if not self.env.user._is_internal():
            raise AccessError(_("Vendor bill text is for internal users."))
        attachment = self.env["ir.attachment"].browse(int(attachment_id))
        attachment.check("read")
        text = parser_base.extract_text(attachment.sudo().raw)
        if normalized:
            text = parser_base.normalize(text)
        start = max(0, int(start))
        return {
            "length": len(text),
            "text": text[start:start + max(0, int(length))],
        }
