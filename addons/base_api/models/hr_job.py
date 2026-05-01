# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrJob(models.Model):
    _inherit = 'hr.job'

    salary_min = fields.Monetary(
        string="Minimum Salary",
        currency_field='salary_currency_id',
        help="Lower bound of the posted salary range. Optional.",
    )
    salary_max = fields.Monetary(
        string="Maximum Salary",
        currency_field='salary_currency_id',
        help="Upper bound of the posted salary range. Optional.",
    )
    salary_currency_id = fields.Many2one(
        'res.currency',
        string="Salary Currency",
        default=lambda self: self.env.company.currency_id,
    )
    salary_period = fields.Selection(
        [
            ('hourly', 'Hourly'),
            ('monthly', 'Monthly'),
            ('yearly', 'Yearly'),
        ],
        string="Salary Period",
        default='monthly',
        required=True,
    )

    @api.constrains('salary_min', 'salary_max')
    def _check_salary_range(self):
        for job in self:
            if job.salary_min and job.salary_min < 0:
                raise ValidationError(_("Minimum salary cannot be negative."))
            if job.salary_max and job.salary_max < 0:
                raise ValidationError(_("Maximum salary cannot be negative."))
            if job.salary_min and job.salary_max and job.salary_max < job.salary_min:
                raise ValidationError(_("Maximum salary cannot be lower than minimum salary."))
