# Shared Odoo 19 modules from Aaron Martin Harness Ltd

Custom Odoo 19 (Enterprise / Odoo.sh) modules shared for use on another
Odoo.sh project. Each top-level folder is one installable module.

| Module | Display name | Depends on |
| --- | --- | --- |
| `amh_bom_copy` | AMH Item & BoM Copy | `mrp`, `sale` |
| `amh_bill_backfill_po` | AMH Backfill PO & Receipt from Bill | `purchase_stock` |
| `amh_customer_receipts` | Customer Receipts | `account` |
| `amh_vendor_bill_import` | AMH Vendor Bill Import | `purchase_stock`, `account` |
| `amh_wise_cad_domestic` | AMH Wise CAD Domestic EFT | `l10n_us_direct_deposit`, `l10n_ca_payment_cpa005` (Enterprise) |

## Installing on Odoo.sh

**As a git submodule (recommended)** - in your Odoo.sh project's repository:

```
git submodule add -b 19.0 https://github.com/martinharness/mh-odoo-shared.git mh-odoo-shared
git commit -m "Add shared AMH modules"
git push
```

If this repository is private, add the deploy key Odoo.sh shows under
*Settings > Submodules* to this repo on GitHub, then it will build. To pick up
later updates, run `git submodule update --remote mh-odoo-shared`, commit and
push.

**Or copy the folders** straight into the root of your Odoo.sh repository.

Then in Odoo: enable developer mode, go to *Apps > Update Apps List*, search
for the module name and install.

## Notes per module

### AMH Item & BoM Copy
Works standalone. It cooperates with `sale_bom_component_pricing` and
`amh_product_exact_search` when those are installed, but does not require them.

### Customer Receipts
A single-customer "Receive Payments" screen (*Accounting > Customers > Customer
Receipts*) for keying a pile of cheques or e-Transfers straight into native Odoo
customer payments, one customer at a time - the screen EBMS/QuickBooks have and
Odoo does not. Keyboard-only entry (Customer -> Cheque/Reference -> Amount ->
Alt+Q), with the invoices a cheque settles worked out from the amount. Depends
only on `account`, so it runs on Community or Enterprise. See the module's own
`README.md` for the full write-up.

### AMH Vendor Bill Import
Reads a vendor's invoice PDF out of a Documents drop folder, matches it to the
purchase order and posts a bill that agrees with the paper to the penny -
or parks it with a plain-English reason. A PDF no parser recognises is handed
to Odoo's invoice digitisation instead of being left for somebody to key.

The engine is vendor-agnostic; adding a vendor is one file in
`models/parsers/`. Two worked examples ship with it, one purchase-order
invoice and one expense invoice, and the module's own `README.md` is a guide
to writing your own - including the mistakes that cost the most to find and a
list of Odoo 19 traps.

Needs *Documents* (Enterprise) for the folder intake and
`account_invoice_extract` for the OCR handoff, but depends on neither: both
are detected at run time and it works without them. **Change the account codes
in the system parameters to match your chart of accounts, and keep both crons
disabled, until the parked/posted split looks right on a real batch.**

### AMH Backfill PO & Receipt from Bill
**Periodic stock valuation only — it checks, and refuses otherwise.**

A counter purchase arrives as paper: eighteen vise grips from the hardware store,
an invoice, and no purchase order in Odoo. The bill is read off the PDF and lands
in Draft, and only at posting time does it become clear the stock has to come in
first - which means leaving the bill for the Purchase app, raising an RFQ,
confirming it, finding the receipt in Inventory, validating it, and coming back.
Re-uploading the PDF would only make a second draft.

So the bill becomes the source. *Create PO & Receipt* in the vendor bill's header
builds the purchase order from the bill's own lines, confirms it, receives the
billed quantities, backdates the receipt to the bill date, links each bill line
to its order line, and leaves the bill in draft to post exactly as before.

Only **storable** lines not already linked to a purchase order are taken. A
service, or a productless line the OCR coded straight to an expense account, has
nothing to receive and is left off - a receipt line that cannot be received is
worse than none. So the button hides itself on the carrier, card-fee and repair
invoices that make up most of the bills arriving without a purchase order.

The order is built *from* the bill, so its price is the bill's price by
construction and there is never a variance to explain. The price is written back
after `create()` and then checked, because `purchase.order.line.price_unit` is
computed-stored-with-precompute and the vendor price list can otherwise win; the
check is at the **Product Price** decimal precision rather than the currency's,
because small hardware is bought at prices like 0.0855 a piece and rounding that
to cents would call two different prices the same. A repriced order is refused
before it is confirmed, so nothing is received.

