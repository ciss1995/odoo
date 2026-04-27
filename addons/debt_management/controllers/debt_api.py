# -*- coding: utf-8 -*-

import json
import logging
from datetime import date, datetime, timedelta

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)


class DebtApiController(http.Controller):
    """REST API endpoints for debt management (``/api/v2/debts/*``)."""

    # ==================================================================
    # Response helpers
    # ==================================================================

    def _json_response(self, data=None, success=True, message=None, status_code=200):
        body = {'success': success, 'data': data, 'message': message}
        resp = request.make_response(
            json.dumps(body, default=str),
            headers=[('Content-Type', 'application/json')],
        )
        resp.status_code = status_code
        return resp

    def _error_response(self, message, status_code=400, error_code=None):
        body = {'success': False, 'error': {'message': message, 'code': error_code}}
        resp = request.make_response(
            json.dumps(body, default=str),
            headers=[('Content-Type', 'application/json')],
        )
        resp.status_code = status_code
        return resp

    # ==================================================================
    # Authentication (mirrors base_api patterns)
    # ==================================================================

    def _authenticate(self):
        api_key = request.httprequest.headers.get('api-key')
        if not api_key:
            return False, self._error_response("Missing API key", 401, "MISSING_API_KEY")
        try:
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(
                scope='rpc', key=api_key,
            )
            if not user_id:
                return False, self._error_response("Invalid API key", 403, "INVALID_API_KEY")
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists() or not user.active:
                return False, self._error_response("User account inactive", 403, "INACTIVE_USER")
            request.update_env(user=user.id)
            return True, user
        except Exception as e:
            _logger.error("Auth error: %s", e)
            return False, self._error_response("Authentication error", 500, "AUTH_ERROR")

    def _authenticate_session(self):
        token = request.httprequest.headers.get('session-token')
        if not token:
            return False, self._error_response(
                "Session token required", 401, "MISSING_SESSION_TOKEN",
            )
        try:
            token_hash = request.env['api.session']._hash_token(token)
            session = request.env['api.session'].sudo().search([
                ('token', '=', token_hash),
                ('active', '=', True),
                ('expires_at', '>', datetime.now()),
            ], limit=1)
            if not session:
                return False, self._error_response(
                    "Invalid or expired session", 401, "INVALID_SESSION",
                )
            try:
                session.sudo().write({'last_activity': datetime.now()})
            except Exception:
                pass  # keep auth successful even if timestamp update fails
            request.update_env(user=session.user_id.id)
            return True, session.user_id
        except Exception as e:
            _logger.error("Session auth error: %s", e)
            return False, self._error_response(
                "Session authentication failed", 500, "SESSION_AUTH_ERROR",
            )

    def _auth(self):
        """Try session token first, fall back to API key.

        If a session-token header is present, use session auth exclusively
        — don't fall back to API key, so the caller sees the real session
        error instead of a misleading "Missing API key".
        """
        if request.httprequest.headers.get('session-token'):
            return self._authenticate_session()
        return self._authenticate()

    def _check_debt_access(self, user, operation='read'):
        """Check if the user has ACL access to debt.record for the given operation.

        Returns None if OK, or an error response if denied.
        """
        try:
            request.env['debt.record'].check_access_rights(operation)
            return None
        except AccessError:
            return self._error_response(
                "Access denied for debt management", 403, "ACCESS_DENIED",
            )

    def _require_admin_or_manager(self, user):
        """Return error response if user is neither admin nor ERP manager."""
        if user.has_group('base.group_system') or user.has_group('base.group_erp_manager'):
            return None
        return self._error_response(
            "Admin or manager access required", 403, "ACCESS_DENIED",
        )

    def _parse_json(self):
        ct = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in ct:
            return None, self._error_response(
                "Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE",
            )
        try:
            data = request.httprequest.get_json(force=True)
            if not data:
                return None, self._error_response("No data provided", 400, "NO_DATA")
            return data, None
        except Exception:
            return None, self._error_response("Invalid JSON", 400, "INVALID_JSON")

    # ==================================================================
    # Formatters
    # ==================================================================

    @staticmethod
    def _fmt_debt(d):
        return {
            'id': d.id,
            'name': d.name,
            'partner': {'id': d.partner_id.id, 'name': d.partner_id.name},
            'sale_order': (
                {'id': d.sale_order_id.id, 'name': d.sale_order_id.name}
                if d.sale_order_id else None
            ),
            'amount': d.amount,
            'amount_interest': d.amount_interest,
            'amount_paid': d.amount_paid,
            'amount_residual': d.amount_residual,
            'amount_total': d.amount_total,
            'currency': d.currency_id.name if d.currency_id else None,
            'issue_date': str(d.issue_date) if d.issue_date else None,
            'due_date': str(d.due_date) if d.due_date else None,
            'state': d.state,
            'interest_rule': {
                'id': d.interest_rule_id.id,
                'name': d.interest_rule_id.name,
                'rate': d.interest_rule_id.rate,
                'cycle': d.interest_rule_id.cycle,
                'compound': d.interest_rule_id.compound,
            } if d.interest_rule_id else None,
            'notes': d.notes,
            'payment_count': len(d.payment_ids),
            'create_date': str(d.create_date) if d.create_date else None,
        }

    @staticmethod
    def _fmt_payment(p):
        return {
            'id': p.id,
            'debt_id': p.debt_id.id,
            'debt_name': p.debt_id.name,
            'amount': p.amount,
            'payment_date': str(p.payment_date) if p.payment_date else None,
            'reference': p.reference,
            'notes': p.notes,
            'create_date': str(p.create_date) if p.create_date else None,
        }

    # ==================================================================
    # DEBT CRUD
    # ==================================================================

    @http.route('/api/v2/debts', type='http', auth='none',
                methods=['GET', 'POST'], csrf=False, readonly=False)
    def handle_debts(self):
        if request.httprequest.method == 'POST':
            return self._create_debt()
        return self._list_debts()

    def _create_debt(self):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'create')
        if acl_err:
            return acl_err
        data, err = self._parse_json()
        if err:
            return err
        try:
            missing = [f for f in ('partner_id', 'amount', 'due_date') if f not in data]
            if missing:
                return self._error_response(
                    f"Missing required fields: {', '.join(missing)}",
                    400, "MISSING_FIELDS",
                )
            partner = request.env['res.partner'].browse(int(data['partner_id']))
            if not partner.exists():
                return self._error_response("Partner not found", 404, "PARTNER_NOT_FOUND")

            amount = float(data['amount'])
            if partner.use_debt_limit and partner.max_debt_limit > 0:
                avail = partner.max_debt_limit - partner.current_debt_total
                if amount > avail:
                    return self._error_response(
                        f"Debt limit exceeded. Limit: {partner.max_debt_limit:.2f}, "
                        f"Current: {partner.current_debt_total:.2f}, "
                        f"Available: {avail:.2f}",
                        400, "DEBT_LIMIT_EXCEEDED",
                    )

            vals = {
                'partner_id': int(data['partner_id']),
                'amount': amount,
                'due_date': data['due_date'],
                'state': 'active',
            }
            if data.get('issue_date'):
                vals['issue_date'] = data['issue_date']
            if data.get('notes'):
                vals['notes'] = data['notes']
            if data.get('sale_order_id'):
                vals['sale_order_id'] = int(data['sale_order_id'])
            if data.get('interest_rule_id'):
                vals['interest_rule_id'] = int(data['interest_rule_id'])
                vals['last_interest_date'] = vals.get(
                    'issue_date', date.today().isoformat(),
                )

            debt = request.env['debt.record'].create(vals)
            return self._json_response(
                data=self._fmt_debt(debt),
                message="Debt record created",
                status_code=201,
            )
        except ValidationError as e:
            return self._error_response(str(e), 400, "VALIDATION_ERROR")
        except Exception as e:
            _logger.error("Error creating debt: %s", e)
            return self._error_response(
                "Error creating debt record", 500, "DEBT_CREATE_ERROR",
            )

    def _list_debts(self):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            args = request.httprequest.args
            limit = int(args.get('limit', 20))
            offset = int(args.get('offset', 0))
            domain = []
            if args.get('state'):
                domain.append(('state', '=', args['state']))
            if args.get('partner_id'):
                domain.append(('partner_id', '=', int(args['partner_id'])))
            if args.get('overdue') == 'true':
                domain.append(('state', '=', 'overdue'))

            Debt = request.env['debt.record']
            debts = Debt.search(
                domain, limit=limit, offset=offset,
                order='issue_date desc, id desc',
            )
            total = Debt.search_count(domain)
            return self._json_response(
                data={
                    'debts': [self._fmt_debt(d) for d in debts],
                    'count': len(debts),
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                },
                message="Debts retrieved",
            )
        except Exception as e:
            _logger.error("Error listing debts: %s", e)
            return self._error_response("Error listing debts", 500, "DEBT_LIST_ERROR")

    # ------------------------------------------------------------------
    # Single-debt operations
    # ------------------------------------------------------------------

    @http.route('/api/v2/debts/<int:debt_id>', type='http', auth='none',
                methods=['GET', 'PUT', 'DELETE'], csrf=False, readonly=False)
    def handle_debt(self, debt_id):
        if request.httprequest.method == 'PUT':
            return self._update_debt(debt_id)
        if request.httprequest.method == 'DELETE':
            return self._cancel_debt(debt_id)
        return self._get_debt(debt_id)

    def _get_debt(self, debt_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            debt = request.env['debt.record'].browse(debt_id)
            if not debt.exists():
                return self._error_response("Debt not found", 404, "DEBT_NOT_FOUND")
            result = self._fmt_debt(debt)
            result['payments'] = [self._fmt_payment(p) for p in debt.payment_ids]
            result['notifications'] = [{
                'id': n.id,
                'type': n.notification_type,
                'channel': n.channel,
                'message': n.message,
                'sent_at': str(n.sent_at) if n.sent_at else None,
                'status': n.status,
            } for n in debt.notification_ids.sorted('sent_at', reverse=True)[:10]]
            return self._json_response(data=result, message="Debt details retrieved")
        except Exception as e:
            _logger.error("Error getting debt %s: %s", debt_id, e)
            return self._error_response(
                "Error retrieving debt", 500, "DEBT_GET_ERROR",
            )

    def _update_debt(self, debt_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'write')
        if acl_err:
            return acl_err
        data, err = self._parse_json()
        if err:
            return err
        try:
            debt = request.env['debt.record'].browse(debt_id)
            if not debt.exists():
                return self._error_response("Debt not found", 404, "DEBT_NOT_FOUND")
            allowed = {'due_date', 'interest_rule_id', 'notes', 'state'}
            vals = {k: v for k, v in data.items() if k in allowed}
            if 'interest_rule_id' in vals and vals['interest_rule_id']:
                vals['interest_rule_id'] = int(vals['interest_rule_id'])
            if vals:
                debt.write(vals)
            return self._json_response(
                data=self._fmt_debt(debt), message="Debt updated",
            )
        except ValidationError as e:
            return self._error_response(str(e), 400, "VALIDATION_ERROR")
        except Exception as e:
            _logger.error("Error updating debt %s: %s", debt_id, e)
            return self._error_response(
                "Error updating debt", 500, "DEBT_UPDATE_ERROR",
            )

    def _cancel_debt(self, debt_id):
        ok, user = self._auth()
        if not ok:
            return user
        # Cancel/delete requires unlink-level permission (admin only per ACL)
        admin_err = self._require_admin_or_manager(user)
        if admin_err:
            return admin_err
        try:
            debt = request.env['debt.record'].browse(debt_id)
            if not debt.exists():
                return self._error_response("Debt not found", 404, "DEBT_NOT_FOUND")
            debt.action_cancel()
            return self._json_response(
                data={'id': debt.id, 'name': debt.name, 'state': debt.state},
                message="Debt cancelled",
            )
        except ValidationError as e:
            return self._error_response(str(e), 400, "VALIDATION_ERROR")
        except Exception as e:
            _logger.error("Error cancelling debt %s: %s", debt_id, e)
            return self._error_response(
                "Error cancelling debt", 500, "DEBT_CANCEL_ERROR",
            )

    # ==================================================================
    # PAYMENTS
    # ==================================================================

    @http.route('/api/v2/debts/<int:debt_id>/payments', type='http',
                auth='none', methods=['GET', 'POST'], csrf=False, readonly=False)
    def handle_payments(self, debt_id):
        if request.httprequest.method == 'POST':
            return self._record_payment(debt_id)
        return self._list_payments(debt_id)

    def _record_payment(self, debt_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'write')
        if acl_err:
            return acl_err
        data, err = self._parse_json()
        if err:
            return err
        try:
            debt = request.env['debt.record'].browse(debt_id)
            if not debt.exists():
                return self._error_response("Debt not found", 404, "DEBT_NOT_FOUND")
            if debt.state not in ('active', 'overdue'):
                return self._error_response(
                    "Can only record payments on active or overdue debts",
                    400, "INVALID_STATE",
                )
            if 'amount' not in data:
                return self._error_response(
                    "Missing required field: amount", 400, "MISSING_FIELDS",
                )
            amount = float(data['amount'])
            if amount <= 0:
                return self._error_response(
                    "Amount must be positive", 400, "INVALID_AMOUNT",
                )
            if amount > debt.amount_residual:
                return self._error_response(
                    f"Payment ({amount:.2f}) exceeds balance ({debt.amount_residual:.2f})",
                    400, "EXCESS_PAYMENT",
                )
            payment = request.env['debt.payment'].create({
                'debt_id': debt.id,
                'amount': amount,
                'payment_date': data.get('payment_date', date.today().isoformat()),
                'reference': data.get('reference', ''),
                'notes': data.get('notes', ''),
            })
            debt._check_auto_paid()
            return self._json_response(
                data={
                    'payment': self._fmt_payment(payment),
                    'debt_balance': debt.amount_residual,
                    'debt_state': debt.state,
                },
                message="Payment recorded",
                status_code=201,
            )
        except ValidationError as e:
            return self._error_response(str(e), 400, "VALIDATION_ERROR")
        except Exception as e:
            _logger.error("Error recording payment: %s", e)
            return self._error_response(
                "Error recording payment", 500, "PAYMENT_ERROR",
            )

    def _list_payments(self, debt_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            debt = request.env['debt.record'].browse(debt_id)
            if not debt.exists():
                return self._error_response("Debt not found", 404, "DEBT_NOT_FOUND")
            payments = debt.payment_ids.sorted('payment_date', reverse=True)
            return self._json_response(
                data={
                    'payments': [self._fmt_payment(p) for p in payments],
                    'count': len(payments),
                    'debt_balance': debt.amount_residual,
                },
                message="Payments retrieved",
            )
        except Exception as e:
            _logger.error("Error listing payments: %s", e)
            return self._error_response(
                "Error listing payments", 500, "PAYMENT_LIST_ERROR",
            )

    # ==================================================================
    # CUSTOMER DEBT
    # ==================================================================

    @http.route('/api/v2/debts/customer/<int:partner_id>', type='http',
                auth='none', methods=['GET'], csrf=False)
    def customer_debts(self, partner_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return self._error_response(
                    "Customer not found", 404, "PARTNER_NOT_FOUND",
                )
            args = request.httprequest.args
            limit = int(args.get('limit', 20))
            offset = int(args.get('offset', 0))
            domain = [('partner_id', '=', partner_id)]
            if args.get('state'):
                domain.append(('state', '=', args['state']))
            Debt = request.env['debt.record']
            debts = Debt.search(
                domain, limit=limit, offset=offset, order='issue_date desc',
            )
            total = Debt.search_count(domain)
            return self._json_response(
                data={
                    'customer': {'id': partner.id, 'name': partner.name},
                    'debts': [self._fmt_debt(d) for d in debts],
                    'count': len(debts),
                    'total': total,
                },
                message="Customer debts retrieved",
            )
        except Exception as e:
            _logger.error("Error getting customer debts: %s", e)
            return self._error_response(
                "Error retrieving customer debts", 500, "CUSTOMER_DEBTS_ERROR",
            )

    @http.route('/api/v2/debts/customer/<int:partner_id>/summary', type='http',
                auth='none', methods=['GET'], csrf=False)
    def customer_debt_summary(self, partner_id):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return self._error_response(
                    "Customer not found", 404, "PARTNER_NOT_FOUND",
                )
            all_debts = request.env['debt.record'].search([
                ('partner_id', '=', partner_id),
            ])
            active = all_debts.filtered(lambda d: d.state in ('active', 'overdue'))
            overdue = all_debts.filtered(lambda d: d.state == 'overdue')
            paid = all_debts.filtered(lambda d: d.state == 'paid')

            outstanding = sum(active.mapped('amount_residual'))
            available = 0.0
            if partner.use_debt_limit and partner.max_debt_limit > 0:
                available = max(partner.max_debt_limit - outstanding, 0.0)

            return self._json_response(
                data={
                    'customer': {
                        'id': partner.id,
                        'name': partner.name,
                        'email': partner.email,
                    },
                    'summary': {
                        'total_debts': len(all_debts),
                        'active_debts': len(active),
                        'overdue_debts': len(overdue),
                        'paid_debts': len(paid),
                        'total_principal': sum(all_debts.mapped('amount')),
                        'total_interest': sum(all_debts.mapped('amount_interest')),
                        'total_paid': sum(all_debts.mapped('amount_paid')),
                        'current_outstanding': outstanding,
                        'max_debt_limit': (
                            partner.max_debt_limit if partner.use_debt_limit else None
                        ),
                        'available_credit': (
                            available if partner.use_debt_limit else None
                        ),
                        'debt_limit_enabled': partner.use_debt_limit,
                    },
                },
                message="Customer debt summary",
            )
        except Exception as e:
            _logger.error("Error getting customer summary: %s", e)
            return self._error_response(
                "Error retrieving customer summary", 500, "SUMMARY_ERROR",
            )

    @http.route('/api/v2/debts/customer/<int:partner_id>/limit', type='http',
                auth='none', methods=['PUT'], csrf=False, readonly=False)
    def set_customer_limit(self, partner_id):
        ok, user = self._auth()
        if not ok:
            return user
        # Setting debt limits is an admin/manager operation
        admin_err = self._require_admin_or_manager(user)
        if admin_err:
            return admin_err
        data, err = self._parse_json()
        if err:
            return err
        try:
            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return self._error_response(
                    "Customer not found", 404, "PARTNER_NOT_FOUND",
                )
            vals = {}
            if 'max_debt_limit' in data:
                vals['max_debt_limit'] = float(data['max_debt_limit'])
            if 'use_debt_limit' in data:
                vals['use_debt_limit'] = bool(data['use_debt_limit'])
            if not vals:
                return self._error_response(
                    "Provide max_debt_limit and/or use_debt_limit", 400, "NO_DATA",
                )
            partner.sudo().write(vals)
            return self._json_response(
                data={
                    'partner_id': partner.id,
                    'name': partner.name,
                    'use_debt_limit': partner.use_debt_limit,
                    'max_debt_limit': partner.max_debt_limit,
                    'current_debt_total': partner.current_debt_total,
                },
                message="Customer debt limit updated",
            )
        except Exception as e:
            _logger.error("Error setting limit: %s", e)
            return self._error_response(
                "Error updating customer limit", 500, "LIMIT_ERROR",
            )

    # ==================================================================
    # OVERDUE & ANALYTICS
    # ==================================================================

    @http.route('/api/v2/debts/overdue', type='http', auth='none',
                methods=['GET'], csrf=False)
    def overdue_debts(self):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            args = request.httprequest.args
            limit = int(args.get('limit', 50))
            offset = int(args.get('offset', 0))
            domain = [('state', '=', 'overdue'), ('amount_residual', '>', 0)]
            Debt = request.env['debt.record']
            debts = Debt.search(
                domain, limit=limit, offset=offset, order='due_date asc',
            )
            total = Debt.search_count(domain)
            today = date.today()
            results = []
            for d in debts:
                item = self._fmt_debt(d)
                item['days_overdue'] = (today - d.due_date).days
                results.append(item)
            return self._json_response(
                data={'debts': results, 'count': len(results), 'total': total},
                message="Overdue debts retrieved",
            )
        except Exception as e:
            _logger.error("Error getting overdue debts: %s", e)
            return self._error_response(
                "Error retrieving overdue debts", 500, "OVERDUE_ERROR",
            )

    @http.route('/api/v2/debts/analytics/overview', type='http', auth='none',
                methods=['GET'], csrf=False)
    def debt_analytics(self):
        ok, user = self._auth()
        if not ok:
            return user
        # Analytics overview requires admin or manager
        admin_err = self._require_admin_or_manager(user)
        if admin_err:
            return admin_err
        try:
            Debt = request.env['debt.record']
            all_debts = Debt.search([('state', '!=', 'cancelled')])
            active = all_debts.filtered(lambda d: d.state in ('active', 'overdue'))
            overdue = all_debts.filtered(lambda d: d.state == 'overdue')
            paid = all_debts.filtered(lambda d: d.state == 'paid')

            total_principal = sum(all_debts.mapped('amount'))
            total_interest = sum(all_debts.mapped('amount_interest'))
            total_outstanding = sum(active.mapped('amount_residual'))
            total_overdue_amt = sum(overdue.mapped('amount_residual'))
            total_collected = sum(all_debts.mapped('amount_paid'))

            gross = total_principal + total_interest
            collection_rate = round(total_collected / gross * 100, 2) if gross else 0.0

            partner_map = {}
            for d in active:
                pid = d.partner_id.id
                if pid not in partner_map:
                    partner_map[pid] = {
                        'id': pid, 'name': d.partner_id.name,
                        'outstanding': 0.0, 'count': 0,
                    }
                partner_map[pid]['outstanding'] += d.amount_residual
                partner_map[pid]['count'] += 1
            top_debtors = sorted(
                partner_map.values(), key=lambda x: x['outstanding'], reverse=True,
            )[:10]

            return self._json_response(
                data={
                    'kpis': {
                        'total_debts': len(all_debts),
                        'active_debts': len(active),
                        'overdue_debts': len(overdue),
                        'paid_debts': len(paid),
                        'total_principal': total_principal,
                        'total_interest': total_interest,
                        'total_outstanding': total_outstanding,
                        'total_overdue_amount': total_overdue_amt,
                        'total_collected': total_collected,
                        'collection_rate': collection_rate,
                    },
                    'top_debtors': top_debtors,
                },
                message="Debt analytics overview",
            )
        except Exception as e:
            _logger.error("Error getting debt analytics: %s", e)
            return self._error_response(
                "Error retrieving analytics", 500, "ANALYTICS_ERROR",
            )

    # ==================================================================
    # NOTIFICATIONS
    # ==================================================================

    @http.route('/api/v2/debts/notifications', type='http', auth='none',
                methods=['GET'], csrf=False)
    def list_notifications(self):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            args = request.httprequest.args
            limit = int(args.get('limit', 50))
            offset = int(args.get('offset', 0))
            domain = []
            if args.get('partner_id'):
                domain.append(('partner_id', '=', int(args['partner_id'])))
            if args.get('type'):
                domain.append(('notification_type', '=', args['type']))

            Notif = request.env['debt.notification.log']
            notifs = Notif.search(
                domain, limit=limit, offset=offset, order='sent_at desc',
            )
            total = Notif.search_count(domain)
            return self._json_response(
                data={
                    'notifications': [{
                        'id': n.id,
                        'debt': {'id': n.debt_id.id, 'name': n.debt_id.name},
                        'partner': {'id': n.partner_id.id, 'name': n.partner_id.name},
                        'type': n.notification_type,
                        'channel': n.channel,
                        'message': n.message,
                        'sent_at': str(n.sent_at) if n.sent_at else None,
                        'status': n.status,
                    } for n in notifs],
                    'count': len(notifs),
                    'total': total,
                },
                message="Notifications retrieved",
            )
        except Exception as e:
            _logger.error("Error listing notifications: %s", e)
            return self._error_response(
                "Error listing notifications", 500, "NOTIFICATION_ERROR",
            )

    # ==================================================================
    # INTEREST RULES
    # ==================================================================

    @http.route('/api/v2/debts/interest-rules', type='http', auth='none',
                methods=['GET', 'POST'], csrf=False, readonly=False)
    def handle_interest_rules(self):
        if request.httprequest.method == 'POST':
            return self._create_interest_rule()
        return self._list_interest_rules()

    def _list_interest_rules(self):
        ok, user = self._auth()
        if not ok:
            return user
        acl_err = self._check_debt_access(user, 'read')
        if acl_err:
            return acl_err
        try:
            rules = request.env['debt.interest.rule'].search([('active', '=', True)])
            return self._json_response(
                data={
                    'rules': [{
                        'id': r.id,
                        'name': r.name,
                        'rate': r.rate,
                        'cycle': r.cycle,
                        'compound': r.compound,
                    } for r in rules],
                    'count': len(rules),
                },
                message="Interest rules retrieved",
            )
        except Exception as e:
            _logger.error("Error listing interest rules: %s", e)
            return self._error_response(
                "Error listing interest rules", 500, "RULE_LIST_ERROR",
            )

    def _create_interest_rule(self):
        ok, user = self._auth()
        if not ok:
            return user
        # Creating interest rules requires admin (per ACL: only group_system can create)
        admin_err = self._require_admin_or_manager(user)
        if admin_err:
            return admin_err
        data, err = self._parse_json()
        if err:
            return err
        try:
            missing = [f for f in ('name', 'rate', 'cycle') if f not in data]
            if missing:
                return self._error_response(
                    f"Missing required fields: {', '.join(missing)}",
                    400, "MISSING_FIELDS",
                )
            valid_cycles = (
                'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'yearly',
            )
            if data['cycle'] not in valid_cycles:
                return self._error_response(
                    f"Invalid cycle. Must be one of: {', '.join(valid_cycles)}",
                    400, "INVALID_CYCLE",
                )
            rule = request.env['debt.interest.rule'].create({
                'name': data['name'],
                'rate': float(data['rate']),
                'cycle': data['cycle'],
                'compound': bool(data.get('compound', False)),
            })
            return self._json_response(
                data={
                    'id': rule.id, 'name': rule.name,
                    'rate': rule.rate, 'cycle': rule.cycle,
                    'compound': rule.compound,
                },
                message="Interest rule created",
                status_code=201,
            )
        except Exception as e:
            _logger.error("Error creating interest rule: %s", e)
            return self._error_response(
                "Error creating interest rule", 500, "RULE_CREATE_ERROR",
            )
