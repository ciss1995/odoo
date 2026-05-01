# -*- coding: utf-8 -*-
"""Integration tests for /api/v2/avatars/res.partner/<id>."""

import base64
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


# 1x1 transparent PNG
_TINY_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4'
    b'2mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
)


@tagged('post_install', '-at_install')
class TestAvatarsEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2/avatars'
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

    def _make_partner_with_image(self, name='Avatar Test'):
        return self.env['res.partner'].sudo().create({
            'name': name,
            'image_1920': base64.b64encode(_TINY_PNG),
        })

    def _make_partner_without_image(self, name='No Avatar'):
        return self.env['res.partner'].sudo().create({
            'name': name,
            'image_1920': False,
        })

    def _get(self, partner_id, token=None, params=None, headers_extra=None):
        qs = ''
        if params:
            qs = '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        headers = {}
        if token:
            headers['session-token'] = token
        if headers_extra:
            headers.update(headers_extra)
        return self.url_open(
            f'{self.api_base}/res.partner/{partner_id}{qs}',
            headers=headers,
        )

    # ===== auth ===============================================================

    def test_requires_session_token(self):
        partner = self._make_partner_with_image()
        resp = self._get(partner.id)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['error']['code'], 'UNAUTHORIZED')

    def test_invalid_token_rejected(self):
        partner = self._make_partner_with_image()
        resp = self._get(partner.id, token='nope')
        self.assertEqual(resp.status_code, 401)

    # ===== happy path =========================================================

    def test_returns_image_bytes(self):
        token = self._login()
        partner = self._make_partner_with_image()
        resp = self._get(partner.id, token=token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp.headers.get('Content-Type', ''),
                      ('image/png', 'image/jpeg'))
        self.assertGreater(len(resp.content), 0)
        self.assertIn('ETag', resp.headers)
        self.assertIn('private', resp.headers.get('Cache-Control', ''))

    def test_etag_returns_304_on_match(self):
        token = self._login()
        partner = self._make_partner_with_image()

        first = self._get(partner.id, token=token)
        self.assertEqual(first.status_code, 200)
        etag = first.headers.get('ETag')
        self.assertTrue(etag)

        second = self._get(
            partner.id, token=token,
            headers_extra={'If-None-Match': etag},
        )
        self.assertEqual(second.status_code, 304)
        self.assertEqual(len(second.content), 0)

    def test_size_param_accepted(self):
        token = self._login()
        partner = self._make_partner_with_image()
        resp = self._get(partner.id, token=token, params={'size': '256'})
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.content), 0)

    # ===== error paths ========================================================

    def test_missing_partner_returns_404(self):
        token = self._login()
        resp = self._get(999999999, token=token)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error']['code'], 'NOT_FOUND')

    def test_partner_without_image_returns_404(self):
        token = self._login()
        partner = self._make_partner_without_image()
        resp = self._get(partner.id, token=token)
        # Odoo may auto-fill a placeholder; if so we get 200, otherwise 404.
        # Either is acceptable but the response must not 500.
        self.assertIn(resp.status_code, (200, 404))

    def test_invalid_size_returns_400(self):
        token = self._login()
        partner = self._make_partner_with_image()
        resp = self._get(partner.id, token=token, params={'size': '999'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')
