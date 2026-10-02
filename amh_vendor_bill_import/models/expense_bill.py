"""Bills with no purchase order behind them.

The rest of this engine bills against a purchase order: it matches invoice
lines to order lines, receives the stock and prices the bill off what was
ordered. Carrier and customs-broker invoices have none of that. There is no
order, nothing is received, and the tax is not a percentage of anything printed
on the page - UPS brokerage recharges the GST it paid CBSA on the value of the
imported goods, and that value appears nowhere on the invoice. No amount of
better reading derives 62.19; the figure has to be taken as printed.

So the expense path asks a parser for three things - what was charged and at
what rate, what is being passed through untaxed, and what the invoice says the
tax and total are - then builds the bill, forces the tax to the printed figure
and checks the total to the penny. Anything that does not agree parks with the
reason, exactly as the purchase-order path does.

Accounts are named by system parameter rather than hard-coded, following
``amh_landed_cost.freight_product_id``:

    amh_vendor_bill_import.expense_account      default 54100
    amh_vendor_bill_import.brokerage_account    default 51400
    amh_vendor_bill_import.card_fees_account    default 73300
    amh_vendor_bill_import.pass_through_account default 23100
    amh_vendor_bill_import.carrier_tax_rate     default 13

A charge says which of those it belongs in through its ``role``, and a role
that resolves to no account PARKS the bill rather than falling back to the
default. That is deliberate and was once the other way round: a silent fallback
means a charge coded to freight because this file had never heard of its role,
which balances to the penny, posts, and says nothing. The parked bill names the
parameter to set instead.

The rate is only ever used for the carriers that print a tax total without
printing what it was charged on - Loomis, Purolator, Canada Post. Their tax is
a blend of whatever provinces they delivered to that week, and no single rate
is the truth, so the figure on the bill is forced to the printed one either
way; the rate decides which tax line it lands on and nothing else. It defaults
to 13 because most of the freight is delivered in Ontario. Set it to 5 to match
how these were coded by hand before. UPS is not affected: it prints its rates
and the bases they applied to, so its bills are built at the real rates. Nor is
PayFacto, which prints a plain 13% on a printed base.

54100 Freight Out, 51400 Brokerage and Duty, 73300 Credit & Debit Card Disc.
and 23100 GST/HST Recoverable (ITC) are where these bills are coded by hand
today - every one of the eight Link+ bills posted so far is in 51400, every
carrier bill is in 54100, and the August and September PayFacto statements are
in 73300 - so nothing about the ledger changes, only who does the typing.
"""
import logging

from odoo import _, fields, models
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)

DEFAULT_PASS_THROUGH_ACCOUNT = "23100"
DEFAULT_CARRIER_TAX_RATE = "13"
BLENDED = "blended"

# role -> (system parameter, account code to fall back on when it is unset).
# A charge with no role at all is "expense"; see _amh_import_expense.
EXPENSE_ROLES = {
    "expense": ("amh_vendor_bill_import.expense_account", "54100"),
    "brokerage": ("amh_vendor_bill_import.brokerage_account", "51400"),
    "card_fees": ("amh_vendor_bill_import.card_fees_account", "73300"),
}


