# -*- coding: utf-8 -*-
"""Integration tests for HR write/action endpoints.

Covers:
- POST /api/v2/hr/applicants/<id>/refuse
- POST /api/v2/hr/applicants/<id>/hire
- POST /api/v2/hr/leaves/<id>/approve
- POST /api/v2/hr/leaves/<id>/refuse

The applicant + leave models are only present once hr_recruitment / hr_holidays
are installed. Each test skips gracefully if the model is missing so the suite
can run on minimal demo databases.
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestHrWriteActions(HttpCase):

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

    def _post(self, path, token=None, body=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(
            path,
            data=json.dumps(body or {}),
            headers=headers,
        )

    # ===== auth ===============================================================

    def test_applicant_refuse_requires_auth(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        resp = self._post('/api/v2/hr/applicants/1/refuse')
        self.assertEqual(resp.status_code, 401)

    def test_applicant_hire_requires_auth(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        resp = self._post('/api/v2/hr/applicants/1/hire')
        self.assertEqual(resp.status_code, 401)

    def test_leave_approve_requires_auth(self):
        if 'hr.leave' not in self.env:
            self.skipTest('hr_holidays not installed')
        resp = self._post('/api/v2/hr/leaves/1/approve')
        self.assertEqual(resp.status_code, 401)

    def test_leave_refuse_requires_auth(self):
        if 'hr.leave' not in self.env:
            self.skipTest('hr_holidays not installed')
        resp = self._post('/api/v2/hr/leaves/1/refuse')
        self.assertEqual(resp.status_code, 401)

    # ===== applicant: refuse ==================================================

    def _make_applicant(self):
        job = self.env['hr.job'].sudo().search([], limit=1)
        if not job:
            job = self.env['hr.job'].sudo().create({'name': 'API Test Position'})
        return self.env['hr.applicant'].sudo().create({
            'partner_name': 'API Test Applicant',
            'email_from': 'apitest@example.com',
            'job_id': job.id,
        })

    def test_applicant_refuse_happy_path(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        token = self._login()
        applicant = self._make_applicant()

        # Ensure at least one refuse reason exists
        if not self.env['hr.applicant.refuse.reason'].sudo().search([], limit=1):
            self.env['hr.applicant.refuse.reason'].sudo().create({'name': 'Test Reason'})

        resp = self._post(f'/api/v2/hr/applicants/{applicant.id}/refuse', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['applicant']['id'], applicant.id)
        self.assertFalse(body['data']['applicant']['active'])
        self.assertEqual(body['data']['applicant']['application_status'], 'refused')

        applicant.invalidate_recordset()
        self.assertFalse(applicant.active)
        self.assertTrue(applicant.refuse_reason_id)

    def test_applicant_refuse_already_refused_returns_400(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        token = self._login()
        applicant = self._make_applicant()
        if not self.env['hr.applicant.refuse.reason'].sudo().search([], limit=1):
            self.env['hr.applicant.refuse.reason'].sudo().create({'name': 'Test Reason'})
        # First refusal succeeds
        self._post(f'/api/v2/hr/applicants/{applicant.id}/refuse', token=token)
        # Second refusal must 400
        resp = self._post(f'/api/v2/hr/applicants/{applicant.id}/refuse', token=token)
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_STATE')

    def test_applicant_refuse_with_explicit_reason(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        token = self._login()
        applicant = self._make_applicant()
        reason = self.env['hr.applicant.refuse.reason'].sudo().create({'name': 'Specific Reason'})

        resp = self._post(
            f'/api/v2/hr/applicants/{applicant.id}/refuse',
            token=token,
            body={'reason_id': reason.id},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        applicant.invalidate_recordset()
        self.assertEqual(applicant.refuse_reason_id.id, reason.id)

    def test_applicant_refuse_unknown_id_returns_404(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        token = self._login()
        resp = self._post('/api/v2/hr/applicants/99999999/refuse', token=token)
        self.assertEqual(resp.status_code, 404, resp.text)

    # ===== applicant: hire ====================================================

    def test_applicant_hire_happy_path(self):
        if 'hr.applicant' not in self.env:
            self.skipTest('hr_recruitment not installed')
        token = self._login()
        applicant = self._make_applicant()

        resp = self._post(f'/api/v2/hr/applicants/{applicant.id}/hire', token=token)
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertIn('employee', body['data'])
        self.assertIsNotNone(body['data']['employee'])
        self.assertTrue(body['data']['employee']['id'])

    # ===== leave: approve / refuse ============================================

    def _make_leave(self):
        employee = self.env['hr.employee'].sudo().search([], limit=1)
        if not employee:
            self.skipTest('No employee available to attach a leave to')
        leave_type = self.env['hr.leave.type'].sudo().search([
            ('requires_allocation', '=', False),
        ], limit=1) or self.env['hr.leave.type'].sudo().search([], limit=1)
        if not leave_type:
            self.skipTest('No leave type available')
        today = datetime.now().date()
        return self.env['hr.leave'].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        ).create({
            'employee_id': employee.id,
            'holiday_status_id': leave_type.id,
            'request_date_from': today,
            'request_date_to': today + timedelta(days=1),
            'name': 'API test leave',
        })

    def test_leave_refuse_from_confirm(self):
        if 'hr.leave' not in self.env:
            self.skipTest('hr_holidays not installed')
        token = self._login()
        leave = self._make_leave()
        # Odoo 19 hr.leave default state is already 'confirm' on create
        self.assertIn(leave.state, ('confirm', 'validate', 'validate1'))

        resp = self._post(f'/api/v2/hr/leaves/{leave.id}/refuse', token=token,
                          body={'reason': 'Project deadline'})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['leave']['state'], 'refuse')

    def test_leave_refuse_from_cancel_returns_400(self):
        if 'hr.leave' not in self.env:
            self.skipTest('hr_holidays not installed')
        token = self._login()
        leave = self._make_leave()
        leave.sudo().with_context(leave_skip_state_check=True).write({'state': 'cancel'})
        resp = self._post(f'/api/v2/hr/leaves/{leave.id}/refuse', token=token)
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_STATE')

    def test_leave_approve_unknown_id_returns_404(self):
        if 'hr.leave' not in self.env:
            self.skipTest('hr_holidays not installed')
        token = self._login()
        resp = self._post('/api/v2/hr/leaves/99999999/approve', token=token)
        self.assertEqual(resp.status_code, 404, resp.text)


@tagged('post_install', '-at_install')
class TestJournalEntryPost(HttpCase):

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

    def _login(self, login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
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

    def _post(self, path, token=None, body=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(path, data=json.dumps(body or {}), headers=headers)

    def test_post_requires_auth(self):
        resp = self._post('/api/v2/accounting/journal-entries/1/post')
        self.assertEqual(resp.status_code, 401)

    def test_post_unknown_id_returns_404(self):
        token = self._login()
        resp = self._post('/api/v2/accounting/journal-entries/99999999/post', token=token)
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_post_already_posted_returns_400(self):
        # Use an existing posted invoice from demo data, if any
        posted = self.env['account.move'].sudo().search([('state', '=', 'posted')], limit=1)
        if not posted:
            self.skipTest('No posted account.move available in test DB')
        token = self._login()
        resp = self._post(f'/api/v2/accounting/journal-entries/{posted.id}/post', token=token)
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_STATE')