Bill lines are paired to order lines by **creation order, not by product** - a
counter invoice can carry the same product twice at two prices.

An existing confirmed order for the same vendor with quantities still to bill is
reported in the bill's chatter rather than used to refuse the button: the goods
may well have been ordered on an order somebody else raised, and the honest
answer is to say so where it will be seen.

#### Reload PO Data

The commoner case is the bill that arrives *before* the goods. Auto-completing
it from the purchase order brings its lines in at quantity **zero**, because
nothing has been received and there is nothing yet to invoice - and re-picking
the order later does not help, since those lines already carry
`purchase_line_id` and Odoo adds only order lines a bill does not have.

*Reload PO Data* fills them in once the warehouse has received: for each line
whose order line has quantities received but not billed, the quantity becomes
`quantity + qty_to_invoice`. That already nets off what the draft claims, so a
line at 10 of 32 lands on 32 rather than 42, and only a positive figure is
taken - a line billed for more than was received is left alone rather than
quietly reduced. Prices are untouched and no line is ever added: both came off
the paper invoice.

#### Which bills are waiting

`amh_po_receipt_state` is a stored field reading **Waiting on receipt**,
**Partly received** or **Ready to bill**, shown as a badge column on the Bills
list with three matching filters, and left blank on bills with no purchase
order behind them. It depends on the order line's `qty_to_invoice`, which is
itself stored, so it updates the moment a receipt is validated - no cron, and
nothing is written to a draft bill to make the status appear.

The column comes from a single view record on `account.view_in_invoice_tree`.
`account.view_in_invoice_bill_tree` is a primary view that *inherits* it, so
extending both puts the column on the Bills list twice.

#### One trap worth knowing

The order line, and therefore the receipt, is built in the **bill's** unit, and
Odoo converts that into the product's own unit when the goods are received.
Where the two units are not sensibly related the conversion is wrong and
silent: a bill for five sheets of poly in "Each", against a product stocked in
"4x8 FT", received 1.68 of them. Odoo 18 and earlier refused to let a product's
purchase unit sit in a different UoM category from its stock unit; Odoo 19
replaced categories with the relative-unit tree and the check went with them.
Worth adding a constraint of your own that a vendor's unit must be the
product's own unit or one of its packagings - at AMH that lives in a separate
module which is not included in this repository.

#### The valuation constraint, worth checking before you install it

Under **periodic** (manual) valuation a receipt writes no journal entry at all,
so the bill's own coding is the entire accounting story and this button changes
nothing in the ledger. Under **perpetual** (automated / real-time) valuation it
would: the receipt posts against a stock-interim account that only a PO-linked
bill line clears, writing the link here does not recode that line, and
backdating an already-validated move leaves its journal entry dated today - so
the interim account would hang with nothing saying so. Rather than guess, the
button **refuses** when any product category on the bill is set to Automated
valuation, and names them. Check *Inventory > Configuration > Product
Categories* before relying on it.

