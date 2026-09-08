from odoo import models

# Odoo Enterprise's Wise-backed USD ACH method (l10n_us_direct_deposit).
WISE_US_PAYMENT_METHOD_CODE = "wise_direct_deposit"


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    def _generate_wise_key(self, *args, **kwargs):
        """Survive Canadian recipients when keying the US recipient lookup.

        ``l10n_us_direct_deposit`` builds its recipient lookup over every
        recipient on the Wise profile and reads ``details['abartn']`` straight
        out of each one::

            recipient['details']['abartn'], recipient['details']['accountNumber']

        A Canadian recipient - the kind this module creates, type ``canadian``
        with ``institutionNumber``/``transitNumber`` - has no ``abartn``, so the
        moment one exists on the profile that comprehension raises KeyError and
        no US batch can be initiated.

        Non-ABA recipients are given a unique key here instead. Unique rather
        than ``None`` so that several of them cannot collapse onto one another,
        and so that nothing derived from a US payment can ever match one.
        Everything else is left to super().
        """
        recipient = kwargs.get("recipient")
        if recipient is None:
            for candidate in args:
                if isinstance(candidate, dict) and "details" in candidate:
                    recipient = candidate
                    break

        if isinstance(recipient, dict):
            details = recipient.get("details") or {}
            if "abartn" not in details:
                return "amh-non-aba:%s" % (
                    recipient.get("id")
                    or details.get("accountNumber")
                    or id(recipient)
                )

        return super()._generate_wise_key(*args, **kwargs)

    def action_amh_send_remittance(self):
        """Send the remittance advice for U.S. Direct Deposit batches too.

        The CAD flow withholds the vendor's email from Wise on purpose, so Wise
        notifies nobody and this advice is the vendor's only notice of payment.
        The same reasoning applies to USD, where the CAD-only method refused
        outright and vendors were left with nothing at all.

        The US case is handled here rather than by relaxing the CAD method's
        guard, so the working CAD path is untouched. The per-vendor loop is the
        only thing repeated; the email address and the HTML body both come from
        the shared helpers, so the two advices stay in step.
        """
        self.ensure_one()
        if self.payment_method_code != WISE_US_PAYMENT_METHOD_CODE:
            return super().action_amh_send_remittance()

        from odoo.tools import formatLang

        Mail = self.env["mail.mail"].sudo()
        company = self.company_id or self.env.company
        from_email = company.email or self.env.user.email_formatted

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
            # The shared body names the CAD rail; this batch went out on the US one.
            body = body.replace(
                "Wise (CAD Domestic EFT)", "Wise (U.S. Direct Deposit)"
            )
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
                self.env._(
                    "No remittance email on file, skipped: %s.", ", ".join(skipped)
                )
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
