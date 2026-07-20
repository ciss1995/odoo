# -*- coding: utf-8 -*-
"""Tests for the phone + PIN identity (WhatsApp entry point, Phase 2).

Two layers:

- Model (``api.phone_identity``): PIN hashing never stores plaintext, PIN-format
  policy, enrollment idempotency, and the anti-enumeration / lockout logic in
  ``authenticate_phone`` — asserted deterministically without HTTP.
- Endpoint (``/api/v2/auth/phone-login`` + ``/auth/phone-enroll``): a phone+PIN
  mints a working session, wrong-PIN and unknown-phone return the *same* generic
  401, lockout surfaces 429 PHONE_LOCKED, and enrollment is manager-gated and
  cannot target a protected (admin) user.

The per-IP login limiter is disabled here (BASE_API_TEST_MODE) so the endpoint
tests exercise the durable per-phone lockout rather than the IP throttle.
"""

import json
import os
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.base_api.models.phone_identity import AUTH_OK, AUTH_INVALID, AUTH_LOCKED


# ======================================================================
# Model-level: hashing, policy, lockout, anti-enumeration
# ======================================================================
@tagged('post_install', '-at_install')
class TestPhoneIdentityModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Identity = cls.env['api.phone_identity']
        cls.user = cls.env['res.users'].sudo().create({
            'name': 'Phone User', 'login': 'phone_user_model',
            'password': 'model-pw',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.phone = '+221771234567'

    def _enroll(self, pin='4729', phone=None):
        return self.Identity.enroll(self.user.id, phone or self.phone, pin)

    def test_pin_is_hashed_not_plaintext(self):
        identity = self._enroll(pin='4729')
        self.assertTrue(identity.pin_hash)
        self.assertNotIn('4729', identity.pin_hash)
        self.assertTrue(identity.pin_hash.startswith('$pbkdf2-sha256$'))
        self.assertTrue(identity._verify_pin('4729'))
        self.assertFalse(identity._verify_pin('0000'))

    def test_pin_format_policy(self):
        v = self.Identity._validate_pin_format
        self.assertIsNone(v('4729'))
        self.assertIsNone(v('83947'))
        self.assertIsNotNone(v(''))            # required
        self.assertIsNotNone(v('12ab'))        # non-digit
        self.assertIsNotNone(v('123'))         # too short
        self.assertIsNotNone(v('123456789'))   # too long
        self.assertIsNotNone(v('1111'))        # all-same
        self.assertIsNotNone(v('1234'))        # trivial sequence

    def test_enroll_is_idempotent_and_rebinds(self):
        first = self._enroll(pin='4729')
        again = self._enroll(pin='8080')
        self.assertEqual(first, again, "same phone+company must reuse the record")
        self.assertEqual(
            self.Identity.search_count([('phone_e164', '=', self.phone)]), 1,
        )
        self.assertTrue(again._verify_pin('8080'))
        self.assertFalse(again._verify_pin('4729'), "old PIN must stop working")

    def test_authenticate_success_resets_counters(self):
        identity = self._enroll(pin='4729')
        identity.sudo().write({'failed_attempts': 3})
        code, got = self.Identity.authenticate_phone(self.phone, '4729')
        self.assertEqual(code, AUTH_OK)
        self.assertEqual(got, identity)
        self.assertEqual(identity.failed_attempts, 0)
        self.assertTrue(identity.last_login_at)

    def test_authenticate_wrong_pin_increments(self):
        identity = self._enroll(pin='4729')
        code, got = self.Identity.authenticate_phone(self.phone, '0000')
        self.assertEqual(code, AUTH_INVALID)
        self.assertEqual(got, identity)
        self.assertEqual(identity.failed_attempts, 1)

    def test_authenticate_unknown_phone_is_indistinguishable(self):
        code, got = self.Identity.authenticate_phone('+221770000000', '4729')
        self.assertEqual(code, AUTH_INVALID, "unknown phone == wrong PIN result code")
        self.assertIsNone(got, "unknown phone must not leak a record")

    def test_lockout_after_threshold(self):
        identity = self._enroll(pin='4729')
        last_code = None
        for _ in range(self.Identity.LOCK_THRESHOLD):
            last_code, _ = self.Identity.authenticate_phone(self.phone, '0000')
        self.assertEqual(last_code, AUTH_LOCKED)
        self.assertTrue(identity._is_locked())
        # Even the correct PIN is refused while locked.
        code, _ = self.Identity.authenticate_phone(self.phone, '4729')
        self.assertEqual(code, AUTH_LOCKED)

    def test_lockout_clears_after_window(self):
        identity = self._enroll(pin='4729')
        for _ in range(self.Identity.LOCK_THRESHOLD):
            self.Identity.authenticate_phone(self.phone, '0000')
        self.assertTrue(identity._is_locked())
        # Simulate the lock window elapsing.
        identity.sudo().write({'locked_until': datetime.now() - timedelta(seconds=1)})
        self.assertFalse(identity._is_locked())
        code, _ = self.Identity.authenticate_phone(self.phone, '4729')
        self.assertEqual(code, AUTH_OK)


# ======================================================================
# Endpoint-level: /auth/phone-login + /auth/phone-enroll
# ======================================================================
@tagged('post_install', '-at_install')
class TestPhoneLoginEndpoints(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable subscription enforcement (authz, not billing) and the per-IP
        # login limiter (so we exercise the durable per-phone lockout).
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)
        cls._orig_test_mode = os.environ.get('BASE_API_TEST_MODE')
        os.environ['BASE_API_TEST_MODE'] = '1'

        Users = cls.env['res.users'].sudo()
        cls.g_system = cls.env.ref('base.group_system')
        cls.g_erp = cls.env.ref('base.group_erp_manager')
        cls.g_user = cls.env.ref('base.group_user')

        cls.system_admin = Users.create({
            'name': 'Sys Admin', 'login': 'ph_sysadmin', 'password': 'sysadmin-pw',
            'group_ids': [(6, 0, [cls.g_system.id, cls.g_erp.id, cls.g_user.id])],
        })
        cls.erp_manager = Users.create({
            'name': 'ERP Manager', 'login': 'ph_erpmgr', 'password': 'erpmgr-pw',
            'group_ids': [(6, 0, [cls.g_erp.id, cls.g_user.id])],
        })
        cls.plain_user = Users.create({
            'name': 'Plain User', 'login': 'ph_plain', 'password': 'plain-pw',
            'group_ids': [(6, 0, [cls.g_user.id])],
        })
        # A user with an enrolled phone for the login tests.
        cls.phone_user = Users.create({
            'name': 'Phone Owner', 'login': 'ph_owner', 'password': 'owner-pw',
            'group_ids': [(6, 0, [cls.g_user.id])],
        })
        cls.phone = '+221781112233'
        cls.env['api.phone_identity'].sudo().enroll(cls.phone_user.id, cls.phone, '4729')

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        if cls._orig_test_mode is None:
            os.environ.pop('BASE_API_TEST_MODE', None)
        else:
            os.environ['BASE_API_TEST_MODE'] = cls._orig_test_mode
        super().tearDownClass()

    # -- helpers -----------------------------------------------------------
    def _token(self, user):
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': self.env['api.session']._hash_token(token),
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return token

    def _post(self, path, body, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(path, method='POST', data=json.dumps(body), headers=headers)

    def _body(self, resp):
        try:
            return resp.json()
        except Exception:
            return {}

    def _code(self, resp):
        return self._body(resp).get('error', {}).get('code')

    def _reset_identity(self):
        """Clear lockout/attempt state on the shared login identity."""
        self.env['api.phone_identity'].sudo().search(
            [('phone_e164', '=', self.phone)]
        ).write({'failed_attempts': 0, 'locked_until': False})

    # -- login -------------------------------------------------------------
    def test_phone_login_success_mints_session(self):
        self._reset_identity()
        resp = self._post('/api/v2/auth/phone-login', {'phone_e164': self.phone, 'pin': '4729'})
        self.assertEqual(resp.status_code, 200, self._body(resp))
        data = self._body(resp)['data']
        self.assertTrue(data['session_token'])
        self.assertEqual(data['user']['login'], 'ph_owner')
        # The minted token resolves to a live session for the right user.
        session = self.env['api.session'].sudo().search([
            ('token', '=', self.env['api.session']._hash_token(data['session_token'])),
            ('active', '=', True),
        ], limit=1)
        self.assertTrue(session)
        self.assertEqual(session.user_id, self.phone_user)

    def test_phone_login_accepts_wa_id_form(self):
        # WhatsApp sends the sender number without a leading '+'.
        self._reset_identity()
        resp = self._post('/api/v2/auth/phone-login',
                          {'phone_e164': '221781112233', 'pin': '4729', 'wa_id': '221781112233'})
        self.assertEqual(resp.status_code, 200, self._body(resp))
        token = self._body(resp)['data']['session_token']
        session = self.env['api.session'].sudo().search([
            ('token', '=', self.env['api.session']._hash_token(token)),
        ], limit=1)
        self.assertEqual(session.wa_id, '221781112233')

    def test_phone_login_wrong_pin_generic_401(self):
        self._reset_identity()
        resp = self._post('/api/v2/auth/phone-login', {'phone_e164': self.phone, 'pin': '0000'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._code(resp), 'INVALID_PHONE_CREDENTIALS')

    def test_phone_login_unknown_phone_same_as_wrong_pin(self):
        resp = self._post('/api/v2/auth/phone-login',
                          {'phone_e164': '+221770000000', 'pin': '4729'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._code(resp), 'INVALID_PHONE_CREDENTIALS',
                         "unknown phone must be indistinguishable from a wrong PIN")

    def test_phone_login_missing_fields(self):
        resp = self._post('/api/v2/auth/phone-login', {'phone_e164': self.phone})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._code(resp), 'MISSING_CREDENTIALS')

    def test_phone_login_lockout_returns_429(self):
        self._reset_identity()
        last = None
        for _ in range(5):
            last = self._post('/api/v2/auth/phone-login', {'phone_e164': self.phone, 'pin': '0000'})
        self.assertEqual(last.status_code, 429, self._body(last))
        self.assertEqual(self._code(last), 'PHONE_LOCKED')
        self.assertIn('Retry-After', last.headers)
        # Correct PIN is refused while locked.
        resp = self._post('/api/v2/auth/phone-login', {'phone_e164': self.phone, 'pin': '4729'})
        self.assertEqual(resp.status_code, 429)
        self._reset_identity()

    # -- enroll ------------------------------------------------------------
    def test_enroll_requires_manager(self):
        token = self._token(self.plain_user)
        resp = self._post('/api/v2/auth/phone-enroll',
                          {'login': 'ph_plain', 'phone_e164': '+221780000001', 'pin': '5561'}, token)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._code(resp), 'FORBIDDEN')

    def test_manager_can_enroll_plain_user(self):
        token = self._token(self.erp_manager)
        resp = self._post('/api/v2/auth/phone-enroll',
                          {'user_id': self.plain_user.id, 'phone_e164': '+221780000002', 'pin': '5561'}, token)
        self.assertEqual(resp.status_code, 200, self._body(resp))
        identity = self.env['api.phone_identity'].sudo().search(
            [('phone_e164', '=', '+221780000002')])
        self.assertEqual(identity.user_id, self.plain_user)
        # And that enrollment actually works for login.
        self._post('/api/v2/auth/phone-login', {'phone_e164': '+221780000002', 'pin': '5561'})

    def test_manager_cannot_enroll_admin_user(self):
        # The escalation guard: an erp_manager must not set a PIN on a system admin.
        token = self._token(self.erp_manager)
        resp = self._post('/api/v2/auth/phone-enroll',
                          {'user_id': self.system_admin.id, 'phone_e164': '+221780000003', 'pin': '5561'}, token)
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'PROTECTED_TARGET')
        self.assertFalse(
            self.env['api.phone_identity'].sudo().search([('phone_e164', '=', '+221780000003')]),
            "no identity may be created for a protected target",
        )

    def test_enroll_rejects_weak_pin(self):
        token = self._token(self.erp_manager)
        resp = self._post('/api/v2/auth/phone-enroll',
                          {'user_id': self.plain_user.id, 'phone_e164': '+221780000004', 'pin': '1111'}, token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._code(resp), 'WEAK_PIN')
