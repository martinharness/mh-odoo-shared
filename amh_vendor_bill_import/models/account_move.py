"""Vendor-agnostic import engine.

Everything that knows about a particular vendor's paper lives in ``parsers/``.
This file only knows how to turn a parsed invoice into a posted Odoo bill, and
when to refuse.

Two shapes of invoice arrive here. Most bill goods against a purchase order and
take the long road below: match the lines, receive the stock, price the bill off
what was ordered. Carrier and customs-broker invoices have no order behind them
and are handed to ``expense_bill.py`` instead.
"""
import logging
import re
from odoo import _, api, fields, models
from odoo.tools import float_compare, float_is_zero

from .parsers import base as parser_base

_logger = logging.getLogger(__name__)

STATES = [
    ("not_recognised", "No parser"),
    ("parked", "Needs review"),
    ("imported", "Imported"),
]

# account.move.line.display_type is NOT False for an ordinary line. In Odoo 19
# the selection has no False member at all - a product line is 'product', and
# the rest are tax, payment_term, line_section, rounding and so on. Testing it
# for truthiness therefore skips EVERY line, which is exactly what happened:
# _amh_apply_amounts forced the tax correctly and silently repriced nothing, so
# bills only ever agreed when the purchase order price already matched the
# invoice. Verified with fields_get against the live database.
PRODUCT_LINE_TYPES = (False, "product")


def _code_keys(code):
    """Spellings of a product code that should be treated as the same item.

    The vendor prints ``0018W34N`` where Odoo carries ``018W-34N``, and
    ``0409DBL`` where Odoo carries ``409D-BK``. Trailing P on the plastic
    range is decorative (``102358P`` is Odoo's ``1023-58``). Leading zeros are
    ambiguous - stripping all of them turns ``0018W34N`` into ``18W34N``,
    which matches nothing - so both readings are offered.
    """
    raw = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if not raw:
        return set()
    keys = set()
    for stem in {raw, raw.lstrip("0"), re.sub(r"^0", "", raw)}:
        if not stem:
            continue
        for variant in {stem, re.sub(r"P$", "", stem)}:
            keys.add(variant)
            keys.add(re.sub(r"(BLK|BL)$", "BK", variant))
            keys.add(re.sub(r"CB$", "BC", variant))
    return {k for k in keys if k}


def _code_segments(code):
    """Break an Odoo product code into its meaningful runs.

    ``705-28SS`` is 705 / 28 / SS, not 705 / 28SS - the size and the finish are
    separate facts, and splitting only on the hyphen would hide the 28 inside a
    token that never appears in the vendor's description.
    """
    out = []
    for chunk in re.split(r"[^A-Za-z0-9]+", (code or "").upper()):
        out += [s for s in re.findall(r"\d+|[A-Z]+", chunk) if s]
    return out


