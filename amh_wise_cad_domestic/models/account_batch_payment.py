import re

from odoo import models
from odoo.exceptions import UserError
from odoo.tools import format_date, formatLang
from odoo.addons.l10n_us_direct_deposit.models.wise_request import Wise

from .account_payment_method import WISE_CAD_PAYMENT_METHOD_CODE


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    def _compute_wise_payments_enabled(self):
        super()._compute_wise_payments_enabled()
        for batch in self.filtered(
            lambda item: item.payment_method_code == WISE_CAD_PAYMENT_METHOD_CODE
        ):
            # Keep the normal Validate action hidden for this electronic payment
            # method even when the Wise connection is missing. The user must use
            # Initiate Payment, which then reports the configuration error rather
            # than accidentally marking the payments as sent without contacting Wise.
            batch.wise_payments_enabled = True

    def check_payments_for_errors(self):
        self.ensure_one()
        errors = super().check_payments_for_errors()
        if self.payment_method_code != WISE_CAD_PAYMENT_METHOD_CODE:
            return errors

        # The parent Wise module is USD-only and adds this error for every CAD
        # payment. Remove only that known parent-module validation, then apply
        # the CAD-specific checks below.
        usd_only_title = self.env._("All payments in the batch must be in USD.")
        errors = [error for error in errors if error.get("title") != usd_only_title]

        payments = self.payment_ids
        if not self.company_id.wise_connected:
            errors.append({
                "title": self.env._("The company is not connected to Wise."),
                "records": payments,
                "help": self.env._(
                    "Connect the company to Wise in Accounting settings before "
                    "initiating this batch."
                ),
            })

        non_cad_payments = payments.filtered(
            lambda payment: payment.currency_id.name != "CAD"
        )
        if non_cad_payments:
            errors.append({
                "title": self.env._("All payments in the batch must be in CAD."),
                "records": non_cad_payments,
                "help": self.env._("Wise CAD Domestic EFT only supports CAD-to-CAD payments."),
            })

        missing_bank = payments.filtered(lambda payment: not payment.partner_bank_id)
        if missing_bank:
            errors.append({
                "title": self.env._("A recipient bank account is required."),
                "records": missing_bank,
                "help": self.env._("Select a trusted Canadian bank account on each vendor payment."),
            })

        untrusted_bank = payments.filtered(
            lambda payment: payment.partner_bank_id
            and not payment.partner_bank_id.allow_out_payment
        )
        if untrusted_bank:
            errors.append({
                "title": self.env._("The recipient bank account must be trusted."),
                "records": untrusted_bank,
                "help": self.env._("Enable Send Money on the vendor bank account before initiating the Wise batch."),
            })

        invalid_routing = payments.filtered(
            lambda payment: payment.partner_bank_id
            and not self._amh_wise_cad_routing_parts(payment.partner_bank_id)
        )
        if invalid_routing:
            errors.append({
                "title": self.env._("A valid Canadian institution and transit number is required."),
                "records": invalid_routing,
                "help": self.env._(
                    "Enter the 9-digit Financial Institution ID Number as 0 + "
                    "3-digit institution + 5-digit transit."
                ),
            })

        invalid_account = payments.filtered(
            lambda payment: payment.partner_bank_id
            and not self._amh_digits(payment.partner_bank_id.acc_number)
        )
        if invalid_account:
            errors.append({
                "title": self.env._("A Canadian recipient account number is required."),
                "records": invalid_account,
                "help": self.env._("Enter the vendor's domestic CAD bank account number."),
            })

        missing_address = payments.filtered(
            lambda payment: not self._amh_wise_cad_has_recipient_address(payment.partner_id)
        )
        if missing_address:
            errors.append({
                "title": self.env._("The recipient address is incomplete."),
                "records": missing_address,
                "help": self.env._(
                    "Wise requires the vendor's street, city, postal code, and country "
                    "when creating a recipient."
                ),
            })

        return errors

    def _send_after_validation(self):
        if self.payment_method_code != WISE_CAD_PAYMENT_METHOD_CODE:
            return super()._send_after_validation()

        self.ensure_one()
        if not self.company_id.wise_connected:
            raise UserError(
                self.env._(
                    "Connect the company to Wise in Accounting settings before "
                    "initiating this batch."
                )
            )
        validation_errors = self.check_payments_for_errors()
        if validation_errors:
            first_error = validation_errors[0]
            raise UserError(
                "%s\n%s"
                % (first_error.get("title", ""), first_error.get("help", ""))
            )

        wise_api = Wise(self.company_id)

        if not self.wise_batch_identifier:
            batch_group = wise_api.create_batch_group(
                currency="CAD",
                batch_name=self.name,
            )
            if wise_api.has_errors(batch_group):
                raise UserError(
                    self.env._(
                        "Failed to create a CAD batch on Wise:\n%(wise_error)s",
                        wise_error=wise_api.format_errors(batch_group),
                    )
                )

            self.wise_batch_identifier = batch_group["id"]
            self.wise_payment_status = "new"
            if self._can_commit():
                self.env.cr.commit()

        recipients_by_key = {}
        banks_without_wise_recipient = self.payment_ids.partner_bank_id.filtered(
            lambda bank: not bank.wise_bank_account
        )
        if banks_without_wise_recipient:
            all_recipients = wise_api.get_recipients()
            if wise_api.has_errors(all_recipients):
                raise UserError(
                    self.env._(
                        "Failed to load recipients from Wise:\n%(wise_error)s",
                        wise_error=wise_api.format_errors(all_recipients),
                    )
                )
            recipient_rows = (
                all_recipients.get("content", [])
                if isinstance(all_recipients, dict)
                else all_recipients
            )
            for recipient in recipient_rows or []:
                recipient_key = self._amh_wise_cad_recipient_key(recipient=recipient)
                if recipient_key:
                    recipients_by_key[recipient_key] = recipient

        for payment in self.payment_ids:
            bank_account = payment.partner_bank_id

            if not bank_account.wise_bank_account:
                recipient_key = self._amh_wise_cad_recipient_key(payment=payment)
                matched_recipient = recipients_by_key.get(recipient_key)
                if not matched_recipient:
                    matched_recipient = wise_api.create_recipient(
                        self._amh_prepare_wise_cad_recipient_data(payment)
                    )
                    if wise_api.has_errors(matched_recipient):
                        raise UserError(
                            self.env._(
                                "Failed to create a Canadian recipient on Wise:\n%(wise_error)s",
                                wise_error=wise_api.format_errors(matched_recipient),
                            )
                        )

                bank_account.wise_bank_account = str(matched_recipient["id"])
                if self._can_commit():
                    self.env.cr.commit()

            if not payment.wise_transfer_identifier:
                quote = wise_api.create_quote({
                    "sourceCurrency": "CAD",
                    "targetCurrency": "CAD",
                    "targetAmount": payment.amount,
                    "targetAccount": bank_account.wise_bank_account,
                    "payOut": "BALANCE",
                })
                if wise_api.has_errors(quote):
                    raise UserError(
                        self.env._(
                            "Failed to create a CAD quote on Wise:\n%(wise_error)s",
                            wise_error=wise_api.format_errors(quote),
                        )
                    )

                transfer = wise_api.create_transfer_in_batch(
                    self.wise_batch_identifier,
                    self._prepare_wise_transfer_data(payment, quote["id"]),
                )
                if wise_api.has_errors(transfer):
                    raise UserError(
                        self.env._(
                            "Failed to create a CAD transfer on Wise:\n%(wise_error)s",
                            wise_error=wise_api.format_errors(transfer),
                        )
                    )

                payment.wise_transfer_identifier = str(transfer["id"])
                if self._can_commit():
                    self.env.cr.commit()

        batch_information = wise_api.get_batch_group(self.wise_batch_identifier)
        if wise_api.has_errors(batch_information):
            raise UserError(
                self.env._(
                    "Failed to retrieve the CAD batch from Wise:\n%(wise_error)s",
                    wise_error=wise_api.format_errors(batch_information),
                )
            )

        status = wise_api.complete_batch_group(
            self.wise_batch_identifier,
            batch_information.get("version"),
        )
        if wise_api.has_errors(status):
            raise UserError(
                self.env._(
                    "Failed to complete the CAD batch on Wise:\n%(wise_error)s",
                    wise_error=wise_api.format_errors(status),
                )
            )

        self.wise_payment_status = status["status"].lower()
        wise_url = (
            "https://sandbox.transferwise.tech"
            if self.company_id.sudo().wise_environment == "sandbox"
            else "https://wise.com"
        )

        # Let account_batch_payment perform its normal post-validation work.
        super()._send_after_validation()

        # Keep final authorization/funding in Wise, matching Odoo's stock Wise
        # workflow. This avoids moving money automatically merely by validating
        # an Odoo batch.
        return {
            "type": "ir.actions.act_url",
            "url": f"{wise_url}/transactions/batch/{self.wise_batch_identifier}",
        }

    @staticmethod
    def _amh_digits(value):
        return re.sub(r"\D", "", value or "")

    def _amh_wise_cad_routing_parts(self, bank_account):
        routing_number = self._amh_digits(
            bank_account.l10n_ca_financial_institution_number
        )
        if len(routing_number) != 9 or not routing_number.startswith("0"):
            return False
        return routing_number[1:4], routing_number[4:9]

    @staticmethod
    def _amh_wise_cad_has_recipient_address(partner):
        return bool(
            partner.street
            and partner.city
            and partner.zip
            and partner.country_id.code
        )

    @staticmethod
    def _amh_normalized_name(value):
        return " ".join((value or "").casefold().split())

    @staticmethod
    def _amh_wise_cad_remittance_email(partner):
        """Address the Odoo remittance advice is emailed to.

        Prefer the vendor's dedicated EFT Remittance Email (a Studio field, so
        read defensively) and fall back to the partner's main email, so the
        advice reaches the accounts-payable / remittance inbox rather than the
        general order-desk email.
        """
        remittance_email = getattr(partner, "x_studio_eft_remittance_email", False)
        return remittance_email or partner.email

    # ------------------------------------------------------------------
    # Remittance advice
    # ------------------------------------------------------------------
    def action_amh_send_remittance(self):
        """Email a remittance advice to each vendor paid in this Wise batch.

        Wise's transfer reference is too short to carry more than one invoice
        number, so instead of relying on it the vendor is emailed a full
        remittance from Odoo -- one message per vendor, to the EFT Remittance
        Email (falling back to the main email), listing every bill this batch
        paid them, the amount sent, and the Wise reference.
        """
        self.ensure_one()
        if self.payment_method_code != WISE_CAD_PAYMENT_METHOD_CODE:
            raise UserError(
                self.env._("Remittance advice is only available for Wise CAD batches.")
            )

        Mail = self.env["mail.mail"].sudo()
        company = self.company_id or self.env.company
        from_email = company.email or self.env.user.email_formatted

        # One remittance per vendor, even when a vendor has several payments.
        payments_by_partner = {}
        for payment in self.payment_ids:
            if not payment.partner_id:
                continue
            payments_by_partner.setdefault(
                payment.partner_id.id, self.env["account.payment"]
            )
            payments_by_partner[payment.partner_id.id] |= payment

        sent, skipped = [], []
        for payments in payments_by_partner.values():
            partner = payments[:1].partner_id
            email = self._amh_wise_cad_remittance_email(partner)
            if not email:
                skipped.append(partner.display_name)
                continue

            total = sum(payments.mapped("amount"))
            subject = self.env._(
                "%(company)s - Remittance advice - %(amount)s",
                company=company.name,
                amount=formatLang(self.env, total, currency_obj=self.currency_id),
            )
            body = self._amh_build_remittance_body(partner, payments, company, total)
            mail = Mail.create({
                "subject": subject,
                "body_html": body,
                "email_from": from_email,
                "reply_to": company.email or from_email,
                "email_to": email,
                "auto_delete": False,
            })
            mail.send()
            sent.append("%s (%s)" % (partner.display_name, email))
            for payment in payments:
                payment.message_post(
                    body=self.env._(
                        "Wise remittance advice emailed to %(email)s.", email=email
                    )
                )

        parts = []
        if sent:
            parts.append(self.env._("Sent to: %s.", ", ".join(sent)))
        if skipped:
            parts.append(
                self.env._("No remittance email on file, skipped: %s.", ", ".join(skipped))
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning" if skipped else "success",
                "title": self.env._("Remittance advice"),
                "message": " ".join(parts) or self.env._("No vendor payments to send."),
                "sticky": bool(skipped),
            },
        }

    def _amh_build_remittance_body(self, partner, payments, company, total):
        """HTML body listing the bills this batch paid a single vendor."""
        currency = self.currency_id
        pay_date = format_date(self.env, self.date) if self.date else ""

        rows_html = ""
        for payment in payments:
            for bill in payment.reconciled_bill_ids:
                number = bill.ref or bill.name or ""
                bill_date = (
                    format_date(self.env, bill.invoice_date)
                    if bill.invoice_date else ""
                )
                amount = formatLang(
                    self.env, abs(bill.amount_total),
                    currency_obj=bill.currency_id or currency,
                )
                rows_html += (
                    "<tr>"
                    "<td style='padding:6px 12px;border-bottom:1px solid #eee;'>%s</td>"
                    "<td style='padding:6px 12px;border-bottom:1px solid #eee;'>%s</td>"
                    "<td style='padding:6px 12px;border-bottom:1px solid #eee;"
                    "text-align:right;'>%s</td>"
                    "</tr>"
                ) % (number, bill_date, amount)

        if not rows_html:
            # No bills linked (e.g. a payment on account): still show the amount.
            rows_html = (
                "<tr><td colspan='2' style='padding:6px 12px;'>%s</td>"
                "<td style='padding:6px 12px;text-align:right;'>%s</td></tr>"
            ) % (
                self.env._("Payment on account"),
                formatLang(self.env, total, currency_obj=currency),
            )

        total_str = formatLang(self.env, total, currency_obj=currency)
        wise_ref = self.wise_batch_identifier or ", ".join(
            payments.mapped("wise_transfer_identifier")
        )

        return (
            "<div style='font-family:Arial,sans-serif;color:#333;font-size:14px;'>"
            "<p>Hello %(vendor)s,</p>"
            "<p>This confirms a payment of <strong>%(total)s</strong> sent to you "
            "by <strong>%(company)s</strong> via Wise (CAD Domestic EFT)%(date)s.</p>"
            "<p>It was applied to the following invoice(s):</p>"
            "<table style='border-collapse:collapse;width:100%%;max-width:520px;'>"
            "<thead><tr>"
            "<th style='padding:6px 12px;text-align:left;border-bottom:2px solid "
            "#333;'>Invoice</th>"
            "<th style='padding:6px 12px;text-align:left;border-bottom:2px solid "
            "#333;'>Date</th>"
            "<th style='padding:6px 12px;text-align:right;border-bottom:2px solid "
            "#333;'>Amount</th>"
            "</tr></thead>"
            "<tbody>%(rows)s</tbody>"
            "<tfoot><tr>"
            "<td colspan='2' style='padding:8px 12px;text-align:right;"
            "font-weight:bold;'>Total paid</td>"
            "<td style='padding:8px 12px;text-align:right;font-weight:bold;'>"
            "%(total)s</td>"
            "</tr></tfoot>"
            "</table>"
            "%(ref)s"
            "<p style='margin-top:16px;'>Thank you,<br/>%(company)s</p>"
            "</div>"
        ) % {
            "vendor": partner.name or "",
            "total": total_str,
            "company": company.name or "",
            "date": (" on %s" % pay_date) if pay_date else "",
            "rows": rows_html,
            "ref": (
                "<p style='color:#666;font-size:12px;'>Wise reference: %s</p>" % wise_ref
            ) if wise_ref else "",
        }

    def _amh_wise_cad_recipient_key(self, payment=None, recipient=None):
        if payment:
            bank_account = payment.partner_bank_id
            routing_parts = self._amh_wise_cad_routing_parts(bank_account)
            if not routing_parts:
                return None
            institution_number, transit_number = routing_parts
            return (
                self._amh_normalized_name(
                    bank_account.acc_holder_name or payment.partner_id.name
                ),
                institution_number,
                transit_number,
                self._amh_digits(bank_account.acc_number),
                (bank_account.amh_wise_cad_account_type or "CHECKING").casefold(),
            )

        if recipient:
            if (recipient.get("type") or "").casefold() != "canadian":
                return None
            if (recipient.get("currency") or "CAD").upper() != "CAD":
                return None

            details = recipient.get("details") or {}
            recipient_name = recipient.get("name") or {}
            if isinstance(recipient_name, dict):
                recipient_name = recipient_name.get("fullName")
            recipient_name = recipient_name or recipient.get("accountHolderName")
            return (
                self._amh_normalized_name(recipient_name),
                self._amh_digits(details.get("institutionNumber")),
                self._amh_digits(details.get("transitNumber")),
                self._amh_digits(details.get("accountNumber")),
                (details.get("accountType") or "CHECKING").casefold(),
            )

        return None

    def _amh_prepare_wise_cad_recipient_data(self, payment):
        partner = payment.partner_id
        bank_account = payment.partner_bank_id
        routing_parts = self._amh_wise_cad_routing_parts(bank_account)
        if not bank_account or not routing_parts:
            raise UserError(
                self.env._(
                    "A valid Canadian bank account is required for %(vendor)s.",
                    vendor=partner.display_name,
                )
            )

        institution_number, transit_number = routing_parts
        details = {
            "legalType": "PRIVATE" if partner.company_type == "person" else "BUSINESS",
            "institutionNumber": institution_number,
            "transitNumber": transit_number,
            "accountNumber": self._amh_digits(bank_account.acc_number),
            "accountType": bank_account.amh_wise_cad_account_type or "CHECKING",
            "address": {
                "firstLine": partner.street,
                "city": partner.city,
                "country": partner.country_id.code,
                "postCode": partner.zip,
            },
        }
        # Deliberately NOT setting details["email"]. With no email on the Wise
        # recipient, Wise sends the vendor no payment notification of its own.
        # The remittance is emailed from Odoo instead (action_amh_send_remittance)
        # so the vendor receives one complete advice listing every invoice paid,
        # rather than Wise's single, length-capped reference that only fits one
        # invoice number.

        return {
            "profile": self.company_id.sudo().wise_profile_identifier,
            "accountHolderName": bank_account.acc_holder_name or partner.name,
            "currency": "CAD",
            "type": "canadian",
            "details": details,
        }
