# Customer Receipts

Odoo takes customer payments one invoice at a time, through the Pay wizard on
the invoice list. For a single payment that is fine. For a deposit of fifty
cheques it is fifty searches, fifty changes away from the journal's default
payment method, and fifty memos cleared out to type a cheque number into.

`account.batch.payment` does not help - it groups payments that already exist.
There is no equivalent of the Receive Payments screen from EBMS or QuickBooks.
This is that screen: one customer at a time, keyed straight into native Odoo
customer payments.

## How it works

**Accounting → Customers → Customer Receipts.**

The cursor lands on the **Customer**. Pick one and their open invoices load in
the grid. Then, per cheque:

1. **Customer** → Tab
2. **Cheque / Reference** (the cheque number; becomes the payment memo) → Tab
3. **Amount Received** → **Alt+Q** (Receive Payment)

A native customer payment is created for the ticked invoices - no separate
document - and the screen clears for the next customer with the cursor back on
Customer. The whole run is keyboard-only; the journal, payment method and date
are set once and default to what you used last.

You can also start from the invoice: type a number into **Find by Invoice #**
and it adopts that invoice's customer and ticks it.

## Picking the invoices

Three things reduce the typing, in order of how often they help:

1. **One open invoice** - it is ticked and the amount filled in. No choice to
   make.
2. **Type the amount** - the invoices it settles are worked out for you.
   Oldest-first is tried before anything else, because that is how customers
   pay: they clear the top of the statement. Only if no run from the oldest
   works does it consider other combinations.
3. **Tick them yourself** - the invoice picker shows each invoice's balance due
   (`INV/20658 — 331.78`), because choosing between two invoices for the same
   customer is guesswork when all you can see is the numbers.

**Where two different sets of invoices add to the same total, neither is
chosen.** A coin toss does not belong in a ledger; the row is left for you. The
exhaustive search is 2^n, so it stops at twelve open invoices - a customer with
more than that is a conversation, not a puzzle to solve automatically.

## Discrepancies

A cheque a few cents out from the invoices it pays can have the difference
written off: set a **Discrepancy Account**, tick **Write Off Difference**. The
invoices close in full and the difference goes to that account. Leave it
unticked and the shortfall stays open on the invoice as a part payment, which is
Odoo's normal behaviour. A row whose amount does not match turns **red**.

There is a **write-off limit**, default 10.00. A cheque out by hundreds is a
wrong amount or the wrong invoices, not a rounding difference, and writing that
off silently lands it in an account nobody looks at again. Over the limit,
posting stops and names the problem. Raise the limit on a receipt where you
genuinely mean to.

## Install

Odoo 19. Copy `amh_customer_receipts/` into your addons path (or add this repo
to it), update the apps list, and install **Customer Receipts**. It depends only
on `account`. Users need the *Accounting / Billing* (`account.group_account_user`)
group.

## Design notes

**Payments go through `account.payment.register`**, the same wizard the invoice
list uses, rather than building `account.payment` records directly. Allocation,
partial settlement, write-offs and multi-invoice cheques then behave exactly as
they already do, and stay correct when Odoo changes how that works. It calls
`_create_payments()` rather than the public `action_create_payments()` because
the internal one returns the payments where the public wrapper returns an
action - the one place to re-read at a major version bump.

**The screen is a `TransientModel` opened by its own record id.** A transient
form opened by an action with *no* res_id renders blank full-page, so a blank
receipt is created first and the form opened by id; the menu runs a small
`ir.actions.server` that does this, and Receive Payment returns the same for the
next customer.

**The invoice residual is appended to `display_name` behind an
`amh_show_residual` context key**, set only on the invoice-lookup field.
Overriding `display_name` globally would have been simpler and would have leaked
the amount into reports, emails and every other invoice reference in Odoo.

**The amount and invoice fields each only auto-fill when the other is empty.**
Ticked invoices set the amount; a typed amount finds the invoices; the guard is
what stops them chasing each other round.

**The customer field is focused from a small JS asset, not `default_focus`.**
`default_focus="1"` is not honoured for this transient-model form opened by an
action, so `static/src/js/receipt_autofocus.js` focuses it on mount (scoped to
this model). Tab order is plain DOM order - Customer, Cheque/Reference and
Amount are simply the first three fields.

## Things learned building this, worth not relearning

- `account.account` has **no `deprecated` field** in Odoo 19. A domain naming a
  field that does not exist takes registry loading down with it, which presents
  as an unhelpful "Failed to load registry".
- `invisible=` on a field in a list view hides the **cell contents** and leaves
  the column header; `column_invisible=` hides the column. Core uses
  `column_invisible="not parent.<field>"` for the conditional case.
- A direct URL to a `TransientModel` form (`/odoo/<model>/<id>`) renders blank -
  it has to be reached through the menu (the server action). Handy to know when
  a "blank screen" looks like a bug and is not.

## License

LGPL-3.