class AccountMove(models.Model):
    _inherit = "account.move"

    amh_import_state = fields.Selection(
        STATES, string="Bill Import", copy=False, index=True, readonly=True)
    amh_import_note = fields.Text(string="Import Note", copy=False, readonly=True)
    amh_vendor_invoice_no = fields.Char(
        string="Vendor Invoice No.", copy=False, readonly=True, index=True)
    amh_successor_move_id = fields.Many2one(
        "account.move", string="Posted As", copy=False, readonly=True,
        help="The real bill this draft turned into. The draft is only ever a "
             "carrier for the PDF; the bill that gets posted is built from the "
             "purchase order, so it is a different record.")

    # ------------------------------------------------------------------
    # entry points
    # ------------------------------------------------------------------
    @api.model
    def _amh_run_vendor_bill_import(self, limit=100):
        """Cron entry point. Never raises - one bad bill must not stop the rest.

        Three passes, in this order:

        1. new PDFs dropped in the Documents inbox folder become drafts and are
           imported (``documents_intake.py``);
        2. every draft still outstanding is retried - that covers bills that
           arrived by email, and ones parked earlier now that the receipt has
           been made or the missing order line added;
        3. documents whose bill has since posted are filed away.

        Pass 2 must not be skipped when Documents is absent, and pass 1 and 3
        are no-ops in that case, so the same cron serves either setup.
        """
        self._amh_scan_documents()
        self._amh_clear_stale_parks()
        drafts = self.search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "draft"),
            ("amh_import_state", "in", [False, "parked"]),
        ], limit=limit, order="id")
        for draft in drafts:
            try:
                draft._amh_import_one()
                self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
                self.env.cr.rollback()
                _logger.exception("Vendor bill import failed on %s", draft.id)
                try:
                    draft._amh_park(_("Import crashed: %s") % exc)
                    self.env.cr.commit()
                except Exception:
                    self.env.cr.rollback()
        self._amh_settle_documents()
        return True

    @api.model
    def _amh_clear_stale_parks(self):
        """Drop the Needs Review flag from bills that are no longer drafts.

        The cron only ever revisits drafts, so a bill parked and then finished
        by hand keeps its warning forever and sits in the review list long after
        the work is done. Nothing is re-imported here and no accounting field is
        touched - only the marker is brought back in line with reality.
        """
        stale = self.search([
            ("move_type", "=", "in_invoice"),
            ("amh_import_state", "=", "parked"),
            ("state", "=", "posted"),
        ])
        if stale:
            stale.write({
                "amh_import_state": "imported",
                "amh_import_note": _(
                    "Posted by hand; the import no longer owns this bill."),
            })
        return True

    @api.model
    def _amh_send_review_digest(self):
        """One mail listing what the import would not touch. Silent when clean."""
        parked = self.search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "draft"),
            ("amh_import_state", "=", "parked"),
        ], order="invoice_date, id")
        if not parked:
            return True
        recipient = self.env["ir.config_parameter"].sudo().get_param(
            "amh_vendor_bill_import.digest_email") or self.env.user.email
        if not recipient:
            return True
        rows = "".join(
            "<li><b>%s</b> %s &mdash; %s</li>" % (
                move.amh_vendor_invoice_no or move.name,
                move.partner_id.display_name or "",
                move.amh_import_note or "")
            for move in parked)
        self.env["mail.mail"].sudo().create({
            "subject": "Vendor bills needing review (%s)" % len(parked),
            "email_to": recipient,
            "body_html": "<p>These vendor bills could not be posted automatically:</p>"
                         "<ul>%s</ul>" % rows,
            "auto_delete": True,
        }).send()
        return True

    def action_amh_import_now(self):
        for move in self:
            move._amh_import_one()
        return True

    # ------------------------------------------------------------------
    def _amh_park(self, note):
        self.write({"amh_import_state": "parked", "amh_import_note": note})
        return False

    def _amh_pdf_attachments(self):
        return self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"),
            ("res_id", "=", self.id),
            ("mimetype", "=", "application/pdf"),
        ], order="id")

    # ------------------------------------------------------------------
    def _amh_import_one(self):
        self.ensure_one()

        parsed = parser = None
        for attachment in self._amh_pdf_attachments():
            try:
                text = parser_base.extract_text(attachment.raw)
            except Exception:
                continue
            parser = parser_base.find_parser(text)
            if not parser:
                continue
            try:
                parsed = parser.parse(text)
            except parser_base.ParseError as exc:
                return self._amh_park(
                    _("%s invoice could not be read: %s") % (parser.name, exc))
            break

        if not parsed:
            # Left completely alone - OCR and humans still own this one.
            #
            # Only ever set this on a bill that has no verdict yet. The bills
            # this engine BUILDS carry no PDF of their own, so a later pass
            # lands here and would otherwise overwrite "parked" and wipe the
            # reason - which is how four bills ended up sitting in draft with
            # no explanation of what was wrong with them.
            if not self.amh_import_state:
                self.write({"amh_import_state": "not_recognised"})
            return False

        partner = self._amh_find_partner(parser)
        if not partner:
            return self._amh_park(_("No vendor in Odoo named %r.") % parser.name)

        self.write({"amh_vendor_invoice_no": parsed["invoice_no"]})

        duplicate = self.search([
            ("id", "!=", self.id),
            ("move_type", "=", "in_invoice"),
            ("partner_id", "=", partner.id),
            ("ref", "=", parsed["invoice_no"]),
            ("state", "!=", "cancel"),
        ], limit=1)
        # A DRAFT carrying our own marker is not a duplicate - it is the bill
        # this engine built for this same invoice on an earlier run and could
        # not post. Adopt it and try again, rather than parking forever or
        # building a second one: action_create_invoice would return nothing the
        # second time round, because the first draft already consumed the
        # order's uninvoiced quantity. The marker is readonly, so a bill someone
        # keyed in by hand can never be picked up this way.
        adopted = None
        if duplicate:
            if duplicate.state == "draft" and \
                    duplicate.amh_vendor_invoice_no == parsed["invoice_no"]:
                adopted = duplicate
            elif duplicate.state == "posted":
                # Someone got there first. Parking would leave the PDF in the
                # inbox and the bill in the review list over work that is
                # already finished, so file the paper and stand down instead.
                return self._amh_adopt_posted(duplicate, parsed)
            else:
                return self._amh_park(
                    _("Invoice %s is already on %s.")
                    % (parsed["invoice_no"], duplicate.display_name))

        # No purchase order behind this one - a carrier or customs broker.
        # Everything below this point is about matching and receiving goods,
        # none of which applies. See expense_bill.py.
        if parsed.get("expense"):
            return self._amh_import_expense(partner, parsed, adopted)

        charged = [l for l in parsed["lines"] if l["line_total"] is not None]

        inferred = False
        if parsed["po_candidates"]:
            order = self.env["purchase.order"].search([
                ("name", "in", parsed["po_candidates"]),
                ("partner_id", "=", partner.id),
                ("state", "in", ["purchase", "done"]),
            ], limit=1)
            if not order:
                return self._amh_park(_(
                    "Invoice %s quotes P.O. %s, which is not a confirmed purchase order for %s."
                ) % (parsed["invoice_no"], parsed["po_candidates"][0], partner.display_name))
        else:
            matches = self._amh_orders_matching_contents(partner, charged)
            if len(matches) > 1:
                return self._amh_park(_(
                    "Invoice %s prints no P.O. number, and its contents fit more than one "
                    "open order for %s: %s. Say which one it belongs to and this will post."
                ) % (parsed["invoice_no"], partner.display_name,
                     ", ".join(matches.mapped("name"))))
            if not matches:
                return self._amh_park(_(
                    "Invoice %s carries no P.O. number, and no open order for %s matches "
                    "what it bills. Raise a purchase order for it, or code the bill by hand."
                ) % (parsed["invoice_no"], partner.display_name))
            order, inferred = matches, True

        pairs, unmatched = self._amh_match_lines(charged, order)
        if unmatched:
            return self._amh_park(_(
                "Invoice %s bills %s, which did not match any line on %s by product "
                "code or the vendor's part number. Add the vendor's part number to "
                "those products (or the missing line to the order), then this will "
                "post itself."
            ) % (parsed["invoice_no"], ", ".join(unmatched), order.name))

        shortfalls = self._amh_shortfalls(parsed)
        if shortfalls:
            # Receiving the order in full on the strength of a part shipment
            # would book stock the vendor never sent, which is what the
            # shortfall guard is for. But that reasoning stops at the warehouse
            # door: once the shipped quantities are received and still unbilled,
            # there is nothing to receive and this is an ordinary bill. The
            # parked note promises exactly that, so it has to be true.
            gaps = self._amh_receipt_gaps(pairs)
            if gaps:
                return self._amh_park(_(
                    "Invoice %s is a part shipment against %s: %s. Receive exactly what "
                    "arrived and this will post itself on the next run. Right now %s."
                ) % (parsed["invoice_no"], order.name, "; ".join(shortfalls),
                     "; ".join(gaps)))
        else:
            self._amh_receive_in_full(order)

        bill = adopted or self._amh_build_bill(order, parsed, pairs)
        if not bill:
            return self._amh_park(_(
                "Nothing left to bill on %s - its received quantities are already invoiced."
            ) % order.name)
        self.write({"amh_successor_move_id": bill.id})
        if inferred:
            # leave the reasoning on the record: this is the one place the
            # module chose an order the vendor never named
            bill.message_post(body=_(
                "Invoice %s printed no P.O. number. Matched to %s on contents - every "
                "line's product code and ordered quantity agrees, the order carries "
                "nothing the invoice does not bill, and no other open order for %s "
                "fits.") % (parsed["invoice_no"], order.name, partner.display_name))

        problem = bill._amh_apply_amounts(parsed, pairs)
        if problem:
            bill.write({"amh_import_state": "parked", "amh_import_note": problem})
            return self._amh_park(_("Built %s but %s") % (bill.display_name, problem))

        bill.action_post()
        bill.write({"amh_import_state": "imported", "amh_import_note": False})
        self._amh_hand_over_to(bill, parsed)
        return True

    # ------------------------------------------------------------------
    def _amh_find_partner(self, parser):
        Partner = self.env["res.partner"]
        if self.partner_id and parser.name.lower() in (self.partner_id.name or "").lower():
            return self.partner_id
        return Partner.search([("name", "=", parser.name)], limit=1) or \
            Partner.search([("name", "ilike", parser.name)], limit=1)

    def _amh_adopt_posted(self, bill, parsed):
        """The invoice is already posted - file the paper and stand down.

        Happens whenever a bill is keyed or finished by hand before the cron
        reaches it. There is nothing left to import, so parking would only put
        finished work back in front of him every morning and strand the PDF in
        the inbox folder. The carrier is cancelled rather than deleted so the
        original mail and anything written on it stays on the record.
        """
        self._amh_pdf_attachments().write({"res_id": bill.id})
        if not bill.amh_vendor_invoice_no:
            bill.write({"amh_vendor_invoice_no": parsed["invoice_no"]})
        bill.message_post(body=_(
            "Vendor invoice %s was posted by hand. Filing its PDF here."
        ) % parsed["invoice_no"])
        self.write({
            "amh_import_state": "imported",
            "amh_import_note": _("Already posted by hand as %s.") % bill.display_name,
            "amh_successor_move_id": bill.id,
        })
        self.button_cancel()
        return True

    def _amh_orders_matching_contents(self, partner, charged):
        """Open orders whose contents this invoice matches exactly.

        Only ever consulted when the invoice prints no P.O. number at all.
        Choosing an order the vendor never named is the one guess this module
        makes, and a wrong guess would receive stock against the wrong order,
        so the bar is deliberately higher than ordinary line matching:

        * every charged line pairs to a distinct order line **by product code**.
          The description fallback and the one-left-over-each-side rule are not
          allowed here - they exist to resolve the last line of an order we are
          already sure of, and are far too loose to identify one;
        * the ordered quantity on the invoice equals the ordered quantity on
          the order line. The vendor prints what was ordered, so this is an
          independent signal that survives any price change;
        * the order carries nothing the invoice does not bill. Without this a
          small invoice would match any larger order that happens to contain
          its items.

        Returning two or more is not a failure of this method - it means the
        evidence genuinely does not single one out, and the caller parks.
        """
        Order = self.env["purchase.order"]
        if not charged:
            return Order.browse()
        candidates = Order.search([
            ("partner_id", "=", partner.id),
            ("state", "in", ["purchase", "done"]),
            ("invoice_status", "!=", "invoiced"),
        ])
        return Order.browse([
            order.id for order in candidates
            if self._amh_contents_agree(charged, order)])

    def _amh_contents_agree(self, charged, order):
        lines = [l for l in order.order_line if l.product_id and not l.display_type]
        if len(lines) != len(charged):
            return False
        vendor = order.partner_id
        remaining = list(lines)
        for inv_line in charged:
            keys = _code_keys(inv_line["code"])
            hit = next(
                (l for l in remaining if keys & (
                    self._amh_vendor_code_keys(l.product_id, vendor)
                    | _code_keys(l.product_id.default_code))),
                None)
            if hit is None:
                return False
            if float_compare(hit.product_qty, float(inv_line["qty_ordered"] or 0), 2) != 0:
                return False
            remaining.remove(hit)
        return not remaining

    def _amh_vendor_code_keys(self, product, vendor):
        """Normalised spellings of *this vendor's own* part numbers for a product.

        A vendor invoices with its own catalogue codes, which Odoo stores as the
        product's supplier (pricelist) code - a far more reliable match than the
        internal reference, which after a data migration rarely equals what the
        vendor prints (one vendor bills 0246F14N where Odoo now carries
        246F-14NB, and 1124NYOS where Odoo carries 1123S-BO). Run through the
        same _code_keys normalisation as everything else so 0246F14N and 246F14N
        are still treated alike. A product with no supplier line for this vendor
        contributes nothing, and matching simply falls back to the Odoo code.
        """
        keys = set()
        commercial = vendor.commercial_partner_id if vendor else vendor
        for seller in product.seller_ids:
            if not seller.product_code:
                continue
            if commercial and seller.partner_id.commercial_partner_id != commercial:
                continue
            keys |= _code_keys(seller.product_code)
        return keys

    def _amh_match_lines(self, charged, order):
        """Pair each charged invoice line with a purchase order line.

        Exact matches first, for every line, before any fuzzy matching runs:

        1. the vendor's own part number - the product's supplier code for this
           vendor. Authoritative for a vendor invoice, and what the paper
           actually prints;
        2. the product's Odoo internal reference.

        Only lines still unmatched after that fall to the description fallback,
        so a loose description hit can never steal a line another line matches
        exactly. That stranding is real: a Web Slides line billed 1025S1 was
        parked because the Beta Webslides line (code 1025B1S, which does not
        match Odoo's 1025SC-1) grabbed 1025S-1 on description first. There is
        deliberately no "closest price" pass: two lines of the same value pair
        to the same bill total either way, so a wrong guess would balance
        perfectly while receiving against the wrong product. Ambiguity is
        parked instead.
        """
        available = [l for l in order.order_line if l.product_id and not l.display_type]
        vendor = order.partner_id
        taken, pairs, remaining = set(), [], []

        # Phase 1 - exact only: vendor part number, then Odoo internal code.
        for inv_line in charged:
            free = [l for l in available if l.id not in taken]
            keys = _code_keys(inv_line["code"])
            hit = next(
                (l for l in free if keys & self._amh_vendor_code_keys(l.product_id, vendor)),
                None)
            if hit is None:
                hit = next(
                    (l for l in free if keys & _code_keys(l.product_id.default_code)), None)
            if hit is None:
                remaining.append(inv_line)
                continue
            taken.add(hit.id)
            pairs.append((inv_line, hit))

        # Phase 2 - description fallback for whatever is still unmatched.
        leftovers = []
        for inv_line in remaining:
            free = [l for l in available if l.id not in taken]
            hit = self._amh_match_by_description(inv_line, free)
            if hit is None:
                leftovers.append(inv_line)
                continue
            taken.add(hit.id)
            pairs.append((inv_line, hit))

        # last resort: one invoice line and one order line left over, and the
        # money agrees. Two independent signals, so a genuine substitution -
        # a 4 3/4" snap against an order for the 3 1/2" - still gets parked.
        free = [l for l in available if l.id not in taken]
        if len(leftovers) == 1 and len(free) == 1 and self._amh_prices_agree(leftovers[0], free[0]):
            pairs.append((leftovers[0], free[0]))
            leftovers = []

        unmatched = ["%s x%s" % (l["code"], l["qty_shipped"]) for l in leftovers]
        return pairs, unmatched

    @staticmethod
    def _amh_prices_agree(inv_line, po_line, tolerance=0.05):
        """Does the invoice line's money land where the order line says it should?"""
        ordered = inv_line["qty_ordered"] or 0
        shipped = inv_line["qty_shipped"] or 0
        if not ordered or not shipped or not po_line.price_unit:
            return False
        expected = po_line.price_unit * po_line.product_qty * (shipped / float(ordered))
        if float_is_zero(expected, 2):
            return False
        return abs(float(inv_line["line_total"]) - expected) / abs(expected) <= tolerance

    @staticmethod
    def _amh_match_by_description(inv_line, candidates):
        """Fall back to the vendor's printed description.

        Odoo's own code carries the distinguishing detail that the vendor's
        code sometimes drops - ``705-28SS`` against a line the vendor calls
        ``070528S``, described as '# 705 28" Farm Hames Pol. SS'. Every segment
        of the Odoo code has to appear, so the 26" hame on the same order does
        not match the 28" line.
        """
        haystack = re.sub(r"[^A-Z0-9]", "",
                          ("%s %s" % (inv_line["code"], inv_line["description"])).upper())
        scored = []
        for line in candidates:
            segments = _code_segments(line.product_id.default_code)
            if segments and all(s in haystack for s in segments):
                scored.append((sum(len(s) for s in segments), line))
        if not scored:
            return None
        best = max(score for score, _ in scored)
        winners = [line for score, line in scored if score == best]
        return winners[0] if len(winners) == 1 else None

    @staticmethod
    def _amh_shortfalls(parsed):
        """Lines the vendor did not ship in full. Empty means safe to receive.

        This reads EVERY parsed line, not just the charged ones. A back-ordered
        line is printed with a quantity, no price and no line total - so
        checking only the charged lines would call a part shipment complete and
        receive stock that never arrived.

        A shortfall is a reason not to RECEIVE. On its own it is not a reason
        not to bill - see ``_amh_receipt_gaps``.
        """
        out = []
        for line in parsed["lines"]:
            if line["note"] and line["line_total"] is None and not line["qty_backordered"]:
                continue  # commentary, e.g. "Included"
            if line["qty_shipped"] < line["qty_ordered"] or line["qty_backordered"]:
                out.append("%s %s of %s" % (
                    line["code"], line["qty_shipped"], line["qty_ordered"]))
        return out

    @staticmethod
    def _amh_receipt_gaps(pairs):
        """Where the receipt disagrees with what a part shipment bills.

        Empty means every charged line has exactly as much received and not yet
        billed as the invoice charges for, so the goods are on hand, nothing
        needs receiving, and the bill Odoo builds will carry the right
        quantities.

        Equality, not "at least". ``action_create_invoice`` bills whatever is
        received and uninvoiced, and ``_amh_apply_amounts`` then divides the
        printed line total by that quantity. So if the vendor shipped 60 of 100
        and all 100 have been received - which is exactly what happens when the
        back order arrives separately - the bill would come to the right money
        at the wrong quantity, consuming 100 of the order instead of 60. It
        would balance to the penny and still be wrong, which is the one failure
        nobody would catch by eye.

        Measured against received-less-invoiced because that, not received, is
        what Odoo will put on the draft. Only charged lines are here: a
        back-ordered line bills nothing and is nobody's gap.
        """
        gaps = []
        for inv_line, po_line in pairs:
            billable = po_line.qty_received - po_line.qty_invoiced
            shipped = float(inv_line["qty_shipped"] or 0)
            if float_compare(billable, shipped, 2) != 0:
                gaps.append(_(
                    "%(code)s bills %(shipped)g with %(billable)g received and "
                    "not yet billed"
                ) % {"code": inv_line["code"], "shipped": shipped, "billable": billable})
        return gaps

    def _amh_receive_in_full(self, order):
        for picking in order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
            for move in picking.move_ids:
                move.write({"quantity": move.product_uom_qty, "picked": True})
            result = picking.button_validate()
            if isinstance(result, dict) and result.get("res_model"):
                context = result.get("context") or {}
                wizard = self.env[result["res_model"]].with_context(**context).create({})
                if hasattr(wizard, "process"):
                    wizard.process()

    def _amh_build_bill(self, order, parsed, pairs):
        action = order.with_context(default_move_type="in_invoice").action_create_invoice()
        bill = self.browse(action.get("res_id")) if isinstance(action, dict) else None
        if not bill or not bill.exists():
            return False
        bill.write({
            "ref": parsed["invoice_no"],
            "invoice_date": parsed["invoice_date"] or fields.Date.context_today(self),
            "amh_vendor_invoice_no": parsed["invoice_no"],
        })
        return bill

    def _amh_apply_amounts(self, parsed, pairs):
        """Price every line off the printed line total, then force the tax.

        Returns a problem string, or False when the bill agrees to the penny.
        """
        self.ensure_one()
        by_po_line = {po_line.id: inv_line for inv_line, po_line in pairs}

        commands, drop = [], []
        for line in self.invoice_line_ids:
            if line.display_type not in PRODUCT_LINE_TYPES:
                continue  # section, note, rounding - see PRODUCT_LINE_TYPES
            inv_line = by_po_line.get(line.purchase_line_id.id)
            if inv_line is None or float_is_zero(line.quantity, 2):
                drop.append(line.id)
                continue
            # the printed line total is authoritative; the printed unit price is not
            commands.append((1, line.id, {
                "price_unit": float(inv_line["line_total"]) / line.quantity,
            }))
        if drop:
            commands += [(2, line_id) for line_id in drop]
        if commands:
            self.write({"invoice_line_ids": commands})

        tax_line = self.line_ids.filtered(lambda l: l.display_type == "tax")[:1]
        if tax_line and float_compare(tax_line.amount_currency, float(parsed["tax"]), 2) != 0:
            # the vendor taxes unrounded extensions and prints against a rounded
            # subtotal, which no percentage tax in Odoo can reproduce
            self.write({"line_ids": [(1, tax_line.id, {
                "amount_currency": float(parsed["tax"]),
                "balance": float(parsed["tax"]),
            })]})

        if float_compare(self.amount_total, float(parsed["total"]), 2) != 0:
            return _("it comes to %s where the invoice says %s, so it was left in draft.") % (
                self.amount_total, parsed["total"])
        return False

    def _amh_hand_over_to(self, bill, parsed):
        """Move the PDF onto the real bill and retire the carrier draft.

        The draft is cancelled rather than deleted so the original email, and
        whatever the vendor wrote in it, stays on the record.
        """
        self._amh_pdf_attachments().write({"res_id": bill.id})
        bill.message_post(body=_(
            "Imported from vendor invoice %s, matched to %s.") % (
                parsed["invoice_no"], bill.invoice_origin or ""))
        self.write({
            "amh_import_state": "imported",
            "amh_import_note": _("Superseded by %s.") % bill.display_name,
            "amh_successor_move_id": bill.id,
        })
        self.button_cancel()
