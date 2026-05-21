# -*- coding: utf-8 -*-
"""Integration tests for the /api/v2/ai/* endpoints.

Locks in that the AI agent gets the authoritative tenant facts it needs
to avoid hallucinating dates and currencies from training data.
"""

import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestAiContextEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/ai/context'
        cls.taxonomy_url = '/api/v2/ai/taxonomy'
        cls.me_url = '/api/v2/ai/me'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        # Disable subscription enforcer for tests (control plane not reachable).
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

    def _get(self, path, token):
        resp = self.url_open(path, headers={'session-token': token})
        return resp.json()

    # ── /api/v2/ai/context ────────────────────────────────────────────

    def test_context_returns_tenant_block(self):
        token = self._login()
        body = self._get(self.url, token)
        self.assertTrue(body.get('success'), body)
        tenant = body['data'].get('tenant')
        self.assertIsNotNone(tenant, "tenant block missing from /ai/context")

        # The whole point of the refactor: today must be present.
        self.assertIn('today', tenant)
        today_str = tenant['today']
        # Parses as ISO date.
        datetime.fromisoformat(today_str)

        self.assertIn('timezone', tenant)
        self.assertIn('locale', tenant)
        self.assertIn('company', tenant)
        self.assertIn('currency', tenant['company'])
        currency = tenant['company']['currency']
        # Symbol + code present so the LLM never has to default to "$".
        self.assertIn('code', currency)
        self.assertIn('symbol', currency)
        self.assertIn('position', currency)

    def test_context_today_matches_server_date(self):
        """Within 1 day to allow for tenant-TZ vs UTC differences."""
        token = self._login()
        body = self._get(self.url, token)
        tenant = body['data']['tenant']
        today_returned = datetime.fromisoformat(tenant['today']).date()
        delta = abs((today_returned - datetime.utcnow().date()).days)
        self.assertLessEqual(delta, 1)

    def test_context_includes_installed_modules(self):
        token = self._login()
        body = self._get(self.url, token)
        modules = body['data']['tenant'].get('installed_modules', [])
        self.assertIsInstance(modules, list)
        # 'base' is always installed
        self.assertIn('base', modules)

    def test_context_requires_authentication(self):
        body = self._get(self.url, 'invalid-token-123')
        self.assertFalse(body.get('success'))

    # ── /api/v2/ai/taxonomy ───────────────────────────────────────────

    def test_taxonomy_endpoint_returns_known_keys(self):
        token = self._login()
        body = self._get(self.taxonomy_url, token)
        self.assertTrue(body.get('success'), body)
        data = body['data']
        for key in ('crm_stages', 'crm_teams', 'account_journals',
                    'hr_departments', 'product_categories'):
            self.assertIn(key, data, f"missing key: {key}")
            self.assertIsInstance(data[key], list)

    def test_taxonomy_items_have_id_and_name(self):
        """Every returned entity must carry an id — the LLM uses it as a filter value."""
        token = self._login()
        body = self._get(self.taxonomy_url, token)
        for key, items in body['data'].items():
            for item in items:
                self.assertIn('id', item, f"{key} item missing id: {item}")
                # Some lists (e.g. crm_stages) have a name field;
                # we just check id is always present.

    # ── /api/v2/ai/me ─────────────────────────────────────────────────

    def test_me_endpoint_returns_user_id(self):
        token = self._login()
        body = self._get(self.me_url, token)
        self.assertTrue(body.get('success'), body)
        self.assertIn('user_id', body['data'])
        self.assertIn('employee', body['data'])
        self.assertIn('manager_chain', body['data'])
        self.assertIn('crm_teams', body['data'])
