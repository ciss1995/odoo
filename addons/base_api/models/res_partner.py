# -*- coding: utf-8 -*-
"""E.164 phone normalization for res.partner.

Why: webhook receivers (Moneroo / Wave / future payment providers) post
us a customer phone in raw international form, e.g. ``221771234567`` or
``+221 77 123 45 67``. The same customer may have typed their phone any
of five different ways into Odoo — ``771234567``, ``77 12 34 567``,
``+221771234567``, ``00221771234567``, ``221-77-123-4567``. We need a
deterministic, indexed lookup that resolves them all to the same key
in <50 ms.

The store is an indexed ``phone_e164`` Char (e.g. ``+221771234567``)
computed from ``phone`` / ``mobile`` with the partner's country (or
the company's country) as the parsing region hint. Falls back to
``False`` when neither field parses — never raises, since phone is
user input.

Lighter than vendoring OCA ``base_phone`` for what we actually need:
one indexed field, one lookup. If we ever need the full OCA stack
(per-field widgets, country-from-prefix detection, format-on-input
validation) we can layer it on top.
"""

try:
    import phonenumbers
except ImportError:
    phonenumbers = None

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    phone_e164 = fields.Char(
        string='Phone (E.164)',
        compute='_compute_phone_e164',
        store=True,
        index=True,
        help="Normalized phone in E.164 (e.g. +221771234567). Used for "
             "exact-match lookups from payment webhooks and SMS gateways.",
    )

    @api.depends('phone', 'mobile', 'country_id', 'company_id')
    def _compute_phone_e164(self):
        for partner in self:
            partner.phone_e164 = partner._best_e164() or False

    def _best_e164(self):
        """Return the first parseable E.164 from (mobile, phone), else None.

        Mobile is tried first because mobile-money lookups always want
        the mobile number, and any partner who entered both meant the
        mobile one to be the SMS-reachable line.
        """
        self.ensure_one()
        if phonenumbers is None:
            return None
        region = self._e164_region_hint()
        for raw in (self.mobile, self.phone):
            normalized = _to_e164(raw, region)
            if normalized:
                return normalized
        return None

    def _e164_region_hint(self):
        """ISO 3166-1 alpha-2 region for phonenumbers.parse.

        Partner country wins; falls back to company country; else None.
        A None region still parses ``+221…`` correctly because the
        leading ``+`` is self-describing — only local-format strings
        like ``771234567`` need the hint.
        """
        self.ensure_one()
        if self.country_id and self.country_id.code:
            return self.country_id.code
        company = self.company_id or self.env.company
        if company and company.country_id and company.country_id.code:
            return company.country_id.code
        return None


def _to_e164(raw, region):
    if not raw or phonenumbers is None:
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
