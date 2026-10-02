"""A drop folder that behaves like the old Google Drive one.

Why this exists
---------------
The email route cannot be relied on here. Odoo's fetchmail asks IMAP for
UNSEEN messages only (``addons/mail/models/fetchmail.py``,
``check_unread_messages``), and the mailbox it reads is a person's own inbox.
Whoever opens the mail first wins: if Aaron reads the invoice before the
five-minute poll, Odoo never sees it and never will. Measured on 15 Aug 2026 -
of fourteen invoice mails sitting in the inbox, Odoo got three, and the eleven
it lost look identical to "the module did nothing".

A folder has no read flag to race over. Drop a PDF in - or forward it to the
folder's own alias, which is how it arrives now - the cron takes it, and the
document stays put until it has been dealt with. Nothing is ever silently
consumed.

How it hangs together
---------------------
``Unposted Invoices`` and ``Posted Invoices`` are created under whatever folder
the Purchases journal is already wired to in Documents (that mapping lives in
``documents.account.folder.setting`` and is per-company, so it has no xmlid to
reference - it is resolved at run time and remembered in ir.model.data).

Every new PDF becomes a draft vendor bill carrying that PDF. What happens next
depends on whether a parser recognises the paper:

* recognised - the draft goes through the same engine the emailed bills use, so
  there is one code path and one set of rules about what may post. Nearly all
  of these post themselves to the penny;
* not recognised - the draft is handed to Odoo's own invoice digitisation
  (OCR) and marked ``ocr``. OCR fills in the vendor, the date and the total,
  and a human checks it. This is the whole tail of the vendor list: in
  September 2026, 91 of 204 bills were keyed by hand across 62 vendors, and
  writing a parser for a vendor who sends two invoices a year will never pay
  for itself.

Before October 2026 the second case got no draft at all - the document was
annotated and left, on the reasoning that an empty draft is just litter. That
reasoning died with the credits: the draft is not empty any more, OCR fills
it, so leaving the document behind only means the same typing done by hand.

OCR OWNS WHAT IT IS GIVEN. A draft marked ``ocr`` is never offered to the
parsers again - the cron's own domain and ``retry_unrecognised.py`` both skip
that state deliberately. Adding a parser for a vendor therefore only affects
invoices that arrive AFTER it; the ones OCR already holds are finished by hand,
and the alternative is a second bill appearing underneath one somebody has
already been editing.

A document is linked to its draft through ``res_model``/``res_id``, which is
also how the scan knows not to pick it up twice. It moves to ``Posted
Invoices`` once the bill it belongs to is actually posted - not before, so the
inbox folder is an accurate to-do list at all times.

There is only ever one copy of the PDF. A ``documents.document`` owns an
``ir.attachment``, so pointing that attachment at the draft is what puts the
file on the bill and is where the engine reads it from. An earlier version made
its own second copy to read from and left both behind, so 77 of the first 325
bills ended up carrying the same invoice twice.

What is NOT an invoice
----------------------
The folder has an email alias on it, so whatever Outlook forwards lands here -
including the artwork out of everybody's signature block. Those arrive as small
images and would now each become a draft bill, which is far worse than the
clutter they used to be. Anything that is not a PDF is therefore kept away from
the OCR, and a small image is moved to the Documents trash, which is exactly
what Aaron was doing by hand.
"""
import logging

from odoo import _, api, fields, models

from .parsers import base as parser_base

_logger = logging.getLogger(__name__)

INBOX_KEY = "amh_documents_folder_inbox"
POSTED_KEY = "amh_documents_folder_posted"
INBOX_NAME = "Unposted Invoices"
POSTED_NAME = "Posted Invoices"

# An email signature logo is a few kilobytes; a photographed or scanned invoice
# is hundreds. Below this an image is junk and goes to the trash; above it the
# document is left in the folder with a note, because throwing away somebody's
# scan of a bill is not a mistake this module gets to make on a guess.
JUNK_IMAGE_MAX_BYTES = 150 * 1024

# account.move inherits ExtractMixin (Enterprise), and the entry point has
# moved between versions. action_manual_send_for_digitization is the one
# verified present on this database on 2 Oct 2026 - it is what the form's own
# "Send for Digitization" button calls - and _autosend_for_digitization is
# preferred ahead of it only because it is Odoo's own automatic path. Neither
# is relied on: see _amh_request_digitisation.
DIGITISE_METHODS = ("_autosend_for_digitization", "action_manual_send_for_digitization")

