from odoo import _, fields, models
from odoo.exceptions import UserError

from .account_batch_payment_us_key import WISE_US_PAYMENT_METHOD_CODE
from .account_payment_method import WISE_CAD_PAYMENT_METHOD_CODE
from .wise_request import WiseCad

# Which Wise balance each payment method draws on. Funding a batch group from
# the balance costs a fraction of letting Wise collect the money by wire, so
# every Wise method this database uses should be able to reach the button.
WISE_BALANCE_CURRENCY_BY_METHOD = {
    WISE_CAD_PAYMENT_METHOD_CODE: "CAD",
    WISE_US_PAYMENT_METHOD_CODE: "USD",
}


class AccountBatchPayment(models.Model):
    _inherit = "account.batch.payment"

    amh_wise_balance_funding_status = fields.Selection(
        selection=[
            ("not_funded", "Not Funded"),
            ("balance_funded", "Funded from Wise Balance"),
            ("already_paid", "Already Paid in Wise"),
        ],
        string="Wise Balance Funding",
        default="not_funded",
        readonly=True,
        copy=False,
        tracking=True,
    )
    amh_wise_balance_funded_at = fields.Datetime(
        string="Wise Balance Funded At",
        readonly=True,
        copy=False,
        tracking=True,
    )
    amh_fund_from_balance_requested = fields.Boolean(
        string="Fund From Wise Balance On Validation",
        default=False,
        copy=False,
        readonly=True,
        help="Set by the Fund from Wise Balance confirmation. It is stored "
             "rather than passed in the context because Odoo answers a "
             "validation warning with a second dialog, and a context does not "
             "survive that.",
    )

    def _amh_wise_balance_currency(self):
        """Currency of the Wise balance this batch would be funded from."""
        self.ensure_one()
        return WISE_BALANCE_CURRENCY_BY_METHOD.get(self.payment_method_code)

    def _send_after_validation(self):
        # Read the request BEFORE validating, and read it from the record as
        # well as the context.
        #
        # The funding wizard calls validate_batch_button() with
        # amh_fund_from_wise_balance in the context. But when the batch raises
        # any validation WARNING - and a Wise batch whose payments are not
        # dated today always raises one, "Wise will send the entire batch
        # payment as soon as you fund it" - core does not validate. It answers
        # with account.batch.error.wizard, and that wizard's "Proceed with
        # validation" button calls validate_batch() fresh, without the context.
        #
        # That is how a live batch came out on 3 Oct 2026: state sent,
        # wise_payment_status completed, amh_wise_balance_funding_status still
        # not_funded, nothing in the chatter, and Odoo handing the user off to
        # the Wise website to fund thirteen transfers he had just asked Odoo to
        # fund. A stored request survives any number of dialogs in between.
        requested = bool(
            self.env.context.get("amh_fund_from_wise_balance")
            or self.amh_fund_from_balance_requested
        )
        result = super()._send_after_validation()
        if requested and self.payment_method_code in WISE_BALANCE_CURRENCY_BY_METHOD:
            # Cleared BEFORE funding, never after: if funding fails, the batch
            # is already initiated and a flag left behind would silently fund
            # the next validation too.
            if self.amh_fund_from_balance_requested:
                self.amh_fund_from_balance_requested = False
            # Persist the completed Wise batch before funding. If the balance is
            # insufficient, Odoo still remains aligned with the completed batch.
            if self._can_commit():
                self.env.cr.commit()
            self._amh_fund_from_wise_balance()
            return self._amh_open_batch_action()
        if self.amh_fund_from_balance_requested:
            # Requested, but this batch is not one this module funds. Do not
            # leave the flag lying on the record.
            self.amh_fund_from_balance_requested = False
        return result

    def action_open_wise_balance_funding_wizard(self):
        self.ensure_one()
        self._amh_check_wise_balance_funding_ready(allow_uninitiated=True)
        return {
            "name": _(
                "Fund from Wise %(currency)s Balance",
                currency=self._amh_wise_balance_currency(),
            ),
            "type": "ir.actions.act_window",
            "res_model": "amh.wise.cad.balance.funding.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_batch_id": self.id},
        }

    def action_fund_from_wise_balance(self):
        self.ensure_one()
        self._amh_check_wise_balance_funding_ready()
        self._amh_fund_from_wise_balance()
        return self._amh_open_batch_action()

    def _amh_open_batch_action(self):
        self.ensure_one()
        return {
            "name": _("Wise Batch"),
            "type": "ir.actions.act_window",
            "res_model": "account.batch.payment",
            "res_id": self.id,
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "account_batch_payment.view_batch_payment_form"
                    ).id,
                    "form",
                )
            ],
            "target": "current",
        }

    def _amh_check_wise_balance_funding_ready(self, allow_uninitiated=False):
        self.ensure_one()
        balance_currency = self._amh_wise_balance_currency()
        if not balance_currency:
            raise UserError(
                _("Only Wise batches can be funded from this button.")
            )
        # Check the payments rather than the batch currency: the batch takes its
        # currency from the journal, which may be left on the company currency
        # even when the payments themselves are foreign.
        payment_currencies = set(self.payment_ids.mapped("currency_id.name"))
        if payment_currencies and payment_currencies != {balance_currency}:
            raise UserError(
                _(
                    "Only %(expected)s payments can be funded from the Wise "
                    "%(expected)s balance. This batch contains %(found)s.",
                    expected=balance_currency,
                    found=", ".join(sorted(payment_currencies)),
                )
            )
        if not self.company_id.wise_connected:
            raise UserError(_("Connect the company to Wise before funding this batch."))
        if self.amh_wise_balance_funding_status not in (False, "not_funded"):
            raise UserError(
                _("This batch has already been recorded as paid or funded in Wise.")
            )
        if allow_uninitiated and self.wise_payment_status in ("uninitiated", "new"):
            if self.state != "draft" or not self.payment_ids:
                raise UserError(
                    _("The batch must contain draft outgoing payments before initiation.")
                )
            # ONLY the future, and the variable is named for what it holds.
            #
            # Wise has no scheduling: funding sends the whole batch the moment
            # it is funded. So a payment Odoo dates AHEAD of today would leave
            # today while the books claim a later date, and that is worth
            # refusing. A payment dated in the PAST is simply being paid late,
            # which is what every batch prepared the evening before looks like,
            # and needs no permission at all.
            #
            # This test read "!= today" from the day it was written, while
            # calling its result future_payments - so it refused yesterday too.
            # Because it raises UserError the dialog offers nothing but Close,
            # and the only remedy it named was changing every payment date,
            # which on posted payments means resetting them all to draft. A
            # live batch of thirteen payments prepared the evening before was
            # unfundable the next morning for that reason alone.
            future_dated = self.payment_ids.filtered(
                lambda payment: payment.date > fields.Date.context_today(self)
            )
            if future_dated:
                raise UserError(
                    _(
                        "Wise balance funding sends the whole batch "
                        "immediately, so a payment dated in the future cannot "
                        "be funded this way. Set these to today or earlier "
                        "first: %(payments)s",
                        payments=", ".join(
                            "%s (%s)" % (payment.display_name, payment.date)
                            for payment in future_dated
                        ),
                    )
                )
            return
        if not self.wise_batch_identifier:
            raise UserError(
                _("Initiate the Wise batch before funding it from the Wise balance.")
            )
        if self.wise_payment_status != "completed":
            raise UserError(
                _(
                    "The Wise batch must be completed before it can be funded. "
                    "Use Initiate Payment first."
                )
            )

    def _amh_fund_from_wise_balance(self):
        self.ensure_one()
        self._amh_check_wise_balance_funding_ready()
        balance_currency = self._amh_wise_balance_currency()

        wise_api = WiseCad(self.company_id)
        batch_information = wise_api.get_batch_group(self.wise_batch_identifier)
        if wise_api.has_errors(batch_information):
            raise UserError(
                self.env._(
                    "Failed to retrieve the Wise batch before funding:\n%(wise_error)s",
                    wise_error=wise_api.format_errors(batch_information),
                )
            )

        source_currency = batch_information.get("sourceCurrency")
        if source_currency and source_currency != balance_currency:
            raise UserError(
                _(
                    "Wise reports that this batch uses %(currency)s, not "
                    "%(expected)s.",
                    currency=source_currency,
                    expected=balance_currency,
                )
            )
        if batch_information.get("status") != "COMPLETED":
            raise UserError(
                _(
                    "Wise reports that this batch is %(status)s. It must be COMPLETED "
                    "before balance funding.",
                    status=batch_information.get("status") or _("in an unknown state"),
                )
            )

        if batch_information.get("alreadyPaid"):
            self.write({
                "amh_wise_balance_funding_status": "already_paid",
                "amh_wise_balance_funded_at": fields.Datetime.now(),
            })
            self.message_post(
                body=_(
                    "Wise reported that this batch had already been paid. "
                    "No additional funding request was sent."
                )
            )
            return True

        funding_result = wise_api.fund_batch_group_from_balance(
            self.wise_batch_identifier
        )
        if wise_api.has_errors(funding_result):
            raise UserError(
                self.env._(
                    "Failed to fund the Wise batch from the %(currency)s balance:"
                    "\n%(wise_error)s",
                    currency=balance_currency,
                    wise_error=wise_api.format_errors(funding_result),
                )
            )

        self.write({
            "amh_wise_balance_funding_status": "balance_funded",
            "amh_wise_balance_funded_at": fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "Wise batch funded from the Wise %(currency)s balance. Wise begins "
                "processing the transfers immediately.",
                currency=balance_currency,
            )
        )
        return True
