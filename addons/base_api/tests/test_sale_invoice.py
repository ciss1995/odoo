# -*- coding: utf-8 -*-
import json
import secrets
import string
from datetime import datetime, timedelta
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSaleCreateInvoice(HttpCase):
    """Integration tests for POST /api/v2/sales/<id>/create-invoice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base_url = '/api/v2'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        # Create a partner for orders / invoices
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Invoice Partner',
            'email': 'testinv@example.com',
        })

        # Create a storable product with an invoice policy of "ordered"
        cls.product = cls.env['product.product'].create({
            'name': 'Test Invoice Product',
            'list_price': 100.0,
            'type': 'consu',
            'invoice_policy': 'order',
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _login(self, login='admin', password='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
        session_token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        token_hash = self.env['api.session']._hash_token(session_token)
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return session_token

    def _post(self, path, token, data=None):
        resp = self.url_open(
            f'{self.api_base_url}{path}',
            data=json.dumps(data or {}),
            headers={
                'Content-Type': 'application/json',
                'session-token': token,
            },
        )
        return resp.json()

    def _create_confirmed_so(self):
        """Create and confirm a sale order, return the record."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 3,
                'price_unit': 100.0,
            })],
        })
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        return order

    # ------------------------------------------------------------------
    # Success cases
    # ------------------------------------------------------------------

    def test_create_invoice_delivered_default(self):
        """POST with no body uses 'delivered' method and creates an invoice."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(f'/sales/{order.id}/create-invoice', token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        inv = body['data']['invoice']
        self.assertIn('id', inv)
        self.assertEqual(inv['move_type'], 'out_invoice')
        self.assertEqual(inv['state'], 'draft')
        self.assertGreater(inv['amount_total'], 0)
        self.assertEqual(inv['invoice_origin'], order.name)

    def test_create_invoice_explicit_delivered(self):
        """Explicitly passing advance_payment_method=delivered works."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(
            f'/sales/{order.id}/create-invoice', token,
            data={'advance_payment_method': 'delivered'},
        )

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        self.assertIn('invoice', body['data'])

    def test_create_invoice_with_dates(self):
        """Optional invoice_date and invoice_date_due are applied."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(
            f'/sales/{order.id}/create-invoice', token,
            data={
                'invoice_date': '2026-04-15',
                'invoice_date_due': '2026-05-15',
            },
        )

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        inv = body['data']['invoice']
        self.assertEqual(inv['invoice_date'], '2026-04-15')
        self.assertEqual(inv['invoice_date_due'], '2026-05-15')

    def test_invoice_amounts_match_order(self):
        """Invoice untaxed amount should match order line subtotal."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(f'/sales/{order.id}/create-invoice', token)
        self.assertTrue(body.get('success'))

        inv = body['data']['invoice']
        # 3 units * 100 = 300 (before tax)
        self.assertAlmostEqual(inv['amount_untaxed'], 300.0, places=2)
        # total >= untaxed (taxes may or may not apply depending on config)
        self.assertGreaterEqual(inv['amount_total'], inv['amount_untaxed'])

    def test_invoice_response_fields(self):
        """Response contains all expected invoice fields."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(f'/sales/{order.id}/create-invoice', token)
        self.assertTrue(body.get('success'))
        inv = body['data']['invoice']

        expected_fields = [
            'id', 'name', 'state', 'move_type', 'partner_id',
            'invoice_date', 'invoice_date_due', 'amount_untaxed',
            'amount_tax', 'amount_total', 'amount_residual',
            'payment_state', 'currency_id', 'invoice_origin',
            'invoice_line_ids',
        ]
        for field in expected_fields:
            self.assertIn(field, inv, f"Missing field: {field}")

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_requires_auth(self):
        """Request without auth is rejected."""
        order = self._create_confirmed_so()
        resp = self.url_open(
            f'{self.api_base_url}/sales/{order.id}/create-invoice',
            data=json.dumps({}),
            headers={'Content-Type': 'application/json'},
        )
        body = resp.json()
        self.assertFalse(body.get('success'))

    def test_order_not_found(self):
        """Non-existent order returns 404."""
        token = self._login()
        body = self._post('/sales/999999/create-invoice', token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_draft_order_rejected(self):
        """Draft (unconfirmed) orders cannot be invoiced."""
        token = self._login()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 50.0,
            })],
        })
        self.assertEqual(order.state, 'draft')

        body = self._post(f'/sales/{order.id}/create-invoice', token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_STATE')

    def test_invalid_method_rejected(self):
        """Invalid advance_payment_method returns 400."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(
            f'/sales/{order.id}/create-invoice', token,
            data={'advance_payment_method': 'bogus'},
        )

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_PARAM')

    def test_percentage_without_amount_rejected(self):
        """percentage method without amount returns 400."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(
            f'/sales/{order.id}/create-invoice', token,
            data={'advance_payment_method': 'percentage'},
        )

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_PARAM')

    def test_fixed_without_amount_rejected(self):
        """fixed method without amount returns 400."""
        token = self._login()
        order = self._create_confirmed_so()

        body = self._post(
            f'/sales/{order.id}/create-invoice', token,
            data={'advance_payment_method': 'fixed'},
        )

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_PARAM')

    def test_already_fully_invoiced(self):
        """Invoicing an already-fully-invoiced order returns an error."""
        token = self._login()
        order = self._create_confirmed_so()

        # First invoice succeeds
        body1 = self._post(f'/sales/{order.id}/create-invoice', token)
        self.assertTrue(body1.get('success'), f"First invoice failed: {body1}")

        # Second attempt — nothing left to invoice
        body2 = self._post(f'/sales/{order.id}/create-invoice', token)
        self.assertFalse(body2.get('success'))
        self.assertEqual(body2['error']['code'], 'NOTHING_TO_INVOICE')
