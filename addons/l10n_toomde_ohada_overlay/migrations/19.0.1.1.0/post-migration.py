"""Backfill the tax-included default on existing OHADA tenants.

The post_init_hook only fires on fresh installs (`-i`). Existing tenants
upgrade with `-u`, which skips the hook entirely. This migration runs
the same idempotent flip so tenants that already had the overlay
installed pick up the TTC pricing convention.

Tenants with existing accounting entries hit Odoo's
`_check_set_account_price_include` constraint and stay HT — clearing
move lines and re-running `-u l10n_toomde_ohada_overlay` is the manual
path to TTC for those.

See hooks.py:_set_company_prices_tax_included for the full rationale.
"""

from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_toomde_ohada_overlay.hooks import _set_company_prices_tax_included


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _set_company_prices_tax_included(env)
