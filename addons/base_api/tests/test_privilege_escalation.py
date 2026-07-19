# -*- coding: utf-8 -*-
"""Privilege-escalation boundary tests for the base_api sudo() paths.

Covers the security findings hardened in the WhatsApp Phase-0 pass:

- F-036  create res.users: a non-system caller cannot mint/promote a user into
         the protected (system / erp_manager) groups, and cannot mass-assign
         fields outside the create whitelist.
- F-010  update / reset / change password: a non-system caller cannot grant the
         protected groups, nor act on a user who already holds one.
- F-035  inventory adjust / decrement: a user without stock rights is refused.
- F-014  client-IP derivation for the login rate-limiter ignores the spoofable
         leftmost X-Forwarded-For entry (pure-function unit tests).

These are the boundaries that were previously unasserted — every mutating path
here runs through sudo(), so a regression would silently re-open account
takeover / stock corruption.
"""

import json
import secrets
import string
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.base_api.services.rate_limiter import derive_client_ip


@tagged('post_install', '-at_install')
class TestUserPrivilegeEscalation(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable subscription enforcement so tests exercise authz, not billing.
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        Users = cls.env['res.users'].sudo()
        cls.g_system = cls.env.ref('base.group_system')
        cls.g_erp = cls.env.ref('base.group_erp_manager')
        cls.g_user = cls.env.ref('base.group_user')

        # A full-system admin (the account an attacker would try to take over).
        cls.system_admin = Users.create({
            'name': 'Sys Admin', 'login': 'sec_sysadmin',
            'password': 'sysadmin-pw',
            'group_ids': [(6, 0, [cls.g_system.id, cls.g_erp.id, cls.g_user.id])],
        })
        # A non-system "user manager" (erp_manager) — the attacker role.
        cls.erp_manager = Users.create({
            'name': 'ERP Manager', 'login': 'sec_erpmgr',
            'password': 'erpmgr-pw',
            'group_ids': [(6, 0, [cls.g_erp.id, cls.g_user.id])],
        })
        # A plain internal user with no elevated rights.
        cls.plain_user = Users.create({
            'name': 'Plain User', 'login': 'sec_plain',
            'password': 'plain-pw',
            'group_ids': [(6, 0, [cls.g_user.id])],
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
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

    def _req(self, method, path, token, body=None):
        headers = {'Content-Type': 'application/json', 'session-token': token}
        return self.url_open(
            path, method=method,
            data=json.dumps(body or {}), headers=headers,
        )

    def _body(self, resp):
        try:
            return resp.json()
        except Exception:
            return {}

    def _code(self, resp):
        """The standardized error code lives at body['error']['code']."""
        return self._body(resp).get('error', {}).get('code')

    # ===== F-036: create user =============================================

    def test_erp_manager_cannot_create_system_admin(self):
        token = self._token(self.erp_manager)
        resp = self._req('POST', '/api/v2/create/res.users', token, {
            'name': 'Smuggled Admin', 'login': 'sec_smuggled',
            'group_xml_ids': ['base.group_system'],
            'auto_generate_credentials': True,
        })
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'GROUP_ESCALATION_DENIED')
        # And nothing was created.
        self.assertFalse(
            self.env['res.users'].sudo().search([('login', '=', 'sec_smuggled')]),
            "escalated user must not be created",
        )

    def test_erp_manager_create_drops_mass_assigned_fields(self):
        token = self._token(self.erp_manager)
        resp = self._req('POST', '/api/v2/create/res.users', token, {
            'name': 'Whitelisted User', 'login': 'sec_whitelisted',
            'active': False,            # not in whitelist → dropped
            'group_xml_ids': ['base.group_user'],
        })
        self.assertEqual(resp.status_code, 201, self._body(resp))
        created = self.env['res.users'].sudo().search([('login', '=', 'sec_whitelisted')])
        self.assertTrue(created)
        self.assertTrue(created.active, "active:false must be dropped for a non-system caller")

    def test_system_admin_can_create_system_admin(self):
        # Positive control: a real system admin is still allowed to grant it.
        token = self._token(self.system_admin)
        resp = self._req('POST', '/api/v2/create/res.users', token, {
            'name': 'Legit Admin', 'login': 'sec_legit_admin',
            'group_xml_ids': ['base.group_system'],
        })
        self.assertEqual(resp.status_code, 201, self._body(resp))
        created = self.env['res.users'].sudo().search([('login', '=', 'sec_legit_admin')])
        self.assertTrue(created.has_group('base.group_system'))

    # ===== F-010: update user =============================================

    def test_erp_manager_cannot_grant_system_via_update(self):
        token = self._token(self.erp_manager)
        resp = self._req('PUT', f'/api/v2/users/{self.plain_user.id}', token, {
            'group_xml_ids': ['base.group_system'],
        })
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'GROUP_ESCALATION_DENIED')
        self.plain_user.invalidate_recordset()
        self.assertFalse(self.plain_user.has_group('base.group_system'))

    def test_erp_manager_cannot_edit_system_admin(self):
        token = self._token(self.erp_manager)
        resp = self._req('PUT', f'/api/v2/users/{self.system_admin.id}', token, {
            'name': 'Hijacked Name',
        })
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'PROTECTED_TARGET')

    # ===== F-010: reset / change password =================================

    def test_erp_manager_cannot_reset_system_admin_password(self):
        token = self._token(self.erp_manager)
        resp = self._req('POST', f'/api/v2/users/{self.system_admin.id}/reset-password', token)
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'PROTECTED_TARGET')

    def test_erp_manager_can_reset_plain_user_password(self):
        # Positive control: managing a non-protected user still works.
        token = self._token(self.erp_manager)
        resp = self._req('POST', f'/api/v2/users/{self.plain_user.id}/reset-password', token)
        self.assertEqual(resp.status_code, 200, self._body(resp))

    def test_erp_manager_cannot_change_system_admin_password(self):
        token = self._token(self.erp_manager)
        resp = self._req('PUT', f'/api/v2/users/{self.system_admin.id}/password', token, {
            'new_password': 'attacker-controlled',
        })
        self.assertEqual(resp.status_code, 403, self._body(resp))
        self.assertEqual(self._code(resp), 'PROTECTED_TARGET')


