{
    "name": "AMH Wise CAD Domestic EFT",
    "version": "19.0.1.9.0",
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

The Wise recipient id each vendor bank account is linked to is shown on the bank
account, with a Forget button beside it. Odoo stores that id but shows it
nowhere, so when Wise stops recognising one - a recipient deleted, or created on
a profile the company no longer pays from - every payment to that vendor fails
at the quote with "We couldn't find an account with that ID" and nothing in the
interface says whose it is or lets it be cleared. Forgetting it makes the next
payment match or create the recipient again. The field stays readonly: it can be
emptied, never typed into, because a mistyped recipient id is a payment into
somebody else's account.

Note for whoever edits that form next: hr wraps the part of the bank account
form around the Wise transfer type in a div that is invisible unless the account
belongs to an employee with more than one. A field placed after
wise_account_type is therefore in the arch and on nobody's screen. Anchor on
l10n_ca_financial_institution_number, and when a field will not appear, walk up
its ancestors rather than trusting the arch.

A batch too large to move into Wise in one go - more than 25,000 cannot be sent
to Wise instantly on the Canadian side, and an EFT takes up to a business day -
can instead be initiated now and have its funding booked for a datetime with
Initiate & Schedule Fund. Wise creates and completes the batch group, nothing
moves, and a cron funds it from the balance at the time set. The money travels
to Wise in the meantime. One attempt is made and the schedule is cleared before
it, so a batch is never funded twice; if the balance is short, Wise's refusal is
posted on the batch and the funding waits for a person.

Two things about balance funding that cost a live pay-run to find, both on
3 Oct 2026, both in models/account_batch_payment_funding.py:

* the guard that refuses to fund a batch whose payments are badly dated tests
  date > today. It used to test date != today while calling its result
  future_payments, which refused a batch prepared the evening before - every
  overnight batch - with a UserError whose dialog has nothing but Close, and
  whose only suggested remedy was resetting every posted payment to draft;
* the request to fund from the balance is STORED on the batch
  (amh_fund_from_balance_requested), not carried in the context. A Wise batch
  whose payments are not dated today always raises a validation warning, core
  answers a warning with its own dialog, and that dialog's "Proceed with
  validation" button calls validate_batch() fresh - so a context flag set by
  the funding wizard is gone by the time _send_after_validation runs. The batch
  initiated, the funding silently did not happen, and Odoo handed the user off
  to the Wise website to fund transfers he had just asked Odoo to fund.
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
        "data/ir_cron.xml",
        "views/account_journal_views.xml",
        "views/res_partner_bank_views.xml",
        "views/account_payment_register_views.xml",
        "views/account_batch_payment_views.xml",
        "wizards/wise_cad_balance_funding_wizard_views.xml",
        "wizards/wise_balance_schedule_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
