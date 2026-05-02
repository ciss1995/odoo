# -*- coding: utf-8 -*-

"""Cookie helpers for the session-cookie auth migration.

Lives in services/ (not controllers/) so both simple_api.py and the
ir.http override in models/cors.py can share the same cookie attributes
without a circular import.

Cookie shape (Option B in the migration spec):
- yiri_session: HttpOnly, host-only (no Domain=), Secure when over HTTPS,
  SameSite=Lax, Path=/, Max-Age = 24h.
- yiri_csrf: same attributes minus HttpOnly so the SPA can read it via
  document.cookie for double-submit.
"""

SESSION_COOKIE_NAME = 'yiri_session'
CSRF_COOKIE_NAME = 'yiri_csrf'
SESSION_TTL_SECONDS = 24 * 60 * 60
SLIDING_REFRESH_THRESHOLD_SECONDS = 30 * 60


def is_secure_request(httprequest):
    """True if the request reached us over HTTPS, including via a trusted proxy.

    Local dev runs on plain http://, where browsers refuse to set Secure cookies.
    Production sits behind Traefik which terminates TLS and forwards
    X-Forwarded-Proto.
    """
    if httprequest.is_secure:
        return True
    return httprequest.environ.get('HTTP_X_FORWARDED_PROTO') == 'https'


def set_session_cookies(response, session_token, csrf_token, secure):
    """Issue both session and CSRF cookies on `response` with shared attributes."""
    response.set_cookie(
        SESSION_COOKIE_NAME, session_token,
        max_age=SESSION_TTL_SECONDS, secure=secure, httponly=True,
        samesite='Lax', path='/',
    )
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf_token,
        max_age=SESSION_TTL_SECONDS, secure=secure, httponly=False,
        samesite='Lax', path='/',
    )


def clear_session_cookies(response, secure):
    """Expire both cookies. Attributes must mirror set_session_cookies or some
    browsers refuse to clear."""
    response.set_cookie(
        SESSION_COOKIE_NAME, '', max_age=0, secure=secure, httponly=True,
        samesite='Lax', path='/',
    )
    response.set_cookie(
        CSRF_COOKIE_NAME, '', max_age=0, secure=secure, httponly=False,
        samesite='Lax', path='/',
    )
