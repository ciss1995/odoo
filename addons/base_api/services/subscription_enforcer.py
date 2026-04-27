# -*- coding: utf-8 -*-
"""Subscription enforcement for multi-tenant SaaS.

This module is a plain Python class that runs inside each Odoo tenant container.
It fetches the tenant's plan info from the Control Plane and caches it.
Every API request in base_api calls its check methods before executing business logic.

Environment variables required:
- TENANT_ID: this tenant's slug (e.g., "acme-corp")
- CONTROL_PLANE_URL: internal URL of the Control Plane (e.g., "http://control-plane:8000")
- CONTROL_PLANE_TOKEN: Bearer token for authenticating with the Control Plane's internal API

When env vars are not set, get_instance() returns None and all enforcement is skipped.
This lets the existing single-tenant setup keep working without changes.
"""

import logging
import os
import threading
import time

import requests

_logger = logging.getLogger(__name__)


class SubscriptionEnforcer:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, tenant_id, control_plane_url, control_plane_token):
        self.tenant_id = tenant_id
        self.cp_url = control_plane_url.rstrip('/')
        self.cp_token = control_plane_token
        self._cache = None
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes

    @classmethod
    def get_instance(cls):
        """Get or create the singleton. Returns None if env vars are not set."""
        if cls._instance is not None:
            return cls._instance

        tenant_id = os.environ.get('TENANT_ID', '').strip()
        cp_url = os.environ.get('CONTROL_PLANE_URL', '').strip()
        cp_token = os.environ.get('CONTROL_PLANE_TOKEN', '').strip()

        if not tenant_id or not cp_url or not cp_token:
            return None

        with cls._lock:
            # Double-check after acquiring lock
            if cls._instance is None:
                cls._instance = cls(tenant_id, cp_url, cp_token)
        return cls._instance

    def get_tenant_info(self):
        """Fetch tenant info from Control Plane, with caching.

        Calls GET {cp_url}/internal/tenants/{tenant_id}/info
        Caches the response for _cache_ttl seconds.
        On network error, returns cached data if available, otherwise raises.
        """
        now = time.time()
        if self._cache is not None and (now - self._cache_timestamp) < self._cache_ttl:
            return self._cache

        url = f"{self.cp_url}/internal/tenants/{self.tenant_id}/info"
        headers = {"Authorization": f"Bearer {self.cp_token}"}

        try:
            resp = requests.get(url, headers=headers, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            self._cache = data
            self._cache_timestamp = now
            return data
        except Exception as exc:
            _logger.warning("Failed to fetch tenant info from Control Plane: %s", exc)
            if self._cache is not None:
                _logger.info("Using stale cached tenant info for %s", self.tenant_id)
                return self._cache
            raise RuntimeError(
                "Cannot reach Control Plane and no cached data available"
            ) from exc

    def invalidate_cache(self):
        """Clear the cached tenant info. Called when Control Plane pushes a plan change."""
        self._cache = None
        self._cache_timestamp = 0

    def check_subscription_active(self):
        """Check if the tenant's subscription is active and paid.

        Returns (True, None) if OK.
        Returns (False, error_dict) if not OK.
        """
        try:
            info = self.get_tenant_info()
        except RuntimeError:
            # Can't reach CP and no cache — fail open or closed?
            # Fail closed: deny access if we can't verify subscription.
            return False, {
                'message': 'Service temporarily unavailable. Please try again later.',
                'code': 'SERVICE_UNAVAILABLE',
                'status_code': 503,
            }

        status = info.get('status', '')
        payment_status = info.get('payment_status', '')

        # Active is OK
        if status == 'active' and payment_status in ('current', 'pending', None):
            return True, None

        # Trial — check if expired
        if status == 'trial':
            # Trial is OK as long as the CP reports it as trial (CP handles expiry)
            return True, None

        # Grace period — allow read-only (caller can check this separately)
        if status == 'grace_period':
            grace_days = info.get('grace_days_remaining')
            if grace_days is not None and grace_days > 0:
                return True, None
            return False, {
                'message': 'Your subscription grace period has expired. Please update your payment method.',
                'code': 'SUBSCRIPTION_EXPIRED',
                'status_code': 403,
            }

        # Suspended
        if status == 'suspended':
            return False, {
                'message': 'Your account has been suspended. Please contact support.',
                'code': 'ACCOUNT_SUSPENDED',
                'status_code': 403,
            }

        # Cancelled / deleted / other
        if status in ('cancelled', 'deleted'):
            return False, {
                'message': 'Your subscription has been cancelled.',
                'code': 'SUBSCRIPTION_CANCELLED',
                'status_code': 403,
            }

        # Payment overdue with no grace
        if payment_status == 'overdue':
            grace_days = info.get('grace_days_remaining')
            if grace_days is not None and grace_days > 0:
                return True, None
            return False, {
                'message': 'Your payment is overdue. Please update your payment method to continue.',
                'code': 'PAYMENT_OVERDUE',
                'status_code': 403,
            }

        # Provisioning — not yet ready
        if status == 'provisioning':
            return False, {
                'message': 'Your account is being set up. Please try again shortly.',
                'code': 'ACCOUNT_PROVISIONING',
                'status_code': 403,
            }

        return True, None

    def check_user_limit(self, current_active_user_count):
        """Check if the tenant can create another user.

        Uses effective.max_users from tenant info.
        -1 means unlimited.
        """
        try:
            info = self.get_tenant_info()
        except RuntimeError:
            return False, {
                'message': 'Service temporarily unavailable. Please try again later.',
                'code': 'SERVICE_UNAVAILABLE',
                'status_code': 503,
            }

        max_users = info.get('effective', {}).get('max_users', -1)
        if max_users == -1:
            return True, None

        if current_active_user_count >= max_users:
            return False, {
                'message': f'User limit reached ({max_users} users). Upgrade your plan to add more users.',
                'code': 'USER_LIMIT_REACHED',
                'status_code': 403,
            }

        return True, None

    def check_module_allowed(self, module_key):
        """Check if a module key is in the tenant's effective allowed_modules.

        '__all__' in allowed_modules means everything is allowed.
        """
        try:
            info = self.get_tenant_info()
        except RuntimeError:
            return False, {
                'message': 'Service temporarily unavailable. Please try again later.',
                'code': 'SERVICE_UNAVAILABLE',
                'status_code': 503,
            }

        allowed = info.get('effective', {}).get('allowed_modules', [])
        if '__all__' in allowed or module_key in allowed:
            return True, None

        return False, {
            'message': f"The '{module_key}' module is not included in your plan. Upgrade to access this feature.",
            'code': 'MODULE_NOT_IN_PLAN',
            'status_code': 403,
        }

    def check_api_quota(self):
        """Check if the tenant has API calls remaining this month.

        Uses effective.max_api_calls and usage.api_calls_this_month.
        -1 means unlimited.
        """
        try:
            info = self.get_tenant_info()
        except RuntimeError:
            return False, {
                'message': 'Service temporarily unavailable. Please try again later.',
                'code': 'SERVICE_UNAVAILABLE',
                'status_code': 503,
            }

        max_calls = info.get('effective', {}).get('max_api_calls', -1)
        if max_calls == -1:
            return True, None

        used = info.get('usage', {}).get('api_calls_this_month', 0)
        if used >= max_calls:
            return False, {
                'message': f'API quota exceeded ({max_calls} calls/month). Upgrade your plan for higher limits.',
                'code': 'API_QUOTA_EXCEEDED',
                'status_code': 429,
            }

        return True, None
