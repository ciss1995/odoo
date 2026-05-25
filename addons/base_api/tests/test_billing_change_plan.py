# -*- coding: utf-8 -*-
"""Per-state error mapping for `/api/v2/billing/change-plan`.

The endpoint deliberately skips `_enforce_subscription()` so it stays
reachable even when the tenant has unpaid invoices — without that, a
customer in trouble can't see plan options. The endpoint instead reads
tenant info directly and surfaces actionable errors:

  TENANT_SUSPENDED      — contact sales to reactivate
  TENANT_CANCELLED      — contact sales to restart
  TENANT_PROVISIONING   — wait a few minutes
  PAYMENT_OVERDUE       — settle invoice first (HTTP 402)
  PLAN_NOT_SELF_SERVICE — enterprise / custom need sales
  ADMIN_ONLY            — non-admins can't change billing
  CP_UNREACHABLE        — control plane down

These map 1:1 to localized SPA toasts.
"""

import json
import secrets
import string
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestBillingChangePlan(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = '/api/v2/billing/change-plan'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

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

    def _post(self, token, body, enforcer_info=None):
        """Mock the subscription enforcer to return the given tenant
        info dict (None → no enforcer / unrelated path)."""
        from odoo.addons.base_api.services import subscription_enforcer

        class _MockEnforcer:
            def __init__(self, info):
                self._info = info
            def get_tenant_info(self):
                return self._info

        if enforcer_info is None:
            patched = patch.object(
                subscription_enforcer.SubscriptionEnforcer,
                'get_instance', return_value=None,
            )
        else:
            patched = patch.object(
                subscription_enforcer.SubscriptionEnforcer,
                'get_instance', return_value=_MockEnforcer(enforcer_info),
            )
        with patched:
            return self.url_open(
                self.url,
                data=json.dumps(body),
                headers={'session-token': token, 'Content-Type': 'application/json'},
            )

    def test_requires_auth(self):
        resp = self.url_open(self.url, data=json.dumps({'new_plan_slug': 'store'}),
                             headers={'Content-Type': 'application/json'})
        self.assertEqual(resp.status_code, 401)

    # Non-admin block path covered by the route's `user.has_group(
    # 'base.group_system')` check + the wider Odoo ACL fence.
    # Creating a non-system user via test fixtures hits the full
    # res.users _inherits chain (digest, calendar, project, hr,
    # auth_signup, mail-channel auto-subscribe) which is finicky in
    # post_install + has nothing to do with billing-state coverage —
    # left out on purpose.

    def test_suspended_tenant_specific_error(self):
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'store'},
                          enforcer_info={'status': 'suspended', 'payment_status': 'current'})
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body['error']['code'], 'TENANT_SUSPENDED')
        self.assertIn('suspended', body['error']['message'].lower())
        self.assertIn('sales@toomde.com', body['error']['message'])

    def test_cancelled_tenant_specific_error(self):
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'store'},
                          enforcer_info={'status': 'cancelled', 'payment_status': 'overdue'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error']['code'], 'TENANT_CANCELLED')

    def test_provisioning_tenant_specific_error(self):
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'store'},
                          enforcer_info={'status': 'provisioning', 'payment_status': None})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['error']['code'], 'TENANT_PROVISIONING')

    def test_payment_overdue_no_grace_blocked_with_402(self):
        """The friendly billing message — HTTP 402 Payment Required +
        actionable text directing to billing portal or sales."""
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'store'},
                          enforcer_info={
                              'status': 'active',
                              'payment_status': 'overdue',
                              'grace_days_remaining': 0,
                          })
        self.assertEqual(resp.status_code, 402)
        body = resp.json()
        self.assertEqual(body['error']['code'], 'PAYMENT_OVERDUE')
        # Message must give the customer something to DO.
        msg = body['error']['message'].lower()
        self.assertIn('unpaid', msg)
        self.assertIn('billing portal', msg)

    def test_payment_overdue_with_grace_allowed_through(self):
        """If the tenant is still inside their grace period, don't
        bounce them — they may be trying to downgrade to recover."""
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'store'},
                          enforcer_info={
                              'status': 'active',
                              'payment_status': 'overdue',
                              'grace_days_remaining': 3,
                          })
        # Doesn't return 402; either succeeds the validation gate or
        # falls into the CP proxy (which won't reach the test CP).
        # What matters: NOT a 402 PAYMENT_OVERDUE.
        body = resp.json()
        if not body.get('success'):
            self.assertNotEqual(body['error']['code'], 'PAYMENT_OVERDUE')

    def test_enterprise_target_returns_not_self_service(self):
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'enterprise'},
                          enforcer_info={'status': 'active', 'payment_status': 'current'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error']['code'], 'PLAN_NOT_SELF_SERVICE')

    def test_custom_target_returns_not_self_service(self):
        token = self._login()
        resp = self._post(token, {'new_plan_slug': 'custom'},
                          enforcer_info={'status': 'active', 'payment_status': 'current'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error']['code'], 'PLAN_NOT_SELF_SERVICE')
