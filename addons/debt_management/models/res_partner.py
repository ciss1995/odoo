# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    use_debt_limit = fields.Boolean('Enable Debt Limit', default=False)
    max_debt_limit = fields.Float('Maximum Debt Limit', digits=(16, 2))
    debt_ids = fields.One2many('debt.record', 'partner_id', string='Debts')
    debt_count = fields.Integer('Active Debts', compute='_compute_debt_stats')
    current_debt_total = fields.Float('Current Debt', compute='_compute_debt_stats',
                                      digits=(16, 2))

    @api.depends('debt_ids.amount_residual', 'debt_ids.state')
    def _compute_debt_stats(self):
        for partner in self:
            active = partner.debt_ids.filtered(
                lambda d: d.state in ('active', 'overdue')
            )
            partner.debt_count = len(active)
            partner.current_debt_total = sum(active.mapped('amount_residual'))
