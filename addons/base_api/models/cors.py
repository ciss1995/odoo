# -*- coding: utf-8 -*-

import os
import logging

import werkzeug.exceptions
from werkzeug.wrappers import Response as WerkzeugResponse

from odoo import models
from odoo.http import request

from odoo.addons.base_api.services.auth_cookies import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    is_secure_request,
    set_session_cookies,
)

_logger = logging.getLogger(__name__)

# Comma-separated origins read once at module load.
# Example: "https://yiri-streamline-flow-production.up.railway.app,http://localhost:5173"
_ALLOWED_ORIGINS_RAW = os.environ.get("ODOO_CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = frozenset(
    o.strip().rstrip("/") for o in _ALLOWED_ORIGINS_RAW.split(",") if o.strip()
)

CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
CORS_HEADERS = "Content-Type, session-token, Authorization, api-key, X-CSRF-Token"
CORS_MAX_AGE = "86400"


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if not ALLOWED_ORIGINS:
        return True
    return origin.rstrip("/") in ALLOWED_ORIGINS


def _set_cors_headers(response, origin: str):
    h = response.headers
    h["Access-Control-Allow-Origin"] = origin
    h["Access-Control-Allow-Methods"] = CORS_METHODS
    h["Access-Control-Allow-Headers"] = CORS_HEADERS
    h["Access-Control-Allow-Credentials"] = "true"
    h["Access-Control-Max-Age"] = CORS_MAX_AGE
    h["Vary"] = "Origin"


class IrHttpCors(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        if not request or request.httprequest.method != "OPTIONS":
            return
        path = request.httprequest.path
        if not path.startswith("/api/v2/"):
            return
        origin = request.httprequest.headers.get("Origin", "")
        if _origin_allowed(origin):
            resp = WerkzeugResponse(status=204)
            _set_cors_headers(resp, origin)
            werkzeug.exceptions.abort(resp)

    @classmethod
    def _post_dispatch(cls, response):
        super()._post_dispatch(response)
        if response is None or not request or not hasattr(request, "httprequest"):
            return
        path = request.httprequest.path
        if not path.startswith("/api/v2/"):
            return
        origin = request.httprequest.headers.get("Origin", "")
        if _origin_allowed(origin):
            _set_cors_headers(response, origin)

        # Cookie-adoption telemetry. UI team reads this to decide when to
        # drop the legacy session-token header path (gate: <0.5% header for
        # 14d per app). Only set when _authenticate_session ran and won.
        auth_source = getattr(request.httprequest, "_auth_source", None)
        if auth_source:
            response.headers["X-Auth-Source"] = auth_source

        # Sliding refresh: if cookie auth fired AND the session row's
        # expires_at was bumped, re-set the cookies so the browser's
        # Max-Age stays in sync with the server's TTL. Same values as
        # the inbound cookies — token rotation is not part of sliding
        # refresh, see the auth-cookies migration spec.
        if getattr(request.httprequest, "_refresh_session_cookie", False):
            session_token = request.httprequest.cookies.get(SESSION_COOKIE_NAME)
            csrf_token = request.httprequest.cookies.get(CSRF_COOKIE_NAME)
            if session_token and csrf_token:
                set_session_cookies(
                    response, session_token, csrf_token,
                    secure=is_secure_request(request.httprequest),
                )
