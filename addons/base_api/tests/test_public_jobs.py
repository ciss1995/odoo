# -*- coding: utf-8 -*-
"""Tests for the public /api/v2/public/jobs/<id> endpoint used for sharing
job postings to WhatsApp / LinkedIn / etc."""

import json

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPublicJobsEndpoint(HttpCase):
    """Verify the unauthenticated job-share endpoint behaves correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2/public/jobs'
        # The endpoint is auth='none', so no session creation needed.
        # The endpoint doesn't go through _enforce_subscription either —
        # public endpoints bypass it on purpose.

    def _create_job(self, name='Frontend Engineer', is_public=False, description='', requirements=''):
        return self.env['hr.job'].sudo().create({
            'name': name,
            'description': description,
            'requirements': requirements,
            'is_public': is_public,
        })

    def test_public_job_returns_200_when_published(self):
        job = self._create_job(name='Senior Backend Engineer', is_public=True,
                                description='<p>Build cool stuff</p>',
                                requirements='Python, Postgres')
        resp = self.url_open(f"{self.api_base}/{job.id}")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
        data = body['data']
        self.assertEqual(data['id'], job.id)
        self.assertEqual(data['name'], 'Senior Backend Engineer')
        self.assertIn('Build cool stuff', data['description'])
        self.assertEqual(data['requirements'], 'Python, Postgres')

    def test_public_job_returns_404_when_not_published(self):
        # is_public defaults to False
        job = self._create_job(name='Internal Only', is_public=False)
        resp = self.url_open(f"{self.api_base}/{job.id}")
        self.assertEqual(resp.status_code, 404)
        body = json.loads(resp.content)
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'JOB_NOT_FOUND')

    def test_public_job_returns_404_when_job_does_not_exist(self):
        resp = self.url_open(f"{self.api_base}/9999999")
        self.assertEqual(resp.status_code, 404)
        body = json.loads(resp.content)
        self.assertFalse(body.get('success'))
        self.assertEqual(body['error']['code'], 'JOB_NOT_FOUND')

    def test_public_job_response_does_not_leak_internal_fields(self):
        job = self._create_job(name='Marketing Manager', is_public=True)
        # Set internal fields that should NOT appear in the public response
        job.write({'user_id': self.env.user.id, 'manager_id': self.env.user.id})
        resp = self.url_open(f"{self.api_base}/{job.id}")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)['data']
        # Confirm the public surface is restricted
        self.assertNotIn('user_id', data)
        self.assertNotIn('manager_id', data)
        self.assertNotIn('applicant_ids', data)
        self.assertNotIn('expected_employees', data)

    def test_public_job_includes_salary_range_when_set(self):
        job = self._create_job(name='Sales Lead', is_public=True)
        job.write({'salary_min': 1000.0, 'salary_max': 2500.0, 'salary_period': 'monthly'})
        resp = self.url_open(f"{self.api_base}/{job.id}")
        data = json.loads(resp.content)['data']
        self.assertEqual(data['salary_min'], 1000.0)
        self.assertEqual(data['salary_max'], 2500.0)
        self.assertEqual(data['salary_period'], 'monthly')

    def test_public_job_endpoint_is_unauthenticated(self):
        """No session-token / api-key headers — public endpoint must answer."""
        job = self._create_job(name='Customer Success', is_public=True)
        # Explicitly send no auth headers
        resp = self.url_open(f"{self.api_base}/{job.id}", headers={})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
