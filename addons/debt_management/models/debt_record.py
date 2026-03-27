# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

CYCLE_DAYS = {
    'daily': 1,
    'weekly': 7,
    'biweekly': 14,
    'monthly': 30,
    'quarterly': 90,
    'yearly': 365,
}


class DebtRecord(models.Model):
    _name = 'debt.record'
    _description = 'Debt Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'issue_date desc, id desc'

    name = fields.Char('Reference', required=True, copy=False, readonly=True, default='New')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True,
                                 index=True, tracking=True)
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency',
                                  default=lambda self: self.env.company.currency_id)

    amount = fields.Monetary('Principal Amount', required=True, tracking=True)
    amount_interest = fields.Monetary('Accrued Interest', default=0.0, tracking=True)
    amount_paid = fields.Monetary('Amount Paid', compute='_compute_amounts', store=True)
    amount_residual = fields.Monetary('Balance Due', compute='_compute_amounts', store=True)
    amount_total = fields.Monetary('Total Amount', compute='_compute_amounts', store=True)

    issue_date = fields.Date('Issue Date', required=True,
                             default=fields.Date.today, tracking=True)
    due_date = fields.Date('Due Date', required=True, tracking=True)
    last_interest_date = fields.Date('Last Interest Calculation')

    interest_rule_id = fields.Many2one('debt.interest.rule', string='Interest Rule')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, index=True)

    payment_ids = fields.One2many('debt.payment', 'debt_id', string='Payments')
    notification_ids = fields.One2many('debt.notification.log', 'debt_id',
                                       string='Notifications')
    notes = fields.Text('Notes')

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('amount', 'amount_interest', 'payment_ids.amount')
    def _compute_amounts(self):
        for rec in self:
            rec.amount_paid = sum(rec.payment_ids.mapped('amount'))
            rec.amount_total = rec.amount + rec.amount_interest
            rec.amount_residual = max(rec.amount_total - rec.amount_paid, 0.0)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError("Principal amount must be positive.")

    @api.constrains('due_date', 'issue_date')
    def _check_dates(self):
        for rec in self:
            if rec.due_date and rec.issue_date and rec.due_date < rec.issue_date:
                raise ValidationError("Due date must be on or after the issue date.")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('debt.record') or 'New'
                )
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError("Only draft debts can be confirmed.")
        self.write({
            'state': 'active',
            'last_interest_date': fields.Date.today(),
        })

    def action_cancel(self):
        for rec in self:
            if rec.state == 'paid':
                raise ValidationError("Cannot cancel a fully-paid debt.")
        self.write({'state': 'cancelled'})

    def _check_auto_paid(self):
        """Mark as paid when balance reaches zero."""
        for rec in self:
            if rec.state in ('active', 'overdue') and rec.amount_residual <= 0:
                rec.state = 'paid'
                rec.message_post(
                    body="Debt fully paid.",
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

    # ------------------------------------------------------------------
    # Cron: Interest
    # ------------------------------------------------------------------

    @api.model
    def _cron_calculate_interest(self):
        """Daily cron — accrue interest on active debts that carry an interest rule."""
        today = fields.Date.today()
        debts = self.search([
            ('state', 'in', ['active', 'overdue']),
            ('interest_rule_id', '!=', False),
            ('amount_residual', '>', 0),
        ])
        for debt in debts:
            rule = debt.interest_rule_id
            last = debt.last_interest_date or debt.issue_date
            elapsed = (today - last).days
            cycle_len = CYCLE_DAYS.get(rule.cycle, 30)
            if elapsed < cycle_len:
                continue
            cycles = elapsed // cycle_len
            rate = rule.rate / 100.0
            base = (debt.amount + debt.amount_interest) if rule.compound else debt.amount
            interest = base * rate * cycles
            if interest > 0:
                debt.write({
                    'amount_interest': debt.amount_interest + interest,
                    'last_interest_date': today,
                })
                debt.message_post(
                    body=(
                        f"Interest applied: {debt.currency_id.symbol or ''}"
                        f"{interest:.2f} "
                        f"({rule.name}: {rule.rate}% {rule.cycle}, "
                        f"{cycles} cycle(s))"
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

    # ------------------------------------------------------------------
    # Cron: Overdue & Notifications
    # ------------------------------------------------------------------

    @api.model
    def _cron_check_overdue_and_notify(self):
        """Daily cron — mark overdue debts & send reminders."""
        today = fields.Date.today()
        NotifLog = self.env['debt.notification.log']

        # 1. Mark newly overdue
        newly_overdue = self.search([
            ('state', '=', 'active'),
            ('due_date', '<', today),
            ('amount_residual', '>', 0),
        ])
        if newly_overdue:
            newly_overdue.write({'state': 'overdue'})
            for debt in newly_overdue:
                msg = (
                    f"Debt {debt.name} is now overdue. "
                    f"Balance: {debt.currency_id.symbol or ''}{debt.amount_residual:.2f}. "
                    f"Due date was {debt.due_date}."
                )
                debt.message_post(
                    body=msg,
                    message_type='notification',
                    subtype_xmlid='mail.mt_comment',
                )
                NotifLog.create({
                    'debt_id': debt.id,
                    'partner_id': debt.partner_id.id,
                    'notification_type': 'overdue',
                    'channel': 'internal',
                    'message': msg,
                    'status': 'sent',
                })

        # 2. Upcoming-due reminders
        notify_days = int(
            self.env['ir.config_parameter'].sudo()
                .get_param('debt.notify_days_before', '3')
        )
        upcoming_date = today + timedelta(days=notify_days)
        upcoming = self.search([
            ('state', '=', 'active'),
            ('due_date', '=', upcoming_date),
            ('amount_residual', '>', 0),
        ])
        today_start = fields.Datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        for debt in upcoming:
            already = NotifLog.search([
                ('debt_id', '=', debt.id),
                ('notification_type', '=', 'reminder'),
                ('sent_at', '>=', today_start),
            ], limit=1)
            if already:
                continue
            msg = (
                f"Reminder: Debt {debt.name} is due on {debt.due_date}. "
                f"Balance: {debt.currency_id.symbol or ''}{debt.amount_residual:.2f}"
            )
            debt.message_post(
                body=msg,
                message_type='notification',
                subtype_xmlid='mail.mt_comment',
            )
            NotifLog.create({
                'debt_id': debt.id,
                'partner_id': debt.partner_id.id,
                'notification_type': 'reminder',
                'channel': 'internal',
                'message': msg,
                'status': 'sent',
            })

        # 3. Weekly overdue reminders
        all_overdue = self.search([
            ('state', '=', 'overdue'),
            ('amount_residual', '>', 0),
        ])
        for debt in all_overdue:
            last_notif = NotifLog.search([
                ('debt_id', '=', debt.id),
                ('notification_type', '=', 'overdue_reminder'),
            ], order='sent_at desc', limit=1)
            if last_notif:
                since = (fields.Datetime.now() - last_notif.sent_at).days
                if since < 7:
                    continue
            days_overdue = (today - debt.due_date).days
            msg = (
                f"Overdue reminder ({days_overdue}d): Debt {debt.name}. "
                f"Balance: {debt.currency_id.symbol or ''}{debt.amount_residual:.2f}"
            )
            debt.message_post(
                body=msg,
                message_type='notification',
                subtype_xmlid='mail.mt_comment',
            )
            NotifLog.create({
                'debt_id': debt.id,
                'partner_id': debt.partner_id.id,
                'notification_type': 'overdue_reminder',
                'channel': 'internal',
                'message': msg,
                'status': 'sent',
            })
