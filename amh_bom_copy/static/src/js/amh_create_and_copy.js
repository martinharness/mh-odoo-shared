/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { makeContext } from "@web/core/context";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";

// Which many2one targets get the extra entry.  Kept as a set so a second
// model is a one-line change.  amh_product_exact_search patches
// addCreateSuggestion on this same prototype against a similar set; the two
// patches touch different methods and compose in either load order.
const AMH_CREATE_AND_COPY_MODELS = new Set(["product.template"]);

patch(Many2XAutocomplete.prototype, {
    /**
     * Odoo 19 builds the dropdown's action rows from this getter, as a list
     * of {enabled, build} descriptors.  Insert ours second-to-last so it sits
     * directly under "Create and edit..." and above "Search more...", without
     * assuming how many rows core ships.
     */
    get actionSuggestions() {
        const suggestions = super.actionSuggestions;
        if (!AMH_CREATE_AND_COPY_MODELS.has(this.props.resModel)) {
            return suggestions;
        }
        suggestions.splice(Math.max(suggestions.length - 1, 0), 0, {
            enabled: this.amhAddCreateCopySuggestion.bind(this),
            build: this.amhBuildCreateCopySuggestion.bind(this),
        });
        return suggestions;
    },

    // Same gate core uses for "Create and edit...".
    amhAddCreateCopySuggestion({ records, request }) {
        return (
            (this.activeActions.createEdit ?? this.activeActions.create) &&
            (request.length > 0 || records?.length === 0)
        );
    },

    amhBuildCreateCopySuggestion(request) {
        return {
            cssClass: "o_m2o_dropdown_option amh_m2o_dropdown_option_create_copy",
            label: _t("Create and copy from..."),
            onSelect: () => this.amhCreateAndCopy(request),
        };
    },

    /**
     * Pick a source item, ask the server what a copy of it looks like, then
     * open the ordinary create dialog pre-filled with that.  Nothing is
     * written until the operator saves, so discarding still leaves no trace --
     * which is the whole reason "Create and edit..." is safe to reach for
     * after a mistyped reference.
     */
    amhCreateAndCopy(request) {
        const { dialog, orm } = this.env.services;
        dialog.add(SelectCreateDialog, {
            title: _t("Copy which item?"),
            resModel: this.props.resModel,
            domain: this.props.getDomain(),
            // search_view_ref pins the picker to a search view whose first
            // field is the Internal Reference, so typing a SKU and pressing
            // Enter searches references rather than product names.  Without
            // it the server picks product.template's default search view,
            // which is whichever primary search view sorts first by
            // (priority, name, id) -- not something to leave to chance.
            context: {
                ...this.props.context,
                search_view_ref: "amh_bom_copy.view_product_template_search_amh_copy",
            },
            multiSelect: false,
            noCreate: true,
            onSelected: async (resIds) => {
                const defaults = await orm.call(
                    this.props.resModel,
                    "amh_prepare_copy_defaults",
                    [[resIds[0]], request],
                    { context: this.props.context }
                );
                // Later entries win in makeContext, so the server's defaults
                // override the default_<name> the plain create flow would set.
                await this.openMany2X({
                    context: makeContext([
                        this.getCreationContext(request),
                        defaults,
                    ]),
                    nextRecordsContext: this.props.context,
                });
            },
        });
    },
});
