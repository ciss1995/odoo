# -*- coding: utf-8 -*-
"""Base controller with shared auth/response/enforcement helpers.

Newer controllers (notifications, future modules) inherit from this.
SimpleApiController in simple_api.py keeps its own response/enforcement
copies (different surface area, harder to deduplicate) but both controllers
now share the same _authenticate_session via services.auth.
"""

import json
import logging
import time as _time

from odoo import http
from odoo.http import request

from odoo.addons.base_api.services.auth import authenticate_session


_logger = logging.getLogger(__name__)


class BaseApiController(http.Controller):
    """Shared helpers: auth, response shapes, subscription/quota enforcement."""

    # ----- Response builders --------------------------------------------------

    def _json_response(self, data=None, success=True, message=None, status_code=200):
        body = {'success': success, 'data': data, 'message': message}
        resp = request.make_response(
            json.dumps(body, default=str),
            headers=[('Content-Type', 'application/json')],
        )
        resp.status_code = status_code
        self._log_api_call(status_code)
        return resp

    def _error_response(self, message, status_code=400, error_code=None):
        body = {
            'success': False,
            'error': {'message': message, 'code': error_code},
        }
        resp = request.make_response(
            json.dumps(body, default=str),
            headers=[('Content-Type', 'application/json')],
        )
        resp.status_code = status_code
        self._log_api_call(status_code)
        return resp

    # ----- Authentication -----------------------------------------------------

    def _authenticate_session(self):
        """Validate the session and switch env to that user.

        Header-wins-with-cookie-fallback. See services.auth for full semantics.
        Returns (True, user) on success; (False, error_response) otherwise.
        """
        return authenticate_session(self._error_response, self._enforce_user_rate_limit)

    # ----- Enforcement --------------------------------------------------------

    def _get_enforcer(self):
        from odoo.addons.base_api.services.subscription_enforcer import (
            SubscriptionEnforcer,
        )
        return SubscriptionEnforcer.get_instance()

    def _enforce_subscription(self):
        enforcer = self._get_enforcer()
        if enforcer is None:
            return None
        allowed, error = enforcer.check_subscription_active()
        if not allowed:
            return self._error_response(
                error['message'], error['status_code'], error['code'],
            )
        return None

    def _enforce_api_quota(self):
        enforcer = self._get_enforcer()
        if enforcer is None:
            return None
        allowed, error = enforcer.check_api_quota()
        if not allowed:
            return self._error_response(
                error['message'], error['status_code'], error['code'],
            )
        return None

    def _enforce_user_rate_limit(self, user):
        from odoo.addons.base_api.services.rate_limiter import (
            check_api_rate_limit,
        )
        allowed, retry_after, _remaining = check_api_rate_limit(user.id)
        if not allowed:
            resp = self._error_response(
                f"Rate limit exceeded. Try again in {retry_after} seconds.",
                429, "RATE_LIMITED",
            )
            resp.headers['Retry-After'] = str(retry_after)
            resp.headers['X-RateLimit-Limit'] = str(120)
            resp.headers['X-RateLimit-Remaining'] = '0'
            return resp
        return None

    # ----- Logging ------------------------------------------------------------

    def _log_api_call(self, status_code):
        start = getattr(request.httprequest, '_api_start_time', None)
        if start is None:
            return
        try:
            from odoo.addons.base_api.services.api_call_logger import (
                ApiCallLogger,
            )
            logger = ApiCallLogger.get_instance()
            if logger is not None:
                response_ms = int((_time.time() - start) * 1000)
                method = request.httprequest.method or 'GET'
                logger.log_call(method, status_code, response_ms)
        except Exception:
            pass  # best-effort, never break the response
