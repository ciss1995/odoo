# -*- coding: utf-8 -*-

from odoo import models, fields


class DebtNotificationLog(models.Model):
    _name = 'debt.notification.log'
    _description = 'Debt Notification Log'
    _order = 'sent_at desc'

    debt_id = fields.Many2one('debt.record', string='Debt', required=True,
                              ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Customer', index=True)
    notification_type = fields.Selection([
        ('reminder', 'Upcoming Reminder'),
        ('overdue', 'Overdue Notice'),
        ('overdue_reminder', 'Overdue Reminder'),
    ], string='Type', required=True)
    channel = fields.Selection([
        ('internal', 'Internal'),
        ('email', 'Email'),
        ('sms', 'SMS'),
    ], string='Channel', default='internal')
    message = fields.Text('Message')
    sent_at = fields.Datetime('Sent At', default=fields.Datetime.now)
    status = fields.Selection([
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ], default='sent')
