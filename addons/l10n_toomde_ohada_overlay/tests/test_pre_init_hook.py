# -*- coding: utf-8 -*-
"""Pre-init guard for l10n_toomde_ohada_overlay.

Refuses install when no res.company sits in an OHADA member state.
The function is a UserError raise — we don't run it through a real
install cycle here, just call it directly with a MagicMock env.
"""

from unittest.mock import MagicMock

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.l10n_toomde_ohada_overlay.hooks import (
    _OHADA_COUNTRIES,
    _pre_init_ohada_overlay,
)


def _fake_env(company_country_codes):
    """Build a MagicMock that quacks like an Odoo env for the hook."""
    companies = []
    for code in company_country_codes:
        c = MagicMock()
        c.name = f"Company {code or 'none'}"
        c.country_id = MagicMock()
        c.country_id.__bool__ = lambda self, code=code: bool(code)
        c.country_id.code = code
        companies.append(c)

    def filtered(predicate):
        return [c for c in companies if predicate(c)]

    res_company_search = MagicMock()
    res_company_search.filtered = filtered
    res_company_search.__iter__ = lambda self: iter(companies)

    res_company = MagicMock()
    res_company.sudo.return_value.search.return_value = res_company_search

    env = MagicMock()
    env.__getitem__.return_value = res_company
    return env


@tagged('post_install', '-at_install')
class TestPreInitHook(TransactionCase):

    def test_install_refused_with_no_companies(self):
        env = _fake_env([])
        with self.assertRaises(UserError) as cm:
            _pre_init_ohada_overlay(env)
        self.assertIn("OHADA member state", str(cm.exception))

    def test_install_refused_with_all_non_ohada(self):
        env = _fake_env(["US", "FR"])
        with self.assertRaises(UserError) as cm:
            _pre_init_ohada_overlay(env)
        msg = str(cm.exception)
        self.assertIn("US", msg)
        self.assertIn("OHADA", msg)

    def test_install_refused_with_country_unset(self):
        env = _fake_env([None])
        with self.assertRaises(UserError) as cm:
            _pre_init_ohada_overlay(env)
        self.assertIn("no country", str(cm.exception))

    def test_install_allowed_on_single_bf_company(self):
        env = _fake_env(["BF"])
        # Should not raise.
        _pre_init_ohada_overlay(env)

    def test_install_allowed_on_any_ohada_country(self):
        for code in _OHADA_COUNTRIES:
            env = _fake_env([code])
            _pre_init_ohada_overlay(env)  # must not raise

    def test_install_allowed_with_mixed_companies_if_one_ohada(self):
        """Multi-company tenant — at least one OHADA company is enough.

        Rationale: the hook is about whether the overlay's controls
        make sense for this *deployment*. If even one company on the
        tenant is OHADA-bound, the overlay needs to be installed; it
        already no-ops on non-OHADA companies at the per-company
        level inside the post_init logic.
        """
        env = _fake_env(["US", "BF"])
        _pre_init_ohada_overlay(env)  # must not raise
