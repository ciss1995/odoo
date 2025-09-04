# -*- coding: utf-8 -*-

import secrets
import string
from odoo import models, fields, api


class ResUsers(models.Model):
    _inherit = 'res.users'

    api_key = fields.Char(
        string="API Key",
        copy=False,
        readonly=True,
        help="API key for REST API authentication"
    )

    @api.model
    def _generate_api_key(self):
        """Generate a secure random API key of at least 40 characters."""
        # Use URL-safe characters (letters, digits, '-', '_')
        alphabet = string.ascii_letters + string.digits + '-_'
        return ''.join(secrets.choice(alphabet) for _ in range(48))

    def action_generate_api_key(self):
        """Generate and set a new API key for the user."""
        self.ensure_one()
        new_api_key = self._generate_api_key()
        self.write({'api_key': new_api_key})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'API Key Generated',
                'message': 'A new API key has been generated successfully.',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_revoke_api_key(self):
        """Revoke the current API key by setting it to False."""
        self.ensure_one()
        self.write({'api_key': False})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'API Key Revoked',
                'message': 'The API key has been revoked successfully.',
                'type': 'info',
                'sticky': False,
            }
        }

    @api.model
    def find_user_by_api_key(self, api_key):
        """Find a user by their API key."""
        if not api_key:
            return False
        return self.search([('api_key', '=', api_key)], limit=1)
