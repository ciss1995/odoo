# -*- coding: utf-8 -*-
"""Tests for res.partner.phone_e164.

The field powers payment-webhook customer lookup. Wave / Moneroo /
Orange Money will post us ``msisdn=221771234567`` and we must find
the customer record in <50ms regardless of how the merchant typed
the number into Odoo.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPhoneE164(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.country_sn = cls.env.ref('base.sn')
        cls.country_us = cls.env.ref('base.us')

    def _partner(self, **vals):
        defaults = {'name': 'Test Partner'}
        defaults.update(vals)
        return self.env['res.partner'].create(defaults)

    def test_senegalese_local_format_normalized_to_e164(self):
        """771234567 + country=SN → +221771234567 (the canonical case)."""
        p = self._partner(phone='771234567', country_id=self.country_sn.id)
        self.assertEqual(p.phone_e164, '+221771234567')

    def test_senegalese_pretty_format_normalized(self):
        """'77 12 34 567' + country=SN → +221771234567 (spaces ignored)."""
        p = self._partner(phone='77 12 34 567', country_id=self.country_sn.id)
        self.assertEqual(p.phone_e164, '+221771234567')

    def test_already_e164_passes_through(self):
        """A correctly-formatted +221… number stays +221…."""
        p = self._partner(phone='+221771234567', country_id=self.country_sn.id)
        self.assertEqual(p.phone_e164, '+221771234567')

    def test_no_plus_with_country_code_is_recognized(self):
        """'221771234567' (no +) with SN country still parses correctly."""
        p = self._partner(phone='221771234567', country_id=self.country_sn.id)
        self.assertEqual(p.phone_e164, '+221771234567')

    def test_garbage_phone_returns_false_not_error(self):
        """Phone is user input — we must not raise on bad data."""
        p = self._partner(phone='not a phone number', country_id=self.country_sn.id)
        self.assertFalse(p.phone_e164)

    def test_no_phone_is_empty(self):
        p = self._partner(country_id=self.country_sn.id)
        self.assertFalse(p.phone_e164)

    def test_country_change_recomputes(self):
        """Same local number is parsed against the partner's country.
        Changing country must trigger a recompute via the @depends."""
        p = self._partner(phone='771234567', country_id=self.country_sn.id)
        self.assertEqual(p.phone_e164, '+221771234567')
        # Pivot to US — 771234567 is not a valid US local number, so
        # phone_e164 becomes False instead of staying stale.
        p.country_id = self.country_us
        self.assertFalse(p.phone_e164)

    def test_lookup_by_phone_e164_works(self):
        """The webhook arrives with msisdn='221771234567'; we look up
        the partner with a single equality search. Smoke test that the
        indexed exact-match path resolves."""
        target = self._partner(
            name='Aminata',
            phone='77 12 34 567',
            country_id=self.country_sn.id,
        )
        # Decoy with a different normalized number — must not match.
        self._partner(
            name='Other',
            phone='+221774444444',
            country_id=self.country_sn.id,
        )
        found = self.env['res.partner'].search([('phone_e164', '=', '+221771234567')])
        self.assertEqual(found.ids, target.ids)

    def test_company_country_fallback_when_partner_has_none(self):
        """If the partner has no country, fall back to the company's
        country so we still get a useful default region hint."""
        company = self.env.company
        original = company.country_id
        try:
            company.country_id = self.country_sn
            p = self._partner(phone='771234567')  # no country_id
            self.assertEqual(p.phone_e164, '+221771234567')
        finally:
            company.country_id = original