class AccountMove(models.Model):
    _inherit = "account.move"

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    def _amh_account_by_code(self, code):
        """An account by its code, in this move's company.

        The company field on ``account.account`` moved from ``company_id`` to
        ``company_ids`` partway through Odoo's recent versions, so both are
        tried rather than pinning this module to one of them.
        """
        Account = self.env["account.account"]
        company = self.company_id or self.env.company
        domain = [("code", "=", code)]
        if "company_ids" in Account._fields:
            domain.append(("company_ids", "in", company.id))
        elif "company_id" in Account._fields:
            domain.append(("company_id", "=", company.id))
        return Account.search(domain, limit=1)

    def _amh_expense_accounts(self):
        """({role: account}, pass-through account), any of them possibly empty.

        A charge says which of these it belongs in through its ``role``: a
        broker's fee is not freight and is not coded as freight by hand, so it
        is not coded as freight here either, and neither are card fees.
        """
        params = self.env["ir.config_parameter"].sudo()
        roles = {
            role: self._amh_account_by_code(params.get_param(parameter, default))
            for role, (parameter, default) in EXPENSE_ROLES.items()
        }
        through = self._amh_account_by_code(params.get_param(
            "amh_vendor_bill_import.pass_through_account",
            DEFAULT_PASS_THROUGH_ACCOUNT))
        return roles, through

    def _amh_carrier_tax_rate(self):
        """The rate to use where a carrier prints no rate of its own."""
        return self.env["ir.config_parameter"].sudo().get_param(
            "amh_vendor_bill_import.carrier_tax_rate", DEFAULT_CARRIER_TAX_RATE)

    def _amh_purchase_tax(self, rate):
        """The purchase tax at this percentage, or an empty recordset for none.

        Matched on the rate rather than by name or xmlid so that renaming a tax
        cannot silently change what a bill is coded at. A rate with no tax
        behind it is a configuration problem and is reported as one.
        """
        if rate is None:
            return self.env["account.tax"]
        if rate == BLENDED:
            rate = self._amh_carrier_tax_rate()
        company = self.company_id or self.env.company
        return self.env["account.tax"].search([
            ("type_tax_use", "=", "purchase"),
            ("amount_type", "=", "percent"),
            ("amount", "=", float(rate)),
            ("company_id", "=", company.id),
        ], limit=1)

    # ------------------------------------------------------------------
    # the expense path
    # ------------------------------------------------------------------
    def _amh_import_expense(self, partner, parsed, adopted=None):
        """Build, balance and post a bill that has no purchase order."""
        self.ensure_one()
        accounts, pass_through_account = self._amh_expense_accounts()

        commands = []
        for charge in parsed.get("charges") or []:
            # No role means ordinary expense. An UNKNOWN role, or a known one
            # whose account code is not in this chart, is not quietly coded
            # somewhere else - see the module docstring.
            role = charge.get("role") or "expense"
            account = accounts.get(role)
            if not account:
                parameter, default = EXPENSE_ROLES.get(
                    role, ("amh_vendor_bill_import.%s_account" % role, "?"))
                return self._amh_park(_(
                    "Invoice %(invoice)s charges %(label)s, which belongs in "
                    "the %(role)s account, and there is no account here with "
                    "that code. Set the system parameter %(parameter)s to an "
                    "account code (it looks for %(default)s)."
                ) % {"invoice": parsed["invoice_no"], "label": charge["label"],
                     "role": role, "parameter": parameter, "default": default})

            rate = charge.get("tax_rate")
            tax = self._amh_purchase_tax(rate)
            if rate is not None and not tax:
                if rate == BLENDED:
                    rate = self._amh_carrier_tax_rate()
                return self._amh_park(_(
                    "Invoice %s is charged at %s%%, and this company has no "
                    "purchase tax at that rate."
                ) % (parsed["invoice_no"], rate))
            commands.append((0, 0, {
                "name": charge["label"],
                "quantity": 1,
                "price_unit": float(charge["amount"]),
                "account_id": account.id,
                "tax_ids": [(6, 0, tax.ids)],
            }))

        for item in parsed.get("pass_through") or []:
            if not pass_through_account:
                return self._amh_park(_(
                    "Invoice %s passes through %s, and there is no account to "
                    "put it in. Set amh_vendor_bill_import.pass_through_account."
                ) % (parsed["invoice_no"], item["label"]))
            commands.append((0, 0, {
                "name": item["label"],
                "quantity": 1,
                "price_unit": float(item["amount"]),
                "account_id": pass_through_account.id,
                "tax_ids": [(6, 0, [])],
            }))

        if not commands:
            return self._amh_park(_(
                "Invoice %s parsed with nothing to bill.") % parsed["invoice_no"])

        bill = adopted
        if bill:
            bill.write({"invoice_line_ids": [(5, 0, 0)] + commands})
        else:
            bill = self.env["account.move"].create({
                "move_type": "in_invoice",
                "partner_id": partner.id,
                "invoice_date": parsed["invoice_date"] or fields.Date.context_today(self),
                "ref": parsed["invoice_no"],
                "amh_vendor_invoice_no": parsed["invoice_no"],
                "invoice_line_ids": commands,
            })
        self.write({"amh_successor_move_id": bill.id})

        problem = bill._amh_force_tax_total(parsed)
        if problem:
            bill.write({"amh_import_state": "parked", "amh_import_note": problem})
            return self._amh_park(_("Built %s but %s") % (bill.display_name, problem))

        bill.action_post()
        bill.write({"amh_import_state": "imported", "amh_import_note": False})
        self._amh_hand_over_to(bill, parsed)
        return True

    def _amh_force_tax_total(self, parsed):
        """Make the bill's tax equal the printed tax. Returns a problem or False.

        The same reasoning as ``_amh_apply_amounts``: the carrier's figure is
        the fact, and no percentage tax in Odoo reproduces every carrier's
        rounding - Link+ rounds each fee line, Harness Hardware rounds the
        total, and a mixed GST/HST courier invoice is two bases at once.
        """
        self.ensure_one()
        printed_tax = float(parsed["tax"])
        tax_lines = self.line_ids.filtered(lambda line: line.display_type == "tax")
        if not tax_lines and not float_is_zero(printed_tax, 2):
            return _(
                "the invoice shows %s of tax but no tax line was created, so it "
                "was left in draft.") % parsed["tax"]
        if len(tax_lines) == 1 and float_compare(
                tax_lines.amount_currency, printed_tax, 2) != 0:
            self.write({"line_ids": [(1, tax_lines.id, {
                "amount_currency": printed_tax,
                "balance": printed_tax,
            })]})
        elif len(tax_lines) > 1:
            # More than one rate on the invoice. UPS prints the tax it charged
            # at each rate, so each line is forced to its own printed figure
            # rather than the difference being pushed arbitrarily onto one of
            # them. Where no breakdown was printed, they are only checked.
            printed = {float(rate): float(amount)
                       for rate, amount in (parsed.get("tax_breakdown") or [])}
            for line in tax_lines:
                figure = printed.get(line.tax_line_id.amount)
                if figure is not None and float_compare(
                        line.amount_currency, figure, 2) != 0:
                    self.write({"line_ids": [(1, line.id, {
                        "amount_currency": figure,
                        "balance": figure,
                    })]})
            total = sum(tax_lines.mapped("amount_currency"))
            if float_compare(total, printed_tax, 2) != 0:
                return _(
                    "its tax comes to %(computed)s where the invoice says "
                    "%(printed)s, so it was left in draft."
                ) % {"computed": total, "printed": parsed["tax"]}

        if float_compare(self.amount_total, float(parsed["total"]), 2) != 0:
            return _(
                "it comes to %(computed)s where the invoice says %(printed)s, so "
                "it was left in draft."
            ) % {"computed": self.amount_total, "printed": parsed["total"]}
        return False
