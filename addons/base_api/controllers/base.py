# -*- coding: utf-8 -*-
"""Base controller with shared auth/response/enforcement helpers.

Newer controllers (notifications, future modules) inherit from this. The
existing simple_api.py keeps its inline copies of these helpers — we will
unify in a separate refactor PR.
"""

import json
import logging
import time as _time
from datetime import datetime

from odoo import http
from odoo.http import request


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
        """Validate the session-token header and switch env to that user.

        Returns (True, user) on success; (False, error_response) otherwise.
        """
        request.httprequest._api_start_time = _time.time()
        token = request.httprequest.headers.get('session-token')
        if not token:
            return False, self._error_response(
                "Session token required", 401, "UNAUTHORIZED",
            )
        try:
            token_hash = request.env['api.session']._hash_token(token)
            session = request.env['api.session'].sudo().search([
                ('token', '=', token_hash),
                ('active', '=', True),
                ('expires_at', '>', datetime.now()),
            ], limit=1)
            if not session:
                return False, self._error_response(
                    "Invalid or expired session", 401, "UNAUTHORIZED",
                )
            # Best-effort last_activity bump. GET routes run on a read-only
            # cursor in Odoo 18+, so the write may fail; wrap in a savepoint
            # so the failure stays contained and the cursor remains usable.
            try:
                with request.env.cr.savepoint():
                    session.sudo().write({'last_activity': datetime.now()})
            except Exception as write_error:
                _logger.debug(
                    "Skipped session last_activity bump: %s", write_error,
                )
            rate_error = self._enforce_user_rate_limit(session.user_id)
            if rate_error:
                return False, rate_error
            request.update_env(user=session.user_id.id)
            return True, session.user_id
        except Exception as e:
            _logger.error("Session authentication error: %s", e)
            return False, self._error_response(
                "Session authentication failed", 500, "AUTH_ERROR",
            )

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
