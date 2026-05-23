# -*- coding: utf-8 -*-
"""Tax catalog rollout + tenant-facing tax management.

Two responsibilities, one file because they share the auth + helper
plumbing already established in `imports.py`:

1. ``POST /api/v2/internal/tax/rollout`` (internal token)
   The control-plane rollout worker calls this once per tenant in a
   country when the operator publishes a new statutory rate. Implements
   the 5-step rate-change pattern from tax.md:

     (1) Create a NEW ``account.tax`` row with the new amount;
     (2) Switch ``res.company.account_sale_tax_id`` (and purchase)
         to the new tax, when the OLD tax was the company default;
     (3) Update any ``account.fiscal.position`` lines that mapped TO
         the old tax to map to the new one;
     (4) Bulk product update is gated by an ir.config_parameter and
         left OFF by default — existing products keep their old tax
         until the tenant decides;
     (5) Mark the old tax inactive (DO NOT delete — historical
         invoices reference it forever via ``account.move.line``).

   Idempotent — if a tax with the same (name, type_tax_use, amount)
   already exists active, the endpoint returns ``ok=true, skipped=true``
   without doing the migration twice.

2. Tenant-facing tax management:

   - ``GET  /api/v2/me/tax/effective``
   - ``POST /api/v2/me/tax/overrides``
   - ``DELETE /api/v2/me/tax/overrides/{id}``

   These are session-token endpoints used by the tenant SPA's tax
   settings page. They require the calling user to be in
   ``base.group_system`` (Odoo admin) and they proxy to the control
   plane — base_api forwards to /admin/tenants/{tenant_id}/tax-* using
   the internal token, picking up the tenant_id from
   ``ir.config_parameter`` ``toomde.tenant_id`` (set at provisioning).

The proxy keeps the source of truth on the control plane: a tenant's
SPA can't write directly to its own ``tax_tenant_override`` rows; every
mutation goes through the audit-logged FastAPI endpoints we already
ship.
"""

import logging
import os
import time as _time
from urllib.parse import quote as _urlquote

import requests

from odoo import http
from odoo.http import request

from .base import BaseApiController


_logger = logging.getLogger(__name__)


def _control_plane_base_url():
    """Where to forward proxied calls. The container env wires this at
    boot; in dev we fall back to the in-cluster name."""
    return os.environ.get("CONTROL_PLANE_URL", "http://app:8000").rstrip("/")


def _control_plane_token():
    return os.environ.get("CONTROL_PLANE_TOKEN", "")


def _resolve_tenant_slug():
    """Tenant slug used in /internal/tenants/{slug}/tax-* URLs.

    Sourced from the ``TENANT_ID`` container env var (set at
    provisioning by the docker-compose template). Falls back to the
    ``toomde.tenant_slug`` ir.config_parameter for tenants where the
    env is missing. None means the proxy returns 503.
    """
    val = os.environ.get("TENANT_ID", "").strip()
    if val:
        return val
    param = (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("toomde.tenant_slug")
    )
    return (param or "").strip() or None


