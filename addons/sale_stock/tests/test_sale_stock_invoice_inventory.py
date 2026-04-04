# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import Command
from odoo.addons.sale_stock.tests.common import TestSaleStockCommon
from odoo.addons.stock_account.tests.test_anglo_saxon_valuation_reconciliation_common import ValuationReconciliationTestCommon
from odoo.tests import Form, tagged


@tagged('post_install', '-at_install')
class TestSaleStockInvoiceInventory(TestSaleStockCommon, ValuationReconciliationTestCommon):
    """Test that creating an invoice from a sale order does NOT update
    inventory. Stock moves and quant quantities should only change when
    the delivery picking is validated, not when an invoice is created."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.company_data['default_warehouse']
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.storable_product = cls.env['product.product'].create({
            'name': 'Test Storable Product',
            'type': 'consu',
            'is_storable': True,
            'invoice_policy': 'order',
            'list_price': 100.0,
            'standard_price': 50.0,
        })
        # Put 20 units in stock
        cls.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': cls.storable_product.id,
            'inventory_quantity': 20,
            'location_id': cls.stock_location.id,
        }).action_apply_inventory()

    def _get_on_hand_qty(self, product):
        """Return the current on-hand quantity in the main stock location."""
        return self.env['stock.quant']._get_available_quantity(
            product, self.stock_location,
        )

    def test_invoice_creation_does_not_affect_stock(self):
        """Creating an invoice from a confirmed SO must not change stock
        quantities or stock move states. Only picking validation does."""
        product = self.storable_product

        # ── 1. Record initial stock ──
        qty_before = self._get_on_hand_qty(product)
        self.assertEqual(qty_before, 20.0, "Should start with 20 units on hand")

        # ── 2. Create and confirm a sale order for 5 units ──
        so = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'product_id': product.id,
                'product_uom_qty': 5,
                'price_unit': product.list_price,
            })],
        })
        so.action_confirm()

        # Confirming the SO should create a picking with stock moves
        self.assertTrue(so.picking_ids, "A picking should be created on SO confirmation")
        picking = so.picking_ids
        moves = picking.move_ids
        self.assertEqual(len(moves), 1, "There should be exactly one stock move")
        self.assertIn(moves.state, ('confirmed', 'assigned'),
                      "Stock move should be confirmed or assigned, not done")

        # Stock on hand should be unchanged (reserved but not consumed)
        qty_after_confirm = self._get_on_hand_qty(product)
        # Available qty may decrease due to reservation, but quant quantity stays
        quant_qty = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(quant_qty, 20.0,
                         "On-hand quant quantity must remain 20 after SO confirmation")

        # ── 3. Create an invoice (without delivering) ──
        invoice = so._create_invoices()
        self.assertTrue(invoice, "Invoice should be created (invoice_policy=order)")

        # Stock moves should NOT be affected by invoicing
        moves.invalidate_recordset()
        self.assertIn(moves.state, ('confirmed', 'assigned'),
                      "Stock move state must not change after invoice creation")

        quant_qty_after_invoice = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(quant_qty_after_invoice, 20.0,
                         "On-hand quant quantity must remain 20 after invoice creation")

        # No new stock moves should have been created by invoicing
        all_moves = self.env['stock.move'].search([
            ('sale_line_id', 'in', so.order_line.ids),
        ])
        self.assertEqual(len(all_moves), 1,
                         "Invoicing must not create additional stock moves")

        # ── 4. Post (validate) the invoice ──
        invoice.action_post()

        moves.invalidate_recordset()
        self.assertIn(moves.state, ('confirmed', 'assigned'),
                      "Stock move state must not change after invoice posting")

        quant_qty_after_post = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(quant_qty_after_post, 20.0,
                         "On-hand quant quantity must remain 20 after invoice posting")

        # ── 5. Now validate the picking — THIS should update stock ──
        picking.move_ids.write({'quantity': 5, 'picked': True})
        picking.button_validate()

        moves.invalidate_recordset()
        self.assertEqual(moves.state, 'done',
                         "Stock move should be 'done' after picking validation")

        quant_qty_after_delivery = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(quant_qty_after_delivery, 15.0,
                         "On-hand quantity should drop to 15 after delivering 5 units")

    def test_invoice_delivery_policy_requires_picking_first(self):
        """For products with invoice_policy='delivery', invoicing depends on
        validated stock moves, confirming the delivery drives the flow."""
        product_delivery = self.company_data['product_delivery_no']
        product_delivery.is_storable = True

        # Put 10 units in stock
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product_delivery.id,
            'inventory_quantity': 10,
            'location_id': self.stock_location.id,
        }).action_apply_inventory()

        qty_before = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product_delivery.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )

        # Create and confirm SO
        so = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'product_id': product_delivery.id,
                'product_uom_qty': 3,
                'price_unit': product_delivery.list_price,
            })],
        })
        so.action_confirm()
        picking = so.picking_ids

        # With delivery policy, qty_delivered should be 0 before delivery
        sol = so.order_line
        self.assertEqual(sol.qty_delivered, 0.0,
                         "No quantity should be delivered before picking validation")

        # Validate the picking — stock should change
        picking.move_ids.write({'quantity': 3, 'picked': True})
        picking.button_validate()

        sol.invalidate_recordset()
        self.assertEqual(sol.qty_delivered, 3.0,
                         "Delivered qty should be 3 after picking validation")

        qty_after_delivery = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product_delivery.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(qty_after_delivery, qty_before - 3,
                         "Stock should decrease by 3 after delivery")

        # Now create the invoice — stock must NOT change further
        invoice = so._create_invoices()
        self.assertTrue(invoice, "Invoice should be created after delivery")

        qty_after_invoice = sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product_delivery.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )
        self.assertEqual(qty_after_invoice, qty_after_delivery,
                         "Stock must not change when creating an invoice after delivery")
