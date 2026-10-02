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

Remittance advice emails go to the vendor's `x_studio_eft_remittance_email`
field if it exists (a Studio field on the contact), otherwise to the vendor's
main email address.

## License
LGPL-3
