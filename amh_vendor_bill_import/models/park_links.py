"""Make the purchase order in a parked bill's note clickable.

A parked bill explains itself in one sentence - "Invoice 198480 is a part
shipment against P70221: 0290T 25 of 50. Receive exactly what arrived and this
will post itself on the next run." - and the very next thing anyone does is go
and look at that order. The note is plain text, so P70221 had to be read off the
screen and typed into the search box.

The order is recorded on the bill when it parks, and the banner is rendered as
HTML with the order's name linked, so it opens in one click.

Two things worth knowing about how this is done:

Finding the order. ``_amh_park`` is handed a finished sentence, not an order,
and there are a dozen call sites that build one. Rather than thread an argument
through all of them, the sentence is searched for names that an actual purchase
order answers to. That is safe because the match is on the exact name - a token
only becomes a link when a purchase order with precisely that name exists - and
because this module wrote the sentence itself and only ever names an order it
has already resolved.

Building the URL. ``/odoo/<action path>/<id>`` is what the web client answers
to, and the path is a field on the action rather than a fixed string, so it is
read from the Purchase Orders action at run time. Both were checked against
this database: ``/odoo/purchase-orders/249`` opens P70221.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# A token that could be a record name: letters and digits, at least three
# digits in it, so ordinary words and short codes are never looked up.
NAME_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9/\-]*\d{3,}[A-Za-z0-9/\-]*\b")

PURCHASE_ACTION = "purchase.purchase_form_action"
PURCHASE_PATH = "purchase-orders"


class AccountMove(models.Model):
    _inherit = "account.move"

    # NOT labelled "Purchase Order": the purchase module already owns that
    # label for account.move.purchase_id, and two fields with one label on a
    # model makes Odoo log a warning on every build - twice over, because
    # account.bank.statement.line inherits these fields too.
    amh_import_po_id = fields.Many2one(
        "purchase.order",
        string="Parked Against Order",
        readonly=True,
        copy=False,
        index=True,
        help="The order a parked bill's note refers to, so it can be opened "
             "from the bill.",
    )
    amh_import_note_html = fields.Html(
        string="Review Note",
        compute="_compute_amh_import_note_html",
        sanitize=False,
    )

    # ------------------------------------------------------------------
    def _amh_purchase_order_url(self, order):
        action = self.env.ref(PURCHASE_ACTION, raise_if_not_found=False)
        path = ""
        if action and "path" in action._fields:
            path = action.path or ""
        return "/odoo/%s/%s" % (path or PURCHASE_PATH, order.id)

    @api.depends("amh_import_note", "amh_import_po_id")
    def _compute_amh_import_note_html(self):
        for move in self:
            note = move.amh_import_note or ""
            body = escape(note)
            order = move.amh_import_po_id
            if order and order.name and order.name in note:
                anchor = Markup('<a href="%s">%s</a>') % (
                    move._amh_purchase_order_url(order), order.name)
                body = body.replace(escape(order.name), anchor)
            move.amh_import_note_html = body

    # ------------------------------------------------------------------
    def _amh_link_parked_order(self, note):
        """Point the bill at the one purchase order its note names."""
        candidates = set(NAME_TOKEN_RE.findall(note or ""))
        orders = self.env["purchase.order"]
        if candidates:
            orders = orders.sudo().search(
                [("name", "in", list(candidates))], limit=2)
        # Exactly one, or none: a sentence naming two orders is not something
        # to guess at, and a wrong link is worse than no link.
        self.amh_import_po_id = orders.id if len(orders) == 1 else False

    def _amh_park(self, note):
        result = super()._amh_park(note)
        try:
            self._amh_link_parked_order(note)
        except Exception:  # noqa: BLE001 - a missing link must never block a park
            _logger.exception(
                "Vendor bill import: could not link the order named in a park note")
        return result