class TaxController(BaseApiController):
    # =================================================================
    # Internal — rollout endpoint (control plane → tenant)
    # =================================================================

    def _authenticate_internal(self):
        """Bearer-token auth shared with imports.py / invalidate-cache."""
        auth_header = request.httprequest.headers.get("Authorization", "") or ""
        if not auth_header.startswith("Bearer "):
            return False, self._error_response(
                "Internal auth required", 401, "INTERNAL_AUTH_REQUIRED",
            )
        token = auth_header.removeprefix("Bearer ").strip()
        expected = os.environ.get("CONTROL_PLANE_TOKEN", "")
        if not expected:
            return False, self._error_response(
                "Internal auth not configured", 503, "INTERNAL_AUTH_DISABLED",
            )
        if token != expected:
            return False, self._error_response(
                "Invalid internal token", 401, "INVALID_INTERNAL_TOKEN",
            )
        admin = request.env.ref("base.user_admin", raise_if_not_found=False)
        if admin is None or not admin.active:
            admin = (
                request.env["res.users"]
                .sudo()
                .search([("login", "=", "admin"), ("active", "=", True)], limit=1)
            )
        if not admin:
            return False, self._error_response(
                "Admin user not available", 500, "NO_ADMIN",
            )
        request.update_env(user=admin.id)
        return True, None

    @http.route(
        "/api/v2/internal/modules/installed",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def installed_modules(self):
        """List installed ir.module.module names. Internal-token only.

        Used by the control plane to verify per-tenant invariants
        ("OHADA tenant must have l10n_<iso2> + l10n_toomde_ohada_overlay
        installed") without operators having to SSH into each tenant.
        Returns a short list of names, not full module records, to keep
        the payload trivially cacheable on the control plane.
        """
        ok, err = self._authenticate_internal()
        if not ok:
            return err

        Module = request.env["ir.module.module"].sudo()
        rows = Module.search([("state", "=", "installed")]).read(["name"])
        return self._json_response(
            data={"modules": [r["name"] for r in rows], "count": len(rows)}
        )

    @http.route(
        "/api/v2/internal/tax/rollout",
        type="http", auth="none", methods=["POST"], csrf=False, readonly=False,
    )
    def rollout(self):
        """Apply the 5-step rate-change pattern. Idempotent.

        Body::

            {
              "kind": "vat_standard",       # informational
              "label": "TVA 20% (2027)",    # name of the NEW tax
              "old_amount": 18.0,           # amount of the tax being replaced
              "new_amount": 20.0,           # rate to publish
              "type_tax_use": "sale",       # 'sale' | 'purchase'
              "dry_run": false              # optional
            }

        Behavior:
        - Find the active ``account.tax`` whose ``amount == old_amount``
          and ``type_tax_use == type_tax_use``. If 0 or >1 match, fail
          loudly (operator picks via the admin UI which one to migrate).
        - If a tax with the new label/amount already exists active, return
          ``skipped=true`` (idempotency).
        - Otherwise: copy the old tax, set the new amount + label, switch
          company default if applicable, remap fiscal positions, deactivate
          the old tax.
        """
        ok, err = self._authenticate_internal()
        if not ok:
            return err

        request.httprequest._api_start_time = _time.time()

        try:
            body = request.httprequest.get_json(force=True)
        except Exception:
            return self._error_response("Invalid JSON body", 400, "INVALID_JSON")
        if not isinstance(body, dict):
            return self._error_response("Body must be JSON object", 400, "INVALID_JSON")

        label = (body.get("label") or "").strip()
        try:
            old_amount = float(body.get("old_amount"))
            new_amount = float(body.get("new_amount"))
        except (TypeError, ValueError):
            return self._error_response(
                "old_amount and new_amount must be numbers", 400, "INVALID_AMOUNT",
            )
        type_tax_use = body.get("type_tax_use") or "sale"
        if type_tax_use not in ("sale", "purchase"):
            return self._error_response(
                "type_tax_use must be 'sale' or 'purchase'", 400, "INVALID_TYPE_TAX_USE",
            )
        dry_run = bool(body.get("dry_run", False))

        if not label:
            return self._error_response("label is required", 400, "MISSING_LABEL")
        if not (0 <= new_amount <= 100 and 0 <= old_amount <= 100):
            return self._error_response(
                "amounts must be in [0,100]", 400, "INVALID_AMOUNT_RANGE",
            )

        Tax = request.env["account.tax"].sudo()

        # Idempotency: if a tax with this label + amount already exists, skip.
        already = Tax.search(
            [
                ("name", "=", label),
                ("amount", "=", new_amount),
                ("type_tax_use", "=", type_tax_use),
                ("active", "in", [True, False]),
            ],
            limit=1,
        )
        if already:
            return self._json_response(
                data={
                    "ok": True,
                    "skipped": True,
                    "reason": "tax_already_exists",
                    "new_tax_id": already.id,
                    "active": already.active,
                }
            )

        # Find the OLD tax. Defensive — refuse to migrate if 0 or >1 match.
        candidates = Tax.search(
            [
                ("amount", "=", old_amount),
                ("type_tax_use", "=", type_tax_use),
                ("active", "=", True),
            ]
        )
        if not candidates:
            return self._json_response(
                data={
                    "ok": True,
                    "skipped": True,
                    "reason": "no_old_tax_to_replace",
                    "new_tax_id": None,
                }
            )
        if len(candidates) > 1:
            return self._error_response(
                f"Ambiguous source tax: {len(candidates)} taxes match "
                f"amount={old_amount} type_tax_use={type_tax_use!r}. "
                "Operator must disambiguate manually.",
                409,
                "AMBIGUOUS_SOURCE_TAX",
            )
        old_tax = candidates[0]

        try:
            with request.env.cr.savepoint():
                # Step 1: copy the old tax, override amount + name. ``copy()``
                # carries over repartition lines (invoice / refund), tax
                # accounts, and tax_group_id — so the new tax posts to the
                # same 4431/4452-style accounts. This is what the
                # 5-step doc in tax.md prescribes.
                new_tax = old_tax.copy({
                    "name": label,
                    "amount": new_amount,
                    "active": True,
                })

                # Step 2: switch company default(s) when the old tax was it.
                companies = request.env["res.company"].sudo().search([])
                for company in companies:
                    if type_tax_use == "sale" and company.account_sale_tax_id.id == old_tax.id:
                        company.account_sale_tax_id = new_tax.id
                    if type_tax_use == "purchase" and company.account_purchase_tax_id.id == old_tax.id:
                        company.account_purchase_tax_id = new_tax.id

                # Step 3: remap fiscal-position tax mappings pointing to old tax.
                fp_taxes = request.env["account.fiscal.position.tax"].sudo().search([
                    ("tax_dest_id", "=", old_tax.id),
                ])
                fp_taxes.write({"tax_dest_id": new_tax.id})

                # Step 4 (bulk product update) is intentionally NOT done here.
                # An ir.config_parameter `toomde.tax.bulk_update_products=1`
                # lets operators opt in per tenant; left out of MVP because
                # bulk-writing thousands of product templates can take
                # minutes and lock the worker.

                # Step 5: deactivate the old tax. Historical invoice lines
                # already reference its id; they remain readable because
                # Odoo loads inactive records via with_context(active_test=False).
                old_tax.active = False

                if dry_run:
                    # Raising inside the savepoint context manager rolls
                    # it back automatically (Odoo's cr.savepoint() is a
                    # try/except-style helper). Caught below.
                    raise _DryRunRollback()
        except _DryRunRollback:
            return self._json_response(
                data={
                    "ok": True,
                    "dry_run": True,
                    "skipped": False,
                    "would_create": {"label": label, "amount": new_amount, "type_tax_use": type_tax_use},
                    "would_deactivate_id": old_tax.id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Tax rollout failed for label=%s", label)
            return self._error_response(str(exc), 500, "ROLLOUT_FAILED")

        return self._json_response(
            data={
                "ok": True,
                "skipped": False,
                "new_tax_id": new_tax.id,
                "deactivated_tax_id": old_tax.id,
                "label": label,
                "amount": new_amount,
            }
        )

    # =================================================================
    # Tenant-facing — proxy to control plane (SPA admin self-service)
    # =================================================================

    def _require_odoo_admin(self):
        """Tenant SPA session-token auth + group_system check.

        Tax-rate overrides have legal implications, so we limit
        self-service to Odoo system admins (the same group that can
        edit ``account.tax`` in the Odoo backend).
        """
        ok, result = self._authenticate_session()
        if not ok:
            return False, result
        user = result
        if not user.has_group("base.group_system"):
            return False, self._error_response(
                "Admin privileges required", 403, "ADMIN_REQUIRED",
            )
        return True, user

    def _proxy_url(self, path):
        slug = _resolve_tenant_slug()
        if not slug:
            return None, self._error_response(
                "Tenant slug not configured", 503, "TENANT_SLUG_MISSING",
            )
        return (
            f"{_control_plane_base_url()}/internal/tenants/{_urlquote(slug)}{path}",
            None,
        )

    def _proxy_headers(self):
        # Forward the Odoo user's email so the control-plane audit log
        # captures the human who initiated the change, not the proxy.
        email = ""
        user = request.env.user
        if user:
            email = (user.login or "").strip() or (user.email or "").strip()
        headers = {
            "Authorization": f"Bearer {_control_plane_token()}",
        }
        if email:
            headers["X-User-Email"] = email
        return headers

    def _proxy_get(self, path):
        url, err = self._proxy_url(path)
        if err:
            return err
        try:
            resp = requests.get(url, headers=self._proxy_headers(), timeout=10)
        except requests.RequestException as exc:
            _logger.warning("Control-plane proxy GET %s failed: %s", url, exc)
            return self._error_response(
                "Control plane unreachable", 502, "CONTROL_PLANE_UNREACHABLE",
            )
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text}
        return self._json_response(data=data, status_code=resp.status_code)

    def _proxy_json(self, method, path, body):
        url, err = self._proxy_url(path)
        if err:
            return err
        headers = self._proxy_headers()
        headers["Content-Type"] = "application/json"
        try:
            resp = requests.request(method, url, headers=headers, json=body, timeout=10)
        except requests.RequestException as exc:
            _logger.warning("Control-plane proxy %s %s failed: %s", method, url, exc)
            return self._error_response(
                "Control plane unreachable", 502, "CONTROL_PLANE_UNREACHABLE",
            )
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text}
        return self._json_response(data=data, status_code=resp.status_code)

    @http.route(
        "/api/v2/me/tax/effective",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def my_effective_rates(self):
        ok, result = self._require_odoo_admin()
        if not ok:
            return result
        return self._proxy_get("/tax-effective")

    @http.route(
        "/api/v2/me/tax/overrides",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def my_overrides(self):
        ok, result = self._require_odoo_admin()
        if not ok:
            return result
        return self._proxy_get("/tax-overrides")

    @http.route(
        "/api/v2/me/tax/overrides",
        type="http", auth="none", methods=["POST"], csrf=False, readonly=False,
    )
    def create_my_override(self):
        ok, result = self._require_odoo_admin()
        if not ok:
            return result
        try:
            body = request.httprequest.get_json(force=True)
        except Exception:
            return self._error_response("Invalid JSON body", 400, "INVALID_JSON")
        if not isinstance(body, dict):
            return self._error_response("Body must be JSON object", 400, "INVALID_JSON")
        return self._proxy_json("POST", "/tax-overrides", body)

    @http.route(
        "/api/v2/me/tax/overrides/<string:override_id>",
        type="http", auth="none", methods=["DELETE"], csrf=False, readonly=False,
    )
    def revoke_my_override(self, override_id):
        ok, result = self._require_odoo_admin()
        if not ok:
            return result
        # DELETE has no body; reuse proxy_json with None to keep one code path.
        return self._proxy_json("DELETE", f"/tax-overrides/{_urlquote(override_id)}", None)


class _DryRunRollback(Exception):
    """Internal sentinel: dry-run intentionally rolled back the savepoint."""
