# Shared Odoo 19 modules from Aaron Martin Harness Ltd

Custom Odoo 19 (Enterprise / Odoo.sh) modules shared for use on another
Odoo.sh project. Each top-level folder is one installable module.

| Module | Display name | Depends on |
| --- | --- | --- |
| `amh_bom_copy` | AMH Item & BoM Copy | `mrp`, `sale` |
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
