# -*- coding: utf-8 -*-

import hashlib
from odoo import models, fields, api
from datetime import datetime, timedelta


class ApiSession(models.Model):
    _name = 'api.session'
    _description = 'API Session'
    _order = 'created_at desc'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade')
    token = fields.Char(string='Token Hash', required=True, index=True)
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now, required=True)
    expires_at = fields.Datetime(string='Expires At', required=True)
    last_activity = fields.Datetime(string='Last Activity', default=fields.Datetime.now)
    active = fields.Boolean(string='Active', default=True)
    ip_address = fields.Char(string='IP Address')
    user_agent = fields.Text(string='User Agent')

    # Step-up re-authentication (0.2). ``last_reauth_at`` is set on login and
    # bumped by POST /api/v2/auth/reauth; ``_enforce_step_up`` compares it to a
    # tenant-configured window to gate high-value money mutations even when the
    # session itself is still valid (mitigates a stolen session token draining
    # an account). ``wa_id`` tags a WhatsApp-originated session (used later to
    # force a re-PIN when the WhatsApp device changes).
    last_reauth_at = fields.Datetime(string='Last Re-auth At', default=fields.Datetime.now)
    wa_id = fields.Char(string='WhatsApp ID', index=True)

    _token_unique = models.Constraint('UNIQUE(token)', 'Session token must be unique')

    @api.model
    def revoke_user_sessions(self, user_id, keep_id=None):
        """Deactivate a user's active sessions (F-018).

        Called after a password change/reset so a hijacker holding a stolen
        token loses access the moment the victim recovers the account. When
        ``keep_id`` is given (the caller's own session on a self-service
        password change) that session is preserved. Returns the count revoked.
        """
        domain = [('user_id', '=', user_id), ('active', '=', True)]
        if keep_id:
            domain.append(('id', '!=', keep_id))
        sessions = self.sudo().search(domain)
        count = len(sessions)
        if count:
            sessions.write({'active': False})
        return count

    @staticmethod
    def _hash_token(raw_token):
        """Hash a raw session token for secure storage."""
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @api.model
    def cleanup_expired_sessions(self):
        """Remove expired sessions."""
        expired_sessions = self.search([
            ('expires_at', '<', datetime.now())
        ])
        expired_sessions.unlink()
        return True

    def is_expired(self):
        """Check if session is expired."""
        return self.expires_at < datetime.now()

    def extend_session(self, hours=24):
        """Extend session expiration."""
        self.expires_at = datetime.now() + timedelta(hours=hours)
        self.last_activity = datetime.now()
