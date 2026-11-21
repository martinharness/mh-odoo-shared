"""Reset my printer: forget the saved Odoo IoT printer for a report.

Odoo IoT remembers, in each browser, which printer every report goes to and
whether to skip the printer dialog ("don't ask again"). That lives in the
browser's localStorage under "odoo-iot-linked_reports", not in the database,
so nothing on the server can clear it. The button returns a client action
that clears this report's entry on the computer it is clicked on
(static/src/js/iot_printer_reset.js).
"""

from odoo import models


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def action_amh_iot_reset_printer_choice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "amh_iot_printer_reset",
            "params": {"report_id": self.id},
        }
