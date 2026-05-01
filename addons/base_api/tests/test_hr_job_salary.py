# -*- coding: utf-8 -*-
"""Tests for the salary range fields added to hr.job.

Covers:
- defaults (salary_currency_id falls back to company currency, salary_period='monthly')
- write via generic POST /api/v2/create/hr.job
- write via generic PUT /api/v2/update/hr.job/{id}
- ValidationError when salary_max < salary_min
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import HttpCase, TransactionCase, tagged


class TestHrJobSalaryFields(TransactionCase):

    def test_defaults_currency_and_period(self):
        job = self.env['hr.job'].create({'name': 'Salary Defaults Position'})
        self.assertEqual(job.salary_currency_id, self.env.company.currency_id)
        self.assertEqual(job.salary_period, 'monthly')
        self.assertFalse(job.salary_min)
        self.assertFalse(job.salary_max)

    def test_constraint_max_below_min_rejected(self):
        with self.assertRaises(ValidationError):
            self.env['hr.job'].create({
                'name': 'Bad Range Position',
                'salary_min': 5000,
                'salary_max': 4000,
            })

    def test_constraint_negative_rejected(self):
        with self.assertRaises(ValidationError):
            self.env['hr.job'].create({
                'name': 'Negative Salary Position',
                'salary_min': -100,
            })

    def test_only_min_or_only_max_is_allowed(self):
        job = self.env['hr.job'].create({
            'name': 'Min Only Position',
            'salary_min': 3000,
        })
        self.assertEqual(job.salary_min, 3000)
        self.assertFalse(job.salary_max)


@tagged('post_install', '-at_install')
class TestHrJobSalaryViaApi(HttpCase):

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

    def _request(self, method, path, token=None, body=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(
            path,
            data=json.dumps(body or {}),
            headers=headers,
            method=method,
        )

    def test_create_hr_job_with_salary_fields(self):
        token = self._login()
        eur = self.env['res.currency'].sudo().search([('name', '=', 'EUR')], limit=1)
        body = {
            'name': 'API Salary Position',
            'salary_min': 60000,
            'salary_max': 90000,
            'salary_period': 'yearly',
        }
        if eur:
            body['salary_currency_id'] = eur.id

        resp = self._request('POST', '/api/v2/create/hr.job', token=token, body=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        new_id = resp.json()['data']['id']

        job = self.env['hr.job'].sudo().browse(new_id)
        self.assertEqual(job.salary_min, 60000)
        self.assertEqual(job.salary_max, 90000)
        self.assertEqual(job.salary_period, 'yearly')
        if eur:
            self.assertEqual(job.salary_currency_id, eur)

    def test_update_hr_job_salary_fields(self):
        token = self._login()
        job = self.env['hr.job'].sudo().create({'name': 'API Update Position'})

        resp = self._request(
            'PUT',
            f'/api/v2/update/hr.job/{job.id}',
            token=token,
            body={'salary_min': 1500, 'salary_max': 2500, 'salary_period': 'hourly'},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        job.invalidate_recordset()
        self.assertEqual(job.salary_min, 1500)
        self.assertEqual(job.salary_max, 2500)
        self.assertEqual(job.salary_period, 'hourly')

    def test_update_hr_job_invalid_range_returns_400(self):
        token = self._login()
        job = self.env['hr.job'].sudo().create({'name': 'API Invalid Range Position'})

        resp = self._request(
            'PUT',
            f'/api/v2/update/hr.job/{job.id}',
            token=token,
            body={'salary_min': 9000, 'salary_max': 1000},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()['error']['code'], 'UPDATE_ERROR')
