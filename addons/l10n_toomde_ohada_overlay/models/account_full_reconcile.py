# -*- coding: utf-8 -*-
"""Alphabetic lettrage codes on `account.full.reconcile` (Saari parity).

Odoo 19 already groups reconciled lines via `matched_*_ids` and exposes a
numeric `matching_number` on `account.move.line`. Comptables trained on
Sage Saari expect *alphabetic* codes (A, B, ..., Z, AA, AB, ...) scoped
to a single account (411XXX, 401XXX, ...) — that's the muscle memory we
need to preserve to win demos.

We denormalize the account from the first reconciled line into
`lettrage_account_id` and assign the next free code on creation.
Concurrent reconciliations on the same account could collide; we accept
that as a rare race (next backfill resolves it) and don't take a DB-level
lock — the alternative is a bottleneck on hot accounts.
"""

from __future__ import annotations

from odoo import api, fields, models


def lettrage_int_to_code(n: int) -> str:
    """1 -> 'A', 26 -> 'Z', 27 -> 'AA', 52 -> 'AZ', 53 -> 'BA', ..."""
    if n <= 0:
        return ""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def lettrage_code_to_int(code: str) -> int:
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27. Returns 0 on invalid input."""
    if not code or not code.isalpha() or not code.isupper():
        return 0
    n = 0
    for ch in code:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


class AccountFullReconcile(models.Model):
    _inherit = "account.full.reconcile"

    lettrage_account_id = fields.Many2one(
        "account.account",
        string="Lettrage account",
        index=True,
        help="Account whose ledger this reconciliation belongs to. "
             "Denormalized from the first reconciled line for fast lookup.",
    )
    lettrage_company_id = fields.Many2one(
        "res.company",
        string="Lettrage company",
        index=True,
    )
    lettrage_code = fields.Char(
        string="Lettrage",
        index=True,
        help="Alphabetic reconcile code (A, B, ..., Z, AA, AB, ...) "
             "scoped to (company, account). Saari-compatible.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        fulls = super().create(vals_list)
        for full in fulls:
            if full.lettrage_code:
                continue
            line = full.reconciled_line_ids[:1]
            if not line:
                continue
            account = line.account_id
            company = line.company_id or self.env.company
            if not account:
                continue
            code = self._next_lettrage_code(account, company)
            # Direct write avoids re-triggering compute chains.
            full.write({
                "lettrage_account_id": account.id,
                "lettrage_company_id": company.id if company else False,
                "lettrage_code": code,
            })
        return fulls

    @api.model
    def _next_lettrage_code(self, account, company) -> str:
        """Highest existing code on (account, company) + 1, base-26 alpha."""
        domain = [("lettrage_account_id", "=", account.id)]
        if company:
            domain.append(("lettrage_company_id", "=", company.id))
        # Pull only the code column; iteration size stays small even on
        # hot accounts because we only need the max.
        existing = self.sudo().search_read(domain, ["lettrage_code"])
        max_idx = 0
        for row in existing:
            idx = lettrage_code_to_int(row.get("lettrage_code") or "")
            if idx > max_idx:
                max_idx = idx
        return lettrage_int_to_code(max_idx + 1)
