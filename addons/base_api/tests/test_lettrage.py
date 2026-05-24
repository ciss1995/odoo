# -*- coding: utf-8 -*-
"""HTTP integration tests for /api/v2/accounting/lettrage/* endpoints.

Skipped when `l10n_toomde_ohada_overlay` is not installed — the overlay
owns the lettrage_code field and the account-scoped denormalization.
The endpoints themselves are graceful on non-overlay tenants (return
None / fall back to a join via reconciled_line_ids), but our test
assertions specifically check the OHADA semantics.
"""

import json
import secrets
import string
from datetime import date, datetime, timedelta

from odoo.tests.common import HttpCase, tagged


def _ensure_recon_account(env, code='411API', name='Test Recv API'):
    Account = env['account.account'].sudo()
    acc = Account.search([('code', '=', code)], limit=1)
    if not acc:
        acc = Account.create({
            'code': code, 'name': name,
            'account_type': 'asset_receivable', 'reconcile': True,
        })
    return acc


def _ensure_misc_journal(env):
    J = env['account.journal'].sudo()
    j = J.search([('type', '=', 'general')], limit=1)
    if not j:
        j = J.create({'name': 'Test OD', 'code': 'TODA', 'type': 'general'})
    return j


def _make_balanced_move(env, account, journal, partner, debit, credit, ref):
    counter = journal.default_account_id
    if not counter:
        Account = env['account.account'].sudo()
        counter = Account.search([('account_type', '=', 'asset_current')], limit=1)
        if not counter:
            counter = Account.create({
                'code': '512API', 'name': 'Test Bank API',
                'account_type': 'asset_current', 'reconcile': False,
            })
    Move = env['account.move'].sudo()
    if debit:
        lines = [
            (0, 0, {'account_id': account.id, 'partner_id': partner.id,
                    'name': ref, 'debit': debit, 'credit': 0}),
            (0, 0, {'account_id': counter.id, 'partner_id': partner.id,
                    'name': ref + ' (cp)', 'debit': 0, 'credit': debit}),
        ]
    else:
        lines = [
            (0, 0, {'account_id': account.id, 'partner_id': partner.id,
                    'name': ref, 'debit': 0, 'credit': credit}),
            (0, 0, {'account_id': counter.id, 'partner_id': partner.id,
                    'name': ref + ' (cp)', 'debit': credit, 'credit': 0}),
        ]
    m = Move.create({'journal_id': journal.id, 'date': date.today(),
                     'ref': ref, 'line_ids': lines})
    m.action_post()
    return m


@tagged('post_install', '-at_install')
class TestLettrageApi(HttpCase):

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

    def _post(self, path, token, body):
        return self.url_open(
            path, data=json.dumps(body),
            headers={'session-token': token, 'Content-Type': 'application/json'},
        )

    def setUp(self):
        super().setUp()
        if 'lettrage_code' not in self.env['account.full.reconcile']._fields:
            self.skipTest("l10n_toomde_ohada_overlay not installed; "
                          "lettrage_code field missing")
        env = self.env
        self.partner = env['res.partner'].sudo().create({'name': 'LET API Partner'})
        self.account = _ensure_recon_account(env)
        self.journal = _ensure_misc_journal(env)
        self.m_inv = _make_balanced_move(
            env, self.account, self.journal, self.partner, 500, 0, 'INV-API-1')
        self.m_pay = _make_balanced_move(
            env, self.account, self.journal, self.partner, 0, 500, 'PAY-API-1')

    def _account_lines(self):
        return (self.m_inv.line_ids + self.m_pay.line_ids).filtered(
            lambda l: l.account_id == self.account)

    # --- auth ---------------------------------------------------------------

    def test_lines_requires_auth(self):
        resp = self.url_open(
            f'/api/v2/accounting/lettrage/lines?account_id={self.account.id}')
        self.assertEqual(resp.status_code, 401)

    # --- lines listing ------------------------------------------------------

    def test_lines_unmatched_listing(self):
        token = self._login()
        resp = self._get('/api/v2/accounting/lettrage/lines', token,
                         account_id=self.account.id)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['success'])
        data = body['data']
        self.assertEqual(data['account']['id'], self.account.id)
        ids = {l['id'] for l in data['lines']}
        expected = {l.id for l in self._account_lines()}
        self.assertTrue(expected.issubset(ids))
        self.assertEqual(data['totals']['debit'], 500)
        self.assertEqual(data['totals']['credit'], 500)
        self.assertEqual(data['totals']['balance'], 0)

    def test_lines_account_id_required(self):
        token = self._login()
        resp = self.url_open(
            '/api/v2/accounting/lettrage/lines',
            headers={'session-token': token})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    # --- reconcile / unreconcile / groups -----------------------------------

    def test_reconcile_then_groups_then_unreconcile(self):
        token = self._login()
        line_ids = self._account_lines().ids
        resp = self._post('/api/v2/accounting/lettrage/reconcile', token,
                          {'line_ids': line_ids})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body['success'])
        full_id = body['data']['full_reconcile_id']
        self.assertTrue(full_id)
        self.assertEqual(body['data']['lettrage_code'], 'A')

        resp = self._get('/api/v2/accounting/lettrage/groups', token,
                         account_id=self.account.id)
        self.assertEqual(resp.status_code, 200)
        codes = [g['lettrage_code'] for g in resp.json()['data']['groups']]
        self.assertIn('A', codes)

        resp = self._post('/api/v2/accounting/lettrage/unreconcile', token,
                          {'full_reconcile_id': full_id})
        self.assertEqual(resp.status_code, 200, resp.text)
        lines = self.env['account.move.line'].browse(line_ids)
        self.assertFalse(any(l.reconciled for l in lines))

    def test_reconcile_rejects_single_line(self):
        token = self._login()
        resp = self._post('/api/v2/accounting/lettrage/reconcile', token,
                          {'line_ids': [self.m_inv.line_ids[:1].id]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_reconcile_rejects_mixed_accounts(self):
        token = self._login()
        mixed_ids = [self.m_inv.line_ids[0].id, self.m_inv.line_ids[1].id]
        resp = self._post('/api/v2/accounting/lettrage/reconcile', token,
                          {'line_ids': mixed_ids})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'MIXED_ACCOUNTS')

    # --- suggest ------------------------------------------------------------

    def test_suggest_finds_exact_match(self):
        token = self._login()
        resp = self._post('/api/v2/accounting/lettrage/suggest', token,
                          {'account_id': self.account.id})
        self.assertEqual(resp.status_code, 200, resp.text)
        suggestions = resp.json()['data']['suggestions']
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]['amount'], 500)
        self.assertEqual(set(suggestions[0]['line_ids']),
                         set(self._account_lines().ids))
