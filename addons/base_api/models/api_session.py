# -*- coding: utf-8 -*-

from odoo import models, fields, api
from datetime import datetime, timedelta


class ApiSession(models.Model):
    _name = 'api.session'
    _description = 'API Session'
    _order = 'created_at desc'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade')
    token = fields.Char(string='Session Token', required=True, index=True)
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now, required=True)
    expires_at = fields.Datetime(string='Expires At', required=True)
    last_activity = fields.Datetime(string='Last Activity', default=fields.Datetime.now)
    active = fields.Boolean(string='Active', default=True)
    ip_address = fields.Char(string='IP Address')
    user_agent = fields.Text(string='User Agent')

    _sql_constraints = [
        ('token_unique', 'UNIQUE(token)', 'Session token must be unique'),
    ]

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
