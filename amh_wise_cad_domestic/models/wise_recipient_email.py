"""Keep the vendor's email address off every Wise recipient.

The CAD flow already omits it deliberately: with no email on the Wise
recipient, Wise sends the vendor no notification of its own, and Odoo emails
one complete remittance advice instead (``action_amh_send_remittance``) listing
every invoice the batch paid. Wise's own notification carries only a short
reference that fits a single invoice number, so two notifications would be both
redundant and less useful than the one Odoo sends.

``l10n_us_direct_deposit`` builds its own recipient payload and does include the
partner's email, so a US vendor was getting a Wise notification as well. That
payload is assembled inside the Enterprise module with no hook in between, so
the address is dropped here at the last point every module shares: the API call
itself. Patching ``create_recipient`` rather than any one payload builder means
this holds for the US flow, the CAD flow, and anything added later.

The key is removed wherever it appears in the payload rather than at one known
path, so this does not depend on how Odoo happens to nest it today.
"""

import logging

from odoo.addons.l10n_us_direct_deposit.models import wise_request

_logger = logging.getLogger(__name__)


def _strip_recipient_email(value):
    """Return the payload with every ``email`` key removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: _strip_recipient_email(item)
            for key, item in value.items()
            if key != "email"
        }
    if isinstance(value, list):
        return [_strip_recipient_email(item) for item in value]
    return value


def _create_recipient_without_email(self, recipient_data, *args, **kwargs):
    return _create_recipient_without_email._amh_original(
        self, _strip_recipient_email(recipient_data), *args, **kwargs
    )


_original = getattr(wise_request.Wise, "create_recipient", None)

if _original is None:
    # Odoo renamed or removed the method. Warn rather than raising: this patch
    # suppresses a duplicate vendor notification, which is not worth refusing to
    # start the whole database over. The log line is the signal to come fix it.
    _logger.warning(
        "amh_wise_cad_domestic: Wise.create_recipient not found, so vendor email "
        "addresses are no longer being stripped from Wise recipients. Wise will "
        "email vendors directly in addition to Odoo's remittance advice."
    )
elif not getattr(_original, "_amh_strips_email", False):
    # Guard against wrapping twice if this module is imported more than once.
    _create_recipient_without_email._amh_original = _original
    _create_recipient_without_email._amh_strips_email = True
    wise_request.Wise.create_recipient = _create_recipient_without_email
