"""Backfill alphabetic `lettrage_code` on existing `account.full.reconcile`.

After this version, `account.full.reconcile.create` assigns the code on
the fly. This migration walks pre-existing reconciliations and assigns
codes ordered by `id` (creation order) so the per-account sequence is
stable and reproducible.

Idempotent: rows that already have `lettrage_code` are skipped.
"""

from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_toomde_ohada_overlay.models.account_full_reconcile import (
    lettrage_int_to_code,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Full = env["account.full.reconcile"].sudo()

    # First pass: denormalize lettrage_account_id / lettrage_company_id
    # for fulls created before this version.
    todo = Full.search([("lettrage_account_id", "=", False)])
    for full in todo:
        line = full.reconciled_line_ids[:1]
        if not line:
            continue
        full.write({
            "lettrage_account_id": line.account_id.id,
            "lettrage_company_id": line.company_id.id if line.company_id else False,
        })

    # Second pass: assign codes per (account, company) in id order so the
    # series stays stable.
    rows = Full.search_read(
        [("lettrage_code", "in", [False, ""])],
        ["id", "lettrage_account_id", "lettrage_company_id"],
        order="lettrage_account_id, lettrage_company_id, id",
    )
    # Track the running max code per (account, company) so we resume
    # correctly when a tenant has *some* fulls already coded (mixed state).
    max_idx_by_key = {}
    for r in rows:
        acc_id = r["lettrage_account_id"][0] if r["lettrage_account_id"] else None
        comp_id = r["lettrage_company_id"][0] if r["lettrage_company_id"] else None
        if not acc_id:
            continue
        key = (acc_id, comp_id)
        if key not in max_idx_by_key:
            # Seed from any existing coded fulls on this key.
            domain = [("lettrage_account_id", "=", acc_id)]
            if comp_id:
                domain.append(("lettrage_company_id", "=", comp_id))
            coded = Full.search_read(domain, ["lettrage_code"])
            from odoo.addons.l10n_toomde_ohada_overlay.models.account_full_reconcile import (
                lettrage_code_to_int,
            )
            max_idx = 0
            for c in coded:
                idx = lettrage_code_to_int(c.get("lettrage_code") or "")
                if idx > max_idx:
                    max_idx = idx
            max_idx_by_key[key] = max_idx
        max_idx_by_key[key] += 1
        Full.browse(r["id"]).write({
            "lettrage_code": lettrage_int_to_code(max_idx_by_key[key]),
        })
