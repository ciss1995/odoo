# -*- coding: utf-8 -*-

import json
import logging
import os
import secrets
import string
import time as _time
from datetime import datetime, timedelta
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

_logger = logging.getLogger(__name__)

BLOCKED_MODELS = frozenset({
    'api.session',
    'ir.cron',
    'ir.rule',
    'ir.model.access',
    'res.users.apikeys',
    'ir.attachment',
    'base.module.update',
    'ir.config_parameter',
    'ir.module.module',
    'ir.actions.server',
    'base.automation',
    'ir.model.data',
})


class SimpleApiController(http.Controller):
    """Simple working API controller without decorators."""

    def _json_response(self, data=None, success=True, message=None, status_code=200):
        """Create a standardized JSON response."""
        response_data = {
            'success': success,
            'data': data,
            'message': message
        }

        response = request.make_response(
            json.dumps(response_data, default=str),
            headers=[('Content-Type', 'application/json')]
        )
        response.status_code = status_code
        self._log_api_call(status_code)
        return response

    def _json_response_sensitive(self, data=None, success=True, message=None, status_code=200):
        """JSON response with no-cache headers for credential-bearing responses."""
        response_data = {
            'success': success,
            'data': data,
            'message': message
        }

        response = request.make_response(
            json.dumps(response_data, default=str),
            headers=[
                ('Content-Type', 'application/json'),
                ('Cache-Control', 'no-store, no-cache, must-revalidate'),
                ('Pragma', 'no-cache'),
            ]
        )
        response.status_code = status_code
        self._log_api_call(status_code)
        return response

    def _error_response(self, message, status_code=400, error_code=None):
        """Create a standardized error response."""
        error_data = {
            'success': False,
            'error': {
                'message': message,
                'code': error_code
            }
        }

        response = request.make_response(
            json.dumps(error_data, default=str),
            headers=[('Content-Type', 'application/json')]
        )
        response.status_code = status_code
        self._log_api_call(status_code)
        return response

    MAX_PAGE_LIMIT = 1000

    def _is_model_blocked(self, model_name):
        """Check if a model is blocked from generic API access."""
        return model_name in BLOCKED_MODELS

    def _parse_pagination(self):
        """Parse and validate limit/offset query parameters.

        Returns (limit, offset) capped to safe bounds, or raises ValueError.
        """
        try:
            limit = int(request.httprequest.args.get('limit', 10))
            offset = int(request.httprequest.args.get('offset', 0))
        except (ValueError, TypeError):
            return None, None
        limit = max(1, min(limit, self.MAX_PAGE_LIMIT))
        offset = max(0, offset)
        return limit, offset

    def _authenticate(self):
        """Authenticate via API key using Odoo 19's hashed key verification."""
        request.httprequest._api_start_time = _time.time()
        api_key = request.httprequest.headers.get('api-key')
        if not api_key:
            return False, self._error_response("Missing API key", 401, "MISSING_API_KEY")

        try:
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=api_key)
            if not user_id:
                return False, self._error_response("Invalid API key", 403, "INVALID_API_KEY")

            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists() or not user.active:
                return False, self._error_response("User account inactive", 403, "INACTIVE_USER")

            # Per-user rate limit check
            rate_error = self._enforce_user_rate_limit(user)
            if rate_error:
                return False, rate_error

            request.update_env(user=user.id)
            return True, user

        except Exception as e:
            _logger.error("Authentication error: %s", str(e))
            return False, self._error_response("Authentication error", 500, "AUTH_ERROR")

    def _authenticate_session(self):
        """Authenticate user with session token (compared via hash)."""
        request.httprequest._api_start_time = _time.time()
        session_token = request.httprequest.headers.get('session-token')
        if not session_token:
            return False, self._error_response("Session token required", 401, "MISSING_SESSION_TOKEN")

        try:
            token_hash = request.env['api.session']._hash_token(session_token)
            session = request.env['api.session'].sudo().search([
                ('token', '=', token_hash),
                ('active', '=', True),
                ('expires_at', '>', datetime.now())
            ], limit=1)

            if not session:
                return False, self._error_response("Invalid or expired session", 401, "INVALID_SESSION")

            try:
                session.sudo().write({'last_activity': datetime.now()})
            except Exception as write_error:
                _logger.warning("Could not update session last_activity: %s", str(write_error))

            # Per-user rate limit check
            rate_error = self._enforce_user_rate_limit(session.user_id)
            if rate_error:
                return False, rate_error

            request.update_env(user=session.user_id.id)
            return True, session.user_id
        except Exception as e:
            _logger.error("Session authentication error: %s", str(e))
            return False, self._error_response("Session authentication failed", 500, "SESSION_AUTH_ERROR")

    def _check_model_access(self, model_name, operation='read'):
        """Check if current user has access to the model and operation."""
        try:
            model = request.env[model_name]
            model.check_access_rights(operation)
            return True
        except AccessError:
            return False

    def _filter_readable_fields(self, model_obj, fields_list):
        """Remove fields the current user cannot safely read.

        Filters out:
        1. Fields with a ``groups`` attribute the user does not satisfy.
        2. One2many / Many2many fields whose comodel the user has no read
           access to (reading them triggers ``check_access_rights`` on the
           related model, which would raise ``AccessError``).
        """
        user = request.env.user
        safe = []
        for fname in fields_list:
            field_def = model_obj._fields.get(fname)
            if not field_def:
                continue
            # 1. Field-level group restriction
            if field_def.groups and not user.has_groups(field_def.groups):
                continue
            # 2. Relational fields — verify comodel is readable
            if field_def.type in ('one2many', 'many2many') and field_def.comodel_name:
                if not self._check_model_access(field_def.comodel_name, 'read'):
                    continue
            safe.append(fname)
        return safe or ['id']

    def _get_model_base_domain(self, model_name):
        """Return model-specific base domain filters.

        For res.partner: exclude internal employees and the current company
        so the endpoint only returns external partners. Employees and the
        company are accessible via hr.employee / hr endpoints instead.

        Supports ``partner_type`` query parameter for res.partner:
        - ``customer``  → customer_rank > 0
        - ``vendor``    → supplier_rank > 0
        - ``contact``   → customer_rank = 0 AND supplier_rank = 0
        """
        if model_name == 'res.partner':
            domain = []
            model_obj = request.env[model_name]
            # Exclude employee-linked partners (field added by hr module)
            if 'employee' in model_obj._fields:
                domain.append(('employee', '=', False))
            # Exclude the current user's company partner record
            company_partner = request.env.company.partner_id
            if company_partner:
                domain.append(('id', '!=', company_partner.id))
            # Filter by partner type if requested
            partner_type = request.httprequest.args.get('partner_type', '').lower()
            if partner_type == 'customer':
                domain.append(('customer_rank', '>', 0))
            elif partner_type == 'vendor':
                domain.append(('supplier_rank', '>', 0))
            elif partner_type == 'contact':
                domain.append(('customer_rank', '=', 0))
                domain.append(('supplier_rank', '=', 0))
            return domain
        return []

    MODULE_ACCESS_MAP = {
        'crm':        {'model': 'crm.lead',          'label': 'CRM'},
        'sales':      {'model': 'sale.order',         'label': 'Sales'},
        'hr':         {'model': 'hr.employee',        'label': 'Employees'},
        'accounting': {'model': 'account.move',       'label': 'Accounting'},
        'inventory':  {'model': 'stock.picking',      'label': 'Inventory'},
        'purchase':   {'model': 'purchase.order',     'label': 'Purchase'},
        'contacts':   {'model': 'res.partner',        'label': 'Contacts'},
        'products':   {'model': 'product.template',   'label': 'Products'},
        'project':    {'model': 'project.project',    'label': 'Project'},
        'calendar':   {'model': 'calendar.event',     'label': 'Calendar'},
        'debt':       {'model': 'debt.record',        'label': 'Debt Management'},
    }

    # Maps module keys to the minimum Odoo group required for access.
    # Admin (base.group_system) bypasses all checks.
    MODULE_REQUIRED_GROUP = {
        'crm':        'sales_team.group_sale_salesman',
        'sales':      'sales_team.group_sale_salesman',
        'accounting': 'account.group_account_invoice',
        'invoicing':  'account.group_account_invoice',
        'inventory':  'stock.group_stock_user',
        'purchase':   'purchase.group_purchase_user',
        'hr':         'hr.group_hr_user',
        'project':    'project.group_project_user',
    }

    def _user_has_module_role(self, user, module_key):
        """Check if user has the required group for a module. Admins always pass."""
        if user.has_group('base.group_system'):
            return True
        required = self.MODULE_REQUIRED_GROUP.get(module_key)
        if not required:
            return True  # No group requirement (contacts, products, calendar, debt)
        return user.has_group(required)

    def _get_enforcer(self):
        """Get the SubscriptionEnforcer singleton. Returns None if enforcement is disabled."""
        from odoo.addons.base_api.services.subscription_enforcer import SubscriptionEnforcer
        return SubscriptionEnforcer.get_instance()

    def _enforce_subscription(self):
        """Check subscription is active. Returns None if OK, or an error response if not.

        Call this at the top of every authenticated endpoint, right after auth succeeds.
        If enforcement is disabled (no Control Plane), returns None (always OK).
        """
        enforcer = self._get_enforcer()
        if enforcer is None:
            return None
        allowed, error = enforcer.check_subscription_active()
        if not allowed:
            return self._error_response(error['message'], error['status_code'], error['code'])
        return None

    def _enforce_module_access(self, model_name):
        """Check if the model's module is in the tenant's plan. Returns None if OK, or error response.

        If the model doesn't map to any module (system model), allow access.
        If enforcement is disabled, returns None.
        """
        enforcer = self._get_enforcer()
        if enforcer is None:
            return None
        from odoo.addons.base_api.services.module_resolver import resolve_module_key
        module_key = resolve_module_key(model_name)
        if module_key is None:
            return None  # System model, always accessible
        allowed, error = enforcer.check_module_allowed(module_key)
        if not allowed:
            _logger.warning(
                "Module access denied: model=%s module_key=%s code=%s",
                model_name, module_key, error.get('code'),
            )
            return self._error_response(error['message'], error['status_code'], error['code'])
        return None

    def _enforce_api_quota(self):
        """Check API call quota. Returns None if OK, or error response if exceeded."""
        enforcer = self._get_enforcer()
        if enforcer is None:
            return None
        allowed, error = enforcer.check_api_quota()
        if not allowed:
            return self._error_response(error['message'], error['status_code'], error['code'])
        return None

    def _enforce_user_rate_limit(self, user):
        """Check per-user API rate limit. Returns None if OK, or 429 error response."""
        from odoo.addons.base_api.services.rate_limiter import check_api_rate_limit
        allowed, retry_after, remaining = check_api_rate_limit(user.id)
        if not allowed:
            response = self._error_response(
                f"Rate limit exceeded. Try again in {retry_after} seconds.",
                429, "RATE_LIMITED"
            )
            response.headers['Retry-After'] = str(retry_after)
            response.headers['X-RateLimit-Limit'] = str(120)
            response.headers['X-RateLimit-Remaining'] = '0'
            return response
        return None

    def _get_record_scope_domain(self, model_name, user):
        """Return a domain that restricts records to those the user should see.

        Admins see everything. For other users, scoping is model-specific:
        - CRM/Sales: own records + team records + unassigned
        - Accounting: accounting group sees all; others see own invoices
        - Purchase: purchase group sees all; others see own POs
        - HR: HR managers see all; others see self + department
        - Project: visible projects (employee-visible or user is member/creator)
        - Tasks: assigned to user or in visible projects
        - Activities: own only
        - Calendar: own events + events user is invited to
        - Products/Contacts: no additional scoping (shared resources)
        """
        if user.has_group('base.group_system'):
            return []

        uid = user.id

        if model_name == 'crm.lead':
            team_ids = user.crm_team_ids.ids if 'crm_team_ids' in user._fields else []
            if team_ids:
                return ['|', '|',
                        ('user_id', '=', uid),
                        ('user_id', '=', False),
                        ('team_id', 'in', team_ids)]
            return ['|', ('user_id', '=', uid), ('user_id', '=', False)]

        if model_name == 'sale.order':
            team_ids = user.crm_team_ids.ids if 'crm_team_ids' in user._fields else []
            if team_ids:
                return ['|', '|',
                        ('user_id', '=', uid),
                        ('user_id', '=', False),
                        ('team_id', 'in', team_ids)]
            return ['|', ('user_id', '=', uid), ('user_id', '=', False)]

        if model_name == 'account.move':
            if user.has_group('account.group_account_invoice'):
                return []
            return ['|',
                    ('invoice_user_id', '=', uid),
                    ('invoice_user_id', '=', False)]

        if model_name == 'purchase.order':
            if user.has_group('purchase.group_purchase_user'):
                return []
            return [('user_id', '=', uid)]

        if model_name == 'mail.activity':
            return [('user_id', '=', uid)]

        if model_name == 'hr.employee':
            # HR Officers and Managers see all employees
            if user.has_group('hr.group_hr_user'):
                return []
            employee = user.employee_id if 'employee_id' in user._fields else False
            if employee and employee.department_id:
                return ['|',
                        ('user_id', '=', uid),
                        ('department_id', '=', employee.department_id.id)]
            return [('user_id', '=', uid)]

        if model_name in ('hr.contract', 'hr.resume.line'):
            if user.has_group('hr.group_hr_user'):
                return []
            employee = user.employee_id if 'employee_id' in user._fields else False
            if employee and employee.department_id:
                return ['|',
                        ('employee_id.user_id', '=', uid),
                        ('employee_id.department_id', '=', employee.department_id.id)]
            return [('employee_id.user_id', '=', uid)]

        if model_name == 'project.project':
            return ['|', '|',
                    ('favorite_user_ids', 'in', [uid]),
                    ('privacy_visibility', '=', 'employees'),
                    ('create_uid', '=', uid)]

        if model_name == 'project.task':
            return ['|',
                    ('user_ids', 'in', [uid]),
                    ('project_id.privacy_visibility', '=', 'employees')]

        if model_name == 'calendar.event':
            partner_id = user.partner_id.id if user.partner_id else False
            if partner_id:
                return ['|',
                        ('user_id', '=', uid),
                        ('partner_ids', 'in', [partner_id])]
            return [('user_id', '=', uid)]

        # product.template, res.partner, and other models: no additional scoping
        return []

    def _log_api_call(self, status_code):
        """Log an API call to the usage tracker. Non-blocking, best-effort."""
        start_time = getattr(request.httprequest, '_api_start_time', None)
        if start_time is None:
            return
        try:
            from odoo.addons.base_api.services.api_call_logger import ApiCallLogger
            logger = ApiCallLogger.get_instance()
            if logger is not None:
                response_ms = int((_time.time() - start_time) * 1000)
                method = request.httprequest.method or 'GET'
                logger.log_call(method, status_code, response_ms)
        except Exception:
            pass  # Best-effort, never break the response

    def _get_module_access(self):
        """Return a dict of module_key → {accessible, in_plan, label, model, models} for the current user.

        When the Control Plane is configured, ``in_plan`` reflects the actual
        plan response.  If the CP is unreachable **and** enforcement is enabled,
        ``in_plan`` is reported as ``False`` so the frontend stays consistent
        with what ``_enforce_module_access`` will actually allow at request time.
        When enforcement is disabled (no env vars), everything is in-plan.

        ``models`` is a dict mapping every model in the module (primary +
        secondary) to its individual ACL status.  The frontend should consult
        this before querying a secondary model like ``stock.move``.
        """
        from odoo.addons.base_api.services.module_resolver import MODEL_TO_MODULE

        enforcer = self._get_enforcer()
        plan_modules = None
        cp_unreachable = False
        if enforcer is not None:
            try:
                info = enforcer.get_tenant_info()
                plan_modules = info.get('effective', {}).get('allowed_modules', [])
            except Exception:
                cp_unreachable = True

        # Build reverse map: module_key → [model_name, ...]
        module_models = {}
        for model_name, mod_key in MODEL_TO_MODULE.items():
            module_models.setdefault(mod_key, []).append(model_name)

        result = {}
        for key, info in self.MODULE_ACCESS_MAP.items():
            primary_model = info['model']
            has_acl = (
                primary_model in request.env
                and not self._is_model_blocked(primary_model)
                and self._check_model_access(primary_model, 'read')
            )
            # Determine if module is in plan — must match _enforce_module_access behaviour
            if enforcer is None:
                in_plan = True  # No enforcement configured
            elif cp_unreachable:
                in_plan = False  # CP down → strict, same as _enforce_module_access
            elif '__all__' in plan_modules:
                in_plan = True
            else:
                in_plan = key in plan_modules

            # Per-model ACL check for all models in this module
            per_model = {}
            for m in module_models.get(key, [primary_model]):
                if m in request.env and not self._is_model_blocked(m):
                    per_model[m] = self._check_model_access(m, 'read')
                else:
                    per_model[m] = False

            result[key] = {
                'accessible': has_acl and in_plan,
                'in_plan': in_plan,
                'label': info['label'],
                'model': primary_model,
                'models': per_model,
            }
        return result

    # ===== CORS PREFLIGHT =====

    @http.route(
        ['/api/v2/<path:subpath>'],
        type='http', auth='none', methods=['OPTIONS'], csrf=False,
    )
    def cors_preflight(self, subpath=None):
        """Handle CORS preflight for all /api/v2/* endpoints."""
        from odoo.addons.base_api.models.cors import _origin_allowed, _set_cors_headers
        origin = request.httprequest.headers.get('Origin', '')
        resp = request.make_response('', status=204)
        if _origin_allowed(origin):
            _set_cors_headers(resp, origin)
        return resp

    # ===== WORKING ENDPOINTS =====

    @http.route('/api/v2/test', type='http', auth='none', methods=['GET'], csrf=False)
    def test_basic(self):
        """Basic API test (no authentication required)."""
        return self._json_response(
            data={'message': 'API v2 is working!', 'version': '2.0'},
            message="Basic test successful"
        )

    @http.route('/api/v2/public/branding', type='http', auth='none', methods=['GET'], csrf=False)
    def public_branding(self):
        """Return tenant branding info (company name) so the SPA can render
        the right name on the login screen and main shell. Public, unauth.

        Single-tenant per Odoo: there is only ever one company that matters,
        the main one created at provisioning time. We pick the lowest id as
        the canonical "main" company.
        """
        company_name = None
        try:
            company = request.env['res.company'].sudo().search([], order='id asc', limit=1)
            if company:
                company_name = company.name
        except Exception:
            pass
        return self._json_response(data={'company_name': company_name})

    @http.route('/api/v2/auth/test', type='http', auth='none', methods=['GET'], csrf=False)
    def test_auth(self):
        """Test authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result
        
        user = result
        return self._json_response(
            data={
                'user_id': user.id,
                'user_name': user.name,
                'user_login': user.login,
                'authenticated': True
            },
            message="Authentication test successful"
        )

    @http.route('/api/v2/user/info', type='http', auth='none', methods=['GET'], csrf=False)
    def user_info(self):
        """Get authenticated user information."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        user = result
        return self._json_response(
            data={
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'login': user.login,
                    'email': user.email,
                    'active': user.active,
                    'company_id': [user.company_id.id, user.company_id.name] if user.company_id else False,
                },
                'api_version': '2.0',
                'database': request.env.cr.dbname
            },
            message="User information retrieved successfully"
        )

    @http.route('/api/v2/partners', type='http', auth='none', methods=['GET'], csrf=False)
    def list_partners(self):
        """List partners with authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            Partner = request.env['res.partner']
            
            # Get parameters from URL
            limit, offset = self._parse_pagination()
            if limit is None:
                return self._error_response("limit and offset must be integers", 400, "INVALID_PARAMS")
            customers_only = request.httprequest.args.get('customers_only', 'true').lower() == 'true'
            
            # Build domain
            domain = [('active', '=', True)]
            if customers_only:
                domain.append(('customer_rank', '>', 0))
            
            # Search partners
            partners = Partner.search(domain, limit=limit, offset=offset, order='name')
            
            # Prepare data
            partners_data = []
            for partner in partners:
                partners_data.append({
                    'id': partner.id,
                    'name': partner.name,
                    'email': partner.email,
                    'phone': partner.phone,
                    'is_company': partner.is_company,
                    'customer_rank': partner.customer_rank,
                    'city': partner.city,
                    'country': partner.country_id.name if partner.country_id else False,
                })
            
            return self._json_response(
                data={
                    'partners': partners_data,
                    'count': len(partners_data),
                    'total_count': Partner.search_count(domain)
                },
                message="Partners retrieved successfully"
            )
            
        except Exception as e:
            _logger.error("Error listing partners: %s", str(e))
            return self._error_response("Error retrieving partners", 500, "PARTNERS_ERROR")

    @http.route('/api/v2/products', type='http', auth='none', methods=['GET'], csrf=False)
    def list_products(self):
        """List products with authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            Product = request.env['product.template']
            
            # Get parameters
            limit, offset = self._parse_pagination()
            if limit is None:
                return self._error_response("limit and offset must be integers", 400, "INVALID_PARAMS")
            sale_ok = request.httprequest.args.get('sale_ok', 'true').lower() == 'true'
            
            # Build domain
            domain = [('active', '=', True)]
            if sale_ok:
                domain.append(('sale_ok', '=', True))
            
            # Search products
            products = Product.search(domain, limit=limit, offset=offset, order='name')
            
            # Prepare data
            products_data = []
            for product in products:
                products_data.append({
                    'id': product.id,
                    'name': product.name,
                    'default_code': product.default_code,
                    'list_price': product.list_price,
                    'sale_ok': product.sale_ok,
                    'category': product.categ_id.name if product.categ_id else False,
                })
            
            return self._json_response(
                data={
                    'products': products_data,
                    'count': len(products_data),
                    'total_count': Product.search_count(domain)
                },
                message="Products retrieved successfully"
            )
            
        except Exception as e:
            _logger.error("Error listing products: %s", str(e))
            return self._error_response("Error retrieving products", 500, "PRODUCTS_ERROR")

    @http.route('/api/v2/search/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
    def search_model(self, model):
        """Search any model with authentication and field specification."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Validate model
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")

            if self._is_model_blocked(model):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Module access enforcement (subscription plan)
            module_error = self._enforce_module_access(model)
            if module_error:
                return module_error

            # Check user access to model (ir.model.access ACL)
            if not self._check_model_access(model, 'read'):
                _logger.warning(
                    "ACL denied: user %s (id=%s) lacks read rights on %s",
                    user.login, user.id, model,
                )
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            model_obj = request.env[model]


            # Get parameters
            limit, offset = self._parse_pagination()
            if limit is None:
                return self._error_response("limit and offset must be integers", 400, "INVALID_PARAMS")
            fields_param = request.httprequest.args.get('fields', '')
            
            # Handle field specification
            if fields_param:
                requested_fields = [f.strip() for f in fields_param.split(',')]
                # Add 'id' if not present (always needed)
                if 'id' not in requested_fields:
                    requested_fields.insert(0, 'id')
                # Validate fields exist in model
                available_fields = [f for f in requested_fields if f in model_obj._fields]
                if not available_fields:
                    return self._error_response("No valid fields specified", 400, "INVALID_FIELDS")
            else:
                # Default basic fields
                basic_fields = ['id', 'name', 'display_name']
                available_fields = [f for f in basic_fields if f in model_obj._fields]
            
            # Filter out fields the user cannot read (field-level group restrictions).
            # check_access_rights only validates model-level access; individual
            # fields may require additional groups (e.g. groups="account.group_...").
            available_fields = self._filter_readable_fields(model_obj, available_fields)

            # Model-specific base domain (e.g. res.partner excludes employees)
            domain = self._get_model_base_domain(model)

            # Parse explicit domain filter (JSON-encoded Odoo domain)
            domain_param = request.httprequest.args.get('domain', '').strip()
            has_custom_filters = False
            if domain_param:
                try:
                    parsed = json.loads(domain_param)
                    if isinstance(parsed, list):
                        # Validate each leaf is a valid (field, op, value) triple or a logic operator
                        for item in parsed:
                            if isinstance(item, (list, tuple)) and len(item) == 3:
                                field_name = item[0]
                                if isinstance(field_name, str) and field_name.split('.')[0] in model_obj._fields:
                                    domain.append(tuple(item))
                                    has_custom_filters = True
                            elif item in ('&', '|', '!'):
                                domain.append(item)
                except (json.JSONDecodeError, TypeError):
                    return self._error_response("Invalid domain parameter (must be JSON-encoded list)", 400, "INVALID_DOMAIN")

            # Handle additional filtering parameters from URL
            _RESERVED_PARAMS = frozenset(['limit', 'offset', 'fields', 'partner_type', 'domain'])
            for param_key, param_value in request.httprequest.args.items():
                if param_key in _RESERVED_PARAMS:
                    continue

                # Add domain filter for other parameters
                if param_key in model_obj._fields:
                    domain.append((param_key, '=', param_value))
                    has_custom_filters = True

            # Only add active filter if no custom filters and active field exists
            if not has_custom_filters and 'active' in model_obj._fields:
                domain.append(('active', '=', True))

            # Record-level scoping: restrict to records the user is allowed to see
            scope_domain = self._get_record_scope_domain(model, user)
            if scope_domain:
                domain = scope_domain + domain

            # Search records
            records = model_obj.search(domain, limit=limit, offset=offset, order='id')

            # Read specified fields
            records_data = records.read(available_fields)

            return self._json_response(
                data={
                    'records': records_data,
                    'count': len(records_data),
                    'model': model,
                    'fields': available_fields,
                    'total_count': model_obj.search_count(domain)
                },
                message=f"Found {len(records_data)} records in {model}"
            )

        except AccessError as e:
            _logger.warning(
                "AccessError during search on %s for user %s (id=%s): %s",
                model, user.login, user.id, e,
            )
            return self._error_response(
                f"Access denied: you do not have permission to read '{model}'. "
                f"Check that your user has the required module group.",
                403, "ACCESS_DENIED",
            )
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "SEARCH_ERROR")
        except Exception as e:
            _logger.error("Error searching %s: %s", model, str(e))
            return self._error_response("Error searching records", 500, "SEARCH_ERROR")

    @http.route('/api/v2/search/<string:model>/<int:record_id>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_record_by_id(self, model, record_id):
        """Get a specific record by ID with all its fields."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Validate model
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")

            if self._is_model_blocked(model):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Module access enforcement (subscription plan)
            module_error = self._enforce_module_access(model)
            if module_error:
                return module_error

            # Check user access to model (ir.model.access ACL)
            if not self._check_model_access(model, 'read'):
                _logger.warning(
                    "ACL denied: user %s (id=%s) lacks read rights on %s",
                    user.login, user.id, model,
                )
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            model_obj = request.env[model]

            # Verify record exists AND is within the user's allowed scope
            scope_domain = self._get_record_scope_domain(model, user)
            record_domain = [('id', '=', record_id)] + scope_domain
            record = model_obj.search(record_domain, limit=1)

            if not record:
                return self._error_response(f"Record with ID {record_id} not found in {model}", 404, "RECORD_NOT_FOUND")
            
            # Get all fields from the model
            all_fields = list(model_obj._fields.keys())

            # Get fields parameter to allow field filtering
            fields_param = request.httprequest.args.get('fields', '')
            if fields_param:
                requested_fields = [f.strip() for f in fields_param.split(',')]
                if 'id' not in requested_fields:
                    requested_fields.insert(0, 'id')
                available_fields = [f for f in requested_fields if f in model_obj._fields]
                if not available_fields:
                    return self._error_response("No valid fields specified", 400, "INVALID_FIELDS")
            else:
                available_fields = all_fields

            # Filter out fields with group restrictions the user doesn't satisfy
            available_fields = self._filter_readable_fields(model_obj, available_fields)

            # Read the record with specified fields
            record_data = record.read(available_fields)[0]
            
            return self._json_response(
                data={
                    'record': record_data,
                    'model': model,
                    'id': record_id,
                    'fields_returned': available_fields,
                    'total_fields_available': len(all_fields)
                },
                message=f"Found record {record_id} in {model}"
            )

        except AccessError as e:
            _logger.warning(
                "AccessError getting record %s/%s for user %s (id=%s): %s",
                model, record_id, user.login, user.id, e,
            )
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "GET_RECORD_ERROR")
        except Exception as e:
            _logger.error("Error getting record %s from %s: %s", record_id, model, str(e))
            return self._error_response("Error retrieving record", 500, "GET_RECORD_ERROR")

    @http.route('/api/v2/fields/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_model_fields(self, model):
        """Get all fields for a specific model."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Validate model exists
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")
            
            # Get model fields from ir.model.fields
            fields_obj = request.env['ir.model.fields']
            model_fields = fields_obj.search([('model', '=', model)], order='name')
            
            # Prepare field data
            fields_data = []
            for field in model_fields:
                fields_data.append({
                    'name': field.name,
                    'description': field.field_description,
                    'type': field.ttype,
                    'required': field.required,
                    'readonly': field.readonly,
                    'help': field.help or '',
                    'relation': field.relation or '',
                    'store': field.store
                })
            
            return self._json_response(
                data={
                    'model': model,
                    'fields': fields_data,
                    'count': len(fields_data)
                },
                message=f"Found {len(fields_data)} fields for model {model}"
            )
            
        except Exception as e:
            _logger.error("Error getting fields for %s: %s", model, str(e))
            return self._error_response("Error retrieving model fields", 500, "FIELDS_ERROR")

    @http.route('/api/v2/auth/login', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def user_login(self):
        """Authenticate user with username/password and create session."""
        try:
            # Per-IP login rate limiting
            from odoo.addons.base_api.services.rate_limiter import check_login_rate_limit
            client_ip = request.httprequest.environ.get(
                'HTTP_X_FORWARDED_FOR', request.httprequest.remote_addr
            )
            if client_ip and ',' in client_ip:
                client_ip = client_ip.split(',')[0].strip()
            allowed, retry_after = check_login_rate_limit(client_ip or 'unknown')
            if not allowed:
                response = self._error_response(
                    f"Too many login attempts. Try again in {retry_after} seconds.",
                    429, "RATE_LIMITED"
                )
                response.headers['Retry-After'] = str(retry_after)
                return response

            # Parse JSON data from HTTP request
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            username = data.get('username')
            password = data.get('password')

            if not username or not password:
                return self._error_response("Username and password required", 400, "MISSING_CREDENTIALS")

            try:
                credential = {
                    'login': username,
                    'password': password,
                    'type': 'password'
                }

                auth_info = request.session.authenticate(request.env, credential)

                if not auth_info or not auth_info.get('uid'):
                    return self._error_response("Invalid credentials", 401, "INVALID_CREDENTIALS")
                
                uid = auth_info['uid']
                user = request.env['res.users'].sudo().browse(uid)
                if not user.exists() or not user.active:
                    return self._error_response("User account inactive", 403, "INACTIVE_USER")
                
                session_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
                token_hash = request.env['api.session']._hash_token(session_token)
                expires_at = datetime.now() + timedelta(hours=24)
                
                request.env['api.session'].sudo().create({
                    'user_id': user.id,
                    'token': token_hash,
                    'expires_at': expires_at,
                    'created_at': datetime.now(),
                    'last_activity': datetime.now(),
                    'active': True
                })
                
                return self._json_response_sensitive(
                    data={
                        'session_token': session_token,
                        'expires_at': expires_at.isoformat(),
                        'user': {
                            'id': user.id,
                            'name': user.name,
                            'login': user.login,
                            'email': user.email,
                            'groups': [group.name for group in user.group_ids]
                        }
                    },
                    message="Login successful"
                )
                    
            except Exception as e:
                _logger.error("Authentication error for user %s: %s", username, str(e))
                return self._error_response("Authentication failed", 401, "AUTH_FAILED")
            
        except Exception as e:
            _logger.error("Login error: %s", str(e))
            return self._error_response("Login failed", 500, "LOGIN_ERROR")

    @http.route('/api/v2/auth/refresh', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def refresh_session(self):
        """Refresh session token to extend expiration."""
        session_token = request.httprequest.headers.get('session-token')
        if not session_token:
            return self._error_response("Session token required", 401, "MISSING_SESSION_TOKEN")
        
        try:
            token_hash = request.env['api.session']._hash_token(session_token)
            grace_period = datetime.now() - timedelta(hours=1)
            
            session = request.env['api.session'].sudo().search([
                ('token', '=', token_hash),
                ('active', '=', True),
                ('expires_at', '>', grace_period)
            ], limit=1)
            
            if not session:
                return self._error_response("Session not found or expired beyond refresh period", 401, "SESSION_NOT_REFRESHABLE")
            
            new_session_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
            new_token_hash = request.env['api.session']._hash_token(new_session_token)
            new_expires_at = datetime.now() + timedelta(hours=24)
            
            session.sudo().write({
                'token': new_token_hash,
                'expires_at': new_expires_at,
                'last_activity': datetime.now()
            })
            
            user = session.user_id
            
            return self._json_response_sensitive(
                data={
                    'session_token': new_session_token,
                    'expires_at': new_expires_at.isoformat(),
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email
                    }
                },
                message="Session refreshed successfully"
            )
            
        except Exception as e:
            _logger.error("Session refresh error: %s", str(e))
            return self._error_response("Session refresh failed", 500, "REFRESH_ERROR")

    @http.route('/api/v2/auth/logout', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def user_logout(self):
        """Logout user and invalidate session."""
        is_valid, result = self._authenticate_session()
        if not is_valid:
            return result
        
        try:
            session_token = request.httprequest.headers.get('session-token')
            token_hash = request.env['api.session']._hash_token(session_token)
            session = request.env['api.session'].sudo().search([('token', '=', token_hash)], limit=1)
            if session:
                session.sudo().write({'active': False})
            
            return self._json_response(message="Logout successful")
            
        except Exception as e:
            _logger.error("Logout error: %s", str(e))
            return self._error_response("Logout failed", 500, "LOGOUT_ERROR")

    @http.route('/api/v2/auth/me', type='http', auth='none', methods=['GET'], csrf=False)
    def current_user(self):
        """Get current authenticated user info."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Build plan info for the response
            plan_info = None
            enforcer = self._get_enforcer()
            if enforcer is not None:
                try:
                    tenant_info = enforcer.get_tenant_info()
                    effective = tenant_info.get('effective', {})
                    current_user_count = request.env['res.users'].sudo().search_count(
                        [('active', '=', True), ('share', '=', False)]
                    )
                    max_users = effective.get('max_users', -1)
                    plan_info = {
                        'slug': tenant_info.get('plan', {}).get('slug'),
                        'name': tenant_info.get('plan', {}).get('name'),
                        'max_users': max_users,
                        'current_users': current_user_count,
                        'can_create_users': max_users == -1 or current_user_count < max_users,
                        'allowed_modules': effective.get('allowed_modules', []),
                    }
                except Exception as e:
                    _logger.warning("Could not fetch plan info: %s", str(e))

            return self._json_response(
                data={
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email,
                        'active': user.active,
                        'company_id': [user.company_id.id, user.company_id.name] if user.company_id else False,
                        'groups': [{'id': g.id, 'name': g.name} for g in user.group_ids],
                        'permissions': {
                            'is_admin': user.has_group('base.group_system'),
                            'is_user': user.has_group('base.group_user'),
                            'can_manage_users': user.has_group('base.group_erp_manager')
                        },
                        'module_access': self._get_module_access(),
                        'plan': plan_info,
                    }
                },
                message="User information retrieved"
            )
        except Exception as e:
            _logger.error("Error getting user info: %s", str(e))
            return self._error_response("Error retrieving user info", 500, "USER_INFO_ERROR")

    @http.route('/api/v2/groups', type='http', auth='none', methods=['GET'], csrf=False)
    def get_available_groups(self):
        """Get all available user groups for assignment."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Check if user can manage users
            if not user.has_group('base.group_erp_manager') and not user.has_group('base.group_system'):
                return self._error_response("Access denied: User management required", 403, "ACCESS_DENIED")

            # Get all groups excluding system and technical ones
            hidden_category = request.env.ref('base.module_category_hidden', raise_if_not_found=False)
            domain = [('share', '=', False)]
            if hidden_category:
                domain.append(('privilege_id.category_id', '!=', hidden_category.id))
            groups = request.env['res.groups'].sudo().search(domain, order='privilege_id desc, name')

            groups_by_category = {}
            for group in groups:
                if group.privilege_id and group.privilege_id.category_id:
                    category_name = group.privilege_id.category_id.name
                elif group.privilege_id:
                    category_name = group.privilege_id.name
                else:
                    category_name = 'Other'
                if category_name not in groups_by_category:
                    groups_by_category[category_name] = []
                
                groups_by_category[category_name].append({
                    'id': group.id,
                    'name': group.name,
                    'full_name': group.full_name,
                    'xml_id': group.get_external_id().get(group.id, ''),
                    'comment': group.comment or '',
                    'users_count': group.all_users_count
                })

            return self._json_response(
                data={
                    'groups_by_category': groups_by_category,
                    'total_groups': len(groups)
                },
                message="Available groups retrieved"
            )

        except Exception as e:
            _logger.error("Error getting groups: %s", str(e))
            return self._error_response("Error retrieving groups", 500, "GROUPS_ERROR")

    @http.route('/api/v2/create/<string:model>', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def create_record(self, model):
        """Create a record with authentication."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            # Validate model
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")

            if self._is_model_blocked(model):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Module access enforcement
            module_error = self._enforce_module_access(model)
            if module_error:
                return module_error

            # Check user access to model
            if not self._check_model_access(model, 'create'):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            model_obj = request.env[model]

            # Special handling for user creation with groups
            if model == 'res.users':
                return self._create_user_with_groups(data)

            # Special handling for employee creation with job/department
            if model == 'hr.employee':
                return self._create_employee(data)

            # Convenience: map partner_type to Odoo rank fields
            if model == 'res.partner':
                partner_type = data.pop('partner_type', None)
                if partner_type == 'customer':
                    data.setdefault('customer_rank', 1)
                elif partner_type == 'vendor':
                    data.setdefault('supplier_rank', 1)

            # Create record
            new_record = model_obj.create(data)

            # Return a safe subset of fields to avoid post-create AccessError
            # on models where some fields are not readable by the creator.
            basic_fields = ['id', 'name', 'display_name', 'create_date']
            safe_fields = [f for f in basic_fields if f in request.env[model]._fields]
            if not safe_fields:
                safe_fields = ['id']
            record_data = new_record.read(safe_fields)[0]

            return self._json_response(
                data={
                    'id': new_record.id,
                    'record': record_data
                },
                message=f"Record created in {model}",
                status_code=201
            )

        except AccessError as e:
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "CREATE_ERROR")
        except Exception as e:
            _logger.error("Error creating %s: %s", model, str(e))
            return self._error_response("Error creating record", 500, "CREATE_ERROR")

    def _create_user_with_groups(self, data):
        """Create a user with groups and optionally set up employee info.

        Accepts employee convenience fields alongside user fields:
        - department_name  → looked up to department_id on the employee
        - job_title        → free-text job title on the employee
        - job_name         → looked up in hr.job to set job_id
        - parent_name      → looked up in hr.employee to set parent_id (manager)
        - work_phone       → work phone on the employee

        Odoo auto-creates an hr.employee record when a user gets internal
        access, so we find that record and update it with the HR fields.
        """
        # User limit enforcement
        enforcer = self._get_enforcer()
        if enforcer is not None:
            current_count = request.env['res.users'].sudo().search_count(
                [('active', '=', True), ('share', '=', False)]
            )
            allowed, error = enforcer.check_user_limit(current_count)
            if not allowed:
                return self._error_response(error['message'], error['status_code'], error['code'])

        try:
            # -- Extract employee-specific fields before user creation --
            employee_fields = {}
            for key in ('department_name', 'job_title', 'job_name', 'parent_name', 'work_phone'):
                val = data.pop(key, None)
                if val is not None:
                    employee_fields[key] = val

            group_names = data.pop('group_names', [])
            group_ids_param = data.pop('group_ids', [])
            auto_generate_credentials = data.pop('auto_generate_credentials', True)

            if 'password' not in data and auto_generate_credentials:
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
                data['password'] = temp_password
            else:
                temp_password = data.get('password', None)

            # Resolve group IDs before create so they can be set atomically
            resolved_group_ids = []
            if group_names:
                groups = request.env['res.groups'].sudo().search([('name', 'in', group_names)])
                resolved_group_ids = groups.ids
            elif group_ids_param:
                resolved_group_ids = group_ids_param
            else:
                default_group = request.env.ref('base.group_user', raise_if_not_found=False)
                if default_group:
                    resolved_group_ids = [default_group.id]

            if resolved_group_ids:
                data['group_ids'] = [(6, 0, resolved_group_ids)]

            user = request.env['res.users'].sudo().create(data)

            api_key = None
            if auto_generate_credentials:
                try:
                    key_env = request.env(user=user.id)
                    api_key = key_env['res.users.apikeys'].sudo()._generate(
                        scope=None,
                        name='Auto-generated API Key',
                        expiration_date=None,
                    )
                except Exception as e:
                    _logger.warning("Could not generate API key for user %s: %s", user.login, str(e))
                    api_key = None

            # -- Update the auto-created employee record with HR fields --
            employee_data = None
            if employee_fields and 'hr.employee' in request.env:
                employee = request.env['hr.employee'].sudo().search(
                    [('user_id', '=', user.id)], limit=1)
                if employee:
                    emp_vals = {}

                    # Resolve department by name
                    dept_name = employee_fields.get('department_name')
                    if dept_name:
                        dept = request.env['hr.department'].sudo().search(
                            [('name', '=ilike', dept_name)], limit=1)
                        if dept:
                            emp_vals['department_id'] = dept.id

                    # Resolve job position by name
                    job_name = employee_fields.get('job_name')
                    if job_name:
                        job = request.env['hr.job'].sudo().search(
                            [('name', '=ilike', job_name)], limit=1)
                        if job:
                            emp_vals['job_id'] = job.id

                    # Resolve manager by name
                    parent_name = employee_fields.get('parent_name')
                    if parent_name:
                        manager = request.env['hr.employee'].sudo().search(
                            [('name', '=ilike', parent_name)], limit=1)
                        if manager:
                            emp_vals['parent_id'] = manager.id

                    # Direct fields
                    if employee_fields.get('job_title'):
                        emp_vals['job_title'] = employee_fields['job_title']
                    if employee_fields.get('work_phone'):
                        emp_vals['work_phone'] = employee_fields['work_phone']

                    if emp_vals:
                        employee.write(emp_vals)

                    employee_data = {
                        'id': employee.id,
                        'job_title': employee.job_title or False,
                        'job_id': {'id': employee.job_id.id, 'name': employee.job_id.name} if employee.job_id else False,
                        'department_id': {'id': employee.department_id.id, 'name': employee.department_id.name} if employee.department_id else False,
                        'parent_id': {'id': employee.parent_id.id, 'name': employee.parent_id.name} if employee.parent_id else False,
                        'work_phone': employee.work_phone or False,
                    }

            # Prepare response data
            response_data = {
                'id': user.id,
                'name': user.name,
                'login': user.login,
                'email': user.email,
                'groups': [{'id': g.id, 'name': g.name} for g in user.group_ids],
                'active': user.active,
                'create_date': user.create_date.isoformat() if user.create_date else None
            }

            if employee_data:
                response_data['employee'] = employee_data

            # Add credentials if auto-generated
            if auto_generate_credentials:
                credentials = {}
                if temp_password:
                    credentials['temporary_password'] = temp_password
                if api_key:
                    credentials['api_key'] = api_key

                if credentials:
                    response_data['credentials'] = credentials
                    response_data['credentials']['note'] = "Store these credentials securely - they won't be shown again"

            if auto_generate_credentials:
                return self._json_response_sensitive(
                    data=response_data,
                    message="User created successfully with credentials",
                    status_code=201
                )
            return self._json_response(
                data=response_data,
                message="User created successfully",
                status_code=201
            )

        except Exception as e:
            _logger.error("Error creating user: %s", str(e))
            return self._error_response("Error creating user", 500, "USER_CREATE_ERROR")

    def _create_employee(self, data):
        """Create an employee with job title, department, and optional user link.

        Accepted convenience fields (resolved by name when given as strings):
        - department_name  → looked up to department_id
        - job_title        → stored directly (free-text on hr.employee)
        - job_name         → looked up in hr.job to set job_id
        - parent_name      → looked up in hr.employee to set parent_id (manager)
        - user_login       → looked up in res.users to set user_id
        """
        try:
            Emp = request.env['hr.employee']

            # Resolve department by name
            dept_name = data.pop('department_name', None)
            if dept_name and 'department_id' not in data:
                dept = request.env['hr.department'].search([('name', '=ilike', dept_name)], limit=1)
                if dept:
                    data['department_id'] = dept.id
                else:
                    return self._error_response(
                        f"Department '{dept_name}' not found", 400, "DEPARTMENT_NOT_FOUND")

            # Resolve job position by name
            job_name = data.pop('job_name', None)
            if job_name and 'job_id' not in data:
                job = request.env['hr.job'].search([('name', '=ilike', job_name)], limit=1)
                if job:
                    data['job_id'] = job.id
                else:
                    return self._error_response(
                        f"Job position '{job_name}' not found", 400, "JOB_NOT_FOUND")

            # Resolve manager by name
            parent_name = data.pop('parent_name', None)
            if parent_name and 'parent_id' not in data:
                manager = Emp.search([('name', '=ilike', parent_name)], limit=1)
                if manager:
                    data['parent_id'] = manager.id

            # Resolve linked user by login
            user_login = data.pop('user_login', None)
            if user_login and 'user_id' not in data:
                user = request.env['res.users'].search([('login', '=', user_login)], limit=1)
                if user:
                    data['user_id'] = user.id

            employee = Emp.create(data)

            response_data = {
                'id': employee.id,
                'name': employee.name,
                'job_title': employee.job_title or False,
                'job_id': {'id': employee.job_id.id, 'name': employee.job_id.name} if employee.job_id else False,
                'department_id': {'id': employee.department_id.id, 'name': employee.department_id.name} if employee.department_id else False,
                'parent_id': {'id': employee.parent_id.id, 'name': employee.parent_id.name} if employee.parent_id else False,
                'work_email': employee.work_email or False,
                'create_date': employee.create_date.isoformat() if employee.create_date else None,
            }

            return self._json_response(
                data=response_data,
                message="Employee created successfully",
                status_code=201
            )

        except Exception as e:
            _logger.error("Error creating employee: %s", str(e))
            return self._error_response("Error creating employee", 500, "EMPLOYEE_CREATE_ERROR")

    @http.route('/api/v2/users/<int:user_id>/password', type='http', auth='none', methods=['PUT'], csrf=False, readonly=False)
    def change_user_password(self, user_id):
        """Change user password (admin or own password)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            new_password = data.get('new_password')
            old_password = data.get('old_password')  # Required for own password change
            
            if not new_password:
                return self._error_response("new_password is required", 400, "MISSING_PASSWORD")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Check permissions
            is_own_password = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_erp_manager')
            
            if not is_own_password and not is_admin:
                return self._error_response("Access denied: Can only change own password or need admin rights", 403, "ACCESS_DENIED")

            # For own password change, verify old password
            if is_own_password and not is_admin:
                if not old_password:
                    return self._error_response("old_password is required when changing own password", 400, "MISSING_OLD_PASSWORD")
                
                # Verify old password
                try:
                    credential = {
                        'login': current_user.login,
                        'password': old_password,
                        'type': 'password'
                    }
                    request.session.authenticate(request.env, credential)
                except Exception:
                    return self._error_response("Invalid old password", 401, "INVALID_OLD_PASSWORD")

            # Change password
            target_user.sudo().password = new_password
            
            return self._json_response(
                data={
                    'user_id': user_id,
                    'message': 'Password changed successfully'
                },
                message="Password updated successfully"
            )

        except Exception as e:
            _logger.error("Error changing password for user %s: %s", user_id, str(e))
            return self._error_response("Error changing password", 500, "PASSWORD_CHANGE_ERROR")

    @http.route('/api/v2/users/<int:user_id>', type='http', auth='none', methods=['PUT'], csrf=False, readonly=False)
    def update_user(self, user_id):
        """Update user information."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            # Check permissions before escalating
            is_own_profile = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_erp_manager')

            if not is_own_profile and not is_admin:
                return self._error_response("Access denied: Can only update own profile or need admin rights", 403, "ACCESS_DENIED")

            # Verify user exists (sudo needed for write, applied after permission check)
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Fields that users can update about themselves
            user_editable_fields = ['name', 'email', 'phone', 'signature', 'lang', 'tz']
            
            # Fields that only admins can update
            admin_only_fields = ['login', 'active', 'company_id', 'company_ids']

            # Filter data based on permissions
            update_data = {}
            
            for field, value in data.items():
                if field == 'password':
                    continue  # Use password change endpoint instead
                elif field in user_editable_fields:
                    update_data[field] = value
                elif field in admin_only_fields:
                    if is_admin:
                        update_data[field] = value
                    else:
                        return self._error_response(f"Access denied: Field '{field}' requires admin rights", 403, "ADMIN_FIELD_ACCESS_DENIED")
                elif field in ['group_names', 'group_ids']:
                    # Handle group updates for admins
                    if is_admin:
                        if field == 'group_names':
                            groups = request.env['res.groups'].sudo().search([('name', 'in', value)])
                            if groups:
                                update_data['group_ids'] = [(6, 0, groups.ids)]
                        elif field == 'group_ids':
                            update_data['group_ids'] = [(6, 0, value)]
                    else:
                        return self._error_response(f"Access denied: Field '{field}' requires admin rights", 403, "ADMIN_FIELD_ACCESS_DENIED")

            if not update_data:
                return self._error_response("No valid fields to update", 400, "NO_VALID_FIELDS")

            # Update user
            target_user.sudo().write(update_data)
            
            # Get updated user data
            updated_user = target_user.read(['id', 'name', 'login', 'email', 'phone', 'active', 'lang', 'tz'])[0]
            if is_admin:
                updated_user['groups'] = [{'id': g.id, 'name': g.name} for g in target_user.group_ids]

            return self._json_response(
                data={
                    'user': updated_user,
                    'updated_fields': list(update_data.keys())
                },
                message="User updated successfully"
            )

        except Exception as e:
            _logger.error("Error updating user %s: %s", user_id, str(e))
            return self._error_response("Error updating user", 500, "USER_UPDATE_ERROR")

    @http.route('/api/v2/users/<int:user_id>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_user(self, user_id):
        """Get user information."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Check permissions first
            is_own_profile = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_erp_manager')
            can_view_users = current_user.has_group('base.group_user')

            if not is_own_profile and not is_admin and not can_view_users:
                return self._error_response("Access denied", 403, "ACCESS_DENIED")

            # Use sudo() only for admins; others go through record rules
            if is_admin:
                target_user = request.env['res.users'].sudo().browse(user_id)
            else:
                target_user = request.env['res.users'].browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Basic fields everyone can see
            user_data = {
                'id': target_user.id,
                'name': target_user.name,
                'email': target_user.email,
                'active': target_user.active,
                'create_date': target_user.create_date.isoformat() if target_user.create_date else None,
            }

            # Additional fields for own profile or admins
            if is_own_profile or is_admin:
                user_data.update({
                    'login': target_user.login,
                    'phone': target_user.phone,
                    'lang': target_user.lang,
                    'tz': target_user.tz,
                    'signature': target_user.signature,
                    'company_id': [target_user.company_id.id, target_user.company_id.name] if target_user.company_id else None,
                })

            # Admin-only fields (sudo already applied above for admins)
            if is_admin:
                user_data.update({
                    'groups': [{'id': g.id, 'name': g.name, 'full_name': g.full_name} for g in target_user.group_ids],
                    'company_ids': [{'id': c.id, 'name': c.name} for c in target_user.company_ids],
                    'login_date': target_user.login_date.isoformat() if target_user.login_date else None,
                })

            return self._json_response(
                data={'user': user_data},
                message="User information retrieved"
            )

        except Exception as e:
            _logger.error("Error getting user %s: %s", user_id, str(e))
            return self._error_response("Error retrieving user", 500, "USER_GET_ERROR")

    @http.route('/api/v2/users/<int:user_id>/reset-password', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def reset_user_password(self, user_id):
        """Reset user password (admin only) - generates a temporary password."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Check admin permissions
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_erp_manager')
            if not is_admin:
                return self._error_response("Access denied: Admin rights required", 403, "ACCESS_DENIED")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Generate temporary password
            temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
            
            # Reset password
            target_user.sudo().password = temp_password
            
            return self._json_response_sensitive(
                data={
                    'user_id': user_id,
                    'temporary_password': temp_password,
                    'message': 'Password has been reset. User should change it on first login.'
                },
                message="Password reset successfully"
            )

        except Exception as e:
            _logger.error("Error resetting password for user %s: %s", user_id, str(e))
            return self._error_response("Error resetting password", 500, "PASSWORD_RESET_ERROR")

    @http.route('/api/v2/users', type='http', auth='none', methods=['GET'], csrf=False)
    def list_users(self):
        """List all users (with pagination)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Role-based access control
            is_admin = current_user.has_group('base.group_system')
            is_manager = current_user.has_group('base.group_erp_manager')

            if not is_admin and not is_manager:
                return self._error_response("Access denied", 403, "ACCESS_DENIED")

            # Get parameters
            limit, offset = self._parse_pagination()
            if limit is None:
                return self._error_response("limit and offset must be integers", 400, "INVALID_PARAMS")
            search = request.httprequest.args.get('search', '')
            active_only = request.httprequest.args.get('active_only', 'true').lower() == 'true'

            # Build domain
            domain = []
            if active_only:
                domain.append(('active', '=', True))
            if search:
                domain.extend(['|', '|',
                    ('name', 'ilike', search),
                    ('login', 'ilike', search),
                    ('email', 'ilike', search)
                ])

            # Scoping: managers see only users they manage (same department or created by them)
            # Admins see all users
            if is_manager and not is_admin:
                employee = current_user.employee_id if 'employee_id' in current_user._fields else False
                dept_id = employee.department_id.id if employee and employee.department_id else False
                scope = ['|', ('create_uid', '=', current_user.id)]
                if dept_id:
                    scope.append(('employee_ids.department_id', '=', dept_id))
                else:
                    scope.append(('id', '=', current_user.id))
                domain = scope + domain

            # Admins use sudo() for full visibility; managers rely on scoped domain + record rules
            if is_admin:
                Users = request.env['res.users'].sudo()
            else:
                Users = request.env['res.users']
            users = Users.search(domain, limit=limit, offset=offset, order='name')
            total_count = Users.search_count(domain)

            # Prepare user data
            users_data = []
            for user in users:
                user_data = {
                    'id': user.id,
                    'name': user.name,
                    'login': user.login,
                    'email': user.email,
                    'active': user.active,
                    'create_date': user.create_date.isoformat() if user.create_date else None,
                }
                
                # Add more fields for admins
                if is_admin:
                    user_data.update({
                        'groups': [g.name for g in user.group_ids],
                        'company_id': user.company_id.name if user.company_id else None,
                        'login_date': user.login_date.isoformat() if user.login_date else None,
                    })
                
                users_data.append(user_data)

            return self._json_response(
                data={
                    'users': users_data,
                    'count': len(users_data),
                    'total_count': total_count,
                    'limit': limit,
                    'offset': offset
                },
                message=f"Found {len(users_data)} users"
            )

        except Exception as e:
            _logger.error("Error listing users: %s", str(e))
            return self._error_response("Error retrieving users", 500, "USERS_LIST_ERROR")

    @http.route('/api/v2/users/<int:user_id>/api-key', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def generate_user_api_key(self, user_id):
        """Generate API key for a user (admin only or own API key)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # Check permissions
            is_own_user = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_erp_manager')
            
            if not is_own_user and not is_admin:
                return self._error_response("Access denied: Can only generate own API key or need admin rights", 403, "ACCESS_DENIED")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Generate API key using Odoo's ORM
            try:
                api_key = request.env(user=user_id)['res.users.apikeys'].sudo()._generate(
                    scope=None,
                    name='Generated API Key',
                    expiration_date=None,
                )
                
                return self._json_response_sensitive(
                    data={
                        'user_id': user_id,
                        'user_name': target_user.name,
                        'api_key': api_key,
                        'note': 'Store this API key securely - it will not be shown again'
                    },
                    message="API key generated successfully"
                )
                
            except Exception as e:
                _logger.error("Error generating API key for user %s: %s", user_id, str(e))
                return self._error_response("Could not generate API key", 500, "API_KEY_GENERATION_ERROR")

        except Exception as e:
            _logger.error("Error in API key generation for user %s: %s", user_id, str(e))
            return self._error_response("Error generating API key", 500, "API_KEY_ERROR")

    # ===== GENERIC CRUD: UPDATE =====

    @http.route('/api/v2/update/<string:model>/<int:record_id>', type='http', auth='none', methods=['PUT'], csrf=False, readonly=False)
    def update_record(self, model, record_id):
        """Update a record in any accessible model."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")

            if self._is_model_blocked(model):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Module access enforcement
            module_error = self._enforce_module_access(model)
            if module_error:
                return module_error

            if not self._check_model_access(model, 'write'):
                return self._error_response(f"Write access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Verify record exists AND is within the user's allowed scope
            scope_domain = self._get_record_scope_domain(model, user)
            record = request.env[model].search([('id', '=', record_id)] + scope_domain, limit=1)
            if not record:
                return self._error_response(f"Record {record_id} not found in {model}", 404, "RECORD_NOT_FOUND")

            record.write(data)

            basic_fields = ['id', 'name', 'display_name', 'write_date']
            safe_fields = [f for f in basic_fields if f in request.env[model]._fields]
            if not safe_fields:
                safe_fields = ['id']
            record_data = record.read(safe_fields)[0]

            return self._json_response(
                data={'record': record_data, 'updated_fields': list(data.keys())},
                message=f"Record {record_id} updated in {model}"
            )

        except AccessError:
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "UPDATE_ERROR")
        except Exception as e:
            _logger.error("Error updating %s/%s: %s", model, record_id, str(e))
            return self._error_response("Error updating record", 500, "UPDATE_ERROR")

    # ===== SALE → INVOICE =====

    @http.route('/api/v2/sales/<int:order_id>/create-invoice', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def create_invoice_from_sale(self, order_id):
        """Create an invoice from a confirmed sale order.

        Uses Odoo's ``_create_invoices()`` so taxes, fiscal positions,
        accounts and quantities are computed correctly.

        Optional JSON body::

            {
                "advance_payment_method": "delivered"  // or "percentage", "fixed"
                "amount": 50.0,          // required when method is percentage/fixed
                "deposit_account_id": 5  // optional override for down-payment account
            }

        ``advance_payment_method`` values:
        - ``delivered`` (default) – invoice only delivered qty
        - ``percentage`` – down-payment invoice for *amount* %
        - ``fixed`` – down-payment invoice for a fixed *amount*
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # --- access checks ---
            if 'sale.order' not in request.env:
                return self._error_response("Sales module not installed", 404, "MODULE_NOT_FOUND")
            if not self._check_model_access('sale.order', 'read'):
                return self._error_response("Access denied for sale.order", 403, "ACCESS_DENIED")
            if not self._check_model_access('account.move', 'create'):
                return self._error_response("Access denied for account.move", 403, "ACCESS_DENIED")

            order = request.env['sale.order'].browse(order_id)
            if not order.exists():
                return self._error_response(f"Sale order {order_id} not found", 404, "NOT_FOUND")

            if order.state != 'sale':
                return self._error_response(
                    f"Order is in state '{order.state}'. Only confirmed orders (state='sale') can be invoiced.",
                    400, "INVALID_STATE",
                )

            # --- optional params ---
            body = {}
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    body = request.httprequest.get_json(force=True) or {}
                except Exception:
                    body = {}

            method = body.get('advance_payment_method', 'delivered')
            if method not in ('delivered', 'percentage', 'fixed'):
                return self._error_response(
                    "advance_payment_method must be 'delivered', 'percentage', or 'fixed'",
                    400, "INVALID_PARAM",
                )

            if method in ('percentage', 'fixed') and not body.get('amount'):
                return self._error_response(
                    f"'amount' is required when advance_payment_method is '{method}'",
                    400, "MISSING_PARAM",
                )

            # --- create invoice ---
            if method == 'delivered':
                invoices = order._create_invoices()
            else:
                wizard_vals = {
                    'advance_payment_method': method,
                    'amount': body.get('amount', 0),
                }
                if body.get('deposit_account_id'):
                    wizard_vals['deposit_account_id'] = body['deposit_account_id']

                wiz_env = request.env['sale.advance.payment.inv'].with_context(
                    active_model='sale.order',
                    active_ids=[order.id],
                    active_id=order.id,
                )
                wiz = wiz_env.create(wizard_vals)
                wiz.create_invoices()
                invoices = order.invoice_ids.sorted('id', reverse=True)[:1]

            if not invoices:
                return self._error_response(
                    "No invoice was created. All lines may already be invoiced.",
                    400, "NOTHING_TO_INVOICE",
                )

            inv = invoices[0] if len(invoices) > 1 else invoices

            # Apply optional date overrides
            date_vals = {}
            if body.get('invoice_date'):
                date_vals['invoice_date'] = body['invoice_date']
            if body.get('invoice_date_due'):
                date_vals['invoice_date_due'] = body['invoice_date_due']
            if date_vals:
                inv.write(date_vals)

            inv_data = inv.read([
                'id', 'name', 'state', 'move_type',
                'partner_id', 'invoice_date', 'invoice_date_due',
                'amount_untaxed', 'amount_tax', 'amount_total',
                'amount_residual', 'payment_state', 'currency_id',
                'invoice_origin', 'invoice_line_ids',
            ])[0]

            return self._json_response(
                data={'invoice': inv_data},
                message="Invoice created from sale order",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(str(e), 400, "NOTHING_TO_INVOICE")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "INVOICE_CREATE_ERROR")
        except Exception as e:
            _logger.error("Error creating invoice from SO %s: %s", order_id, str(e))
            return self._error_response("Error creating invoice", 500, "INVOICE_CREATE_ERROR")

    # ===== IN-STORE PURCHASE (one-shot) =====

    @http.route('/api/v2/sales/in-store-purchase', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def in_store_purchase(self):
        """One-shot in-store purchase: create SO, confirm, deliver, invoice,
        post invoice, and register payment – all in a single API call.

        Because the sale happens at the counter, delivery is instant and
        payment is collected immediately.

        JSON body::

            {
                "partner_id": 7,                     // optional – defaults to "Walk-In Store Customer"
                "order_lines": [                     // required, at least one
                    {
                        "product_id": 12,
                        "quantity": 2,
                        "price_unit": 50.0           // optional, defaults to product list price
                    }
                ],
                "warehouse_id": 1,                   // optional – warehouse (must be ship_only)
                "journal_id": 4,                     // optional – payment journal (bank/cash)
                "payment_date": "2026-04-04",        // optional, defaults to today
                "invoice_date": "2026-04-04"         // optional, defaults to today
            }

        Returns the sale order, invoice, payment, and stock-picking data.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            # --- parse body ---
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response(
                    "Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                body = request.httprequest.get_json(force=True)
                if not body:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            # --- validate required fields ---
            order_lines = body.get('order_lines')
            if not order_lines or not isinstance(order_lines, list):
                return self._error_response(
                    "'order_lines' is required and must be a non-empty list", 400, "MISSING_PARAM")

            # --- resolve customer partner ---
            # If partner_id is provided and is a real customer, use it.
            # Otherwise fall back to a default "Walk-In Store Customer".
            partner_id = body.get('partner_id')
            company_partner_id = request.env.company.partner_id.id

            if partner_id and partner_id != company_partner_id:
                partner = request.env['res.partner'].browse(partner_id)
                if not partner.exists():
                    return self._error_response(
                        f"Partner {partner_id} not found", 404, "NOT_FOUND")
            else:
                # Use or create a default walk-in customer
                partner = request.env['res.partner'].sudo().search([
                    ('name', '=', 'Walk-In Store Customer'),
                    ('company_id', 'in', [request.env.company.id, False]),
                ], limit=1)
                if not partner:
                    partner = request.env['res.partner'].sudo().create({
                        'name': 'Walk-In Store Customer',
                        'company_id': request.env.company.id,
                        'customer_rank': 1,
                    })
                partner_id = partner.id

            # --- validate products ---
            sol_vals = []
            for idx, line in enumerate(order_lines):
                pid = line.get('product_id')
                if not pid:
                    return self._error_response(
                        f"order_lines[{idx}]: 'product_id' is required",
                        400, "MISSING_PARAM")

                product = request.env['product.product'].browse(pid)
                if not product.exists():
                    return self._error_response(
                        f"Product {pid} not found", 404, "NOT_FOUND")

                qty = line.get('quantity', 1)
                if qty <= 0:
                    return self._error_response(
                        f"order_lines[{idx}]: 'quantity' must be > 0",
                        400, "INVALID_PARAM")

                sol_vals.append((0, 0, {
                    'product_id': pid,
                    'product_uom_qty': qty,
                    'price_unit': line.get('price_unit', product.list_price),
                }))

            # =============================================================
            # Step 1 – Resolve a one-step warehouse for instant delivery
            # =============================================================
            Warehouse = request.env['stock.warehouse']
            company_id = request.env.company.id

            if body.get('warehouse_id'):
                warehouse = Warehouse.browse(body['warehouse_id'])
                if not warehouse.exists():
                    return self._error_response(
                        f"Warehouse {body['warehouse_id']} not found", 404, "NOT_FOUND")
                if warehouse.delivery_steps != 'ship_only':
                    return self._error_response(
                        f"Warehouse '{warehouse.name}' uses multi-step delivery "
                        f"({warehouse.delivery_steps}). In-store purchases require "
                        "a warehouse with one-step delivery (ship_only).",
                        400, "INVALID_WAREHOUSE")
            else:
                # Prefer the first ship_only warehouse for this company
                warehouse = Warehouse.search([
                    ('company_id', '=', company_id),
                    ('delivery_steps', '=', 'ship_only'),
                ], limit=1)
                if not warehouse:
                    # Fall back: take the default warehouse and temporarily
                    # won't work if none exists at all.
                    warehouse = Warehouse.search([
                        ('company_id', '=', company_id),
                    ], limit=1)
                    if not warehouse:
                        return self._error_response(
                            "No warehouse found for the current company.",
                            400, "NO_WAREHOUSE")
                    if warehouse.delivery_steps != 'ship_only':
                        # Switch it to one-step for a clean in-store flow
                        warehouse.sudo().write({'delivery_steps': 'ship_only'})

            # =============================================================
            # Step 2 – Create & confirm the sale order
            # =============================================================
            order = request.env['sale.order'].create({
                'partner_id': partner_id,
                'warehouse_id': warehouse.id,
                'order_line': sol_vals,
            })

            # Clear any routes on order lines so procurement does not
            # try to use multi-step routes configured on the line.
            order.order_line.write({'route_ids': [(5, 0, 0)]})

            # Procurement also reads routes directly from the product
            # and its category (stock_rule.py _get_rule line ~608).
            # Temporarily strip product & category routes so the
            # warehouse's simple ship_only route is used instead.
            products = order.order_line.mapped('product_id')
            saved_product_routes = {p.id: p.route_ids for p in products}
            saved_categ_routes = {}
            categs = products.mapped('categ_id')
            for categ in categs:
                saved_categ_routes[categ.id] = categ.route_ids
                categ.sudo().write({'route_ids': [(5, 0, 0)]})
            products.sudo().write({'route_ids': [(5, 0, 0)]})

            try:
                order.action_confirm()
            finally:
                # Restore original routes on products and categories
                for p in products:
                    if saved_product_routes.get(p.id):
                        p.sudo().write({'route_ids': [(6, 0, saved_product_routes[p.id].ids)]})
                for categ in categs:
                    if saved_categ_routes.get(categ.id):
                        categ.sudo().write({'route_ids': [(6, 0, saved_categ_routes[categ.id].ids)]})

            # =============================================================
            # Step 3 – Instant delivery: validate all pickings
            # =============================================================
            pickings_data = []
            for picking in order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
                for move in picking.move_ids:
                    move.write({'quantity': move.product_uom_qty, 'picked': True})
                picking.button_validate()
                pickings_data.append({
                    'id': picking.id,
                    'name': picking.name,
                    'state': picking.state,
                })

            # =============================================================
            # Step 4 – Create & post the invoice
            # =============================================================
            invoices = order._create_invoices()
            if not invoices:
                return self._error_response(
                    "No invoice could be created. Check product invoice policies.",
                    400, "NOTHING_TO_INVOICE")

            invoice = invoices[0] if len(invoices) > 1 else invoices

            date_vals = {}
            if body.get('invoice_date'):
                date_vals['invoice_date'] = body['invoice_date']
            if date_vals:
                invoice.write(date_vals)

            invoice.action_post()

            # =============================================================
            # Step 5 – Register payment (instant, full amount)
            # =============================================================
            ctx = {
                'active_model': 'account.move',
                'active_ids': invoice.ids,
            }
            wizard_vals = {}
            if body.get('journal_id'):
                wizard_vals['journal_id'] = body['journal_id']
            if body.get('payment_date'):
                wizard_vals['payment_date'] = body['payment_date']

            pay_wizard = request.env['account.payment.register'] \
                .with_context(**ctx).create(wizard_vals)
            pay_wizard.action_create_payments()

            invoice.invalidate_recordset()
            order.invalidate_recordset()

            # --- read payment record ---
            payment = request.env['account.payment'].search([
                ('reconciled_invoice_ids', 'in', invoice.ids),
            ], limit=1, order='id desc')

            # =============================================================
            # Build response
            # =============================================================
            inv_data = invoice.read([
                'id', 'name', 'state', 'move_type',
                'partner_id', 'invoice_date', 'invoice_date_due',
                'amount_untaxed', 'amount_tax', 'amount_total',
                'amount_residual', 'payment_state', 'currency_id',
                'invoice_origin',
            ])[0]

            order_data = order.read([
                'id', 'name', 'state', 'partner_id',
                'amount_untaxed', 'amount_tax', 'amount_total',
                'invoice_status',
            ])[0]

            payment_data = {}
            if payment:
                payment_data = payment.read([
                    'id', 'name', 'state', 'amount',
                    'payment_type', 'journal_id', 'date',
                ])[0]

            stock_moves = []
            for move in order.picking_ids.move_ids:
                stock_moves.append({
                    'id': move.id,
                    'product_id': move.product_id.id,
                    'product_name': move.product_id.display_name,
                    'quantity': move.quantity,
                    'state': move.state,
                    'reference': move.reference,
                })

            return self._json_response(
                data={
                    'sale_order': order_data,
                    'invoice': inv_data,
                    'payment': payment_data,
                    'pickings': pickings_data,
                    'stock_moves': stock_moves,
                },
                message="In-store purchase completed: SO confirmed, delivered, invoiced, and paid",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(str(e), 400, "PURCHASE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "PURCHASE_ERROR")
        except Exception as e:
            _logger.error("In-store purchase error: %s", str(e))
            return self._error_response(
                "Error processing in-store purchase", 500, "PURCHASE_ERROR")

    # ===== INVENTORY ADJUSTMENT =====

    @http.route('/api/v2/inventory/adjust', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def inventory_adjust(self):
        """Adjust inventory for a product, creating proper stock moves."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            product_id = data.get('product_id')
            product_template_id = data.get('product_template_id')
            new_quantity = data.get('new_quantity')
            location_id = data.get('location_id')

            if not product_id and not product_template_id:
                return self._error_response(
                    "product_id or product_template_id is required", 400, "MISSING_FIELDS"
                )
            if new_quantity is None:
                return self._error_response(
                    "new_quantity is required", 400, "MISSING_FIELDS"
                )

            StockQuant = request.env['stock.quant'].sudo()
            Product = request.env['product.product'].sudo()

            if product_template_id and not product_id:
                template = request.env['product.template'].sudo().browse(int(product_template_id))
                if not template.exists():
                    return self._error_response("Product template not found", 404, "PRODUCT_NOT_FOUND")
                variant = template.product_variant_id
                if not variant:
                    return self._error_response("Product template has no variant", 404, "NO_VARIANT")
                product_id = variant.id

            product = Product.browse(int(product_id))
            if not product.exists():
                return self._error_response("Product not found", 404, "PRODUCT_NOT_FOUND")

            if not location_id:
                warehouse = request.env['stock.warehouse'].sudo().search([], limit=1)
                if warehouse:
                    location_id = warehouse.lot_stock_id.id
                else:
                    return self._error_response(
                        "No warehouse found", 404, "NO_WAREHOUSE"
                    )

            location = request.env['stock.location'].sudo().browse(int(location_id))
            if not location.exists():
                return self._error_response("Location not found", 404, "LOCATION_NOT_FOUND")

            quant = StockQuant.search([
                ('product_id', '=', int(product_id)),
                ('location_id', '=', int(location_id)),
            ], limit=1)

            if not quant:
                quant = StockQuant.create({
                    'product_id': int(product_id),
                    'location_id': int(location_id),
                    'quantity': 0,
                })

            old_quantity = quant.quantity
            diff = float(new_quantity) - old_quantity

            if diff == 0:
                return self._json_response(
                    data={
                        'quant_id': quant.id,
                        'old_quantity': old_quantity,
                        'new_quantity': float(new_quantity),
                        'diff': 0,
                        'move_id': None,
                    },
                    message="No adjustment needed"
                )

            quant.with_context(inventory_mode=True).write({
                'inventory_quantity': float(new_quantity),
            })

            try:
                quant.action_apply_inventory()
            except Exception as apply_err:
                _logger.warning(
                    "action_apply_inventory failed, falling back to direct write: %s",
                    str(apply_err)
                )
                quant.with_context(inventory_mode=True).write({
                    'quantity': float(new_quantity),
                })

            quant.invalidate_recordset()
            product.invalidate_recordset()

            moves = request.env['stock.move'].sudo().search([
                ('product_id', '=', int(product_id)),
                ('is_inventory', '=', True),
                ('state', '=', 'done'),
            ], order='id desc', limit=1)

            move_data = None
            if moves:
                m = moves[0]
                move_data = {
                    'id': m.id,
                    'reference': m.reference or m.origin or '',
                    'quantity': m.quantity,
                    'state': m.state,
                    'date': str(m.date),
                    'location_id': [m.location_id.id, m.location_id.display_name],
                    'location_dest_id': [m.location_dest_id.id, m.location_dest_id.display_name],
                }

            return self._json_response(
                data={
                    'quant_id': quant.id,
                    'old_quantity': old_quantity,
                    'new_quantity': quant.quantity,
                    'diff': diff,
                    'move': move_data,
                },
                message="Inventory adjusted successfully"
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Error adjusting inventory: %s", str(e))
            return self._error_response(
                "Error adjusting inventory", 500, "INVENTORY_ADJUST_ERROR"
            )

    @http.route('/api/v2/inventory/decrement', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def inventory_decrement(self):
        """Decrement inventory for a product when a sale is confirmed.

        Creates a proper outgoing stock.picking with a stock.move from the
        warehouse stock location to the customer location, then validates it
        so that stock.quant quantities are decreased through Odoo's standard
        inventory pipeline.

        Body params:
            product_id (int)            – product.product id  (or use product_template_id)
            product_template_id (int)   – resolved to its first variant when product_id absent
            quantity (float)            – positive qty to subtract
            location_id (int, optional) – source stock.location; defaults to the main warehouse
            allow_negative (bool)       – if true, skip the insufficient-stock guard
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            product_id = data.get('product_id')
            product_template_id = data.get('product_template_id')
            quantity = data.get('quantity')
            location_id = data.get('location_id')
            allow_negative = bool(data.get('allow_negative', False))

            if not product_id and not product_template_id:
                return self._error_response(
                    "product_id or product_template_id is required", 400, "MISSING_FIELDS"
                )
            if quantity is None:
                return self._error_response(
                    "quantity is required", 400, "MISSING_FIELDS"
                )

            quantity = float(quantity)
            if quantity <= 0:
                return self._error_response(
                    "quantity must be positive", 400, "INVALID_QUANTITY"
                )

            Product = request.env['product.product'].sudo()

            if product_template_id and not product_id:
                template = request.env['product.template'].sudo().browse(int(product_template_id))
                if not template.exists():
                    return self._error_response("Product template not found", 404, "PRODUCT_NOT_FOUND")
                variant = template.product_variant_id
                if not variant:
                    return self._error_response("Product template has no variant", 404, "NO_VARIANT")
                product_id = variant.id

            product = Product.browse(int(product_id))
            if not product.exists():
                return self._error_response("Product not found", 404, "PRODUCT_NOT_FOUND")

            warehouse = request.env['stock.warehouse'].sudo().search([], limit=1)
            if not warehouse:
                return self._error_response("No warehouse found", 404, "NO_WAREHOUSE")

            if not location_id:
                location_id = warehouse.lot_stock_id.id

            source_location = request.env['stock.location'].sudo().browse(int(location_id))
            if not source_location.exists():
                return self._error_response("Source location not found", 404, "LOCATION_NOT_FOUND")

            customer_location = request.env.ref('stock.stock_location_customers').sudo()

            # ---- check current on-hand stock ----
            StockQuant = request.env['stock.quant'].sudo()
            quant = StockQuant.search([
                ('product_id', '=', int(product_id)),
                ('location_id', '=', int(location_id)),
            ], limit=1)

            old_quantity = quant.quantity if quant else 0.0

            if not allow_negative and old_quantity < quantity:
                return self._error_response(
                    f"Insufficient stock: available {old_quantity}, requested {quantity}",
                    400, "INSUFFICIENT_STOCK"
                )

            # ---- create an outgoing delivery picking ----
            picking_type = warehouse.out_type_id
            picking = request.env['stock.picking'].sudo().create({
                'picking_type_id': picking_type.id,
                'location_id': source_location.id,
                'location_dest_id': customer_location.id,
                'origin': 'API Sale Decrement',
                'scheduled_date': datetime.now(),
            })

            move = request.env['stock.move'].sudo().create({
                'product_id': int(product_id),
                'product_uom_qty': quantity,
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': source_location.id,
                'location_dest_id': customer_location.id,
                'origin': 'API Sale Decrement',
                'description_picking': f"Sale Decrement: {product.display_name}",
            })

            # ---- confirm → reserve → set done qty → validate ----
            picking.action_confirm()
            picking.action_assign()

            move.write({'quantity': quantity, 'picked': True})

            try:
                result = picking.with_context(
                    skip_backorder=True,
                    skip_sms=True,
                    skip_immediate=True,
                ).button_validate()
                if isinstance(result, dict) and result.get('res_model'):
                    wiz = (request.env[result['res_model']]
                           .sudo()
                           .with_context(**(result.get('context') or {}))
                           .create({}))
                    wiz.process()
            except Exception as validate_err:
                _logger.warning(
                    "button_validate raised %s – falling back to move._action_done",
                    validate_err,
                )
                move._action_done()

            # ---- refresh caches and read back final state ----
            for rec in (quant, product, move, picking):
                if rec:
                    rec.invalidate_recordset()

            quant = StockQuant.search([
                ('product_id', '=', int(product_id)),
                ('location_id', '=', int(location_id)),
            ], limit=1)
            new_quantity = quant.quantity if quant else 0.0

            move_data = {
                'id': move.id,
                'reference': move.reference or move.origin or '',
                'product_id': move.product_id.id,
                'quantity': move.quantity,
                'state': move.state,
                'date': str(move.date),
                'location_id': [move.location_id.id, move.location_id.display_name],
                'location_dest_id': [move.location_dest_id.id, move.location_dest_id.display_name],
            }

            picking_data = {
                'id': picking.id,
                'name': picking.name,
                'state': picking.state,
                'origin': picking.origin,
                'date_done': str(picking.date_done) if picking.date_done else None,
            }

            return self._json_response(
                data={
                    'quant_id': quant.id if quant else None,
                    'product_id': int(product_id),
                    'old_quantity': old_quantity,
                    'new_quantity': new_quantity,
                    'decremented_by': quantity,
                    'move': move_data,
                    'picking': picking_data,
                },
                message="Inventory decremented successfully"
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Error decrementing inventory: %s", str(e))
            return self._error_response(
                "Error decrementing inventory", 500, "INVENTORY_DECREMENT_ERROR"
            )

    # ===== PURCHASE ORDER: CONFIRM =====

    @http.route('/api/v2/purchase/<int:order_id>/confirm', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def purchase_confirm(self, order_id):
        """Confirm a draft/sent purchase order via Odoo's ``button_confirm()``.

        This triggers the full business logic including:
        - State transition (draft/sent → purchase)
        - Auto-creation of stock.picking for consumable products
        - Creation of stock.moves linked to PO lines
        - Move confirmation and reservation

        No JSON body required (POST with empty body is fine).

        Returns the confirmed PO with its picking_ids and order lines.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'purchase.order' not in request.env:
                return self._error_response(
                    "Purchase module not installed", 404, "MODULE_NOT_FOUND")

            if not self._check_model_access('purchase.order', 'write'):
                return self._error_response(
                    "Access denied for purchase.order", 403, "ACCESS_DENIED")

            order = request.env['purchase.order'].browse(order_id)
            if not order.exists():
                return self._error_response(
                    f"Purchase order {order_id} not found", 404, "NOT_FOUND")

            if order.state not in ('draft', 'sent'):
                return self._error_response(
                    f"Order is in state '{order.state}'. "
                    "Only draft or sent orders can be confirmed.",
                    400, "INVALID_STATE")

            if not order.order_line:
                return self._error_response(
                    "Cannot confirm a purchase order with no lines",
                    400, "NO_ORDER_LINES")

            # Call the real Odoo confirm method (triggers _create_picking)
            order.button_confirm()
            order.invalidate_recordset()

            # Read back confirmed order
            order_data = order.read([
                'id', 'name', 'state', 'partner_id',
                'date_order', 'date_approve',
                'amount_untaxed', 'amount_tax', 'amount_total',
                'picking_ids', 'order_line', 'invoice_status',
            ])[0]

            # Enrich picking details
            pickings_data = []
            for picking in order.picking_ids:
                moves = []
                for move in picking.move_ids:
                    moves.append({
                        'id': move.id,
                        'product_id': move.product_id.id,
                        'product_name': move.product_id.display_name,
                        'product_uom_qty': move.product_uom_qty,
                        'quantity': move.quantity,
                        'state': move.state,
                    })
                pickings_data.append({
                    'id': picking.id,
                    'name': picking.name,
                    'state': picking.state,
                    'picking_type_id': [picking.picking_type_id.id,
                                        picking.picking_type_id.display_name],
                    'scheduled_date': str(picking.scheduled_date) if picking.scheduled_date else None,
                    'move_ids': moves,
                })

            # Enrich order lines
            lines_data = []
            for line in order.order_line:
                lines_data.append({
                    'id': line.id,
                    'product_id': line.product_id.id,
                    'product_name': line.product_id.display_name,
                    'product_type': line.product_id.type,
                    'product_qty': line.product_qty,
                    'qty_received': line.qty_received,
                    'price_unit': line.price_unit,
                    'price_subtotal': line.price_subtotal,
                })

            order_data['pickings'] = pickings_data
            order_data['lines'] = lines_data

            # Warn if no pickings were created
            message = "Purchase order confirmed"
            if not order.picking_ids:
                message += (
                    " — but no receipt was generated. This typically means "
                    "the PO contains only service-type products. Only "
                    "consumable (storable) products generate receipts."
                )

            return self._json_response(
                data={'purchase_order': order_data},
                message=message,
                status_code=200,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(str(e), 400, "CONFIRM_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "CONFIRM_ERROR")
        except Exception as e:
            _logger.error("Error confirming PO %s: %s", order_id, str(e))
            return self._error_response(
                "Error confirming purchase order", 500, "CONFIRM_ERROR")

    # ===== STOCK PICKING: VALIDATE (RECEIVE) =====

    @http.route('/api/v2/picking/<int:picking_id>/validate', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def picking_validate(self, picking_id):
        """Validate (receive) a stock picking via Odoo's ``button_validate()``.

        This triggers the full receiving logic including:
        - Setting done quantities on stock.moves
        - Updating stock.quant (actual inventory)
        - Processing any subsequent/chained moves

        Optional JSON body::

            {
                "move_lines": [          // optional – partial receipt
                    {
                        "move_id": 42,
                        "quantity": 5.0  // qty to receive (less than ordered = backorder)
                    }
                ],
                "create_backorder": true  // default true; false = no backorder for unprocessed qty
            }

        If ``move_lines`` is omitted, all moves are received in full
        (quantity = product_uom_qty).
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'stock.picking' not in request.env:
                return self._error_response(
                    "Stock module not installed", 404, "MODULE_NOT_FOUND")

            if not self._check_model_access('stock.picking', 'write'):
                return self._error_response(
                    "Access denied for stock.picking", 403, "ACCESS_DENIED")

            picking = request.env['stock.picking'].browse(picking_id)
            if not picking.exists():
                return self._error_response(
                    f"Picking {picking_id} not found", 404, "NOT_FOUND")

            if picking.state == 'done':
                return self._error_response(
                    f"Picking {picking.name} is already validated (done)",
                    400, "ALREADY_DONE")

            if picking.state == 'cancel':
                return self._error_response(
                    f"Picking {picking.name} is cancelled",
                    400, "CANCELLED")

            if picking.state == 'draft':
                picking.action_confirm()

            if picking.state == 'waiting':
                picking.action_assign()

            # --- parse optional body for partial receipt ---
            body = {}
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    body = request.httprequest.get_json(force=True) or {}
                except Exception:
                    body = {}

            move_lines = body.get('move_lines')
            create_backorder = body.get('create_backorder', True)

            if move_lines and isinstance(move_lines, list):
                # Partial receipt: set specific quantities
                move_map = {ml['move_id']: ml['quantity'] for ml in move_lines
                            if 'move_id' in ml and 'quantity' in ml}
                for move in picking.move_ids.filtered(
                        lambda m: m.state not in ('done', 'cancel')):
                    if move.id in move_map:
                        qty = move_map[move.id]
                        if qty < 0:
                            return self._error_response(
                                f"Quantity for move {move.id} cannot be negative",
                                400, "INVALID_QUANTITY")
                        move.write({'quantity': qty, 'picked': True})
                    else:
                        # Not mentioned → leave at zero (will trigger backorder)
                        move.write({'quantity': 0, 'picked': False})
            else:
                # Full receipt: receive everything
                for move in picking.move_ids.filtered(
                        lambda m: m.state not in ('done', 'cancel')):
                    move.write({
                        'quantity': move.product_uom_qty,
                        'picked': True,
                    })

            # --- validate ---
            ctx = {
                'skip_sms': True,
                'skip_immediate': True,
            }
            if not create_backorder:
                ctx['skip_backorder'] = True

            result = picking.with_context(**ctx).button_validate()

            # Handle wizard popups (backorder confirmation, immediate transfer)
            if isinstance(result, dict) and result.get('res_model'):
                wiz_model = result['res_model']
                wiz_ctx = result.get('context') or {}
                wiz = (request.env[wiz_model]
                       .sudo()
                       .with_context(**wiz_ctx)
                       .create({}))
                if hasattr(wiz, 'process'):
                    wiz.process()
                elif hasattr(wiz, 'action_back_order'):
                    if create_backorder:
                        wiz.action_back_order()
                    else:
                        wiz.process_cancel_backorder()

            picking.invalidate_recordset()

            # --- build response ---
            moves_data = []
            for move in picking.move_ids:
                moves_data.append({
                    'id': move.id,
                    'product_id': move.product_id.id,
                    'product_name': move.product_id.display_name,
                    'product_uom_qty': move.product_uom_qty,
                    'quantity': move.quantity,
                    'state': move.state,
                })

            picking_data = {
                'id': picking.id,
                'name': picking.name,
                'state': picking.state,
                'date_done': str(picking.date_done) if picking.date_done else None,
                'origin': picking.origin,
                'picking_type_id': [picking.picking_type_id.id,
                                    picking.picking_type_id.display_name],
                'move_ids': moves_data,
            }

            # Check for backorder
            backorder = None
            if picking.backorder_ids:
                bo = picking.backorder_ids.sorted('id', reverse=True)[:1]
                if bo:
                    backorder = {
                        'id': bo.id,
                        'name': bo.name,
                        'state': bo.state,
                    }

            response_data = {'picking': picking_data}
            if backorder:
                response_data['backorder'] = backorder

            return self._json_response(
                data=response_data,
                message=f"Picking {picking.name} validated successfully",
                status_code=200,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(str(e), 400, "VALIDATE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "VALIDATE_ERROR")
        except Exception as e:
            _logger.error("Error validating picking %s: %s", picking_id, str(e))
            return self._error_response(
                "Error validating picking", 500, "VALIDATE_ERROR")

    # ===== STOCK PICKING: RETURN =====

    @http.route('/api/v2/picking/<int:picking_id>/return', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def picking_return(self, picking_id):
        """Create a return for a validated (done) picking using Odoo's
        ``stock.return.picking`` wizard.

        This properly reverses inventory by creating a new return picking
        with reversed stock.moves.

        Optional JSON body::

            {
                "return_lines": [        // optional – partial return
                    {
                        "product_id": 12,
                        "quantity": 3.0
                    }
                ],
                "validate_return": false  // default false; true = auto-validate the return picking
            }

        If ``return_lines`` is omitted, all products are returned in full.

        Returns the newly created return picking.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'stock.picking' not in request.env:
                return self._error_response(
                    "Stock module not installed", 404, "MODULE_NOT_FOUND")

            if not self._check_model_access('stock.picking', 'write'):
                return self._error_response(
                    "Access denied for stock.picking", 403, "ACCESS_DENIED")

            picking = request.env['stock.picking'].browse(picking_id)
            if not picking.exists():
                return self._error_response(
                    f"Picking {picking_id} not found", 404, "NOT_FOUND")

            if picking.state != 'done':
                return self._error_response(
                    f"Picking {picking.name} is in state '{picking.state}'. "
                    "Only validated (done) pickings can be returned.",
                    400, "INVALID_STATE")

            # --- parse optional body ---
            body = {}
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    body = request.httprequest.get_json(force=True) or {}
                except Exception:
                    body = {}

            return_lines = body.get('return_lines')
            validate_return = body.get('validate_return', False)

            # --- create the return wizard ---
            ctx = {
                'active_id': picking.id,
                'active_ids': [picking.id],
                'active_model': 'stock.picking',
            }
            wizard = request.env['stock.return.picking'].with_context(**ctx).create({})

            # --- adjust quantities if partial return ---
            if return_lines and isinstance(return_lines, list):
                qty_by_product = {rl['product_id']: rl['quantity']
                                  for rl in return_lines
                                  if 'product_id' in rl and 'quantity' in rl}
                for wiz_line in wizard.product_return_moves:
                    if wiz_line.product_id.id in qty_by_product:
                        qty = qty_by_product[wiz_line.product_id.id]
                        if qty < 0:
                            return self._error_response(
                                f"Quantity for product {wiz_line.product_id.id} "
                                "cannot be negative",
                                400, "INVALID_QUANTITY")
                        wiz_line.write({'quantity': qty})
                    else:
                        wiz_line.write({'quantity': 0})

            # --- create the return picking ---
            result = wizard.action_create_returns()

            new_picking_id = result.get('res_id')
            if not new_picking_id:
                return self._error_response(
                    "Return creation failed — no picking was generated",
                    500, "RETURN_ERROR")

            new_picking = request.env['stock.picking'].browse(new_picking_id)

            # --- optionally auto-validate the return ---
            if validate_return and new_picking.state != 'done':
                for move in new_picking.move_ids.filtered(
                        lambda m: m.state not in ('done', 'cancel')):
                    move.write({
                        'quantity': move.product_uom_qty,
                        'picked': True,
                    })
                new_picking.with_context(
                    skip_sms=True,
                    skip_immediate=True,
                    skip_backorder=True,
                ).button_validate()
                new_picking.invalidate_recordset()

            # --- build response ---
            moves_data = []
            for move in new_picking.move_ids:
                moves_data.append({
                    'id': move.id,
                    'product_id': move.product_id.id,
                    'product_name': move.product_id.display_name,
                    'product_uom_qty': move.product_uom_qty,
                    'quantity': move.quantity,
                    'state': move.state,
                })

            return_data = {
                'id': new_picking.id,
                'name': new_picking.name,
                'state': new_picking.state,
                'origin': new_picking.origin,
                'date_done': str(new_picking.date_done) if new_picking.date_done else None,
                'picking_type_id': [new_picking.picking_type_id.id,
                                    new_picking.picking_type_id.display_name],
                'move_ids': moves_data,
                'original_picking_id': picking.id,
                'original_picking_name': picking.name,
            }

            return self._json_response(
                data={'return_picking': return_data},
                message=f"Return picking {new_picking.name} created"
                        + (" and validated" if validate_return else "")
                        + f" for {picking.name}",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(str(e), 400, "RETURN_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "RETURN_ERROR")
        except Exception as e:
            _logger.error("Error returning picking %s: %s", picking_id, str(e))
            return self._error_response(
                "Error creating return", 500, "RETURN_ERROR")

    # ===== GENERIC CRUD: DELETE =====

    @http.route('/api/v2/delete/<string:model>/<int:record_id>', type='http', auth='none', methods=['DELETE'], csrf=False, readonly=False)
    def delete_record(self, model, record_id):
        """Delete a record from any accessible model."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")

            if self._is_model_blocked(model):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Module access enforcement
            module_error = self._enforce_module_access(model)
            if module_error:
                return module_error

            if not self._check_model_access(model, 'unlink'):
                return self._error_response(f"Delete access denied for model '{model}'", 403, "ACCESS_DENIED")

            # Verify record exists AND is within the user's allowed scope
            scope_domain = self._get_record_scope_domain(model, user)
            record = request.env[model].search([('id', '=', record_id)] + scope_domain, limit=1)
            if not record:
                return self._error_response(f"Record {record_id} not found in {model}", 404, "RECORD_NOT_FOUND")

            record.unlink()

            return self._json_response(
                data={'id': record_id, 'model': model},
                message=f"Record {record_id} deleted from {model}"
            )

        except AccessError:
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(str(e), 400, "DELETE_ERROR")
        except Exception as e:
            _logger.error("Error deleting %s/%s: %s", model, record_id, str(e))
            return self._error_response("Error deleting record", 500, "DELETE_ERROR")

    # ===== MODEL DISCOVERY =====

    @http.route('/api/v2/models', type='http', auth='none', methods=['GET'], csrf=False)
    def list_models(self):
        """List all models the authenticated user can access."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            search_term = request.httprequest.args.get('search', '')
            transient = request.httprequest.args.get('transient', 'false').lower() == 'true'

            domain = []
            if search_term:
                domain = ['|', ('model', 'ilike', search_term), ('name', 'ilike', search_term)]
            if not transient:
                domain.append(('transient', '=', False))

            ir_models = request.env['ir.model'].sudo().search(domain, order='model')

            models_data = []
            for m in ir_models:
                try:
                    if m.model in request.env and not self._is_model_blocked(m.model) and self._check_model_access(m.model, 'read'):
                        models_data.append({
                            'model': m.model,
                            'name': m.name,
                            'info': m.info or '',
                            'field_count': len(request.env[m.model]._fields),
                        })
                except Exception:
                    continue

            return self._json_response(
                data={'models': models_data, 'count': len(models_data)},
                message=f"Found {len(models_data)} accessible models"
            )

        except Exception as e:
            _logger.error("Error listing models: %s", str(e))
            return self._error_response("Error listing models", 500, "MODELS_ERROR")

    # ===== MODULE ACCESS CHECK =====

    @http.route('/api/v2/modules/access', type='http', auth='none', methods=['GET'], csrf=False)
    def check_module_access(self):
        """Return which functional modules the current user can access."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            module_access = self._get_module_access()

            return self._json_response(
                data={
                    'user_id': user.id,
                    'module_access': module_access,
                },
                message="Module access retrieved"
            )

        except Exception as e:
            _logger.error("Error checking module access: %s", str(e))
            return self._error_response("Error checking module access", 500, "MODULE_ACCESS_ERROR")

    # ===== ANALYTICS HELPERS =====

    def _parse_analytics_params(self):
        """Parse date range (from, to), timezone, and optional filter query params."""
        args = request.httprequest.args
        today = datetime.now().date()

        try:
            from_date = datetime.strptime(args.get('from', ''), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            from_date = today.replace(day=1)
        try:
            to_date = datetime.strptime(args.get('to', ''), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            to_date = today

        duration = max((to_date - from_date).days + 1, 1)
        prev_to = from_date - timedelta(days=1)
        prev_from = prev_to - timedelta(days=duration - 1)

        extra_domain = []
        for qp, field in [('company_id', 'company_id'), ('team_id', 'team_id'), ('owner_id', 'user_id')]:
            v = args.get(qp)
            if v:
                try:
                    extra_domain.append((field, '=', int(v)))
                except (ValueError, TypeError):
                    pass

        return {
            'from_date': from_date,
            'to_date': to_date,
            'prev_from': prev_from,
            'prev_to': prev_to,
            'extra_domain': extra_domain,
            'timezone': args.get('timezone', 'UTC'),
            'period_label': f"{from_date.isoformat()} to {to_date.isoformat()}",
        }

    def _kpi(self, current, previous):
        """Build a KPI dict with current, previous, delta, delta_percent."""
        c = round(current, 2) if isinstance(current, float) else current
        p = round(previous, 2) if isinstance(previous, float) else previous
        delta = round(c - p, 2) if isinstance(c, float) else c - p
        pct = round(delta / abs(p) * 100, 1) if p else (100.0 if c > 0 else 0.0)
        return {'current': c, 'previous': p, 'delta': delta, 'delta_percent': pct}

    def _ddom(self, field, fr, to):
        """Build an inclusive date-range domain for a field."""
        return [(field, '>=', fr.isoformat()), (field, '<', (to + timedelta(days=1)).isoformat())]

    def _agg(self, model_obj, domain, sum_field=None):
        """Return (count, sum_value) via a single read_group aggregate."""
        data = model_obj.read_group(domain, [sum_field] if sum_field else [], [])
        if not data:
            return 0, 0
        count = data[0].get('__count', 0)
        value = (data[0].get(sum_field, 0) or 0) if sum_field else 0
        return count, value

    def _analytics_meta(self, params):
        """Build the meta block included in every analytics response."""
        return {
            'generated_at': datetime.now().isoformat(),
            'period': {
                'from': params['from_date'].isoformat(),
                'to': params['to_date'].isoformat(),
                'previous_from': params['prev_from'].isoformat(),
                'previous_to': params['prev_to'].isoformat(),
            },
            'period_label': params['period_label'],
            'timezone': params['timezone'],
        }

    def _chart_series(self, rg_data, date_key, value_field=None):
        """Convert read_group results into {labels, series} for charting."""
        chart = {'labels': [], 'series': [{'label': 'Count', 'data': []}]}
        if value_field:
            chart['series'].append({'label': value_field, 'data': []})
        for row in rg_data:
            chart['labels'].append(row.get(date_key, ''))
            chart['series'][0]['data'].append(row.get('__count', 0))
            if value_field:
                chart['series'][1]['data'].append(row.get(value_field, 0) or 0)
        return chart

    def _breakdown(self, rg_data, group_field, value_field=None):
        """Convert read_group results into a list of breakdown buckets."""
        buckets = []
        for row in rg_data:
            g = row.get(group_field)
            buckets.append({
                'id': g[0] if isinstance(g, (list, tuple)) else g,
                'label': g[1] if isinstance(g, (list, tuple)) else str(g or 'Undefined'),
                'count': row.get('__count', 0),
                'value': (row.get(value_field, 0) or 0) if value_field else 0,
            })
        return buckets

    def _overdue_activities(self, res_model):
        """Count overdue mail.activity records for a model (returns 0 on error)."""
        try:
            if 'mail.activity' not in request.env:
                return 0
            return request.env['mail.activity'].search_count([
                ('res_model', '=', res_model),
                ('date_deadline', '<', datetime.now().date().isoformat()),
            ])
        except Exception:
            return 0

    # ===== AI CONTEXT =====

    @http.route('/api/v2/ai/context', type='http', auth='none', methods=['GET'], csrf=False)
    def ai_context(self):
        """Return a compact context blob for the AI agent in a single call.

        Combines: user info, permissions, module access, and a lightweight
        activity summary so the agent doesn't need 3-4 separate requests.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            module_access = self._get_module_access()
            accessible_modules = [
                key for key, info in module_access.items() if info.get('accessible')
            ]

            # Lightweight recent activity summary
            recent_summary = {}
            today = datetime.now().date()
            week_ago = today - timedelta(days=7)

            # Count recent records per accessible module (last 7 days)
            module_models = {
                'crm': ('crm.lead', 'create_date'),
                'sales': ('sale.order', 'date_order'),
                'accounting': ('account.move', 'invoice_date'),
                'hr': ('hr.employee', 'create_date'),
                'contacts': ('res.partner', 'create_date'),
            }

            for mod_key, (model_name, date_field) in module_models.items():
                if mod_key not in accessible_modules:
                    continue
                if model_name not in request.env:
                    continue
                try:
                    domain = [
                        (date_field, '>=', week_ago.isoformat()),
                        (date_field, '<=', today.isoformat()),
                    ]
                    scope = self._get_record_scope_domain(model_name, user)
                    if scope:
                        domain = scope + domain
                    count = request.env[model_name].search_count(domain)
                    recent_summary[mod_key] = {'new_this_week': count}
                except Exception:
                    continue

            # Overdue activities
            try:
                if 'mail.activity' in request.env:
                    overdue_count = request.env['mail.activity'].search_count([
                        ('date_deadline', '<', today.isoformat()),
                        ('user_id', '=', user.id),
                    ])
                    recent_summary['overdue_activities'] = overdue_count
            except Exception:
                pass

            return self._json_response(
                data={
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email,
                        'company': user.company_id.name if user.company_id else None,
                    },
                    'permissions': {
                        'is_admin': user.has_group('base.group_system'),
                        'is_user': user.has_group('base.group_user'),
                        'can_manage_users': user.has_group('base.group_erp_manager'),
                    },
                    'module_access': module_access,
                    'accessible_modules': accessible_modules,
                    'recent_summary': recent_summary,
                },
                message="AI context retrieved"
            )

        except Exception as e:
            _logger.error("Error building AI context: %s", str(e))
            return self._error_response("Error building AI context", 500, "AI_CONTEXT_ERROR")

    # ===== ANALYTICS ENDPOINTS =====

    @http.route('/api/v2/analytics/dashboard/summary', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_dashboard(self):
        """Cross-module dashboard summary with key KPIs from each accessible module."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            params = self._parse_analytics_params()
            module_kpis = {}

            specs = [
                ('crm', 'crm.lead', 'create_date', 'expected_revenue',
                 [('total_leads', None), ('expected_revenue', 'expected_revenue')]),
                ('sales', 'sale.order', 'date_order', 'amount_total',
                 [('total_orders', None), ('total_revenue', 'amount_total')]),
                ('invoicing', 'account.move', 'invoice_date', 'amount_total',
                 [('total_invoices', None), ('total_amount', 'amount_total')]),
                ('inventory', 'stock.picking', 'scheduled_date', None,
                 [('total_transfers', None)]),
                ('purchase', 'purchase.order', 'date_order', 'amount_total',
                 [('total_orders', None), ('total_amount', 'amount_total')]),
                ('hr', 'hr.employee', 'create_date', None,
                 [('new_hires', None)]),
                ('project', 'project.task', 'create_date', None,
                 [('total_tasks', None)]),
            ]

            for mod_key, model_name, date_field, _, kpi_defs in specs:
                if model_name not in request.env:
                    continue
                if not self._check_model_access(model_name):
                    continue
                if not self._user_has_module_role(user, mod_key):
                    continue

                model_obj = request.env[model_name]
                cur_d = self._ddom(date_field, params['from_date'], params['to_date']) + params['extra_domain']
                prev_d = self._ddom(date_field, params['prev_from'], params['prev_to']) + params['extra_domain']

                if model_name == 'account.move':
                    invoice_filter = [('move_type', 'in', ('out_invoice', 'out_refund'))]
                    cur_d = cur_d + invoice_filter
                    prev_d = prev_d + invoice_filter

                kpis = {}
                for kpi_name, sum_field in kpi_defs:
                    cur_count, cur_val = self._agg(model_obj, cur_d, sum_field)
                    prev_count, prev_val = self._agg(model_obj, prev_d, sum_field)
                    kpis[kpi_name] = self._kpi(cur_val if sum_field else cur_count,
                                               prev_val if sum_field else prev_count)
                module_kpis[mod_key] = kpis

            total_employees = 0
            if 'hr.employee' in request.env and self._check_model_access('hr.employee') and self._user_has_module_role(user, 'hr'):
                total_employees = request.env['hr.employee'].search_count(
                    [('active', '=', True)] + params['extra_domain'])

            if 'hr' in module_kpis:
                module_kpis['hr']['total_employees'] = {
                    'current': total_employees, 'previous': None, 'delta': None, 'delta_percent': None
                }

            overdue = 0
            if self._user_has_module_role(user, 'crm'):
                overdue += self._overdue_activities('crm.lead')
            if self._user_has_module_role(user, 'sales'):
                overdue += self._overdue_activities('sale.order')
            alerts = []
            if overdue > 0:
                alerts.append({
                    'type': 'warning',
                    'title': 'Overdue activities',
                    'message': f'{overdue} overdue follow-ups across CRM and Sales',
                    'count': overdue,
                })

            return self._json_response(
                data={
                    'kpis': module_kpis,
                    'accessible_modules': list(module_kpis.keys()),
                    'alerts': alerts,
                    'meta': self._analytics_meta(params),
                },
                message="Dashboard summary retrieved"
            )

        except Exception as e:
            _logger.error("Dashboard analytics error: %s", str(e))
            return self._error_response("Error retrieving dashboard analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/crm/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_crm(self):
        """CRM analytics: leads, pipeline, revenue, win rate."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'crm.lead' not in request.env or not self._check_model_access('crm.lead'):
                return self._error_response("CRM not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'crm'):
                return self._error_response("Access denied: requires Sales/CRM role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Lead = request.env['crm.lead']
            cur_d = self._ddom('create_date', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('create_date', params['prev_from'], params['prev_to']) + params['extra_domain']

            cur_count, cur_rev = self._agg(Lead, cur_d, 'expected_revenue')
            prev_count, prev_rev = self._agg(Lead, prev_d, 'expected_revenue')

            cur_won = Lead.search_count(cur_d + [('stage_id.is_won', '=', True)])
            prev_won = Lead.search_count(prev_d + [('stage_id.is_won', '=', True)])
            cur_wr = round(cur_won / cur_count * 100, 1) if cur_count else 0.0
            prev_wr = round(prev_won / prev_count * 100, 1) if prev_count else 0.0

            kpis = {
                'total_leads': self._kpi(cur_count, prev_count),
                'expected_revenue': self._kpi(cur_rev, prev_rev),
                'won': self._kpi(cur_won, prev_won),
                'win_rate': self._kpi(cur_wr, prev_wr),
            }

            stage_rg = Lead.read_group(cur_d, ['expected_revenue'], ['stage_id'])
            chart_rg = Lead.read_group(cur_d, ['expected_revenue'], ['create_date:month'])

            alerts = []
            overdue = self._overdue_activities('crm.lead')
            if overdue:
                alerts.append({'type': 'warning', 'title': 'Overdue activities',
                               'message': f'{overdue} overdue follow-ups on leads', 'count': overdue})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_stage': self._breakdown(stage_rg, 'stage_id', 'expected_revenue')},
                'chart': self._chart_series(chart_rg, 'create_date:month', 'expected_revenue'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="CRM analytics retrieved")

        except Exception as e:
            _logger.error("CRM analytics error: %s", str(e))
            return self._error_response("Error retrieving CRM analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/sales/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_sales(self):
        """Sales analytics: orders, revenue, quotation conversion."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'sale.order' not in request.env or not self._check_model_access('sale.order'):
                return self._error_response("Sales not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'sales'):
                return self._error_response("Access denied: requires Sales role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            SO = request.env['sale.order']
            cur_d = self._ddom('date_order', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('date_order', params['prev_from'], params['prev_to']) + params['extra_domain']

            cur_count, cur_rev = self._agg(SO, cur_d, 'amount_total')
            prev_count, prev_rev = self._agg(SO, prev_d, 'amount_total')
            cur_avg = round(cur_rev / cur_count, 2) if cur_count else 0.0
            prev_avg = round(prev_rev / prev_count, 2) if prev_count else 0.0

            cur_confirmed = SO.search_count(cur_d + [('state', '=', 'sale')])
            prev_confirmed = SO.search_count(prev_d + [('state', '=', 'sale')])
            cur_draft = SO.search_count(cur_d + [('state', '=', 'draft')])
            prev_draft = SO.search_count(prev_d + [('state', '=', 'draft')])

            kpis = {
                'total_orders': self._kpi(cur_count, prev_count),
                'total_revenue': self._kpi(cur_rev, prev_rev),
                'avg_order_value': self._kpi(cur_avg, prev_avg),
                'confirmed_orders': self._kpi(cur_confirmed, prev_confirmed),
                'draft_quotations': self._kpi(cur_draft, prev_draft),
            }

            state_rg = SO.read_group(cur_d, ['amount_total'], ['state'])
            chart_rg = SO.read_group(cur_d, ['amount_total'], ['date_order:month'])

            alerts = []
            overdue = self._overdue_activities('sale.order')
            if overdue:
                alerts.append({'type': 'warning', 'title': 'Overdue activities',
                               'message': f'{overdue} overdue follow-ups on orders', 'count': overdue})
            if cur_draft > 5:
                alerts.append({'type': 'info', 'title': 'Pending quotations',
                               'message': f'{cur_draft} draft quotations awaiting confirmation', 'count': cur_draft})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state', 'amount_total')},
                'chart': self._chart_series(chart_rg, 'date_order:month', 'amount_total'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Sales analytics retrieved")

        except Exception as e:
            _logger.error("Sales analytics error: %s", str(e))
            return self._error_response("Error retrieving sales analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/invoicing/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_invoicing(self):
        """Invoicing analytics: invoices, revenue, payments, overdue."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.move' not in request.env or not self._check_model_access('account.move'):
                return self._error_response("Invoicing not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'invoicing'):
                return self._error_response("Access denied: requires Accounting/Invoicing role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Move = request.env['account.move']
            inv_filter = [('move_type', 'in', ('out_invoice', 'out_refund'))]
            cur_d = self._ddom('invoice_date', params['from_date'], params['to_date']) + params['extra_domain'] + inv_filter
            prev_d = self._ddom('invoice_date', params['prev_from'], params['prev_to']) + params['extra_domain'] + inv_filter

            cur_count, cur_total = self._agg(Move, cur_d, 'amount_total')
            prev_count, prev_total = self._agg(Move, prev_d, 'amount_total')
            _, cur_residual = self._agg(Move, cur_d + [('state', '=', 'posted')], 'amount_residual')
            _, prev_residual = self._agg(Move, prev_d + [('state', '=', 'posted')], 'amount_residual')
            cur_paid = cur_total - cur_residual
            prev_paid = prev_total - prev_residual

            kpis = {
                'total_invoices': self._kpi(cur_count, prev_count),
                'total_amount': self._kpi(cur_total, prev_total),
                'amount_paid': self._kpi(cur_paid, prev_paid),
                'amount_due': self._kpi(cur_residual, prev_residual),
            }

            state_rg = Move.read_group(cur_d + [('state', '=', 'posted')], ['amount_total'], ['payment_state'])
            chart_rg = Move.read_group(cur_d, ['amount_total'], ['invoice_date:month'])

            alerts = []
            today_str = datetime.now().date().isoformat()
            overdue_count = Move.search_count(inv_filter + [
                ('state', '=', 'posted'),
                ('payment_state', 'not in', ('paid', 'reversed')),
                ('invoice_date_due', '<', today_str),
            ] + params['extra_domain'])
            if overdue_count:
                alerts.append({'type': 'danger', 'title': 'Overdue invoices',
                               'message': f'{overdue_count} invoices past due date', 'count': overdue_count})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_payment_state': self._breakdown(state_rg, 'payment_state', 'amount_total')},
                'chart': self._chart_series(chart_rg, 'invoice_date:month', 'amount_total'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Invoicing analytics retrieved")

        except Exception as e:
            _logger.error("Invoicing analytics error: %s", str(e))
            return self._error_response("Error retrieving invoicing analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/inventory/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_inventory(self):
        """Inventory analytics: transfers, late shipments, status breakdown."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'stock.picking' not in request.env or not self._check_model_access('stock.picking'):
                return self._error_response("Inventory not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'inventory'):
                return self._error_response("Access denied: requires Inventory role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Pick = request.env['stock.picking']
            cur_d = self._ddom('scheduled_date', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('scheduled_date', params['prev_from'], params['prev_to']) + params['extra_domain']

            cur_count, _ = self._agg(Pick, cur_d)
            prev_count, _ = self._agg(Pick, prev_d)
            cur_done = Pick.search_count(cur_d + [('state', '=', 'done')])
            prev_done = Pick.search_count(prev_d + [('state', '=', 'done')])
            cur_waiting = Pick.search_count(cur_d + [('state', 'in', ('waiting', 'confirmed', 'assigned'))])
            prev_waiting = Pick.search_count(prev_d + [('state', 'in', ('waiting', 'confirmed', 'assigned'))])

            today_str = datetime.now().date().isoformat()
            cur_late = Pick.search_count([
                ('scheduled_date', '<', today_str),
                ('state', 'not in', ('done', 'cancel')),
            ] + params['extra_domain'])

            kpis = {
                'total_transfers': self._kpi(cur_count, prev_count),
                'done': self._kpi(cur_done, prev_done),
                'waiting': self._kpi(cur_waiting, prev_waiting),
                'late': self._kpi(cur_late, 0),
            }

            state_rg = Pick.read_group(cur_d, [], ['state'])
            chart_rg = Pick.read_group(cur_d, [], ['scheduled_date:month'])

            alerts = []
            if cur_late:
                alerts.append({'type': 'danger', 'title': 'Late transfers',
                               'message': f'{cur_late} transfers past scheduled date', 'count': cur_late})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state')},
                'chart': self._chart_series(chart_rg, 'scheduled_date:month'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Inventory analytics retrieved")

        except Exception as e:
            _logger.error("Inventory analytics error: %s", str(e))
            return self._error_response("Error retrieving inventory analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/purchases/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_purchases(self):
        """Purchase analytics: orders, amounts, status breakdown."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'purchase.order' not in request.env or not self._check_model_access('purchase.order'):
                return self._error_response("Purchases not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'purchase'):
                return self._error_response("Access denied: requires Purchase role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            PO = request.env['purchase.order']
            cur_d = self._ddom('date_order', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('date_order', params['prev_from'], params['prev_to']) + params['extra_domain']

            cur_count, cur_total = self._agg(PO, cur_d, 'amount_total')
            prev_count, prev_total = self._agg(PO, prev_d, 'amount_total')
            cur_draft = PO.search_count(cur_d + [('state', '=', 'draft')])
            prev_draft = PO.search_count(prev_d + [('state', '=', 'draft')])
            cur_confirmed = PO.search_count(cur_d + [('state', '=', 'purchase')])
            prev_confirmed = PO.search_count(prev_d + [('state', '=', 'purchase')])

            kpis = {
                'total_orders': self._kpi(cur_count, prev_count),
                'total_amount': self._kpi(cur_total, prev_total),
                'draft': self._kpi(cur_draft, prev_draft),
                'confirmed': self._kpi(cur_confirmed, prev_confirmed),
            }

            state_rg = PO.read_group(cur_d, ['amount_total'], ['state'])
            chart_rg = PO.read_group(cur_d, ['amount_total'], ['date_order:month'])

            alerts = []
            if cur_draft > 3:
                alerts.append({'type': 'info', 'title': 'Pending RFQs',
                               'message': f'{cur_draft} draft purchase orders awaiting confirmation', 'count': cur_draft})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state', 'amount_total')},
                'chart': self._chart_series(chart_rg, 'date_order:month', 'amount_total'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Purchase analytics retrieved")

        except Exception as e:
            _logger.error("Purchase analytics error: %s", str(e))
            return self._error_response("Error retrieving purchase analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/hr/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_hr(self):
        """HR analytics: headcount, new hires, department breakdown."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'hr.employee' not in request.env or not self._check_model_access('hr.employee'):
                return self._error_response("HR not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'hr'):
                return self._error_response("Access denied: requires HR role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Emp = request.env['hr.employee']

            total_active = Emp.search_count([('active', '=', True)] + params['extra_domain'])

            cur_d = self._ddom('create_date', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('create_date', params['prev_from'], params['prev_to']) + params['extra_domain']
            cur_new = Emp.search_count(cur_d)
            prev_new = Emp.search_count(prev_d)

            dept_count = 0
            if 'hr.department' in request.env:
                dept_count = request.env['hr.department'].search_count([])

            kpis = {
                'total_employees': {'current': total_active, 'previous': None, 'delta': None, 'delta_percent': None},
                'new_hires': self._kpi(cur_new, prev_new),
                'departments': {'current': dept_count, 'previous': None, 'delta': None, 'delta_percent': None},
            }

            dept_rg = Emp.read_group(
                [('active', '=', True)] + params['extra_domain'], [], ['department_id'])
            chart_rg = Emp.read_group(cur_d, [], ['create_date:month'])

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {'by_department': self._breakdown(dept_rg, 'department_id')},
                'chart': self._chart_series(chart_rg, 'create_date:month'),
                'alerts': [],
                'meta': self._analytics_meta(params),
            }, message="HR analytics retrieved")

        except Exception as e:
            _logger.error("HR analytics error: %s", str(e))
            return self._error_response("Error retrieving HR analytics", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/projects/overview', type='http', auth='none', methods=['GET'], csrf=False)
    def analytics_projects(self):
        """Project analytics: tasks, status breakdown, overdue items."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # Subscription enforcement
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'project.task' not in request.env or not self._check_model_access('project.task'):
                return self._error_response("Projects not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'project'):
                return self._error_response("Access denied: requires Project role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Task = request.env['project.task']
            cur_d = self._ddom('create_date', params['from_date'], params['to_date']) + params['extra_domain']
            prev_d = self._ddom('create_date', params['prev_from'], params['prev_to']) + params['extra_domain']

            cur_count, _ = self._agg(Task, cur_d)
            prev_count, _ = self._agg(Task, prev_d)

            today_str = datetime.now().date().isoformat()
            overdue_tasks = Task.search_count([
                ('date_deadline', '<', today_str),
                ('stage_id.fold', '=', False),
            ] + params['extra_domain'])

            closed_domain = [('stage_id.fold', '=', True)]
            cur_closed = Task.search_count(cur_d + closed_domain)
            prev_closed = Task.search_count(prev_d + closed_domain)

            kpis = {
                'total_tasks': self._kpi(cur_count, prev_count),
                'closed': self._kpi(cur_closed, prev_closed),
                'overdue': self._kpi(overdue_tasks, 0),
            }

            stage_rg = Task.read_group(cur_d, [], ['stage_id'])
            project_rg = Task.read_group(cur_d, [], ['project_id'])
            chart_rg = Task.read_group(cur_d, [], ['create_date:month'])

            alerts = []
            if overdue_tasks:
                alerts.append({'type': 'danger', 'title': 'Overdue tasks',
                               'message': f'{overdue_tasks} tasks past deadline', 'count': overdue_tasks})

            return self._json_response(data={
                'kpis': kpis,
                'breakdowns': {
                    'by_stage': self._breakdown(stage_rg, 'stage_id'),
                    'by_project': self._breakdown(project_rg, 'project_id'),
                },
                'chart': self._chart_series(chart_rg, 'create_date:month'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Project analytics retrieved")

        except Exception as e:
            _logger.error("Project analytics error: %s", str(e))
            return self._error_response("Error retrieving project analytics", 500, "ANALYTICS_ERROR")

    # ===== INTERNAL: CACHE INVALIDATION =====

    @http.route('/api/v2/internal/invalidate-cache', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def invalidate_enforcer_cache(self):
        """Internal endpoint for Control Plane to push cache invalidation.

        Authenticated by internal token (not user session).
        """
        token = request.httprequest.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        expected = os.environ.get('CONTROL_PLANE_TOKEN', '')
        if not expected or token != expected:
            return self._error_response("Unauthorized", 401, "UNAUTHORIZED")

        enforcer = self._get_enforcer()
        if enforcer is not None:
            enforcer.invalidate_cache()

        return self._json_response(data={"invalidated": True}, message="Cache invalidated")
