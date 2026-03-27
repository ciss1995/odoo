# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class DebtPayment(models.Model):
    _name = 'debt.payment'
    _description = 'Debt Payment'
    _order = 'payment_date desc, id desc'

    debt_id = fields.Many2one('debt.record', string='Debt', required=True,
                              ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='debt_id.partner_id', store=True)
    currency_id = fields.Many2one(related='debt_id.currency_id', store=True)

    amount = fields.Monetary('Amount', required=True)
    payment_date = fields.Date('Payment Date', required=True,
                               default=fields.Date.today)
    reference = fields.Char('Reference')
    notes = fields.Text('Notes')

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError("Payment amount must be positive.")
