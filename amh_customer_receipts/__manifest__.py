{
    "name": "Customer Receipts",
    "version": "19.0.3.0.0",
    "category": "Accounting/Accounting",
    "summary": "Key a pile of customer cheques or e-Transfers straight into native Odoo payments, one customer at a time",
    "description": """
Odoo takes customer payments one invoice at a time, through the Pay wizard on
the invoice list: a search, a payment-method change away from the journal
default, and a memo cleared out to type a cheque number into - times fifty when
a deposit is fifty cheques. account.batch.payment does not help; it only groups
payments that already exist. This is the Receive Payments screen EBMS and
QuickBooks have and Odoo does not.

**Accounting > Customers > Customer Receipts.** The cursor lands on the
customer; their open invoices load beneath. Type the cheque/reference and the
amount, and the invoices it settles are ticked for you - a single open invoice
ticks itself, and typing an invoice number pulls up its customer. Hit Receive
Payment (Alt+Q) and a native Odoo customer payment is created for the ticked
invoices - no separate document - then the screen clears for the next customer
with the cursor back on the customer field. The journal and payment method
default to the ones you used last.

Built for keyboard-only entry: Customer -> Cheque/Reference -> Amount by Tab, so
a stack of cheques is keyed without reaching for the mouse. Small over/under
differences can be written off within a limit. Everything posts through Odoo's
own account.payment.register, so allocation, partial payments, write-offs and
multi-invoice cheques behave exactly as they do from the invoice list.

See README.md for the full write-up.
    """,
    "author": "Aaron Martin Harness Ltd",
    "website": "https://github.com/martinharness/mh-odoo-shared",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/customer_receipt_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "amh_customer_receipts/static/src/js/receipt_autofocus.js",
        ],
    },
    "installable": True,
    "application": False,
}
