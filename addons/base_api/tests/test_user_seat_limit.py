# -*- coding: utf-8 -*-
"""Unit tests for `SubscriptionEnforcer.check_user_limit`.

Exercises every branch of the per-plan seat cap that gates
`/api/v2/create/res.users` in `_create_user_with_groups`:

  - unlimited (max_users == -1) → always allowed
  - under cap → allowed
  - at cap → blocked with USER_LIMIT_REACHED, message mentions
    deactivate/remove + upgrade
  - over cap (legacy data) → blocked the same way
  - CP unreachable → 503 SERVICE_UNAVAILABLE (fail-closed)

The error message is also surfaced verbatim through the SPA's
toast in NewUserDialog / NewEmployeeDialog when the SPA can't
match the i18n key — keep both copies in sync.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.base_api.services.subscription_enforcer import (
    SubscriptionEnforcer,
)


def _make_enforcer(tenant_info):
    """Build an enforcer instance whose `get_tenant_info` returns the
    provided dict (no network)."""
    enforcer = SubscriptionEnforcer(
        tenant_id="test",
        control_plane_url="http://cp.test",
        control_plane_token="token",
    )
    patcher = patch.object(enforcer, "get_tenant_info", return_value=tenant_info)
    patcher.start()
    return enforcer, patcher


@tagged("post_install", "-at_install")
class TestUserSeatLimit(TransactionCase):

    def tearDown(self):
        super().tearDown()
        patch.stopall()

    def test_unlimited_plan_always_allowed(self):
        """max_users == -1 means no cap (custom & legacy enterprise)."""
        enforcer, _ = _make_enforcer({"effective": {"max_users": -1}})
        allowed, err = enforcer.check_user_limit(current_active_user_count=999)
        self.assertTrue(allowed)
        self.assertIsNone(err)

    def test_under_cap_allowed(self):
        enforcer, _ = _make_enforcer({"effective": {"max_users": 5}})
        allowed, err = enforcer.check_user_limit(current_active_user_count=4)
        self.assertTrue(allowed)
        self.assertIsNone(err)

    def test_at_cap_blocked_with_clear_message(self):
        """The headline use case — tenant has used every seat."""
        enforcer, _ = _make_enforcer({"effective": {"max_users": 3}})
        allowed, err = enforcer.check_user_limit(current_active_user_count=3)
        self.assertFalse(allowed)
        self.assertEqual(err["code"], "USER_LIMIT_REACHED")
        self.assertEqual(err["status_code"], 403)
        # The message must call out the "remove a user first" action
        # explicitly — that's what the operator needs to know.
        msg = err["message"]
        self.assertIn("3/3 users", msg)
        self.assertIn("Deactivate", msg)
        self.assertIn("remove", msg)
        self.assertIn("upgrade", msg)

    def test_over_cap_legacy_data_still_blocks(self):
        """Existing tenants with 7 active users on a 5-seat plan after
        a plan downgrade — block any further additions, message
        reflects actual usage."""
        enforcer, _ = _make_enforcer({"effective": {"max_users": 5}})
        allowed, err = enforcer.check_user_limit(current_active_user_count=7)
        self.assertFalse(allowed)
        self.assertIn("7/5 users", err["message"])

    def test_cp_unreachable_fails_closed(self):
        """If we can't reach Control Plane to verify the cap, refuse
        new users rather than guess at the limit."""
        enforcer = SubscriptionEnforcer(
            tenant_id="test",
            control_plane_url="http://cp.test",
            control_plane_token="token",
        )
        with patch.object(
            enforcer, "get_tenant_info",
            side_effect=RuntimeError("CP unreachable"),
        ):
            allowed, err = enforcer.check_user_limit(current_active_user_count=1)
        self.assertFalse(allowed)
        self.assertEqual(err["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(err["status_code"], 503)
