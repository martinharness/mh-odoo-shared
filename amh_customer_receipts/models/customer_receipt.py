from itertools import combinations

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero
from odoo.tools.misc import formatLang


class ResUsers(models.Model):
    _inherit = "res.users"

    # "Method defaulted to last used": remember what this user last received a
    # customer payment with, so the Customer Receipts screen opens on it.
    mh_receipt_journal_id = fields.Many2one(
        "account.journal", string="Last Receipt Journal", company_dependent=False,
    )
    mh_receipt_method_line_id = fields.Many2one(
        "account.payment.method.line", string="Last Receipt Payment Method",
        company_dependent=False,
    )
    mh_receipt_writeoff_account_id = fields.Many2one(
        "account.account", string="Last Receipt Discrepancy Account",
        company_dependent=False,
    )


class AmhCustomerReceipt(models.TransientModel):
    _name = "amh.customer.receipt"
    _description = "Customer Receipt (single-customer payment entry)"

    # ------------------------------------------------------------------
    # defaults - the journal / method / write-off account default to what
    # this user last received a payment with.
    # ------------------------------------------------------------------
    def _default_journal(self):
        user = self.env.user
        if user.mh_receipt_journal_id and user.mh_receipt_journal_id.company_id == self.env.company:
            return user.mh_receipt_journal_id
        return self.env["account.journal"].search([
            ("type", "in", ("bank", "cash")),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

    def _default_method_line(self):
        journal = self._default_journal()
        if not journal:
            return False
        last = self.env.user.mh_receipt_method_line_id
        if last and last.journal_id == journal and last.payment_type == "inbound":
            return last
        return journal.inbound_payment_method_line_ids[:1]

    def _default_writeoff_account(self):
        return self.env.user.mh_receipt_writeoff_account_id or False

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")

    partner_id = fields.Many2one(
        "res.partner", string="Customer", domain="[('customer_rank', '>', 0)]",
    )
    partner_display = fields.Char(
        string="Address", compute="_compute_partner_display",
    )
    invoice_lookup_id = fields.Many2one(
        "account.move",
        string="Find by Invoice #",
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),"
               " ('amount_residual', '>', 0)]",
        help="Type an invoice number to pull up its customer and their open "
             "invoices (that invoice is ticked for you).",
    )

    journal_id = fields.Many2one(
        "account.journal", string="Deposit To", required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        default=lambda self: self._default_journal(),
    )
    payment_method_line_id = fields.Many2one(
        "account.payment.method.line", string="Payment Method", required=True,
        domain="[('journal_id', '=', journal_id), ('payment_type', '=', 'inbound')]",
        default=lambda self: self._default_method_line(),
    )
    payment_date = fields.Date(
        required=True, default=fields.Date.context_today,
    )
    reference = fields.Char(
        string="Cheque / Reference",
        help="Becomes the payment's memo - for a cheque, the cheque number.",
    )
    amount = fields.Monetary(
        string="Amount Received", currency_field="currency_id",
    )

    line_ids = fields.One2many(
        "amh.customer.receipt.line", "receipt_id", string="Open Invoices",
    )

    selected_total = fields.Monetary(
        compute="_compute_totals", currency_field="currency_id",
        string="Selected Total",
    )
    difference = fields.Monetary(
        compute="_compute_totals", currency_field="currency_id",
        help="What the amount received leaves unsettled against the ticked "
             "invoices. Non-zero is not always an error - a customer can pay "
             "part of a balance - but it is worth a look.",
    )
    has_discrepancy = fields.Boolean(compute="_compute_totals")

    writeoff = fields.Boolean(
        string="Write Off Difference",
        help="Send a small difference to the Discrepancy Account and close the "
             "invoices in full, instead of leaving a part payment open.",
    )
    writeoff_account_id = fields.Many2one(
        "account.account", string="Discrepancy Account",
        domain="[('company_ids', 'in', company_id)]",
        default=lambda self: self._default_writeoff_account(),
    )
    writeoff_limit = fields.Monetary(
        string="Write-off Limit", currency_field="currency_id", default=10.0,
        help="The largest difference a receipt may write off - a guard against a "
             "wrong amount quietly disappearing into an account.",
    )

    last_result = fields.Char(readonly=True)

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends("partner_id")
    def _compute_display_name(self):
        """A readable breadcrumb - "Customer Receipt", or "Receipt: <customer>"
        once one is picked - instead of the raw "amh.customer.receipt,11"."""
        for rec in self:
            rec.display_name = (
                _("Receipt: %s", rec.partner_id.display_name)
                if rec.partner_id else _("Customer Receipt")
            )

    @api.depends("partner_id")
    def _compute_partner_display(self):
        for rec in self:
            p = rec.partner_id
            if not p:
                rec.partner_display = False
                continue
            bits = [p.name, p.street, p.street2]
            city = ", ".join(x for x in [p.city, p.state_id.code, p.zip] if x)
            if city:
                bits.append(city)
            rec.partner_display = "\n".join(x for x in bits if x)

    @api.depends("line_ids.selected", "line_ids.amount_residual", "amount", "currency_id")
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.filtered("selected").mapped("amount_residual"))
            rec.selected_total = total
            rec.difference = total - rec.amount
            rounding = (rec.currency_id or rec.company_id.currency_id).rounding
            rec.has_discrepancy = not float_is_zero(rec.difference, precision_rounding=rounding)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _open_invoices(self):
        """Every posted customer invoice this customer still owes on."""
        self.ensure_one()
        if not self.partner_id:
            return self.env["account.move"]
        company = self.company_id or self.env.company
        return self.env["account.move"].search([
            ("partner_id", "child_of", self.partner_id.commercial_partner_id.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_residual", ">", 0),
            ("company_id", "=", company.id),
        ], order="invoice_date, id")

    def _rebuild_lines(self, preselect=None):
        """Replace the grid with this customer's open invoices.

        ``preselect`` is an optional account.move to tick (the one found via the
        invoice lookup). Where the customer has exactly one open invoice it is
        ticked and the amount is filled - there is no choice to make.
        """
        self.ensure_one()
        invoices = self._open_invoices()
        single = len(invoices) == 1
        cmds = [fields.Command.clear()]
        for inv in invoices:
            tick = single or (preselect and inv.id == preselect.id)
            cmds.append(fields.Command.create({"move_id": inv.id, "selected": tick}))
        self.line_ids = cmds
        if single:
            self.amount = invoices.amount_residual
        elif preselect:
            self.amount = preselect.amount_residual

    def _find_invoice_combination(self, target):
        """Which open invoices add up to this amount? Oldest first, and only
        where the answer is unambiguous (mirrors the batch tool)."""
        self.ensure_one()
        moves = self.line_ids.mapped("move_id").sorted(
            key=lambda m: (m.invoice_date or m.date, m.id)
        )
        if not moves:
            return self.env["account.move"]
        rounding = (self.currency_id or self.env.company.currency_id).rounding

        running = 0.0
        picked = self.env["account.move"]
        for m in moves:
            running += m.amount_residual
            picked |= m
            if float_is_zero(running - target, precision_rounding=rounding):
                return picked
            if float_compare(running, target, precision_rounding=rounding) > 0:
                break

        if len(moves) > 12:
            return self.env["account.move"]
        found = None
        for size in range(1, len(moves) + 1):
            for combo in combinations(moves, size):
                tot = sum(x.amount_residual for x in combo)
                if not float_is_zero(tot - target, precision_rounding=rounding):
                    continue
                if found is not None:
                    return self.env["account.move"]
                found = combo
        if not found:
            return self.env["account.move"]
        res = self.env["account.move"]
        for m in found:
            res |= m
        return res

    # ------------------------------------------------------------------
    # onchanges
    # ------------------------------------------------------------------
    @api.onchange("partner_id")
    def _onchange_partner(self):
        """Load this customer's open invoices. Keep an existing selection when it
        already belongs to this customer (so an invoice-lookup tick survives)."""
        self.last_result = False
        if self.invoice_lookup_id and (
            self.invoice_lookup_id.commercial_partner_id
            != self.partner_id.commercial_partner_id
        ):
            self.invoice_lookup_id = False
        if not self.partner_id:
            self.line_ids = [fields.Command.clear()]
            return
        commercial = self.partner_id.commercial_partner_id
        if self.line_ids and all(
            line.move_id.commercial_partner_id == commercial for line in self.line_ids
        ):
            return
        self._rebuild_lines()

    @api.onchange("invoice_lookup_id")
    def _onchange_invoice_lookup(self):
        """Start a receipt by invoice number: adopt its customer, load their open
        invoices, tick the one typed."""
        inv = self.invoice_lookup_id
        if not inv:
            return
        self.partner_id = inv.commercial_partner_id
        self._rebuild_lines(preselect=inv)

    @api.onchange("journal_id")
    def _onchange_journal(self):
        """A method line belongs to one journal, so default it to the journal's."""
        if self.payment_method_line_id.journal_id != self.journal_id:
            self.payment_method_line_id = self.journal_id.inbound_payment_method_line_ids[:1]

    @api.onchange("line_ids")
    def _onchange_lines_set_amount(self):
        """Ticking invoices sets the amount to their total - the usual case
        (a cheque paying exactly what was picked)."""
        total = sum(self.line_ids.filtered("selected").mapped("amount_residual"))
        if total:
            self.amount = total

    @api.onchange("amount")
    def _onchange_amount_finds_invoices(self):
        """Type the cheque amount and it works out which invoices it settles -
        only when nothing is ticked yet, so it never fights a manual selection."""
        if not self.partner_id or not self.amount or not self.line_ids:
            return
        if any(self.line_ids.mapped("selected")):
            return
        match = self._find_invoice_combination(self.amount)
        if match:
            for line in self.line_ids:
                if line.move_id in match:
                    line.selected = True

    @api.onchange("writeoff")
    def _onchange_writeoff_needs_account(self):
        if self.writeoff and not self.writeoff_account_id:
            return {"warning": {
                "title": _("No Discrepancy Account"),
                "message": _(
                    "Pick a Discrepancy Account for the difference to go to."
                ),
            }}

    # ------------------------------------------------------------------
    # the actions
    # ------------------------------------------------------------------
    @api.model
    def action_open_screen(self, last_result=None):
        """Create a blank receipt and open it full-page.

        A transient form opened by an action with no res_id renders blank, so the
        record is created first and opened by its id - which also lets the
        post-payment reload carry a success banner in on a real record. Used by
        the menu (server action) and by Receive Payment for the next screen.
        """
        rec = self.create({"last_result": last_result} if last_result else {})
        return {
            "type": "ir.actions.act_window",
            "name": _("Customer Receipts"),
            "res_model": "amh.customer.receipt",
            "res_id": rec.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_receive_payment(self):
        """Create the native customer payment for the ticked invoices, remember the
        method for next time, and land on a fresh blank screen for the next
        customer."""
        self.ensure_one()
        selected = self.line_ids.filtered("selected")
        if not self.partner_id:
            raise UserError(_("Choose a customer first."))
        if not selected:
            raise UserError(_("Tick at least one invoice to pay."))
        if not self.journal_id or not self.payment_method_line_id:
            raise UserError(_("Choose where the money goes (Deposit To) and the Payment Method."))
        if self.amount <= 0:
            raise UserError(_("Enter the amount received."))

        rounding = (self.currency_id or self.company_id.currency_id).rounding
        has_diff = not float_is_zero(self.difference, precision_rounding=rounding)

        if self.writeoff and has_diff:
            if not self.writeoff_account_id:
                raise UserError(_(
                    "To write off the difference, set a Discrepancy Account."
                ))
            if abs(self.difference) > (self.writeoff_limit or 0.0):
                raise UserError(_(
                    "The difference (%(d).2f) is larger than the write-off limit "
                    "(%(l).2f). Fix the amount or the selection, or raise the limit.",
                    d=self.difference, l=self.writeoff_limit,
                ))

        invoices = selected.mapped("move_id")
        vals = {
            "journal_id": self.journal_id.id,
            "payment_method_line_id": self.payment_method_line_id.id,
            "payment_date": self.payment_date,
            "amount": self.amount,
            "communication": self.reference or "",
            "group_payment": True,
        }
        if self.writeoff and has_diff:
            vals.update({
                "payment_difference_handling": "reconcile",
                "writeoff_account_id": self.writeoff_account_id.id,
                "writeoff_label": self.reference and _(
                    "Cheque %s discrepancy", self.reference,
                ) or _("Receipt discrepancy"),
            })

        register = self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoices.ids,
        ).create(vals)
        payments = register._create_payments()

        # remember the method for next time (drives the defaults above)
        self.env.user.sudo().write({
            "mh_receipt_journal_id": self.journal_id.id,
            "mh_receipt_method_line_id": self.payment_method_line_id.id,
            "mh_receipt_writeoff_account_id": self.writeoff_account_id.id or False,
        })

        payment = payments[:1]
        banner = _(
            "✓ Received %(amount)s from %(customer)s  →  %(payment)s",
            amount=formatLang(self.env, self.amount, currency_obj=self.currency_id),
            customer=self.partner_id.display_name,
            payment=payment.name or _("payment recorded"),
        )
        return self.action_open_screen(last_result=banner)


class AmhCustomerReceiptLine(models.TransientModel):
    _name = "amh.customer.receipt.line"
    _description = "Customer Receipt - Open Invoice"
    _order = "date_due, move_id"

    receipt_id = fields.Many2one(
        "amh.customer.receipt", required=True, ondelete="cascade",
    )
    currency_id = fields.Many2one(related="receipt_id.currency_id")
    move_id = fields.Many2one("account.move", string="Invoice", required=True)

    selected = fields.Boolean(string="Pay")
    invoice_date = fields.Date(related="move_id.invoice_date", string="Date")
    date_due = fields.Date(related="move_id.invoice_date_due", string="Due")
    description = fields.Char(compute="_compute_description")
    origin = fields.Char(related="move_id.invoice_origin", string="P.O. / Job")
    amount_total = fields.Monetary(
        related="move_id.amount_total", currency_field="currency_id", string="Invoice Total",
    )
    amount_residual = fields.Monetary(
        related="move_id.amount_residual", currency_field="currency_id", string="Balance",
    )
    is_discrepancy = fields.Boolean(compute="_compute_is_discrepancy")

    @api.depends("move_id")
    def _compute_description(self):
        for line in self:
            product_line = line.move_id.invoice_line_ids.filtered(
                lambda l: l.display_type == "product"
            )[:1]
            name = (product_line.name or "").split("\n")[0] if product_line else ""
            line.description = name or line.move_id.payment_reference or line.move_id.name

    @api.depends("selected", "receipt_id.has_discrepancy")
    def _compute_is_discrepancy(self):
        for line in self:
            line.is_discrepancy = bool(line.selected and line.receipt_id.has_discrepancy)
