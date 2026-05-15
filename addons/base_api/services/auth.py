# -*- coding: utf-8 -*-

"""Shared session authentication for /api/v2/* controllers.

Single source of truth for header-wins-with-cookie-fallback session auth.
Previously each controller carried an inline copy and they drifted — the
cookie migration only updated SimpleApiController's copy, leaving every
endpoint inheriting from BaseApiController (notifications, avatars) on
header-only auth and 401-ing cookie-authed callers. This module exists so
that doesn't happen again.

Stashes on request.httprequest:
- _auth_source: 'header' | 'cookie' (drives X-Auth-Source response header)
- _api_session: the validated api.session record (used by /auth/logout to
  invalidate exactly the session that authed the request, regardless of
  whatever else might be in cookies/headers)
- _refresh_session_cookie: True when sliding refresh fired AND auth was
  via cookie, so the post-dispatch hook re-issues the cookie with a
  fresh Max-Age. Header-authed callers don't get cookies set behind
  their back; the upgrade through /auth/me is the explicit cookie path.
"""

import logging
import time as _time
from datetime import datetime, timedelta

from odoo.http import request

from odoo.addons.base_api.services.auth_cookies import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    SLIDING_REFRESH_THRESHOLD_SECONDS,
)

_logger = logging.getLogger(__name__)


def authenticate_session(error_response, enforce_user_rate_limit):
    """Validate session via header-wins-with-cookie-fallback.

    Args:
        error_response: callable(message, status_code, error_code) → response.
            Pulled in from the calling controller so the error envelope
            matches whatever shape that controller's other endpoints use.
        enforce_user_rate_limit: callable(user) → response_or_None.
            Per-user rate limit gate, also from the calling controller.

    Returns:
        (True, user_record) on success
        (False, error_response_object) on failure
    """
    request.httprequest._api_start_time = _time.time()

    header_token = request.httprequest.headers.get('session-token')
    cookie_token = request.httprequest.cookies.get(SESSION_COOKIE_NAME)
    session_token = header_token or cookie_token
    auth_source = 'header' if header_token else ('cookie' if cookie_token else None)

    if not session_token:
        return False, error_response(
            "Session token required", 401, "MISSING_SESSION_TOKEN",
        )

    try:
        token_hash = request.env['api.session']._hash_token(session_token)
        session = request.env['api.session'].sudo().search([
            ('token', '=', token_hash),
            ('active', '=', True),
            ('expires_at', '>', datetime.now()),
        ], limit=1)

        if not session:
            return False, error_response(
                "Invalid or expired session", 401, "INVALID_SESSION",
            )

        # Best-effort last_activity bump + sliding refresh in one savepoint.
        # GET routes run on a read-only cursor in Odoo 18+, so the write may
        # fail; the failure stays contained and the cursor remains usable.
        now = datetime.now()
        ttl = timedelta(seconds=SESSION_TTL_SECONDS)
        refresh_threshold = timedelta(seconds=SLIDING_REFRESH_THRESHOLD_SECONDS)
        needs_refresh = (session.expires_at - now) < (ttl - refresh_threshold)
        try:
            with request.env.cr.savepoint():
                writes = {'last_activity': now}
                if needs_refresh:
                    writes['expires_at'] = now + ttl
                session.sudo().write(writes)
                if needs_refresh and auth_source == 'cookie':
                    request.httprequest._refresh_session_cookie = True
        except Exception as write_error:
            _logger.debug("Skipped session bump: %s", str(write_error))

        request.httprequest._auth_source = auth_source
        request.httprequest._api_session = session

        rate_error = enforce_user_rate_limit(session.user_id)
        if rate_error:
            return False, rate_error

        request.update_env(user=session.user_id.id)
        return True, session.user_id
    except Exception as e:
        _logger.error("Session authentication error: %s", str(e))
        return False, error_response(
            "Session authentication failed", 500, "SESSION_AUTH_ERROR",
        )