@tagged('post_install', '-at_install')
class TestInventoryAuthz(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        Users = cls.env['res.users'].sudo()
        g_user = cls.env.ref('base.group_user')
        cls.plain_user = Users.create({
            'name': 'Stockless User', 'login': 'sec_stockless',
            'password': 'x', 'group_ids': [(6, 0, [g_user.id])],
        })
        stock_mgr_group = cls.env.ref('stock.group_stock_manager', raise_if_not_found=False)
        cls.has_stock = bool(stock_mgr_group)
        if cls.has_stock:
            cls.stock_mgr = Users.create({
                'name': 'Stock Manager', 'login': 'sec_stockmgr',
                'password': 'x',
                'group_ids': [(6, 0, [g_user.id, stock_mgr_group.id])],
            })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

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

    def _post(self, path, token, body):
        return self.url_open(
            path, method='POST',
            data=json.dumps(body), headers={'Content-Type': 'application/json', 'session-token': token},
        )

    def test_stockless_user_cannot_adjust(self):
        if 'stock.quant' not in self.env:
            self.skipTest('stock not installed')
        token = self._token(self.plain_user)
        resp = self._post('/api/v2/inventory/adjust', token,
                          {'product_id': 1, 'new_quantity': 999999})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_stockless_user_cannot_decrement(self):
        if 'stock.quant' not in self.env:
            self.skipTest('stock not installed')
        token = self._token(self.plain_user)
        resp = self._post('/api/v2/inventory/decrement', token,
                          {'product_id': 1, 'quantity': 5, 'allow_negative': True})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_stock_manager_passes_authz_gate(self):
        if not self.has_stock:
            self.skipTest('stock not installed')
        token = self._token(self.stock_mgr)
        # Missing product → the request fails later, but must NOT be 403:
        # the point is the authorization gate lets a stock manager through.
        resp = self._post('/api/v2/inventory/adjust', token,
                          {'product_id': 999999999, 'new_quantity': 1})
        self.assertNotEqual(resp.status_code, 403, resp.text)


@tagged('post_install', '-at_install')
class TestClientIpDerivation(TransactionCase):
    """F-014: the login limiter must key on a non-spoofable client IP."""

    def test_ignores_spoofed_leftmost_xff(self):
        # Trusted proxy (Traefik) appends the real client as the rightmost hop.
        self.assertEqual(
            derive_client_ip('evil-spoof, 1.2.3.4', '10.0.0.1', hops=1),
            '1.2.3.4',
        )

    def test_rotating_leftmost_keeps_stable_bucket(self):
        # An attacker rotating the client-controlled leftmost value must still
        # land in the same limiter bucket (the real rightmost hop).
        a = derive_client_ip('attackerA, 1.2.3.4', '10.0.0.1', hops=1)
        b = derive_client_ip('attackerB, 1.2.3.4', '10.0.0.1', hops=1)
        self.assertEqual(a, b)
        self.assertEqual(a, '1.2.3.4')

    def test_falls_back_to_remote_addr(self):
        self.assertEqual(derive_client_ip('', '10.0.0.9'), '10.0.0.9')
        self.assertEqual(derive_client_ip(None, None), 'unknown')

    def test_two_trusted_hops(self):
        self.assertEqual(
            derive_client_ip('client, edge, traefik', 'x', hops=2),
            'edge',
        )

    def test_cf_connecting_ip_wins_when_trusted(self):
        self.assertEqual(
            derive_client_ip('evil', '10.0.0.1', cf_connecting_ip='9.9.9.9', trust_cf=True),
            '9.9.9.9',
        )

    def test_cf_ignored_when_not_trusted(self):
        # Without the trust flag the CF header is not consulted (can't be
        # forged into the bucket); we fall through to XFF parsing.
        self.assertEqual(
            derive_client_ip('1.2.3.4', '10.0.0.1', cf_connecting_ip='9.9.9.9', trust_cf=False),
            '1.2.3.4',
        )


@tagged('post_install', '-at_install')
class TestSessionRevocation(HttpCase):
    """F-018: a password change/reset must invalidate the target's sessions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)
        Users = cls.env['res.users'].sudo()
        g_sys = cls.env.ref('base.group_system')
        g_user = cls.env.ref('base.group_user')
        cls.admin = Users.create({
            'name': 'Revoke Admin', 'login': 'rev_admin', 'password': 'admin-pw',
            'group_ids': [(6, 0, [g_sys.id, g_user.id])],
        })
        cls.victim = Users.create({
            'name': 'Revoke Victim', 'login': 'rev_victim', 'password': 'victim-pw',
            'group_ids': [(6, 0, [g_user.id])],
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig
        super().tearDownClass()

    def _token(self, user):
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        self.env['api.session'].sudo().create({
            'user_id': user.id, 'token': self.env['api.session']._hash_token(token),
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(), 'last_activity': datetime.now(), 'active': True,
        })
        return token

    def _me(self, token):
        return self.url_open('/api/v2/auth/me', headers={'session-token': token})

    def test_admin_reset_revokes_target_sessions(self):
        victim_token = self._token(self.victim)
        self.assertEqual(self._me(victim_token).status_code, 200)  # token works
        admin_token = self._token(self.admin)
        resp = self.url_open(
            f'/api/v2/users/{self.victim.id}/reset-password', method='POST',
            data='{}', headers={'Content-Type': 'application/json', 'session-token': admin_token},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # The victim's stolen token is now dead.
        self.assertIn(self._me(victim_token).status_code, (401, 403))

    def test_own_change_keeps_current_revokes_others(self):
        keep = self._token(self.victim)
        other = self._token(self.victim)
        resp = self.url_open(
            f'/api/v2/users/{self.victim.id}/password', method='PUT',
            data=json.dumps({'new_password': 'victim-pw2', 'old_password': 'victim-pw'}),
            headers={'Content-Type': 'application/json', 'session-token': keep},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._me(keep).status_code, 200)          # caller kept
        self.assertIn(self._me(other).status_code, (401, 403))     # other revoked


@tagged('post_install', '-at_install')
class TestStepUpAuth(HttpCase):
    """0.2: step-up re-auth gate + /auth/reauth endpoint."""

    WINDOW_PARAM = 'base_api.step_up_window_seconds'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)
        Users = cls.env['res.users'].sudo()
        g_user = cls.env.ref('base.group_user')
        acct = cls.env.ref('account.group_account_manager', raise_if_not_found=False)
        cls.has_acct = bool(acct)
        groups = [g_user.id] + ([acct.id] if acct else [])
        cls.acct_user = Users.create({
            'name': 'Acct User', 'login': 'su_acct', 'password': 'acct-pw',
            'group_ids': [(6, 0, groups)],
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig
        super().tearDownClass()

    def tearDown(self):
        self.env['ir.config_parameter'].sudo().set_param(self.WINDOW_PARAM, '0')
        super().tearDown()

    def _set_window(self, seconds):
        self.env['ir.config_parameter'].sudo().set_param(self.WINDOW_PARAM, str(seconds))

    def _session(self, user, reauth_ago_seconds=0):
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        self.env['api.session'].sudo().create({
            'user_id': user.id, 'token': self.env['api.session']._hash_token(token),
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(), 'last_activity': datetime.now(), 'active': True,
            'last_reauth_at': datetime.now() - timedelta(seconds=reauth_ago_seconds),
        })
        return token

    def _post(self, path, token, body=None):
        return self.url_open(path, method='POST', data=json.dumps(body or {}),
                             headers={'Content-Type': 'application/json', 'session-token': token})

    def _code(self, resp):
        try:
            return resp.json().get('error', {}).get('code')
        except Exception:
            return None

    # -- /auth/reauth ------------------------------------------------------

    def test_reauth_requires_password(self):
        token = self._session(self.acct_user)
        resp = self._post('/api/v2/auth/reauth', token, {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._code(resp), 'MISSING_PASSWORD')

    def test_reauth_wrong_password(self):
        token = self._session(self.acct_user)
        resp = self._post('/api/v2/auth/reauth', token, {'password': 'nope'})
        self.assertEqual(resp.status_code, 401)

    def test_reauth_success_bumps_last_reauth(self):
        token = self._session(self.acct_user, reauth_ago_seconds=99999)
        resp = self._post('/api/v2/auth/reauth', token, {'password': 'acct-pw'})
        self.assertEqual(resp.status_code, 200, resp.text)
        sess = self.env['api.session'].sudo().search(
            [('token', '=', self.env['api.session']._hash_token(token))], limit=1)
        self.assertTrue(sess.last_reauth_at)
        self.assertLess((datetime.now() - sess.last_reauth_at).total_seconds(), 120)

    # -- step-up enforcement (fires before the money mechanics) ------------

    def test_step_up_blocks_stale_session(self):
        if not self.has_acct:
            self.skipTest('account not installed')
        self._set_window(300)
        token = self._session(self.acct_user, reauth_ago_seconds=3600)
        resp = self._post('/api/v2/account_move/999999999/register_payment', token, {})
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(self._code(resp), 'STEP_UP_REQUIRED')

    def test_step_up_disabled_allows(self):
        if not self.has_acct:
            self.skipTest('account not installed')
        self._set_window(0)  # disabled (default)
        token = self._session(self.acct_user, reauth_ago_seconds=3600)
        resp = self._post('/api/v2/account_move/999999999/register_payment', token, {})
        # Passes the (disabled) gate → fails later on the missing move, NOT step-up.
        self.assertNotEqual(self._code(resp), 'STEP_UP_REQUIRED')

    def test_reauth_clears_step_up(self):
        if not self.has_acct:
            self.skipTest('account not installed')
        self._set_window(300)
        token = self._session(self.acct_user, reauth_ago_seconds=3600)
        # Stale → blocked.
        self.assertEqual(self._code(self._post('/api/v2/account_move/999999999/register_payment', token, {})), 'STEP_UP_REQUIRED')
        # Re-auth, then the same call passes the gate.
        self.assertEqual(self._post('/api/v2/auth/reauth', token, {'password': 'acct-pw'}).status_code, 200)
        resp = self._post('/api/v2/account_move/999999999/register_payment', token, {})
        self.assertNotEqual(self._code(resp), 'STEP_UP_REQUIRED')


@tagged('post_install', '-at_install')
class TestForgotPasswordDelivery(HttpCase):
    """Phase 4: forgot-password must distinguish a real SMTP failure from an
    unknown login, while never leaking which case occurred to the client."""

    LOGGER = 'odoo.addons.base_api.controllers.simple_api'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)
        ICP = cls.env['ir.config_parameter'].sudo()
        cls._orig_reset = ICP.get_param('auth_signup.reset_password')
        ICP.set_param('auth_signup.reset_password', 'True')
        cls.user = cls.env['res.users'].sudo().create({
            'name': 'FP User', 'login': 'fp_user', 'email': 'fp_user@example.com',
            'password': 'fp-pw', 'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig
        cls.env['ir.config_parameter'].sudo().set_param(
            'auth_signup.reset_password', cls._orig_reset or 'False')
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Deterministic: start each test with an empty login-throttle bucket so
        # the shared 127.0.0.1 key can't spill over from other tests.
        from odoo.addons.base_api.services import rate_limiter
        rate_limiter._login_attempts.clear()

    def _forgot(self, login):
        return self.url_open(
            '/api/v2/auth/forgot-password', method='POST',
            data=json.dumps({'login': login}),
            headers={'Content-Type': 'application/json'},
        )

    def _res_users_cls(self):
        return type(self.env['res.users'])

    def test_unknown_login_returns_generic_200(self):
        resp = self._forgot('does-not-exist@nowhere.test')
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn('if an account', resp.json().get('message', '').lower())

    def test_known_user_send_failure_logs_error_but_returns_200(self):
        # Simulate a dead SMTP relay: reset_password raises.
        with patch.object(self._res_users_cls(), 'reset_password',
                          side_effect=Exception('SMTP relay down (test)')):
            with self.assertLogs(self.LOGGER, level='ERROR') as cm:
                resp = self._forgot('fp_user')
        # Client still gets the generic anti-enumeration 200 ...
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn('if an account', resp.json().get('message', '').lower())
        # ... but the failure is now visible/alertable.
        self.assertTrue(
            any('FAILED to send' in m for m in cm.output),
            f"expected an ERROR log for the send failure, got: {cm.output}",
        )

    def test_known_user_success_returns_200(self):
        # Successful send: reset_password is a no-op, no exception mail created.
        with patch.object(self._res_users_cls(), 'reset_password', return_value=None):
            resp = self._forgot('fp_user')
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn('if an account', resp.json().get('message', '').lower())
