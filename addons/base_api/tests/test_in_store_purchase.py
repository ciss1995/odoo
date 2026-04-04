# -*- coding: utf-8 -*-
import json
import secrets
import string
from datetime import datetime, timedelta
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestInStorePurchase(HttpCase):
    """Integration tests for POST /api/v2/sales/in-store-purchase."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_url = '/api/v2/sales/in-store-purchase'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        cls.partner = cls.env['res.partner'].create({
            'name': 'Walk-In Customer',
            'email': 'walkin@example.com',
        })

        # Storable product with invoice_policy = order (so we can invoice
        # right away without depending on delivery qty computation).
        cls.product_a = cls.env['product.product'].create({
            'name': 'In-Store Product A',
            'list_price': 50.0,
            'standard_price': 25.0,
            'type': 'consu',
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.product_b = cls.env['product.product'].create({
            'name': 'In-Store Product B',
            'list_price': 120.0,
            'standard_price': 60.0,
            'type': 'consu',
            'is_storable': True,
            'invoice_policy': 'order',
        })

        # Pre-load stock so delivery can be fulfilled
        warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        stock_loc = warehouse.lot_stock_id
        cls.env['stock.quant'].with_context(inventory_mode=True).create([
            {
                'product_id': cls.product_a.id,
                'inventory_quantity': 100,
                'location_id': stock_loc.id,
            },
            {
                'product_id': cls.product_b.id,
                'inventory_quantity': 100,
                'location_id': stock_loc.id,
            },
        ]).action_apply_inventory()

        cls.stock_location = stock_loc

        # Cash journal for payments
        cls.cash_journal = cls.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', cls.env.company.id),
        ], limit=1)

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

    def _post(self, data, token=None, headers=None):
        hdrs = {
            'Content-Type': 'application/json',
        }
        if token:
            hdrs['session-token'] = token
        if headers:
            hdrs.update(headers)
        resp = self.url_open(
            self.api_url,
            data=json.dumps(data),
            headers=hdrs,
        )
        return resp.json()

    def _quant_qty(self, product):
        return sum(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.stock_location.id),
            ]).mapped('quantity')
        )

    # ------------------------------------------------------------------
    # Success cases
    # ------------------------------------------------------------------

    def test_full_flow_single_product(self):
        """Single product in-store purchase completes the full lifecycle."""
        token = self._login()
        qty_before = self._quant_qty(self.product_a)

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 3},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        data = body['data']

        # Sale order confirmed
        self.assertEqual(data['sale_order']['state'], 'sale')

        # Invoice posted and paid
        self.assertEqual(data['invoice']['state'], 'posted')
        self.assertEqual(data['invoice']['payment_state'], 'paid')
        # 3 units * 50.0 = 150.0 untaxed (total may include taxes)
        self.assertAlmostEqual(data['invoice']['amount_untaxed'], 150.0, places=2)
        self.assertGreaterEqual(data['invoice']['amount_total'],
                                data['invoice']['amount_untaxed'])

        # Payment created
        self.assertTrue(data['payment'])
        self.assertIn(data['payment']['state'], ('paid', 'in_process'))

        # Stock moves are done
        self.assertTrue(data['stock_moves'])
        for move in data['stock_moves']:
            self.assertEqual(move['state'], 'done')

        # Pickings are done
        self.assertTrue(data['pickings'])
        for pick in data['pickings']:
            self.assertEqual(pick['state'], 'done')

        # Inventory actually decreased
        qty_after = self._quant_qty(self.product_a)
        self.assertEqual(qty_after, qty_before - 3,
                         "Stock should decrease by ordered quantity")

    def test_full_flow_multiple_products(self):
        """Multiple order lines are handled correctly."""
        token = self._login()
        qty_a_before = self._quant_qty(self.product_a)
        qty_b_before = self._quant_qty(self.product_b)

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 2, 'price_unit': 50.0},
                {'product_id': self.product_b.id, 'quantity': 1, 'price_unit': 120.0},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        data = body['data']

        # Total = 2*50 + 1*120 = 220
        self.assertAlmostEqual(data['invoice']['amount_untaxed'], 220.0, places=2)
        self.assertEqual(data['invoice']['payment_state'], 'paid')

        # Both stock moves done
        self.assertEqual(len(data['stock_moves']), 2)
        for move in data['stock_moves']:
            self.assertEqual(move['state'], 'done')

        # Both quantities decreased
        self.assertEqual(self._quant_qty(self.product_a), qty_a_before - 2)
        self.assertEqual(self._quant_qty(self.product_b), qty_b_before - 1)

    def test_custom_price_unit(self):
        """Custom price_unit in order lines overrides the product list price."""
        token = self._login()

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1, 'price_unit': 75.0},
            ],
        }, token)

        self.assertTrue(body.get('success'))
        self.assertAlmostEqual(body['data']['invoice']['amount_untaxed'], 75.0, places=2)

    def test_product_with_multi_step_route_succeeds(self):
        """Products with multi-step routes on them should still work because
        the endpoint clears product-level routes and uses ship_only."""
        token = self._login()

        # Create a 3-step warehouse and its delivery route
        multi_wh = self.env['stock.warehouse'].create({
            'name': 'Pick Pack Ship WH',
            'code': 'PPS',
            'delivery_steps': 'pick_pack_ship',
        })
        multi_route = multi_wh.delivery_route_id

        # Assign the multi-step route directly on the product
        self.product_a.write({
            'route_ids': [(4, multi_route.id)],
        })
        self.assertTrue(self.product_a.route_ids, "Product should have a route set")

        qty_before = self._quant_qty(self.product_a)

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 2},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        data = body['data']

        self.assertEqual(data['invoice']['payment_state'], 'paid')
        for move in data['stock_moves']:
            self.assertEqual(move['state'], 'done')

        qty_after = self._quant_qty(self.product_a)
        self.assertEqual(qty_after, qty_before - 2)

    def test_product_category_with_route_succeeds(self):
        """Products whose CATEGORY has multi-step routes should still work.
        This reproduces the exact 'inter-warehouse transit' error the UI hit."""
        token = self._login()

        multi_wh = self.env['stock.warehouse'].create({
            'name': 'Category Route WH',
            'code': 'CRW',
            'delivery_steps': 'pick_pack_ship',
        })
        multi_route = multi_wh.delivery_route_id

        # Create a category with the multi-step route
        categ = self.env['product.category'].create({
            'name': 'Routed Category',
            'route_ids': [(4, multi_route.id)],
        })

        # Assign the product to this category (no routes on product itself)
        self.product_b.write({
            'categ_id': categ.id,
            'route_ids': [(5, 0, 0)],
        })
        self.assertFalse(self.product_b.route_ids)
        self.assertTrue(self.product_b.categ_id.total_route_ids)

        qty_before = self._quant_qty(self.product_b)

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_b.id, 'quantity': 1},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        self.assertEqual(body['data']['invoice']['payment_state'], 'paid')

        qty_after = self._quant_qty(self.product_b)
        self.assertEqual(qty_after, qty_before - 1)

    def test_with_journal_id(self):
        """Explicit journal_id is accepted for payment."""
        token = self._login()
        if not self.cash_journal:
            return  # skip if no cash journal configured

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
            'journal_id': self.cash_journal.id,
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        self.assertEqual(body['data']['invoice']['payment_state'], 'paid')

    def test_response_structure(self):
        """Response contains all expected top-level keys and sub-fields."""
        token = self._login()

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertTrue(body.get('success'))
        data = body['data']

        # Top-level keys
        for key in ('sale_order', 'invoice', 'payment', 'pickings', 'stock_moves'):
            self.assertIn(key, data, f"Missing top-level key: {key}")

        # Sale order fields
        for f in ('id', 'name', 'state', 'amount_total'):
            self.assertIn(f, data['sale_order'], f"Missing sale_order field: {f}")

        # Invoice fields
        for f in ('id', 'name', 'state', 'payment_state', 'amount_total', 'amount_residual'):
            self.assertIn(f, data['invoice'], f"Missing invoice field: {f}")

        # Payment fields
        for f in ('id', 'name', 'state', 'amount'):
            self.assertIn(f, data['payment'], f"Missing payment field: {f}")

    def test_invoice_amount_residual_is_zero(self):
        """After payment, the invoice residual should be zero."""
        token = self._login()

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 5, 'price_unit': 40.0},
            ],
        }, token)

        self.assertTrue(body.get('success'))
        self.assertAlmostEqual(body['data']['invoice']['amount_residual'], 0.0, places=2)

    # ------------------------------------------------------------------
    # Auth tests
    # ------------------------------------------------------------------

    def test_requires_session_token(self):
        """Request without any auth header is rejected."""
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        })

        self.assertFalse(body.get('success'))

    def test_expired_session_rejected(self):
        """Expired session token is rejected."""
        user = self.env['res.users'].sudo().search([('login', '=', 'admin')], limit=1)
        raw_token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        token_hash = self.env['api.session']._hash_token(raw_token)
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': token_hash,
            'expires_at': datetime.now() - timedelta(hours=1),  # already expired
            'created_at': datetime.now() - timedelta(hours=25),
            'last_activity': datetime.now() - timedelta(hours=2),
            'active': True,
        })

        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, raw_token)

        self.assertFalse(body.get('success'))

    def test_invalid_session_token_rejected(self):
        """A random invalid token is rejected."""
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, 'totally-invalid-token-that-does-not-exist')

        self.assertFalse(body.get('success'))

    # ------------------------------------------------------------------
    # Validation / error cases
    # ------------------------------------------------------------------

    def test_no_partner_defaults_to_walkin_customer(self):
        """Omitting partner_id creates a 'Walk-In Store Customer' and succeeds."""
        token = self._login()
        body = self._post({
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        self.assertIn('Walk-In Store Customer',
                       body['data']['sale_order']['partner_id'][1])

    def test_company_partner_defaults_to_walkin_customer(self):
        """Passing the company's own partner_id uses walk-in customer instead."""
        token = self._login()
        company_partner_id = self.env.company.partner_id.id

        body = self._post({
            'partner_id': company_partner_id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertTrue(body.get('success'), f"Expected success: {body}")
        self.assertIn('Walk-In Store Customer',
                       body['data']['sale_order']['partner_id'][1])

    def test_missing_order_lines(self):
        """Missing order_lines returns 400."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_PARAM')

    def test_empty_order_lines(self):
        """Empty order_lines list returns 400."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_PARAM')

    def test_invalid_partner(self):
        """Non-existent partner returns 404."""
        token = self._login()
        body = self._post({
            'partner_id': 999999,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_invalid_product(self):
        """Non-existent product returns 404."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': 999999, 'quantity': 1},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_missing_product_id_in_line(self):
        """Order line without product_id returns 400."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'quantity': 2},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_PARAM')

    def test_zero_quantity_rejected(self):
        """Quantity of 0 is rejected."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 0},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_PARAM')

    def test_negative_quantity_rejected(self):
        """Negative quantity is rejected."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': -3},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_PARAM')

    def test_multi_step_warehouse_rejected(self):
        """Explicitly passing a multi-step warehouse is rejected."""
        token = self._login()
        multi_wh = self.env['stock.warehouse'].create({
            'name': 'Multi-Step WH',
            'code': 'MSW',
            'delivery_steps': 'pick_pack_ship',
        })
        body = self._post({
            'partner_id': self.partner.id,
            'warehouse_id': multi_wh.id,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_WAREHOUSE')

    def test_invalid_warehouse_not_found(self):
        """Non-existent warehouse_id returns 404."""
        token = self._login()
        body = self._post({
            'partner_id': self.partner.id,
            'warehouse_id': 999999,
            'order_lines': [
                {'product_id': self.product_a.id, 'quantity': 1},
            ],
        }, token)

        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_invalid_content_type(self):
        """Non-JSON content type is rejected."""
        token = self._login()
        resp = self.url_open(
            self.api_url,
            data='not json',
            headers={
                'Content-Type': 'text/plain',
                'session-token': token,
            },
        )
        body = resp.json()
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_CONTENT_TYPE')
