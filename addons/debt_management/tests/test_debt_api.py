# -*- coding: utf-8 -*-

import json
import secrets
import string
from datetime import datetime, timedelta, date

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestDebtApi(HttpCase):
    """Integration tests for /api/v2/debts/* endpoints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Debtor',
            'email': 'debtor@example.com',
            'phone': '+15551234567',
        })
        cls.interest_rule = cls.env['debt.interest.rule'].create({
            'name': 'Monthly 5%',
            'rate': 5.0,
            'cycle': 'monthly',
            'compound': False,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _login(self, login='admin', password='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
        raw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        token_hash = self.env['api.session']._hash_token(raw)
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return raw

    def _get(self, path, token, params=None):
        qs = ''
        if params:
            qs = '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        resp = self.url_open(
            f'{self.api_base}{path}{qs}',
            headers={'session-token': token},
        )
        return resp.json()

    def _post(self, path, token, data):
        resp = self.url_open(
            f'{self.api_base}{path}',
            data=json.dumps(data),
            headers={
                'session-token': token,
                'Content-Type': 'application/json',
            },
        )
        return resp.json()

    def _put(self, path, token, data):
        url = self.base_url() + self.api_base + path
        resp = self.opener.put(
            url,
            data=json.dumps(data),
            headers={
                'session-token': token,
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        return resp.json()

    def _delete(self, path, token):
        url = self.base_url() + self.api_base + path
        resp = self.opener.delete(
            url,
            headers={'session-token': token},
            timeout=30,
        )
        return resp.json()

    def _create_debt_via_api(self, token, **overrides):
        payload = {
            'partner_id': self.partner.id,
            'amount': 1000.00,
            'due_date': (date.today() + timedelta(days=30)).isoformat(),
        }
        payload.update(overrides)
        return self._post('/debts', token, payload)

    # ------------------------------------------------------------------
    # Debt CRUD
    # ------------------------------------------------------------------

    def test_create_debt_success(self):
        token = self._login()
        body = self._create_debt_via_api(token)
        self.assertTrue(body.get('success'), f"Create failed: {body}")
        data = body['data']
        self.assertEqual(data['amount'], 1000.00)
        self.assertEqual(data['state'], 'active')
        self.assertEqual(data['partner']['id'], self.partner.id)
        self.assertTrue(data['name'].startswith('DEBT/'))

    def test_create_debt_missing_fields(self):
        token = self._login()
        body = self._post('/debts', token, {'amount': 500})
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'MISSING_FIELDS')

    def test_create_debt_with_interest(self):
        token = self._login()
        body = self._create_debt_via_api(
            token,
            interest_rule_id=self.interest_rule.id,
        )
        self.assertTrue(body.get('success'))
        self.assertIsNotNone(body['data']['interest_rule'])
        self.assertEqual(body['data']['interest_rule']['rate'], 5.0)

    def test_list_debts(self):
        token = self._login()
        self._create_debt_via_api(token, amount=100)
        self._create_debt_via_api(token, amount=200)
        body = self._get('/debts', token)
        self.assertTrue(body.get('success'))
        self.assertGreaterEqual(body['data']['count'], 2)
        self.assertIn('total', body['data'])

    def test_list_debts_filter_by_state(self):
        token = self._login()
        self._create_debt_via_api(token)
        body = self._get('/debts', token, {'state': 'active'})
        self.assertTrue(body.get('success'))
        for d in body['data']['debts']:
            self.assertEqual(d['state'], 'active')

    def test_get_debt_detail(self):
        token = self._login()
        created = self._create_debt_via_api(token)
        debt_id = created['data']['id']
        body = self._get(f'/debts/{debt_id}', token)
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['id'], debt_id)
        self.assertIn('payments', body['data'])
        self.assertIn('notifications', body['data'])

    def test_get_debt_not_found(self):
        token = self._login()
        body = self._get('/debts/999999', token)
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'DEBT_NOT_FOUND')

    def test_update_debt(self):
        token = self._login()
        created = self._create_debt_via_api(token)
        debt_id = created['data']['id']
        new_due = (date.today() + timedelta(days=60)).isoformat()
        body = self._put(f'/debts/{debt_id}', token, {
            'due_date': new_due,
            'notes': 'Extended deadline',
        })
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['due_date'], new_due)

    def test_cancel_debt(self):
        token = self._login()
        created = self._create_debt_via_api(token)
        debt_id = created['data']['id']
        body = self._delete(f'/debts/{debt_id}', token)
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['state'], 'cancelled')

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    def test_record_payment(self):
        token = self._login()
        created = self._create_debt_via_api(token, amount=500)
        debt_id = created['data']['id']
        body = self._post(f'/debts/{debt_id}/payments', token, {
            'amount': 200,
            'reference': 'PAY-001',
        })
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['payment']['amount'], 200)
        self.assertEqual(body['data']['debt_balance'], 300)

    def test_record_payment_exceeds_balance(self):
        token = self._login()
        created = self._create_debt_via_api(token, amount=100)
        debt_id = created['data']['id']
        body = self._post(f'/debts/{debt_id}/payments', token, {'amount': 200})
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'EXCESS_PAYMENT')

    def test_full_payment_marks_paid(self):
        token = self._login()
        created = self._create_debt_via_api(token, amount=300)
        debt_id = created['data']['id']
        body = self._post(f'/debts/{debt_id}/payments', token, {'amount': 300})
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['debt_state'], 'paid')
        self.assertEqual(body['data']['debt_balance'], 0)

    def test_list_payments(self):
        token = self._login()
        created = self._create_debt_via_api(token, amount=500)
        debt_id = created['data']['id']
        self._post(f'/debts/{debt_id}/payments', token, {'amount': 100})
        self._post(f'/debts/{debt_id}/payments', token, {'amount': 150})
        body = self._get(f'/debts/{debt_id}/payments', token)
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['count'], 2)
        self.assertEqual(body['data']['debt_balance'], 250)

    # ------------------------------------------------------------------
    # Customer debt
    # ------------------------------------------------------------------

    def test_customer_debts(self):
        token = self._login()
        self._create_debt_via_api(token, amount=100)
        body = self._get(f'/debts/customer/{self.partner.id}', token)
        self.assertTrue(body.get('success'))
        self.assertGreaterEqual(body['data']['count'], 1)
        self.assertEqual(body['data']['customer']['id'], self.partner.id)

    def test_customer_summary(self):
        token = self._login()
        self._create_debt_via_api(token, amount=400)
        self._create_debt_via_api(token, amount=600)
        body = self._get(f'/debts/customer/{self.partner.id}/summary', token)
        self.assertTrue(body.get('success'))
        summary = body['data']['summary']
        self.assertGreaterEqual(summary['active_debts'], 2)
        self.assertGreaterEqual(summary['current_outstanding'], 1000)

    def test_set_customer_limit(self):
        token = self._login()
        body = self._put(f'/debts/customer/{self.partner.id}/limit', token, {
            'max_debt_limit': 5000,
            'use_debt_limit': True,
        })
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['max_debt_limit'], 5000)
        self.assertTrue(body['data']['use_debt_limit'])

    def test_debt_limit_blocks_creation(self):
        token = self._login()
        self._put(f'/debts/customer/{self.partner.id}/limit', token, {
            'max_debt_limit': 500,
            'use_debt_limit': True,
        })
        body = self._create_debt_via_api(token, amount=600)
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'DEBT_LIMIT_EXCEEDED')

    # ------------------------------------------------------------------
    # Overdue & analytics
    # ------------------------------------------------------------------

    def test_overdue_debts(self):
        token = self._login()
        body = self._get('/debts/overdue', token)
        self.assertTrue(body.get('success'))
        self.assertIn('debts', body['data'])

    def test_debt_analytics(self):
        token = self._login()
        self._create_debt_via_api(token, amount=1000)
        body = self._get('/debts/analytics/overview', token)
        self.assertTrue(body.get('success'))
        kpis = body['data']['kpis']
        self.assertIn('total_debts', kpis)
        self.assertIn('total_outstanding', kpis)
        self.assertIn('collection_rate', kpis)
        self.assertIn('top_debtors', body['data'])

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def test_list_notifications(self):
        token = self._login()
        body = self._get('/debts/notifications', token)
        self.assertTrue(body.get('success'))
        self.assertIn('notifications', body['data'])

    # ------------------------------------------------------------------
    # Interest rules
    # ------------------------------------------------------------------

    def test_list_interest_rules(self):
        token = self._login()
        body = self._get('/debts/interest-rules', token)
        self.assertTrue(body.get('success'))
        self.assertGreaterEqual(body['data']['count'], 1)

    def test_create_interest_rule(self):
        token = self._login()
        body = self._post('/debts/interest-rules', token, {
            'name': 'Weekly 2%',
            'rate': 2.0,
            'cycle': 'weekly',
            'compound': True,
        })
        self.assertTrue(body.get('success'))
        self.assertEqual(body['data']['name'], 'Weekly 2%')
        self.assertEqual(body['data']['cycle'], 'weekly')
        self.assertTrue(body['data']['compound'])

    def test_create_interest_rule_invalid_cycle(self):
        token = self._login()
        body = self._post('/debts/interest-rules', token, {
            'name': 'Bad',
            'rate': 1.0,
            'cycle': 'hourly',
        })
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'INVALID_CYCLE')

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def test_requires_auth(self):
        resp = self.url_open(f'{self.api_base}/debts')
        body = resp.json()
        self.assertFalse(body.get('success'))

    # ------------------------------------------------------------------
    # Model-level: interest cron
    # ------------------------------------------------------------------

    def test_cron_interest_calculation(self):
        debt = self.env['debt.record'].create({
            'partner_id': self.partner.id,
            'amount': 1000,
            'issue_date': date.today() - timedelta(days=31),
            'due_date': date.today() + timedelta(days=60),
            'interest_rule_id': self.interest_rule.id,
            'last_interest_date': date.today() - timedelta(days=31),
            'state': 'active',
        })
        self.env['debt.record']._cron_calculate_interest()
        debt.invalidate_recordset()
        self.assertGreater(debt.amount_interest, 0)
        self.assertEqual(debt.amount_interest, 50.0)

    def test_cron_overdue_detection(self):
        debt = self.env['debt.record'].create({
            'partner_id': self.partner.id,
            'amount': 500,
            'issue_date': date.today() - timedelta(days=10),
            'due_date': date.today() - timedelta(days=1),
            'state': 'active',
        })
        self.env['debt.record']._cron_check_overdue_and_notify()
        debt.invalidate_recordset()
        self.assertEqual(debt.state, 'overdue')
        notifs = self.env['debt.notification.log'].search([
            ('debt_id', '=', debt.id),
            ('notification_type', '=', 'overdue'),
        ])
        self.assertTrue(notifs)

    # ------------------------------------------------------------------
    # Sale order integration
    # ------------------------------------------------------------------

    def test_sale_order_debt_creation(self):
        product = self.env['product.product'].create({
            'name': 'Test Widget',
            'list_price': 100,
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'is_debt_sale': True,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 2,
                'price_unit': 100,
            })],
        })
        order.action_confirm()
        self.assertTrue(order.debt_record_id)
        self.assertEqual(order.debt_record_id.state, 'active')
        self.assertEqual(order.debt_record_id.partner_id, self.partner)

    def test_sale_order_debt_limit_block(self):
        self.partner.write({
            'use_debt_limit': True,
            'max_debt_limit': 50,
        })
        product = self.env['product.product'].create({
            'name': 'Expensive Widget',
            'list_price': 100,
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'is_debt_sale': True,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })],
        })
        with self.assertRaises(Exception):
            order.action_confirm()
