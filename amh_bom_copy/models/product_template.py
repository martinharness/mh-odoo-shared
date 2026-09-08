# -*- coding: utf-8 -*-
import json

from odoo import Command, api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    amh_copy_source_id = fields.Many2one(
        comodel_name="product.template",
        string="Copied From",
        copy=False,
        ondelete="set null",
        index=True,
        help="The item this one was copied from. Set automatically by "
             "Duplicate and by 'Create and copy from...'; it is what tells "
             "the server to bring the bill of materials across.",
    )

    # Values that must not ride along in the create dialog's context.
    _AMH_COPY_DEFAULT_SKIP = frozenset({
        "default_code",                  # replaced by the reference just typed
        "barcode",                       # uniqueness-constrained, entered fresh
        "amh_copy_source_id",            # set explicitly below
        "bom_option_group_ids",          # variant FKs; rebuilt after create
        "bom_fixed_component_line_ids",
    })

    # Field types that cannot survive a round trip through a context dict.
    #
    # `properties` is the one that actually bit: copy_data() hands back a
    # Property object, the RPC layer stringifies it to its repr on the way out
    # ("<odoo.orm.fields_properties.Property object at 0x...>"), and default_get
    # then feeds that string to convert_to_cache, which calls json.loads on it
    # and raises JSONDecodeError. Binary is excluded for size rather than
    # correctness -- an image_1920 would travel to the browser and back.
    #
    # Everything listed here is applied server-side in create() instead, by
    # _amh_apply_context_unsafe_values_from().
    _AMH_CONTEXT_UNSAFE_TYPES = frozenset({
        "binary", "properties", "properties_definition", "json",
    })

    # ------------------------------------------------------------------
    # Called by the "Create and copy from..." dropdown entry
    # ------------------------------------------------------------------
    def amh_prepare_copy_defaults(self, typed_reference=None):
        """Return a ``default_*`` context dict pre-filling a create form.

        Derived from ``copy_data()`` rather than a hand-kept field list, so it
        tracks whatever ``copy()`` would produce rather than going stale.
        Whatever the create form does not display is discarded client-side, so
        anything that has to survive regardless is re-applied server-side in
        ``create()``.
        """
        self.ensure_one()
        vals = self.copy_data()[0]
        defaults = {}
        for name, value in vals.items():
            field = self._fields.get(name)
            if not field or name in self._AMH_COPY_DEFAULT_SKIP:
                continue
            if field.type in self._AMH_CONTEXT_UNSAFE_TYPES:
                continue
            # Belt and braces for a field type not yet on the list above: the
            # RPC encoder falls back to str() for anything it cannot encode,
            # which is exactly how the Property object slipped through as a
            # repr string. Drop it here instead.
            try:
                json.dumps(value)
            except TypeError:
                continue
            defaults["default_%s" % name] = value
        defaults["default_amh_copy_source_id"] = self.id
        # copy_data() appends " (copy)" to the name. The operator is renaming
        # this anyway, so hand them the original to edit rather than a name
        # they have to strip a suffix off.
        defaults["default_name"] = self.name
        if typed_reference:
            defaults["default_default_code"] = typed_reference.strip().upper()
        return defaults

    # ------------------------------------------------------------------
    # Duplicate, and create-from-a-source
    # ------------------------------------------------------------------
    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for template, vals in zip(self, vals_list):
            vals.setdefault("amh_copy_source_id", template.id)
            # sale_bom_component_pricing's option-kit records are copy=True,
            # but each one carries a product_id pointing at a *variant of the
            # source template*, and _check_variant_matches_template rejects
            # that against the new template -- so duplicating a configured kit
            # raises today. They are dropped here and rebuilt in create(),
            # once the new variants exist and can be mapped.
            for fname in ("bom_fixed_component_line_ids", "bom_option_group_ids"):
                vals.pop(fname, None)
        return vals_list

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        if self.env.context.get("amh_skip_copy_from_source"):
            return templates
        for template in templates:
            source = template.amh_copy_source_id
            if source and source != template:
                template._amh_pull_from_source(source)
        return templates

    def _amh_pull_from_source(self, source):
        """Bring across everything that could not travel as a create value."""
        self.ensure_one()
        variant_map = self._amh_variant_map_from(source)
        ptav_map = self._amh_ptav_map_from(source)
        self._amh_apply_context_unsafe_values_from(source)
        self._amh_copy_boms_from(source, variant_map, ptav_map)
        self._amh_copy_kit_options_from(source, variant_map)

    def _amh_apply_context_unsafe_values_from(self, source):
        """Write the values that could not travel through the create dialog.

        Images and Properties are stripped from amh_prepare_copy_defaults()
        because a context dict cannot carry them intact; they are applied here
        instead. Only fields still empty on the new record are touched, so
        nothing the operator typed in the dialog is overwritten.
        """
        self.ensure_one()
        vals = {}
        for name, field in self._fields.items():
            if field.type not in self._AMH_CONTEXT_UNSAFE_TYPES:
                continue
            if not field.store or field.related:
                continue
            if field.compute and field.readonly:
                continue
            if self[name] or not source[name]:
                continue
            # A Properties value is only meaningful against the definition it
            # was written for. If the operator moved the copy to a different
            # category, its definition no longer matches and the values would
            # be nonsense -- leave them off rather than write garbage.
            definition = getattr(field, "definition", None)
            if definition:
                owner = definition.rsplit(".", 1)[0]
                if self.mapped(owner) != source.mapped(owner):
                    continue
            vals[name] = source[name]
        if vals:
            self.write(vals)

    # ------------------------------------------------------------------
    # Mapping the source's variants and attribute values onto ours
    # ------------------------------------------------------------------
    def _amh_variant_map_from(self, source):
        """{source variant id: this template's matching variant id}.

        Variants are matched on the set of underlying product.attribute.value
        records, because product.template.attribute.value records are per
        template and so are never shared between the two.
        """
        self.ensure_one()

        def key(variant):
            return frozenset(
                variant.product_template_attribute_value_ids
                .mapped("product_attribute_value_id").ids
            )

        mine = {key(variant): variant.id for variant in self.product_variant_ids}
        return {
            variant.id: mine.get(key(variant))
            for variant in source.product_variant_ids
        }

    def _amh_ptav_map_from(self, source):
        """{source ptav id: this template's matching ptav id}."""
        self.ensure_one()
        mine = {
            (ptav.attribute_id.id, ptav.product_attribute_value_id.id): ptav.id
            for ptav in self.attribute_line_ids.product_template_value_ids
        }
        return {
            ptav.id: mine.get(
                (ptav.attribute_id.id, ptav.product_attribute_value_id.id)
            )
            for ptav in source.attribute_line_ids.product_template_value_ids
        }

    # ------------------------------------------------------------------
    # The bills of materials
    # ------------------------------------------------------------------
    def _amh_copy_boms_from(self, source, variant_map, ptav_map):
        """Clone every one of the source's BoMs onto this template.

        The copy is deliberately made **on the source template first** and only
        then moved. ``mrp.bom._check_bom_lines`` is an ``@api.constrains`` on
        ``product_tmpl_id``, ``bom_line_ids``, ``byproduct_ids`` and
        ``operation_ids``, and it rejects any 'Apply on Variants' value whose
        template is not the BoM's own. A copy made directly onto this template
        therefore raises *during the copy* -- before anything gets the chance
        to correct those values:

            The attribute value Beta Color: Black BL520 set on product X does
            not match the BoM product X (copy).

        Copying in place keeps every intermediate state valid, and lets
        ``mrp.bom.copy()`` do its own remapping of each line's ``operation_id``
        and of the operations' ``blocked_by_operation_ids``, which is worth
        keeping rather than reimplementing. Clearing the collections in the
        copy default is the other obvious route and is a trap: that method zips
        old operations against new, so with operations cleared its dependency
        pass raises KeyError on any BoM whose operations block each other.

        The move is then a single ``write()`` carrying the new template, the
        variant pin and every corrected attribute value together, so the
        constraint only ever sees the finished state.
        """
        self.ensure_one()
        if "bom_ids" not in self._fields:
            return
        for bom in source.bom_ids:
            if bom.company_id and self.company_id and bom.company_id != self.company_id:
                continue
            copied = bom.copy()
            copied.write(self._amh_bom_retarget_vals(
                copied, variant_map.get(bom.product_id.id), ptav_map))

    def _amh_bom_retarget_vals(self, bom, new_variant_id, ptav_map):
        """One write that moves a copied BoM onto this template.

        Everything that has to change together goes in here: the template, the
        variant pin, and the 'Apply on Variants' values on every component,
        by-product **and operation**. Operations carry that field too and are
        checked by the same constraint, which is easy to miss because they are
        the rarest of the three to use it.

        Splitting this into separate writes would leave the BoM pointing at one
        template while its attribute values still point at another, which is
        precisely what the constraint exists to catch.
        """
        self.ensure_one()
        vals = {
            "product_tmpl_id": self.id,
            "product_id": new_variant_id or False,
        }
        for field_name in ("bom_line_ids", "byproduct_ids", "operation_ids"):
            updates = [
                Command.update(record.id, {
                    "bom_product_template_attribute_value_ids":
                        self._amh_ptav_command(record, ptav_map),
                })
                for record in bom[field_name]
                if record.bom_product_template_attribute_value_ids
            ]
            if updates:
                vals[field_name] = updates
        return vals

    def _amh_ptav_command(self, record, ptav_map):
        """Repoint 'Apply on Variants' at this template's own values.

        Many2many fields copy by default, so a copied record arrives holding
        the *source* template's ``product.template.attribute.value`` records.
        Anything without a counterpart here is dropped rather than guessed at:
        a line that applied to one specific colour on the source, where this
        product has no such colour, is better applied to all of them than to
        the wrong one.
        """
        mapped = [
            ptav_map[ptav.id]
            for ptav in record.bom_product_template_attribute_value_ids
            if ptav_map.get(ptav.id)
        ]
        return [Command.set(mapped)]

    # ------------------------------------------------------------------
    # sale_bom_component_pricing's option-kit configuration
    # ------------------------------------------------------------------
    def _amh_copy_kit_options_from(self, source, variant_map):
        """Rebuild the option-kit records stripped in copy_data().

        Skips any record whose source variant has no counterpart here -- the
        models require a variant and would raise, and a wrong variant is worse
        than a missing line.
        """
        self.ensure_one()
        for fname in ("bom_fixed_component_line_ids", "bom_option_group_ids"):
            if fname not in self._fields or fname not in source._fields:
                continue
            for record in source[fname]:
                new_variant = variant_map.get(record.product_id.id)
                if record.product_id and not new_variant:
                    continue
                record.copy({
                    "product_tmpl_id": self.id,
                    "product_id": new_variant or False,
                })
