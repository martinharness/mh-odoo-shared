import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

# Batches funded from the Wise balance are paid the moment Wise accepts the
# funding, so that is when the vendor is told. Forgetting the manual button is
# how a fourteen-payment batch, and the USD one after it, went out without a
# single remittance until somebody noticed a week later.
REMITTANCE_FUNDED_STATES = ("balance_funded", "already_paid")


class AccountPayment(models.Model):
    _inherit = "account.payment"

    amh_remittance_sent = fields.Boolean(
        string="Remittance Advice Sent",
        readonly=True,
        copy=False,
        help="Set when the Wise remittance advice for this payment has been "
             "emailed to the vendor. A payment is never sent a second advice, "
             "whether by the automatic send after funding or by the button.",
    )
    amh_remittance_sent_at = fields.Datetime(
        string="Remittance Advice Sent At",
        readonly=True,
        copy=False,
    )


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    # ------------------------------------------------------------------
    # Only vendors not yet sent an advice
    # ------------------------------------------------------------------
    def _amh_wise_cad_remittance_email(self, partner):
        """Hide the vendors already sent an advice from the shared send loops.

        The CAD and USD loops both skip a vendor whose remittance email is
        empty. While a send is running, the vendors left out of
        ``amh_remittance_partner_ids`` are given no email, so they are passed
        over without the loops themselves being copied here. The notification
        and the chatter are written below from our own record of who was sent,
        never from the loops' "skipped" wording, so those vendors are not
        reported as missing an email.
        """
        allowed = self.env.context.get("amh_remittance_partner_ids")
        if allowed is not None and partner.id not in allowed:
            return False
        return super()._amh_wise_cad_remittance_email(partner)

    def _amh_send_pending_remittance(self):
        """Email the advice to every vendor in the batch not yet sent one.

        Returns (sent, skipped, already): display strings for vendors emailed,
        vendors with no email on file, and vendors sent an advice before.
        """
        self.ensure_one()
        payments = self.payment_ids.filtered("partner_id")
        pending = payments.filtered(lambda payment: not payment.amh_remittance_sent)
        already = (payments - pending).partner_id

        with_email = self.env["res.partner"]
        without_email = self.env["res.partner"]
        for partner in pending.partner_id:
            if super(AccountBatchPayment, self)._amh_wise_cad_remittance_email(partner):
                with_email |= partner
            else:
                without_email |= partner

        if with_email:
            # The existing CAD / USD method does the sending; the context limits
            # it to the vendors still owed an advice.
            self.with_context(
                amh_remittance_partner_ids=with_email.ids
            ).action_amh_send_remittance_core()
            now = fields.Datetime.now()
            pending.filtered(lambda payment: payment.partner_id in with_email).write({
                "amh_remittance_sent": True,
                "amh_remittance_sent_at": now,
            })

        sent = [
            "%s (%s)" % (
                partner.display_name,
                super(AccountBatchPayment, self)._amh_wise_cad_remittance_email(partner),
            )
            for partner in with_email
        ]
        return sent, without_email.mapped("display_name"), already.mapped("display_name")

    def action_amh_send_remittance_core(self):
        """The original per-rail send (CAD here, USD in the us_key override)."""
        return super().action_amh_send_remittance()

    def action_amh_send_remittance(self):
        """Send Remittance Advice button: only vendors not sent one yet."""
        self.ensure_one()
        sent, skipped, already = self._amh_send_pending_remittance()
        if sent:
            # Only when something went out: pressing the button again on a
            # batch already done should not repeat the no-email list each time.
            self._amh_post_remittance_summary(sent, skipped, automatic=False)

        parts = []
        if sent:
            parts.append(_("Sent to: %s.", ", ".join(sent)))
        if skipped:
            parts.append(_("No email on file, not sent: %s.", ", ".join(skipped)))
        if already and not sent:
            parts.append(_(
                "Already sent earlier, not sent again: %s.", ", ".join(already)
            ))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning" if skipped else "success",
                "title": _("Remittance advice"),
                "message": " ".join(parts) or _("No vendor payments to send."),
                "sticky": bool(skipped),
            },
        }

    # ------------------------------------------------------------------
    # Automatic send once the batch is funded
    # ------------------------------------------------------------------
    def _amh_fund_from_wise_balance(self):
        result = super()._amh_fund_from_wise_balance()
        if self.amh_wise_balance_funding_status in REMITTANCE_FUNDED_STATES:
            self._amh_auto_send_remittance()
        return result

    def _amh_auto_send_remittance(self):
        """Send the advice after funding. Never undoes or blocks the funding.

        The funding is already committed with Wise by the time this runs, so a
        failure to email is caught, logged and posted on the batch for the
        button to retry, rather than raised.
        """
        self.ensure_one()
        if self._can_commit():
            # Keep the funded state even if anything below goes wrong.
            self.env.cr.commit()
        try:
            with self.env.cr.savepoint():
                sent, skipped, _already = self._amh_send_pending_remittance()
        except Exception as error:  # noqa: BLE001 - must not undo the funding
            _logger.exception("Automatic remittance advice failed for %s", self.name)
            self.message_post(body=_(
                "The remittance advice could not be emailed automatically: "
                "%(error)s. Use Send Remittance Advice to send it.",
                error=error,
            ))
            return False
        if sent or skipped:
            self._amh_post_remittance_summary(sent, skipped, automatic=True)
        return True

    def _amh_post_remittance_summary(self, sent, skipped, automatic):
        lines = []
        if sent:
            lines.append(_("Emailed to: %s.", ", ".join(sent)))
        if skipped:
            lines.append(_("No email on file, not sent: %s.", ", ".join(skipped)))
        heading = (
            _("Remittance advice sent automatically after funding.")
            if automatic else _("Remittance advice sent.")
        )
        self.message_post(body="%s %s" % (heading, " ".join(lines)))
