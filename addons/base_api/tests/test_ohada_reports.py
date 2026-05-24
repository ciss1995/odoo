# -*- coding: utf-8 -*-
"""OHADA report endpoints — HTML rendering + JSON payload."""

import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestOhadaReports(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    def _login(self):
        admin = self.env.ref('base.user_admin')
        token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        token_hash = self.env['api.session']._hash_token(token)
        self.env['api.session'].sudo().create({
            'user_id': admin.id, 'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return token

    def _get(self, path, token=None, **params):
        qs = '&'.join(f'{k}={v}' for k, v in params.items() if v is not None)
        headers = {'session-token': token} if token else {}
        return self.url_open(f'{path}?{qs}', headers=headers)

    def test_bilan_requires_auth(self):
        resp = self.url_open('/api/v2/accounting/reports/bilan-sn')
        self.assertEqual(resp.status_code, 401)

    def test_bilan_html(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/reports/bilan-sn', token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/html', resp.headers.get('Content-Type', ''))
        self.assertIn('Bilan', resp.text)
        self.assertIn('ACTIF', resp.text)
        self.assertIn('PASSIF', resp.text)

    def test_bilan_json(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/reports/bilan-sn', token, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type', '').split(';')[0], 'application/json')
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertIn('actif', body['data'])
        self.assertIn('passif', body['data'])
        self.assertIn('totals', body['data'])

    def test_compte_resultat_html(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/reports/compte-resultat-sn', token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Compte de Résultat', resp.text)
        self.assertIn('Produits', resp.text)
        self.assertIn('Charges', resp.text)

    def test_balance_generale_html(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/reports/balance-generale', token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Balance', resp.text)

    def test_grand_livre_html(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/reports/grand-livre', token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Grand livre', resp.text)
