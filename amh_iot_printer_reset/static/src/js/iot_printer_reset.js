/** @odoo-module **/

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

// Odoo IoT remembers, per browser, which printer each report goes to (and
// whether to skip the printer dialog) under this localStorage key, as
// { "<report id>": { selectedDevices: [...], skipDialog: bool }, ... }.
const LINKED_REPORTS_KEY = "odoo-iot-linked_reports";

function resetPrinterChoices(env, reportId) {
    let message;
    try {
        if (reportId) {
            const linked = JSON.parse(browser.localStorage.getItem(LINKED_REPORTS_KEY) || "{}");
            if (linked[reportId]) {
                delete linked[reportId];
                browser.localStorage.setItem(LINKED_REPORTS_KEY, JSON.stringify(linked));
                message = _t("Printer choice for this report cleared on this computer. The next print will ask which printer to use.");
            } else {
                message = _t("This computer has no saved printer for this report.");
            }
        } else {
            browser.localStorage.removeItem(LINKED_REPORTS_KEY);
            message = _t("All saved printer choices cleared on this computer. Each report will ask which printer to use next time.");
        }
    } catch (error) {
        console.error("Could not reset IoT printer choices", error);
        env.services.notification.add(_t("Could not reset the saved printer choices on this computer."), { type: "danger" });
        return;
    }
    env.services.notification.add(message, { type: "success" });
}

// Report form button (ir.actions.report.action_amh_iot_reset_printer_choice).
registry.category("actions").add("amh_iot_printer_reset", (env, action) => {
    resetPrinterChoices(env, action.params && action.params.report_id);
});

// User menu (top right): clear every saved choice on this computer.
registry.category("user_menuitems").add("amh_iot_printer_reset_all", (env) => ({
    type: "item",
    id: "amh_iot_printer_reset_all",
    description: _t("Reset Printer Choices"),
    callback: () => resetPrinterChoices(env),
    sequence: 55,
}));
