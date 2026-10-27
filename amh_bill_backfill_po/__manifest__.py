{
    "name": "AMH Backfill PO & Receipt from Bill",
    "version": "19.0.1.2.2",
    "category": "Accounting/Accounting",
    "summary": "Keep a vendor bill and its purchase order in step, from the bill",
    "description": """
A counter purchase arrives as paper: eighteen vise grips from the hardware
store, an invoice, and no purchase order in Odoo. The bill is read off the PDF
and lands in Draft, and only at posting time does it become clear the stock has
to come in first - which means leaving the bill for the Purchase app, raising an
RFQ, confirming it, finding the receipt in Inventory, validating it, and coming
back. Re-uploading the PDF would only make a second draft.

So the bill, which is the document that actually exists, becomes the source.
"Create PO & Receipt" on a draft vendor bill builds the purchase order from the
bill's own lines, confirms it, receives the billed quantities, backdates the
receipt to the bill date, links each bill line to its order line, and leaves the
bill in draft to be posted exactly as before.

Only STORABLE product lines not already linked to a purchase order are taken. A
service, or a productless line the OCR coded straight to an expense account, has
nothing to receive and is deliberately left off - a receipt line that cannot be
received is worse than no receipt at all. The button hides itself when a bill has
no such lines, which is most of them: the vendor bills that arrive without a
purchase order are mostly carrier, card-fee and repair invoices with no stock on
them whatsoever.

The order is built FROM the bill, so its price is the bill's price by
construction and there is never a variance to explain. Because the vendor price
list can still win over the values handed to create() on a precomputed field,
the price is written again from the bill and then checked at the Product Price
precision - four decimals, because small hardware is bought at prices like
0.0855 a piece and rounding that to cents would call two different prices the
same. A repriced order is refused before it is confirmed, so nothing is
received.

The action also strips every default_* key from the context before it builds
anything. Odoo applies a default_<field> key to ANY model with a field of that
name, and the Bills action carries default_move_type="in_invoice" - which Odoo
duly tried to write into stock.picking.move_type, the Shipping Policy, the
first time the button was pressed from the form.

An existing confirmed order for the same vendor with quantities still to bill is
reported in the bill's chatter rather than used to refuse the button. The goods
may well have been ordered on an order somebody else raised, and the honest
answer to that is to say so where it will be seen, not to block the button and
leave a dialog whose only choice is Close.

THE OTHER DIRECTION: RELOADING A BILL FROM ITS ORDER

A bill often arrives before the goods do. Auto-completing it from the purchase
order then brings its lines in at quantity ZERO, because nothing has been
received and there is nothing yet to invoice - and re-picking the order later
does not help, since those lines already carry purchase_line_id and Odoo adds
only order lines a bill does not have. "Reload PO Data" fills them in once the
warehouse has received: for every line whose order line has quantities received
but not billed, the quantity becomes what is receivable. Prices are untouched,
because the price has to agree with the paper, and no line is ever added,
because the bill's shape came off the paper too.

Which bills are waiting on the warehouse is answered without opening any of
them. The stored field amh_po_receipt_state reads Waiting on receipt, Partly
received or Ready to bill, and appears as a column and three filters on the
Bills list. It depends on the order line's qty_to_invoice, which is itself
stored, so it updates the instant a receipt is validated - no cron, and nothing
is written to a draft bill to make the status appear. The column is added by a
SINGLE view record, on account.view_in_invoice_tree: the Bills list view is a
primary view that inherits it, so extending both put the column on twice.

PERIODIC VALUATION ONLY, AND IT CHECKS

Under PERIODIC (manual) stock valuation a receipt writes no journal entry at
all: no stock-interim account to clear, no three-way match to satisfy, no
price-difference entry. A storable line codes to Cost of Goods Sold whether a
purchase order sits behind it or not, so the accounts are identical before and
after this button and the order of operations is a convenience question rather
than an accounting one.

Under PERPETUAL (real-time) valuation it is not. There the receipt posts its own
entry against a stock-interim account that only a PO-linked bill line clears,
and writing the link here does not recode that line - so the interim account
would be left hanging, and a backdated validated move would keep a journal entry
dated today. Wrong quietly, which is the worst way to be wrong about stock. So
the button REFUSES when any product category on the bill is set to Automated
valuation, and names them. Supporting it there means recoding the bill line to
the interim account and dating the stock valuation layer as well.

It does build the documents in the order perpetual valuation would require
anyway - order, then receipt, then the bill posted by hand - so the sequence is
right even where the accounting is not yet handled.

UNITS: this module copies the bill line's unit onto the order line, so a bill
line in a unit the product is not sold in is converted silently when the
receipt is validated - five sheets of poly once became 1.68. That is guarded at
the source by amh_variant_fields, which requires a vendor's unit to be one the
product is actually sold in.
    """,
    "author": "Aaron Martin Harness Ltd",
    "license": "LGPL-3",
    "depends": [
        "purchase_stock",
    ],
    "data": [
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
}
