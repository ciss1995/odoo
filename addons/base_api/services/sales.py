# -*- coding: utf-8 -*-
"""Sales orchestration service.

Business logic for multi-step sale flows, extracted from the HTTP controller so
a second channel (the WhatsApp worker) can drive the *same* orchestration
without re-implementing it or round-tripping through the HTTP endpoint.

Functions here take an Odoo ``env`` and plain, already-parsed params — no
``request`` / HTTP coupling — and raise :class:`SalesServiceError` for
business-validation failures. Each caller translates that to its own transport:
the HTTP controller maps ``status``/``code`` onto its error envelope; a WhatsApp
worker reads ``message``/``code`` to compose a reply.
"""

import logging

_logger = logging.getLogger(__name__)


class SalesServiceError(Exception):
    """A business-validation failure in a sales orchestration.

    Carries ``code`` and ``status`` so an HTTP caller reproduces the exact
    error envelope the endpoint used before extraction, while a non-HTTP caller
    can branch on ``code`` directly.
    """

    def __init__(self, message, code='PURCHASE_ERROR', status=400):
        self.message = message
        self.code = code
        self.status = status
        super().__init__(message)


def _resolve_partner(env, partner_id):
    """Resolve the sale's customer, defaulting to a shared walk-in partner.

    A provided ``partner_id`` must exist (and not be the company's own partner
    record); otherwise reuse/create the "Walk-In Store Customer".
    """
    company_partner_id = env.company.partner_id.id
    if partner_id and partner_id != company_partner_id:
        partner = env['res.partner'].browse(partner_id)
        if not partner.exists():
            raise SalesServiceError(f"Partner {partner_id} not found", 'NOT_FOUND', 404)
        return partner

    partner = env['res.partner'].sudo().search([
        ('name', '=', 'Walk-In Store Customer'),
        ('company_id', 'in', [env.company.id, False]),
    ], limit=1)
    if not partner:
        partner = env['res.partner'].sudo().create({
            'name': 'Walk-In Store Customer',
            'company_id': env.company.id,
            'customer_rank': 1,
        })
    return partner


def _build_order_line_commands(env, order_lines):
    """Validate the requested lines and return sale.order.line create commands."""
    if not order_lines or not isinstance(order_lines, list):
        raise SalesServiceError(
            "'order_lines' is required and must be a non-empty list",
            'MISSING_PARAM', 400,
        )
    sol_vals = []
    for idx, line in enumerate(order_lines):
        pid = line.get('product_id')
        if not pid:
            raise SalesServiceError(
                f"order_lines[{idx}]: 'product_id' is required", 'MISSING_PARAM', 400)
        product = env['product.product'].browse(pid)
        if not product.exists():
            raise SalesServiceError(f"Product {pid} not found", 'NOT_FOUND', 404)
        qty = line.get('quantity', 1)
        if qty <= 0:
            raise SalesServiceError(
                f"order_lines[{idx}]: 'quantity' must be > 0", 'INVALID_PARAM', 400)
        sol_vals.append((0, 0, {
            'product_id': pid,
            'product_uom_qty': qty,
            'price_unit': line.get('price_unit', product.list_price),
        }))
    return sol_vals


def _resolve_ship_only_warehouse(env, warehouse_id):
    """Resolve a one-step (ship_only) warehouse for instant counter delivery."""
    Warehouse = env['stock.warehouse']
    company_id = env.company.id
    if warehouse_id:
        warehouse = Warehouse.browse(warehouse_id)
        if not warehouse.exists():
            raise SalesServiceError(f"Warehouse {warehouse_id} not found", 'NOT_FOUND', 404)
        if warehouse.delivery_steps != 'ship_only':
            raise SalesServiceError(
                f"Warehouse '{warehouse.name}' uses multi-step delivery "
                f"({warehouse.delivery_steps}). In-store purchases require "
                "a warehouse with one-step delivery (ship_only).",
                'INVALID_WAREHOUSE', 400,
            )
        return warehouse

    warehouse = Warehouse.search([
        ('company_id', '=', company_id),
        ('delivery_steps', '=', 'ship_only'),
    ], limit=1)
    if not warehouse:
        # Fall back to the company's default warehouse and switch it to
        # one-step so the in-store flow has a clean single picking.
        warehouse = Warehouse.search([('company_id', '=', company_id)], limit=1)
        if not warehouse:
            raise SalesServiceError(
                "No warehouse found for the current company.", 'NO_WAREHOUSE', 400)
        if warehouse.delivery_steps != 'ship_only':
            warehouse.sudo().write({'delivery_steps': 'ship_only'})
    return warehouse


