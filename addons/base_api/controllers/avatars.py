# -*- coding: utf-8 -*-
"""Avatar endpoint — serves res.partner avatar bytes through session-token auth.

Browsers cannot send custom headers on plain `<img src="...">` requests, so SPA
clients should fetch this endpoint with their authFetch wrapper and convert
the response to an object URL via URL.createObjectURL(blob) for use in img
tags. Caching is browser-friendly (ETag + Cache-Control private,1h) so a blob
URL keyed on partner_id is the common pattern.
"""

import base64
import logging

from odoo import http
from odoo.http import request

from .base import BaseApiController


_logger = logging.getLogger(__name__)


VALID_SIZES = ('128', '256', '512', '1024', '1920')


class AvatarsController(BaseApiController):

    @http.route(
        '/api/v2/avatars/res.partner/<int:partner_id>',
        type='http', auth='none', methods=['GET'], csrf=False,
    )
    def partner_avatar(self, partner_id, **_kwargs):
        ok, user_or_err = self._authenticate_session()
        if not ok:
            return user_or_err
        sub_err = self._enforce_subscription()
        if sub_err is not None:
            return sub_err

        size = request.httprequest.args.get('size', '128')
        if size not in VALID_SIZES:
            return self._error_response(
                f"size must be one of {', '.join(VALID_SIZES)}",
                400, "INVALID_INPUT",
            )
        field = f'image_{size}'

        partner = request.env['res.partner'].browse(partner_id)
        if not partner.exists():
            return self._error_response("Partner not found", 404, "NOT_FOUND")
        try:
            partner.check_access('read')
        except Exception:
            # Treat ACL denial as 404 to avoid leaking partner existence
            return self._error_response("Partner not found", 404, "NOT_FOUND")

        img_b64 = partner[field]
        if not img_b64:
            return self._error_response("No avatar set", 404, "NOT_FOUND")

        try:
            img_bytes = (
                base64.b64decode(img_b64)
                if isinstance(img_b64, (str, bytes)) else None
            )
        except Exception as e:
            _logger.error("avatar decode failed for partner %s: %s", partner_id, e)
            return self._error_response(
                "Avatar unavailable", 500, "AVATAR_ERROR",
            )
        if not img_bytes:
            return self._error_response("No avatar set", 404, "NOT_FOUND")

        # ETag derived from write_date so browsers can revalidate cheaply.
        write_dt = partner.write_date
        etag_seed = write_dt.isoformat() if write_dt else '0'
        etag = f'"avatar-{partner_id}-{size}-{etag_seed}"'
        if request.httprequest.headers.get('If-None-Match') == etag:
            resp = request.make_response('', headers=[('ETag', etag)])
            resp.status_code = 304
            self._log_api_call(304)
            return resp

        content_type = self._sniff_image_type(img_bytes)
        resp = request.make_response(
            img_bytes,
            headers=[
                ('Content-Type', content_type),
                ('Cache-Control', 'private, max-age=3600'),
                ('ETag', etag),
                ('Content-Length', str(len(img_bytes))),
            ],
        )
        self._log_api_call(200)
        return resp

    def _sniff_image_type(self, img_bytes):
        """Detect image type from leading magic bytes; default to PNG."""
        if len(img_bytes) < 4:
            return 'application/octet-stream'
        head = img_bytes[:4]
        if head[:3] == b'\xff\xd8\xff':
            return 'image/jpeg'
        if head == b'\x89PNG':
            return 'image/png'
        if head[:3] == b'GIF':
            return 'image/gif'
        if head == b'RIFF':
            return 'image/webp'
        return 'image/png'
