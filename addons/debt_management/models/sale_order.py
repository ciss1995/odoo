# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_debt_sale = fields.Boolean('Credit Sale', default=False)
    debt_record_id = fields.Many2one('debt.record', string='Debt Record', copy=False)
    debt_due_date = fields.Date('Debt Due Date',
                                help="Due date for the auto-created debt record. "
                                     "Defaults to 30 days from confirmation.")
    debt_interest_rule_id = fields.Many2one('debt.interest.rule',
                                            string='Debt Interest Rule')

    def action_confirm(self):
        for order in self.filtered('is_debt_sale'):
            partner = order.partner_id
            if partner.use_debt_limit and partner.max_debt_limit > 0:
                headroom = partner.max_debt_limit - partner.current_debt_total
                if order.amount_total > headroom:
                    raise ValidationError(
                        f"Cannot confirm: {partner.name}'s debt would exceed "
                        f"the limit of {partner.max_debt_limit:.2f}. "
                        f"Current debt: {partner.current_debt_total:.2f}, "
                        f"Order total: {order.amount_total:.2f}, "
                        f"Available: {headroom:.2f}"
                    )

        result = super().action_confirm()

        for order in self.filtered(lambda o: o.is_debt_sale and not o.debt_record_id):
            due = order.debt_due_date or (fields.Date.today() + timedelta(days=30))
            vals = {
                'partner_id': order.partner_id.id,
                'sale_order_id': order.id,
                'amount': order.amount_total,
                'issue_date': fields.Date.today(),
                'due_date': due,
                'state': 'active',
                'last_interest_date': fields.Date.today(),
            }
            if order.debt_interest_rule_id:
                vals['interest_rule_id'] = order.debt_interest_rule_id.id
            debt = self.env['debt.record'].create(vals)
            order.debt_record_id = debt.id

        return result
