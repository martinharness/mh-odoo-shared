/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { onMounted } from "@odoo/owl";

// The Customer Receipts screen is built for fast, mouse-free entry: when a
// blank receipt opens - on load, and again after every Receive Payment - the
// cursor should land straight in the Customer field so a pile of cheques can be
// keyed without reaching for the mouse. The view's `default_focus` is not
// honoured for this transient-model form opened by an action, so we focus the
// Customer field ourselves on mount. Scoped to this one model, so no other form
// is affected.
patch(FormController.prototype, {
    setup() {
        super.setup();
        onMounted(() => {
            if (this.props.resModel !== "amh.customer.receipt") {
                return;
            }
            const focusCustomer = () => {
                const input = document.querySelector(
                    ".o_form_view .o_field_widget[name='partner_id'] input"
                );
                if (input) {
                    input.focus();
                }
            };
            focusCustomer();
            // A tick later too, so it wins if a late render steals focus.
            setTimeout(focusCustomer, 0);
        });
    },
});
