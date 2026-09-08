# -*- coding: utf-8 -*-
from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.onchange("product_template_id")
    def _amh_onchange_template_resolve_single_variant(self):
        """Resolve a single-variant template to its variant, server-side.

        Odoo 17+ made the order line's visible product field
        ``product_template_id``, which is not stored -- it is a proxy for
        ``product_id``, and the browser is what turns one into the other. The
        ``sol_product_many2one`` widget fires ``_onProductTemplateUpdate`` from
        a ``useEffect``, after render, which calls ``get_single_product_variant``
        and writes ``product_id``. Nothing awaits that effect.

        ``sale.order.line.name`` is ``required=True`` and computes from
        ``product_id`` alone. So between the template landing and the effect
        finishing, the line has no description and cannot be saved. Normally
        nobody notices: the gap is milliseconds and the operator is still
        typing.

        It bites when something navigates inside that window. Pressing the
        create dialog's expand button saves the product, closes the dialog and
        immediately leaves the sales order -- which forces a save of a line
        whose description has not been computed yet. The save fails with
        "Missing required fields", the navigation is cancelled, and the
        operator lands back on the order wondering what it wanted.

        Doing the resolution here puts it inside the onchange round trip that
        the field update already awaits, so the line is complete before
        anything gets the chance to navigate.

        Configurable templates are deliberately left alone: those need the
        product configurator to choose a variant, and that is the browser's
        job. Same for lines that already have a product, and for sections and
        notes, which have no product at all.
        """
        for line in self:
            if line.display_type or line.product_id:
                continue
            template = line.product_template_id
            if not template or template.has_configurable_attributes:
                continue
            variants = template.product_variant_ids
            if len(variants) == 1:
                line.product_id = variants
