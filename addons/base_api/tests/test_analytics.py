# -*- coding: utf-8 -*-
import json
import secrets
import string
from datetime import datetime, timedelta
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestAnalyticsEndpoints(HttpCase):
    """Integration tests for the /api/v2/analytics/* endpoints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base_url = '/api/v2'
        # Ensure admin password is stored with current hash algorithm to avoid
        # write attempts during HTTP authentication in test mode.
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

    def _login(self, login='admin', password='admin'):
        """Create a valid API session token for test HTTP calls."""
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
        session_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
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

    def _get(self, path, token, params=None):
        """GET an analytics endpoint with auth header."""
        qs = ''
        if params:
            qs = '&'.join(f'{k}={v}' for k, v in params.items())
            qs = '?' + qs
        resp = self.url_open(
            f'{self.api_base_url}{path}{qs}',
            headers={'session-token': token},
        )
        return resp.json()

    def _get_with_headers(self, path, headers, params=None):
        """GET endpoint with explicit headers."""
        qs = ''
        if params:
            qs = '&'.join(f'{k}={v}' for k, v in params.items())
            qs = '?' + qs
        resp = self.url_open(f'{self.api_base_url}{path}{qs}', headers=headers)
        return resp.json()

    def _generate_api_key(self, token):
        """Generate an API key for current user via session auth."""
        me = self._get_with_headers('/auth/me', {'session-token': token})
        self.assertTrue(me.get('success'))
        user_id = me['data']['user']['id']

        resp = self.url_open(
            f'{self.api_base_url}/users/{user_id}/api-key',
            data=json.dumps({}),
            headers={
                'Content-Type': 'application/json',
                'session-token': token,
            },
        )
        body = resp.json()
        self.assertTrue(body.get('success'), f"API key generation failed: {body}")
        return body['data']['api_key']

    def _assert_kpi_shape(self, kpi, allow_null_delta=False):
        """Validate that a KPI dict has the expected keys."""
        self.assertIn('current', kpi)
        if allow_null_delta:
            return
        for key in ('previous', 'delta', 'delta_percent'):
            self.assertIn(key, kpi)

    def _assert_analytics_shape(self, data, expected_kpis=None):
        """Validate the standard analytics response shape."""
        self.assertIn('kpis', data)
        self.assertIn('meta', data)
        meta = data['meta']
        self.assertIn('generated_at', meta)
        self.assertIn('period', meta)
        self.assertIn('period_label', meta)
        self.assertIn('timezone', meta)

        if 'breakdowns' in data:
            self.assertIsInstance(data['breakdowns'], dict)
        if 'chart' in data:
            chart = data['chart']
            self.assertIn('labels', chart)
            self.assertIn('series', chart)
            self.assertIsInstance(chart['labels'], list)
            self.assertIsInstance(chart['series'], list)
        if 'alerts' in data:
            self.assertIsInstance(data['alerts'], list)

        if expected_kpis:
            for kpi_name in expected_kpis:
                self.assertIn(kpi_name, data['kpis'], f"Missing KPI: {kpi_name}")

    # --- Dashboard Summary ---

    def test_dashboard_summary_success(self):
        token = self._login()
        body = self._get('/analytics/dashboard/summary', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        self.assertTrue(body.get('success'))
        data = body['data']
        self.assertIn('kpis', data)
        self.assertIn('accessible_modules', data)
        self.assertIsInstance(data['accessible_modules'], list)
        self.assertIn('alerts', data)
        self.assertIn('meta', data)

    def test_dashboard_summary_default_dates(self):
        token = self._login()
        body = self._get('/analytics/dashboard/summary', token)
        self.assertTrue(body.get('success'))

    # --- CRM Overview ---

    def test_crm_overview_success(self):
        token = self._login()
        body = self._get('/analytics/crm/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        self.assertTrue(body.get('success'))
        self._assert_analytics_shape(
            body['data'],
            expected_kpis=['total_leads', 'expected_revenue', 'won', 'win_rate'],
        )
        self.assertIn('by_stage', body['data']['breakdowns'])

    def test_crm_overview_with_team_filter(self):
        token = self._login()
        body = self._get('/analytics/crm/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31', 'team_id': '1',
        })
        self.assertTrue(body.get('success'))

    # --- Sales Overview ---

    def test_sales_overview_success(self):
        token = self._login()
        body = self._get('/analytics/sales/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        self.assertTrue(body.get('success'))
        self._assert_analytics_shape(
            body['data'],
            expected_kpis=['total_orders', 'total_revenue', 'avg_order_value',
                           'confirmed_orders', 'draft_quotations'],
        )
        self.assertIn('by_state', body['data']['breakdowns'])

    # --- Invoicing Overview ---

    def test_invoicing_overview_success(self):
        token = self._login()
        body = self._get('/analytics/invoicing/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        self.assertTrue(body.get('success'))
        self._assert_analytics_shape(
            body['data'],
            expected_kpis=['total_invoices', 'total_amount', 'amount_paid', 'amount_due'],
        )
        self.assertIn('by_payment_state', body['data']['breakdowns'])

    # --- Inventory Overview ---

    def test_inventory_overview_success(self):
        token = self._login()
        body = self._get('/analytics/inventory/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        if body.get('success'):
            self._assert_analytics_shape(
                body['data'],
                expected_kpis=['total_transfers', 'done', 'waiting', 'late'],
            )
            self.assertIn('by_state', body['data']['breakdowns'])
        else:
            self.assertIn(body.get('error', {}).get('code', ''),
                          ['ACCESS_DENIED', 'MODULE_NOT_FOUND'],
                          "Unexpected error for inventory")

    # --- Purchases Overview ---

    def test_purchases_overview_success(self):
        token = self._login()
        body = self._get('/analytics/purchases/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        if body.get('success'):
            self._assert_analytics_shape(
                body['data'],
                expected_kpis=['total_orders', 'total_amount', 'draft', 'confirmed'],
            )
            self.assertIn('by_state', body['data']['breakdowns'])
        else:
            self.assertIn(body.get('error', {}).get('code', ''),
                          ['ACCESS_DENIED', 'MODULE_NOT_FOUND'],
                          "Unexpected error for purchases")

    # --- HR Overview ---

    def test_hr_overview_success(self):
        token = self._login()
        body = self._get('/analytics/hr/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        self.assertTrue(body.get('success'))
        self._assert_analytics_shape(
            body['data'],
            expected_kpis=['total_employees', 'new_hires', 'departments'],
        )
        self.assertIn('by_department', body['data']['breakdowns'])
        total_emp = body['data']['kpis']['total_employees']
        self.assertIsNone(total_emp['previous'])

    # --- Projects Overview ---

    def test_projects_overview_success(self):
        token = self._login()
        body = self._get('/analytics/projects/overview', token, {
            'from': '2025-01-01', 'to': '2026-12-31',
        })
        if body.get('success'):
            self._assert_analytics_shape(
                body['data'],
                expected_kpis=['total_tasks', 'closed', 'overdue'],
            )
            self.assertIn('by_stage', body['data']['breakdowns'])
            self.assertIn('by_project', body['data']['breakdowns'])
        else:
            self.assertIn(body.get('error', {}).get('code', ''),
                          ['ACCESS_DENIED', 'MODULE_NOT_FOUND'],
                          "Unexpected error for projects")

    # --- Auth & Edge Cases ---

    def test_analytics_requires_auth(self):
        resp = self.url_open(f'{self.api_base_url}/analytics/dashboard/summary')
        body = resp.json()
        self.assertFalse(body.get('success'))

    def test_analytics_rejects_api_key_auth(self):
        body = self._get_with_headers('/analytics/dashboard/summary', {'X-API-Key': 'dummy-test-key'})
        self.assertFalse(body.get('success'))
        self.assertEqual(body.get('error', {}).get('code'), 'MISSING_SESSION_TOKEN')

    def test_analytics_invalid_dates_fallback(self):
        token = self._login()
        body = self._get('/analytics/crm/overview', token, {
            'from': 'not-a-date', 'to': 'also-bad',
        })
        self.assertTrue(body.get('success'))

    def test_analytics_meta_period(self):
        token = self._login()
        body = self._get('/analytics/crm/overview', token, {
            'from': '2026-02-01', 'to': '2026-02-28',
        })
        self.assertTrue(body.get('success'))
        period = body['data']['meta']['period']
        self.assertEqual(period['from'], '2026-02-01')
        self.assertEqual(period['to'], '2026-02-28')
        self.assertIn('previous_from', period)
        self.assertIn('previous_to', period)

    def test_analytics_timezone_param(self):
        token = self._login()
        body = self._get('/analytics/sales/overview', token, {
            'from': '2026-01-01', 'to': '2026-03-31',
            'timezone': 'America/New_York',
        })
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['meta']['timezone'], 'America/New_York')

    def test_analytics_company_filter(self):
        token = self._login()
        body = self._get('/analytics/dashboard/summary', token, {
            'from': '2026-01-01', 'to': '2026-12-31',
            'company_id': '1',
        })
        self.assertTrue(body.get('success'))
