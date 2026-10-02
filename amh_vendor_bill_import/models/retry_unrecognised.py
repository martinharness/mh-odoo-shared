"""Give a new parser a second look at the bills that predate it.

A draft the engine could not read is marked ``not_recognised``, and the import
cron deliberately leaves those alone - otherwise every bill someone is keying by
hand would have its PDF re-read every fifteen minutes for as long as it sat
there, and a Link+ invoice is a nine-megabyte PDF.

That is right until a parser is added. The five carrier parsers went live with
two carrier bills already sitting in the inbox marked unreadable, and nothing
would ever have looked at either of them again.

So the marker is cleared once a day, off the back of the digest cron. Anything
still unreadable is marked unreadable again on the next import run, at a cost of
one read a day rather than ninety-six. Nothing else about the bill is touched,
and a parked bill keeps its reason - only the "nothing here for me" marker is
lifted.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _amh_retry_unrecognised(self):
        drafts = self.search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "draft"),
            ("amh_import_state", "=", "not_recognised"),
        ])
        if drafts:
            _logger.info(
                "Vendor bill import: offering %s unreadable draft(s) to the "
                "parsers again", len(drafts))
            drafts.write({"amh_import_state": False})
        return True

    @api.model
    def _amh_send_review_digest(self):
        self._amh_retry_unrecognised()
        return super()._amh_send_review_digest()
