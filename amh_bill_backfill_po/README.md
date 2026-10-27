# AMH Backfill PO & Receipt from Bill

Keeps a vendor bill and its purchase order in step, from the bill. Two buttons
in the bill's header, pointing in opposite directions, and one column on the
Bills list so you can tell which bills are waiting on the warehouse without
opening any of them.

**Periodic stock valuation only.** It checks, and refuses otherwise — see the
last section.

## Create PO & Receipt

For the bill that arrives with no purchase order behind it at all: a counter
purchase, eighteen vise grips from the hardware store, an invoice. The PDF
becomes a draft bill, and only at posting time does it become clear the stock
has to come in first — which means leaving the bill for the Purchase app,
raising an RFQ, confirming it, finding the receipt in Inventory, validating it,
and coming back. Re-uploading the PDF would only make a second draft.

So the bill becomes the source. The button builds the purchase order from the
bill's own lines, confirms it, receives the billed quantities, backdates the
receipt to the bill date, links each bill line to its order line, and leaves
the bill **in draft** to post exactly as before.

Only **storable** lines not already linked to an order are taken. A service, or
a productless line the OCR coded straight to an expense account, has nothing to
receive and is left off — a receipt line that cannot be received is worse than
none. So the button hides itself on the carrier, card-fee and repair invoices
that make up most bills arriving without an order.

Things it refuses rather than guess at:

* an order Odoo has **repriced** off the vendor price list. `price_unit` on
  `purchase.order.line` is computed-stored-with-precompute, so the price list
  can beat the value handed to `create()`. The price is written back from the
  bill and then checked at the **Product Price** precision — four decimals,
  because small hardware is bought at prices like 0.0855 and rounding that to
  cents would call two different prices the same;
* a bill whose product categories use **Automated** valuation (below).

Things it does *not* do: it never validates a receipt you have not asked for
anywhere else, and it never re-dates your payments. An existing confirmed order
for the same vendor with quantities still to bill is reported in the chatter
rather than used to block the button — the goods may well have been ordered on
an order somebody else raised, and the honest answer is to say so where it will
be seen.

## Reload PO Data

For the commoner case: the bill arrives **before** the goods. Auto-completing
it from the order brings its lines in at quantity **zero**, because nothing has
been received and there is nothing yet to invoice. Re-picking the order later
does not help — those lines already carry `purchase_line_id`, and Odoo adds
only order lines a bill does not already have.

The button fills them in once the warehouse has received. For each line whose
order line has quantities received but not billed, the quantity becomes
`quantity + qty_to_invoice`. That figure already nets off what the draft
claims, so a line sitting at 10 of 32 lands on 32 rather than 42, and only a
*positive* figure is taken — a line billed for more than was received is left
alone rather than quietly reduced.

**Prices are untouched and no line is ever added.** Both came off the paper
invoice; the order's price is only what was expected. It fills in what is
*receivable*, so if the vendor bills 32 and only 20 arrived you get 20 and the
mismatch is visible — which is the point.

## The Receipt column

`amh_po_receipt_state` is a stored field on the bill reading **Waiting on
receipt**, **Partly received** or **Ready to bill**, shown as a badge column on
the Bills list with three matching filters. Bills with no purchase order behind
them are left blank rather than flagged as if they were waiting for something.

It depends on the order line's `qty_to_invoice`, which is itself stored, so it
updates **the moment a receipt is validated** — no cron, no polling, and
nothing is written to a draft bill to make the status appear.

The column comes from a *single* view record, on `account.view_in_invoice_tree`.
`account.view_in_invoice_bill_tree` is a primary view that inherits it, so
extending both puts the column on the Bills list twice.

## Periodic valuation only

Under **periodic** (manual) valuation a receipt writes no journal entry at all:
no stock-interim account to clear, no three-way match, no price-difference
entry. A storable line codes to Cost of Goods Sold whether an order sits behind
it or not, so the accounts are identical before and after either button, and
the order of operations is a convenience question rather than an accounting
one.

Under **perpetual** (automated / real-time) valuation it is not. There the
receipt posts against a stock-interim account that only a PO-linked bill line
clears; writing the link here does not recode that line, and backdating an
already-validated move leaves its journal entry dated today. The interim
account would hang with nothing saying so. Rather than guess, Create PO &
Receipt **refuses** when any product category on the bill is set to Automated
valuation, and names them.

It does build the documents in the order perpetual valuation would require
anyway — order, then receipt, then the bill posted by hand — so the sequence is
right even where the accounting is not yet handled.

## Two things that cost a live run to find

**Context defaults leak across models.** Odoo applies a `default_<field>` key
to *any* model with a field of that name. The Bills action carries
`default_move_type="in_invoice"`; `stock.picking.move_type` is the Shipping
Policy. The first press from the form died on
`ValueError: Wrong value for stock.picking.move_type: 'in_invoice'`. The action
now strips every `default_*` key before it builds anything. It never showed up
in testing because the tests called the method over RPC, where no form context
exists — a button has to be exercised with the context the UI supplies.

**Units are converted silently.** The order line, and so the receipt, is built
in the *bill's* unit, and Odoo converts that into the product's own unit when
the goods are received. Where the two are not sensibly related the conversion
is wrong and says nothing: a bill for five sheets of poly in "Each", against a
product stocked in "4x8 FT", received 1.68 of them. That is guarded at the
source by `amh_variant_fields`, which requires a vendor's unit to be one the
product is actually sold in.
