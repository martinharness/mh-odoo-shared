{
    "name": "IoT Printer Reset",
    "version": "19.0.1.0.0",
    "summary": "Undo 'don't ask again' on Odoo IoT printing: reset the saved printer per report or for the whole computer",
    "description": """
Odoo IoT remembers, in each browser, which printer every report goes to, and
whether the user ticked "don't ask again" in the printer dialog. Odoo gives no
way to undo that short of clearing the browser's site data. This module adds:

* a "Reset Printer Choices" item in the user menu (top right) that clears every
  saved printer choice on this computer;
* a "Reset my printer" button on each PDF report's settings form that clears
  the saved choice for just that report, on this computer.

See README.md.
""",
    "author": "Aaron Martin Harness Ltd",
    "category": "Technical",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": [
        "views/ir_actions_report_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "amh_iot_printer_reset/static/src/js/iot_printer_reset.js",
        ],
    },
    "installable": True,
    "application": False,
}
