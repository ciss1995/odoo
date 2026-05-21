# -*- coding: utf-8 -*-
"""Batch import endpoints for tenant onboarding.

These are dedicated, validate-then-write endpoints for getting a brand-new
tenant's master data in via the wizard. They differ from the generic
/api/v2/create/<model> path on three axes:

  1. Dual auth. Accepts either ``Authorization: Bearer <internal_token>``
     (control plane → tenant — same pattern as /internal/invalidate-cache)
     OR a session token (a tenant admin running the same import flow from
     the SPA in a later phase). The internal-token path runs as the admin
     user and skips per-tenant API quota; the session-token path counts as
     a single API call per batch.
  2. Idempotent. Each input row carries an operator-supplied ``ext_id``.
     We bind it via ``ir.model.data`` (module = ``__import_partners__``);
     re-runs of the same payload update rather than duplicate.
  3. Dry-run. ``dry_run: true`` validates + transforms every row in a
     savepoint that always rolls back. The summary + errors come back
     exactly as they would on commit, so the wizard can preview without
     touching the tenant DB.

Only ``/internal/import/partners`` lives here in the first slice. Products,
opening stock, opening balances, etc. follow the same shape and are added
incrementally to keep this file from sprawling.
"""

import logging
import os
import time as _time

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError

from .base import BaseApiController


_logger = logging.getLogger(__name__)


# Hard cap. The wizard is meant for onboarding — anything bigger almost
# certainly means a misuse (full historical migration), and we want to fail
# loudly rather than time out mid-import.
MAX_ROWS_PER_BATCH = 2000

# xmlid module prefixes — keep these stable; operators rely on the
# external_id staying the same across re-runs.
XMLID_MODULE = {
    'partners': '__import_partners__',
    'products': '__import_products__',
    'product_categories': '__import_product_categories__',
    'employees': '__import_employees__',
    'opening_stock': '__import_opening_stock__',
    'chart_of_accounts': '__import_coa__',
    'opening_balances': '__import_opening_balances__',
}


