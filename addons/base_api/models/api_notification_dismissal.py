# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ApiNotificationDismissal(models.Model):
    _name = 'api.notification.dismissal'
    _description = 'Per-user dismissal state for notifications surfaced by base_api'
    _order = 'dismissed_at desc'

    user_id = fields.Many2one(
        'res.users', required=True, ondelete='cascade', index=True,
    )
    source_kind = fields.Selection(
        [('activity', 'Activity')],
        required=True,
    )
    source_id = fields.Integer(required=True, index=True)
    dismissed_at = fields.Datetime(
        required=True, default=fields.Datetime.now,
    )

    _uniq_user_source = models.Constraint(
        'UNIQUE(user_id, source_kind, source_id)',
        'Dismissal already exists for this user and source.',
    )

    @api.model
    def dismiss_many(self, user_id, source_kind, source_ids):
        """Idempotently insert dismissals for a list of source ids.

        Returns the number of newly-created rows (existing rows are skipped).
        """
        if not source_ids:
            return 0
        existing = self.sudo().search([
            ('user_id', '=', user_id),
            ('source_kind', '=', source_kind),
            ('source_id', 'in', list(source_ids)),
        ])
        existing_ids = set(existing.mapped('source_id'))
        to_create = [
            {'user_id': user_id, 'source_kind': source_kind, 'source_id': sid}
            for sid in source_ids if sid not in existing_ids
        ]
        if not to_create:
            return 0
        self.sudo().create(to_create)
        return len(to_create)

    @api.model
    def dismissed_ids_for(self, user_id, source_kind, source_ids):
        """Return the subset of source_ids already dismissed by this user."""
        if not source_ids:
            return set()
        rows = self.sudo().search([
            ('user_id', '=', user_id),
            ('source_kind', '=', source_kind),
            ('source_id', 'in', list(source_ids)),
        ])
        return set(rows.mapped('source_id'))
