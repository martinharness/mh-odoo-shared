# AMH Vendor Bill Import

Reads a vendor's own invoice PDF out of a Documents drop folder, matches it to
the purchase order, and posts a bill that agrees with the paper to the penny.
Anything it is not certain about is parked with a plain-English reason instead
of being guessed at.

Built and run in production at Aaron Martin Harness Ltd on Odoo 19 Enterprise
(Odoo.sh). Shared as a starting point: the engine is vendor-agnostic, and the
two parsers that ship with it are worked examples to copy.

## How a bill travels

1. The PDF lands in **Unposted Invoices** in Documents. Drag it in, or give
   the folder a mail alias and forward invoices to it.
2. Every 15 minutes the cron reads each new PDF and makes a draft vendor bill
   carrying it. Then one of two things happens:
   - **a parser recognises the paper** - the bill is matched to its purchase
     order, the receipt validated if the vendor shipped in full, every line
     priced off the printed line total, and the bill posted. Or parked, with
     the reason;
   - **no parser recognises it** - the draft is marked `ocr` and handed to
     Odoo's invoice digitisation, which fills in the vendor, date and total
     for a human to check. One credit per document.
3. Posted ones move themselves to **Posted Invoices** with the bill number on
   them. Parked ones stay put, with the reason written on the document, so
   that folder is always an honest to-do list.
4. A daily digest lists everything parked.

## What it refuses to do

- A receipt is validated **only** when the vendor shipped every line in full.
  Posting a bill can be reset to draft; moving inventory cannot be unwound so
  cheaply. A part shipment is parked with the quantities spelled out - receive
  what actually arrived and the next run posts it.
- Nothing posts unless `amount_total` equals the printed total exactly. A
  mismatch is a parked bill, never a rounded-off one.
- An invoice printing no P.O. number is matched on contents, and that is the
  one guess the module makes - so the bar is higher than ordinary line
  matching. Every charged line must pair to a distinct order line **by product
  code**, the ordered quantities must agree line for line, the order must
  carry nothing the invoice does not bill, and exactly one open order may
  qualify. Two candidates is a park, not a coin toss.
- Nothing that is not a PDF is sent to the digitisation. A small image (under
  150 KB) is moved to the Documents trash, because a mail alias on a folder
  collects everybody's signature artwork and each one would otherwise cost a
  draft bill and a credit. Anything else is noted and left alone.
- Once a document is handed to the OCR it belongs to the OCR. A parser added
  later only affects invoices arriving **after** it, so a second bill can
  never appear underneath one somebody has been editing.

## Configuration

System parameters, all with defaults, all account **codes** rather than ids:

| Parameter | Default | What it is |
| --- | --- | --- |
| `amh_vendor_bill_import.expense_account` | `54100` | ordinary expense charges (freight) |
| `amh_vendor_bill_import.brokerage_account` | `51400` | customs-broker fees |
| `amh_vendor_bill_import.card_fees_account` | `73300` | card-processing fees |
| `amh_vendor_bill_import.pass_through_account` | `23100` | tax a broker paid on your behalf and is recharging |
| `amh_vendor_bill_import.carrier_tax_rate` | `13` | which tax line a blended carrier tax lands on |
| `amh_vendor_bill_import.digest_email` | *(empty)* | where the daily review mail goes; empty means the cron user |

Change the codes to match your chart of accounts before turning the cron on.
A charge whose account code is not in your chart **parks the bill and names
the parameter to set** - it is never quietly coded somewhere else.

Both crons ship **disabled**. Enable *Vendor Bills: read PDFs, match POs,
post* once the parked/posted split looks right on a real batch, and *Vendor
Bills: review digest* when you want the daily mail. `Import vendor bill now`
is a server action on any vendor bill, from the list or the form, for testing
one document.

The two Documents folders are created on first run under whatever folder your
Purchases journal is mapped to in Documents, and tracked by xmlid - so
renaming or moving them in the Documents tree does not break the intake.
`documents` is deliberately **not** in `depends`: it is Enterprise, and the
engine stays installable without it.

## Writing a parser

Subclass `VendorInvoiceParser` in `models/parsers/`, implement `detect` and
`parse`, and import it in that package's `__init__.py`. The engine in
`account_move.py` never mentions a vendor by name. `base.py` documents the
dict `parse` must return; there are two shapes:

- **goods against a purchase order** - return the lines, quantities, prices and
  totals. `harness_hardware.py` is the worked example: a fixed-width report
  writer, parsed by character column, 38 consecutive invoices reproduced
  exactly;
- **an expense with no order behind it** - carriers, brokers, card processors.
  Return `expense: True` and describe the money: `charges` (each with a
  `tax_rate` and optionally a `role` naming which account it belongs in),
  `pass_through`, and what the invoice says the tax and total are.
  `freightcom.py` is the worked example.

