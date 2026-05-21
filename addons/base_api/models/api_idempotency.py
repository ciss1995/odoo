# -*- coding: utf-8 -*-
"""Idempotency-key storage for mutating /api/v2/* endpoints.

Why: a double-click on "Create invoice" or a flaky network that
retries POST /api/v2/create/sale.order would otherwise create two
records. Storing the (user, key, request_hash) → response of the
first successful call and replaying it on the second is the standard
fix — RFC 9457 + Stripe / GitHub-style.

Why not just a 5s debounce on the client: doesn't help when the
client is curl / a third-party integrator / a webhook handler retry.
"""

import hashlib
import json
from datetime import datetime, timedelta

from odoo import api, fields, models

# How long a key stays valid. Long enough that retries-after-error
# work, short enough that the table doesn't grow unbounded. 24h
# matches Stripe's default.
_TTL_HOURS = 24


class ApiIdempotency(models.Model):
    _name = 'api.idempotency'
    _description = 'API Idempotency Key Cache'
    _order = 'created_at desc'

    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='cascade', index=True,
    )
    # Client-supplied key from the Idempotency-Key request header.
    # Capped at 64 chars (controller validates).
    key = fields.Char(string='Idempotency Key', required=True, index=True)
    # SHA-256 of the request method + path + body. If the same key is
    # reused with a DIFFERENT payload we must NOT replay — that's a
    # client bug and we surface it as 409 to avoid silent misroutes.
    request_hash = fields.Char(string='Request Hash', required=True)
    # Cached response body + status code, replayed verbatim on hit.
    response_json = fields.Text(string='Response JSON')
    response_status = fields.Integer(string='Response Status', default=200)
    created_at = fields.Datetime(
        string='Created At', default=fields.Datetime.now, required=True, index=True,
    )

    _user_key_unique = models.Constraint(
        'UNIQUE(user_id, key)',
        'Idempotency key must be unique per user',
    )

    @staticmethod
    def hash_request(method: str, path: str, body: bytes | str) -> str:
        """Hash a request for replay-detection.

        Body can be empty for safe methods, but for POST/PATCH the body
        IS the identifying material — two POSTs with the same key but
        different bodies are a client bug.
        """
        h = hashlib.sha256()
        h.update((method or '').upper().encode())
        h.update(b'\x00')
        h.update((path or '').encode())
        h.update(b'\x00')
        if isinstance(body, str):
            body = body.encode('utf-8', errors='replace')
        h.update(body or b'')
        return h.hexdigest()

    @api.model
    def cleanup_expired(self):
        """Drop keys older than _TTL_HOURS. Wire as a cron."""
        cutoff = datetime.now() - timedelta(hours=_TTL_HOURS)
        self.search([('created_at', '<', cutoff)]).unlink()
        return True

    def is_expired(self) -> bool:
        return self.created_at < datetime.now() - timedelta(hours=_TTL_HOURS)

    def replay(self) -> tuple[dict, int]:
        """Return (parsed_response, status_code) for this cached entry."""
        try:
            data = json.loads(self.response_json) if self.response_json else None
        except (ValueError, TypeError):
            data = None
        return data, self.response_status or 200
