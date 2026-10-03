"""Fund a Wise batch from the Wise balance later, at a time somebody picked.

Wise does not schedule anything: funding a batch group sends every transfer in
it at once, which is why validating a batch whose payments are not dated today
raises "Scheduled payments are not yet supported". And on the Canadian side
there is no instant way to move more than 25,000 into Wise - that is an EFT,
and an EFT takes up to a business day to land.

A large batch therefore had two bad options: hold the whole thing until the
money arrives, or initiate it and remember to come back and fund it. This adds
a third. The batch is initiated now - Wise creates and completes the batch
group, the recipients and the transfers all exist, nothing has moved - and the
funding is booked for a datetime. A cron funds it then, from the Wise balance,
by exactly the same call the Fund from Wise Balance button makes. The EFT
travels in the meantime.

ONE ATTEMPT, deliberately. The schedule is cleared and COMMITTED before the
funding call, so a crash, a rollback or a killed worker leaves no schedule
behind: the worst case is a batch that was not funded, never one funded twice.
If the balance is short because the EFT has not landed, Wise's refusal is
posted on the batch as a message - which notifies its followers - and the
schedule is gone. Funding then waits for a person pressing Fund from Wise
Balance, which is the right way round for the one thing in this module that
moves money with nobody watching.

That button is also the repair for an attempt that failed ambiguously, say a
timeout after Wise had already accepted the funding: it asks Wise for the batch
group first, and records "Already Paid in Wise" instead of sending a second
funding request.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime, formatLang

from .account_batch_payment_funding import WISE_BALANCE_CURRENCY_BY_METHOD

_logger = logging.getLogger(__name__)


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    amh_wise_fund_scheduled_at = fields.Datetime(
        string="Fund From Wise Balance At",
        readonly=True,
        copy=False,
        tracking=True,
        help="When the scheduled funding runs. It is cleared the moment it "
             "runs, whether or not the funding succeeded: the cron makes one "
             "attempt and never repeats it.",
    )
    amh_wise_fund_schedule_requested = fields.Boolean(
        string="Schedule Funding On Validation",
        default=False,
        copy=False,
        readonly=True,
        help="Set by the Schedule Funding confirmation. It is stored rather "
             "than passed in the context because Odoo answers a validation "
             "warning with a second dialog, and a context does not survive "
             "that.",
    )

    # ------------------------------------------------------------------
    # Booking, cancelling
    # ------------------------------------------------------------------
    def action_open_wise_balance_schedule_wizard(self):
        self.ensure_one()
        self._amh_check_wise_balance_funding_ready(allow_uninitiated=True)
        if self.amh_wise_fund_scheduled_at:
            raise UserError(
                _(
                    "This batch is already scheduled to be funded on "
                    "%(moment)s. Cancel that first if you want another time.",
                    moment=format_datetime(
                        self.env, self.amh_wise_fund_scheduled_at
                    ),
                )
            )
        return {
            "name": _(
                "Schedule Wise %(currency)s Balance Funding",
                currency=self._amh_wise_balance_currency(),
            ),
            "type": "ir.actions.act_window",
            "res_model": "amh.wise.balance.schedule.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_batch_id": self.id},
        }

    def action_amh_cancel_scheduled_funding(self):
        for batch in self:
            if not batch.amh_wise_fund_scheduled_at:
                continue
            moment = format_datetime(
                batch.env, batch.amh_wise_fund_scheduled_at
            )
            batch.write({
                "amh_wise_fund_scheduled_at": False,
                "amh_wise_fund_schedule_requested": False,
            })
            batch.message_post(
                body=_(
                    "Scheduled Wise balance funding for %(moment)s cancelled. "
                    "Nothing was sent to Wise. The batch stays initiated and "
                    "can still be funded with Fund from Wise Balance.",
                    moment=moment,
                )
            )
        return True

    def _amh_schedule_wise_balance_funding(self, scheduled_at):
        """Book the funding, initiating the batch first if it has not been."""
        self.ensure_one()
        if not scheduled_at:
            raise UserError(
                _("Pick the date and time the batch should be funded.")
            )
        if scheduled_at <= fields.Datetime.now():
            raise UserError(_("The funding time has to be in the future."))
        self._amh_check_wise_balance_funding_ready(allow_uninitiated=True)

        if self.wise_payment_status == "completed":
            self.amh_wise_fund_scheduled_at = scheduled_at
            self._amh_post_schedule_note()
            return self._amh_open_batch_action()

        # Not initiated yet. Validating creates and completes the batch group on
        # Wise. The request is WRITTEN on the batch, not put in the context, for
        # the same reason the fund-now request is - see
        # account_batch_payment_funding.py.
        self.write({
            "amh_wise_fund_scheduled_at": scheduled_at,
            "amh_wise_fund_schedule_requested": True,
        })
        return self.validate_batch_button()

    # ------------------------------------------------------------------
    # Initiation
    # ------------------------------------------------------------------
    def _send_after_validation(self):
        scheduling = bool(
            self.amh_wise_fund_schedule_requested
            and self.amh_wise_fund_scheduled_at
        )
        result = super()._send_after_validation()
        if not scheduling:
            return result

        self.amh_wise_fund_schedule_requested = False
        if self.payment_method_code not in WISE_BALANCE_CURRENCY_BY_METHOD:
            # Scheduled, but this is not a batch this module funds. Do not
            # leave a booking on the record that nothing would ever act on.
            self.amh_wise_fund_scheduled_at = False
            return result

        self._amh_post_schedule_note()
        if self._can_commit():
            self.env.cr.commit()
        # Deliberately NOT the act_url the plain Wise flow returns: being handed
        # off to the Wise website is the whole thing scheduling exists to avoid.
        return self._amh_open_batch_action()

    def _amh_fund_from_wise_balance(self):
        result = super()._amh_fund_from_wise_balance()
        if self.amh_wise_fund_scheduled_at or self.amh_wise_fund_schedule_requested:
            # Funded by hand before the booking came round, or by the cron,
            # which has already cleared it. Either way there is nothing left to
            # run.
            self.write({
                "amh_wise_fund_scheduled_at": False,
                "amh_wise_fund_schedule_requested": False,
            })
        return result

    def _amh_post_schedule_note(self):
        self.ensure_one()
        self.message_post(
            body=_(
                "Wise batch initiated. Funding from the Wise %(currency)s "
                "balance is scheduled for %(moment)s - nothing has left the "
                "account yet, so make sure the balance covers %(amount)s by "
                "then. One attempt is made: if the balance is short it is "
                "reported here and the schedule is dropped.",
                currency=self._amh_wise_balance_currency(),
                moment=format_datetime(self.env, self.amh_wise_fund_scheduled_at),
                amount=formatLang(
                    self.env, self.amount, currency_obj=self.currency_id
                ),
            )
        )

    # ------------------------------------------------------------------
    # The cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_amh_fund_scheduled_wise_batches(self, limit=50):
        """Fund every batch whose booked time has come. One attempt each."""
        due = self.search(
            [
                ("amh_wise_fund_scheduled_at", "!=", False),
                ("amh_wise_fund_scheduled_at", "<=", fields.Datetime.now()),
                ("amh_wise_balance_funding_status", "in", (False, "not_funded")),
            ],
            order="amh_wise_fund_scheduled_at",
            limit=limit,
        )
        for batch in due:
            batch._amh_run_scheduled_funding()
        return True

    def _amh_run_scheduled_funding(self):
        self.ensure_one()
        moment = format_datetime(self.env, self.amh_wise_fund_scheduled_at)
        amount = formatLang(self.env, self.amount, currency_obj=self.currency_id)
        currency = self._amh_wise_balance_currency()

        # Cleared and committed BEFORE the attempt, on purpose. Whatever happens
        # to this transaction afterwards, no booking is left to fire a second
        # time.
        self.write({
            "amh_wise_fund_scheduled_at": False,
            "amh_wise_fund_schedule_requested": False,
        })
        if self._can_commit():
            self.env.cr.commit()

        try:
            self._amh_fund_from_wise_balance()
        except Exception as error:  # noqa: BLE001 - a cron must not die here
            self.env.cr.rollback()
            _logger.warning(
                "Wise: scheduled funding of batch %s did not go through: %s",
                self.name, error,
            )
            self.message_post(
                body=_(
                    "Scheduled Wise balance funding for %(moment)s did not go "
                    "through, and the schedule has been dropped - it is not "
                    "retried. Wise said:\n\n%(error)s\n\nUsually that means "
                    "the %(currency)s balance does not cover %(amount)s yet. "
                    "Once the money is in Wise, press Fund from Wise Balance. "
                    "That is safe even if this attempt got further than it "
                    "looks: it asks Wise about the batch first and records "
                    "Already Paid in Wise rather than sending a second funding "
                    "request.",
                    moment=moment,
                    error=str(error),
                    currency=currency,
                    amount=amount,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        if self._can_commit():
            self.env.cr.commit()
        return True
