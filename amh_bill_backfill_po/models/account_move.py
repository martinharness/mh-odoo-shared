"""Backfill the purchase order and receipt a vendor bill should have had.

A counter purchase arrives as paper: eighteen vise grips from the hardware
store, an invoice, and no purchase order in Odoo. The bill is read off the PDF
and lands in Draft, and only at posting time does it become clear the stock has
to come in first - which means leaving the bill for the Purchase app, raising an
RFQ, confirming it, finding the receipt in Inventory, validating it, and coming
back. Re-uploading the PDF would only make a second draft.

So the bill, which is the document that actually exists, becomes the source: one
button on it builds the purchase order and the receipt behind it, links them to
the bill, and leaves the bill in draft to be posted as before.

The other direction is the commoner case. A bill usually arrives BEFORE the
goods, auto-completing it from the order brings its lines in at quantity zero,
and re-picking the order later does nothing because those lines already carry
purchase_line_id. Reload PO Data fills them in once the warehouse has received,
and amh_po_receipt_state says which bills are still waiting without opening any
of them.

WHY THIS IS SAFE HERE, AND WHAT WOULD CHANGE IF IT WERE NOT
-----------------------------------------------------------
Under PERIODIC valuation a receipt writes no journal entry at all: no
stock-interim account to clear, no three-way match to satisfy, no
price-difference entry. A storable line codes to Cost of Goods Sold whether a
purchase order sits behind it or not, so the accounts are identical before and
after this button, and the order of operations is a convenience question rather
than an accounting one.

It stops being a convenience question under PERPETUAL (real-time) valuation,
where the receipt posts its own entry against a stock-interim account that only
a PO-linked bill line clears. Writing purchase_line_id here does not recode the
line, and a backdated validated move keeps a journal entry dated today, so the
result would be wrong quietly. Rather than guess, the button REFUSES when any
product category on the bill is set to real-time valuation - see
_amh_backfill_perpetual_categories. Making it work there means recoding the bill
line to the interim account and dating the stock valuation layer, neither of
which periodic valuation needs.

It does build the documents in the order perpetual valuation would require
anyway: purchase order, then receipt, then the bill posted by hand.

The purchase order is built FROM the bill, so its price is the bill's price by
construction and there is never a variance to explain.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    amh_can_backfill_po = fields.Boolean(
        string="Can Backfill Purchase Order",
        compute="_compute_amh_can_backfill_po",
        help="Set on a draft vendor bill carrying storable product lines that "
             "are not linked to a purchase order line yet.",
    )

    amh_po_receipt_state = fields.Selection(
        selection=[
            ("waiting", "Waiting on receipt"),
            ("partial", "Partly received"),
            ("ready", "Ready to bill"),
        ],
        string="Receipt",
        compute="_compute_amh_po_receipt_state",
        store=True,
        help="On a draft vendor bill whose lines came from a purchase order: "
             "whether what it bills has been received yet. Stored, so it is a "
             "column and a filter on the Bills list, and it updates the moment "
             "a receipt is validated.",
    )
    amh_can_reload_po = fields.Boolean(
        string="Can Reload Purchase Order",
        compute="_compute_amh_can_reload_po",
        help="Set when a purchase order line behind this bill has quantities "
             "received but not yet billed.",
    )

    def _amh_backfill_lines(self):
        """The bill lines that want a purchase order line behind them.

        Storable products only. A service, a product Odoo does not count, and
        the productless lines OCR writes straight to an expense account are all
        deliberately left off: a receipt line that cannot be received is worse
        than no receipt at all, and an expensed shop supply has nothing to
        receive in the first place.
        """
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.product_id
            and line.product_id.is_storable
            and not line.purchase_line_id
            and line.quantity > 0
        )

    @api.depends(
        "state",
        "move_type",
        "invoice_line_ids.product_id",
        "invoice_line_ids.purchase_line_id",
        "invoice_line_ids.quantity",
    )
    def _compute_amh_can_backfill_po(self):
        for move in self:
            move.amh_can_backfill_po = bool(
                move.move_type == "in_invoice"
                and move.state == "draft"
                and move._amh_backfill_lines()
            )

    def _amh_reload_lines(self):
        """Bill lines whose purchase order line has something left to bill.

        qty_to_invoice is received minus invoiced, and it already counts THIS
        draft line, so a line sitting at 10 of 32 received reports 22 and lands
        on 32 rather than 42. Only a positive figure is taken: a line billed
        for more than was received reports a negative, and quietly reducing a
        quantity somebody typed off the paper invoice is not this button's job.
        """
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.purchase_line_id
            and line.purchase_line_id.qty_to_invoice > 0
        )

    @api.depends(
        "state",
        "move_type",
        "invoice_line_ids.quantity",
        "invoice_line_ids.purchase_line_id",
        "invoice_line_ids.purchase_line_id.qty_to_invoice",
    )
    def _compute_amh_can_reload_po(self):
        for move in self:
            move.amh_can_reload_po = bool(
                move.move_type == "in_invoice"
                and move.state == "draft"
                and move._amh_reload_lines()
            )

    @api.depends(
        "state",
        "move_type",
        "invoice_line_ids.quantity",
        "invoice_line_ids.purchase_line_id",
        "invoice_line_ids.purchase_line_id.qty_to_invoice",
    )
    def _compute_amh_po_receipt_state(self):
        """Has what this bill bills actually arrived?

        qty_to_invoice is STORED on purchase.order.line, so this recomputes by
        itself the moment a receipt is validated - no cron, no polling, and
        nothing is ever written to the bill to make the status appear.
        """
        for move in self:
            move.amh_po_receipt_state = move._amh_po_receipt_state_value()

    def _amh_po_receipt_state_value(self):
        self.ensure_one()
        if self.move_type != "in_invoice" or self.state != "draft":
            return False
        seen = set()
        for line in self.invoice_line_ids:
            if line.display_type != "product" or not line.purchase_line_id:
                continue
            if line.purchase_line_id.qty_to_invoice > 0:
                seen.add("pullable")
            elif line.quantity > 0:
                # Already carries what was receivable - pulled, or keyed by hand.
                seen.add("billed")
            else:
                seen.add("waiting")
        if not seen:
            # No purchase order behind it: a carrier, card-fee or repair bill,
            # which has nothing to wait for and should not be flagged as if it
            # had.
            return False
        if seen == {"waiting"}:
            return "waiting"
        if "waiting" in seen:
            return "partial"
        return "ready"

    def action_amh_reload_po_quantities(self):
        """Pull the receivable quantities off the purchase order.

        Quantities only. The price stays as it is, because the price has to
        agree with the paper invoice and the purchase order's is only what was
        expected. Lines are never ADDED either: the bill's shape came off the
        paper, and a line the vendor did not invoice has no business appearing
        on it just because the order grew.
        """
        self.ensure_one()
        if self.move_type != "in_invoice" or self.state != "draft":
            raise UserError(self.env._(
                "Only a draft vendor bill can be reloaded from its purchase "
                "order."
            ))
        lines = self._amh_reload_lines()
        if not lines:
            raise UserError(self.env._(
                "Nothing to pull in. Either the goods behind this bill have "
                "not been received yet, or its lines already carry everything "
                "that has been."
            ))

        # Snapshot BEFORE writing: writing a quantity recomputes qty_invoiced
        # and therefore qty_to_invoice on the order line, so two bill lines
        # sharing one order line would otherwise read a figure the first write
        # had already moved.
        wanted = {
            line.id: line.quantity + line.purchase_line_id.qty_to_invoice
            for line in lines
        }
        changes = []
        for line in lines:
            changes.append((line.product_id.display_name, line.quantity, wanted[line.id]))
            line.quantity = wanted[line.id]

        self.message_post(body=self.env._(
            "Quantities pulled from %(order)s: %(changes)s. Prices are "
            "untouched - check the total against the invoice before posting.",
            order=", ".join(sorted(set(lines.purchase_line_id.order_id.mapped("name")))),
            changes="; ".join(
                "%s %s -> %s" % (name, before, after) for name, before, after in changes
            ),
        ))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": self.env._("Quantities reloaded"),
                "message": self.env._(
                    "%(count)s line(s) updated from the purchase order.",
                    count=len(changes),
                ),
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def _amh_backfill_datetime(self):
        """The bill's date as a datetime that reads as that date in any timezone.

        date_order and the stock move dates are datetimes stored in UTC and
        shown in the user's timezone. Midnight UTC on 23 October shows as the
        evening of the 22nd in Eastern, which would date every backfilled
        document the day before the invoice. Noon is clear of both ends of the
        day for any timezone this company works in.
        """
        self.ensure_one()
        return fields.Datetime.to_datetime(self.invoice_date).replace(hour=12)

    def _amh_open_vendor_purchase_orders(self):
        """Confirmed orders for this vendor with something still to bill.

        Read BEFORE the backfill creates its own, and then reported in the
        chatter rather than used to refuse the button: the goods may well have
        been ordered on a purchase order somebody else raised, and the honest
        answer to that is to say so on the bill, not to block the button and
        leave the user with a dialog whose only choice is Close.
        """
        self.ensure_one()
        return self.env["purchase.order"].search([
            ("partner_id", "child_of", self.partner_id.commercial_partner_id.id),
            ("state", "=", "purchase"),
            ("company_id", "=", self.company_id.id),
            ("invoice_status", "=", "to invoice"),
        ])

    def _amh_backfill_perpetual_categories(self, lines):
        """Categories on this bill that value stock in real time.

        This is built for PERIODIC valuation, where a receipt writes no journal
        entry and the bill's own coding is the whole accounting story. Under
        real-time valuation the receipt credits a stock-interim account that
        only a PO-linked bill line debits - and writing purchase_line_id here
        does not recompute that line's account, so the interim account would be
        left hanging. Backdating an already-validated move would also leave its
        journal entry dated today. Both are wrong QUIETLY, which is the worst
        way to be wrong about stock, so this refuses instead of guessing.
        """
        return lines.product_id.categ_id.filtered(
            lambda category: category.property_valuation == "real_time"
        )

    def _amh_pair_backfill_lines(self, lines, order):
        """Bill line <-> order line, by creation order.

        The order lines were created from `lines` in one go and in that order,
        so their ids ascend together. Matching by product instead would be
        wrong the first time a bill carries the same product twice at two
        prices, which is exactly what a counter invoice does.
        """
        order_lines = order.order_line.sorted("id")
        if len(order_lines) != len(lines):
            raise UserError(self.env._(
                "The purchase order came back with %(orders)s lines for "
                "%(bills)s bill lines, so they cannot be matched up.",
                orders=len(order_lines), bills=len(lines),
            ))
        pairs = list(zip(lines, order_lines))
        for line, order_line in pairs:
            if line.product_id != order_line.product_id:
                raise UserError(self.env._(
                    "The purchase order lines do not line up with the bill "
                    "lines (%(bill)s against %(order)s).",
                    bill=line.product_id.display_name,
                    order=order_line.product_id.display_name,
                ))
        return pairs

    def _amh_assert_backfill_prices(self, lines, order):
        """Refuse an order Odoo has repriced off the vendor price list.

        Compared at the Product Price precision, not the currency's: hardware
        is bought at four decimals (0.0855 a triglide) and rounding that to
        cents would call 0.0855 and 0.09 the same price.
        """
        precision = self.env["decimal.precision"].precision_get("Product Price")
        for line, order_line in self._amh_pair_backfill_lines(lines, order):
            if float_compare(
                order_line.price_unit, line.price_unit, precision_digits=precision
            ) != 0:
                raise UserError(self.env._(
                    "Odoo priced %(product)s at %(got)s on the purchase order "
                    "where the bill says %(want)s - the vendor price list is "
                    "overriding the bill. The order has not been confirmed and "
                    "nothing has been received.",
                    product=line.product_id.display_name,
                    got=order_line.price_unit,
                    want=line.price_unit,
                ))

    def _amh_create_backfill_order(self, lines):
        self.ensure_one()
        when = self._amh_backfill_datetime()
        order = self.env["purchase.order"].create({
            "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
            "company_id": self.company_id.id,
            "date_order": when,
            "partner_ref": self.ref or "",
            "order_line": [
                (0, 0, {
                    "product_id": line.product_id.id,
                    "name": line.name or line.product_id.display_name,
                    "product_qty": line.quantity,
                    "product_uom_id": (line.product_uom_id or line.product_id.uom_id).id,
                    "price_unit": line.price_unit,
                    "tax_ids": [(6, 0, line.tax_ids.ids)],
                    "date_planned": when,
                })
                for line in lines
            ],
        })

        # price_unit, product_qty and date_planned on purchase.order.line are
        # computed-and-stored with precompute, so the vendor price list can win
        # over the values handed to create(). Write them again from the bill and
        # then CHECK, because an order whose price is not the bill's price is
        # the one thing this must never produce.
        for line, order_line in self._amh_pair_backfill_lines(lines, order):
            order_line.write({
                "price_unit": line.price_unit,
                "product_qty": line.quantity,
                "date_planned": when,
            })
        self._amh_assert_backfill_prices(lines, order)

        order.button_confirm()
        if order.state == "to approve":
            order.button_approve(force=True)
        if order.state != "purchase":
            raise UserError(self.env._(
                "Purchase order %(order)s was created but is %(state)s rather "
                "than confirmed, so nothing has been received. Confirm it by "
                "hand, then press the button again to receive and link it.",
                order=order.name, state=order.state,
            ))
        return order

    def _amh_validate_backfill_receipt(self, order):
        """Receive the whole order, dated the bill.

        Every line came off the bill, so the vendor has already delivered all
        of it: there is no partial case here and no backorder to leave behind.
        """
        self.ensure_one()
        when = self._amh_backfill_datetime()
        pickings = order.picking_ids.filtered(
            lambda picking: picking.state not in ("done", "cancel")
        )
        if not pickings:
            raise UserError(self.env._(
                "Confirming %(order)s produced no receipt to validate. Check "
                "that these products are set to be received into stock.",
                order=order.name,
            ))
        for picking in pickings:
            picking.scheduled_date = when
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()

        unfinished = pickings.filtered(lambda picking: picking.state != "done")
        if unfinished:
            raise UserError(self.env._(
                "Receipt %(receipts)s did not complete - it is still "
                "%(state)s. The purchase order %(order)s has been confirmed, "
                "so finish the receipt in Inventory and press the button again "
                "to link the bill.",
                receipts=", ".join(unfinished.mapped("name")),
                state=unfinished[0].state,
                order=order.name,
            ))

        # Backdated AFTER validation: Odoo stamps a done move with the moment
        # it was validated, which would put goods bought on the 23rd into stock
        # today. Under periodic valuation these dates drive the stock reports
        # and nothing at all in the ledger.
        pickings.write({"date_done": when})
        pickings.move_ids.write({"date": when})
        pickings.move_ids.move_line_ids.write({"date": when})
        return pickings

    def _amh_link_backfill_order(self, order, lines):
        self.ensure_one()
        for line, order_line in self._amh_pair_backfill_lines(lines, order):
            line.purchase_line_id = order_line.id
        if not self.invoice_origin:
            self.invoice_origin = order.name

    def _amh_post_backfill_note(self, order, pickings, open_orders):
        self.ensure_one()
        body = self.env._(
            "Backfilled from this bill: purchase order %(order)s confirmed, "
            "receipt %(receipts)s validated and dated %(date)s. The bill lines "
            "are linked to the order; the bill itself is untouched and still "
            "draft.",
            order=order.name,
            receipts=", ".join(pickings.mapped("name")),
            date=format_date(self.env, self.invoice_date),
        )
        if open_orders:
            body += self.env._(
                " Worth checking: %(vendor)s already had %(count)s confirmed "
                "purchase order(s) with quantities not yet billed (%(names)s). "
                "If these goods were on one of those, this has duplicated it - "
                "cancel %(order)s and link the bill to the existing order "
                "instead.",
                vendor=self.partner_id.display_name,
                count=len(open_orders),
                names=", ".join(open_orders.mapped("name")),
                order=order.name,
            )
        self.message_post(body=body)

    def action_amh_backfill_po_and_receipt(self):
        self.ensure_one()
        if self.move_type != "in_invoice" or self.state != "draft":
            raise UserError(self.env._(
                "Only a draft vendor bill can have a purchase order "
                "backfilled onto it."
            ))
        if not self.partner_id:
            raise UserError(self.env._("Set the vendor on the bill first."))
        if not self.invoice_date:
            raise UserError(self.env._(
                "Set the bill date first: the purchase order and the receipt "
                "are both dated from it."
            ))
        lines = self._amh_backfill_lines()
        if not lines:
            raise UserError(self.env._(
                "Nothing on this bill needs receiving. Only storable products "
                "not already linked to a purchase order are backfilled - a "
                "service, or a line coded straight to an expense account, has "
                "nothing to receive."
            ))

        perpetual = self._amh_backfill_perpetual_categories(lines)
        if perpetual:
            raise UserError(self.env._(
                "This backfills a receipt without touching the ledger, which "
                "is only right where stock is valued PERIODICALLY. These "
                "product categories are set to Automated (real-time) "
                "valuation: %(categories)s. There the receipt posts its own "
                "journal entry and the bill line would have to be recoded to "
                "the stock-interim account, so raise the purchase order and "
                "the receipt in the Purchase app instead.",
                categories=", ".join(perpetual.mapped("display_name")),
            ))

        open_orders = self._amh_open_vendor_purchase_orders()
        order = self._amh_create_backfill_order(lines)
        pickings = self._amh_validate_backfill_receipt(order)
        self._amh_link_backfill_order(order, lines)
        self._amh_post_backfill_note(order, pickings, open_orders)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": self.env._("Purchase order and receipt created"),
                "message": self.env._(
                    "%(order)s confirmed and %(receipts)s received. Post the "
                    "bill when you are ready.",
                    order=order.name,
                    receipts=", ".join(pickings.mapped("name")),
                ),
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
