from . import account_batch_payment
from . import account_batch_payment_address
from . import account_batch_payment_funding
from . import account_batch_payment_schedule
from . import account_batch_payment_us_key
from . import account_journal
from . import account_payment
from . import account_payment_method
from . import account_payment_register
from . import res_partner_bank
from . import wise_recipient_address
from . import wise_recipient_email
from . import wise_transfer_reference

# Out of alphabetical order on purpose. Odoo registers the most recent override
# first, so this has to be imported last to sit outside the others and explain
# the errors they raise.
from . import wise_stale_recipient
