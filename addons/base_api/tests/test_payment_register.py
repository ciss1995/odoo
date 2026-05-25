# -*- coding: utf-8 -*-
"""Integration tests for POST /api/v2/account_move/<id>/register_payment.

The endpoint is the SPA's only entry point for recording payments
against invoices and vendor bills, so these tests pin the things that
are easy to regress as Odoo evolves:

- happy path: a payment is created + posted + reconciled with the move
  in one call (the move's ``payment_state`` flips to ``in_payment`` /
  ``paid`` and ``matched_payment_ids`` contains the new payment),
- partial payments: amount < residual leaves the move partially paid,
- rejection paths: missing record, unposted move, non-invoice move,
  negative amount, missing auth.
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentRegister(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        # Subscription enforcer would block these tests because the
        # control plane isn't reachable in the test runner; see the
        # mail endpoint suite for the same trick.
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls.vendor = cls.env['res.partner'].sudo().create({
            'name': 'Test Vendor',
            'supplier_rank': 1,
        })
        cls.product = cls.env['product.product'].sudo().create({
            'name': 'Payment Register Product',
            'list_price': 100.0,
            'type': 'consu',
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    # ----- helpers --------------------------------------------------------

    def _login(self, login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        token_hash = self.env['api.session']._hash_token(token)
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return token

    def _make_posted_bill(self, amount=100.0):
        """Posted vendor bill ready to receive payments."""
        bill = self.env['account.move'].sudo().create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': datetime.now().date().isoformat(),
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': amount,
            })],
        })
        bill.action_post()
        return bill

    def _post(self, path, token, body=None):
        return self.url_open(
            f'/api/v2{path}',
            data=json.dumps(body or {}),
            headers={
                'Content-Type': 'application/json',
                'session-token': token,
            },
        )

    # ----- success paths --------------------------------------------------

    def test_register_full_payment_reconciles_bill(self):
        token = self._login()
        bill = self._make_posted_bill(amount=100.0)
        self.assertIn(bill.payment_state, ('not_paid', 'in_payment'))

        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'amount': 100, 'date': datetime.now().date().isoformat(),
             'ref': 'Bank slip #42'},
        )
        self.assertEqual(r.status_code, 201, r.text)
        payload = r.json()
        self.assertTrue(payload.get('success'), payload)
        pay = payload['data']['payment']
        self.assertEqual(pay['state'], 'posted')
        self.assertEqual(pay['amount'], 100.0)
        self.assertEqual(pay['ref'], 'Bank slip #42')

        bill.invalidate_recordset()
        self.assertIn(bill.payment_state, ('paid', 'in_payment'))
        # The new payment must show up in the move's matched set.
        self.assertIn(pay['id'], bill.matched_payment_ids.ids)

    def test_register_partial_payment_leaves_residual(self):
        token = self._login()
        bill = self._make_posted_bill(amount=100.0)
        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'amount': 40, 'date': datetime.now().date().isoformat()},
        )
        self.assertEqual(r.status_code, 201, r.text)
        bill.invalidate_recordset()
        self.assertAlmostEqual(bill.amount_residual, 60.0, places=2)
        # Either "partial" or "in_payment" depending on Odoo version.
        self.assertIn(bill.payment_state, ('partial', 'in_payment'))

    def test_register_payment_defaults_to_residual_when_amount_absent(self):
        token = self._login()
        bill = self._make_posted_bill(amount=100.0)
        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'date': datetime.now().date().isoformat()},
        )
        self.assertEqual(r.status_code, 201, r.text)
        pay = r.json()['data']['payment']
        self.assertEqual(pay['amount'], 100.0)

    # ----- rejection paths ------------------------------------------------

    def test_register_payment_without_auth_rejected(self):
        bill = self._make_posted_bill()
        r = self.url_open(
            f'/api/v2/account_move/{bill.id}/register_payment',
            data=json.dumps({'amount': 100}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertIn(r.status_code, (401, 403))

    def test_register_payment_on_missing_move_returns_404(self):
        token = self._login()
        r = self._post(
            '/account_move/99999999/register_payment', token,
            {'amount': 100},
        )
        self.assertEqual(r.status_code, 404)

    def test_register_payment_on_draft_move_rejected(self):
        token = self._login()
        # Draft bill — not posted yet.
        bill = self.env['account.move'].sudo().create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': datetime.now().date().isoformat(),
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 50,
            })],
        })
        self.assertEqual(bill.state, 'draft')
        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'amount': 50},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'INVALID_MOVE_STATE')

    def test_register_payment_with_negative_amount_rejected(self):
        token = self._login()
        bill = self._make_posted_bill()
        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'amount': -1},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'INVALID_PARAMS')

    def test_register_payment_with_non_numeric_amount_rejected(self):
        token = self._login()
        bill = self._make_posted_bill()
        r = self._post(
            f'/account_move/{bill.id}/register_payment', token,
            {'amount': 'twelve'},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'INVALID_PARAMS')

    def test_register_payment_on_journal_entry_rejected(self):
        token = self._login()
        # A miscellaneous journal entry is an account.move with
        # move_type='entry' — must not accept a payment registration.
        misc_journal = self.env['account.journal'].sudo().search(
            [('type', '=', 'general')], limit=1,
        )
        entry = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': misc_journal.id,
            'date': datetime.now().date().isoformat(),
        })
        # Note: state may be draft; we don't post — the move_type
        # check should fire first.
        r = self._post(
            f'/account_move/{entry.id}/register_payment', token,
            {'amount': 10},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn(
            r.json()['error']['code'],
            ('INVALID_MOVE_TYPE', 'INVALID_MOVE_STATE'),
        )
