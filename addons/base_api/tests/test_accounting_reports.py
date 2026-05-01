# -*- coding: utf-8 -*-
"""Integration tests for /api/v2/analytics/accounting/* report endpoints."""

import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestAccountingReports(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2/analytics/accounting'
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
        return token

    def _get(self, path, token=None, params=None):
        qs = ''
        if params:
            qs = '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        headers = {}
        if token:
            headers['session-token'] = token
        return self.url_open(f'{self.api_base}{path}{qs}', headers=headers)

    # ===== auth ===============================================================

    def test_journals_cards_requires_auth(self):
        resp = self._get('/journals/cards')
        self.assertEqual(resp.status_code, 401)

    def test_chart_of_accounts_requires_auth(self):
        resp = self._get('/chart-of-accounts')
        self.assertEqual(resp.status_code, 401)

    def test_balance_sheet_requires_auth(self):
        resp = self._get('/balance-sheet')
        self.assertEqual(resp.status_code, 401)

    def test_profit_and_loss_requires_auth(self):
        resp = self._get('/profit-and-loss')
        self.assertEqual(resp.status_code, 401)

    def test_cash_flow_requires_auth(self):
        resp = self._get('/cash-flow')
        self.assertEqual(resp.status_code, 401)

    # ===== happy paths ========================================================

    def test_journals_cards_returns_list(self):
        token = self._login()
        resp = self._get('/journals/cards', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn('data', body)
        self.assertIn('cards', body['data'])
        self.assertIsInstance(body['data']['cards'], list)
        if body['data']['cards']:
            card = body['data']['cards'][0]
            for key in ('id', 'name', 'type', 'balance', 'entries_count', 'to_validate', 'overdue'):
                self.assertIn(key, card, f"Missing key: {key}")

    def test_chart_of_accounts_returns_paginated_list(self):
        token = self._login()
        resp = self._get('/chart-of-accounts', token=token, params={'limit': 50})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        self.assertIn('accounts', body)
        self.assertIn('pagination', body)
        self.assertEqual(body['pagination']['limit'], 50)
        self.assertEqual(body['pagination']['offset'], 0)
        if body['accounts']:
            acc = body['accounts'][0]
            for key in ('id', 'code', 'name', 'type', 'allow_reconciliation', 'debit', 'credit', 'balance'):
                self.assertIn(key, acc, f"Missing key: {key}")

    def test_chart_of_accounts_filter_by_type(self):
        token = self._login()
        resp = self._get('/chart-of-accounts', token=token,
                         params={'account_type': 'asset_cash,asset_current', 'limit': 100})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        for acc in body['accounts']:
            self.assertIn(acc['type'], ('asset_cash', 'asset_current'))

    def test_balance_sheet_has_three_sections(self):
        token = self._login()
        resp = self._get('/balance-sheet', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        self.assertIn('sections', body)
        labels = [s['label'] for s in body['sections']]
        self.assertEqual(labels, ['Assets', 'Liabilities', 'Equity'])
        self.assertIn('totals', body)
        for key in ('assets', 'liabilities', 'equity', 'liabilities_and_equity'):
            self.assertIn(key, body['totals'])

    def test_profit_and_loss_has_income_and_expenses(self):
        token = self._login()
        resp = self._get('/profit-and-loss', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        labels = [s['label'] for s in body['sections']]
        self.assertEqual(labels, ['Income', 'Expenses'])
        for key in ('income', 'expenses', 'net_profit'):
            self.assertIn(key, body['totals'])

    def test_cash_flow_returns_totals(self):
        token = self._login()
        resp = self._get('/cash-flow', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        for key in ('opening_balance', 'inflows', 'outflows', 'net_change', 'closing_balance'):
            self.assertIn(key, body['totals'])
        self.assertIn('by_account', body)

    def test_period_param_respected(self):
        token = self._login()
        resp = self._get('/profit-and-loss', token=token,
                         params={'from': '2024-01-01', 'to': '2024-12-31'})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()['data']
        self.assertEqual(body['period']['from'], '2024-01-01')
        self.assertEqual(body['period']['to'], '2024-12-31')