### AMH Wise CAD Domestic EFT
Requires the company to be connected to Wise in *Accounting > Settings* (the
connection is provided by Odoo's *United States - Direct Deposit* module). Tick
*Wise CAD Domestic EFT* on each CAD bank journal that should offer the method.

Select posted CAD vendor bills, *Pay*, choose *Wise CAD Domestic EFT* and press
*Create Wise Batch*: the payments, the reconciliation and the draft Wise batch
are all made in one step. A batch then has three ways out, and none of them
ends on the Wise website:

* **Initiate Payment** - Odoo's own button. Creates and completes the batch on
  Wise and leaves the funding to be done in Wise.
* **Initiate & Fund from Wise Balance** - creates, completes and funds it from
  the company's Wise balance, after a confirmation that says plainly that it
  moves money. *Fund from Wise Balance* does the funding half for a batch
  already initiated, and is safe to press on one Wise has already paid: it reads
  the batch group first and records *Already Paid in Wise* instead of sending a
  second funding request.
* **Initiate & Schedule Fund** - for a batch too large to prefund in a day. On
  the Canadian side more than 25,000 cannot be moved into Wise instantly, and an
  EFT takes up to a business day, so this initiates the batch now and books the
  funding for a datetime you pick (defaulting to the next weekday at 15:00).
  Nothing leaves the account until then, and the money travels to Wise in the
  meantime. A cron, *Wise: fund scheduled batches from the Wise balance*, funds
  it on its first run at or after that time - it is shipped active and every 15
  minutes, and does nothing until a batch is actually scheduled. One attempt is
  made, and the booking is cleared and committed before the attempt, so nothing
  can be funded twice; if the balance is short, Wise's refusal is posted on the
  batch as a message to its followers and the booking is dropped rather than
  retried. *Cancel Scheduled Funding* drops it earlier, and *Fund from Wise
  Balance* stays available if you would rather not wait.

All of these work for USD *U.S. Direct Deposit* batches too.

#### "The batch cannot be validated"

Odoo warns before validating a Wise batch that *Wise will send the entire batch
payment as soon as you fund it* / *Scheduled payments are not yet supported*,
and the warning names every payment whose date is not today. It is per-payment:
re-date one payment of a seventeen-payment batch to today and the warning comes
back naming the other sixteen.

On a batch prepared on Monday and funded on Wednesday that is true and
uninteresting - the money leaves when the batch is funded, which the funding
confirmation already says in bold. What it costs is a second dialog headed
**The batch cannot be validated**, on a batch with no errors in it at all,
answered by a button called *Proceed with validation*. Read the way a new user
reads it, that says the pay-run has failed.

So the warning is dropped while every payment in the batch is dated today or
earlier. A payment dated in the **future** keeps it, because that is the case
the warning is actually for: fund it now and Wise sends now while the books say
next week. (*Fund from Wise Balance* refuses a future-dated batch outright;
plain *Initiate Payment* does not, and there the warning is the only thing in
the way.) That the payments are not dated today is instead said once, on the
funding confirmation, where the money is about to move.

It is matched on Odoo's own wording. If Odoo rewords it the match stops and the
warning comes back - the safe direction to fail in, since the result is the
dialog you have today rather than a silence nobody intended.

#### The remittance advice

Advice emails go to the vendor's `x_studio_eft_remittance_email` field if it
exists (a Studio field on the contact), otherwise to the vendor's main email
address. The vendor's email is deliberately kept off the Wise recipient so Wise
sends no notification of its own - Wise's reference is capped short and only
fits one invoice number, while the advice is one message per vendor listing
every bill the batch paid them.

It is sent **automatically once the batch is funded from the Wise balance**,
including by the scheduled cron: the scheduler funds through the same call the
button does, and the automatic send is imported last in `models/__init__.py` so
that it wraps both the funding and the schedule overrides. The vendor is told at
the moment the money actually moves rather than when the batch was initiated,
which for a scheduled batch can be a day or two later.

* A funding Wise refuses never reaches that point, so nobody is told they have
  been paid when they have not. Press *Fund from Wise Balance* once the money is
  in Wise and the advice goes then.
* Each payment records that its vendor has been told, so the button after an
  automatic send does nothing, and a part-sent batch only emails the rest.
* A failure to email can never undo a funding: the funded state is committed
  first and the send runs inside a savepoint, with any error logged and posted
  on the batch for the button to retry.

Where an **early-payment discount** was taken, the table gains *Discount* and
*Paid* columns. The discount is read off the bill's own payment-term lines and
counted only when the payment is dated on or before the discount date - never
inferred from the gap between what was billed and what was sent, because that
gap is also what a part payment looks like. The columns print only when the
table proves itself: amounts less discounts must equal the figure sent, in one
currency, to the cent. Anything else - a part payment, a credit note, a second
currency - falls back to the plain invoice/date/amount advice.

#### Three things worth knowing before the first run

Each of these cost a live pay-run to find:

* **Wise will not send to a P.O. box.** Where the vendor's `street` holds a box
  beside a real address, or the real address is on `street2`, the box is
  dropped and the street is sent. Where there is nothing but a box, the batch
  fails Odoo's own pre-flight naming the vendor, rather than inventing an
  address for a compliance check - Wise checks the recipient against it.
* **Wise rejects any reference outside `[a-zA-Z0-9- ]`.** Odoo builds it from
  the payment memo, so a vendor's own punctuation and the comma-separated list
  Odoo writes when one payment settles several bills both break it. The
  reference is cleaned on the way out; length is left alone.
* **A stale Wise recipient id stops every payment to that vendor** at the quote
  with "We couldn't find an account with that ID" and a bare number. The error
  is rewritten to name the vendor, and the id is shown on the bank account with
  a *Forget* button beside it - readonly, so it can be emptied but never typed
  into. The next payment then re-matches or re-creates the recipient.

## License
LGPL-3
