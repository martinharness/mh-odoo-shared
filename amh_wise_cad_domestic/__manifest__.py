{
    "name": "AMH Wise CAD Domestic EFT",
    "version": "19.0.1.5.0",
    "category": "Accounting/Payment",
    "summary": "Pay Canadian vendors in CAD through Wise batch payments",
    "description": """
Adds a Wise CAD Domestic EFT outbound payment method for Canadian companies.

The payment method is available on CAD bank journals that opt in and uses the
existing Wise connection supplied by Odoo's United States - Direct Deposit
module. Canadian recipient accounts are created from the vendor bank account's
9-digit Financial Institution ID Number (0 + institution + transit), account
number, and account type. From selected vendor bills, Odoo can create the
accounting payments and the draft Wise batch in one step. A completed Wise batch
can then be funded directly from the company's Wise CAD balance after an
explicit confirmation. A remittance advice can be emailed to each vendor.
    """,
    "author": "Aaron Martin Harness Ltd",
    "license": "LGPL-3",
    "depends": [
        "l10n_us_direct_deposit",
        "l10n_ca_payment_cpa005",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/account_payment_method_data.xml",
        "data/repair_payment_method_links.xml",
        "views/account_journal_views.xml",
        "views/res_partner_bank_views.xml",
        "views/account_payment_register_views.xml",
        "views/account_batch_payment_views.xml",
        "wizards/wise_cad_balance_funding_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