def record_in_store_sale(env, *, order_lines, partner_id=None, warehouse_id=None,
                         journal_id=None, payment_date=None, invoice_date=None):
    """One-shot in-store purchase: create SO → confirm → deliver → invoice →
    post → register full payment, atomically.

    Runs under the caller's ``env`` so ACL applies to the sale/invoice/payment
    writes; only the counter-flow conveniences that legitimately need elevation
    (the shared walk-in partner, temporary route stripping) use ``sudo()``.

    Returns a JSON-serializable dict of ``read()``-shaped records
    (``sale_order``, ``invoice``, ``payment``, ``pickings``, ``stock_moves``).
    Raises :class:`SalesServiceError` on business-validation failures; ORM
    errors (AccessError / UserError / ValidationError) propagate to the caller.
    """
    sol_vals = _build_order_line_commands(env, order_lines)
    partner = _resolve_partner(env, partner_id)
    warehouse = _resolve_ship_only_warehouse(env, warehouse_id)

    # --- Step 2: create & confirm the sale order ---------------------------
    order = env['sale.order'].create({
        'partner_id': partner.id,
        'warehouse_id': warehouse.id,
        'order_line': sol_vals,
    })

    # Strip routes on lines, products, and categories so procurement uses the
    # warehouse's simple ship_only route rather than any multi-step route
    # configured on the product/category. Restored in the finally block.
    order.order_line.write({'route_ids': [(5, 0, 0)]})
    products = order.order_line.mapped('product_id')
    saved_product_routes = {p.id: p.route_ids for p in products}
    saved_categ_routes = {}
    categs = products.mapped('categ_id')
    for categ in categs:
        saved_categ_routes[categ.id] = categ.route_ids
        categ.sudo().write({'route_ids': [(5, 0, 0)]})
    products.sudo().write({'route_ids': [(5, 0, 0)]})

    try:
        order.action_confirm()
    finally:
        for p in products:
            if saved_product_routes.get(p.id):
                p.sudo().write({'route_ids': [(6, 0, saved_product_routes[p.id].ids)]})
        for categ in categs:
            if saved_categ_routes.get(categ.id):
                categ.sudo().write({'route_ids': [(6, 0, saved_categ_routes[categ.id].ids)]})

    # --- Step 3: instant delivery — validate all pickings ------------------
    pickings_data = []
    for picking in order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
        for move in picking.move_ids:
            move.write({'quantity': move.product_uom_qty, 'picked': True})
        picking.button_validate()
        pickings_data.append({
            'id': picking.id,
            'name': picking.name,
            'state': picking.state,
        })

    # --- Step 4: create & post the invoice ---------------------------------
    invoices = order._create_invoices()
    if not invoices:
        raise SalesServiceError(
            "No invoice could be created. Check product invoice policies.",
            'NOTHING_TO_INVOICE', 400,
        )
    invoice = invoices[0] if len(invoices) > 1 else invoices
    if invoice_date:
        invoice.write({'invoice_date': invoice_date})
    invoice.action_post()

    # --- Step 5: register payment (instant, full amount) -------------------
    ctx = {'active_model': 'account.move', 'active_ids': invoice.ids}
    wizard_vals = {}
    if journal_id:
        wizard_vals['journal_id'] = journal_id
    if payment_date:
        wizard_vals['payment_date'] = payment_date
    pay_wizard = env['account.payment.register'].with_context(**ctx).create(wizard_vals)
    pay_wizard.action_create_payments()

    invoice.invalidate_recordset()
    order.invalidate_recordset()

    payment = env['account.payment'].search(
        [('reconciled_invoice_ids', 'in', invoice.ids)], limit=1, order='id desc')

    # --- Build the serializable result -------------------------------------
    inv_data = invoice.read([
        'id', 'name', 'state', 'move_type',
        'partner_id', 'invoice_date', 'invoice_date_due',
        'amount_untaxed', 'amount_tax', 'amount_total',
        'amount_residual', 'payment_state', 'currency_id',
        'invoice_origin',
    ])[0]
    order_data = order.read([
        'id', 'name', 'state', 'partner_id',
        'amount_untaxed', 'amount_tax', 'amount_total',
        'invoice_status',
    ])[0]
    payment_data = {}
    if payment:
        payment_data = payment.read([
            'id', 'name', 'state', 'amount',
            'payment_type', 'journal_id', 'date',
        ])[0]

    stock_moves = []
    for move in order.picking_ids.move_ids:
        stock_moves.append({
            'id': move.id,
            'product_id': move.product_id.id,
            'product_name': move.product_id.display_name,
            'quantity': move.quantity,
            'state': move.state,
            'reference': move.reference,
        })

    return {
        'sale_order': order_data,
        'invoice': inv_data,
        'payment': payment_data,
        'pickings': pickings_data,
        'stock_moves': stock_moves,
    }