# Credits are Aaron's money, so the company's own Digitization setting decides
# whether this module may spend one, and it is checked HERE rather than left to
# whichever method answers above. Anything other than auto_send means the draft
# is made and left alone, with the vendor bill form's own Send for Digitization
# button right there if he wants it.
DIGITISE_MODE_FIELD = "extract_in_invoice_digitalization_mode"


class AccountMoveDocuments(models.Model):
    _inherit = "account.move"

    # Extended rather than edited into STATES in account_move.py so the engine
    # file stays free of anything that is only true when Documents is around.
    # ondelete is mandatory on a stored selection_add; a bill left pointing at
    # a state this module no longer ships should look unimported, not crash.
    amh_import_state = fields.Selection(
        selection_add=[("ocr", "Sent to OCR")],
        ondelete={"ocr": "set null"})

    # ------------------------------------------------------------------
    # folders
    # ------------------------------------------------------------------
    @api.model
    def _amh_documents_enabled(self):
        return "documents.document" in self.env

    @api.model
    def _amh_purchase_journal(self):
        return self.env["account.journal"].sudo().search(
            [("type", "=", "purchase")], order="sequence, id", limit=1)

    @api.model
    def _amh_documents_parent(self, journal):
        """The Documents folder the Purchases journal already files into.

        Created per company by ``documents_account``, so it carries no xmlid
        and cannot be referenced from data - look it up. Falls back to the
        Finance folder, and then to the root, so a database that has never had
        the accounting/documents bridge configured still gets a usable place.
        """
        if "documents.account.folder.setting" in self.env:
            setting = self.env["documents.account.folder.setting"].sudo().search(
                [("journal_id", "=", journal.id)], limit=1)
            if setting and setting.folder_id:
                return setting.folder_id
        finance = self.env.ref("documents.document_finance_folder",
                               raise_if_not_found=False)
        return finance or self.env["documents.document"].browse()

    @api.model
    def _amh_documents_folder(self, key, name, parent, company):
        """Find-or-create one of our folders, remembering it by xmlid.

        Registering the xmlid ourselves means the folder survives being
        renamed or dragged elsewhere in the Documents tree: we follow the
        record, not the name. Creating it lazily means no post_init hook and
        nothing to repair if the module is upgraded.
        """
        Document = self.env["documents.document"].sudo()
        existing = self.env.ref("amh_vendor_bill_import.%s" % key,
                                raise_if_not_found=False)
        if existing and existing.exists():
            return existing
        folder = Document.create({
            "name": name,
            "type": "folder",
            "folder_id": parent.id if parent else False,
            "company_id": company.id if company else False,
        })
        self.env["ir.model.data"].sudo().create({
            "module": "amh_vendor_bill_import",
            "name": key,
            "model": "documents.document",
            "res_id": folder.id,
            "noupdate": True,
        })
        _logger.info("amh_vendor_bill_import: created Documents folder %r (id %s)",
                     name, folder.id)
        return folder

    @api.model
    def _amh_documents_folders(self):
        """(inbox, posted), or (None, None) when Documents is not installed."""
        if not self._amh_documents_enabled():
            return None, None
        journal = self._amh_purchase_journal()
        if not journal:
            return None, None
        parent = self._amh_documents_parent(journal)
        company = journal.company_id
        inbox = self._amh_documents_folder(INBOX_KEY, INBOX_NAME, parent, company)
        posted = self._amh_documents_folder(POSTED_KEY, POSTED_NAME, parent, company)
        return inbox, posted

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    @api.model
    def _amh_scan_documents(self, limit=100):
        """Turn new PDFs in the inbox folder into bills. Never raises."""
        if not self._amh_documents_enabled():
            return True
        inbox, posted = self._amh_documents_folders()
        if not inbox:
            return True
        journal = self._amh_purchase_journal()

        docs = self.env["documents.document"].sudo().search([
            ("folder_id", "=", inbox.id),
            ("type", "=", "binary"),
            ("res_model", "!=", "account.move"),
        ], limit=limit, order="id")
        for doc in docs:
            try:
                self._amh_scan_one_document(doc, journal)
                self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the rest
                self.env.cr.rollback()
                _logger.exception("Documents intake failed on document %s", doc.id)
                try:
                    self._amh_note_on_document(
                        doc, _("Could not be processed: %s") % exc)
                    self.env.cr.commit()
                except Exception:  # noqa: BLE001
                    self.env.cr.rollback()
        return True

    @api.model
    def _amh_note_on_document(self, doc, note):
        """Say on the document why it is still sitting there. Idempotent."""
        if (doc.description or "") != note:
            doc.sudo().write({"description": note})

    @api.model
    def _amh_scan_one_document(self, doc, journal):
        if not self._amh_document_is_pdf(doc):
            return self._amh_set_aside_non_pdf(doc)

        payload = doc.sudo().raw
        if not payload:
            return False

        # No text is not a failure any more. A scan or a photograph read as a
        # PDF yields nothing to a parser and is precisely what OCR is for, so
        # an unreadable PDF takes the same road as an unrecognised one rather
        # than dead-ending with "this PDF could not be opened".
        parser = None
        try:
            text = parser_base.extract_text(payload)
        except parser_base.MissingPdfLibrary:
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.info("Documents intake: no text out of document %s (%s) - "
                         "handing it to the digitisation instead", doc.id, exc)
        else:
            parser = parser_base.find_parser(text)

        # company comes from the environment, not from the values: account.move
        # computes company_id off the journal and writing it directly is not
        # portable between versions
        draft = self.sudo().with_company(journal.company_id).create({
            "move_type": "in_invoice",
            "journal_id": journal.id,
        })
        # This is what attaches the PDF to the draft. A documents.document owns
        # an ir.attachment, so moving the document moves the file, and making a
        # second attachment here would leave the bill holding two copies of the
        # same invoice for the rest of its life.
        #
        # res_model and res_id are stored on the document in this version
        # rather than related to the attachment, so the attachment is pointed
        # at the draft explicitly rather than trusting Documents to push the
        # values across. Where it does, the second write changes nothing.
        doc.sudo().write({"res_model": "account.move", "res_id": draft.id})
        doc.sudo().attachment_id.write({
            "res_model": "account.move", "res_id": draft.id})

        if not parser:
            return self._amh_hand_to_ocr(doc, draft)

        draft._amh_import_one()
        self._amh_settle_document(doc, draft)
        return True

    # ------------------------------------------------------------------
    # not an invoice
    # ------------------------------------------------------------------
    @api.model
    def _amh_document_is_pdf(self, doc):
        """Only a PDF is a candidate. Nothing else is offered to the OCR.

        Checked on the mimetype first and the extension second, because mail
        gateways do set application/octet-stream on a perfectly good PDF.
        """
        if (doc.mimetype or "") == "application/pdf":
            return True
        return (doc.name or "").lower().endswith(".pdf")

    @api.model
    def _amh_set_aside_non_pdf(self, doc):
        """Keep everything that is not a PDF out of the bills.

        The small-image case is the signature artwork that rides along with
        every forwarded mail, and it is thrown away: Odoo's trash keeps it for
        the configured delay, so this is undoable, and leaving it in place
        would now cost a draft bill and an OCR credit each.

        Anything else - a spreadsheet, a Word document, a large image that
        might be a scan of a bill - is noted and left exactly where it is. A
        human can see it in the folder, and no guess is made about it.
        """
        mimetype = doc.mimetype or ""
        size = doc.file_size if "file_size" in doc._fields else 0
        if mimetype.startswith("image/") and 0 < (size or 0) <= JUNK_IMAGE_MAX_BYTES:
            if "active" in doc._fields:
                _logger.info(
                    "Documents intake: trashing %r (document %s, %s, %s bytes) - "
                    "not an invoice", doc.name, doc.id, mimetype, size)
                self._amh_note_on_document(doc, _(
                    "Not an invoice: a small image, which is almost always the "
                    "artwork out of an email signature. Moved to the trash."))
                doc.sudo().action_archive()
                return True
        return self._amh_note_on_document(doc, _(
            "Not a PDF, so nothing here can read it and it was not sent to the "
            "digitisation. Left for you."))

    # ------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------
    @api.model
    def _amh_hand_to_ocr(self, doc, draft):
        """Give the draft to Odoo's invoice digitisation and stand down.

        The marker is what keeps this bill out of the parsers' way for good -
        see the module docstring. It is written BEFORE the digitisation is
        asked for, so that a failure in the OCR leaves a draft a human can
        finish rather than one the cron will keep picking at.
        """
        note = _("Sent to Odoo's invoice digitisation. A draft vendor bill is "
                 "waiting in Vendor Bills for you to check and post.")
        draft.sudo().write({"amh_import_state": "ocr", "amh_import_note": note})
        self._amh_request_digitisation(draft, doc.sudo().attachment_id)
        self._amh_note_on_document(doc, note)
        return True

    @api.model
    def _amh_request_digitisation(self, move, attachment):
        """Ask the extract service to read this bill. Never raises.

        Two steps, because either one on its own is not enough:

        1. set the main attachment. That is what the extract mixin hooks, and
           it is also what makes the PDF show up in the bill's preview pane
           beside the fields a human is checking. ``no_document=True`` is the
           documents module's own opt-out, and without it Odoo raises "This
           attachment is already a document" - which it is, because the file
           IS the dropped document;
        2. if the bill is still sitting at no_extract_requested afterwards,
           ask explicitly. Whether step 1 triggers the send is an Enterprise
           implementation detail that has moved between versions, and the
           ``extract_state`` check means asking twice cannot cost two credits.

        Everything here is optional: ``account_invoice_extract`` is Enterprise
        and may not be installed, and the credits can run out. None of that is
        worth losing the draft over, so a failure is logged on the bill and the
        bill is left for a human - which is still a draft with its PDF on it,
        and better than the document nobody made a bill for.
        """
        move = move.sudo().with_context(no_document=True)
        # The document's own attachment, not _amh_pdf_attachments(): a mail
        # gateway will happily label a perfectly good PDF
        # application/octet-stream, and that filter would then find nothing.
        if not attachment:
            return False
        if "message_main_attachment_id" in move._fields and \
                not move.message_main_attachment_id:
            try:
                move.write({"message_main_attachment_id": attachment.id})
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Could not set the main attachment on bill %s: %s",
                                move.id, exc)

        if "extract_state" not in move._fields:
            _logger.info("No invoice digitisation on this database - bill %s is "
                         "a draft with its PDF and nothing more", move.id)
            return False
        if move.extract_state != "no_extract_requested":
            return True

        company = move.company_id
        mode = company[DIGITISE_MODE_FIELD] \
            if DIGITISE_MODE_FIELD in company._fields else None
        if mode != "auto_send":
            _logger.info("Digitisation for vendor bills is set to %r, so bill %s "
                         "was left alone rather than spending a credit",
                         mode, move.id)
            return False

        for name in DIGITISE_METHODS:
            method = getattr(move, name, None)
            if method is None:
                continue
            try:
                method()
            except Exception as exc:  # noqa: BLE001 - see docstring
                _logger.exception("Digitisation refused bill %s", move.id)
                move.message_post(body=_(
                    "Could not send this bill for digitisation (%s). Its PDF is "
                    "attached; it needs coding by hand.") % exc)
            return True
        _logger.info("Found no digitisation entry point on account.move - bill %s "
                     "is a draft with its PDF and nothing more", move.id)
        return False

    # ------------------------------------------------------------------
    # settle
    # ------------------------------------------------------------------
    @api.model
    def _amh_settle_documents(self, limit=200):
        """Move documents whose bill has since posted into Posted Invoices.

        Runs after the draft pass, so a document that was parked on an earlier
        run - waiting on a receipt, or on a line being added to the order -
        files itself away the moment the bill it belongs to goes through. The
        OCR drafts come through here too: the document follows the bill into
        Posted Invoices when a human posts it.
        """
        if not self._amh_documents_enabled():
            return True
        inbox, posted = self._amh_documents_folders()
        if not inbox or not posted:
            return True
        docs = self.env["documents.document"].sudo().search([
            ("folder_id", "=", inbox.id),
            ("res_model", "=", "account.move"),
            ("res_id", "!=", 0),
        ], limit=limit)
        for doc in docs:
            move = self.sudo().browse(doc.res_id)
            if move.exists():
                self._amh_settle_document(doc, move)
        return True

    @api.model
    def _amh_settle_document(self, doc, move):
        """File the document if its bill is done with, or explain the hold-up."""
        _inbox, posted = self._amh_documents_folders()
        bill = move.amh_successor_move_id or move
        if bill.state == "posted":
            doc.sudo().write({
                "folder_id": posted.id,
                "res_id": bill.id,
                "description": _("Posted as %s.") % bill.display_name,
            })
            # Keep the file with the document, for the same reason as above.
            doc.sudo().attachment_id.write({
                "res_model": "account.move", "res_id": bill.id})
            return True
        note = move.amh_import_note or bill.amh_import_note
        if note:
            self._amh_note_on_document(doc, note)
        return False
