"""Backfill the Domestic-FP country binding on existing OHADA tenants.

The post_init_hook only fires on fresh installs (`-i`). Existing tenants
upgrade with `-u`, which skips the hook entirely. This migration runs
the same idempotent rebind so tenants installed before this version
pick up the corrected Domestic fiscal position.

See hooks.py:_rebind_domestic_fiscal_position for full rationale —
short version: l10n_<iso2> chart templates bind the Domestic FP to
whatever country the company had at chart-load time (Odoo's default
"United States" in our provisioning order), so BF/SN/CI customers
fell through to the catch-all "Foreign Trade" FP and lost all TVA.
"""

from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_toomde_ohada_overlay.hooks import _rebind_domestic_fiscal_position


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _rebind_domestic_fiscal_position(env)
