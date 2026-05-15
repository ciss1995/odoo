# -*- coding: utf-8 -*-
"""Integration tests for /api/v2/internal/import/partners.

Covers:
 - dual auth (internal token + session token, plus rejection of neither)
 - dry-run rolls back; commit persists
 - idempotency via ext_id (re-run updates rather than duplicating)
 - per-row validation: missing ext_id, missing name, unknown country, bad
   partner_type, fields outside the allow-list are stripped
 - partner_type → customer_rank/supplier_rank convenience mapping
"""

import json
import os
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestImportPartnersEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/internal/import/partners'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        # Subscription enforcer hits the control plane — disable for tests.
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        # Pin the internal token. The endpoint reads CONTROL_PLANE_TOKEN
        # at request time, so setting it here is enough for the test class.
        cls._orig_cp_token = os.environ.get('CONTROL_PLANE_TOKEN')
        os.environ['CONTROL_PLANE_TOKEN'] = 'test-internal-token'

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        if cls._orig_cp_token is None:
            os.environ.pop('CONTROL_PLANE_TOKEN', None)
        else:
            os.environ['CONTROL_PLANE_TOKEN'] = cls._orig_cp_token
        super().tearDownClass()

    # ----- helpers ----------------------------------------------------------

    def _session_token(self, login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
        token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': self.env['api.session']._hash_token(token),
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return token

    def _post(self, payload, *, internal_token=None, session_token=None):
        headers = {'Content-Type': 'application/json'}
        if internal_token:
            headers['Authorization'] = f'Bearer {internal_token}'
        if session_token:
            headers['session-token'] = session_token
        return self.url_open(self.url, data=json.dumps(payload), headers=headers)

    def _row(self, ext_id, name, **extra):
        row = {'ext_id': ext_id, 'name': name}
        row.update(extra)
        return row

    def _body(self, resp):
        return json.loads(resp.content.decode('utf-8'))

    def _xmlid_lookup(self, ext_id):
        rec = self.env['ir.model.data'].sudo().search([
            ('module', '=', '__import_partners__'),
            ('name', '=', ext_id),
        ], limit=1)
        if not rec:
            return None
        return self.env[rec.model].browse(rec.res_id)

    # ----- auth -------------------------------------------------------------

    def test_rejects_unauthenticated(self):
        resp = self._post({'rows': [self._row('A', 'Acme')]})
        self.assertEqual(resp.status_code, 401)

    def test_rejects_wrong_internal_token(self):
        resp = self._post(
            {'rows': [self._row('A', 'Acme')]},
            internal_token='wrong-token',
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._body(resp)['error']['code'], 'INVALID_INTERNAL_TOKEN')

    def test_accepts_internal_token(self):
        resp = self._post(
            {'rows': [self._row('A1', 'Acme via Internal')]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._body(resp)['success'])

    def test_accepts_session_token(self):
        token = self._session_token()
        resp = self._post(
            {'rows': [self._row('S1', 'Acme via Session')]},
            session_token=token,
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['summary']['created'], 1)

    # ----- dry run ----------------------------------------------------------

    def test_dry_run_does_not_persist(self):
        resp = self._post(
            {'rows': [self._row('DRY-1', 'Should Not Stick')], 'dry_run': True},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertTrue(body['data']['dry_run'])
        self.assertEqual(body['data']['summary']['created'], 1)
        self.assertIsNone(self._xmlid_lookup('DRY-1'))

    def test_commit_persists(self):
        resp = self._post(
            {'rows': [self._row('COMMIT-1', 'Persisted Corp')]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        partner = self._xmlid_lookup('COMMIT-1')
        self.assertIsNotNone(partner)
        self.assertEqual(partner.name, 'Persisted Corp')
        self.assertEqual(partner.customer_rank, 1)

    # ----- idempotency ------------------------------------------------------

    def test_rerun_updates_existing(self):
        self._post(
            {'rows': [self._row('IDEMP-1', 'Original Name', email='a@x.com')]},
            internal_token='test-internal-token',
        )
        resp = self._post(
            {'rows': [self._row('IDEMP-1', 'Updated Name', email='b@x.com')]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['updated'], 1)
        self.assertEqual(body['data']['summary']['created'], 0)
        partner = self._xmlid_lookup('IDEMP-1')
        self.assertEqual(partner.name, 'Updated Name')
        self.assertEqual(partner.email, 'b@x.com')

    # ----- per-row validation ----------------------------------------------

    def test_partial_failure_does_not_abort_batch(self):
        resp = self._post({
            'rows': [
                self._row('OK-1', 'Good Co'),
                {'name': 'Missing ExtId'},
                {'ext_id': 'OK-2'},  # missing name
                self._row('OK-3', 'Bad Country', country='ZZ-NOWHERE'),
                self._row('OK-4', 'Bad Type', partner_type='lead'),
                self._row('OK-5', 'Another Good'),
            ]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        summary = body['data']['summary']
        self.assertEqual(summary['created'], 2)
        self.assertEqual(summary['failed'], 4)
        codes = {e['code'] for e in body['data']['errors']}
        self.assertEqual(codes, {
            'MISSING_EXT_ID', 'MISSING_NAME',
            'UNKNOWN_COUNTRY', 'BAD_PARTNER_TYPE',
        })

    def test_unknown_fields_are_stripped(self):
        # If unknown fields leaked into create(), Odoo would raise on
        # company_id / parent_id / id; success here proves they're filtered.
        resp = self._post(
            {'rows': [self._row(
                'STRIP-1', 'Stripped Corp',
                company_id=999999, parent_id=999999, password='secret',
            )]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['created'], 1)

    def test_partner_type_vendor(self):
        self._post(
            {'rows': [self._row('V-1', 'Vendor Co', partner_type='vendor')]},
            internal_token='test-internal-token',
        )
        partner = self._xmlid_lookup('V-1')
        self.assertEqual(partner.supplier_rank, 1)
        self.assertEqual(partner.customer_rank, 0)

    def test_partner_type_both(self):
        self._post(
            {'rows': [self._row('B-1', 'Hybrid Co', partner_type='both')]},
            internal_token='test-internal-token',
        )
        partner = self._xmlid_lookup('B-1')
        self.assertEqual(partner.customer_rank, 1)
        self.assertEqual(partner.supplier_rank, 1)

    def test_country_resolution_by_code(self):
        self._post(
            {'rows': [self._row(
                'C-1', 'Country Coded', country='US',
            )]},
            internal_token='test-internal-token',
        )
        partner = self._xmlid_lookup('C-1')
        self.assertTrue(partner.country_id)
        self.assertEqual(partner.country_id.code, 'US')

    # ----- envelope errors --------------------------------------------------

    def test_rejects_non_json(self):
        resp = self.url_open(
            self.url,
            data='not json',
            headers={
                'Content-Type': 'text/plain',
                'Authorization': 'Bearer test-internal-token',
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_rejects_empty_batch(self):
        resp = self._post(
            {'rows': []},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._body(resp)['error']['code'], 'EMPTY_BATCH')


@tagged('post_install', '-at_install')
class TestImportProductsEndpoint(HttpCase):
    """Mirrors the partners suite; covers the bits that are domain-specific:
    category auto-create + path, UoM resolution, numeric coercion, type
    canonicalization."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/internal/import/products'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls._orig_cp_token = os.environ.get('CONTROL_PLANE_TOKEN')
        os.environ['CONTROL_PLANE_TOKEN'] = 'test-internal-token'

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        if cls._orig_cp_token is None:
            os.environ.pop('CONTROL_PLANE_TOKEN', None)
        else:
            os.environ['CONTROL_PLANE_TOKEN'] = cls._orig_cp_token
        super().tearDownClass()

    def _post(self, payload, *, internal_token=None):
        headers = {'Content-Type': 'application/json'}
        if internal_token:
            headers['Authorization'] = f'Bearer {internal_token}'
        return self.url_open(self.url, data=json.dumps(payload), headers=headers)

    def _body(self, resp):
        return json.loads(resp.content.decode('utf-8'))

    def _xmlid_lookup(self, ext_id):
        rec = self.env['ir.model.data'].sudo().search([
            ('module', '=', '__import_products__'),
            ('name', '=', ext_id),
        ], limit=1)
        if not rec:
            return None
        return self.env[rec.model].browse(rec.res_id)

    def test_creates_basic_product(self):
        resp = self._post(
            {'rows': [{
                'ext_id': 'P-1',
                'name': 'Widget',
                'default_code': 'WDG-001',
                'list_price': '12.50',
                'sale_ok': 'true',
            }]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['created'], 1)
        product = self._xmlid_lookup('P-1')
        self.assertIsNotNone(product)
        self.assertEqual(product.name, 'Widget')
        self.assertEqual(product.default_code, 'WDG-001')
        self.assertEqual(product.list_price, 12.50)
        self.assertTrue(product.sale_ok)

    def test_category_path_auto_created(self):
        resp = self._post(
            {'rows': [{
                'ext_id': 'P-CAT-1', 'name': 'Phone X',
                'category': 'Electronics / Phones',
            }]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        product = self._xmlid_lookup('P-CAT-1')
        self.assertEqual(product.categ_id.name, 'Phones')
        self.assertEqual(product.categ_id.parent_id.name, 'Electronics')

    def test_decimal_comma_accepted(self):
        """European-style decimal commas are common in FR/AR Excel files."""
        resp = self._post(
            {'rows': [{
                'ext_id': 'P-EU', 'name': 'Eurowidget',
                'list_price': '19,99',
            }]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        product = self._xmlid_lookup('P-EU')
        self.assertEqual(product.list_price, 19.99)

    def test_bad_numeric_reported_per_row(self):
        resp = self._post(
            {'rows': [
                {'ext_id': 'OK-A', 'name': 'Good A', 'list_price': '10'},
                {'ext_id': 'BAD-B', 'name': 'Bad B', 'list_price': 'abc'},
                {'ext_id': 'OK-C', 'name': 'Good C', 'list_price': '20'},
            ]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['created'], 2)
        self.assertEqual(body['data']['summary']['failed'], 1)
        codes = {e['code'] for e in body['data']['errors']}
        self.assertIn('BAD_NUMBER', codes)

    def test_type_synonyms(self):
        resp = self._post(
            {'rows': [
                {'ext_id': 'T-SVC', 'name': 'Service Plan', 'type': 'service'},
                {'ext_id': 'T-CON', 'name': 'Sticker', 'type': 'consumable'},
                {'ext_id': 'T-STK', 'name': 'Box', 'type': 'storable'},
            ]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['created'], 3)

    def test_rerun_updates_existing_product(self):
        self._post(
            {'rows': [{
                'ext_id': 'P-IDEMP', 'name': 'Original', 'list_price': '5',
            }]},
            internal_token='test-internal-token',
        )
        resp = self._post(
            {'rows': [{
                'ext_id': 'P-IDEMP', 'name': 'Renamed', 'list_price': '7.5',
            }]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['updated'], 1)
        self.assertEqual(body['data']['summary']['created'], 0)
        product = self._xmlid_lookup('P-IDEMP')
        self.assertEqual(product.name, 'Renamed')
        self.assertEqual(product.list_price, 7.5)

    def test_dry_run_does_not_persist_products(self):
        resp = self._post(
            {'rows': [{'ext_id': 'P-DRY', 'name': 'Should Not Stick'}],
             'dry_run': True},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._body(resp)['data']['summary']['created'], 1)
        self.assertIsNone(self._xmlid_lookup('P-DRY'))


@tagged('post_install', '-at_install')
class TestImportCoAEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/internal/import/chart-of-accounts'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls._orig_cp_token = os.environ.get('CONTROL_PLANE_TOKEN')
        os.environ['CONTROL_PLANE_TOKEN'] = 'test-internal-token'

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        if cls._orig_cp_token is None:
            os.environ.pop('CONTROL_PLANE_TOKEN', None)
        else:
            os.environ['CONTROL_PLANE_TOKEN'] = cls._orig_cp_token
        super().tearDownClass()

    def _post(self, payload, *, internal_token=None):
        headers = {'Content-Type': 'application/json'}
        if internal_token:
            headers['Authorization'] = f'Bearer {internal_token}'
        return self.url_open(self.url, data=json.dumps(payload), headers=headers)

    def _body(self, resp):
        return json.loads(resp.content.decode('utf-8'))

    def _xmlid_lookup(self, ext_id):
        rec = self.env['ir.model.data'].sudo().search([
            ('module', '=', '__import_coa__'),
            ('name', '=', ext_id),
        ], limit=1)
        if not rec:
            return None
        return self.env[rec.model].browse(rec.res_id)

    def test_creates_account_with_natural_language_type(self):
        resp = self._post(
            {'rows': [{
                'ext_id': 'A-RX', 'code': '411099',
                'name': 'Trade Receivables Extra', 'account_type': 'receivable',
            }]},
            internal_token='test-internal-token',
        )
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['created'], 1)
        account = self._xmlid_lookup('A-RX')
        self.assertEqual(account.code, '411099')
        self.assertEqual(account.account_type, 'asset_receivable')
        self.assertTrue(account.reconcile)  # auto-true for receivable

    def test_rejects_unknown_account_type(self):
        resp = self._post(
            {'rows': [{
                'ext_id': 'BAD', 'code': '999000',
                'name': 'Bogus', 'account_type': 'rocketship',
            }]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['failed'], 1)
        self.assertIn('BAD_ACCOUNT_TYPE',
                      {e['code'] for e in body['data']['errors']})

    def test_idempotent_by_code_when_no_ext_id(self):
        """Operator who doesn't bother with ext_id should still get
        idempotent re-imports because account code is unique."""
        self._post(
            {'rows': [{'code': '777777', 'name': 'First Name', 'account_type': 'asset'}]},
            internal_token='test-internal-token',
        )
        resp = self._post(
            {'rows': [{'code': '777777', 'name': 'Renamed', 'account_type': 'asset'}]},
            internal_token='test-internal-token',
        )
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['updated'], 1)
        account = self._xmlid_lookup('code-777777')
        self.assertEqual(account.name, 'Renamed')


@tagged('post_install', '-at_install')
class TestImportOpeningBalancesEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/internal/import/opening-balances'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls._orig_cp_token = os.environ.get('CONTROL_PLANE_TOKEN')
        os.environ['CONTROL_PLANE_TOKEN'] = 'test-internal-token'

        # Seed two accounts + a suspense account via the CoA import endpoint
        # to keep this test self-contained (no reliance on a specific country
        # localization).
        coa_url = '/api/v2/internal/import/chart-of-accounts'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer test-internal-token',
        }
        coa_payload = {'rows': [
            {'ext_id': 'A-CASH', 'code': '5121', 'name': 'Cash',
             'account_type': 'cash'},
            {'ext_id': 'A-EQ', 'code': '1010', 'name': 'Equity',
             'account_type': 'equity'},
            {'ext_id': 'A-SUSP', 'code': '999999', 'name': 'Suspense',
             'account_type': 'asset_current'},
        ]}
        cls.opener = cls.url_open  # for use in setup
        # Use the http client directly since url_open is an instance method
        # — we attach via the class to make it available before setUp.
        import requests
        requests.post(
            cls.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
            + coa_url, json=coa_payload, headers=headers, timeout=10,
        )

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        if cls._orig_cp_token is None:
            os.environ.pop('CONTROL_PLANE_TOKEN', None)
        else:
            os.environ['CONTROL_PLANE_TOKEN'] = cls._orig_cp_token
        super().tearDownClass()

    def _post(self, payload, *, internal_token=None):
        headers = {'Content-Type': 'application/json'}
        if internal_token:
            headers['Authorization'] = f'Bearer {internal_token}'
        return self.url_open(self.url, data=json.dumps(payload), headers=headers)

    def _body(self, resp):
        return json.loads(resp.content.decode('utf-8'))

    def test_balanced_tb_posts_move(self):
        resp = self._post({
            'rows': [
                {'account_code': '5121', 'debit': '100.00', 'credit': '0'},
                {'account_code': '1010', 'debit': '0', 'credit': '100.00'},
            ],
            'import_run_id': 'ob-test-1',
            'options': {'narration': 'Balanced TB test'},
        }, internal_token='test-internal-token')
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['failed'], 0)
        self.assertEqual(body['data']['summary']['lines_posted'], 2)
        self.assertEqual(body['data']['summary']['imbalance'], 0)

    def test_unbalanced_without_suspense_rejected(self):
        resp = self._post({
            'rows': [
                {'account_code': '5121', 'debit': '120.00', 'credit': '0'},
                {'account_code': '1010', 'debit': '0', 'credit': '100.00'},
            ],
            'import_run_id': 'ob-test-2',
        }, internal_token='test-internal-token')
        body = self._body(resp)
        codes = {e['code'] for e in body['data']['errors']}
        self.assertIn('UNBALANCED', codes)
        self.assertEqual(body['data']['summary']['imbalance'], 20.0)

    def test_unbalanced_with_suspense_auto_balances(self):
        resp = self._post({
            'rows': [
                {'account_code': '5121', 'debit': '120.00', 'credit': '0'},
                {'account_code': '1010', 'debit': '0', 'credit': '100.00'},
            ],
            'import_run_id': 'ob-test-3',
            'options': {'suspense_account_code': '999999'},
        }, internal_token='test-internal-token')
        body = self._body(resp)
        self.assertEqual(body['data']['summary']['failed'], 0)
        # The suspense line brings total lines to 3 (2 source + 1 suspense).
        self.assertEqual(body['data']['summary']['lines_posted'], 3)
        suspense = body['data']['records'][0]['suspense_applied']
        self.assertIsNotNone(suspense)
        self.assertEqual(suspense['account_code'], '999999')
        self.assertEqual(suspense['side'], 'credit')  # excess debit → balancing credit
        self.assertEqual(suspense['amount'], 20.0)

    def test_bad_date_rejected(self):
        resp = self._post({
            'rows': [{'account_code': '5121', 'debit': '1', 'credit': '0'}],
            'options': {'date': 'tomorrow'},
        }, internal_token='test-internal-token')
        body = self._body(resp)
        codes = {e['code'] for e in body['data']['errors']}
        self.assertIn('BAD_DATE', codes)
