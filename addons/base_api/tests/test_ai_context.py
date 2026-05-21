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


@tagged('post_install', '-at_install')
class TestAiContextAuthIsolation(HttpCase):
    """Isolation tests — a restricted user (only base.group_user, no module
    groups) must NOT receive taxonomy data for modules they can't access.

    Stock Odoo lets every internal user read crm.stage, account.journal,
    hr.department, etc. via ORM ACLs. Without explicit gating in the
    controller, the AI taxonomy endpoint would leak journal/department/
    stage names to users who have no business seeing them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.taxonomy_url = '/api/v2/ai/taxonomy'
        cls.me_url = '/api/v2/ai/me'

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        # Restricted user: internal user with NO module-specific groups.
        Users = cls.env['res.users'].sudo()
        cls.restricted_user = Users.create({
            'name': 'Restricted AI Test User',
            'login': 'restricted_ai_test',
            'password': 'restricted_pw',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    def _login(self, user):
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
        return self.url_open(path, headers={'session-token': token}).json()

    def test_restricted_user_does_not_leak_account_journals(self):
        token = self._login(self.restricted_user)
        body = self._get(self.taxonomy_url, token)
        self.assertTrue(body.get('success'), body)
        journals = body['data'].get('account_journals', [])
        self.assertEqual(
            journals, [],
            f"User without accounting access leaked account_journals: {journals}",
        )

    def test_restricted_user_does_not_leak_hr_departments(self):
        token = self._login(self.restricted_user)
        body = self._get(self.taxonomy_url, token)
        depts = body['data'].get('hr_departments', [])
        self.assertEqual(
            depts, [],
            f"User without HR access leaked hr_departments: {depts}",
        )

    def test_restricted_user_does_not_leak_crm_stages(self):
        token = self._login(self.restricted_user)
        body = self._get(self.taxonomy_url, token)
        stages = body['data'].get('crm_stages', [])
        teams = body['data'].get('crm_teams', [])
        self.assertEqual(
            stages, [],
            f"User without CRM access leaked crm_stages: {stages}",
        )
        self.assertEqual(
            teams, [],
            f"User without CRM access leaked crm_teams: {teams}",
        )

    def test_restricted_user_does_not_leak_project_stages(self):
        token = self._login(self.restricted_user)
        body = self._get(self.taxonomy_url, token)
        stages = body['data'].get('project_stages', [])
        self.assertEqual(
            stages, [],
            f"User without Project access leaked project_stages: {stages}",
        )

    def test_taxonomy_response_shape_preserved_for_restricted_user(self):
        """API shape must stay stable: keys present, just empty arrays."""
        token = self._login(self.restricted_user)
        body = self._get(self.taxonomy_url, token)
        data = body['data']
        for key in ('crm_stages', 'crm_teams', 'account_journals',
                    'hr_departments', 'product_categories', 'project_stages'):
            self.assertIn(key, data, f"missing key for restricted user: {key}")
            self.assertIsInstance(data[key], list)

    def test_admin_still_sees_full_taxonomy(self):
        """Sanity check: gating must not block admin.

        crm.stage records ship as data (not demo) in the crm module, which
        is a base_api dependency, so admin always sees at least the default
        CRM stages on a fresh install — independent of demo data.
        """
        admin = self.env['res.users'].sudo().search([('login', '=', 'admin')], limit=1)
        token = self._login(admin)
        body = self._get(self.taxonomy_url, token)
        self.assertTrue(body.get('success'), body)
        self.assertGreater(
            len(body['data'].get('crm_stages', [])), 0,
            "Admin got empty crm_stages — gating broke the happy path",
        )
