{
    "name": "AMH Vendor Bill Import",
    "version": "19.0.7.0.0",
    "summary": "Read a vendor's own invoice PDF, match it to the purchase order, and post a bill that agrees to the penny.",
    "description": """
AMH Vendor Bill Import
======================

Drop a vendor's invoice PDF into a Documents folder. This module reads it,
finds the purchase order it belongs to, and either posts a bill that matches
the paper exactly - to the penny - or parks it with a plain-English reason and
leaves the document sitting there. A PDF no parser recognises is handed to
Odoo's own invoice digitisation instead, so nothing is left for somebody to
key by hand twice.

The engine is vendor-agnostic: everything that knows about one vendor's paper
lives in ``models/parsers/``, and adding a vendor is one new file. Two worked
examples ship with it - a fixed-width purchase-order invoice and a bilingual
expense invoice.

See README.md for how a bill travels, what the module refuses to do, every
system parameter, and a full guide to writing a parser - including the
mistakes that cost the most to find.
""",
    "author": "Aaron Martin Harness Ltd",
    "category": "Accounting/Accounting",
    "license": "LGPL-3",
    "depends": ["purchase_stock", "account"],
    "data": [
        "data/ir_cron.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
}
