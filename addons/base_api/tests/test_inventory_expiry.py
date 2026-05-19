# -*- coding: utf-8 -*-
"""Tests for /api/v2/inventory/expiring-soon — the product-expiry alert
endpoint added in version 19.0.7.x.

The endpoint is intentionally defensive: when `product_expiry` is not
installed in the tenant (basic/mid plans), it returns `{ enabled: false,
lots: [] }` instead of erroring out. When the addon IS installed (full
plan), it returns the lots whose alert_date has been reached.
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestInventoryExpiringSoon(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2/inventory/expiring-soon'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    def _login(self, login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
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
        return token, user

    def test_endpoint_requires_authentication(self):
        resp = self.url_open(self.api_base)
        # Either 401 (no session) or a JSON error — never a 500 or crash.
        self.assertIn(resp.status_code, (401, 403))

    def test_endpoint_returns_disabled_when_expiry_module_not_installed(self):
        """Smoke-test the defensive path: if `product_expiry` isn't installed
        in this test database, the endpoint should answer cleanly with
        enabled=false and an empty list."""
        token, _ = self._login()
        resp = self.url_open(self.api_base, headers={'session-token': token})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
        data = body['data']
        # Either product_expiry is installed (data['enabled']=True with lots)
        # or it's not (False with empty list). Both are valid outcomes.
        self.assertIn('enabled', data)
        self.assertIn('lots', data)
        self.assertIsInstance(data['lots'], list)
        # When disabled, list must be empty
        if not data['enabled']:
            self.assertEqual(data['lots'], [])

    def test_endpoint_accepts_days_query_parameter(self):
        """The days= query parameter should be parsed without erroring even
        when product_expiry isn't installed."""
        token, _ = self._login()
        resp = self.url_open(f"{self.api_base}?days=30", headers={'session-token': token})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))

    def test_endpoint_accepts_invalid_days_without_crashing(self):
        token, _ = self._login()
        resp = self.url_open(f"{self.api_base}?days=not-a-number", headers={'session-token': token})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
