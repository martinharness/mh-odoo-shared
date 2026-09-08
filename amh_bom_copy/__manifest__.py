# -*- coding: utf-8 -*-
{
    "name": "AMH Item & BoM Copy",
    "version": "19.0.2.3.0",
    "summary": "Copy an item and its bill of materials in one step, from the "
               "sales order line, from Duplicate, or from the BoM form.",
    "author": "Aaron Martin Harness Ltd",
    "category": "Manufacturing/Manufacturing",
    "license": "LGPL-3",
    "depends": ["mrp", "sale"],
    "data": [
        "views/product_views.xml",
        "views/mrp_bom_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "amh_bom_copy/static/src/js/amh_create_and_copy.js",
        ],
    },
    "installable": True,
    "application": False,
    "description": """
AMH Item & BoM Copy
===================

Most new items here are "the same as that one, with two changes". This module
makes that the short path, in the three places the work actually starts.

The technical module name stays ``amh_bom_copy`` -- renaming a directory
would orphan the installed record and need a manual rename in
``ir_module_module``. Only the display name changed.

1. "Create and copy from..." on a product many2one
--------------------------------------------------

Type a reference into a sales order line, and a third entry now sits under
"Create and edit...". It asks which item to copy, then opens the ordinary
create form pre-filled from that item, with the text just typed entered as
the new **internal reference, upper-cased**. Save, and the bill of materials
comes across with it.

Nothing is written until Save, so a mistyped reference is still discarded
with no trace -- the property that makes "Create and edit..." safe to reach
for in the first place.

Because the bill of materials lands in the same save as the product, the
sales order line prices off it immediately. Creating the product first and
building its BoM afterwards leaves the line priced against a product that had
no components yet.

2. Duplicate now carries the bill of materials
-----------------------------------------------

``product.template.copy()`` copies the BoMs too, the way EBMS did. Variant
pins and 'Apply on Variants' values are remapped onto the new template's own
variants and attribute values, so a duplicate of a multi-variant item is
usable rather than subtly wrong.

Suppress it with ``amh_skip_copy_from_source`` in the context.

3. "Copy From Another Item" on the BoM form
--------------------------------------------

A block at the top of the Components tab: pick a source item, pick (or
auto-select) its bill of materials, choose replace or append, press Copy
Components. Components, operations and by-products are cloned. This is the
path for a BoM that already exists, or one being built on an item that was
not copied from anything.

It is on the form and not beside **New** in the BoM list because that list is
filtered to the current item -- it never shows a candidate to copy *from*, and
there is no target BoM to copy *into* until one exists.

Notes for maintainers, learned the hard way
--------------------------------------------

* **Copy a BoM onto the SOURCE template first, then move it.**
  ``mrp.bom._check_bom_lines`` is an ``@api.constrains`` on
  ``product_tmpl_id``, ``bom_line_ids``, ``byproduct_ids`` and
  ``operation_ids``, and it rejects any 'Apply on Variants' value whose
  template is not the BoM's own. Copying straight onto the new template raises
  *during the copy*, before any remap can run -- "The attribute value Beta
  Color: Black BL520 set on product X does not match the BoM product X (copy)".
  Copying in place keeps every intermediate state valid; the move is then one
  ``write()`` carrying the template, the variant pin and every corrected
  attribute value together, so the constraint only ever sees the finished
  state.
* **Operations carry ``bom_product_template_attribute_value_ids`` too**, and
  the same constraint reads them. Easy to miss, because they are the rarest of
  the three to use it -- the first version of the remap only covered lines and
  by-products.
* **Do not clear the collections in the copy default** to dodge the above.
  ``mrp.bom.copy()`` zips old operations against new, so with ``operation_ids``
  cleared its dependency pass raises ``KeyError`` on any BoM whose operations
  block each other.
* **Duplicating a configured option-kit raised a ValidationError before this
  module.** ``sale_bom_component_pricing``'s ``bom_option_group_ids`` and
  ``bom_fixed_component_line_ids`` are ``copy=True``, but each record carries
  a ``product_id`` pointing at a *variant of the source template*, and
  ``_check_variant_matches_template`` rejects that against the new template.
  ``copy_data()`` here strips both fields and ``create()`` rebuilds them once
  the new variants exist and can be mapped. Any record whose source variant
  has no counterpart is skipped -- a missing line beats a wrong variant.
* Variants are matched on the set of underlying ``product.attribute.value``
  ids, never on ``product.template.attribute.value``: ptav records are per
  template and are never shared between the source and the copy. The same
  goes for the ptav map used for 'Apply on Variants'.
* ``amh_copy_source_id`` is what triggers the pull, and it has to survive the
  round trip from the create dialog -- hence ``force_save="1"`` next to
  ``readonly="1"`` in the view. A readonly field with no ``force_save`` is
  dropped from the save payload and the BoM would silently not copy.
* ``amh_prepare_copy_defaults()`` derives its ``default_*`` dict from
  ``copy_data()`` rather than a hand-kept field list, so it does not go stale.
  Values for fields the create form does not display are discarded by the
  client -- anything that must survive regardless belongs in
  ``_amh_pull_from_source()``, not in the defaults.
* **Not every field survives a context dict, and the failure is ugly.**
  ``copy_data()`` returns ``product_properties`` as a ``Property`` object; the
  RPC encoder falls back to ``str()`` on the way out, so it reaches the browser
  as ``"<odoo.orm.fields_properties.Property object at 0x...>"``, comes back as
  that string, and ``default_get`` feeds it to ``convert_to_cache`` which calls
  ``json.loads`` on it -- ``JSONDecodeError: Expecting value: line 1 column 1``.
  ``_AMH_CONTEXT_UNSAFE_TYPES`` drops properties, binary and json values from
  the context, and ``_amh_apply_context_unsafe_values_from()`` writes them
  server-side after create instead. There is a ``json.dumps`` guard behind the
  type list for whatever the next such type turns out to be.
* A Properties value is only meaningful against the definition it was written
  for, so it is skipped when the operator moved the copy to a different
  category. The owning record is resolved from the field's own ``definition``
  path rather than hardcoding ``categ_id``.
* **Ship code and its manifest version in the same commit.** Splitting them
  put production on a build that had loaded ``product_template.py`` -- so the
  registry carried a stored ``amh_copy_source_id`` whose column was never
  created and whose views were never loaded -- because the module version had
  not changed and no update ran.

Why there is a sale.order.line onchange in here
------------------------------------------------

``product_template_id`` on an order line is **not stored**. It is a proxy for
``product_id``, and the browser is what turns one into the other: the
``sol_product_many2one`` widget fires ``_onProductTemplateUpdate`` from a
``useEffect`` after render, which calls ``get_single_product_variant`` and
writes ``product_id``. Nothing awaits that effect.

``sale.order.line.name`` is ``required=True`` and depends on ``product_id``
alone, so between the template landing and the effect finishing the line has
no description and cannot be saved. Normally the gap is milliseconds and
nobody notices. It bites when something navigates inside that window: the
create dialog's expand button saves the product, closes, and leaves the sales
order immediately, forcing a save of a line whose description does not exist
yet -- "Missing required fields", navigation cancelled, operator dumped back
on the order.

``_amh_onchange_template_resolve_single_variant`` does that resolution inside
the onchange round trip the field update already awaits, so the line is
complete before anything can navigate. Configurable templates are left alone;
they need the configurator, and that is the browser's job.

Worth knowing: ``amh_product_exact_search`` normally papers over this, because
its ``update`` override resolves an exact typed SKU straight to a variant --
but only within a five second window of the text being typed. Picking a source
item and editing a name blows straight through that, which is why this flow
surfaced a race that had always been there.

The picker's search view
-------------------------

The "Copy which item?" dialog pins its search view with ``search_view_ref``,
to ``view_product_template_search_amh_copy`` -- a **primary** inherit of
``product.product_template_search_view`` with the Internal Reference inserted
as the first field, so typing a SKU searches references rather than names.
Being a primary inherit, it keeps every core filter and group-by along with
``amh_product_exact_search``'s extension of the name field.

Pinning it is not belt and braces. A view left unpinned resolves to the
model's default: the primary view of that type sorting first by
``(priority, name, id)``. ``product.template`` has five primary search views,
all at priority 16, so the winner is decided alphabetically by view *name* --
and ``amh_image_vet``'s ``image.vet.product.search`` takes it, which is why the
picker first shipped with a plain "Name" search that could not find ``nk001``.
This view sits at priority 99 so it can never become the model default itself.

Raising ``amh_image_vet``'s priority is the proper fix for everyone else --
every record picker on ``product.template`` currently gets that bespoke search
view, whose plain name field never sees ``amh_product_exact_search``'s
filter_domain -- and it is still outstanding. Bumping that module's version to
apply it turned the build red on both branches, and it has never been updated
since it was installed, so whatever breaks on re-running its data files has to
be understood first. Pinning here means this feature does not wait on that.

More notes
-----------

* The dropdown entry is added by patching ``Many2XAutocomplete.actionSuggestions``
  -- the getter Odoo 19 introduced for exactly this, returning a list of
  ``{enabled, build}`` descriptors. ``amh_product_exact_search`` patches
  ``addCreateSuggestion`` on the same prototype to remove the bare
  ``Create "xxx"`` row; the two touch different methods and compose in either
  load order. Both are gated on ``props.resModel``, so the entry appears on
  every ``product.template`` many2one in the backend, not only sales order
  lines. That is deliberate -- the same shortcut is worth having on a purchase
  line.
* The five ``amh_copy_*`` fields on ``mrp.bom`` are stored columns on purpose.
  The copy runs from a ``type="object"`` button and the web client saves the
  form before executing a button, so the server reads the operator's choice
  back off the record. Non-stored fields arrive as ``False``.
* ``blocked_by_operation_ids`` is cleared then rebuilt between the *copies* by
  the BoM-form button. Core's own ``copy_to_bom`` for operations does not do
  this and leaves cross-BoM dependencies behind.
""",
}
