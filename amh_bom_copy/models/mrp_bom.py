# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    # ------------------------------------------------------------------
    # Operator input for the "copy from another item" shortcut.
    #
    # These five columns are STORED on purpose.  The copy runs from a
    # type="object" button, and the web client saves the form before it
    # executes a button, so the server has to be able to read the operator's
    # selection back off the record.  A non-stored field would arrive as
    # False.  They are cleared again the moment the copy succeeds, and
    # copy=False keeps them out of duplicated BoMs.
    # ------------------------------------------------------------------
    amh_copy_from_product_id = fields.Many2one(
        comodel_name="product.template",
        string="Copy From Item",
        copy=False,
        ondelete="set null",
        help="The item whose bill of materials you want to pull components "
             "from. Type its internal reference to find it.",
    )
    amh_copy_from_bom_id = fields.Many2one(
        comodel_name="mrp.bom",
        string="Copy From BoM",
        copy=False,
        ondelete="set null",
        help="The bill of materials to copy. Filled in for you when the item "
             "you picked only has one.",
    )
    amh_copy_mode = fields.Selection(
        selection=[
            ("replace", "Replace what is here"),
            ("append", "Add to what is here"),
        ],
        string="Copy Mode",
        default="replace",
        copy=False,
        help="Replace clears this bill of materials first, so picking the "
             "wrong source and correcting it does not leave both sets of "
             "components behind.",
    )
    amh_copy_operations = fields.Boolean(
        string="Include Operations",
        default=True,
        copy=False,
        help="Also copy the source's operations. Each component's 'Consumed "
             "in Operation' link is remapped onto the copied operation.",
    )
    amh_copy_byproducts = fields.Boolean(
        string="Include By-products",
        default=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Onchanges
    # ------------------------------------------------------------------
    @api.onchange("amh_copy_from_product_id")
    def _onchange_amh_copy_from_product_id(self):
        """Drop a stale source BoM, and preselect when there is only one."""
        for bom in self:
            product = bom.amh_copy_from_product_id
            if not product:
                bom.amh_copy_from_bom_id = False
                continue
            if bom.amh_copy_from_bom_id.product_tmpl_id != product:
                bom.amh_copy_from_bom_id = False
            candidates = bom._amh_candidate_source_boms(product)
            if len(candidates) == 1:
                bom.amh_copy_from_bom_id = candidates

    @api.onchange("amh_copy_from_bom_id")
    def _onchange_amh_copy_from_bom_id(self):
        """Backfill the item when the operator went straight for the BoM."""
        for bom in self:
            source = bom.amh_copy_from_bom_id
            if source and not bom.amh_copy_from_product_id:
                bom.amh_copy_from_product_id = source.product_tmpl_id

    def _amh_candidate_source_boms(self, product):
        """Every BoM of `product` that could legitimately be copied here."""
        self.ensure_one()
        company = self.company_id or self.env.company
        domain = [
            ("product_tmpl_id", "=", product.id),
            ("company_id", "in", [company.id, False]),
        ]
        # self._origin is the saved record behind an in-form NewId, and is an
        # empty recordset on a BoM that has never been saved.
        if self._origin.id:
            domain.append(("id", "!=", self._origin.id))
        return self.env["mrp.bom"].search(domain)

    # ------------------------------------------------------------------
    # The button
    # ------------------------------------------------------------------
    def amh_action_copy_from_bom(self):
        """Copy the chosen bill of materials onto this one.

        Returns True rather than an action: the web client reloads the form
        after a button that returns no action, which is what puts the copied
        components on screen.  A display_notification would suppress that
        reload on some paths, and the copied lines appearing *is* the
        confirmation.
        """
        self.ensure_one()
        source = self.amh_copy_from_bom_id
        if not source:
            raise UserError(_(
                "Choose the bill of materials you want to copy from first."
            ))
        if source == self:
            raise UserError(_(
                "A bill of materials cannot be copied onto itself."
            ))
        if source.company_id and self.company_id and source.company_id != self.company_id:
            raise UserError(_(
                "%(source)s belongs to %(source_company)s, but this bill of "
                "materials belongs to %(target_company)s.",
                source=source.display_name,
                source_company=source.company_id.display_name,
                target_company=self.company_id.display_name,
            ))

        self._amh_copy_bom_content(
            source,
            mode=self.amh_copy_mode or "replace",
            with_operations=self.amh_copy_operations,
            with_byproducts=self.amh_copy_byproducts,
        )

        self.write({
            "amh_copy_from_product_id": False,
            "amh_copy_from_bom_id": False,
        })
        return True

    # ------------------------------------------------------------------
    # The copy itself
    # ------------------------------------------------------------------
    def _amh_copy_bom_content(self, source, mode="replace",
                              with_operations=True, with_byproducts=True):
        """Clone `source`'s components, operations and by-products onto self.

        Everything goes through the ORM's own copy(), so stored columns added
        by other modules -- amh_production_reports' production_note being the
        obvious one -- come across without this module having to know they
        exist.  Four things are deliberately overridden:

        * ``bom_id`` -- repointed at this BoM.
        * ``bom_product_template_attribute_value_ids`` ("Apply on Variants") --
          those are attribute values of the *source* template. Left alone,
          copy() would carry them over (m2m fields copy by default) and they
          are meaningless, and unwritable, against a different product.
        * ``operation_id`` on components and by-products -- remapped onto the
          copied operation, or cleared when operations are not being copied.
        * ``blocked_by_operation_ids`` on operations -- cleared during the
          first pass so the copies never point at the source's operations,
          then rebuilt between the copies in a second pass.

        Returns a dict of counts, for callers that want to report.
        """
        self.ensure_one()
        counts = {"components": 0, "operations": 0, "byproducts": 0}

        # An operator without the routings / by-products groups cannot see
        # those tabs at all, so silently skip rather than create records they
        # have no way to review.
        with_operations = with_operations and self.env.user.has_group(
            "mrp.group_mrp_routings")
        with_byproducts = with_byproducts and self.env.user.has_group(
            "mrp.group_mrp_byproducts")

        if mode == "replace":
            self.bom_line_ids.unlink()
            if with_operations:
                self.operation_ids.unlink()
            if with_byproducts:
                self.byproduct_ids.unlink()

        operation_map = {}
        if with_operations and source.operation_ids:
            # Operation dependencies are only usable on a BoM that allows
            # them, so carry that switch across too -- otherwise the second
            # pass below would write links the form cannot show.
            if source.allow_operation_dependencies and not self.allow_operation_dependencies:
                self.allow_operation_dependencies = True

            for operation in source.operation_ids.sorted(lambda o: (o.sequence, o.id)):
                copied = operation.copy({
                    "bom_id": self.id,
                    "blocked_by_operation_ids": [Command.clear()],
                })
                operation_map[operation.id] = copied.id
                counts["operations"] += 1

            if self.allow_operation_dependencies:
                for operation in source.operation_ids:
                    blockers = [
                        operation_map[blocker.id]
                        for blocker in operation.blocked_by_operation_ids
                        if blocker.id in operation_map
                    ]
                    if blockers:
                        self.env["mrp.routing.workcenter"].browse(
                            operation_map[operation.id]
                        ).blocked_by_operation_ids = [Command.set(blockers)]

        for line in source.bom_line_ids.sorted(lambda l: (l.sequence, l.id)):
            line.copy({
                "bom_id": self.id,
                "operation_id": operation_map.get(line.operation_id.id, False),
                "bom_product_template_attribute_value_ids": [Command.clear()],
            })
            counts["components"] += 1

        if with_byproducts:
            for byproduct in source.byproduct_ids.sorted(lambda b: (b.sequence, b.id)):
                byproduct.copy({
                    "bom_id": self.id,
                    "operation_id": operation_map.get(byproduct.operation_id.id, False),
                    "bom_product_template_attribute_value_ids": [Command.clear()],
                })
                counts["byproducts"] += 1

        return counts
