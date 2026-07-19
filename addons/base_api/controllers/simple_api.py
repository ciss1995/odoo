# -*- coding: utf-8 -*-

import json
import logging
import os
import secrets
import string
import time as _time
from datetime import datetime, timedelta
from odoo import fields, http
from odoo.http import request
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

from odoo.addons.base_api.services.auth_cookies import (
    SESSION_COOKIE_NAME,
    clear_session_cookies,
    is_secure_request,
    set_session_cookies,
)

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

    def _finalize_response(self, response, status_code):
        """Common pre-return work for every base_api response.

        Closes the request cursor's open transaction and forces the HTTP
        connection to close. Both are defenses against an Odoo-19 behavior
        where, with auth='none' routes running on threading-mode workers, a
        successful request can leave its psycopg2 connection in
        `idle in transaction (ClientRead)` — the next request that touches an
        overlapping row (notably `api.session.last_activity`) then blocks
        indefinitely on the held transactionid lock. See BUGS.md.

        Committing here is safe: Odoo's post-dispatch middleware will run an
        additional commit on a clean cursor, which is a no-op.
        """
        try:
            request.env.cr.commit()
        except Exception as commit_err:  # pragma: no cover — defensive
            _logger.debug("response finalize commit skipped: %s", commit_err)
        response.headers['Connection'] = 'close'
        response.status_code = status_code
        self._log_api_call(status_code)
        return response

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
        return self._finalize_response(response, status_code)

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
        return self._finalize_response(response, status_code)

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
        return self._finalize_response(response, status_code)

    # ===== Idempotency =====

    _IDEMPOTENCY_KEY_HEADER = 'Idempotency-Key'
    _IDEMPOTENCY_KEY_MAX_LEN = 64

    def _idempotency_lookup(self, user):
        """Check the Idempotency-Key header and decide replay/proceed/conflict.

        Returns one of:
            (None, None)              — no key supplied, caller proceeds normally
            (response, None)          — replay the cached response
            (None, idem_record)       — first time seeing this key; caller proceeds
                                        and MUST call _idempotency_store(idem_record, ...)
                                        once the work succeeds.
            (error_response, None)    — key reused with different body → 409
        """
        raw_key = request.httprequest.headers.get(self._IDEMPOTENCY_KEY_HEADER) or ''
        raw_key = raw_key.strip()
        if not raw_key:
            return None, None
        if len(raw_key) > self._IDEMPOTENCY_KEY_MAX_LEN:
            return self._error_response(
                "Idempotency-Key too long (max 64)", 400, "IDEMPOTENCY_KEY_INVALID",
            ), None
        # Allowlist: UUIDs, opaque alphanumeric ids. Reject anything that
        # could be log-poisoning or path-traversal-shaped.
        if not all(c.isalnum() or c in '-_' for c in raw_key):
            return self._error_response(
                "Idempotency-Key has invalid characters", 400, "IDEMPOTENCY_KEY_INVALID",
            ), None

        try:
            body = request.httprequest.get_data(cache=True) or b''
        except Exception:
            body = b''
        request_hash = request.env['api.idempotency'].hash_request(
            request.httprequest.method,
            request.httprequest.path,
            body,
        )

        Idem = request.env['api.idempotency'].sudo()
        existing = Idem.search(
            [('user_id', '=', user.id), ('key', '=', raw_key)], limit=1,
        )
        if existing and not existing.is_expired():
            if existing.request_hash != request_hash:
                return self._error_response(
                    "Idempotency-Key reused with a different request body",
                    409, "IDEMPOTENCY_KEY_CONFLICT",
                ), None
            cached_data, status_code = existing.replay()
            if cached_data is not None:
                # Re-wrap so headers and finalization match a fresh response.
                response = request.make_response(
                    json.dumps(cached_data, default=str),
                    headers=[('Content-Type', 'application/json')],
                )
                return self._finalize_response(response, status_code), None
            # Stored but parse failed → treat as miss and overwrite.
            existing.unlink()

        # First time seeing this key — create a placeholder so concurrent
        # retries collide on the UNIQUE(user_id, key) constraint instead
        # of both creating records.
        idem = Idem.create({
            'user_id': user.id,
            'key': raw_key,
            'request_hash': request_hash,
            'response_status': 0,  # filled in by _idempotency_store
        })
        return None, idem

    def _idempotency_store(self, idem_record, response):
        """Persist the response body + status against an idempotency placeholder.

        Called after the wrapped operation succeeds. If response is an
        Odoo Response object, we pull the body and status off it.
        Failures here are logged but never raised — idempotency is a
        latency optimisation, not a correctness guarantee, and breaking
        the real response on a cache write would be worse.
        """
        if idem_record is None:
            return response
        try:
            body = response.get_data(as_text=True) if hasattr(response, 'get_data') else ''
            status = response.status_code if hasattr(response, 'status_code') else 200
            idem_record.sudo().write({
                'response_json': body,
                'response_status': status,
            })
        except Exception:
            _logger.exception("Failed to persist idempotency response")
        return response

    def _safe_exc_message(self, exc, fallback="Internal error"):
        """Return a user-facing message for an exception, suppressing internals.

        Odoo's UserError / ValidationError / MissingError / AccessError carry
        strings that are designed to be shown to end users (business-rule
        violations, missing records, access denial). Anything else — psycopg
        IntegrityError, KeyError, AttributeError, etc. — may contain SQL
        fragments, file paths, or internal IDs that an attacker could mine to
        map the backend. For those, return ``fallback`` and log the full
        traceback server-side instead.
        """
        if isinstance(exc, (UserError, ValidationError, MissingError, AccessError)):
            return str(exc)
        _logger.exception("Unhandled exception leaked to API caller (suppressed): %s", exc)
        return fallback

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
        """Validate the session and switch env to that user.

        Header-wins-with-cookie-fallback. See services.auth for full semantics
        (sliding refresh, _auth_source / _api_session / _refresh_session_cookie
        request stash). Single source of truth shared with BaseApiController.
        """
        from odoo.addons.base_api.services.auth import authenticate_session
        return authenticate_session(self._error_response, self._enforce_user_rate_limit)

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

    def _safe_read(self, records, fields_list):
        """Read records degrading per-field on AccessError.

        ``_filter_readable_fields`` doesn't catch all restrictions because
        some fields gate access through Many2one comodels or computed
        getters whose ACL is only enforced at read time. When the bulk
        ``.read()`` raises AccessError, fall back to probing each field
        individually and drop the offenders. The omitted fields are
        returned alongside the data so the caller can surface them.
        """
        if not records:
            return [], []
        try:
            return records.read(fields_list), []
        except AccessError:
            pass

        safe_fields = []
        dropped = []
        for fname in fields_list:
            if fname == 'id':
                safe_fields.append(fname)
                continue
            try:
                records.read([fname])
                safe_fields.append(fname)
            except AccessError:
                dropped.append(fname)
        return records.read(safe_fields), dropped

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

    def _auto_apply_fiscal_position(self, partner):
        """Resolve and assign the partner's fiscal position via Odoo's
        canonical resolver.

        Called after create/update of res.partner when the payload
        didn't pin ``property_account_position_id`` explicitly. Uses
        ``account.fiscal.position._get_fiscal_position(partner)``,
        which respects the partner's country / state / vat against the
        FPs the company has seeded (typically by an ``l10n_*`` addon —
        domestic / export / intracom / non-resident services).

        Safe to call even when the resolver returns nothing — we simply
        leave the field untouched. Errors are swallowed and logged
        rather than failing the parent request: an FP miss is a quality
        signal, not a reason to 500 the partner create.
        """
        try:
            FP = request.env['account.fiscal.position'].sudo()
            resolved = FP._get_fiscal_position(partner)
            if resolved:
                partner.with_context(active_test=False).sudo().property_account_position_id = resolved.id
        except Exception:  # noqa: BLE001 — diagnostic-only side effect
            _logger.exception(
                "Auto-apply fiscal position failed for partner %s; "
                "leaving property_account_position_id unset",
                partner.id,
            )

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

        # Sales Administrator / "All Documents" tier sees every lead and
        # every order regardless of team or owner. The default (Salesman
        # tier) is scoped to own + unassigned + own teams.
        sales_sees_all = (
            user.has_group('sales_team.group_sale_manager')
            or user.has_group('sales_team.group_sale_salesman_all_leads')
        )

        if model_name == 'crm.lead':
            if sales_sees_all:
                return []
            team_ids = user.crm_team_ids.ids if 'crm_team_ids' in user._fields else []
            if team_ids:
                return ['|', '|',
                        ('user_id', '=', uid),
                        ('user_id', '=', False),
                        ('team_id', 'in', team_ids)]
            return ['|', ('user_id', '=', uid), ('user_id', '=', False)]

        if model_name == 'sale.order':
            if sales_sees_all:
                return []
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
        currency_code = None
        language = None
        try:
            company = request.env['res.company'].sudo().search([], order='id asc', limit=1)
            if company:
                company_name = company.name
                if company.currency_id:
                    currency_code = company.currency_id.name
            # Tenant default language: the admin user's lang. Set at provisioning
            # time; new users inherit it via res.partner.lang unless overridden.
            admin = request.env['res.users'].sudo().search([('login', '=', 'admin')], limit=1)
            if admin and admin.lang:
                language = admin.lang
        except Exception:
            pass

        # Per-tenant capability flags so SPAs can conditionally render
        # optional UI without a separate roundtrip. Cheap to compute — we
        # just probe field presence on the relevant Odoo models.
        features = {}
        try:
            features['product_expiry'] = (
                'use_expiration_date' in request.env['stock.lot']._fields
                and 'use_expiration_date' in request.env['product.template']._fields
            )
        except Exception:
            features['product_expiry'] = False

        return self._json_response(data={
            'company_name': company_name,
            'currency': currency_code,
            'language': language,
            'features': features,
        })

    @http.route('/api/v2/public/jobs/<int:job_id>', type='http', auth='none', methods=['GET'], csrf=False, cors='*')
    def public_job(self, job_id):
        """Return a job posting for public consumption (sharing on WhatsApp,
        LinkedIn, careers page, etc.). Unauthenticated.

        Only jobs explicitly marked `is_public=True` are exposed. The response
        is a minimal projection — no internal recruiter/manager IDs, no
        applicant data — to keep the surface safe to share publicly.
        """
        try:
            job = request.env['hr.job'].sudo().browse(job_id)
            if not job.exists() or not job.is_public:
                return self._error_response('Job not found or not public.', 404, 'JOB_NOT_FOUND')

            company = job.company_id
            logo_url = None
            if company and company.id:
                logo_url = f"/web/image/res.company/{company.id}/logo"

            data = {
                'id': job.id,
                'name': job.name,
                'description': job.description or '',
                'requirements': job.requirements or '',
                'department': job.department_id.name if job.department_id else None,
                'employment_type': job.contract_type_id.name if job.contract_type_id else None,
                'salary_min': job.salary_min or None,
                'salary_max': job.salary_max or None,
                'salary_currency': job.salary_currency_id.name if job.salary_currency_id else None,
                'salary_period': job.salary_period,
                'company_name': company.name if company else None,
                'company_logo_url': logo_url,
                'created_at': job.create_date.isoformat() if job.create_date else None,
            }
            return self._json_response(data=data)
        except Exception as e:
            return self._error_response(self._safe_exc_message(e), 500, 'INTERNAL_ERROR')

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
        # Try session authentication first, then API key
        is_valid, result = self._authenticate_session()
        if not is_valid:
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

            # Optional category filter. Uses child_of so picking a parent
            # category includes products in its subcategories too.
            category_id_param = request.httprequest.args.get('category_id', '').strip()
            if category_id_param:
                try:
                    category_id = int(category_id_param)
                except ValueError:
                    return self._error_response("category_id must be an integer", 400, "INVALID_PARAMS")
                if not request.env['product.category'].browse(category_id).exists():
                    return self._error_response("Category not found", 404, "CATEGORY_NOT_FOUND")
                domain.append(('categ_id', 'child_of', category_id))

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
                    'category_id': product.categ_id.id if product.categ_id else False,
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

    @http.route('/api/v2/product-categories', type='http', auth='none', methods=['GET'], csrf=False)
    def list_product_categories(self):
        """List product categories for the UI category picker/sidebar."""
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
            Category = request.env['product.category']
            Product = request.env['product.template']

            if not self._check_model_access('product.category', 'read'):
                return self._error_response("Access denied for product.category", 403, "ACCESS_DENIED")

            limit, offset = self._parse_pagination()
            if limit is None:
                return self._error_response("limit and offset must be integers", 400, "INVALID_PARAMS")

            # Optional filters
            domain = []
            search_term = request.httprequest.args.get('search', '').strip()
            if search_term:
                domain.append(('complete_name', 'ilike', search_term))

            parent_id_param = request.httprequest.args.get('parent_id', '').strip()
            if parent_id_param:
                if parent_id_param.lower() in ('false', 'null', '0'):
                    domain.append(('parent_id', '=', False))
                else:
                    try:
                        domain.append(('parent_id', '=', int(parent_id_param)))
                    except ValueError:
                        return self._error_response("parent_id must be an integer", 400, "INVALID_PARAMS")

            categories = Category.search(domain, limit=limit, offset=offset, order='complete_name')

            # Per-category product count (descendants included), respecting ACLs.
            categories_data = []
            for category in categories:
                product_count = Product.search_count([
                    ('categ_id', 'child_of', category.id),
                    ('active', '=', True),
                ])
                categories_data.append({
                    'id': category.id,
                    'name': category.name,
                    'complete_name': category.complete_name,
                    'parent_id': category.parent_id.id if category.parent_id else False,
                    'parent_name': category.parent_id.complete_name if category.parent_id else False,
                    'product_count': product_count,
                })

            can_create = Category.check_access_rights('create', raise_exception=False)

            return self._json_response(
                data={
                    'categories': categories_data,
                    'count': len(categories_data),
                    'total_count': Category.search_count(domain),
                    'can_create': bool(can_create),
                },
                message="Product categories retrieved successfully"
            )

        except AccessError:
            return self._error_response("Access denied for product.category", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Error listing product categories: %s", str(e))
            return self._error_response("Error retrieving product categories", 500, "CATEGORIES_ERROR")

    @http.route('/api/v2/product-categories', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def create_product_category(self):
        """Create a product category (requires Inventory Manager rights)."""
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
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            if not data or not isinstance(data, dict):
                return self._error_response("No data provided", 400, "NO_DATA")

            name = (data.get('name') or '').strip()
            if not name:
                return self._error_response("name is required", 400, "MISSING_NAME")

            if not self._check_model_access('product.category', 'create'):
                return self._error_response(
                    "You don't have permission to create product categories",
                    403, "ACCESS_DENIED",
                )

            Category = request.env['product.category']

            payload = {'name': name}
            parent_id = data.get('parent_id')
            if parent_id:
                try:
                    parent_id = int(parent_id)
                except (TypeError, ValueError):
                    return self._error_response("parent_id must be an integer", 400, "INVALID_PARAMS")
                if not Category.browse(parent_id).exists():
                    return self._error_response("Parent category not found", 404, "PARENT_NOT_FOUND")
                payload['parent_id'] = parent_id

            new_category = Category.create(payload)

            return self._json_response(
                data={
                    'id': new_category.id,
                    'name': new_category.name,
                    'complete_name': new_category.complete_name,
                    'parent_id': new_category.parent_id.id if new_category.parent_id else False,
                },
                message="Product category created",
                status_code=201,
            )

        except AccessError:
            return self._error_response(
                "You don't have permission to create product categories",
                403, "ACCESS_DENIED",
            )
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CREATE_ERROR")
        except Exception as e:
            _logger.error("Error creating product category: %s", str(e))
            return self._error_response("Error creating product category", 500, "CREATE_ERROR")

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
            has_custom_filters = False

            # product.template-specific: category_id maps to categ_id child_of
            # (descendant-aware), matching the /api/v2/products?category_id=... contract.
            # product.template has no `category_id` field, so the generic param loop
            # below would otherwise silently drop this argument.
            if model == 'product.template':
                category_id_param = request.httprequest.args.get('category_id', '').strip()
                if category_id_param:
                    try:
                        category_id = int(category_id_param)
                    except ValueError:
                        return self._error_response("category_id must be an integer", 400, "INVALID_PARAMS")
                    if not request.env['product.category'].browse(category_id).exists():
                        return self._error_response("Category not found", 404, "CATEGORY_NOT_FOUND")
                    domain.append(('categ_id', 'child_of', category_id))
                    has_custom_filters = True

            # Parse explicit domain filter (JSON-encoded Odoo domain)
            domain_param = request.httprequest.args.get('domain', '').strip()
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

            # hr.employee: the ACL above already gates read access
            # (hr.group_hr_user or base.group_system). Within a tenant the
            # company directory should be visible to everyone who passes
            # that gate. Odoo's multi-company record rule on hr.employee
            # otherwise hides peers whose company_id isn't in the
            # requester's allowed_company_ids — which happens when extra
            # res.company rows exist or auto-created employees landed
            # under a different company than the requester. sudo() the
            # search; field-level groups still protect sensitive columns.
            search_obj = model_obj.sudo() if model == 'hr.employee' else model_obj

            # Search records
            records = search_obj.search(domain, limit=limit, offset=offset, order='id')

            # Read specified fields — degrade per-field on AccessError so a
            # single restricted field doesn't fail the whole request.
            records_data, dropped_fields = self._safe_read(records, available_fields)
            effective_fields = [f for f in available_fields if f not in dropped_fields]

            response = {
                'records': records_data,
                'count': len(records_data),
                'model': model,
                'fields': effective_fields,
                'total_count': search_obj.search_count(domain),
            }
            if dropped_fields:
                response['fields_dropped'] = dropped_fields

            return self._json_response(
                data=response,
                message=f"Found {len(records_data)} records in {model}",
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
            return self._error_response(self._safe_exc_message(e), 400, "SEARCH_ERROR")
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
            # See search_model: bypass Odoo's multi-company rule on
            # hr.employee — ACL already gates the read.
            search_obj = model_obj.sudo() if model == 'hr.employee' else model_obj
            record = search_obj.search(record_domain, limit=1)

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

            # Read the record with per-field AccessError degradation
            records_data, dropped_fields = self._safe_read(record, available_fields)
            record_data = records_data[0] if records_data else {}
            effective_fields = [f for f in available_fields if f not in dropped_fields]

            response = {
                'record': record_data,
                'model': model,
                'id': record_id,
                'fields_returned': effective_fields,
                'total_fields_available': len(all_fields),
            }
            if dropped_fields:
                response['fields_dropped'] = dropped_fields

            return self._json_response(
                data=response,
                message=f"Found record {record_id} in {model}",
            )

        except AccessError as e:
            _logger.warning(
                "AccessError getting record %s/%s for user %s (id=%s): %s",
                model, record_id, user.login, user.id, e,
            )
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "GET_RECORD_ERROR")
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

                csrf_token = secrets.token_urlsafe(32)
                response = self._json_response_sensitive(
                    data={
                        'session_token': session_token,
                        'expires_at': expires_at.isoformat(),
                        'user': {
                            'id': user.id,
                            'name': user.name,
                            'login': user.login,
                            'email': user.email,
                            'groups': sorted(xid for xid in user.group_ids.get_external_id().values() if xid),
                        }
                    },
                    message="Login successful"
                )
                set_session_cookies(
                    response, session_token, csrf_token,
                    secure=is_secure_request(request.httprequest),
                )
                return response
                    
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
            # Use the session that authed this request, not a re-lookup. With
            # header-wins precedence and a stale cookie around, a cookie-first
            # lookup can invalidate a different (still-current) session — same
            # class of mismatch as the CSRF gate fix in fea5d5b07daa.
            session = getattr(request.httprequest, '_api_session', None)
            if session:
                session.sudo().write({'active': False})

            response = self._json_response(message="Logout successful")
            clear_session_cookies(response, secure=is_secure_request(request.httprequest))
            return response

        except Exception as e:
            _logger.error("Logout error: %s", str(e))
            return self._error_response("Logout failed", 500, "LOGOUT_ERROR")

    @http.route('/api/v2/auth/forgot-password', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def forgot_password(self):
        """Send a password-reset email for the given login.

        Public endpoint — no session required. Wraps Odoo's standard
        ``res.users.reset_password(login)`` which generates a signup
        token, stores it on the partner, and queues an email via the
        ``auth_signup.reset_password_email`` template. The email link
        points to ``/web/reset_password?token=<token>`` on the tenant
        host (built from ``web.base.url``), where Odoo renders the
        standard "Set new password" form.

        Always returns success — never reveals whether the email exists.
        Prevents user-enumeration attacks. Rate-limited per IP to slow
        down email-flood abuse.
        """
        try:
            # Per-IP rate limiting (reuse the login limiter — same risk
            # profile: anonymous, email-typed input, server-side action).
            from odoo.addons.base_api.services.rate_limiter import check_login_rate_limit
            client_ip = request.httprequest.environ.get(
                'HTTP_X_FORWARDED_FOR', request.httprequest.remote_addr
            )
            if client_ip and ',' in client_ip:
                client_ip = client_ip.split(',')[0].strip()
            allowed, retry_after = check_login_rate_limit(client_ip or 'unknown')
            if not allowed:
                response = self._error_response(
                    f"Too many requests. Try again in {retry_after} seconds.",
                    429, "RATE_LIMITED"
                )
                response.headers['Retry-After'] = str(retry_after)
                return response

            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")

            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            login = (data.get('login') or data.get('email') or '').strip()
            if not login:
                return self._error_response("login or email required", 400, "MISSING_LOGIN")

            # Check whether reset is even enabled for this tenant. If
            # the admin disabled signup-reset in Settings → General, we
            # surface that as a 400 rather than silently swallowing.
            reset_enabled = request.env['ir.config_parameter'].sudo().get_param(
                'auth_signup.reset_password'
            ) == 'True'
            if not reset_enabled:
                return self._error_response(
                    "Password reset is disabled. Contact your administrator.",
                    400, "RESET_DISABLED",
                )

            # Best-effort: call Odoo's reset_password. It raises on
            # unknown login — we swallow that so we don't leak whether
            # the email exists. Log it server-side for support.
            try:
                request.env['res.users'].sudo().reset_password(login)
                _logger.info("Password reset email sent for <%s>", login)
            except Exception as exc:
                # Don't leak: same response shape either way.
                _logger.info(
                    "Password reset attempted for unknown/invalid login <%s>: %s",
                    login, exc,
                )

            return self._json_response(
                message="If an account with that email exists, password reset instructions have been sent.",
                data={'login': login},
            )

        except Exception as e:
            _logger.error("forgot_password error: %s", str(e))
            return self._error_response("Internal error", 500, "FORGOT_PASSWORD_ERROR")

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

            api_session = getattr(request.httprequest, '_api_session', None)
            expires_at_iso = api_session.expires_at.isoformat() if api_session else None

            # xml_ids only — UI keys role gating off these. Drops UI-created
            # custom groups that have no xml_id, which is the intended behavior
            # (those groups can't be referenced by the SPA's role enum).
            group_xmlids = sorted(
                xid for xid in user.group_ids.get_external_id().values() if xid
            )
            module_access = self._get_module_access()

            response = self._json_response(
                data={
                    'expires_at': expires_at_iso,
                    'module_access': module_access,
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email,
                        'phone': user.phone or None,
                        'active': user.active,
                        'password_is_temporary': user.password_is_temporary,
                        'company_id': [user.company_id.id, user.company_id.name] if user.company_id else False,
                        'partner_id': [user.partner_id.id, user.partner_id.name] if user.partner_id else False,
                        'groups': group_xmlids,
                        'permissions': {
                            'is_admin': user.has_group('base.group_system'),
                            'is_user': user.has_group('base.group_user'),
                            'can_manage_users': user.has_group('base.group_erp_manager')
                        },
                        'module_access': module_access,
                        'plan': plan_info,
                    }
                },
                message="User information retrieved"
            )

            # Upgrade handshake: header-authed call with no cookie present →
            # mint cookies on this response so the SPA can switch to
            # credentials: 'include' on its next request.
            auth_source = getattr(request.httprequest, '_auth_source', None)
            has_session_cookie = SESSION_COOKIE_NAME in request.httprequest.cookies
            if api_session and auth_source == 'header' and not has_session_cookie:
                header_token = request.httprequest.headers.get('session-token')
                if header_token:
                    csrf_token = secrets.token_urlsafe(32)
                    set_session_cookies(
                        response, header_token, csrf_token,
                        secure=is_secure_request(request.httprequest),
                    )

            return response
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

            # Get all groups excluding hidden/technical ones.
            # The hidden-category filter must allow groups with no
            # privilege at all (top-level "Role / *" groups in Odoo 19
            # like base.group_system / base.group_user) — otherwise
            # SQL NULL semantics drop them and the catalog is incomplete.
            hidden_category = request.env.ref('base.module_category_hidden', raise_if_not_found=False)
            domain = [('share', '=', False)]
            if hidden_category:
                domain += [
                    '|',
                    ('privilege_id', '=', False),
                    ('privilege_id.category_id', '!=', hidden_category.id),
                ]
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

        # Idempotency-Key check — a double-click / retried POST with the
        # same key replays the cached response instead of creating a
        # second record. No-op when no header is supplied.
        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

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

            # Fiscal-position auto-apply: when the caller didn't pin one
            # explicitly, ask Odoo which FP matches the partner's country
            # / state / VAT and write it. Lets SPA tenants get correct
            # tax math (export vs domestic vs intracom) without having
            # to know fiscal positions exist. Manual override always
            # wins because we only fire when the field is ABSENT from
            # the payload — explicit values flow through `create(data)`
            # above and are respected.
            if (
                model == 'res.partner'
                and 'property_account_position_id' not in data
                and 'account.fiscal.position' in request.env
            ):
                self._auto_apply_fiscal_position(new_record)

            # Return a safe subset of fields to avoid post-create AccessError
            # on models where some fields are not readable by the creator.
            basic_fields = ['id', 'name', 'display_name', 'create_date']
            safe_fields = [f for f in basic_fields if f in request.env[model]._fields]
            if not safe_fields:
                safe_fields = ['id']
            record_data = new_record.read(safe_fields)[0]

            response = self._json_response(
                data={
                    'id': new_record.id,
                    'record': record_data
                },
                message=f"Record created in {model}",
                status_code=201
            )
            return self._idempotency_store(idem, response)

        except AccessError as e:
            return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CREATE_ERROR")
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
            # *_id variants are direct Odoo IDs (preferred by the SPA when it
            # already has them from a dropdown); *_name variants do a name
            # lookup and stay for programmatic callers without ID resolution.
            employee_fields = {}
            for key in (
                'department_name', 'department_id',
                'job_title', 'job_name', 'job_id',
                'parent_name', 'parent_id',
                'work_phone',
            ):
                val = data.pop(key, None)
                if val is not None:
                    employee_fields[key] = val

            group_names = data.pop('group_names', [])
            group_ids_param = data.pop('group_ids', [])
            group_xml_ids = data.pop('group_xml_ids', [])
            auto_generate_credentials = data.pop('auto_generate_credentials', True)

            if 'password' not in data and auto_generate_credentials:
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
                data['password'] = temp_password
                data['password_is_temporary'] = True
            else:
                temp_password = data.get('password', None)

            # Resolve group IDs before create so they can be set atomically.
            # xml_ids are preferred (stable across locale/version); fall back
            # to names (legacy callers) then raw ids.
            resolved_group_ids = []
            if group_xml_ids:
                for xid in group_xml_ids:
                    g = request.env.ref(xid, raise_if_not_found=False)
                    if g and g._name == 'res.groups':
                        resolved_group_ids.append(g.id)
            elif group_names:
                groups = request.env['res.groups'].sudo().search([('name', 'in', group_names)])
                resolved_group_ids = groups.ids
            elif group_ids_param:
                resolved_group_ids = group_ids_param

            if not resolved_group_ids:
                default_group = request.env.ref('base.group_user', raise_if_not_found=False)
                if default_group:
                    resolved_group_ids = [default_group.id]

            if resolved_group_ids:
                data['group_ids'] = [(6, 0, resolved_group_ids)]

            # Odoo 19's hr.res_users.create only auto-creates the linked
            # hr.employee when create_employee=True (or create_employee_id
            # is set). Without it, internal users land without an
            # employee record and never appear in /api/v2/search/hr.employee.
            if 'hr.employee' in request.env and 'create_employee' not in data and 'create_employee_id' not in data:
                data['create_employee'] = True

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

                    # Department: prefer the explicit ID if provided
                    dept_id = employee_fields.get('department_id')
                    dept_name = employee_fields.get('department_name')
                    if dept_id:
                        if request.env['hr.department'].sudo().browse(dept_id).exists():
                            emp_vals['department_id'] = dept_id
                    elif dept_name:
                        dept = request.env['hr.department'].sudo().search(
                            [('name', '=ilike', dept_name)], limit=1)
                        if dept:
                            emp_vals['department_id'] = dept.id

                    # Job position: prefer the explicit ID if provided
                    job_id = employee_fields.get('job_id')
                    job_name = employee_fields.get('job_name')
                    if job_id:
                        if request.env['hr.job'].sudo().browse(job_id).exists():
                            emp_vals['job_id'] = job_id
                    elif job_name:
                        job = request.env['hr.job'].sudo().search(
                            [('name', '=ilike', job_name)], limit=1)
                        if job:
                            emp_vals['job_id'] = job.id

                    # Manager: prefer the explicit ID if provided
                    parent_id = employee_fields.get('parent_id')
                    parent_name = employee_fields.get('parent_name')
                    if parent_id:
                        if request.env['hr.employee'].sudo().browse(parent_id).exists():
                            emp_vals['parent_id'] = parent_id
                    elif parent_name:
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

            # Change password. Clearing password_is_temporary only on own-password
            # change — when the user deliberately picks a value, the modal is done.
            # Admin-via-this-endpoint preserves the existing flag (use the
            # /reset-password endpoint to explicitly mark a new temp).
            writes = {'password': new_password}
            if is_own_password:
                writes['password_is_temporary'] = False
            target_user.sudo().write(writes)
            
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
                elif field in ['group_names', 'group_ids', 'group_xml_ids']:
                    # Handle group updates for admins. xml_ids are preferred
                    # (stable across locale/Odoo version) but we keep the
                    # name- and id-based forms for backwards compatibility.
                    if is_admin:
                        if field == 'group_xml_ids':
                            resolved_ids = []
                            for xid in value or []:
                                g = request.env.ref(xid, raise_if_not_found=False)
                                if g and g._name == 'res.groups':
                                    resolved_ids.append(g.id)
                            if resolved_ids:
                                update_data['group_ids'] = [(6, 0, resolved_ids)]
                        elif field == 'group_names':
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
                group_ext = target_user.group_ids.get_external_id()
                user_data.update({
                    'groups': [{
                        'id': g.id,
                        'name': g.name,
                        'full_name': g.full_name,
                        'xml_id': group_ext.get(g.id, ''),
                    } for g in target_user.group_ids],
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

            # Reset password and flag as temporary so the SPA forces a change.
            target_user.sudo().write({
                'password': temp_password,
                'password_is_temporary': True,
            })
            
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
                    group_ext = user.group_ids.get_external_id()
                    user_data.update({
                        'groups': [{
                            'id': g.id,
                            'name': g.name,
                            'full_name': g.full_name,
                            'xml_id': group_ext.get(g.id, ''),
                        } for g in user.group_ids],
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

            # Fiscal-position auto-apply on update — same trigger as
            # create: only when the caller didn't supply the field AND
            # something that affects FP resolution changed (country,
            # state, or vat). Without those guards every benign update
            # ("rename customer") would re-run the resolver, which is
            # wasteful and would re-trigger auto-apply after a user
            # deliberately cleared the FP.
            if (
                model == 'res.partner'
                and 'property_account_position_id' not in data
                and 'account.fiscal.position' in request.env
                and any(k in data for k in ('country_id', 'state_id', 'vat'))
            ):
                self._auto_apply_fiscal_position(record)

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
            return self._error_response(self._safe_exc_message(e), 400, "UPDATE_ERROR")
        except Exception as e:
            _logger.error("Error updating %s/%s: %s", model, record_id, str(e))
            return self._error_response("Error updating record", 500, "UPDATE_ERROR")

    # ===== MAIL: NOTES + ATTACHMENTS =====
    #
    # Generic post-a-note + upload-an-attachment endpoints. Work on any
    # mail.thread-enabled record (crm.lead, project.task, sale.order, …)
    # so we don't have to wire per-model routes. Authorization is rooted
    # in the parent record's ACL: WRITE for posting/uploading, READ for
    # downloading. ir.attachment stays in BLOCKED_MODELS — these
    # purpose-built routes are the only sanctioned way to create or
    # serve attachments through the API, since direct CRUD lets a
    # caller forge res_model/res_id and bypass the parent's ACL.

    # Hard ceiling per upload. Keeps a runaway client (or an attacker
    # with valid creds) from filling the tenant volume with one POST.
    # Matches the soft cap used elsewhere; raise deliberately if a
    # legit use case needs it.
    _ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB

    def _resolve_mail_target(self, model_name, record_id, user, operation='write'):
        """Resolve (record, error_response). Used by all mail endpoints.

        Centralises the four checks every mail endpoint needs:
        1. Model exists, is not blocked, and inherits ``mail.thread``
           (so ``message_post`` / ``message_ids`` actually work).
        2. The model's module is in the tenant's plan.
        3. The user holds the requested operation right on the model
           (typically ``write`` for posting/uploading, ``read`` for
           downloading).
        4. The specific record exists AND is inside the user's scope
           domain — otherwise we'd let a salesman post notes on a lead
           they can't see, which surfaces existence + leaks audit data.
        """
        if model_name not in request.env:
            return None, self._error_response(
                f"Model '{model_name}' not found", 404, "MODEL_NOT_FOUND",
            )
        if self._is_model_blocked(model_name):
            return None, self._error_response(
                f"Access denied for model '{model_name}'", 403, "ACCESS_DENIED",
            )
        model_obj = request.env[model_name]
        if not hasattr(model_obj, 'message_post'):
            return None, self._error_response(
                f"Model '{model_name}' does not support messages",
                400, "MODEL_NOT_MAIL_THREAD",
            )
        module_error = self._enforce_module_access(model_name)
        if module_error:
            return None, module_error
        if not self._check_model_access(model_name, operation):
            return None, self._error_response(
                f"Access denied for model '{model_name}'", 403, "ACCESS_DENIED",
            )
        scope_domain = self._get_record_scope_domain(model_name, user)
        record = model_obj.search([('id', '=', record_id)] + scope_domain, limit=1)
        if not record:
            return None, self._error_response(
                f"Record {record_id} not found in {model_name}",
                404, "RECORD_NOT_FOUND",
            )
        return record, None

    def _serialize_attachment(self, attachment):
        """Public-safe attachment dict — never includes the raw bytes.

        Frontend pulls the file body separately via ``GET
        /api/v2/attachment/<id>``, so omitting ``datas`` here keeps
        listing/post responses small and avoids re-encoding base64
        for every note that references the same file.
        """
        return {
            'id': attachment.id,
            'name': attachment.name,
            'mimetype': attachment.mimetype or 'application/octet-stream',
            'file_size': attachment.file_size or 0,
            'res_model': attachment.res_model or '',
            'res_id': attachment.res_id or 0,
            'url': f'/api/v2/attachment/{attachment.id}',
        }

    @http.route('/api/v2/message_post', type='http', auth='none',
                methods=['POST'], csrf=False, readonly=False)
    def message_post(self):
        """Post an internal note (mt_note) on any mail-thread record.

        Body: ``{"model": "crm.lead", "res_id": 42, "body": "text",
        "attachment_ids": [1, 2]}``

        ``attachment_ids`` are IDs previously returned by
        ``/api/v2/attachment/upload`` — Odoo's ``message_post`` will
        link them to the new mail.message via the M2M.

        Uses ``subtype_xmlid='mail.mt_note'`` so the message is an
        internal note (not a public comment that notifies followers /
        sends emails). The SPA's notes UI is internal-only by design.
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
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response(
                    "Content-Type must be application/json",
                    400, "INVALID_CONTENT_TYPE",
                )
            try:
                data = request.httprequest.get_json(force=True)
                if not isinstance(data, dict):
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            model_name = (data.get('model') or '').strip()
            res_id = data.get('res_id')
            body = data.get('body') or ''
            attachment_ids = data.get('attachment_ids') or []

            if not model_name or not isinstance(res_id, int) or res_id <= 0:
                return self._error_response(
                    "Both 'model' (string) and 'res_id' (positive int) are required",
                    400, "INVALID_PARAMS",
                )
            if not isinstance(body, str) or not body.strip():
                return self._error_response(
                    "'body' must be a non-empty string", 400, "INVALID_BODY",
                )
            if not isinstance(attachment_ids, list) or not all(
                isinstance(a, int) and a > 0 for a in attachment_ids
            ):
                return self._error_response(
                    "'attachment_ids' must be a list of positive ints",
                    400, "INVALID_ATTACHMENTS",
                )

            record, err = self._resolve_mail_target(model_name, res_id, user, 'write')
            if err:
                return err

            # Re-check that the caller actually owns/can-read every
            # attachment they're trying to glue to the message. Without
            # this, a user who knows an attachment id from another
            # tenant record could attach it to a record THEY can write —
            # effectively leaking content across the ACL boundary.
            if attachment_ids:
                attachments = request.env['ir.attachment'].search(
                    [('id', 'in', attachment_ids)],
                )
                if len(attachments) != len(set(attachment_ids)):
                    return self._error_response(
                        "One or more attachments not found or not accessible",
                        404, "ATTACHMENT_NOT_FOUND",
                    )

            # mt_note = internal note (not a comment that emails followers).
            message = record.message_post(
                body=body,
                attachment_ids=list(attachment_ids),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

            return self._json_response(
                data={
                    'message': {
                        'id': message.id,
                        'body': message.body or '',
                        'date': fields.Datetime.to_string(message.date) if message.date else None,
                        'author': (
                            [message.author_id.id, message.author_id.display_name]
                            if message.author_id else None
                        ),
                        'attachments': [
                            self._serialize_attachment(a) for a in message.attachment_ids
                        ],
                    },
                },
                message="Note posted",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "MESSAGE_POST_ERROR")
        except Exception as e:
            _logger.error("Error posting message: %s", str(e))
            return self._error_response("Error posting message", 500, "MESSAGE_POST_ERROR")

    @http.route('/api/v2/attachment/upload', type='http', auth='none',
                methods=['POST'], csrf=False, readonly=False)
    def upload_attachment(self):
        """Upload a file as an ir.attachment bound to a target record.

        Multipart form fields:
        - ``model`` — target res_model (e.g. ``crm.lead``)
        - ``res_id`` — target res_id (positive integer)
        - ``file`` — the file part

        Caller must hold WRITE access on the target record. The created
        attachment's ``res_model`` / ``res_id`` are set to the target so
        Odoo's standard ACL ("read this attachment if you can read its
        parent record") covers downloads automatically.

        Returns the attachment metadata (no body). The id is then
        passed to ``/api/v2/message_post`` as part of ``attachment_ids``.
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
            form = request.httprequest.form
            files = request.httprequest.files
            model_name = (form.get('model') or '').strip()
            try:
                res_id = int(form.get('res_id') or 0)
            except (TypeError, ValueError):
                res_id = 0
            uploaded = files.get('file')

            if not model_name or res_id <= 0:
                return self._error_response(
                    "Form fields 'model' and 'res_id' are required",
                    400, "INVALID_PARAMS",
                )
            if uploaded is None or not uploaded.filename:
                return self._error_response(
                    "Form field 'file' is required", 400, "FILE_REQUIRED",
                )

            # Stream-aware size guard. ``content_length`` is the
            # multipart-claimed size (cheap pre-check); we still
            # measure the actual bytes after read() so a lying client
            # can't bypass the limit.
            claimed = uploaded.content_length or 0
            if claimed and claimed > self._ATTACHMENT_MAX_BYTES:
                return self._error_response(
                    f"File exceeds {self._ATTACHMENT_MAX_BYTES} bytes",
                    413, "FILE_TOO_LARGE",
                )

            record, err = self._resolve_mail_target(model_name, res_id, user, 'write')
            if err:
                return err

            raw = uploaded.read()
            if len(raw) > self._ATTACHMENT_MAX_BYTES:
                return self._error_response(
                    f"File exceeds {self._ATTACHMENT_MAX_BYTES} bytes",
                    413, "FILE_TOO_LARGE",
                )
            if not raw:
                return self._error_response("File is empty", 400, "FILE_EMPTY")

            import base64
            attachment = request.env['ir.attachment'].create({
                'name': uploaded.filename,
                'datas': base64.b64encode(raw),
                'res_model': model_name,
                'res_id': record.id,
                'mimetype': uploaded.mimetype or 'application/octet-stream',
            })

            return self._json_response(
                data={'attachment': self._serialize_attachment(attachment)},
                message="Attachment uploaded",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "UPLOAD_ERROR")
        except Exception as e:
            _logger.error("Error uploading attachment: %s", str(e))
            return self._error_response("Error uploading attachment", 500, "UPLOAD_ERROR")

    @http.route('/api/v2/attachments', type='http', auth='none',
                methods=['GET'], csrf=False)
    def list_attachments(self):
        """Bulk metadata lookup for attachments referenced by messages.

        ``?ids=1,2,3`` — returns the subset the caller can read. Silent
        on missing/forbidden ids (no 404) so partial visibility doesn't
        break the notes UI when a single attachment is restricted.
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

        raw_ids = (request.httprequest.args.get('ids') or '').strip()
        if not raw_ids:
            return self._json_response(data={'records': []}, message="OK")
        try:
            ids = [int(x) for x in raw_ids.split(',') if x.strip()]
        except ValueError:
            return self._error_response("Invalid ids", 400, "INVALID_PARAMS")
        if not ids:
            return self._json_response(data={'records': []}, message="OK")
        if len(ids) > self.MAX_PAGE_LIMIT:
            return self._error_response(
                f"Too many ids (max {self.MAX_PAGE_LIMIT})",
                400, "INVALID_PARAMS",
            )

        # ir.attachment's record rules already gate by parent-record
        # readability, so .search() returns only what the user is
        # allowed to see. No need to re-check per record.
        attachments = request.env['ir.attachment'].search([('id', 'in', ids)])
        return self._json_response(
            data={'records': [self._serialize_attachment(a) for a in attachments]},
            message="OK",
        )

    @http.route('/api/v2/attachment/<int:attachment_id>', type='http',
                auth='none', methods=['GET'], csrf=False)
    def download_attachment(self, attachment_id):
        """Stream an attachment's binary body with the correct mimetype.

        Read access on the parent record is sufficient (ir.attachment's
        own record rules enforce this). 404 covers both "doesn't exist"
        and "exists but you can't see it" to avoid existence-probe
        leaks.
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

        attachment = request.env['ir.attachment'].search(
            [('id', '=', attachment_id)], limit=1,
        )
        if not attachment:
            return self._error_response(
                "Attachment not found", 404, "ATTACHMENT_NOT_FOUND",
            )
        try:
            raw = attachment.raw
        except AccessError:
            return self._error_response(
                "Attachment not found", 404, "ATTACHMENT_NOT_FOUND",
            )
        if raw is None:
            return self._error_response(
                "Attachment not found", 404, "ATTACHMENT_NOT_FOUND",
            )

        # Quote the filename for the Content-Disposition header so
        # commas / spaces / Unicode characters don't break parsing.
        from urllib.parse import quote
        filename = attachment.name or 'attachment'
        response = request.make_response(
            raw,
            headers=[
                ('Content-Type', attachment.mimetype or 'application/octet-stream'),
                ('Content-Length', str(len(raw))),
                ('Content-Disposition', f"inline; filename*=UTF-8''{quote(filename)}"),
                ('Cache-Control', 'private, max-age=0'),
            ],
        )
        return self._finalize_response(response, 200)

    @http.route('/api/v2/attachment/<int:attachment_id>', type='http',
                auth='none', methods=['DELETE'], csrf=False, readonly=False)
    def delete_attachment(self, attachment_id):
        """Delete an attachment. Authorization is rooted in the parent record:
        the caller must hold WRITE access on the ``res_model``/``res_id`` the
        attachment is bound to (same rule that lets them upload it). Attachments
        not bound to a writable parent record are refused.
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
            attachment = request.env['ir.attachment'].search(
                [('id', '=', attachment_id)], limit=1,
            )
            if not attachment:
                return self._error_response(
                    "Attachment not found", 404, "ATTACHMENT_NOT_FOUND",
                )

            if not attachment.res_model or not attachment.res_id:
                return self._error_response(
                    "Cannot delete an attachment not linked to a record",
                    403, "ACCESS_DENIED",
                )

            # Require write access on the parent record.
            _record, err = self._resolve_mail_target(
                attachment.res_model, attachment.res_id, user, 'write',
            )
            if err:
                return err

            # Authorized via parent-write; unlink with sudo so ir.attachment's
            # own (stricter) ACL doesn't block a legitimate delete.
            attachment.sudo().unlink()
            return self._json_response(
                data={'id': attachment_id}, message="Attachment deleted",
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "DELETE_ERROR")
        except Exception as e:
            _logger.error("Error deleting attachment %s: %s", attachment_id, str(e))
            return self._error_response("Error deleting attachment", 500, "DELETE_ERROR")

    @http.route('/api/v2/account_move/<int:move_id>/register_payment',
                type='http', auth='none',
                methods=['POST'], csrf=False, readonly=False)
    def register_payment_for_move(self, move_id):
        """Register a payment against a posted invoice or vendor bill.

        Uses Odoo's ``account.payment.register`` wizard — the only
        reliable path to create + post + reconcile a payment in one
        atomic call. Direct ``account.payment`` creation with
        ``reconciled_invoice_ids`` is fragile in Odoo 19 (the M2M is
        a computed depends field, not writable at create time); the
        wizard, in contrast, derives partner / partner_type /
        payment_type from the move, picks a default journal +
        method-line if not provided, posts the payment, and reconciles
        it with the move's open AR/AP lines.

        Body (JSON):
        - ``amount`` (number, optional) — defaults to the move's
          residual. Pass a smaller number for a partial payment.
        - ``date`` (YYYY-MM-DD, optional) — defaults to today.
        - ``journal_id`` (int, optional) — bank/cash journal id.
          Wizard picks the company's first valid journal if absent.
        - ``payment_method_line_id`` (int, optional) — the specific
          method line within the journal (cheque / mobile money /
          generic manual). Wizard picks the journal default if absent.
        - ``ref`` (str, optional) — proof-of-payment reference (Wave
          TX id, bank slip number, MoMo ref). Stored on
          ``account.payment.ref``.

        Returns the created payment + the move's refreshed
        payment_state / amount_residual so the SPA can update the
        UI without a separate fetch.
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

        if not self._check_model_access('account.move', 'write'):
            return self._error_response(
                "Access denied for account.move", 403, "ACCESS_DENIED",
            )
        module_error = self._enforce_module_access('account.move')
        if module_error:
            return module_error

        # Parse body — empty body is OK (wizard defaults take over).
        try:
            data = request.httprequest.get_json(force=True) or {}
            if not isinstance(data, dict):
                return self._error_response(
                    "Body must be a JSON object", 400, "INVALID_BODY",
                )
        except Exception:
            data = {}

        # Resolve target move under the user's scope so a salesperson
        # can't register payment against a bill they can't see.
        scope_domain = self._get_record_scope_domain('account.move', user)
        move = request.env['account.move'].search(
            [('id', '=', move_id)] + scope_domain, limit=1,
        )
        if not move:
            return self._error_response(
                f"Invoice {move_id} not found", 404, "MOVE_NOT_FOUND",
            )
        if move.state != 'posted':
            return self._error_response(
                "Invoice must be posted before payment can be registered",
                400, "INVALID_MOVE_STATE",
            )
        if move.move_type not in (
            'out_invoice', 'out_refund', 'in_invoice', 'in_refund',
        ):
            return self._error_response(
                "Only invoices and bills can have payments registered",
                400, "INVALID_MOVE_TYPE",
            )

        # Build wizard payload. The wizard's amount field defaults to
        # the move residual; we only set it when the SPA wants a
        # partial payment.
        wizard_vals = {}
        if 'amount' in data and data['amount'] is not None:
            try:
                wizard_vals['amount'] = float(data['amount'])
            except (TypeError, ValueError):
                return self._error_response(
                    "'amount' must be a number", 400, "INVALID_PARAMS",
                )
            if wizard_vals['amount'] <= 0:
                return self._error_response(
                    "'amount' must be positive", 400, "INVALID_PARAMS",
                )
        if data.get('date'):
            wizard_vals['payment_date'] = data['date']
        if data.get('journal_id'):
            try:
                wizard_vals['journal_id'] = int(data['journal_id'])
            except (TypeError, ValueError):
                return self._error_response(
                    "'journal_id' must be an integer",
                    400, "INVALID_PARAMS",
                )
        if data.get('payment_method_line_id'):
            try:
                wizard_vals['payment_method_line_id'] = int(
                    data['payment_method_line_id'],
                )
            except (TypeError, ValueError):
                return self._error_response(
                    "'payment_method_line_id' must be an integer",
                    400, "INVALID_PARAMS",
                )
        if data.get('ref'):
            # The wizard's "communication" field becomes the payment's
            # memo + the bank statement narration on reconciliation.
            wizard_vals['communication'] = str(data['ref'])[:200]

        try:
            ctx = {
                'active_model': 'account.move',
                'active_ids': move.ids,
                'active_id': move.id,
            }
            wizard = request.env['account.payment.register'] \
                .with_context(**ctx).create(wizard_vals)
            wizard.action_create_payments()

            # Refresh move + locate the freshly-created payment. The
            # wizard records the payment(s) on move.matched_payment_ids;
            # we read the most recent one for the response.
            move.invalidate_recordset()
            payment = request.env['account.payment']
            if move.matched_payment_ids:
                payment = move.matched_payment_ids.sorted('id')[-1]

            payment_data = None
            if payment:
                # Resolve journal + method line into [id, name] tuples
                # so the SPA can render them without a second fetch.
                # In Odoo 19 the payment's free-text note lives on
                # ``memo`` (the legacy ``ref`` field was removed); we
                # expose it as ``memo`` to the SPA.
                payment_data = {
                    'id': payment.id,
                    'name': payment.name or '',
                    'amount': payment.amount,
                    'date': fields.Date.to_string(payment.date) if payment.date else None,
                    'state': payment.state,
                    'memo': payment.memo or '',
                    'partner_id': [payment.partner_id.id, payment.partner_id.display_name] if payment.partner_id else False,
                    'journal_id': [payment.journal_id.id, payment.journal_id.display_name] if payment.journal_id else False,
                    'payment_method_line_id': [
                        payment.payment_method_line_id.id,
                        payment.payment_method_line_id.display_name,
                    ] if payment.payment_method_line_id else False,
                }
            move_data = {
                'id': move.id,
                'name': move.name,
                'state': move.state,
                'payment_state': move.payment_state,
                'amount_residual': move.amount_residual,
                'matched_payment_ids': move.matched_payment_ids.ids,
            }
            return self._json_response(
                data={'payment': payment_data, 'move': move_data},
                message="Payment registered",
                status_code=201,
            )
        except (AccessError, UserError, ValidationError) as e:
            return self._error_response(
                self._safe_exc_message(e), 400, "PAYMENT_REGISTER_ERROR",
            )
        except Exception as e:
            _logger.error(
                "Error registering payment for move %d: %s", move_id, str(e),
            )
            return self._error_response(
                "Error registering payment", 500, "PAYMENT_REGISTER_ERROR",
            )

    @http.route('/api/v2/record_attachments', type='http', auth='none',
                methods=['GET'], csrf=False)
    def list_record_attachments(self):
        """List attachments bound to a single record.

        Query: ``?model=account.payment&res_id=42``. Returns every
        ``ir.attachment`` whose ``(res_model, res_id)`` matches and which
        the caller can read. Used to render the receipts/PDFs section on
        a bill or payment without forcing the SPA to walk message_ids.

        Re-uses ``_resolve_mail_target`` so the same gates (model
        allowed, module entitled, ACL, scope domain) apply — a caller
        who cannot read the parent record gets a 404, not a leak of
        the attachment list. Mail-thread requirement is enforced
        intentionally: surfaces only attachments living next to a
        proper chatter, never on free-floating models.
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

        model_name = (request.httprequest.args.get('model') or '').strip()
        res_id_raw = (request.httprequest.args.get('res_id') or '').strip()
        if not model_name or not res_id_raw:
            return self._error_response(
                "Both 'model' and 'res_id' query params are required",
                400, "INVALID_PARAMS",
            )
        try:
            res_id = int(res_id_raw)
        except ValueError:
            return self._error_response(
                "'res_id' must be an integer", 400, "INVALID_PARAMS",
            )
        if res_id <= 0:
            return self._error_response(
                "'res_id' must be positive", 400, "INVALID_PARAMS",
            )

        record, err = self._resolve_mail_target(model_name, res_id, user, 'read')
        if err:
            return err

        # ir.attachment's own record rules will further filter what
        # the caller can see; explicit res_model/res_id domain restricts
        # to this record only.
        attachments = request.env['ir.attachment'].search([
            ('res_model', '=', model_name),
            ('res_id', '=', record.id),
        ], order='create_date desc')
        return self._json_response(
            data={'records': [self._serialize_attachment(a) for a in attachments]},
            message="OK",
        )

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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

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

            response = self._json_response(
                data={'invoice': inv_data},
                message="Invoice created from sale order",
                status_code=201,
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "NOTHING_TO_INVOICE")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "INVOICE_CREATE_ERROR")
        except Exception as e:
            _logger.error("Error creating invoice from SO %s: %s", order_id, str(e))
            return self._error_response("Error creating invoice", 500, "INVOICE_CREATE_ERROR")

    @http.route('/api/v2/sales/<int:order_id>/confirm', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def confirm_sale_order(self, order_id):
        """Confirm a quotation via Odoo's canonical ``action_confirm()``.

        Why this exists rather than just ``PUT /update/sale.order/{id}``
        with ``state='sale'``: Odoo's CRM-Sales bridge
        (``addons/sale_crm/models/sale_order.py::action_confirm``) is what
        moves the linked ``crm.lead`` to its "Won" stage and updates
        expected revenue. That bridge fires from ``action_confirm()``,
        not from a raw ``state`` write — so going through write() leaves
        leads stranded in their current stage.

        The endpoint also runs the rest of the standard confirmation
        plumbing (delivery picking creation, stock reservation,
        sequence assignment, etc.) that ``action_confirm`` does.

        Returns the refreshed order's state plus opportunity_id so the
        caller can verify the lead linkage took.
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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        try:
            if 'sale.order' not in request.env:
                return self._error_response("Sales module not installed", 404, "MODULE_NOT_FOUND")
            if not self._check_model_access('sale.order', 'write'):
                return self._error_response("Access denied for sale.order", 403, "ACCESS_DENIED")

            order = request.env['sale.order'].browse(order_id)
            if not order.exists():
                return self._error_response(f"Sale order {order_id} not found", 404, "NOT_FOUND")

            if order.state in ('sale', 'done'):
                # Idempotent: already confirmed.
                pass
            elif order.state == 'cancel':
                return self._error_response(
                    "Cancelled orders cannot be confirmed.", 400, "INVALID_STATE",
                )
            else:
                order.action_confirm()

            data = order.read(['id', 'name', 'state', 'opportunity_id'])[0]
            response = self._json_response(
                data={'order': data},
                message=f"Sale order {order_id} confirmed",
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "CONFIRM_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CONFIRM_ERROR")
        except Exception as e:
            _logger.error("Error confirming SO %s: %s", order_id, str(e))
            return self._error_response("Error confirming sale order", 500, "CONFIRM_ERROR")

    @http.route('/api/v2/sales/<int:order_id>/cancel', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def cancel_sale_order(self, order_id):
        """Cancel a sale order via Odoo's canonical ``action_cancel()``.

        Goes through the action (not a raw ``state='cancel'`` write) so the
        standard cancellation plumbing runs (cancels pickings, etc.). Needed
        so a confirmed/sent order can be deleted afterwards — Odoo refuses to
        unlink a sale order unless it is in ``draft`` or ``cancel``. A clean
        UserError (e.g. an order with posted invoices) surfaces as a handled
        400 rather than a 500.
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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        try:
            if 'sale.order' not in request.env:
                return self._error_response("Sales module not installed", 404, "MODULE_NOT_FOUND")
            if not self._check_model_access('sale.order', 'write'):
                return self._error_response("Access denied for sale.order", 403, "ACCESS_DENIED")

            order = request.env['sale.order'].browse(order_id)
            if not order.exists():
                return self._error_response(f"Sale order {order_id} not found", 404, "NOT_FOUND")

            if order.state != 'cancel':
                order.action_cancel()

            data = order.read(['id', 'name', 'state'])[0]
            response = self._json_response(
                data={'order': data},
                message=f"Sale order {order_id} cancelled",
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "CANCEL_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CANCEL_ERROR")
        except Exception as e:
            _logger.error("Error cancelling SO %s: %s", order_id, str(e))
            return self._error_response("Error cancelling sale order", 500, "CANCEL_ERROR")

    @http.route('/api/v2/sales/<int:order_id>/send', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def send_sale_order(self, order_id):
        """Mark a quotation as sent (``state='sent'``) via Odoo's
        ``action_quotation_sent()``.

        This is what populates the "Quotation Sent" pipeline column — nothing
        in the draft → Confirm flow sets ``sent`` otherwise. We use
        ``action_quotation_sent`` (state transition only) rather than
        ``action_quotation_send`` (which returns a mail-composer wizard action
        that a JSON API can't drive, and which depends on outgoing SMTP).
        Only draft quotations transition; already-sent/confirmed orders are
        returned as-is (idempotent).
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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        try:
            if 'sale.order' not in request.env:
                return self._error_response("Sales module not installed", 404, "MODULE_NOT_FOUND")
            if not self._check_model_access('sale.order', 'write'):
                return self._error_response("Access denied for sale.order", 403, "ACCESS_DENIED")

            order = request.env['sale.order'].browse(order_id)
            if not order.exists():
                return self._error_response(f"Sale order {order_id} not found", 404, "NOT_FOUND")

            if order.state == 'draft':
                if hasattr(order, 'action_quotation_sent'):
                    order.action_quotation_sent()
                else:  # pragma: no cover — defensive for non-standard sale
                    order.write({'state': 'sent'})

            data = order.read(['id', 'name', 'state'])[0]
            response = self._json_response(
                data={'order': data},
                message=f"Sale order {order_id} marked as sent",
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "SEND_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "SEND_ERROR")
        except Exception as e:
            _logger.error("Error sending SO %s: %s", order_id, str(e))
            return self._error_response("Error sending sale order", 500, "SEND_ERROR")

    @http.route('/api/v2/invoices/<int:invoice_id>/credit-note', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def create_credit_note(self, invoice_id):
        """Create a credit note (reversal) for a customer invoice.

        A credit note is structurally a reversing ``account.move`` of type
        ``out_refund`` linked back to the original via ``reversed_entry_id``.
        We use Odoo's canonical ``account.move._reverse_moves()`` (the same
        engine the "Add Credit Note" / "Reverse" wizard uses) rather than a
        raw ``create({'move_type': 'out_refund'})``, so the reversal is
        properly linked and its lines mirror the source.

        Optional JSON body ``{"post": true}`` performs a full "reverse &
        reconcile": the credit note is posted and reconciled against the
        source, so the invoice's balance/status immediately reflects it
        (payment_state becomes ``reversed``). Default (``post`` false /
        absent) leaves the credit note in ``draft`` for review.
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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        try:
            if 'account.move' not in request.env:
                return self._error_response("Accounting module not installed", 404, "MODULE_NOT_FOUND")
            if not self._check_model_access('account.move', 'create'):
                return self._error_response("Access denied for account.move", 403, "ACCESS_DENIED")

            move = request.env['account.move'].browse(invoice_id)
            if not move.exists():
                return self._error_response(f"Invoice {invoice_id} not found", 404, "NOT_FOUND")

            if move.move_type not in ('out_invoice', 'in_invoice'):
                return self._error_response(
                    "Only invoices can be credited/reversed.", 400, "INVALID_MOVE_TYPE",
                )
            if move.state != 'posted':
                return self._error_response(
                    "Only a posted invoice can have a credit note.", 400, "INVALID_STATE",
                )

            # Optional body: {"post": true} → reverse-and-reconcile (posts the
            # credit note and reconciles it against the invoice). Body is
            # optional, so tolerate a missing/invalid one.
            try:
                body = request.httprequest.get_json(force=True, silent=True) or {}
            except Exception:
                body = {}
            do_post = bool(body.get('post'))

            reverse = move._reverse_moves([{
                'ref': f"Reversal of: {move.name or ''}",
                'invoice_date': move.invoice_date,
            }], cancel=do_post)

            data = reverse.read(['id', 'name', 'move_type', 'state', 'reversed_entry_id'])[0]
            response = self._json_response(
                data={'credit_note': data},
                message=f"Credit note created for invoice {invoice_id}",
                status_code=201,
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "CREDIT_NOTE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CREDIT_NOTE_ERROR")
        except Exception as e:
            _logger.error("Error creating credit note for %s: %s", invoice_id, str(e))
            return self._error_response("Error creating credit note", 500, "CREDIT_NOTE_ERROR")

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

        # Critical: this endpoint mutates SO + invoice + payment + stock
        # in one call. A retried double-click without idempotency = double
        # sale, double payment, double stock decrement.
        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

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

            response = self._json_response(
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
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "PURCHASE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "PURCHASE_ERROR")
        except Exception as e:
            _logger.error("In-store purchase error: %s", str(e))
            return self._error_response(
                "Error processing in-store purchase", 500, "PURCHASE_ERROR")

    # ===== INVENTORY ADJUSTMENT =====

    @http.route('/api/v2/inventory/expiring-soon', type='http', auth='none', methods=['GET'], csrf=False)
    def inventory_expiring_soon(self):
        """Return product lots whose alert date has been reached. Requires the
        `product_expiry` Odoo addon (in the `full` plan). If the addon is not
        installed for this tenant, return an empty list with module=disabled
        so the SPA can hide the widget gracefully.
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

        # Probe for product_expiry by checking if the fields exist on stock.lot.
        # When the addon isn't installed, the field is absent and we short-circuit.
        try:
            lot_model = request.env['stock.lot'].sudo().with_user(user)
            field_names = lot_model._fields.keys()
            if 'use_expiration_date' not in field_names or 'expiration_date' not in field_names:
                return self._json_response(data={'enabled': False, 'lots': []})

            # Days-ahead window — defaults to "alert date reached" (today). Caller
            # may pass ?days=30 to expand the window.
            try:
                days = int(request.params.get('days', 0) or 0)
            except (TypeError, ValueError):
                days = 0

            domain = [('use_expiration_date', '=', True)]
            if days > 0:
                # Lots with expiration_date within the next N days
                from datetime import datetime, timedelta
                horizon = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
                domain += [('expiration_date', '!=', False), ('expiration_date', '<=', horizon)]
            else:
                # Default: lots whose alert_date has passed
                domain += [('alert_date', '!=', False),
                           ('alert_date', '<=', fields.Datetime.now())]

            try:
                limit = max(1, min(int(request.params.get('limit', 100) or 100), 500))
            except (TypeError, ValueError):
                limit = 100

            lots = lot_model.search(domain, order='expiration_date asc', limit=limit)
            results = []
            for lot in lots:
                product = lot.product_id
                results.append({
                    'lot_id': lot.id,
                    'lot_name': lot.name,
                    'product_id': product.id if product else None,
                    'product_name': product.display_name if product else None,
                    'product_default_code': product.default_code if product else None,
                    'expiration_date': lot.expiration_date.isoformat() if lot.expiration_date else None,
                    'alert_date': lot.alert_date.isoformat() if lot.alert_date else None,
                    'removal_date': lot.removal_date.isoformat() if lot.removal_date else None,
                    'product_qty': lot.product_qty if hasattr(lot, 'product_qty') else None,
                })
            return self._json_response(data={'enabled': True, 'lots': results})
        except Exception as e:
            return self._error_response(
                self._safe_exc_message(e, fallback="expiring-soon failed"),
                500, "EXPIRY_ERROR",
            )

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

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

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

            response = self._json_response(
                data={'purchase_order': order_data},
                message=message,
                status_code=200,
            )
            return self._idempotency_store(idem, response)

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "CONFIRM_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "CONFIRM_ERROR")
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
            return self._error_response(self._safe_exc_message(e), 400, "VALIDATE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "VALIDATE_ERROR")
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
            return self._error_response(self._safe_exc_message(e), 400, "RETURN_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "RETURN_ERROR")
        except Exception as e:
            _logger.error("Error returning picking %s: %s", picking_id, str(e))
            return self._error_response(
                "Error creating return", 500, "RETURN_ERROR")

    # ===== HR RECRUITMENT: APPLICANT ACTIONS =====

    def _hr_recruitment_guard(self, model='hr.applicant', operation='write'):
        """Shared guard for hr.applicant action endpoints.

        Returns (user, None) on success or (None, error_response) on failure.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return None, user

        sub_error = self._enforce_subscription()
        if sub_error:
            return None, sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return None, quota_error

        if model not in request.env:
            return None, self._error_response(
                "hr_recruitment module not installed", 404, "MODULE_NOT_FOUND")

        if not self._user_has_module_role(user, 'hr'):
            return None, self._error_response(
                "User does not have HR access", 403, "MODULE_ACCESS_DENIED")

        if not self._check_model_access(model, operation):
            return None, self._error_response(
                f"Access denied for model '{model}'", 403, "ACCESS_DENIED")

        return user, None

    @http.route('/api/v2/hr/managers', type='http', auth='none', methods=['GET'], csrf=False)
    def hr_managers(self, **_kwargs):
        """List employees eligible to act as a manager.

        Returns active hr.employee records whose linked res.users belongs to
        ``hr.group_hr_user``, ``hr.group_hr_manager``, or
        ``base.group_system`` — i.e. HR Officers, HR Administrators, and
        System Administrators. These are the people the New/Edit Employee
        dialogs offer in the Manager dropdown.

        Read via sudo() so non-HR users still get a complete list (the
        dropdown itself is admin-only UI; the data is intentionally
        directory-style, not company-confidential).
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
            if 'hr.employee' not in request.env:
                return self._json_response(data={'records': []}, message="hr module not installed")

            group_xml_ids = (
                'hr.group_hr_user',
                'hr.group_hr_manager',
                'base.group_system',
            )
            group_ids = []
            for xid in group_xml_ids:
                g = request.env.ref(xid, raise_if_not_found=False)
                if g:
                    group_ids.append(g.id)

            if not group_ids:
                return self._json_response(data={'records': []}, message="No manager groups found")

            users = request.env['res.users'].sudo().search([
                ('active', '=', True),
                ('group_ids', 'in', group_ids),
            ])

            employees = request.env['hr.employee'].sudo().search([
                ('active', '=', True),
                ('user_id', 'in', users.ids),
            ], order='name')

            records = [{'id': e.id, 'name': e.name} for e in employees]
            return self._json_response(
                data={'records': records, 'total_count': len(records)},
                message="Manager-eligible employees retrieved",
            )
        except Exception as e:
            _logger.error("Error listing hr managers: %s", str(e))
            return self._error_response("Error listing managers", 500, "MANAGERS_ERROR")

    @http.route('/api/v2/hr/applicants/<int:applicant_id>/refuse', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def applicant_refuse(self, applicant_id, **_kwargs):
        """Refuse an applicant.

        Mirrors Odoo's refuse-reason wizard apply: sets ``refuse_reason_id``,
        ``active = False``, ``refuse_date = now``. Skips email sending and
        duplicate-detection — those are wizard-only concerns.

        Body (optional): ``{"reason_id": <hr.applicant.refuse.reason id>}``.
        If omitted, the first reason record is used (matches the wizard
        default).
        """
        user, err = self._hr_recruitment_guard()
        if err:
            return err

        try:
            try:
                data = request.httprequest.get_json(force=True, silent=True) or {}
            except Exception:
                data = {}

            applicant = request.env['hr.applicant'].browse(applicant_id)
            if not applicant.exists():
                return self._error_response(
                    f"Applicant {applicant_id} not found", 404, "NOT_FOUND")

            if applicant.application_status in ('refused', 'hired'):
                return self._error_response(
                    f"Applicant is already in state '{applicant.application_status}'",
                    400, "INVALID_STATE")

            reason_id = data.get('reason_id')
            if reason_id:
                reason = request.env['hr.applicant.refuse.reason'].browse(int(reason_id))
                if not reason.exists():
                    return self._error_response(
                        f"Refuse reason {reason_id} not found", 400, "INVALID_REASON")
            else:
                reason = request.env['hr.applicant.refuse.reason'].search([], limit=1)
                if not reason:
                    return self._error_response(
                        "No refuse reason configured. Pass reason_id or seed hr.applicant.refuse.reason.",
                        400, "NO_REFUSE_REASON")

            applicant.write({
                'refuse_reason_id': reason.id,
                'active': False,
                'refuse_date': datetime.now(),
            })
            applicant.invalidate_recordset()

            return self._json_response(
                data={
                    'applicant': {
                        'id': applicant.id,
                        'partner_name': applicant.partner_name,
                        'application_status': applicant.application_status,
                        'active': applicant.active,
                        'refuse_reason_id': [reason.id, reason.name],
                        'refuse_date': str(applicant.refuse_date) if applicant.refuse_date else None,
                    }
                },
                message="Applicant refused",
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "REFUSE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "REFUSE_ERROR")
        except Exception as e:
            _logger.error("Error refusing applicant %s: %s", applicant_id, str(e))
            return self._error_response(
                "Error refusing applicant", 500, "REFUSE_ERROR")

    @http.route('/api/v2/hr/applicants/<int:applicant_id>/hire', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def applicant_hire(self, applicant_id, **_kwargs):
        """Hire an applicant — creates the linked hr.employee record.

        Wraps Odoo's ``create_employee_from_applicant`` so the hire flow goes
        through the same path as the kanban "Create Employee" button. Returns
        the new employee id and the applicant's updated application_status.
        """
        user, err = self._hr_recruitment_guard()
        if err:
            return err

        try:
            applicant = request.env['hr.applicant'].browse(applicant_id)
            if not applicant.exists():
                return self._error_response(
                    f"Applicant {applicant_id} not found", 404, "NOT_FOUND")

            if applicant.application_status in ('refused', 'hired'):
                return self._error_response(
                    f"Applicant is already in state '{applicant.application_status}'",
                    400, "INVALID_STATE")

            existing_employee = applicant.employee_id
            if existing_employee:
                return self._error_response(
                    f"Applicant already linked to employee {existing_employee.id}",
                    400, "ALREADY_HIRED")

            applicant.create_employee_from_applicant()

            # Move applicant to a hired stage so application_status flips to
            # 'hired'. We pick the first stage flagged ``hired_stage`` that
            # applies to this applicant's job (or has no job restriction).
            hired_stage = request.env['hr.recruitment.stage'].search([
                ('hired_stage', '=', True),
                '|', ('job_ids', '=', False), ('job_ids', '=', applicant.job_id.id),
            ], order='sequence asc', limit=1)
            if hired_stage:
                applicant.stage_id = hired_stage.id
            applicant.invalidate_recordset()
            employee = applicant.employee_id

            return self._json_response(
                data={
                    'applicant': {
                        'id': applicant.id,
                        'application_status': applicant.application_status,
                        'date_closed': str(applicant.date_closed) if applicant.date_closed else None,
                    },
                    'employee': {
                        'id': employee.id,
                        'name': employee.name,
                        'job_id': [employee.job_id.id, employee.job_id.name] if employee.job_id else None,
                        'department_id': [employee.department_id.id, employee.department_id.name] if employee.department_id else None,
                    } if employee else None,
                },
                message="Applicant hired and employee record created",
                status_code=201,
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "HIRE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "HIRE_ERROR")
        except Exception as e:
            _logger.error("Error hiring applicant %s: %s", applicant_id, str(e))
            return self._error_response(
                "Error hiring applicant", 500, "HIRE_ERROR")

    # ===== HR TIME OFF: LEAVE ACTIONS =====

    def _hr_leave_guard(self, operation='write'):
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return None, user

        sub_error = self._enforce_subscription()
        if sub_error:
            return None, sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return None, quota_error

        if 'hr.leave' not in request.env:
            return None, self._error_response(
                "hr_holidays module not installed", 404, "MODULE_NOT_FOUND")

        if not self._user_has_module_role(user, 'hr'):
            return None, self._error_response(
                "User does not have HR access", 403, "MODULE_ACCESS_DENIED")

        if not self._check_model_access('hr.leave', operation):
            return None, self._error_response(
                "Access denied for model 'hr.leave'", 403, "ACCESS_DENIED")

        return user, None

    def _serialize_leave(self, leave):
        return {
            'id': leave.id,
            'name': leave.name or '',
            'state': leave.state,
            'employee_id': [leave.employee_id.id, leave.employee_id.name] if leave.employee_id else None,
            'holiday_status_id': [leave.holiday_status_id.id, leave.holiday_status_id.name] if leave.holiday_status_id else None,
            'request_date_from': str(leave.request_date_from) if leave.request_date_from else None,
            'request_date_to': str(leave.request_date_to) if leave.request_date_to else None,
            'number_of_days': leave.number_of_days,
            'validation_type': leave.validation_type,
            'first_approver_id': [leave.first_approver_id.id, leave.first_approver_id.name] if leave.first_approver_id else None,
            'second_approver_id': [leave.second_approver_id.id, leave.second_approver_id.name] if leave.second_approver_id else None,
        }

    @http.route('/api/v2/hr/leaves/<int:leave_id>/approve', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def leave_approve(self, leave_id, **_kwargs):
        """Approve a leave request.

        For ``validation_type = 'both'``, calling this once moves
        confirm → validate1. The UI team asked for "one endpoint that does
        the full happy path", so we call ``action_approve`` a second time when
        the leave is still in validate1 and the same user has rights to
        complete it. If the user lacks the second-approval right, we leave the
        record at validate1 and return the current state.
        """
        user, err = self._hr_leave_guard()
        if err:
            return err

        try:
            leave = request.env['hr.leave'].browse(leave_id)
            if not leave.exists():
                return self._error_response(
                    f"Leave request {leave_id} not found", 404, "NOT_FOUND")

            if leave.state in ('validate', 'refuse', 'cancel'):
                return self._error_response(
                    f"Leave is in state '{leave.state}' and cannot be approved",
                    400, "INVALID_STATE")

            leave.action_approve()
            leave.invalidate_recordset()

            # Second pass for double-validation: only if the user has the
            # second-approver right (action_approve will raise UserError
            # otherwise, which we catch and treat as "first approval done").
            if leave.state == 'validate1':
                try:
                    leave.action_approve()
                    leave.invalidate_recordset()
                except UserError:
                    pass  # First approval recorded; second approver must act.

            return self._json_response(
                data={'leave': self._serialize_leave(leave)},
                message=f"Leave {leave.state}",
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "APPROVE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "APPROVE_ERROR")
        except Exception as e:
            _logger.error("Error approving leave %s: %s", leave_id, str(e))
            return self._error_response(
                "Error approving leave", 500, "APPROVE_ERROR")

    @http.route('/api/v2/hr/leaves/<int:leave_id>/refuse', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def leave_refuse(self, leave_id, **_kwargs):
        """Refuse a leave request — calls ``action_refuse`` (state → refuse).

        Optional body: ``{"reason": "..."}`` — if present, posted as a chatter
        message before the state transition.
        """
        user, err = self._hr_leave_guard()
        if err:
            return err

        try:
            try:
                data = request.httprequest.get_json(force=True, silent=True) or {}
            except Exception:
                data = {}

            leave = request.env['hr.leave'].browse(leave_id)
            if not leave.exists():
                return self._error_response(
                    f"Leave request {leave_id} not found", 404, "NOT_FOUND")

            if leave.state not in ('confirm', 'validate', 'validate1'):
                return self._error_response(
                    f"Leave is in state '{leave.state}' and cannot be refused",
                    400, "INVALID_STATE")

            reason = (data.get('reason') or '').strip()
            if reason:
                leave.message_post(body=f"Refusal reason: {reason}")

            leave.action_refuse()
            leave.invalidate_recordset()

            return self._json_response(
                data={'leave': self._serialize_leave(leave)},
                message="Leave refused",
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "REFUSE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "REFUSE_ERROR")
        except Exception as e:
            _logger.error("Error refusing leave %s: %s", leave_id, str(e))
            return self._error_response(
                "Error refusing leave", 500, "REFUSE_ERROR")

    # ===== ACCOUNTING: JOURNAL ENTRY POST =====

    @http.route('/api/v2/accounting/journal-entries/<int:move_id>/post', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def journal_entry_post(self, move_id, **_kwargs):
        """Post a draft journal entry (account.move) — calls ``action_post``.

        Use the generic ``POST /api/v2/create/account.move`` to create a draft
        miscellaneous entry with embedded ``line_ids`` (Odoo accepts the
        standard one2many tuple syntax: ``[(0, 0, {...}), ...]``). Then call
        this endpoint to transition draft → posted.
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

        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response(
                "User does not have Accounting access", 403, "MODULE_ACCESS_DENIED")

        if not self._check_model_access('account.move', 'write'):
            return self._error_response(
                "Access denied for model 'account.move'", 403, "ACCESS_DENIED")

        try:
            move = request.env['account.move'].browse(move_id)
            if not move.exists():
                return self._error_response(
                    f"Journal entry {move_id} not found", 404, "NOT_FOUND")

            if move.state == 'posted':
                return self._error_response(
                    "Journal entry already posted", 400, "INVALID_STATE")
            if move.state == 'cancel':
                return self._error_response(
                    "Cannot post a cancelled journal entry", 400, "INVALID_STATE")

            move.action_post()
            move.invalidate_recordset()

            return self._json_response(
                data={
                    'move': {
                        'id': move.id,
                        'name': move.name,
                        'state': move.state,
                        'date': str(move.date) if move.date else None,
                        'journal_id': [move.journal_id.id, move.journal_id.name] if move.journal_id else None,
                        'amount_total': move.amount_total,
                    }
                },
                message="Journal entry posted",
            )

        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "POST_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "POST_ERROR")
        except Exception as e:
            _logger.error("Error posting journal entry %s: %s", move_id, str(e))
            return self._error_response(
                "Error posting journal entry", 500, "POST_ERROR")

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
        except UserError as e:
            # A model's unlink guard (e.g. sale.order refuses to delete a sent
            # quotation / confirmed order) raises UserError. Surface it as a
            # clean, handled 409 with the real message instead of an opaque 500.
            return self._error_response(self._safe_exc_message(e), 409, "DELETE_BLOCKED")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "DELETE_ERROR")
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

    def _read_group_count(self, row, group_field):
        """Pull the row count from a read_group result row.

        Odoo 19 lazy read_group with a single groupby renames the
        count key from ``__count`` to ``<groupfield>_count`` (see
        odoo/orm/models.py:2812). Older code paths still emit
        ``__count``. Check both so behavior is stable across modes.
        """
        key_basename = group_field.split(':')[0] if group_field else None
        candidate = f'{key_basename}_count' if key_basename else None
        if candidate and candidate in row:
            return row.get(candidate, 0) or 0
        return row.get('__count', 0) or 0

    def _chart_series(self, rg_data, date_key, value_field=None):
        """Convert read_group results into {labels, series} for charting."""
        chart = {'labels': [], 'series': [{'label': 'Count', 'data': []}]}
        if value_field:
            chart['series'].append({'label': value_field, 'data': []})
        for row in rg_data:
            chart['labels'].append(row.get(date_key, ''))
            chart['series'][0]['data'].append(self._read_group_count(row, date_key))
            if value_field:
                chart['series'][1]['data'].append(row.get(value_field, 0) or 0)
        return chart

    def _breakdown(self, rg_data, group_field, value_field=None, model=None):
        """Convert read_group results into a list of breakdown buckets.

        When ``model`` is provided and ``group_field`` is a Selection
        field, labels are looked up via ``fields_get`` so they appear
        translated in the user's lang (e.g. ``draft`` → ``Brouillon``
        for a fr_FR user). Without ``model``, selection rows fall
        back to the raw technical key, which is never translated.
        """
        selection_labels = None
        if model is not None and group_field:
            try:
                field_info = model.fields_get([group_field]).get(group_field) or {}
                if field_info.get('type') == 'selection':
                    selection_labels = dict(field_info.get('selection') or [])
            except Exception:
                selection_labels = None

        buckets = []
        for row in rg_data:
            g = row.get(group_field)
            if isinstance(g, (list, tuple)):
                bucket_id, bucket_label = g[0], g[1]
            else:
                bucket_id = g
                if selection_labels is not None and g in selection_labels:
                    bucket_label = selection_labels[g]
                else:
                    bucket_label = str(g) if g else 'Undefined'
            buckets.append({
                'id': bucket_id,
                'label': bucket_label,
                'count': self._read_group_count(row, group_field),
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

    def _build_tenant_block(self, user):
        """Build the tenant context block for the AI agent.

        Authoritative facts that the LLM otherwise hallucinates: today's
        date in the tenant's timezone, currency, locale, company,
        fiscal year, and installed modules. Without this, Claude falls
        back to its training cutoff (e.g. infers "last month" as a date
        from 2024).
        """
        try:
            from zoneinfo import ZoneInfo
        except ImportError:  # pragma: no cover — Python <3.9
            ZoneInfo = None

        company = user.company_id or request.env.company
        tz_name = user.tz or (company.partner_id.tz if company and company.partner_id else None) or 'UTC'
        try:
            tz = ZoneInfo(tz_name) if ZoneInfo else None
        except Exception:
            tz = None
            tz_name = 'UTC'

        now_local = datetime.now(tz) if tz else datetime.utcnow()
        today_local = now_local.date()

        # Currency — falls back to USD if company has none.
        currency = company.currency_id if company else None
        currency_block = {
            'code': currency.name if currency else 'USD',
            'symbol': currency.symbol if currency else '$',
            'position': currency.position if currency else 'before',
            'decimal_places': currency.decimal_places if currency else 2,
        }

        # Country
        country_name = None
        if company and company.country_id:
            country_name = company.country_id.name
        country_code = None
        if company and company.country_id:
            country_code = company.country_id.code

        # Fiscal year — only present when account module is installed.
        fiscal_year = None
        if hasattr(company, 'fiscalyear_last_month') and hasattr(company, 'fiscalyear_last_day'):
            try:
                last_month = int(company.fiscalyear_last_month or 12)
                last_day = int(company.fiscalyear_last_day or 31)
                # Build current FY window: ends on last_month/last_day of current or next year.
                year = today_local.year
                try:
                    fy_end = today_local.replace(year=year, month=last_month, day=last_day)
                except ValueError:
                    fy_end = today_local.replace(year=year, month=last_month, day=28)
                if today_local > fy_end:
                    try:
                        fy_end = fy_end.replace(year=year + 1)
                    except ValueError:
                        pass
                # Start = day after previous FY end
                try:
                    fy_start = fy_end.replace(year=fy_end.year - 1) + timedelta(days=1)
                except ValueError:
                    fy_start = fy_end.replace(year=fy_end.year - 1, day=1) + timedelta(days=1)
                fiscal_year = {
                    'last_month': last_month,
                    'last_day': last_day,
                    'current_start': fy_start.isoformat(),
                    'current_end': fy_end.isoformat(),
                }
            except Exception:
                fiscal_year = None

        # Multi-company list (only ones the user can switch to)
        companies = []
        try:
            for c in user.company_ids:
                companies.append({'id': c.id, 'name': c.name})
        except Exception:
            pass

        # Installed modules (technical names) — sudo because ir.module.module is restricted.
        installed_modules = []
        try:
            modules = request.env['ir.module.module'].sudo().search([
                ('state', '=', 'installed'),
            ])
            installed_modules = [m.name for m in modules]
        except Exception:
            pass

        return {
            'today': today_local.isoformat(),
            'now': now_local.isoformat(),
            'timezone': tz_name,
            'locale': user.lang or 'en_US',
            'company': {
                'id': company.id if company else None,
                'name': company.name if company else None,
                'country': country_name,
                'country_code': country_code,
                'currency': currency_block,
            },
            'companies': companies,
            'fiscal_year': fiscal_year,
            'installed_modules': installed_modules,
        }

    @http.route('/api/v2/ai/context', type='http', auth='none', methods=['GET'], csrf=False)
    def ai_context(self):
        """Return a compact context blob for the AI agent in a single call.

        Combines: user info, permissions, module access, tenant facts
        (date, currency, locale, fiscal year), and a lightweight activity
        summary so the agent doesn't need 3-4 separate requests.
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
                    # Match /search/hr.employee: bypass Odoo's
                    # multi-company rule so the weekly count agrees with
                    # what the user sees in the list.
                    model_env = (
                        request.env[model_name].sudo()
                        if model_name == 'hr.employee'
                        else request.env[model_name]
                    )
                    count = model_env.search_count(domain)
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

            tenant_block = self._build_tenant_block(user)

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
                    'tenant': tenant_block,
                },
                message="AI context retrieved"
            )

        except Exception as e:
            _logger.error("Error building AI context: %s", str(e))
            return self._error_response("Error building AI context", 500, "AI_CONTEXT_ERROR")

    # ===== AI TAXONOMY =====

    @http.route('/api/v2/ai/taxonomy', type='http', auth='none', methods=['GET'], csrf=False)
    def ai_taxonomy(self):
        """Return tenant-specific entity names the LLM needs for grounding.

        CRM stages, journals, departments, product categories — all with
        the tenant's actual (often translated) names. Without this, the
        agent guesses English defaults like "New" / "Won" which may not
        match what the tenant set up.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            def _safe_search(model_name, fields, domain=None, order=None, limit=200):
                if model_name not in request.env:
                    return []
                try:
                    rs = request.env[model_name].search(domain or [], limit=limit, order=order or 'id')
                    out = []
                    for r in rs:
                        item = {}
                        for f in fields:
                            v = getattr(r, f, False)
                            # Many2one → [id, name]
                            if hasattr(v, '_name') and hasattr(v, 'id'):
                                item[f] = [v.id, v.display_name] if v else None
                            else:
                                item[f] = v
                        out.append(item)
                    return out
                except Exception:
                    return []

            # Gate each category by module access — stock Odoo lets every
            # internal user read crm.stage / account.journal / hr.department
            # via ORM ACLs, so without this gate a sales-only user would see
            # accounting journal names and HR department names.
            module_access = self._get_module_access()

            def _accessible(*keys):
                return any(
                    (module_access.get(k) or {}).get('accessible')
                    for k in keys
                )

            data = {
                'crm_stages': [],
                'crm_teams': [],
                'account_journals': [],
                'hr_departments': [],
                'product_categories': [],
                'product_uoms': [],
                'project_stages': [],
            }
            if _accessible('crm'):
                data['crm_stages'] = _safe_search(
                    'crm.stage',
                    ['id', 'name', 'sequence', 'is_won', 'team_id'],
                    order='sequence,id',
                )
                data['crm_teams'] = _safe_search(
                    'crm.team', ['id', 'name'], order='name',
                )
            if _accessible('accounting'):
                data['account_journals'] = _safe_search(
                    'account.journal',
                    ['id', 'name', 'code', 'type'],
                    order='sequence,id',
                )
            if _accessible('hr'):
                data['hr_departments'] = _safe_search(
                    'hr.department',
                    ['id', 'name', 'parent_id', 'manager_id'],
                    order='name',
                )
            if _accessible('products', 'inventory', 'sales', 'purchase'):
                data['product_categories'] = _safe_search(
                    'product.category',
                    ['id', 'name', 'parent_id'],
                    order='parent_path',
                )
                data['product_uoms'] = _safe_search(
                    'uom.uom', ['id', 'name'], order='name',
                )
            if _accessible('project'):
                data['project_stages'] = _safe_search(
                    'project.task.type',
                    ['id', 'name', 'sequence'],
                    order='sequence,id',
                )

            return self._json_response(
                data=data,
                message='Taxonomy retrieved',
            )
        except Exception as e:
            _logger.error("Error building AI taxonomy: %s", str(e))
            return self._error_response("Error building taxonomy", 500, "AI_TAXONOMY_ERROR")

    # ===== AI ME (user-specific employee context) =====

    @http.route('/api/v2/ai/me', type='http', auth='none', methods=['GET'], csrf=False)
    def ai_me(self):
        """Return user-specific HR context: employee record, manager chain, team.

        Lets the LLM answer "my deals", "my team", "who's my manager" without
        guessing the user→employee mapping.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            employee_data = None
            manager_chain = []
            if 'hr.employee' in request.env:
                try:
                    Employee = request.env['hr.employee'].sudo()
                    emp = Employee.search([('user_id', '=', user.id)], limit=1)
                    if emp:
                        employee_data = {
                            'id': emp.id,
                            'name': emp.name,
                            'job_title': emp.job_title,
                            'work_email': emp.work_email,
                            'department': [emp.department_id.id, emp.department_id.name] if emp.department_id else None,
                            'manager': [emp.parent_id.id, emp.parent_id.name] if emp.parent_id else None,
                        }
                        # Walk up the manager chain (max 5 to avoid cycles).
                        # Bound by the employee's own company — the search is
                        # sudo'd, so without this filter a manager in another
                        # company could appear in the chain.
                        emp_company_id = emp.company_id.id if emp.company_id else False
                        current = emp.parent_id
                        seen = set()
                        while current and current.id not in seen and len(manager_chain) < 5:
                            seen.add(current.id)
                            if emp_company_id and current.company_id and current.company_id.id != emp_company_id:
                                break
                            manager_chain.append({
                                'id': current.id,
                                'name': current.name,
                                'job_title': current.job_title,
                            })
                            current = current.parent_id
                except Exception:
                    pass

            crm_teams = []
            if 'crm.team' in request.env:
                try:
                    teams = request.env['crm.team'].search([
                        '|', ('user_id', '=', user.id), ('member_ids', 'in', [user.id]),
                    ])
                    crm_teams = [{'id': t.id, 'name': t.name} for t in teams]
                except Exception:
                    pass

            return self._json_response(
                data={
                    'user_id': user.id,
                    'partner_id': user.partner_id.id if user.partner_id else None,
                    'employee': employee_data,
                    'manager_chain': manager_chain,
                    'crm_teams': crm_teams,
                },
                message='User context retrieved',
            )
        except Exception as e:
            _logger.error("Error building AI me: %s", str(e))
            return self._error_response("Error building user context", 500, "AI_ME_ERROR")

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

                # Match /search/hr.employee: bypass Odoo's multi-company
                # rule on hr.employee so KPIs reflect every employee in
                # the tenant, not just those in the requester's company.
                model_obj = (
                    request.env[model_name].sudo()
                    if model_name == 'hr.employee'
                    else request.env[model_name]
                )
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
                # sudo() — matches the /search/hr.employee path so the
                # dashboard count doesn't disagree with the list.
                total_employees = request.env['hr.employee'].sudo().search_count(
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
                'breakdowns': {'by_stage': self._breakdown(stage_rg, 'stage_id', 'expected_revenue', model=Lead)},
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
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state', 'amount_total', model=SO)},
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
                'breakdowns': {'by_payment_state': self._breakdown(state_rg, 'payment_state', 'amount_total', model=Move)},
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
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state', model=Pick)},
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
                'breakdowns': {'by_state': self._breakdown(state_rg, 'state', 'amount_total', model=PO)},
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
            # sudo() — matches /search/hr.employee so HR analytics counts
            # don't drift below the visible list when extra companies exist.
            Emp = request.env['hr.employee'].sudo()

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
                'breakdowns': {'by_department': self._breakdown(dept_rg, 'department_id', model=Emp)},
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
                    'by_stage': self._breakdown(stage_rg, 'stage_id', model=Task),
                    'by_project': self._breakdown(project_rg, 'project_id', model=Task),
                },
                'chart': self._chart_series(chart_rg, 'create_date:month'),
                'alerts': alerts,
                'meta': self._analytics_meta(params),
            }, message="Project analytics retrieved")

        except Exception as e:
            _logger.error("Project analytics error: %s", str(e))
            return self._error_response("Error retrieving project analytics", 500, "ANALYTICS_ERROR")

    # ===== ACCOUNTING REPORTS =====

    _ACCT_ASSET_TYPES = (
        'asset_receivable', 'asset_cash', 'asset_current',
        'asset_non_current', 'asset_prepayments', 'asset_fixed',
    )
    _ACCT_LIABILITY_TYPES = (
        'liability_payable', 'liability_credit_card',
        'liability_current', 'liability_non_current',
    )
    _ACCT_EQUITY_TYPES = ('equity', 'equity_unaffected')
    _ACCT_INCOME_TYPES = ('income', 'income_other')
    _ACCT_EXPENSE_TYPES = ('expense', 'expense_depreciation', 'expense_direct_cost')

    _ACCT_TYPE_LABELS = {
        'asset_receivable': 'Receivable',
        'asset_cash': 'Bank and Cash',
        'asset_current': 'Current Assets',
        'asset_non_current': 'Non-current Assets',
        'asset_prepayments': 'Prepayments',
        'asset_fixed': 'Fixed Assets',
        'liability_payable': 'Payable',
        'liability_credit_card': 'Credit Card',
        'liability_current': 'Current Liabilities',
        'liability_non_current': 'Non-current Liabilities',
        'equity': 'Equity',
        'equity_unaffected': 'Current Year Earnings',
        'income': 'Income',
        'income_other': 'Other Income',
        'expense': 'Expenses',
        'expense_depreciation': 'Depreciation',
        'expense_direct_cost': 'Cost of Revenue',
    }

    def _account_balances(self, account_types, date_from=None, date_to=None, extra_domain=None):
        """Aggregate move-line balances per account.

        Returns list of dicts: {account_id, code, name, account_type, balance}.
        balance = sum(debit) - sum(credit) over posted move lines in window.
        """
        # In Odoo 17+, `account.account.code` is computed per-company from
        # `code_store` ({company_id: code}). Under auth='none' HTTP routes
        # `env.company` is sometimes blank, so we pin the company explicitly
        # to the user's main company and fall back to reading `code_store`
        # directly if `code` still resolves to a falsy value.
        company = request.env.user.company_id
        AccountAccount = request.env['account.account'].with_company(company)
        MoveLine = request.env['account.move.line'].with_company(company)

        accounts = AccountAccount.search([
            ('account_type', 'in', list(account_types)),
            ('active', '=', True),
        ] + (extra_domain or []))
        if not accounts:
            return []

        ml_domain = [
            ('parent_state', '=', 'posted'),
            ('account_id', 'in', accounts.ids),
        ]
        if date_from:
            ml_domain.append(('date', '>=', date_from.isoformat()))
        if date_to:
            ml_domain.append(('date', '<=', date_to.isoformat()))

        rg = MoveLine.read_group(ml_domain, ['balance:sum', 'debit:sum', 'credit:sum'], ['account_id'])
        by_id = {row['account_id'][0]: row for row in rg if row.get('account_id')}

        company_key = str(company.id)
        rows = []
        for acc in accounts:
            row = by_id.get(acc.id)
            balance = (row.get('balance', 0.0) if row else 0.0) or 0.0
            debit = (row.get('debit', 0.0) if row else 0.0) or 0.0
            credit = (row.get('credit', 0.0) if row else 0.0) or 0.0
            code = acc.code
            if not code and hasattr(acc, 'code_store'):
                code = (acc.code_store or {}).get(company_key) or ''
            rows.append({
                'account_id': acc.id,
                'code': code or '',
                'name': acc.name,
                'account_type': acc.account_type,
                'reconcile': bool(acc.reconcile),
                'debit': round(debit, 2),
                'credit': round(credit, 2),
                'balance': round(balance, 2),
            })
        return rows

    def _section_from_rows(self, rows, account_types, label, sign=1):
        """Build a balance-sheet/P&L section from filtered rows."""
        accounts = [r for r in rows if r['account_type'] in account_types and r['balance'] != 0]
        accounts.sort(key=lambda r: (r['code'] or ''))
        total = round(sum(r['balance'] for r in accounts) * sign, 2)
        return {
            'label': label,
            'total': total,
            'accounts': [{
                'id': a['account_id'],
                'code': a['code'],
                'name': a['name'],
                'type_label': self._ACCT_TYPE_LABELS.get(a['account_type'], a['account_type']),
                'amount': round(a['balance'] * sign, 2),
                'level': 1,
            } for a in accounts],
        }

    @http.route('/api/v2/analytics/accounting/journals/cards', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_journal_cards(self, **_kwargs):
        """Journal summary cards: per-journal counts, balances, to-validate, overdue."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.journal' not in request.env or not self._check_model_access('account.journal'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Journal = request.env['account.journal']
            Move = request.env['account.move']
            today_str = datetime.now().date().isoformat()

            journal_domain = []
            for q, f in [('company_id', 'company_id')]:
                v = request.httprequest.args.get(q)
                if v:
                    try:
                        journal_domain.append((f, '=', int(v)))
                    except (ValueError, TypeError):
                        pass

            journals = Journal.search(journal_domain)
            cards = []
            for j in journals:
                base_d = [('journal_id', '=', j.id)] + params['extra_domain']
                window_d = base_d + self._ddom('invoice_date', params['from_date'], params['to_date'])

                total_count, total_amount = self._agg(Move, window_d + [('state', '=', 'posted')], 'amount_total')
                to_validate = Move.search_count(base_d + [('state', '=', 'draft')])

                overdue = 0
                if j.type in ('sale', 'purchase'):
                    overdue = Move.search_count(base_d + [
                        ('state', '=', 'posted'),
                        ('payment_state', 'not in', ('paid', 'reversed', 'in_payment')),
                        ('invoice_date_due', '<', today_str),
                    ])

                bank_balance = None
                if j.type in ('bank', 'cash') and j.default_account_id:
                    rows = self._account_balances(
                        (j.default_account_id.account_type,),
                        date_to=params['to_date'],
                    )
                    bank_balance = round(sum(
                        r['balance'] for r in rows
                        if r['account_id'] == j.default_account_id.id
                    ), 2)

                cards.append({
                    'id': j.id,
                    'name': j.name,
                    'code': j.code,
                    'type': j.type,
                    'currency': j.currency_id.name if j.currency_id else (j.company_id.currency_id.name if j.company_id else None),
                    'balance': round(total_amount, 2),
                    'bank_balance': bank_balance,
                    'entries_count': total_count,
                    'to_validate': to_validate,
                    'overdue': overdue,
                })

            return self._json_response(data={
                'cards': cards,
                'meta': self._analytics_meta(params),
            }, message="Journal cards retrieved")

        except Exception as e:
            _logger.error("Journal cards error: %s", str(e))
            return self._error_response("Error retrieving journal cards", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/chart-of-accounts', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_chart_of_accounts(self, **_kwargs):
        """Chart of accounts with cumulative balances as of `to` date."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.account' not in request.env or not self._check_model_access('account.account'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            args = request.httprequest.args
            try:
                limit = max(1, min(int(args.get('limit', 200)), 500))
            except (ValueError, TypeError):
                limit = 200
            try:
                offset = max(0, int(args.get('offset', 0)))
            except (ValueError, TypeError):
                offset = 0

            type_filter = args.get('account_type')
            account_types = (
                self._ACCT_ASSET_TYPES + self._ACCT_LIABILITY_TYPES +
                self._ACCT_EQUITY_TYPES + self._ACCT_INCOME_TYPES + self._ACCT_EXPENSE_TYPES
            )
            if type_filter:
                account_types = tuple(t.strip() for t in type_filter.split(',') if t.strip())

            extra = []
            company_id = args.get('company_id')
            if company_id:
                try:
                    extra.append(('company_id', '=', int(company_id)))
                except (ValueError, TypeError):
                    pass

            rows = self._account_balances(account_types, date_to=params['to_date'], extra_domain=extra)
            rows.sort(key=lambda r: (r['code'] or ''))
            total_count = len(rows)
            page = rows[offset:offset + limit]

            return self._json_response(data={
                'accounts': [{
                    'id': r['account_id'],
                    'code': r['code'],
                    'name': r['name'],
                    'type': r['account_type'],
                    'type_label': self._ACCT_TYPE_LABELS.get(r['account_type'], r['account_type']),
                    'allow_reconciliation': r['reconcile'],
                    'debit': r['debit'],
                    'credit': r['credit'],
                    'balance': r['balance'],
                } for r in page],
                'pagination': {
                    'total': total_count,
                    'limit': limit,
                    'offset': offset,
                    'has_more': offset + limit < total_count,
                },
                'meta': self._analytics_meta(params),
            }, message="Chart of accounts retrieved")

        except Exception as e:
            _logger.error("Chart of accounts error: %s", str(e))
            return self._error_response("Error retrieving chart of accounts", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/balance-sheet', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_balance_sheet(self, **_kwargs):
        """Balance sheet: assets, liabilities, equity as of `to` date."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.account' not in request.env or not self._check_model_access('account.account'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            extra = []
            company_id = request.httprequest.args.get('company_id')
            if company_id:
                try:
                    extra.append(('company_id', '=', int(company_id)))
                except (ValueError, TypeError):
                    pass

            all_types = (
                self._ACCT_ASSET_TYPES + self._ACCT_LIABILITY_TYPES + self._ACCT_EQUITY_TYPES
            )
            rows = self._account_balances(all_types, date_to=params['to_date'], extra_domain=extra)

            # Liabilities & equity carry credit-normal balances; flip sign so amounts read positive.
            assets = self._section_from_rows(rows, self._ACCT_ASSET_TYPES, 'Assets', sign=1)
            liabilities = self._section_from_rows(rows, self._ACCT_LIABILITY_TYPES, 'Liabilities', sign=-1)
            equity = self._section_from_rows(rows, self._ACCT_EQUITY_TYPES, 'Equity', sign=-1)

            return self._json_response(data={
                'as_of': params['to_date'].isoformat(),
                'sections': [assets, liabilities, equity],
                'totals': {
                    'assets': assets['total'],
                    'liabilities': liabilities['total'],
                    'equity': equity['total'],
                    'liabilities_and_equity': round(liabilities['total'] + equity['total'], 2),
                },
                'meta': self._analytics_meta(params),
            }, message="Balance sheet retrieved")

        except Exception as e:
            _logger.error("Balance sheet error: %s", str(e))
            return self._error_response("Error retrieving balance sheet", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/profit-and-loss', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_profit_and_loss(self, **_kwargs):
        """Profit and loss: income vs expenses for the requested window."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.account' not in request.env or not self._check_model_access('account.account'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            extra = []
            company_id = request.httprequest.args.get('company_id')
            if company_id:
                try:
                    extra.append(('company_id', '=', int(company_id)))
                except (ValueError, TypeError):
                    pass

            all_types = self._ACCT_INCOME_TYPES + self._ACCT_EXPENSE_TYPES
            rows = self._account_balances(
                all_types,
                date_from=params['from_date'],
                date_to=params['to_date'],
                extra_domain=extra,
            )

            income = self._section_from_rows(rows, self._ACCT_INCOME_TYPES, 'Income', sign=-1)
            expenses = self._section_from_rows(rows, self._ACCT_EXPENSE_TYPES, 'Expenses', sign=1)
            net_profit = round(income['total'] - expenses['total'], 2)

            return self._json_response(data={
                'period': {
                    'from': params['from_date'].isoformat(),
                    'to': params['to_date'].isoformat(),
                },
                'sections': [income, expenses],
                'totals': {
                    'income': income['total'],
                    'expenses': expenses['total'],
                    'net_profit': net_profit,
                },
                'meta': self._analytics_meta(params),
            }, message="Profit and loss retrieved")

        except Exception as e:
            _logger.error("P&L error: %s", str(e))
            return self._error_response("Error retrieving profit and loss", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/cash-flow', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_cash_flow(self, **_kwargs):
        """Cash flow: opening, inflows, outflows, closing balance for cash & bank accounts."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.move.line' not in request.env or not self._check_model_access('account.move.line'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            extra = []
            company_id = request.httprequest.args.get('company_id')
            if company_id:
                try:
                    extra.append(('company_id', '=', int(company_id)))
                except (ValueError, TypeError):
                    pass

            opening_rows = self._account_balances(
                ('asset_cash',),
                date_to=params['from_date'] - timedelta(days=1),
                extra_domain=extra,
            )
            window_rows = self._account_balances(
                ('asset_cash',),
                date_from=params['from_date'],
                date_to=params['to_date'],
                extra_domain=extra,
            )
            closing_rows = self._account_balances(
                ('asset_cash',),
                date_to=params['to_date'],
                extra_domain=extra,
            )

            opening = round(sum(r['balance'] for r in opening_rows), 2)
            inflows = round(sum(r['debit'] for r in window_rows), 2)
            outflows = round(sum(r['credit'] for r in window_rows), 2)
            net_change = round(inflows - outflows, 2)
            closing = round(sum(r['balance'] for r in closing_rows), 2)

            by_account = []
            for r in window_rows:
                if r['debit'] == 0 and r['credit'] == 0:
                    continue
                by_account.append({
                    'id': r['account_id'],
                    'code': r['code'],
                    'name': r['name'],
                    'inflows': r['debit'],
                    'outflows': r['credit'],
                    'net': round(r['debit'] - r['credit'], 2),
                })
            by_account.sort(key=lambda r: (r['code'] or ''))

            return self._json_response(data={
                'period': {
                    'from': params['from_date'].isoformat(),
                    'to': params['to_date'].isoformat(),
                },
                'totals': {
                    'opening_balance': opening,
                    'inflows': inflows,
                    'outflows': outflows,
                    'net_change': net_change,
                    'closing_balance': closing,
                },
                'by_account': by_account,
                'meta': self._analytics_meta(params),
            }, message="Cash flow retrieved")

        except Exception as e:
            _logger.error("Cash flow error: %s", str(e))
            return self._error_response("Error retrieving cash flow", 500, "ANALYTICS_ERROR")

    # -----------------------------------------------------------------------
    # Tax reports — declaration, per-tax breakdown, fiscal position reconciliation.
    # All three read posted account.move.line rows in the analytics window;
    # sale-side amounts come back as -balance (credit-natural), purchase-side
    # as +balance (debit-natural), so credit notes naturally net the totals.
    # -----------------------------------------------------------------------
    def _tax_lines_in_period(self, params):
        """Aggregate posted tax lines for the period, grouped by tax_line_id.

        Returns list of dicts: {tax_id, balance, tax_base_amount}.
        """
        MoveLine = request.env['account.move.line']
        domain = [
            ('parent_state', '=', 'posted'),
            ('tax_line_id', '!=', False),
            ('date', '>=', params['from_date'].isoformat()),
            ('date', '<=', params['to_date'].isoformat()),
        ]
        rg = MoveLine.read_group(
            domain,
            ['balance:sum', 'tax_base_amount:sum'],
            ['tax_line_id'],
        )
        out = []
        for row in rg:
            tax_ref = row.get('tax_line_id')
            if not tax_ref:
                continue
            out.append({
                'tax_id': tax_ref[0],
                'balance': row.get('balance', 0.0) or 0.0,
                'tax_base_amount': row.get('tax_base_amount', 0.0) or 0.0,
            })
        return out

    @http.route('/api/v2/analytics/accounting/tax-declaration', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_tax_declaration(self, **_kwargs):
        """VAT/sales-tax declaration: collected, deductible, net to remit."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.move.line' not in request.env or not self._check_model_access('account.move.line'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            rows = self._tax_lines_in_period(params)

            Tax = request.env['account.tax']
            tax_ids = [r['tax_id'] for r in rows]
            taxes = {t.id: t for t in Tax.browse(tax_ids)} if tax_ids else {}

            collected = 0.0
            deductible = 0.0
            by_group = {}

            for r in rows:
                tax = taxes.get(r['tax_id'])
                if not tax:
                    continue
                # Sale-side tax lines are credit-natural; flip so positive = owed.
                if tax.type_tax_use == 'sale':
                    amount = -r['balance']
                    base = -r['tax_base_amount']
                    bucket = 'collected'
                elif tax.type_tax_use == 'purchase':
                    amount = r['balance']
                    base = r['tax_base_amount']
                    bucket = 'deductible'
                else:
                    continue

                if bucket == 'collected':
                    collected += amount
                else:
                    deductible += amount

                gid = tax.tax_group_id.id if tax.tax_group_id else 0
                gname = tax.tax_group_id.name if tax.tax_group_id else 'Other'
                if gid not in by_group:
                    by_group[gid] = {
                        'id': gid, 'label': gname,
                        'collected': 0.0, 'deductible': 0.0,
                        'collected_base': 0.0, 'deductible_base': 0.0,
                    }
                by_group[gid][bucket] += amount
                by_group[gid][bucket + '_base'] += base

            groups = [
                {
                    'id': g['id'],
                    'label': g['label'],
                    'collected': round(g['collected'], 2),
                    'deductible': round(g['deductible'], 2),
                    'collected_base': round(g['collected_base'], 2),
                    'deductible_base': round(g['deductible_base'], 2),
                    'net': round(g['collected'] - g['deductible'], 2),
                }
                for g in by_group.values()
            ]
            groups.sort(key=lambda r: -abs(r['net']))

            return self._json_response(data={
                'period': {
                    'from': params['from_date'].isoformat(),
                    'to': params['to_date'].isoformat(),
                },
                'totals': {
                    'collected': round(collected, 2),
                    'deductible': round(deductible, 2),
                    'net': round(collected - deductible, 2),
                },
                'by_group': groups,
                'meta': self._analytics_meta(params),
            }, message="Tax declaration retrieved")

        except Exception as e:
            _logger.error("Tax declaration error: %s", str(e))
            return self._error_response("Error retrieving tax declaration", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/tax-breakdown', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_tax_breakdown(self, **_kwargs):
        """Per-tax breakdown: each account.tax with its base + tax amount for the period."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.move.line' not in request.env or not self._check_model_access('account.move.line'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            rows = self._tax_lines_in_period(params)

            Tax = request.env['account.tax']
            tax_ids = [r['tax_id'] for r in rows]
            taxes = {t.id: t for t in Tax.browse(tax_ids)} if tax_ids else {}

            breakdown = []
            for r in rows:
                tax = taxes.get(r['tax_id'])
                if not tax or tax.type_tax_use not in ('sale', 'purchase'):
                    continue
                if tax.type_tax_use == 'sale':
                    amount = -r['balance']
                    base = -r['tax_base_amount']
                else:
                    amount = r['balance']
                    base = r['tax_base_amount']
                breakdown.append({
                    'id': tax.id,
                    'name': tax.name,
                    'rate': float(tax.amount),
                    'type': tax.type_tax_use,
                    'group': tax.tax_group_id.name if tax.tax_group_id else None,
                    'base': round(base, 2),
                    'amount': round(amount, 2),
                })
            breakdown.sort(key=lambda r: (r['type'], -abs(r['amount'])))

            return self._json_response(data={
                'period': {
                    'from': params['from_date'].isoformat(),
                    'to': params['to_date'].isoformat(),
                },
                'by_tax': breakdown,
                'meta': self._analytics_meta(params),
            }, message="Tax breakdown retrieved")

        except Exception as e:
            _logger.error("Tax breakdown error: %s", str(e))
            return self._error_response("Error retrieving tax breakdown", 500, "ANALYTICS_ERROR")

    @http.route('/api/v2/analytics/accounting/fiscal-position-reconciliation', type='http',
                auth='none', methods=['GET'], csrf=False)
    def accounting_fiscal_position_reconciliation(self, **_kwargs):
        """Invoices/bills grouped by fiscal_position_id over the analytics window."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error

        try:
            if 'account.move' not in request.env or not self._check_model_access('account.move'):
                return self._error_response("Accounting not accessible", 403, "ACCESS_DENIED")
            if not self._user_has_module_role(user, 'accounting'):
                return self._error_response("Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")

            params = self._parse_analytics_params()
            Move = request.env['account.move']
            move_types = ['out_invoice', 'in_invoice', 'out_refund', 'in_refund']

            base_domain = [
                ('state', '=', 'posted'),
                ('date', '>=', params['from_date'].isoformat()),
                ('date', '<=', params['to_date'].isoformat()),
                ('move_type', 'in', move_types),
            ]

            mapped = Move.read_group(
                base_domain + [('fiscal_position_id', '!=', False)],
                ['amount_total:sum', 'amount_untaxed:sum', 'amount_tax:sum'],
                ['fiscal_position_id'],
            )
            by_position = []
            for row in mapped:
                fp_ref = row.get('fiscal_position_id')
                if not fp_ref:
                    continue
                by_position.append({
                    'id': fp_ref[0],
                    'name': fp_ref[1],
                    'invoices_count': row.get('__count', 0),
                    'amount_total': round(row.get('amount_total', 0) or 0, 2),
                    'amount_untaxed': round(row.get('amount_untaxed', 0) or 0, 2),
                    'amount_tax': round(row.get('amount_tax', 0) or 0, 2),
                })
            by_position.sort(key=lambda r: -abs(r['amount_total']))

            unmapped_count = Move.search_count(base_domain + [('fiscal_position_id', '=', False)])

            return self._json_response(data={
                'period': {
                    'from': params['from_date'].isoformat(),
                    'to': params['to_date'].isoformat(),
                },
                'by_position': by_position,
                'unmapped_count': unmapped_count,
                'meta': self._analytics_meta(params),
            }, message="Fiscal position reconciliation retrieved")

        except Exception as e:
            _logger.error("Fiscal position reconciliation error: %s", str(e))
            return self._error_response("Error retrieving fiscal position reconciliation", 500, "ANALYTICS_ERROR")

    # ===== LETTRAGE (RECONCILIATION) =====
    #
    # Saari-style account-by-account reconciliation. The SPA reads unmatched
    # / matched lines on a chosen account (411XXX / 401XXX typically),
    # selects a balanced subset, and POSTs to /reconcile. Letter codes
    # (A, B, ..., AA, AB, ...) are assigned by the OHADA overlay on
    # `account.full.reconcile.create`.

    def _lettrage_check_access(self, user, write=False):
        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response(
                "Access denied: requires Accounting role", 403, "ROLE_ACCESS_DENIED")
        op = 'write' if write else 'read'
        if not self._check_model_access('account.move.line', op):
            return self._error_response(
                "Accounting not accessible", 403, "ACCESS_DENIED")
        return None

    def _lettrage_account(self, account_id):
        Account = request.env['account.account']
        try:
            acc = Account.browse(int(account_id))
        except (TypeError, ValueError):
            return None
        if not acc.exists():
            return None
        return acc

    def _lettrage_line_payload(self, line):
        full = line.full_reconcile_id
        return {
            'id': line.id,
            'date': str(line.date) if line.date else None,
            'move_id': line.move_id.id,
            'move_name': line.move_id.name or '',
            'ref': line.ref or '',
            'label': line.name or '',
            'partner_id': line.partner_id.id if line.partner_id else None,
            'partner_name': line.partner_id.name if line.partner_id else '',
            'debit': round(line.debit or 0.0, 2),
            'credit': round(line.credit or 0.0, 2),
            'amount_residual': round(line.amount_residual or 0.0, 2),
            'currency': line.currency_id.name if line.currency_id else (
                line.company_currency_id.name if line.company_currency_id else None),
            'matching_number': line.matching_number or None,
            'full_reconcile_id': full.id if full else None,
            # `lettrage_code` is provided by l10n_toomde_ohada_overlay; falls
            # back to None on non-OHADA tenants. getattr keeps the endpoint
            # usable without the overlay installed.
            'lettrage_code': getattr(full, 'lettrage_code', None) if full else None,
            'reconciled': bool(line.reconciled),
        }

    @http.route('/api/v2/accounting/lettrage/lines', type='http',
                auth='none', methods=['GET'], csrf=False)
    def lettrage_lines(self, **_kwargs):
        """Move lines on an account, filterable by state/partner/date range.

        Query params:
          - account_id (required): account.account id
          - partner_id (optional): filter to one partner
          - state: 'unmatched' (default) | 'matched' | 'all'
          - date_from / date_to: ISO dates (account.move.line.date)
          - limit (default 200, max 1000), offset
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        args = request.httprequest.args
        account = self._lettrage_account(args.get('account_id'))
        if not account:
            return self._error_response("account_id required and must exist", 400, "INVALID_PARAMS")

        domain = [('account_id', '=', account.id), ('parent_state', '=', 'posted')]

        partner_id = args.get('partner_id')
        if partner_id:
            try:
                domain.append(('partner_id', '=', int(partner_id)))
            except (TypeError, ValueError):
                return self._error_response("partner_id must be integer", 400, "INVALID_PARAMS")

        state = (args.get('state') or 'unmatched').lower()
        if state == 'unmatched':
            domain.append(('reconciled', '=', False))
        elif state == 'matched':
            domain.append(('full_reconcile_id', '!=', False))
        elif state != 'all':
            return self._error_response("state must be 'unmatched', 'matched', or 'all'",
                                        400, "INVALID_PARAMS")

        date_from = args.get('date_from')
        date_to = args.get('date_to')
        if date_from:
            domain.append(('date', '>=', date_from))
        if date_to:
            domain.append(('date', '<=', date_to))

        try:
            limit = max(1, min(int(args.get('limit', 200)), 1000))
        except (ValueError, TypeError):
            limit = 200
        try:
            offset = max(0, int(args.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0

        try:
            Line = request.env['account.move.line']
            total = Line.search_count(domain)
            lines = Line.search(domain, order='date, id', limit=limit, offset=offset)
            payload = [self._lettrage_line_payload(l) for l in lines]
            debit_total = sum(l['debit'] for l in payload)
            credit_total = sum(l['credit'] for l in payload)
            return self._json_response(data={
                'account': {
                    'id': account.id,
                    'code': account.code,
                    'name': account.name,
                    'allow_reconciliation': account.reconcile,
                },
                'lines': payload,
                'totals': {
                    'debit': round(debit_total, 2),
                    'credit': round(credit_total, 2),
                    'balance': round(debit_total - credit_total, 2),
                },
                'pagination': {
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                    'has_more': offset + limit < total,
                },
            }, message="Lettrage lines retrieved")
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Lettrage lines error: %s", str(e))
            return self._error_response("Error retrieving lettrage lines", 500, "LETTRAGE_ERROR")

    @http.route('/api/v2/accounting/lettrage/groups', type='http',
                auth='none', methods=['GET'], csrf=False)
    def lettrage_groups(self, **_kwargs):
        """Already-lettered reconcile groups on an account."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        args = request.httprequest.args
        account = self._lettrage_account(args.get('account_id'))
        if not account:
            return self._error_response("account_id required and must exist", 400, "INVALID_PARAMS")

        Full = request.env['account.full.reconcile']
        if 'lettrage_account_id' in Full._fields:
            domain = [('lettrage_account_id', '=', account.id)]
        else:
            # No overlay: filter via the reconciled lines' account.
            domain = [('reconciled_line_ids.account_id', '=', account.id)]
        partner_id = args.get('partner_id')
        if partner_id:
            try:
                domain.append(('reconciled_line_ids.partner_id', '=', int(partner_id)))
            except (TypeError, ValueError):
                return self._error_response("partner_id must be integer", 400, "INVALID_PARAMS")

        try:
            limit = max(1, min(int(args.get('limit', 100)), 500))
        except (ValueError, TypeError):
            limit = 100
        try:
            offset = max(0, int(args.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0

        try:
            total = Full.search_count(domain)
            fulls = Full.search(domain, order='id desc', limit=limit, offset=offset)
            groups = []
            for full in fulls:
                lines = full.reconciled_line_ids
                groups.append({
                    'id': full.id,
                    'lettrage_code': getattr(full, 'lettrage_code', None),
                    'line_count': len(lines),
                    'lines': [self._lettrage_line_payload(l) for l in lines],
                    'debit_total': round(sum(l.debit or 0 for l in lines), 2),
                    'credit_total': round(sum(l.credit or 0 for l in lines), 2),
                })
            return self._json_response(data={
                'account': {
                    'id': account.id,
                    'code': account.code,
                    'name': account.name,
                },
                'groups': groups,
                'pagination': {
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                    'has_more': offset + limit < total,
                },
            }, message="Lettrage groups retrieved")
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Lettrage groups error: %s", str(e))
            return self._error_response("Error retrieving lettrage groups", 500, "LETTRAGE_ERROR")

    @http.route('/api/v2/accounting/lettrage/reconcile', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def lettrage_reconcile(self, **_kwargs):
        """Reconcile a set of move lines. Body: {"line_ids": [int, ...]}.

        Lines must share the same account and that account must allow
        reconciliation. Totals (sum debit == sum credit) are recommended
        but not enforced — Odoo creates a partial reconcile when amounts
        differ, and the residual stays on the books.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user, write=True)
        if acc_error:
            return acc_error

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        content_type = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            return self._error_response("Content-Type must be application/json",
                                        400, "INVALID_CONTENT_TYPE")
        try:
            data = request.httprequest.get_json(force=True) or {}
        except Exception:
            return self._error_response("Invalid JSON", 400, "INVALID_JSON")

        raw_ids = data.get('line_ids') or []
        if not isinstance(raw_ids, list) or len(raw_ids) < 2:
            return self._error_response("line_ids must be a list of at least 2 ids",
                                        400, "INVALID_PARAMS")
        try:
            ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            return self._error_response("line_ids must be integers", 400, "INVALID_PARAMS")

        try:
            Line = request.env['account.move.line']
            lines = Line.browse(ids).exists()
            if len(lines) != len(ids):
                return self._error_response("Some line ids do not exist",
                                            404, "NOT_FOUND")

            accounts = lines.mapped('account_id')
            if len(accounts) != 1:
                return self._error_response("All lines must share the same account",
                                            400, "MIXED_ACCOUNTS")
            account = accounts
            if not account.reconcile:
                return self._error_response(
                    f"Account {account.code} does not allow reconciliation",
                    400, "ACCOUNT_NOT_RECONCILABLE")

            already = lines.filtered(lambda l: l.reconciled)
            if already:
                return self._error_response(
                    "One or more lines are already fully reconciled",
                    400, "ALREADY_RECONCILED")

            lines.reconcile()
            full = lines.mapped('full_reconcile_id')[:1]
            response = self._json_response(data={
                'full_reconcile_id': full.id if full else None,
                'lettrage_code': getattr(full, 'lettrage_code', None) if full else None,
                'partial': not bool(full),
                'line_ids': lines.ids,
            }, message="Lines reconciled")
            return self._idempotency_store(idem, response)
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "RECONCILE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "RECONCILE_ERROR")
        except Exception as e:
            _logger.error("Lettrage reconcile error: %s", str(e))
            return self._error_response("Error reconciling lines", 500, "LETTRAGE_ERROR")

    @http.route('/api/v2/accounting/lettrage/unreconcile', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def lettrage_unreconcile(self, **_kwargs):
        """Délettrage. Body: either {"full_reconcile_id": int} or {"line_ids": [int,...]}."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user, write=True)
        if acc_error:
            return acc_error

        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        content_type = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            return self._error_response("Content-Type must be application/json",
                                        400, "INVALID_CONTENT_TYPE")
        try:
            data = request.httprequest.get_json(force=True) or {}
        except Exception:
            return self._error_response("Invalid JSON", 400, "INVALID_JSON")

        try:
            Line = request.env['account.move.line']
            target_lines = Line.browse()
            if data.get('full_reconcile_id'):
                Full = request.env['account.full.reconcile']
                full = Full.browse(int(data['full_reconcile_id']))
                if not full.exists():
                    return self._error_response("full_reconcile not found", 404, "NOT_FOUND")
                target_lines = full.reconciled_line_ids
            elif data.get('line_ids'):
                try:
                    ids = [int(i) for i in data['line_ids']]
                except (TypeError, ValueError):
                    return self._error_response("line_ids must be integers",
                                                400, "INVALID_PARAMS")
                target_lines = Line.browse(ids).exists()
            else:
                return self._error_response(
                    "Provide either full_reconcile_id or line_ids",
                    400, "INVALID_PARAMS")

            if not target_lines:
                return self._error_response("Nothing to unreconcile", 400, "INVALID_PARAMS")

            target_lines.remove_move_reconcile()
            response = self._json_response(data={
                'unreconciled_line_ids': target_lines.ids,
            }, message="Lines unreconciled")
            return self._idempotency_store(idem, response)
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "UNRECONCILE_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "UNRECONCILE_ERROR")
        except Exception as e:
            _logger.error("Lettrage unreconcile error: %s", str(e))
            return self._error_response("Error unreconciling", 500, "LETTRAGE_ERROR")

    @http.route('/api/v2/accounting/lettrage/suggest', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def lettrage_suggest(self, **_kwargs):
        """Auto-suggest balanced groups of unmatched lines.

        Body: {"account_id": int, "partner_id": int?, "amount_tolerance": float?}.

        Algorithm v1: per partner, find debit/credit pairs whose absolute
        amounts match within tolerance. Catches >80% of payment-against-
        invoice matches on a clean ledger. Heuristics can grow later.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        content_type = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            return self._error_response("Content-Type must be application/json",
                                        400, "INVALID_CONTENT_TYPE")
        try:
            data = request.httprequest.get_json(force=True) or {}
        except Exception:
            return self._error_response("Invalid JSON", 400, "INVALID_JSON")

        account = self._lettrage_account(data.get('account_id'))
        if not account:
            return self._error_response("account_id required and must exist",
                                        400, "INVALID_PARAMS")
        try:
            tolerance = float(data.get('amount_tolerance', 0.0))
        except (TypeError, ValueError):
            return self._error_response("amount_tolerance must be a number",
                                        400, "INVALID_PARAMS")

        domain = [
            ('account_id', '=', account.id),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
        ]
        partner_id = data.get('partner_id')
        if partner_id:
            try:
                domain.append(('partner_id', '=', int(partner_id)))
            except (TypeError, ValueError):
                return self._error_response("partner_id must be integer",
                                            400, "INVALID_PARAMS")

        try:
            Line = request.env['account.move.line']
            lines = Line.search(domain, order='partner_id, date, id', limit=2000)
            # Bucket by partner; within each, pair off debits and credits
            # that match within tolerance. Greedy O(n²) per partner is fine
            # for typical lettrage workflows (< few hundred open lines per
            # partner). We can switch to amount-indexed lookups later.
            by_partner = {}
            for l in lines:
                pid = l.partner_id.id if l.partner_id else 0
                by_partner.setdefault(pid, []).append(l)

            suggestions = []
            for pid, plines in by_partner.items():
                debits = [l for l in plines if (l.debit or 0) > 0]
                credits = [l for l in plines if (l.credit or 0) > 0]
                used = set()
                for d in debits:
                    if d.id in used:
                        continue
                    for c in credits:
                        if c.id in used:
                            continue
                        if abs((d.debit or 0) - (c.credit or 0)) <= max(tolerance, 0.005):
                            suggestions.append({
                                'partner_id': pid or None,
                                'partner_name': d.partner_id.name if d.partner_id else '',
                                'amount': round(d.debit or 0, 2),
                                'line_ids': [d.id, c.id],
                                'lines': [
                                    self._lettrage_line_payload(d),
                                    self._lettrage_line_payload(c),
                                ],
                            })
                            used.add(d.id)
                            used.add(c.id)
                            break

            return self._json_response(data={
                'account': {
                    'id': account.id,
                    'code': account.code,
                    'name': account.name,
                },
                'suggestions': suggestions,
                'total': len(suggestions),
            }, message="Suggestions computed")
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except Exception as e:
            _logger.error("Lettrage suggest error: %s", str(e))
            return self._error_response("Error computing suggestions", 500, "LETTRAGE_ERROR")

    # ===== BANK RECONCILIATION =====
    #
    # Statement import (CSV / OFX) + per-line listing + matching against
    # existing payments / invoices + statement close. Targets the common
    # WA workflow: comptable uploads the monthly bank export, system
    # proposes matches, comptable confirms.

    def _bank_journal(self, journal_id):
        Journal = request.env['account.journal']
        try:
            j = Journal.browse(int(journal_id))
        except (TypeError, ValueError):
            return None
        if not j.exists() or j.type not in ('bank', 'cash'):
            return None
        return j

    def _statement_line_payload(self, sl):
        suspense_aml = sl.move_id.line_ids.filtered(
            lambda l: not l.account_id.account_type or l.account_id.id != (
                sl.journal_id.default_account_id.id))[:1]
        return {
            'id': sl.id,
            'date': str(sl.date) if sl.date else None,
            'label': sl.payment_ref or '',
            'partner_id': sl.partner_id.id if sl.partner_id else None,
            'partner_name': sl.partner_id.name if sl.partner_id else (sl.partner_name or ''),
            'amount': round(sl.amount or 0.0, 2),
            'account_number': sl.account_number or '',
            'is_reconciled': bool(sl.is_reconciled),
            'suspense_aml_id': suspense_aml.id if suspense_aml else None,
        }

    def _statement_payload(self, st, include_lines=True):
        data = {
            'id': st.id,
            'name': st.name or '',
            'reference': st.reference or '',
            'date': str(st.date) if st.date else None,
            'journal_id': st.journal_id.id,
            'journal_name': st.journal_id.name,
            'balance_start': round(st.balance_start or 0.0, 2),
            'balance_end': round(st.balance_end or 0.0, 2),
            'balance_end_real': round(st.balance_end_real or 0.0, 2),
            'line_count': len(st.line_ids),
            'unreconciled_count': sum(1 for l in st.line_ids if not l.is_reconciled),
        }
        if include_lines:
            data['lines'] = [self._statement_line_payload(l) for l in st.line_ids]
        return data

    @staticmethod
    def _parse_csv_statement(file_bytes, decimal_sep='auto'):
        """Best-effort CSV bank statement parser.

        Required headers (case-insensitive, any order): `date`, `amount`,
        and at least one of `label`/`description`/`memo`. Optional:
        `partner`, `ref`/`reference`.

        Decimal separator auto-detected unless overridden — French exports
        use `,` and English exports use `.`.
        """
        import csv
        import io
        text = file_bytes.decode('utf-8-sig', errors='replace')
        # Sniff dialect
        try:
            dialect = csv.Sniffer().sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = []
        for raw in reader:
            row = {(k or '').strip().lower(): (v or '').strip() for k, v in raw.items()}
            date_str = row.get('date') or row.get('transaction date') or row.get('value date') or ''
            label = (row.get('label') or row.get('description') or row.get('memo') or
                     row.get('libelle') or row.get('libellé') or '')
            amount_str = row.get('amount') or row.get('montant') or ''
            if not amount_str:
                debit_str = row.get('debit') or row.get('débit') or '0'
                credit_str = row.get('credit') or row.get('crédit') or '0'
                amount_str = credit_str if credit_str and credit_str != '0' else f'-{debit_str}'
            # Decimal sep auto-detection
            sep = decimal_sep
            if sep == 'auto':
                sep = ',' if (',' in amount_str and '.' not in amount_str) else '.'
            normalized = amount_str.replace(' ', '').replace(' ', '')
            if sep == ',':
                normalized = normalized.replace('.', '').replace(',', '.')
            try:
                amount = float(normalized) if normalized else 0.0
            except ValueError:
                continue  # skip malformed row
            partner_name = row.get('partner') or row.get('tiers') or ''
            ref = row.get('ref') or row.get('reference') or row.get('référence') or ''
            rows.append({
                'date': date_str,
                'label': label,
                'amount': amount,
                'partner_name': partner_name,
                'ref': ref,
            })
        return rows

    @staticmethod
    def _parse_ofx_statement(file_bytes):
        """Best-effort OFX 1.x parser. Tolerant of SGML quirks.

        Extracts every <STMTTRN>...</STMTTRN> block and pulls DTPOSTED,
        TRNAMT, NAME, MEMO, FITID. Returns same shape as CSV parser.
        """
        import re
        text = file_bytes.decode('utf-8', errors='replace')

        def _tag(block, name):
            m = re.search(rf'<{name}>([^<\r\n]*)', block, re.IGNORECASE)
            return (m.group(1) if m else '').strip()

        rows = []
        for block in re.findall(r'<STMTTRN>(.*?)</STMTTRN>',
                                text, re.DOTALL | re.IGNORECASE):
            dt = _tag(block, 'DTPOSTED')
            iso_date = ''
            if len(dt) >= 8 and dt[:8].isdigit():
                iso_date = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            try:
                amount = float(_tag(block, 'TRNAMT') or '0')
            except ValueError:
                amount = 0.0
            rows.append({
                'date': iso_date,
                'label': _tag(block, 'NAME') or _tag(block, 'MEMO'),
                'amount': amount,
                'partner_name': _tag(block, 'NAME'),
                'ref': _tag(block, 'FITID'),
            })
        return rows

    @http.route('/api/v2/accounting/bank-statement/import', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def bank_statement_import(self, **_kwargs):
        """Upload a bank statement (CSV or OFX) on a bank journal.

        Multipart form fields:
          - file (required): the statement file
          - journal_id (required): account.journal id (must be type bank/cash)
          - statement_date (optional): ISO date — overrides file's earliest

        Returns the created `account.bank.statement` summary.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user, write=True)
        if acc_error:
            return acc_error

        files = request.httprequest.files
        form = request.httprequest.form
        upload = files.get('file')
        journal = self._bank_journal(form.get('journal_id'))
        if not upload or not upload.filename:
            return self._error_response("file is required", 400, "INVALID_PARAMS")
        if not journal:
            return self._error_response("journal_id required and must be bank/cash type",
                                        400, "INVALID_PARAMS")

        raw = upload.read()
        fname = upload.filename.lower()
        try:
            if fname.endswith('.ofx') or raw[:1024].lstrip().startswith(b'OFXHEADER'):
                rows = self._parse_ofx_statement(raw)
            else:
                rows = self._parse_csv_statement(raw)
        except Exception as e:
            _logger.exception("Bank statement parse failed")
            return self._error_response(
                f"Could not parse file: {self._safe_exc_message(e)}",
                400, "PARSE_ERROR")

        if not rows:
            return self._error_response("No transactions parsed from file",
                                        400, "EMPTY_FILE")

        line_vals = []
        for r in rows:
            line_vals.append((0, 0, {
                'date': r['date'] or False,
                'payment_ref': r['label'] or r['ref'] or 'Bank line',
                'amount': r['amount'],
                'partner_name': r['partner_name'] or False,
                'journal_id': journal.id,
            }))
        try:
            statement = request.env['account.bank.statement'].create({
                'journal_id': journal.id,
                'name': form.get('statement_name') or upload.filename,
                'reference': upload.filename,
                'date': form.get('statement_date') or False,
                'line_ids': line_vals,
            })
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "IMPORT_ERROR")
        except Exception as e:
            _logger.error("Bank statement import error: %s", str(e))
            return self._error_response("Error importing statement", 500, "IMPORT_ERROR")

        return self._json_response(
            data={'statement': self._statement_payload(statement)},
            message="Bank statement imported",
        )

    @http.route('/api/v2/accounting/bank-statement', type='http',
                auth='none', methods=['GET'], csrf=False)
    def bank_statement_list(self, **_kwargs):
        """List bank statements, optionally filtered by journal."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        args = request.httprequest.args
        domain = []
        if args.get('journal_id'):
            try:
                domain.append(('journal_id', '=', int(args['journal_id'])))
            except (TypeError, ValueError):
                return self._error_response("journal_id must be integer",
                                            400, "INVALID_PARAMS")
        try:
            limit = max(1, min(int(args.get('limit', 50)), 200))
        except (ValueError, TypeError):
            limit = 50
        try:
            offset = max(0, int(args.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0

        Statement = request.env['account.bank.statement']
        total = Statement.search_count(domain)
        statements = Statement.search(domain, order='date desc, id desc',
                                      limit=limit, offset=offset)
        return self._json_response(data={
            'statements': [self._statement_payload(st, include_lines=False)
                           for st in statements],
            'pagination': {
                'total': total, 'limit': limit, 'offset': offset,
                'has_more': offset + limit < total,
            },
        }, message="Statements retrieved")

    @http.route('/api/v2/accounting/bank-statement/<int:statement_id>',
                type='http', auth='none', methods=['GET'], csrf=False)
    def bank_statement_detail(self, statement_id, **_kwargs):
        """Statement detail with all lines + reconciliation status."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        st = request.env['account.bank.statement'].browse(statement_id)
        if not st.exists():
            return self._error_response("Statement not found", 404, "NOT_FOUND")
        return self._json_response(data={'statement': self._statement_payload(st)},
                                   message="Statement retrieved")

    @http.route('/api/v2/accounting/bank-statement/lines/<int:line_id>/suggestions',
                type='http', auth='none', methods=['GET'], csrf=False)
    def bank_statement_line_suggestions(self, line_id, **_kwargs):
        """Suggest move lines to reconcile against a bank statement line.

        Heuristic: open AMLs on receivable / payable accounts whose amount
        matches the bank line's amount (within 1 unit of the journal
        currency) and partner matches when known. Catches the common case
        of "customer paid invoice X by bank transfer".
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user)
        if acc_error:
            return acc_error

        st_line = request.env['account.bank.statement.line'].browse(line_id)
        if not st_line.exists():
            return self._error_response("Line not found", 404, "NOT_FOUND")

        amount = abs(st_line.amount or 0.0)
        is_inflow = (st_line.amount or 0.0) > 0

        # For inflows (customer paid us), suggest open receivable AMLs.
        # For outflows (we paid supplier), suggest open payable AMLs.
        target_types = ['asset_receivable'] if is_inflow else ['liability_payable']
        domain = [
            ('account_id.account_type', 'in', target_types),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('amount_residual', '!=', 0.0),
        ]
        if st_line.partner_id:
            domain.append(('partner_id', '=', st_line.partner_id.id))

        Line = request.env['account.move.line']
        candidates = Line.search(domain, order='date desc, id desc', limit=200)

        suggestions = []
        tolerance = 1.0  # 1 unit of currency
        for c in candidates:
            residual = abs(c.amount_residual or 0.0)
            if abs(residual - amount) <= tolerance:
                suggestions.append({
                    'move_line_id': c.id,
                    'move_id': c.move_id.id,
                    'move_name': c.move_id.name or '',
                    'partner_id': c.partner_id.id if c.partner_id else None,
                    'partner_name': c.partner_id.name if c.partner_id else '',
                    'date': str(c.date) if c.date else None,
                    'amount_residual': round(c.amount_residual or 0.0, 2),
                    'currency': c.currency_id.name if c.currency_id else None,
                    'confidence': 'high' if st_line.partner_id else 'medium',
                })
        return self._json_response(data={
            'bank_line': self._statement_line_payload(st_line),
            'suggestions': suggestions,
        }, message="Suggestions computed")

    @http.route('/api/v2/accounting/bank-statement/lines/<int:line_id>/match',
                type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def bank_statement_line_match(self, line_id, **_kwargs):
        """Match a bank statement line against one or more open move lines.

        Body: {"move_line_ids": [int, ...]}.

        Mechanism: rewrite the bank line's suspense-side AML onto the
        target account (the receivable/payable of the matched lines),
        then reconcile. Single-account constraint applies — all targets
        must share an account.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user, write=True)
        if acc_error:
            return acc_error

        content_type = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            return self._error_response("Content-Type must be application/json",
                                        400, "INVALID_CONTENT_TYPE")
        try:
            data = request.httprequest.get_json(force=True) or {}
        except Exception:
            return self._error_response("Invalid JSON", 400, "INVALID_JSON")

        st_line = request.env['account.bank.statement.line'].browse(line_id)
        if not st_line.exists():
            return self._error_response("Bank line not found", 404, "NOT_FOUND")

        raw_ids = data.get('move_line_ids') or []
        if not isinstance(raw_ids, list) or not raw_ids:
            return self._error_response("move_line_ids must be a non-empty list",
                                        400, "INVALID_PARAMS")
        try:
            target_ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            return self._error_response("move_line_ids must be integers",
                                        400, "INVALID_PARAMS")

        try:
            Line = request.env['account.move.line']
            targets = Line.browse(target_ids).exists()
            if len(targets) != len(target_ids):
                return self._error_response("Some target lines not found",
                                            404, "NOT_FOUND")
            target_accounts = targets.mapped('account_id')
            if len(target_accounts) != 1:
                return self._error_response("Target lines must share a single account",
                                            400, "MIXED_ACCOUNTS")
            target_account = target_accounts

            bank_default = st_line.journal_id.default_account_id
            suspense_aml = st_line.move_id.line_ids.filtered(
                lambda l: l.account_id != bank_default)[:1]
            if not suspense_aml:
                return self._error_response(
                    "Bank statement line has no suspense leg to reconcile",
                    500, "NO_SUSPENSE_LEG")

            # Rewrite the suspense leg's account so reconcile() can pair
            # it with the receivable/payable target lines.
            if suspense_aml.account_id != target_account:
                # Need to reset bank line to draft to mutate the move.
                st_move = st_line.move_id
                if st_move.state == 'posted':
                    st_move.button_draft()
                suspense_aml.write({'account_id': target_account.id})
                if st_move.state == 'draft':
                    st_move.action_post()

            (suspense_aml + targets).reconcile()
            return self._json_response(data={
                'bank_line': self._statement_line_payload(st_line),
                'reconciled_with': targets.ids,
            }, message="Bank line matched")
        except AccessError:
            return self._error_response("Access denied", 403, "ACCESS_DENIED")
        except UserError as e:
            return self._error_response(self._safe_exc_message(e), 400, "MATCH_ERROR")
        except (MissingError, ValidationError) as e:
            return self._error_response(self._safe_exc_message(e), 400, "MATCH_ERROR")
        except Exception as e:
            _logger.error("Bank match error: %s", str(e))
            return self._error_response("Error matching bank line", 500, "MATCH_ERROR")

    @http.route('/api/v2/accounting/bank-statement/<int:statement_id>/close',
                type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def bank_statement_close(self, statement_id, **_kwargs):
        """Validate a statement once balance_end == balance_end_real.

        Soft-close: refuse to validate when lines remain unreconciled
        unless caller passes {"force": true}.
        """
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        acc_error = self._lettrage_check_access(user, write=True)
        if acc_error:
            return acc_error

        try:
            data = request.httprequest.get_json(force=True, silent=True) or {}
        except Exception:
            data = {}

        st = request.env['account.bank.statement'].browse(statement_id)
        if not st.exists():
            return self._error_response("Statement not found", 404, "NOT_FOUND")

        unreconciled = sum(1 for l in st.line_ids if not l.is_reconciled)
        if unreconciled and not data.get('force'):
            return self._error_response(
                f"{unreconciled} line(s) still unreconciled — pass force:true to close anyway",
                400, "UNRECONCILED_LINES")

        # Hard-set the real end balance to the computed one to mark it
        # complete. Odoo's `statement_complete` recomputes from there.
        st.balance_end_real = st.balance_end
        return self._json_response(
            data={'statement': self._statement_payload(st, include_lines=False)},
            message="Statement closed",
        )

    # ===== OHADA REPORTS (print-ready HTML / JSON) =====
    #
    # Comptables expect to download a PDF formatted like the SYSCOHADA
    # OHADA filing layout. We render print-ready HTML server-side with
    # embedded CSS — browser handles PDF via File → Print. Avoids the
    # wkhtmltopdf / QWeb / Enterprise account_reports stack.

    _OHADA_REPORT_CSS = """
        @page { size: A4; margin: 14mm 12mm; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
               font-size: 10pt; color: #111; margin: 16px; }
        .report-header { border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 12px; }
        .report-header h1 { font-size: 16pt; margin: 0 0 4px; }
        .report-header .meta { font-size: 9pt; color: #555; }
        .section-title { background: #f0f0f0; padding: 6px 8px; margin-top: 16px;
                         font-weight: 600; border-left: 4px solid #111; }
        table.lines { width: 100%; border-collapse: collapse; margin-top: 6px; }
        table.lines th, table.lines td { padding: 4px 8px; border-bottom: 1px solid #eee;
                                          text-align: left; font-size: 9.5pt; }
        table.lines th { background: #fafafa; font-weight: 600; }
        table.lines td.num { text-align: right; font-family: ui-monospace, 'SF Mono', monospace; }
        table.lines tr.total td { border-top: 1px solid #111; border-bottom: 2px solid #111;
                                  font-weight: 700; background: #fafafa; }
        table.lines tr.grand-total td { border-top: 2px solid #111; border-bottom: 3px double #111;
                                         font-weight: 700; font-size: 11pt; background: #f8f8f8; }
        .footer { margin-top: 24px; font-size: 8pt; color: #777; border-top: 1px solid #ddd;
                  padding-top: 6px; }
        @media print { body { margin: 0; } .no-print { display: none; } .print-button { display: none; } }
        .print-button { position: fixed; top: 16px; right: 16px; background: #111; color: white;
                        padding: 8px 14px; border-radius: 6px; cursor: pointer; border: 0;
                        font-size: 10pt; }
    """

    def _html_escape(self, value):
        try:
            import markupsafe
            return str(markupsafe.escape(value or ''))
        except ImportError:
            return (str(value) if value else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def _format_xof(self, amount):
        """Display amount with thousands separator, no decimals (OHADA convention)."""
        try:
            value = int(round(float(amount or 0)))
        except (TypeError, ValueError):
            return ''
        sign = '-' if value < 0 else ''
        s = f'{abs(value):,}'.replace(',', ' ')
        return f'{sign}{s}'

    def _render_report_html(self, title, subtitle, sections_html, params, currency_code='XOF'):
        """Wrap section HTML in the OHADA report shell."""
        company = request.env.user.company_id
        company_name = self._html_escape(company.name if company else '')
        country = self._html_escape(company.country_id.name if company and company.country_id else '')
        vat = self._html_escape(company.vat if company else '')
        period_from = params.get('from_date').isoformat() if params.get('from_date') else ''
        period_to = params.get('to_date').isoformat() if params.get('to_date') else ''
        generated = datetime.now().strftime('%Y-%m-%d %H:%M')
        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>{self._html_escape(title)} — {company_name}</title>
<style>{self._OHADA_REPORT_CSS}</style>
</head>
<body>
<button class="print-button no-print" onclick="window.print()">Imprimer / PDF</button>
<div class="report-header">
  <h1>{self._html_escape(title)}</h1>
  <div class="meta">
    <strong>{company_name}</strong> · {country}{' · NIF ' + vat if vat else ''}
  </div>
  <div class="meta">
    {self._html_escape(subtitle)} ·
    Période : {period_from} → {period_to} ·
    Devise : {self._html_escape(currency_code)} ·
    Édité le {generated}
  </div>
</div>
{sections_html}
<div class="footer">
  Édité depuis Toomde — conformité OHADA / SYSCOHADA Révisé.
</div>
</body>
</html>"""
        return html

    def _html_response(self, html):
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    def _section_html(self, label, rows, key_label='Compte', show_grand_total=True):
        """Render one section (table) with accounts + total."""
        body = []
        total = 0.0
        for r in rows:
            amount = r.get('balance') if 'balance' in r else r.get('amount', 0)
            total += amount
            body.append(
                f'<tr>'
                f'<td>{self._html_escape(r.get("code") or "")}</td>'
                f'<td>{self._html_escape(r.get("name") or "")}</td>'
                f'<td class="num">{self._format_xof(amount)}</td>'
                f'</tr>'
            )
        rows_html = ''.join(body) if body else (
            '<tr><td colspan="3" style="color:#888;font-style:italic;">'
            'Aucune écriture sur la période.</td></tr>'
        )
        total_row = ''
        if show_grand_total:
            total_row = (
                f'<tr class="total"><td colspan="2">Total {self._html_escape(label)}</td>'
                f'<td class="num">{self._format_xof(total)}</td></tr>'
            )
        return f"""
<div class="section-title">{self._html_escape(label)}</div>
<table class="lines">
<thead><tr><th style="width:120px">{self._html_escape(key_label)}</th><th>Intitulé</th><th style="width:140px;text-align:right">Montant</th></tr></thead>
<tbody>{rows_html}{total_row}</tbody>
</table>"""

    @http.route('/api/v2/accounting/reports/bilan-sn', type='http',
                auth='none', methods=['GET'], csrf=False)
    def report_bilan_sn(self, **_kwargs):
        """Bilan SYSCOHADA Système Normal — print-ready HTML or JSON."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response("Access denied: requires Accounting role",
                                        403, "ROLE_ACCESS_DENIED")

        params = self._parse_analytics_params()
        fmt = (request.httprequest.args.get('format') or 'html').lower()

        all_types = self._ACCT_ASSET_TYPES + self._ACCT_LIABILITY_TYPES + self._ACCT_EQUITY_TYPES
        rows = self._account_balances(all_types, date_to=params['to_date'])
        actif = [r for r in rows if r['account_type'] in self._ACCT_ASSET_TYPES and r['balance'] != 0]
        passif = [r for r in rows if r['account_type'] in self._ACCT_LIABILITY_TYPES and r['balance'] != 0]
        equity = [r for r in rows if r['account_type'] in self._ACCT_EQUITY_TYPES and r['balance'] != 0]
        # Passif and equity carry credit-natural balances → flip sign for display
        for r in passif + equity:
            r['balance'] = -r['balance']
        for group in (actif, passif, equity):
            group.sort(key=lambda r: r['code'] or '')

        total_actif = sum(r['balance'] for r in actif)
        total_passif = sum(r['balance'] for r in passif) + sum(r['balance'] for r in equity)

        if fmt == 'json':
            return self._json_response(data={
                'actif': actif, 'passif': passif, 'equity': equity,
                'totals': {
                    'actif': round(total_actif, 2),
                    'passif_equity': round(total_passif, 2),
                },
                'as_of': params['to_date'].isoformat(),
            }, message="Bilan SN")

        sections_html = (
            self._section_html('ACTIF', actif)
            + self._section_html('PASSIF', passif)
            + self._section_html('CAPITAUX PROPRES', equity)
            + f"""
<table class="lines" style="margin-top:24px;">
<tbody>
<tr class="grand-total"><td>Total ACTIF</td><td class="num">{self._format_xof(total_actif)}</td></tr>
<tr class="grand-total"><td>Total PASSIF + CAPITAUX PROPRES</td><td class="num">{self._format_xof(total_passif)}</td></tr>
</tbody>
</table>"""
        )
        html = self._render_report_html(
            title='Bilan — SYSCOHADA Système Normal',
            subtitle='Conforme à l\'AUDCIF (Acte Uniforme révisé)',
            sections_html=sections_html,
            params=params,
        )
        return self._html_response(html)

    @http.route('/api/v2/accounting/reports/compte-resultat-sn', type='http',
                auth='none', methods=['GET'], csrf=False)
    def report_compte_resultat_sn(self, **_kwargs):
        """Compte de Résultat SYSCOHADA Système Normal."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response("Access denied: requires Accounting role",
                                        403, "ROLE_ACCESS_DENIED")

        params = self._parse_analytics_params()
        fmt = (request.httprequest.args.get('format') or 'html').lower()

        types = self._ACCT_INCOME_TYPES + self._ACCT_EXPENSE_TYPES
        rows = self._account_balances(
            types,
            date_from=params['from_date'], date_to=params['to_date'],
        )
        produits = [r for r in rows if r['account_type'] in self._ACCT_INCOME_TYPES and r['balance'] != 0]
        charges = [r for r in rows if r['account_type'] in self._ACCT_EXPENSE_TYPES and r['balance'] != 0]
        for r in produits:
            r['balance'] = -r['balance']  # income natural-credit
        for group in (produits, charges):
            group.sort(key=lambda r: r['code'] or '')

        total_produits = sum(r['balance'] for r in produits)
        total_charges = sum(r['balance'] for r in charges)
        resultat = total_produits - total_charges

        if fmt == 'json':
            return self._json_response(data={
                'produits': produits, 'charges': charges,
                'totals': {
                    'produits': round(total_produits, 2),
                    'charges': round(total_charges, 2),
                    'resultat_net': round(resultat, 2),
                },
            }, message="Compte de Résultat SN")

        result_label = "Résultat net (Bénéfice)" if resultat >= 0 else "Résultat net (Perte)"
        sections_html = (
            self._section_html('Produits', produits, key_label='Classe 7')
            + self._section_html('Charges', charges, key_label='Classe 6')
            + f"""
<table class="lines" style="margin-top:24px;">
<tbody>
<tr class="grand-total"><td>Total Produits</td><td class="num">{self._format_xof(total_produits)}</td></tr>
<tr class="grand-total"><td>Total Charges</td><td class="num">{self._format_xof(total_charges)}</td></tr>
<tr class="grand-total"><td>{result_label}</td><td class="num">{self._format_xof(resultat)}</td></tr>
</tbody>
</table>"""
        )
        html = self._render_report_html(
            title='Compte de Résultat — SYSCOHADA Système Normal',
            subtitle='Conforme à l\'AUDCIF (Acte Uniforme révisé)',
            sections_html=sections_html,
            params=params,
        )
        return self._html_response(html)

    @http.route('/api/v2/accounting/reports/balance-generale', type='http',
                auth='none', methods=['GET'], csrf=False)
    def report_balance_generale(self, **_kwargs):
        """Balance générale — all accounts with debit, credit, balance."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response("Access denied: requires Accounting role",
                                        403, "ROLE_ACCESS_DENIED")

        params = self._parse_analytics_params()
        fmt = (request.httprequest.args.get('format') or 'html').lower()

        all_types = (self._ACCT_ASSET_TYPES + self._ACCT_LIABILITY_TYPES
                     + self._ACCT_EQUITY_TYPES + self._ACCT_INCOME_TYPES
                     + self._ACCT_EXPENSE_TYPES)
        rows = self._account_balances(
            all_types,
            date_from=params['from_date'], date_to=params['to_date'],
        )
        rows = [r for r in rows if r['debit'] or r['credit']]
        rows.sort(key=lambda r: r['code'] or '')

        if fmt == 'json':
            return self._json_response(data={'rows': rows}, message="Balance générale")

        body = ''.join(
            f'<tr>'
            f'<td>{self._html_escape(r["code"] or "")}</td>'
            f'<td>{self._html_escape(r["name"] or "")}</td>'
            f'<td class="num">{self._format_xof(r["debit"])}</td>'
            f'<td class="num">{self._format_xof(r["credit"])}</td>'
            f'<td class="num">{self._format_xof(r["balance"])}</td>'
            f'</tr>'
            for r in rows
        )
        total_debit = sum(r['debit'] for r in rows)
        total_credit = sum(r['credit'] for r in rows)
        body += (
            f'<tr class="grand-total"><td colspan="2">Totaux</td>'
            f'<td class="num">{self._format_xof(total_debit)}</td>'
            f'<td class="num">{self._format_xof(total_credit)}</td>'
            f'<td class="num">{self._format_xof(total_debit - total_credit)}</td>'
            f'</tr>'
        )
        sections_html = f"""
<table class="lines">
<thead><tr>
  <th style="width:100px">Compte</th><th>Intitulé</th>
  <th style="width:120px;text-align:right">Débit</th>
  <th style="width:120px;text-align:right">Crédit</th>
  <th style="width:120px;text-align:right">Solde</th>
</tr></thead>
<tbody>{body}</tbody>
</table>"""
        html = self._render_report_html(
            title='Balance générale',
            subtitle='Soldes de tous les comptes mouvementés',
            sections_html=sections_html,
            params=params,
        )
        return self._html_response(html)

    @http.route('/api/v2/accounting/reports/grand-livre', type='http',
                auth='none', methods=['GET'], csrf=False)
    def report_grand_livre(self, **_kwargs):
        """Grand livre — moves per account over the period."""
        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user
        sub_error = self._enforce_subscription()
        if sub_error:
            return sub_error
        quota_error = self._enforce_api_quota()
        if quota_error:
            return quota_error
        if not self._user_has_module_role(user, 'accounting'):
            return self._error_response("Access denied: requires Accounting role",
                                        403, "ROLE_ACCESS_DENIED")

        params = self._parse_analytics_params()
        args = request.httprequest.args
        fmt = (args.get('format') or 'html').lower()

        account_filter = None
        if args.get('account_id'):
            try:
                account_filter = int(args['account_id'])
            except (TypeError, ValueError):
                return self._error_response("account_id must be integer",
                                            400, "INVALID_PARAMS")

        Line = request.env['account.move.line']
        domain = [
            ('parent_state', '=', 'posted'),
            ('date', '>=', params['from_date'].isoformat()),
            ('date', '<=', params['to_date'].isoformat()),
        ]
        if account_filter:
            domain.append(('account_id', '=', account_filter))
        lines = Line.search(domain, order='account_id, date, id', limit=5000)

        # Group by account
        by_account = {}
        for l in lines:
            acc_id = l.account_id.id
            if acc_id not in by_account:
                by_account[acc_id] = {
                    'code': l.account_id.code or '',
                    'name': l.account_id.name,
                    'lines': [],
                }
            by_account[acc_id]['lines'].append(l)

        if fmt == 'json':
            return self._json_response(data={
                'accounts': [
                    {
                        'code': v['code'],
                        'name': v['name'],
                        'lines': [{
                            'id': ll.id,
                            'date': str(ll.date) if ll.date else None,
                            'move_name': ll.move_id.name,
                            'partner': ll.partner_id.name if ll.partner_id else '',
                            'label': ll.name or '',
                            'debit': round(ll.debit or 0, 2),
                            'credit': round(ll.credit or 0, 2),
                        } for ll in v['lines']],
                    } for v in by_account.values()
                ],
            }, message="Grand livre")

        # HTML render
        sections_html = ''
        for v in sorted(by_account.values(), key=lambda x: x['code']):
            running = 0.0
            rows_html = []
            for ll in v['lines']:
                running += (ll.debit or 0) - (ll.credit or 0)
                rows_html.append(
                    f'<tr>'
                    f'<td>{str(ll.date) if ll.date else ""}</td>'
                    f'<td>{self._html_escape(ll.move_id.name or "")}</td>'
                    f'<td>{self._html_escape(ll.partner_id.name if ll.partner_id else "")}</td>'
                    f'<td>{self._html_escape(ll.name or "")}</td>'
                    f'<td class="num">{self._format_xof(ll.debit) if ll.debit else ""}</td>'
                    f'<td class="num">{self._format_xof(ll.credit) if ll.credit else ""}</td>'
                    f'<td class="num">{self._format_xof(running)}</td>'
                    f'</tr>'
                )
            sections_html += f"""
<div class="section-title">{self._html_escape(v['code'])} — {self._html_escape(v['name'])}</div>
<table class="lines">
<thead><tr>
  <th style="width:90px">Date</th><th style="width:110px">Pièce</th>
  <th>Tiers</th><th>Libellé</th>
  <th style="width:100px;text-align:right">Débit</th>
  <th style="width:100px;text-align:right">Crédit</th>
  <th style="width:110px;text-align:right">Solde</th>
</tr></thead>
<tbody>{''.join(rows_html) or '<tr><td colspan="7" style="color:#888;font-style:italic;">Aucun mouvement.</td></tr>'}</tbody>
</table>"""

        if not by_account:
            sections_html = '<p style="color:#888;font-style:italic;">Aucun mouvement sur la période.</p>'

        html = self._render_report_html(
            title='Grand livre',
            subtitle='Mouvements détaillés par compte sur la période',
            sections_html=sections_html,
            params=params,
        )
        return self._html_response(html)

    # ===== BILLING: SELF-SERVICE PLAN CHANGE =====
    #
    # The SPA Settings → Subscription Plan card lets an admin switch
    # the tenant between store / lite / business. Enterprise + Custom
    # are sales-assisted and return 403 from this endpoint — the SPA
    # surfaces "Contact us" instead of a switch button for those.

    _SELF_SERVICE_PLAN_SLUGS = frozenset({"store", "lite", "business"})

    @http.route('/api/v2/billing/change-plan', type='http',
                auth='none', methods=['POST'], csrf=False, readonly=False)
    def billing_change_plan(self, **_kwargs):
        """POST {"new_plan_slug": "store|lite|business"} — admin only.

        Proxies to the control plane's internal plan-change endpoint
        (POST {cp_url}/internal/tenants/{tenant_id}/plan-change). The
        CP handles validation, module install diff, audit row, and
        cache invalidation. Synchronous — up to 10 min to give the
        Odoo install time to finish.
        """
        import urllib.request
        import urllib.error

        is_valid, user = self._authenticate_session()
        if not is_valid:
            return user

        # NOTE: we deliberately skip the generic _enforce_subscription()
        # gate here. Billing endpoints have to stay reachable even when
        # the rest of the API is blocked (overdue payment, expired grace
        # period, etc.) — otherwise the customer can never act on the
        # problem they're being shown. The per-state messaging below
        # gives them a concrete next action (pay invoice / contact sales)
        # instead of the generic "service unavailable" they'd otherwise
        # get from _enforce_subscription.

        # Admin-only — non-admins can't change their own plan even
        # for downgrades; the operation is billable.
        if not user.has_group('base.group_system'):
            return self._error_response(
                "Only administrators can change the subscription plan.",
                403, "ADMIN_ONLY",
            )

        # Per-state gate: refuse with a billing-aware message instead
        # of the generic subscription block.
        enforcer = self._get_enforcer()
        if enforcer is not None:
            try:
                info = enforcer.get_tenant_info() or {}
            except RuntimeError:
                return self._error_response(
                    "Could not verify subscription status. Try again in a moment.",
                    503, "CP_UNREACHABLE",
                )
            status = (info.get('status') or '').lower()
            payment_status = (info.get('payment_status') or '').lower()

            if status == 'suspended':
                return self._error_response(
                    "Your tenant is suspended. Contact sales@toomde.com to reactivate.",
                    403, "TENANT_SUSPENDED",
                )
            if status in ('cancelled', 'deleted'):
                return self._error_response(
                    "Your subscription has ended. Contact sales@toomde.com to restart it.",
                    403, "TENANT_CANCELLED",
                )
            if status == 'provisioning':
                return self._error_response(
                    "Your tenant is still being set up. Try again in a few minutes.",
                    409, "TENANT_PROVISIONING",
                )
            # Overdue with no grace days left: block with HTTP 402
            # Payment Required + actionable message.
            if payment_status == 'overdue':
                grace = info.get('grace_days_remaining')
                if grace is None or grace <= 0:
                    return self._error_response(
                        "Your last invoice is unpaid. Please settle it on your "
                        "billing portal, or contact sales@toomde.com for help, "
                        "before changing your plan.",
                        402, "PAYMENT_OVERDUE",
                    )

        content_type = request.httprequest.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            return self._error_response("Content-Type must be application/json",
                                        400, "INVALID_CONTENT_TYPE")
        try:
            data = request.httprequest.get_json(force=True) or {}
        except Exception:
            return self._error_response("Invalid JSON", 400, "INVALID_JSON")

        new_plan_slug = (data.get('new_plan_slug') or '').strip().lower()
        if new_plan_slug not in self._SELF_SERVICE_PLAN_SLUGS:
            return self._error_response(
                f"Plan '{new_plan_slug}' is not available for self-service. "
                "Contact sales for Enterprise or Custom plans.",
                403, "PLAN_NOT_SELF_SERVICE",
            )

        # Idempotency: the CP plan-change call can take up to 600s (it
        # runs an Odoo module install). A user double-clicking the
        # confirm button — or a network blip that triggers a retry —
        # must not start a second concurrent install. The cached
        # response replays on retry; same key + different new_plan_slug
        # → 409 via the request-hash mismatch path.
        replay, idem = self._idempotency_lookup(user)
        if replay is not None:
            return replay

        tenant_id = os.environ.get('TENANT_ID', '').strip()
        cp_url = os.environ.get('CONTROL_PLANE_URL', '').strip().rstrip('/')
        cp_token = os.environ.get('CONTROL_PLANE_TOKEN', '').strip()
        if not tenant_id or not cp_url or not cp_token:
            return self._error_response(
                "Tenant is not configured for self-service plan changes.",
                500, "CONFIG_MISSING",
            )

        payload = json.dumps({
            'new_plan_slug': new_plan_slug,
            'changed_by': user.login or user.email or f'user#{user.id}',
            'reason': data.get('reason') or '',
        }).encode('utf-8')
        req = urllib.request.Request(
            f"{cp_url}/internal/tenants/{tenant_id}/plan-change",
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {cp_token}',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = resp.read().decode('utf-8')
            response = self._json_response(
                data=json.loads(body),
                message="Plan changed",
            )
            return self._idempotency_store(idem, response)
        except urllib.error.HTTPError as exc:
            try:
                cp_body = exc.read().decode('utf-8')
                cp_detail = json.loads(cp_body).get('detail', cp_body)
            except Exception:
                cp_detail = exc.reason or 'Unknown error'
            # 403 from CP (not in self-service list) maps to 403 here;
            # 400 (install failure) maps to 400 so the SPA toast is honest.
            status = 403 if exc.code == 403 else (exc.code if 400 <= exc.code < 600 else 500)
            code = "PLAN_NOT_SELF_SERVICE" if exc.code == 403 else "PLAN_CHANGE_FAILED"
            return self._error_response(str(cp_detail), status, code)
        except Exception as exc:
            _logger.error("Plan change proxy failed: %s", exc)
            return self._error_response(
                "Could not reach control plane to change plan. Try again later.",
                503, "CP_UNREACHABLE",
            )

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