class ImportsController(BaseApiController):
    """POST /api/v2/internal/import/<domain> handlers."""

    # ---- shared helpers ----------------------------------------------------

    def _authenticate_dual(self):
        """Accept internal token OR session token.

        Internal token: ``Authorization: Bearer <CONTROL_PLANE_TOKEN env>``.
        Validated against the same env var as /internal/invalidate-cache.
        On success the env is switched to the admin user (id from
        base.user_admin xmlid, falling back to login='admin') and the
        request is flagged as quota-exempt.

        Session token: standard /api/v2/* header-or-cookie auth. The caller
        must already have create rights on res.partner; quota counts.

        Returns (ok: bool, user, is_internal: bool, error_response_or_None).
        """
        request.httprequest._api_start_time = _time.time()

        auth_header = request.httprequest.headers.get('Authorization', '') or ''
        if auth_header.startswith('Bearer '):
            token = auth_header.removeprefix('Bearer ').strip()
            expected = os.environ.get('CONTROL_PLANE_TOKEN', '')
            if not expected:
                return False, None, False, self._error_response(
                    "Internal auth not configured on this tenant",
                    503, "INTERNAL_AUTH_DISABLED",
                )
            if token != expected:
                return False, None, False, self._error_response(
                    "Invalid internal token", 401, "INVALID_INTERNAL_TOKEN",
                )
            admin = request.env.ref('base.user_admin', raise_if_not_found=False)
            if admin is None or not admin.active:
                admin = request.env['res.users'].sudo().search(
                    [('login', '=', 'admin'), ('active', '=', True)], limit=1,
                )
            if not admin:
                return False, None, False, self._error_response(
                    "Admin user not available", 500, "NO_ADMIN",
                )
            request.update_env(user=admin.id)
            return True, admin, True, None

        ok, result = self._authenticate_session()
        if not ok:
            return False, None, False, result
        return True, result, False, None

    def _read_batch_payload(self, domain):
        """Parse + shallow-validate the batch envelope.

        Shape:
            {
              "rows": [...],
              "dry_run": false,
              "import_run_id": "uuid-ish"
            }

        Returns (payload_dict, error_response_or_None). The per-row schema
        is the domain handler's problem.
        """
        content_type = request.httprequest.headers.get('Content-Type', '') or ''
        if 'application/json' not in content_type:
            return None, self._error_response(
                "Content-Type must be application/json",
                400, "INVALID_CONTENT_TYPE",
            )
        try:
            data = request.httprequest.get_json(force=True)
        except Exception:
            return None, self._error_response(
                "Invalid JSON body", 400, "INVALID_JSON",
            )
        if not isinstance(data, dict):
            return None, self._error_response(
                "Body must be a JSON object", 400, "INVALID_JSON",
            )

        rows = data.get('rows')
        if not isinstance(rows, list) or not rows:
            return None, self._error_response(
                "'rows' must be a non-empty list", 400, "EMPTY_BATCH",
            )
        if len(rows) > MAX_ROWS_PER_BATCH:
            return None, self._error_response(
                f"Batch too large; max {MAX_ROWS_PER_BATCH} rows per call",
                413, "BATCH_TOO_LARGE",
            )

        # ``options`` is the per-domain knobs (date, journal, suspense
        # account for opening_balances). Most domains ignore it.
        options = data.get('options')
        if not isinstance(options, dict):
            options = {}

        return {
            'rows': rows,
            'dry_run': bool(data.get('dry_run', False)),
            'import_run_id': str(data.get('import_run_id') or 'adhoc'),
            'options': options,
        }, None

    # ---- /internal/import/partners ----------------------------------------

    @http.route(
        '/api/v2/internal/import/partners',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_partners(self):
        ok, user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        # Session-token callers must clear subscription + quota gates. The
        # internal-token path is the CP's import pipeline and bypasses them
        # by design — onboarding writes must succeed even when the tenant
        # is still in 'provisioning' state.
        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('partners')
        if err:
            return err

        try:
            result = self._run_partners_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing partners: %s", exc)
            return self._error_response(
                "Access denied on res.partner", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_partners")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else f"Imported {result['summary']['created']} new, "
                     f"updated {result['summary']['updated']}"
            ),
        )

    # ---- /internal/import/chart-of-accounts --------------------------------

    @http.route(
        '/api/v2/internal/import/chart-of-accounts',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_chart_of_accounts(self):
        ok, _user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('chart_of_accounts')
        if err:
            return err

        try:
            result = self._run_coa_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing CoA: %s", exc)
            return self._error_response(
                "Access denied on account.account", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_chart_of_accounts")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else f"Imported {result['summary']['created']} new accounts, "
                     f"updated {result['summary']['updated']}"
            ),
        )

    # ---- /internal/import/opening-balances ---------------------------------

    @http.route(
        '/api/v2/internal/import/opening-balances',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_opening_balances(self):
        ok, _user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('opening_balances')
        if err:
            return err

        try:
            result = self._run_opening_balances_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
                options=payload['options'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing opening balances: %s", exc)
            return self._error_response(
                "Access denied on account.move", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_opening_balances")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else "Opening balances posted"
            ),
        )

    # ---- /internal/import/opening-stock ------------------------------------

    @http.route(
        '/api/v2/internal/import/opening-stock',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_opening_stock(self):
        ok, _user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('opening_stock')
        if err:
            return err

        try:
            result = self._run_opening_stock_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing opening stock: %s", exc)
            return self._error_response(
                "Access denied on stock.quant", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_opening_stock")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else f"Set opening stock on {result['summary']['created'] + result['summary']['updated']} quants"
            ),
        )

    # ---- /internal/import/employees ----------------------------------------

    @http.route(
        '/api/v2/internal/import/employees',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_employees(self):
        ok, _user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('employees')
        if err:
            return err

        try:
            result = self._run_employees_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing employees: %s", exc)
            return self._error_response(
                "Access denied on hr.employee", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_employees")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else f"Imported {result['summary']['created']} new, "
                     f"updated {result['summary']['updated']}"
            ),
        )

    # ---- /internal/import/products -----------------------------------------

    @http.route(
        '/api/v2/internal/import/products',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def import_products(self):
        ok, _user, is_internal, err = self._authenticate_dual()
        if not ok:
            return err

        if not is_internal:
            sub_err = self._enforce_subscription()
            if sub_err:
                return sub_err
            quota_err = self._enforce_api_quota()
            if quota_err:
                return quota_err

        payload, err = self._read_batch_payload('products')
        if err:
            return err

        try:
            result = self._run_products_batch(
                rows=payload['rows'],
                dry_run=payload['dry_run'],
                run_id=payload['import_run_id'],
            )
        except AccessError as exc:
            _logger.warning("Access denied importing products: %s", exc)
            return self._error_response(
                "Access denied on product.template", 403, "ACCESS_DENIED",
            )
        except Exception:
            _logger.exception("Unhandled error in import_products")
            return self._error_response(
                "Import failed", 500, "IMPORT_ERROR",
            )

        return self._json_response(
            data=result,
            message=(
                "Dry run complete" if payload['dry_run']
                else f"Imported {result['summary']['created']} new, "
                     f"updated {result['summary']['updated']}"
            ),
        )

    # ---- partner domain logic ---------------------------------------------

    # Allow-listed fields. Anything not here gets stripped — keeps callers
    # from sneaking arbitrary writes through the wizard (e.g. company_id,
    # parent_id) without an explicit decision.
    PARTNER_FIELDS = frozenset({
        'name', 'display_name', 'email', 'phone', 'mobile',
        'street', 'street2', 'city', 'zip', 'website',
        'vat', 'ref', 'comment', 'is_company', 'lang',
        'function', 'title',
    })

    def _run_partners_batch(self, *, rows, dry_run, run_id):
        """Validate + apply every row. Always returns a structured summary."""
        env = request.env
        IrModelData = env['ir.model.data'].sudo()
        # Writes to res.partner go through the user's env so ACLs still apply;
        # only ir.model.data is sudo'd because it sits in BLOCKED_MODELS for
        # generic CRUD but is required for the xmlid-based idempotency.

        country_cache = {}
        def _resolve_country(value):
            if not value:
                return None
            key = value.strip().upper()
            if key in country_cache:
                return country_cache[key]
            country = env['res.country'].sudo().search([
                '|', ('code', '=', key), ('name', '=ilike', value.strip()),
            ], limit=1)
            country_cache[key] = country.id if country else None
            return country_cache[key]

        state_cache = {}
        def _resolve_state(value, country_id):
            if not value or not country_id:
                return None
            key = (country_id, value.strip().lower())
            if key in state_cache:
                return state_cache[key]
            state = env['res.country.state'].sudo().search([
                ('country_id', '=', country_id),
                '|', ('code', '=', value.strip().upper()),
                     ('name', '=ilike', value.strip()),
            ], limit=1)
            state_cache[key] = state.id if state else None
            return state_cache[key]

        created = 0
        updated = 0
        skipped = 0
        failed = 0
        records = []
        errors = []
        module = XMLID_MODULE['partners']

        # Single savepoint that covers all rows. Dry-run flushes it; commit
        # path lets the request transaction proceed normally.
        cr = env.cr
        cr.execute("SAVEPOINT import_partners")

        try:
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'BAD_ROW', 'message': 'Row must be an object',
                    })
                    continue

                ext_id = (raw.get('ext_id') or '').strip()
                if not ext_id:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'MISSING_EXT_ID',
                        'message': 'ext_id is required for idempotent import',
                    })
                    continue
                if not raw.get('name'):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'MISSING_NAME',
                        'message': 'name is required',
                    })
                    continue

                # Strip to allow-listed fields.
                values = {k: v for k, v in raw.items() if k in self.PARTNER_FIELDS}

                # Country + state resolution (rows give names/codes; Odoo
                # needs ids).
                country_in = raw.get('country')
                country_id = _resolve_country(country_in) if country_in else None
                if country_in and country_id is None:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'UNKNOWN_COUNTRY',
                        'message': f"Country not found: {country_in!r}",
                    })
                    continue
                if country_id:
                    values['country_id'] = country_id
                state_in = raw.get('state')
                if state_in:
                    state_id = _resolve_state(state_in, country_id)
                    if state_id:
                        values['state_id'] = state_id
                    # Unknown state is non-fatal — Odoo allows partner
                    # without a state.

                # Customer/vendor flag — mirrors the convenience mapping
                # in simple_api.create_record.
                partner_type = (raw.get('partner_type') or 'customer').lower()
                if partner_type == 'customer':
                    values.setdefault('customer_rank', 1)
                elif partner_type == 'vendor':
                    values.setdefault('supplier_rank', 1)
                elif partner_type == 'both':
                    values.setdefault('customer_rank', 1)
                    values.setdefault('supplier_rank', 1)
                else:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'BAD_PARTNER_TYPE',
                        'message': "partner_type must be customer, vendor, or both",
                    })
                    continue

                # Find by xmlid.
                xmlid_name = ext_id
                existing = IrModelData.search([
                    ('module', '=', module), ('name', '=', xmlid_name),
                ], limit=1)
                partner = None
                if existing and existing.model == 'res.partner':
                    partner = env['res.partner'].browse(existing.res_id)
                    if not partner.exists():
                        existing.unlink()
                        existing = IrModelData.browse()
                        partner = None

                try:
                    if partner:
                        partner.write(values)
                        updated += 1
                        action = 'updated'
                    else:
                        partner = env['res.partner'].create(values)
                        IrModelData.create({
                            'module': module, 'name': xmlid_name,
                            'model': 'res.partner', 'res_id': partner.id,
                            'noupdate': True,
                        })
                        created += 1
                        action = 'created'
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'VALIDATION',
                        'message': str(exc),
                    })
                    continue
                except AccessError:
                    # Bubble up — this is a permissions problem the whole
                    # batch is going to hit. Let the outer handler 403.
                    raise
                except Exception as exc:
                    _logger.exception(
                        "Partner write failed for ext_id=%s", ext_id,
                    )
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'UNKNOWN',
                        'message': f"Internal error: {exc!s}",
                    })
                    continue

                records.append({
                    'row': idx, 'ext_id': ext_id,
                    'id': partner.id, 'action': action,
                })

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_partners")
            else:
                cr.execute("RELEASE SAVEPOINT import_partners")
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_partners")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'failed': failed,
                'total': len(rows),
            },
            'records': records,
            'errors': errors,
        }

    # ---- product domain logic ---------------------------------------------

    PRODUCT_FIELDS = frozenset({
        'name', 'default_code', 'barcode', 'list_price', 'standard_price',
        'sale_ok', 'purchase_ok', 'description', 'description_sale',
        'description_purchase', 'active', 'weight', 'volume',
    })

    # Odoo's `type` field values. Older databases use these literals; we map
    # operator-friendly synonyms to the canonical set.
    _PRODUCT_TYPE_MAP = {
        'product': 'product', 'storable': 'product', 'stockable': 'product',
        'goods': 'product',
        'service': 'service',
        'consu': 'consu', 'consumable': 'consu',
    }

    def _run_products_batch(self, *, rows, dry_run, run_id):
        """Validate + apply every row. Auto-creates categories on first sight
        (operators rarely pre-create them); UoM is resolved by name or code
        and falls back to the company default if unspecified.
        """
        env = request.env
        IrModelData = env['ir.model.data'].sudo()

        category_cache = {}

        def _resolve_category(value):
            """Resolve a category by name, optionally with a '/'-separated path.

            Path semantics match Odoo's `complete_name` (e.g.
            "All / Saleable / Phones"). Missing intermediate categories are
            auto-created — operators almost never pre-create the tree
            manually, and refusing to import on the first unknown level
            would force them to abandon the wizard.
            """
            if not value:
                return None
            path = [p.strip() for p in str(value).split('/') if p.strip()]
            if not path:
                return None
            key = tuple(path)
            if key in category_cache:
                return category_cache[key]
            parent_id = False
            current = None
            for name in path:
                domain = [('name', '=', name)]
                if parent_id:
                    domain.append(('parent_id', '=', parent_id))
                else:
                    # Top-level: match parent_id is False OR the name is the
                    # default 'All' category which sits at the root.
                    domain.append(('parent_id', '=', False))
                cat = env['product.category'].sudo().search(domain, limit=1)
                if not cat:
                    cat = env['product.category'].sudo().create({
                        'name': name,
                        'parent_id': parent_id or False,
                    })
                current = cat
                parent_id = cat.id
            category_cache[key] = current.id if current else None
            return category_cache[key]

        uom_cache = {}

        def _resolve_uom(value):
            if not value:
                return None
            key = str(value).strip().lower()
            if key in uom_cache:
                return uom_cache[key]
            # Match by exact name (case-insensitive) — UoM categories are
            # also a thing in Odoo, but for onboarding the operator just
            # names the unit ("Units", "kg", "L").
            uom = env['uom.uom'].sudo().search([
                ('name', '=ilike', value.strip()),
            ], limit=1)
            uom_cache[key] = uom.id if uom else None
            return uom_cache[key]

        # Default UoM (used when row doesn't specify one). PRODUCT_UOM_UNIT
        # exists in every Odoo install; if it's been deleted, we fall back
        # to whatever the company default is.
        default_uom = env.ref('uom.product_uom_unit', raise_if_not_found=False)
        if default_uom is None:
            default_uom = env['uom.uom'].sudo().search([], limit=1)
        default_uom_id = default_uom.id if default_uom else False

        created = 0
        updated = 0
        skipped = 0
        failed = 0
        records = []
        errors = []
        module = XMLID_MODULE['products']

        cr = env.cr
        cr.execute("SAVEPOINT import_products")

        try:
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'BAD_ROW', 'message': 'Row must be an object',
                    })
                    continue

                ext_id = (raw.get('ext_id') or '').strip()
                if not ext_id:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'MISSING_EXT_ID',
                        'message': 'ext_id is required for idempotent import',
                    })
                    continue
                if not raw.get('name'):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'MISSING_NAME',
                        'message': 'name is required',
                    })
                    continue

                # Strip to allow-listed fields.
                values = {k: v for k, v in raw.items() if k in self.PRODUCT_FIELDS}

                # Coerce numeric strings — CSV always delivers strings; Odoo
                # write() with a string in a float field would raise.
                for fld in ('list_price', 'standard_price', 'weight', 'volume'):
                    if fld in values and isinstance(values[fld], str):
                        try:
                            values[fld] = float(values[fld].replace(',', '.'))
                        except (ValueError, AttributeError):
                            failed += 1
                            errors.append({
                                'row': idx, 'ext_id': ext_id,
                                'code': 'BAD_NUMBER',
                                'message': f"{fld!r} must be a number",
                            })
                            values = None
                            break
                if values is None:
                    continue

                # Boolean coercion for sale_ok / purchase_ok / active.
                for fld in ('sale_ok', 'purchase_ok', 'active'):
                    if fld in values and isinstance(values[fld], str):
                        values[fld] = values[fld].strip().lower() in (
                            'true', '1', 'yes', 'y', 'oui',
                        )

                # Type → canonical literal. Default is 'product' (storable);
                # if the tenant doesn't have stock_account installed they
                # need to set 'consu' or 'service' explicitly.
                type_in = (raw.get('type') or raw.get('product_type') or '').strip().lower()
                if type_in:
                    canonical = self._PRODUCT_TYPE_MAP.get(type_in)
                    if not canonical:
                        failed += 1
                        errors.append({
                            'row': idx, 'ext_id': ext_id,
                            'code': 'BAD_TYPE',
                            'message': f"type {type_in!r} must be one of "
                                       "product/storable, consu/consumable, service",
                        })
                        continue
                    # Odoo 18+ renamed product type field to detailed_type for
                    # template; the older `type` field still exists as a stored
                    # related. Set both for safety where applicable.
                    if 'detailed_type' in env['product.template']._fields:
                        values['detailed_type'] = canonical
                    if 'type' in env['product.template']._fields:
                        values['type'] = canonical

                # Category resolution (optional but recommended).
                category_in = raw.get('category') or raw.get('categ_id')
                if category_in:
                    if isinstance(category_in, int):
                        # Trust an explicit numeric id (advanced use).
                        if env['product.category'].browse(category_in).exists():
                            values['categ_id'] = category_in
                        else:
                            failed += 1
                            errors.append({
                                'row': idx, 'ext_id': ext_id,
                                'code': 'UNKNOWN_CATEGORY',
                                'message': f"category_id {category_in} not found",
                            })
                            continue
                    else:
                        cat_id = _resolve_category(category_in)
                        if cat_id is None:
                            failed += 1
                            errors.append({
                                'row': idx, 'ext_id': ext_id,
                                'code': 'CATEGORY_RESOLVE_FAILED',
                                'message': f"Could not resolve category {category_in!r}",
                            })
                            continue
                        values['categ_id'] = cat_id

                # UoM resolution (optional; default fills in).
                uom_in = raw.get('uom') or raw.get('uom_id')
                if uom_in:
                    if isinstance(uom_in, int):
                        if env['uom.uom'].browse(uom_in).exists():
                            values['uom_id'] = uom_in
                            values.setdefault('uom_po_id', uom_in)
                    else:
                        uom_id = _resolve_uom(uom_in)
                        if uom_id is None:
                            failed += 1
                            errors.append({
                                'row': idx, 'ext_id': ext_id,
                                'code': 'UNKNOWN_UOM',
                                'message': f"Unit of measure {uom_in!r} not found",
                            })
                            continue
                        values['uom_id'] = uom_id
                        values.setdefault('uom_po_id', uom_id)
                elif default_uom_id:
                    values.setdefault('uom_id', default_uom_id)
                    values.setdefault('uom_po_id', default_uom_id)

                # Find by xmlid.
                existing = IrModelData.search([
                    ('module', '=', module), ('name', '=', ext_id),
                ], limit=1)
                product = None
                if existing and existing.model == 'product.template':
                    product = env['product.template'].browse(existing.res_id)
                    if not product.exists():
                        existing.unlink()
                        product = None

                try:
                    if product:
                        product.write(values)
                        updated += 1
                        action = 'updated'
                    else:
                        product = env['product.template'].create(values)
                        IrModelData.create({
                            'module': module, 'name': ext_id,
                            'model': 'product.template', 'res_id': product.id,
                            'noupdate': True,
                        })
                        created += 1
                        action = 'created'
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'VALIDATION',
                        'message': str(exc),
                    })
                    continue
                except AccessError:
                    raise
                except Exception as exc:
                    _logger.exception(
                        "Product write failed for ext_id=%s", ext_id,
                    )
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'UNKNOWN',
                        'message': f"Internal error: {exc!s}",
                    })
                    continue

                records.append({
                    'row': idx, 'ext_id': ext_id,
                    'id': product.id, 'action': action,
                })

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_products")
            else:
                cr.execute("RELEASE SAVEPOINT import_products")
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_products")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'failed': failed,
                'total': len(rows),
            },
            'records': records,
            'errors': errors,
        }

    # ---- employee domain logic --------------------------------------------

    EMPLOYEE_FIELDS = frozenset({
        'name', 'work_email', 'work_phone', 'mobile_phone',
        'job_title', 'active', 'gender', 'identification_id',
        'passport_id', 'private_email', 'private_phone',
    })

    def _run_employees_batch(self, *, rows, dry_run, run_id):
        """Import hr.employee. Departments and jobs are auto-created on
        first sight (operator workflow rarely pre-creates them — they're
        usually free-text in the source spreadsheet)."""
        env = request.env
        IrModelData = env['ir.model.data'].sudo()

        dept_cache = {}
        def _resolve_department(value):
            if not value:
                return None
            key = str(value).strip().lower()
            if key in dept_cache:
                return dept_cache[key]
            name = str(value).strip()
            dept = env['hr.department'].sudo().search([
                ('name', '=ilike', name),
            ], limit=1)
            if not dept:
                dept = env['hr.department'].sudo().create({'name': name})
            dept_cache[key] = dept.id
            return dept.id

        job_cache = {}
        def _resolve_job(value, department_id=None):
            if not value:
                return None
            key = (department_id, str(value).strip().lower())
            if key in job_cache:
                return job_cache[key]
            name = str(value).strip()
            domain = [('name', '=ilike', name)]
            if department_id:
                domain.append(('department_id', '=', department_id))
            job = env['hr.job'].sudo().search(domain, limit=1)
            if not job:
                values = {'name': name}
                if department_id:
                    values['department_id'] = department_id
                job = env['hr.job'].sudo().create(values)
            job_cache[key] = job.id
            return job.id

        created = 0
        updated = 0
        skipped = 0
        failed = 0
        records = []
        errors = []
        module = XMLID_MODULE['employees']

        cr = env.cr
        cr.execute("SAVEPOINT import_employees")

        try:
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'BAD_ROW', 'message': 'Row must be an object',
                    })
                    continue

                ext_id = (raw.get('ext_id') or '').strip()
                if not ext_id:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'MISSING_EXT_ID',
                        'message': 'ext_id is required for idempotent import',
                    })
                    continue
                if not raw.get('name'):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'MISSING_NAME',
                        'message': 'name is required',
                    })
                    continue

                values = {k: v for k, v in raw.items() if k in self.EMPLOYEE_FIELDS}

                if isinstance(values.get('active'), str):
                    values['active'] = values['active'].strip().lower() in (
                        'true', '1', 'yes', 'y', 'oui',
                    )

                dept_in = raw.get('department') or raw.get('department_name')
                department_id = None
                if dept_in:
                    department_id = _resolve_department(dept_in)
                    if department_id:
                        values['department_id'] = department_id

                job_in = raw.get('job') or raw.get('job_name') or raw.get('position')
                if job_in:
                    job_id = _resolve_job(job_in, department_id)
                    if job_id:
                        values['job_id'] = job_id

                existing = IrModelData.search([
                    ('module', '=', module), ('name', '=', ext_id),
                ], limit=1)
                employee = None
                if existing and existing.model == 'hr.employee':
                    employee = env['hr.employee'].browse(existing.res_id)
                    if not employee.exists():
                        existing.unlink()
                        employee = None

                try:
                    if employee:
                        employee.write(values)
                        updated += 1
                        action = 'updated'
                    else:
                        employee = env['hr.employee'].create(values)
                        IrModelData.create({
                            'module': module, 'name': ext_id,
                            'model': 'hr.employee', 'res_id': employee.id,
                            'noupdate': True,
                        })
                        created += 1
                        action = 'created'
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'VALIDATION',
                        'message': str(exc),
                    })
                    continue
                except AccessError:
                    raise
                except Exception as exc:
                    _logger.exception(
                        "Employee write failed for ext_id=%s", ext_id,
                    )
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'UNKNOWN',
                        'message': f"Internal error: {exc!s}",
                    })
                    continue

                records.append({
                    'row': idx, 'ext_id': ext_id,
                    'id': employee.id, 'action': action,
                })

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_employees")
            else:
                cr.execute("RELEASE SAVEPOINT import_employees")
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_employees")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'failed': failed,
                'total': len(rows),
            },
            'records': records,
            'errors': errors,
        }

    # ---- opening-stock domain logic ---------------------------------------
    #
    # Opening stock is *transactional* setup, unlike the master-data domains
    # above. Each input row sets the on-hand quantity for a (product,
    # location) pair. We write directly into stock.quant.quantity rather
    # than going through inventory_quantity + action_apply_inventory()
    # because the contract is "this runs before the tenant has any moves,
    # so there's nothing to reconcile against." If you re-run after the
    # tenant has started transacting, you'll want a proper inventory
    # adjustment instead — the wizard surfaces a warning for that case in
    # the UI.

    def _run_opening_stock_batch(self, *, rows, dry_run, run_id):
        env = request.env
        IrModelData = env['ir.model.data'].sudo()

        warehouse_cache = {}

        def _resolve_location(warehouse_in, location_in):
            """Resolve a (warehouse | location) hint to a stock.location id.

            ``location_in`` wins when given (operators occasionally point
            directly at a non-stock internal location like "WH/QC"). When
            only a warehouse is given, use its lot_stock_id ("WH/Stock").
            When neither is given, default to the first warehouse — most
            tenants have exactly one at onboarding time.
            """
            if location_in:
                key = ('loc', str(location_in).strip().lower())
                if key in warehouse_cache:
                    return warehouse_cache[key]
                loc = env['stock.location'].sudo().search([
                    ('usage', '=', 'internal'),
                    '|', ('complete_name', '=ilike', str(location_in).strip()),
                         ('name', '=ilike', str(location_in).strip()),
                ], limit=1)
                warehouse_cache[key] = loc.id if loc else None
                return warehouse_cache[key]

            key = ('wh', str(warehouse_in or '').strip().lower())
            if key in warehouse_cache:
                return warehouse_cache[key]
            if warehouse_in:
                wh = env['stock.warehouse'].sudo().search([
                    '|', ('name', '=ilike', str(warehouse_in).strip()),
                         ('code', '=ilike', str(warehouse_in).strip()),
                ], limit=1)
            else:
                wh = env['stock.warehouse'].sudo().search([], limit=1)
            warehouse_cache[key] = wh.lot_stock_id.id if wh else None
            return warehouse_cache[key]

        # Cache product lookups by SKU; resolves to the single product.product
        # variant when there's exactly one. Templates with multiple variants
        # can't be set this way and we fail the row with a clear message.
        product_cache = {}

        def _resolve_product(product_ext_id, sku):
            """Look up a product.product. Two paths:

            1. ``product_ext_id`` — operator-supplied xmlid from the
               earlier products import (``__import_products__.<ext_id>``).
               Most reliable: deterministic, survives renames.
            2. ``sku`` — falls back to product.default_code lookup.
            """
            if product_ext_id:
                key = ('xid', str(product_ext_id).strip())
                if key in product_cache:
                    return product_cache[key]
                rec = IrModelData.search([
                    ('module', '=', XMLID_MODULE['products']),
                    ('name', '=', key[1]),
                ], limit=1)
                tpl = env['product.template'].browse(rec.res_id) if rec else None
                if tpl and tpl.exists() and len(tpl.product_variant_ids) == 1:
                    pid = tpl.product_variant_ids.id
                    product_cache[key] = pid
                    return pid
                product_cache[key] = None
                return None
            if sku:
                key = ('sku', str(sku).strip())
                if key in product_cache:
                    return product_cache[key]
                products = env['product.product'].sudo().search([
                    ('default_code', '=', key[1]),
                ], limit=2)
                if len(products) == 1:
                    product_cache[key] = products.id
                    return products.id
                product_cache[key] = None
                return None
            return None

        created = 0
        updated = 0
        skipped = 0
        failed = 0
        records = []
        errors = []
        module = XMLID_MODULE['opening_stock']

        cr = env.cr
        cr.execute("SAVEPOINT import_opening_stock")

        try:
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'BAD_ROW', 'message': 'Row must be an object',
                    })
                    continue

                # ext_id is optional for opening stock — we synthesize
                # <product>__<location> if not given. Operators rarely
                # think of stock entries as having external ids.
                product_ext_id = (raw.get('product_ext_id') or '').strip()
                sku = (raw.get('sku') or raw.get('default_code') or '').strip()
                if not product_ext_id and not sku:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'MISSING_PRODUCT',
                        'message': 'product_ext_id or sku is required',
                    })
                    continue

                product_id = _resolve_product(product_ext_id, sku)
                if not product_id:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': product_ext_id or sku,
                        'code': 'PRODUCT_NOT_FOUND',
                        'message': (
                            f"No single product matches "
                            f"{'ext_id=' + product_ext_id if product_ext_id else 'sku=' + sku}. "
                            "Multi-variant templates aren't supported in opening stock — "
                            "set quantity per variant."
                        ),
                    })
                    continue

                location_id = _resolve_location(
                    raw.get('warehouse'), raw.get('location'),
                )
                if not location_id:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': product_ext_id or sku,
                        'code': 'LOCATION_NOT_FOUND',
                        'message': (
                            f"Could not resolve warehouse "
                            f"{raw.get('warehouse')!r} or location "
                            f"{raw.get('location')!r}"
                        ),
                    })
                    continue

                qty_raw = raw.get('quantity', raw.get('on_hand'))
                if qty_raw in (None, ''):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': product_ext_id or sku,
                        'code': 'MISSING_QUANTITY',
                        'message': 'quantity is required',
                    })
                    continue
                try:
                    quantity = float(str(qty_raw).replace(',', '.'))
                except (ValueError, TypeError):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': product_ext_id or sku,
                        'code': 'BAD_NUMBER',
                        'message': f"quantity {qty_raw!r} is not a number",
                    })
                    continue

                xmlid_name = f"{product_ext_id or sku}__loc{location_id}"

                # Re-runs: look up existing import-owned quant by xmlid;
                # otherwise locate the canonical quant by (product, lot,
                # location) so we don't create a parallel row.
                existing = IrModelData.search([
                    ('module', '=', module), ('name', '=', xmlid_name),
                ], limit=1)
                quant = None
                if existing and existing.model == 'stock.quant':
                    quant = env['stock.quant'].sudo().browse(existing.res_id)
                    if not quant.exists():
                        existing.unlink()
                        quant = None

                if not quant:
                    quant = env['stock.quant'].sudo().search([
                        ('product_id', '=', product_id),
                        ('location_id', '=', location_id),
                        ('lot_id', '=', False),
                    ], limit=1)

                try:
                    if quant:
                        quant.sudo().write({'quantity': quantity})
                        action = 'updated'
                        updated += 1
                    else:
                        quant = env['stock.quant'].sudo().create({
                            'product_id': product_id,
                            'location_id': location_id,
                            'quantity': quantity,
                        })
                        action = 'created'
                        created += 1

                    # Bind the xmlid so re-runs find the same quant.
                    if not existing:
                        IrModelData.create({
                            'module': module, 'name': xmlid_name,
                            'model': 'stock.quant', 'res_id': quant.id,
                            'noupdate': True,
                        })
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': xmlid_name,
                        'code': 'VALIDATION',
                        'message': str(exc),
                    })
                    continue
                except AccessError:
                    raise
                except Exception as exc:
                    _logger.exception(
                        "Opening stock write failed at row %d", idx,
                    )
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': xmlid_name,
                        'code': 'UNKNOWN',
                        'message': f"Internal error: {exc!s}",
                    })
                    continue

                records.append({
                    'row': idx, 'ext_id': xmlid_name,
                    'id': quant.id, 'action': action,
                })

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_opening_stock")
            else:
                cr.execute("RELEASE SAVEPOINT import_opening_stock")
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_opening_stock")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'failed': failed,
                'total': len(rows),
            },
            'records': records,
            'errors': errors,
        }

    # ---- chart-of-accounts domain logic -----------------------------------

    COA_FIELDS = frozenset({
        'name', 'code', 'reconcile', 'note', 'currency_id',
    })

    # User-friendly synonyms → canonical Odoo account_type literals. The
    # localization-installed CoA already covers the standard accounts; this
    # import is for the tenant's *additions* (custom suspense, sub-accounts,
    # bank-style cash, etc.). Source files tend to use the operator's
    # natural language, not Odoo's enum strings.
    _ACCOUNT_TYPE_SYNONYMS = {
        'receivable': 'asset_receivable',
        'accounts receivable': 'asset_receivable',
        'ar': 'asset_receivable',
        'payable': 'liability_payable',
        'accounts payable': 'liability_payable',
        'ap': 'liability_payable',
        'cash': 'asset_cash',
        'bank': 'asset_cash',
        'asset': 'asset_current',
        'current asset': 'asset_current',
        'non-current asset': 'asset_non_current',
        'non current asset': 'asset_non_current',
        'fixed asset': 'asset_fixed',
        'prepayment': 'asset_prepayments',
        'prepayments': 'asset_prepayments',
        'liability': 'liability_current',
        'current liability': 'liability_current',
        'non-current liability': 'liability_non_current',
        'non current liability': 'liability_non_current',
        'credit card': 'liability_credit_card',
        'equity': 'equity',
        'income': 'income',
        'revenue': 'income',
        'sales': 'income',
        'other income': 'income_other',
        'expense': 'expense',
        'cost': 'expense',
        'cogs': 'expense_direct_cost',
        'cost of goods': 'expense_direct_cost',
        'cost of goods sold': 'expense_direct_cost',
        'depreciation': 'expense_depreciation',
        'off balance': 'off_balance',
        'off-balance': 'off_balance',
    }
    _CANONICAL_ACCOUNT_TYPES = frozenset({
        'asset_receivable', 'asset_cash', 'asset_current', 'asset_non_current',
        'asset_prepayments', 'asset_fixed', 'liability_payable',
        'liability_credit_card', 'liability_current', 'liability_non_current',
        'equity', 'equity_unaffected', 'income', 'income_other',
        'expense', 'expense_depreciation', 'expense_direct_cost', 'off_balance',
    })

    def _canonicalize_account_type(self, value):
        if not value:
            return None
        key = str(value).strip().lower()
        if key in self._CANONICAL_ACCOUNT_TYPES:
            return key
        return self._ACCOUNT_TYPE_SYNONYMS.get(key)

    def _run_coa_batch(self, *, rows, dry_run, run_id):
        env = request.env
        IrModelData = env['ir.model.data'].sudo()

        company_id = env.company.id
        created = 0
        updated = 0
        skipped = 0
        failed = 0
        records = []
        errors = []
        module = XMLID_MODULE['chart_of_accounts']

        cr = env.cr
        cr.execute("SAVEPOINT import_coa")

        try:
            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': None,
                        'code': 'BAD_ROW', 'message': 'Row must be an object',
                    })
                    continue

                ext_id = (raw.get('ext_id') or '').strip()
                code = (raw.get('code') or '').strip()
                name = (raw.get('name') or '').strip()

                # Both code and name required. ext_id defaults to "code-<code>"
                # so the operator doesn't have to invent one — account codes
                # are already unique per company.
                if not code:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id or None,
                        'code': 'MISSING_CODE',
                        'message': 'Account code is required',
                    })
                    continue
                if not name:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id or f'code-{code}',
                        'code': 'MISSING_NAME',
                        'message': 'Account name is required',
                    })
                    continue
                if not ext_id:
                    ext_id = f'code-{code}'

                type_in = raw.get('account_type') or raw.get('type')
                canonical_type = self._canonicalize_account_type(type_in)
                if not canonical_type:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'BAD_ACCOUNT_TYPE',
                        'message': (
                            f"account_type {type_in!r} unrecognized. Use one of: "
                            "receivable, payable, cash, asset, fixed asset, "
                            "liability, equity, income, expense, cogs, depreciation."
                        ),
                    })
                    continue

                values = {
                    'code': code,
                    'name': name,
                    'account_type': canonical_type,
                    'company_ids': [(4, company_id)],
                }

                # Reconcile auto-enables for AR/AP unless the row says otherwise.
                if 'reconcile' in raw:
                    rec_val = raw['reconcile']
                    if isinstance(rec_val, str):
                        rec_val = rec_val.strip().lower() in ('true', '1', 'yes', 'y', 'oui')
                    values['reconcile'] = bool(rec_val)
                elif canonical_type in ('asset_receivable', 'liability_payable'):
                    values['reconcile'] = True

                if raw.get('note'):
                    values['note'] = str(raw['note']).strip()

                # Resolve by xmlid first, then by code (in case a localization
                # CoA already created the account and the operator just wants
                # to rename / re-type it).
                existing = IrModelData.search([
                    ('module', '=', module), ('name', '=', ext_id),
                ], limit=1)
                account = None
                if existing and existing.model == 'account.account':
                    account = env['account.account'].browse(existing.res_id)
                    if not account.exists():
                        existing.unlink()
                        account = None
                if not account:
                    account = env['account.account'].sudo().search([
                        ('code', '=', code),
                        ('company_ids', 'in', [company_id]),
                    ], limit=1)

                try:
                    if account:
                        account.sudo().write(values)
                        updated += 1
                        action = 'updated'
                    else:
                        account = env['account.account'].sudo().create(values)
                        action = 'created'
                        created += 1
                    if not existing:
                        IrModelData.create({
                            'module': module, 'name': ext_id,
                            'model': 'account.account', 'res_id': account.id,
                            'noupdate': True,
                        })
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'VALIDATION',
                        'message': str(exc),
                    })
                    continue
                except AccessError:
                    raise
                except Exception as exc:
                    _logger.exception(
                        "CoA write failed for ext_id=%s", ext_id,
                    )
                    failed += 1
                    errors.append({
                        'row': idx, 'ext_id': ext_id,
                        'code': 'UNKNOWN',
                        'message': f"Internal error: {exc!s}",
                    })
                    continue

                records.append({
                    'row': idx, 'ext_id': ext_id,
                    'id': account.id, 'action': action,
                })

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_coa")
            else:
                cr.execute("RELEASE SAVEPOINT import_coa")
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_coa")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'failed': failed,
                'total': len(rows),
            },
            'records': records,
            'errors': errors,
        }

    # ---- opening-balances domain logic ------------------------------------
    #
    # Each input row is one *line* of the trial balance — not one move.
    # The whole batch posts as ONE balanced account.move into the opening
    # journal, dated to ``options.date`` (typically fiscal year start).
    #
    # Operator UX for the unbalanced case (common from paper books): if
    # totals don't match AND ``options.suspense_account_code`` is set, we
    # add a balancing line to that account. Without a suspense code we
    # refuse the whole batch and surface a clear UNBALANCED error so the
    # wizard can ask the operator to either fix the file or pick a
    # suspense account. Currency is locked at provisioning so this MUST
    # run before any real transactions.

    def _run_opening_balances_batch(self, *, rows, dry_run, run_id, options):
        env = request.env
        IrModelData = env['ir.model.data'].sudo()
        options = options or {}

        # --- Resolve envelope-level inputs ------------------------------

        # Date — string YYYY-MM-DD. We compare against fiscalyear-start
        # constraints further down via the journal's normal validation.
        date_str = (options.get('date') or '').strip()
        if not date_str:
            move_date = datetime.now().date()
        else:
            try:
                move_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return {
                    'run_id': run_id,
                    'dry_run': dry_run,
                    'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                                'failed': len(rows), 'total': len(rows)},
                    'records': [],
                    'errors': [{
                        'row': -1, 'ext_id': None,
                        'code': 'BAD_DATE',
                        'message': f"options.date {date_str!r} must be YYYY-MM-DD",
                    }],
                }

        # Journal — try the operator's pick first; fall back to any
        # general journal; if neither exists, fail with a clear message.
        journal_code = (options.get('journal_code') or '').strip()
        journal = None
        if journal_code:
            journal = env['account.journal'].sudo().search([
                '|', ('code', '=', journal_code),
                     ('name', '=ilike', journal_code),
            ], limit=1)
        if not journal:
            journal = env['account.journal'].sudo().search([
                ('type', '=', 'general'),
            ], limit=1)
        if not journal:
            return {
                'run_id': run_id,
                'dry_run': dry_run,
                'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                            'failed': len(rows), 'total': len(rows)},
                'records': [],
                'errors': [{
                    'row': -1, 'ext_id': None,
                    'code': 'NO_JOURNAL',
                    'message': (
                        "No general journal available. Verify the tenant's "
                        "localization installed an opening / miscellaneous journal."
                    ),
                }],
            }

        # Suspense account — required only when totals don't balance.
        suspense_code = (options.get('suspense_account_code') or '').strip()
        suspense_account = None
        if suspense_code:
            suspense_account = env['account.account'].sudo().search([
                ('code', '=', suspense_code),
            ], limit=1)
            if not suspense_account:
                return {
                    'run_id': run_id,
                    'dry_run': dry_run,
                    'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                                'failed': len(rows), 'total': len(rows)},
                    'records': [],
                    'errors': [{
                        'row': -1, 'ext_id': None,
                        'code': 'SUSPENSE_ACCOUNT_NOT_FOUND',
                        'message': f"Suspense account {suspense_code!r} not found",
                    }],
                }

        narration = (options.get('narration') or 'Opening balances').strip()

        # --- Per-line validation ----------------------------------------

        line_payloads = []
        line_errors = []
        total_debit = 0.0
        total_credit = 0.0

        partner_xmlid_module = XMLID_MODULE['partners']
        coa_xmlid_module = XMLID_MODULE['chart_of_accounts']

        for idx, raw in enumerate(rows):
            if not isinstance(raw, dict):
                line_errors.append({
                    'row': idx, 'ext_id': None,
                    'code': 'BAD_ROW', 'message': 'Row must be an object',
                })
                continue

            # Resolve the account: prefer xmlid (links to a prior CoA import),
            # fall back to account code lookup.
            account_ext = (raw.get('account_ext_id') or '').strip()
            account_code = (raw.get('account_code') or raw.get('code') or '').strip()
            account = None
            if account_ext:
                rec = IrModelData.search([
                    ('module', '=', coa_xmlid_module), ('name', '=', account_ext),
                ], limit=1)
                if rec and rec.model == 'account.account':
                    a = env['account.account'].browse(rec.res_id)
                    if a.exists():
                        account = a
            if not account and account_code:
                account = env['account.account'].sudo().search([
                    ('code', '=', account_code),
                ], limit=1)

            if not account:
                line_errors.append({
                    'row': idx, 'ext_id': account_ext or account_code or None,
                    'code': 'ACCOUNT_NOT_FOUND',
                    'message': (
                        "Could not resolve account "
                        f"{'ext_id=' + account_ext if account_ext else 'code=' + account_code}"
                    ),
                })
                continue

            # Parse debit / credit — exactly one must be non-zero.
            def _amt(value):
                if value in (None, ''):
                    return 0.0
                try:
                    return float(str(value).replace(',', '.'))
                except (TypeError, ValueError):
                    return None
            debit = _amt(raw.get('debit'))
            credit = _amt(raw.get('credit'))
            if debit is None or credit is None:
                line_errors.append({
                    'row': idx, 'ext_id': account_ext or account_code,
                    'code': 'BAD_NUMBER',
                    'message': 'debit and credit must be numeric',
                })
                continue
            if debit < 0 or credit < 0:
                line_errors.append({
                    'row': idx, 'ext_id': account_ext or account_code,
                    'code': 'NEGATIVE_AMOUNT',
                    'message': 'debit and credit must be non-negative; flip the column for the opposite sign',
                })
                continue
            if debit > 0 and credit > 0:
                line_errors.append({
                    'row': idx, 'ext_id': account_ext or account_code,
                    'code': 'BOTH_SIDES',
                    'message': 'A line can have only one of debit or credit, not both',
                })
                continue
            if debit == 0 and credit == 0:
                line_errors.append({
                    'row': idx, 'ext_id': account_ext or account_code,
                    'code': 'ZERO_LINE',
                    'message': 'Lines with zero on both sides are dropped',
                })
                continue

            line_values = {
                'account_id': account.id,
                'name': str(raw.get('label') or raw.get('memo') or 'Opening balance')[:200],
                'debit': debit,
                'credit': credit,
            }

            # Optional partner (for AR/AP partner-keyed openings).
            partner_ext = (raw.get('partner_ext_id') or '').strip()
            partner_ref = (raw.get('partner_ref') or '').strip()
            if partner_ext:
                rec = IrModelData.search([
                    ('module', '=', partner_xmlid_module), ('name', '=', partner_ext),
                ], limit=1)
                if rec and rec.model == 'res.partner':
                    line_values['partner_id'] = rec.res_id
            elif partner_ref:
                p = env['res.partner'].sudo().search([
                    ('ref', '=', partner_ref),
                ], limit=1)
                if p:
                    line_values['partner_id'] = p.id

            line_payloads.append(line_values)
            total_debit += debit
            total_credit += credit

        # --- Balance check + suspense handling --------------------------

        diff = round(total_debit - total_credit, 2)
        suspense_applied = None

        if abs(diff) >= 0.01:
            if not suspense_account:
                # Fatal — return summary with everything failed and a single
                # batch-level UNBALANCED error so the wizard knows to ask the
                # operator for a suspense account.
                return {
                    'run_id': run_id,
                    'dry_run': dry_run,
                    'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                                'failed': len(rows), 'total': len(rows),
                                'total_debit': round(total_debit, 2),
                                'total_credit': round(total_credit, 2),
                                'imbalance': diff},
                    'records': [],
                    'errors': line_errors + [{
                        'row': -1, 'ext_id': None,
                        'code': 'UNBALANCED',
                        'message': (
                            f"Trial balance is off by {abs(diff):.2f} "
                            f"({'excess debit' if diff > 0 else 'excess credit'}). "
                            "Provide options.suspense_account_code to auto-balance "
                            "or fix the source file."
                        ),
                    }],
                }
            # Apply suspense: if excess debit, add a credit line; if excess
            # credit, add a debit line. Either way the line lands on the
            # suspense account.
            if diff > 0:
                line_payloads.append({
                    'account_id': suspense_account.id,
                    'name': f'Opening balance suspense (off by {abs(diff):.2f})',
                    'debit': 0.0,
                    'credit': abs(diff),
                })
            else:
                line_payloads.append({
                    'account_id': suspense_account.id,
                    'name': f'Opening balance suspense (off by {abs(diff):.2f})',
                    'debit': abs(diff),
                    'credit': 0.0,
                })
            suspense_applied = {
                'account_id': suspense_account.id,
                'account_code': suspense_account.code,
                'amount': abs(diff),
                'side': 'credit' if diff > 0 else 'debit',
            }

        # --- Refuse if any line had a per-row error ---------------------

        # Opening balance is all-or-nothing: a single broken line means the
        # whole TB is wrong. Returning here avoids posting a partial move.
        if line_errors:
            return {
                'run_id': run_id,
                'dry_run': dry_run,
                'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                            'failed': len(rows), 'total': len(rows),
                            'total_debit': round(total_debit, 2),
                            'total_credit': round(total_credit, 2),
                            'imbalance': diff},
                'records': [],
                'errors': line_errors,
            }

        if not line_payloads:
            return {
                'run_id': run_id,
                'dry_run': dry_run,
                'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                            'failed': 0, 'total': 0,
                            'total_debit': 0.0, 'total_credit': 0.0,
                            'imbalance': 0.0},
                'records': [],
                'errors': [{
                    'row': -1, 'ext_id': None,
                    'code': 'NO_LINES',
                    'message': 'No valid lines to post',
                }],
            }

        # --- Build or update the move -----------------------------------

        module = XMLID_MODULE['opening_balances']
        existing = IrModelData.search([
            ('module', '=', module), ('name', '=', run_id),
        ], limit=1)
        move = None
        if existing and existing.model == 'account.move':
            move = env['account.move'].sudo().browse(existing.res_id)
            if not move.exists():
                existing.unlink()
                move = None
            elif move.state == 'posted':
                return {
                    'run_id': run_id,
                    'dry_run': dry_run,
                    'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                                'failed': len(rows), 'total': len(rows)},
                    'records': [],
                    'errors': [{
                        'row': -1, 'ext_id': run_id,
                        'code': 'ALREADY_POSTED',
                        'message': (
                            "An opening-balance entry already exists for this "
                            "import run and is posted. Cancel/reset the existing "
                            "move first if you want to re-import."
                        ),
                    }],
                }

        cr = env.cr
        cr.execute("SAVEPOINT import_opening_balances")
        try:
            move_values = {
                'journal_id': journal.id,
                'date': move_date,
                'ref': narration,
                'line_ids': [(0, 0, lv) for lv in line_payloads],
            }
            if move:
                # Existing draft: wipe lines and re-add. We don't try to
                # diff — the source file is authoritative.
                move.sudo().line_ids.unlink()
                move.sudo().write({
                    'journal_id': journal.id,
                    'date': move_date,
                    'ref': narration,
                    'line_ids': [(0, 0, lv) for lv in line_payloads],
                })
                action = 'updated'
            else:
                move = env['account.move'].sudo().create(move_values)
                IrModelData.create({
                    'module': module, 'name': run_id,
                    'model': 'account.move', 'res_id': move.id,
                    'noupdate': True,
                })
                action = 'created'

            # Post (unless dry-run). Posting validates balance one more time
            # at the ORM level; if the move is still unbalanced for any
            # reason, Odoo raises ValidationError and the savepoint rolls back.
            if not dry_run:
                move.sudo().action_post()

            if dry_run:
                cr.execute("ROLLBACK TO SAVEPOINT import_opening_balances")
            else:
                cr.execute("RELEASE SAVEPOINT import_opening_balances")
        except (ValidationError, UserError) as exc:
            cr.execute("ROLLBACK TO SAVEPOINT import_opening_balances")
            return {
                'run_id': run_id,
                'dry_run': dry_run,
                'summary': {'created': 0, 'updated': 0, 'skipped': 0,
                            'failed': len(rows), 'total': len(rows),
                            'total_debit': round(total_debit, 2),
                            'total_credit': round(total_credit, 2),
                            'imbalance': diff},
                'records': [],
                'errors': [{
                    'row': -1, 'ext_id': run_id,
                    'code': 'POST_FAILED',
                    'message': str(exc),
                }],
            }
        except Exception:
            cr.execute("ROLLBACK TO SAVEPOINT import_opening_balances")
            raise

        return {
            'run_id': run_id,
            'dry_run': dry_run,
            'summary': {
                'created': 1 if action == 'created' else 0,
                'updated': 1 if action == 'updated' else 0,
                'skipped': 0,
                'failed': 0,
                'total': len(rows),
                'lines_posted': len(line_payloads),
                'total_debit': round(total_debit, 2),
                'total_credit': round(total_credit, 2),
                'imbalance': diff,
            },
            'records': [{
                'row': -1, 'ext_id': run_id,
                'id': move.id, 'action': action,
                'state': move.state,
                'suspense_applied': suspense_applied,
            }],
            'errors': [],
        }
