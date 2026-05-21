# -*- coding: utf-8 -*-
"""Integration tests for Idempotency-Key support on /api/v2/create/*.

The contract:
- Same key + same body twice → second call replays the first response;
  exactly one record exists in the DB.
- Same key + different body → second call returns 409 (client bug).
- No key → behaves as before, every call creates a new record.
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestIdempotencyKey(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.create_url = '/api/v2/create/res.partner'

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    def _login(self):
        admin = self.env.ref('base.user_admin')
        session_token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        token_hash = self.env['api.session']._hash_token(session_token)
        self.env['api.session'].sudo().create({
            'user_id': admin.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return session_token

    def _post(self, token, body, idempotency_key=None):
        headers = {
            'session-token': token,
            'Content-Type': 'application/json',
        }
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        return self.url_open(self.create_url, data=json.dumps(body), headers=headers)

    def test_same_key_same_body_creates_one_record(self):
        token = self._login()
        key = 'idem-' + ''.join(secrets.choice(string.ascii_letters) for _ in range(24))
        body = {'name': 'Idempotent Partner ' + key[-6:]}

        r1 = self._post(token, body, idempotency_key=key)
        r2 = self._post(token, body, idempotency_key=key)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        d1, d2 = r1.json(), r2.json()
        # Replay means the second response is byte-equivalent.
        self.assertEqual(d1.get('data', {}).get('id'), d2.get('data', {}).get('id'),
                         "Second call should replay the first record's id")

        # And exactly one record was actually written.
        partners = self.env['res.partner'].sudo().search([('name', '=', body['name'])])
        self.assertEqual(len(partners), 1, f"Expected 1 record, found {len(partners)}")

    def test_same_key_different_body_returns_409(self):
        token = self._login()
        key = 'idem-' + ''.join(secrets.choice(string.ascii_letters) for _ in range(24))

        r1 = self._post(token, {'name': 'First'}, idempotency_key=key)
        r2 = self._post(token, {'name': 'Different Body'}, idempotency_key=key)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 409,
                         "Reusing the key with a different body must 409")
        self.assertIn('IDEMPOTENCY', r2.json().get('error', {}).get('code', ''))

    def test_no_key_still_creates_multiple_records(self):
        """The header is opt-in — without it the endpoint must behave as before."""
        token = self._login()
        body = {'name': 'Non-Idempotent Partner ' + secrets.token_hex(4)}

        r1 = self._post(token, body)
        r2 = self._post(token, body)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        # Two separate records — different ids.
        self.assertNotEqual(r1.json()['data']['id'], r2.json()['data']['id'])

    def test_invalid_key_format_rejected(self):
        token = self._login()
        # Path-traversal-shaped key — must 400.
        r = self._post(token, {'name': 'X'}, idempotency_key='../../etc/passwd')
        self.assertEqual(r.status_code, 400)
        self.assertIn('IDEMPOTENCY_KEY_INVALID', r.json().get('error', {}).get('code', ''))

    def test_oversized_key_rejected(self):
        token = self._login()
        r = self._post(token, {'name': 'X'}, idempotency_key='a' * 65)
        self.assertEqual(r.status_code, 400)
