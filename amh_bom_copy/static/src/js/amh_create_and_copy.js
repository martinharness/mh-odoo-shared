/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { makeContext } from "@web/core/context";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

// A product.template many2one gets the entry by its target model. A
// product.product many2one - a purchase line, a BoM component, a manufacturing
// order's product - opts in through its own context key, amh_create_copy, set
// on the field in the view (see views/amh_copy_variant_fields.xml), so the
// entry appears exactly there and not on every stock, inventory or invoice
// product picker.
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
        if (
            !AMH_CREATE_AND_COPY_MODELS.has(this.props.resModel) &&
            !(this.props.context && this.props.context.amh_create_copy)
        ) {
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
     * open the ordinary create form pre-filled with that.  Nothing is
     * written until the operator saves, so discarding still leaves no trace --
     * which is the whole reason "Create and edit..." is safe to reach for
     * after a mistyped reference.
     *
     * A product.product field copies at the template level instead (the
     * picker, the defaults and the bill-of-materials copy all live on
     * product.template), then drops the finished item's variant into the field
     * -- see amhCreateAndCopyVariant.
     */
    amhCreateAndCopy(request) {
        if (this.props.resModel === "product.product") {
            return this.amhCreateAndCopyVariant(request);
        }
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

    /**
     * The product.product path (a purchase line, a BoM component, an MO's
     * product).  The source picker and the create form run on
     * product.template -- that is where amh_prepare_copy_defaults and the
     * bill-of-materials copy live -- and the new template's variant is then set
     * on this field.  Nothing is written until the operator saves the item, and
     * the bill of materials comes across in that same save (product.template's
     * create override reads amh_copy_source_id from the pre-filled defaults).
     *
     * A multi-variant copy lands the field on the template's primary variant.
     */
    amhCreateAndCopyVariant(request) {
        const { dialog, orm } = this.env.services;
        const fieldUpdate = this.props.update;
        dialog.add(SelectCreateDialog, {
            title: _t("Copy which item?"),
            resModel: "product.template",
            domain: [],
            context: {
                search_view_ref: "amh_bom_copy.view_product_template_search_amh_copy",
            },
            multiSelect: false,
            noCreate: true,
            onSelected: async (resIds) => {
                const defaults = await orm.call(
                    "product.template",
                    "amh_prepare_copy_defaults",
                    [[resIds[0]], request],
                    {}
                );
                dialog.add(FormViewDialog, {
                    resModel: "product.template",
                    context: makeContext([defaults]),
                    title: _t("Create Item (copy)"),
                    onRecordSaved: async (record) => {
                        const templateId = record.resId;
                        const [template] = await orm.read(
                            "product.template",
                            [templateId],
                            ["product_variant_id", "product_variant_ids"]
                        );
                        const variantId =
                            (template.product_variant_id &&
                                template.product_variant_id[0]) ||
                            (template.product_variant_ids &&
                                template.product_variant_ids[0]);
                        if (variantId) {
                            const [variant] = await orm.read(
                                "product.product",
                                [variantId],
                                ["display_name"]
                            );
                            await fieldUpdate([
                                { id: variantId, display_name: variant.display_name },
                            ]);
                        }
                    },
                });
            },
        });
    },
});
