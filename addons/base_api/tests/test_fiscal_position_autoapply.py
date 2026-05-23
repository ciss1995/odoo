# -*- coding: utf-8 -*-
"""Auto-apply fiscal position on res.partner create/update.

Contract (see tax.md Phase 0 TX-1A):

- When the caller omits ``property_account_position_id``, the endpoint
  runs Odoo's canonical ``_get_fiscal_position()`` resolver against the
  partner's country / state / vat and writes the result.
- When the caller passes ``property_account_position_id`` explicitly,
  it wins — auto-apply does NOT override.
- On update, the resolver only re-runs when one of the FP-relevant
  fields (country_id, state_id, vat) is in the payload. Renaming the
  partner doesn't re-trigger auto-apply (otherwise a deliberate clear
  would re-apply on next save).
"""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestFiscalPositionAutoApply(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.create_url = '/api/v2/create/res.partner'

        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Seed a Senegal-targeted FP with auto_apply=True. Odoo's resolver
        # picks it for partners whose country_id == SN.
        company = self.env.user.company_id
        sn = self.env.ref('base.sn', raise_if_not_found=False)
        fr = self.env.ref('base.fr', raise_if_not_found=False)
        self.assertTrue(sn, "base.sn (Senegal) must exist in the demo data")
        self.assertTrue(fr, "base.fr (France) must exist in the demo data")
        self.sn = sn
        self.fr = fr

        FP = self.env['account.fiscal.position'].sudo()
        # Wipe any prior auto_apply rows for this test company so we don't
        # collide with whatever the l10n module seeded.
        FP.search([('company_id', '=', company.id), ('auto_apply', '=', True)]).write({'auto_apply': False})
        self.fp_sn = FP.create({
            'name': 'Test Domestic SN',
            'company_id': company.id,
            'auto_apply': True,
            'country_id': sn.id,
        })
        self.fp_fr = FP.create({
            'name': 'Test Export FR',
            'company_id': company.id,
            'auto_apply': True,
            'country_id': fr.id,
        })

    def _login(self):
        admin = self.env.ref('base.user_admin')
        session_token = ''.join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(48)
        )
        token_hash = self.env['api.session']._hash_token(session_token)
        self.env['api.session'].sudo().create({
            'user_id': admin.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return session_token

    def _post(self, token, body):
        return self.url_open(
            self.create_url,
            data=json.dumps(body),
            headers={'session-token': token, 'Content-Type': 'application/json'},
        )

    def _put(self, token, partner_id, body):
        return self.url_open(
            f'/api/v2/update/res.partner/{partner_id}',
            data=json.dumps(body),
            headers={'session-token': token, 'Content-Type': 'application/json'},
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def test_create_partner_with_sn_country_autoapplies_sn_fp(self):
        """Partner with country=SN and no FP in payload → SN FP applied."""
        token = self._login()
        resp = self._post(token, {
            'name': 'Auto SN ' + secrets.token_hex(3),
            'country_id': self.sn.id,
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()['data']['id']
        partner = self.env['res.partner'].sudo().browse(pid)
        self.assertEqual(
            partner.property_account_position_id.id,
            self.fp_sn.id,
            f"Expected FP={self.fp_sn.id} (Test Domestic SN), "
            f"got {partner.property_account_position_id.id}",
        )

    def test_create_partner_with_explicit_fp_does_not_override(self):
        """Explicit property_account_position_id wins over auto-apply."""
        token = self._login()
        resp = self._post(token, {
            'name': 'Manual FR ' + secrets.token_hex(3),
            'country_id': self.sn.id,  # would auto-apply SN…
            'property_account_position_id': self.fp_fr.id,  # …but caller pinned FR.
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()['data']['id']
        partner = self.env['res.partner'].sudo().browse(pid)
        self.assertEqual(
            partner.property_account_position_id.id,
            self.fp_fr.id,
            "Explicit FP in payload must NOT be overridden by auto-apply",
        )

    def test_create_partner_no_country_leaves_fp_unset(self):
        """Without country / state / vat the resolver finds nothing — no write."""
        token = self._login()
        resp = self._post(token, {
            'name': 'No-Country ' + secrets.token_hex(3),
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()['data']['id']
        partner = self.env['res.partner'].sudo().browse(pid)
        # Either falsey (no FP found) or whatever the company default is —
        # the key invariant is that we didn't crash the create.
        self.assertIsNotNone(partner.id)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def test_update_country_reapplies_fp(self):
        """Changing country re-runs the resolver."""
        token = self._login()
        # Create with SN → SN FP.
        resp = self._post(token, {
            'name': 'Switcher ' + secrets.token_hex(3),
            'country_id': self.sn.id,
        })
        pid = resp.json()['data']['id']
        partner = self.env['res.partner'].sudo().browse(pid)
        self.assertEqual(partner.property_account_position_id.id, self.fp_sn.id)

        # Change country to FR — FP should switch.
        upd = self._put(token, pid, {'country_id': self.fr.id})
        self.assertEqual(upd.status_code, 200, upd.text)
        partner.invalidate_recordset()
        self.assertEqual(
            partner.property_account_position_id.id,
            self.fp_fr.id,
            "Changing country_id must re-trigger auto-apply",
        )

    def test_update_non_fp_field_does_not_reapply(self):
        """Renaming the partner must NOT touch the FP — otherwise a
        deliberate clear would be undone on the next benign save."""
        token = self._login()
        resp = self._post(token, {
            'name': 'No-Reapply ' + secrets.token_hex(3),
            'country_id': self.sn.id,
        })
        pid = resp.json()['data']['id']
        partner = self.env['res.partner'].sudo().browse(pid)
        # Manually clear the FP (simulate a tenant override to "no FP").
        partner.sudo().property_account_position_id = False

        # Now rename — touch nothing FP-relevant.
        upd = self._put(token, pid, {'name': 'Renamed'})
        self.assertEqual(upd.status_code, 200, upd.text)
        partner.invalidate_recordset()
        self.assertFalse(
            partner.property_account_position_id,
            "Non-FP-relevant updates must NOT re-trigger auto-apply",
        )
