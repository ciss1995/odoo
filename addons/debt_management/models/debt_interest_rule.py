# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class DebtInterestRule(models.Model):
    _name = 'debt.interest.rule'
    _description = 'Debt Interest Rule'
    _order = 'name'

    name = fields.Char('Name', required=True)
    rate = fields.Float('Rate (%)', required=True, digits=(6, 4))
    cycle = fields.Selection([
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
    ], string='Cycle', required=True, default='monthly')
    compound = fields.Boolean('Compound Interest', default=False,
                              help="If enabled, interest is calculated on principal + accrued interest.")
    active = fields.Boolean('Active', default=True)

    @api.constrains('rate')
    def _check_rate(self):
        for rec in self:
            if rec.rate <= 0:
                raise ValidationError("Interest rate must be positive.")
