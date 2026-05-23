"""Backfill default sale tax on income accounts for existing OHADA tenants.

The post_init_hook only fires on fresh installs (`-i`). Existing tenants
upgrade with `-u`, which skips the hook entirely. This migration runs
the same idempotent binding so tenants installed before this version
get TVA on service / standalone invoices retroactively.

See hooks.py:_bind_default_sale_tax_to_income_accounts for full
rationale — short version: l10n_<iso2> charts declare TVA rates but
don't bind them to income accounts, so any invoice line without a
product (consulting fees, services, repairs) posts with
amount_tax=0 and TVA collectée account 4431 stays empty.

Accounts that already have a sale tax bound are left untouched, so
this migration is safe to run on tenants where operators have
manually configured per-account defaults.
"""

from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_toomde_ohada_overlay.hooks import (
    _bind_default_sale_tax_to_income_accounts,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _bind_default_sale_tax_to_income_accounts(env)