`name` must equal the vendor's name in Odoo - that is how the engine finds the
partner.

### The lessons that cost the most to find

**Write against what the engine reads.** The engine runs the PDF through pypdf
itself. `ir.attachment.index_content` - the text Odoo extracts for search, and
the only text visible from outside the server - produces the same figures in
the same order but breaks lines in *different places*. Five parsers shipped
here anchored on labels that existed in one and not the other, and every bill
parked. `account.move.amh_engine_text(attachment_id, start, length)` returns
the engine's own reading of an attachment over RPC, for exactly this purpose.
Use it.

**Prove the money with arithmetic, not position.** No PDF text extractor puts a
column back together the way a reader does: sometimes each label is followed by
its figure, sometimes every label comes first and every figure after, and which
way round it comes out varies between invoices from the same vendor in the same
month. One real near-miss: a carrier's summary read `GST/HST Subtotal Charges
Subtotal 15.10 116.30`, so a regex looking for the figure after "Charges
Subtotal" found the *tax*. Swapped, the two still added up to the printed
total, so a total check alone would not have caught it. Read figures off their
own labels, then make the invoice's own arithmetic prove which is which:
components must sum to the printed subtotal, subtotal plus tax must equal the
printed total, and a tax must be its own rate times its own base.

**Adding up is evidence, not proof.** An early parser looked for any run of
printed figures summing to the amount due. On two of seven invoices it found a
run that added up perfectly and was the wrong run.

**Equality, not "at least".** When checking whether a part shipment is already
received, compare received-less-invoiced to the shipped quantity for equality.
Odoo bills whatever is received and uninvoiced, so if the vendor shipped 60 of
100 and all 100 have been received, the bill comes to the right money at the
wrong quantity and eats 100 of the order. It balances to the penny and is
still wrong, which is the one failure nobody catches by eye.

**Layouts move under you.** One vendor here moved its GST line to the other
side of the subtotal between two months and started printing "Total Amount
Due" as "Total Amount Du", with the *e* missing. Another prints its own
invoice number in every page footer while also naming the *previous* invoice
in a shipment-history section, so taking the first match bills this month's
money against last month's reference. Read the labelled one, and require every
occurrence to agree.

### Odoo 19 traps worth knowing

- `account.move.line.display_type` is **never** `False` - the selection has no
  False member. An ordinary line is `'product'`. `if line.display_type:
  continue` therefore skips every line, silently. (`purchase.order.line.
  display_type` is a different field and *is* False for ordinary lines.)
- Writing `message_main_attachment_id` makes `documents_account` try to file
  that attachment as a new document, and if it already is one Odoo raises
  "This attachment is already a document" and the write fails. Pass
  `no_document=True` in the context - the documents module's own opt-out.
- `message_main_attachment_id` is not a `mail.thread` field in 19;
  `account.move` declares it and `sale.order` does not. Guard with
  `in record._fields`.
- Cron records shipped as data need `noupdate="1"`, or every version bump
  re-applies the shipping default and switches your running automation back
  off. See the comment in `data/ir_cron.xml`.
- Two fields with the same **label** on one model makes every build amber.
- `ir.cron` has no `numbercall` field since 17, and the invoice list view
  carries no `state` field in 19 - only `status_in_payment`.
- On Odoo.sh a module is upgraded only when the manifest **version** moves. A
  Python-only change takes effect on the restart alone, so a new field, view or
  data file without a version bump goes green and never reaches the database.

## PDF library

None to install. Odoo ships `pypdf` on Python 3.13+, `PyPDF2` 2.x on 3.11/3.12
and `PyPDF2` 1.26 below that; `parsers/base.py` uses whichever is present.
pypdf and PyPDF2 2.12.1 produce byte-identical text on all 38 reference
invoices, so a parser's column offsets hold for both. PyPDF2 1.26 is a
different extractor and is unverified - if bills start parking with *"line
totals do not sum to the printed subtotal"*, that is the symptom; add `pypdf`
to a `requirements.txt` at the repository root and rebuild.

## Not in this copy

Two things in the original are deliberately left out, because they are
specific to one tenant rather than useful anywhere:

- **the other eight parsers** (parcel carriers, a customs broker, a card
  processor). They encode one company's suppliers' layouts and would only be
  noise here;
- **the mail-gateway fallback.** Odoo's fetchmail asks IMAP for *unread*
  messages only, and if the mailbox it reads is a person's own inbox, whoever
  opens the mail first wins - measured here as eleven of fourteen invoices
  lost, indistinguishable from the module doing nothing. The cure is not more
  code: give the Documents folder its own mail alias on a domain Odoo accepts
  mail for directly, so no IMAP poll is involved at all. That is what the
  folder intake does.

## License

LGPL-3
