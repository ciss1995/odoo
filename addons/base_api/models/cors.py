# -*- coding: utf-8 -*-

import os
import logging

import werkzeug.exceptions
from werkzeug.wrappers import Response as WerkzeugResponse

from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Comma-separated origins read once at module load.
# Example: "https://yiri-streamline-flow-production.up.railway.app,http://localhost:5173"
_ALLOWED_ORIGINS_RAW = os.environ.get("ODOO_CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = frozenset(
    o.strip().rstrip("/") for o in _ALLOWED_ORIGINS_RAW.split(",") if o.strip()
)

CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
CORS_HEADERS = "Content-Type, session-token, Authorization, api-key"
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
