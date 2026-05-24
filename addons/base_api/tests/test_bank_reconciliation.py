# -*- coding: utf-8 -*-
"""Bank statement import + reconciliation endpoints."""

import io
import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged

from odoo.addons.base_api.controllers.simple_api import SimpleApiController


SAMPLE_CSV = (
    "date,label,amount\n"
    "2025-01-05,INV-001 Acme,1500.00\n"
    "2025-01-06,Bank fees,-12.50\n"
    "2025-01-07,Salary payout,-250000\n"
)

SAMPLE_CSV_FR = (
    "date;libellé;débit;crédit\n"
    "05/01/2025;INV-001 Acme;;1500,00\n"
    "06/01/2025;Frais bancaires;12,50;\n"
)

SAMPLE_OFX = """OFXHEADER:100
DATA:OFXSGML

<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>CREDIT
            <DTPOSTED>20250105
            <TRNAMT>1500.00
            <FITID>ABC123
            <NAME>Acme Corp
            <MEMO>INV-001 settlement
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>DEBIT
            <DTPOSTED>20250106
            <TRNAMT>-12.50
            <FITID>FEE001
            <NAME>Bank
            <MEMO>Monthly fees
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>
"""


def _ensure_bank_journal(env):
    J = env['account.journal'].sudo()
    j = J.search([('type', '=', 'bank')], limit=1)
    if not j:
        j = J.create({'name': 'Test Bank', 'code': 'BNKT', 'type': 'bank'})
    return j


@tagged('post_install', '-at_install')
class TestBankParsers(HttpCase):
    """Pure parser tests — no DB writes."""

    def test_csv_parses_amount_column(self):
        rows = SimpleApiController._parse_csv_statement(SAMPLE_CSV.encode())
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows[0]['amount'], 1500.00)
        self.assertAlmostEqual(rows[1]['amount'], -12.50)
        self.assertEqual(rows[0]['label'], 'INV-001 Acme')

    def test_csv_parses_debit_credit_columns_fr_decimals(self):
        rows = SimpleApiController._parse_csv_statement(SAMPLE_CSV_FR.encode())
        self.assertEqual(len(rows), 2)
        # Credit row → positive amount
        self.assertAlmostEqual(rows[0]['amount'], 1500.00)
        # Debit row → negative amount
        self.assertAlmostEqual(rows[1]['amount'], -12.50)

    def test_ofx_parses_transactions(self):
        rows = SimpleApiController._parse_ofx_statement(SAMPLE_OFX.encode())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['date'], '2025-01-05')
        self.assertAlmostEqual(rows[0]['amount'], 1500.00)
        self.assertEqual(rows[0]['ref'], 'ABC123')
        self.assertEqual(rows[1]['date'], '2025-01-06')
        self.assertAlmostEqual(rows[1]['amount'], -12.50)


@tagged('post_install', '-at_install')
class TestBankReconciliationApi(HttpCase):

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

    def setUp(self):
        super().setUp()
        self.journal = _ensure_bank_journal(self.env)

    def _upload_csv(self, token, csv_bytes, **extra):
        return self.url_open(
            '/api/v2/accounting/bank-statement/import',
            data={'journal_id': str(self.journal.id), **extra},
            files={'file': ('statement.csv', io.BytesIO(csv_bytes), 'text/csv')},
            headers={'session-token': token},
        )

    def test_import_requires_auth(self):
        resp = self.url_open(
            '/api/v2/accounting/bank-statement/import',
            data={'journal_id': str(self.journal.id)},
        )
        self.assertEqual(resp.status_code, 401)

    def test_import_csv_creates_statement(self):
        token = self._login()
        resp = self._upload_csv(token, SAMPLE_CSV.encode())
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body['success'])
        st = body['data']['statement']
        self.assertEqual(st['line_count'], 3)
        self.assertEqual(st['journal_id'], self.journal.id)
        self.assertEqual(st['unreconciled_count'], 3)

    def test_import_rejects_non_bank_journal(self):
        token = self._login()
        J = self.env['account.journal'].sudo()
        misc = J.search([('type', '=', 'general')], limit=1)
        if not misc:
            misc = J.create({'name': 'OD test', 'code': 'ODX', 'type': 'general'})
        resp = self.url_open(
            '/api/v2/accounting/bank-statement/import',
            data={'journal_id': str(misc.id)},
            files={'file': ('statement.csv', io.BytesIO(SAMPLE_CSV.encode()), 'text/csv')},
            headers={'session-token': token},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_list_and_detail(self):
        token = self._login()
        upload = self._upload_csv(token, SAMPLE_CSV.encode())
        st_id = upload.json()['data']['statement']['id']

        list_resp = self.url_open(
            f'/api/v2/accounting/bank-statement?journal_id={self.journal.id}',
            headers={'session-token': token},
        )
        self.assertEqual(list_resp.status_code, 200)
        listed_ids = [s['id'] for s in list_resp.json()['data']['statements']]
        self.assertIn(st_id, listed_ids)

        detail = self.url_open(
            f'/api/v2/accounting/bank-statement/{st_id}',
            headers={'session-token': token},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['data']['statement']['id'], st_id)
        self.assertEqual(len(detail.json()['data']['statement']['lines']), 3)

    def test_close_refuses_unreconciled_without_force(self):
        token = self._login()
        upload = self._upload_csv(token, SAMPLE_CSV.encode())
        st_id = upload.json()['data']['statement']['id']

        resp = self.url_open(
            f'/api/v2/accounting/bank-statement/{st_id}/close',
            data=json.dumps({}),
            headers={'session-token': token, 'Content-Type': 'application/json'},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'UNRECONCILED_LINES')

    def test_close_force_succeeds(self):
        token = self._login()
        upload = self._upload_csv(token, SAMPLE_CSV.encode())
        st_id = upload.json()['data']['statement']['id']

        resp = self.url_open(
            f'/api/v2/accounting/bank-statement/{st_id}/close',
            data=json.dumps({'force': True}),
            headers={'session-token': token, 'Content-Type': 'application/json'},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()['success'])
