"""Withholding-tax helpers on `account.payment` for the AFR-6 report.

We don't add new stored fields — the gross / withheld / net split is
already derivable from the linked move's tax lines + amount. These
methods produce the values pre-formatted for the QWeb template so the
.xml stays presentation-only.
"""

from __future__ import annotations

from odoo import models
from odoo.tools.misc import format_date


class AccountPayment(models.Model):
    _inherit = "account.payment"

    def _toomde_wht_lines(self):
        """Return the move-line records on the reconciled move that
        carry a withholding-type tax. Empty recordset if none."""
        self.ensure_one()
        # Withholding lines on the bill we're paying. SYSCOHADA chart
        # books these on Class-4 sub-accounts (4423 - retenues sur
        # salaires, 4424 - retenues à la source TVA, etc.). Detection
        # follows Odoo's negative `account_tax.amount` convention for
        # withholding (Odoo doesn't ship a dedicated `is_withholding`
        # field across versions; the negative-rate trick is portable).
        target_moves = self.reconciled_invoice_ids | self.reconciled_bill_ids
        return target_moves.line_ids.filtered(
            lambda l: l.tax_line_id and l.tax_line_id.amount < 0
        )

    def _toomde_wht_gross_amount(self):
        """Gross amount before withholding (sum of base lines on the move)."""
        self.ensure_one()
        moves = self.reconciled_invoice_ids | self.reconciled_bill_ids
        return sum(moves.mapped("amount_untaxed"))

    def _toomde_wht_withheld_amount(self):
        """Total withheld — absolute value of the negative-rate tax lines."""
        return sum(abs(l.balance) for l in self._toomde_wht_lines())

    def _toomde_wht_net_amount(self):
        return self.amount  # the payment line itself is the net paid

    # Display variants — keep formatting concerns out of the QWeb template.
    # `formatLang` is the Odoo idiom; falling back to `f"{x:,.2f}"` keeps
    # tests deterministic when `currency` is not in the recordset.
    def _toomde_wht_gross_display(self):
        return self._toomde_wht_format(self._toomde_wht_gross_amount())

    def _toomde_wht_withheld_display(self):
        return self._toomde_wht_format(self._toomde_wht_withheld_amount())

    def _toomde_wht_net_display(self):
        return self._toomde_wht_format(self._toomde_wht_net_amount())

    def _toomde_wht_today_display(self):
        return format_date(self.env, self.date) if self.date else ""

    def _toomde_wht_format(self, amount):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        symbol = (currency and currency.symbol) or ""
        return f"{amount:,.2f} {symbol}".strip()
